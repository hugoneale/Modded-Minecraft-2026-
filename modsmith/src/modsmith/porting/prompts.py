"""Instructions handed to the porting agent."""
from __future__ import annotations

from ..models import FeatureDiff
from ..platform import TargetInfo
from ..versions import Version

RESOURCES = """\
Reference material (fetch what you need):
- NeoForge docs: https://docs.neoforged.net/ and release posts at https://neoforged.net/news/
  (each post links the vanilla "porting primer" for that version; read every primer
  between the source and target versions, in order)
- NeoForge 26.1 release notes: https://neoforged.net/news/26.1release/
- Fabric porting guides: https://docs.fabricmc.net/develop/porting/ and https://fabricmc.net/blog/
- ModDevGradle (NeoForge build plugin): https://github.com/neoforged/ModDevGradle
- Forge -> NeoForge (1.20.2+): META-INF/mods.toml became META-INF/neoforge.mods.toml,
  packages moved net.minecraftforge -> net.neoforged, events/registries/capabilities
  and networking were redesigned.
- 1.20.5+: item NBT replaced by Data Components; 1.21: data folders renamed to
  singular (recipes->recipe, loot_tables->loot_table, tags/blocks->tags/block, ...);
  1.21.2+: recipe/rendering changes; 1.21.4+: item model definitions in assets/<ns>/items/.
- 26.1+: Minecraft is no longer obfuscated (no mappings needed) and requires Java 25.
- Gradle must support the JDK it runs on: Java 21 needs Gradle 8.5+, Java 25 needs
  Gradle 9.1+ (update gradle/wrapper/gradle-wrapper.properties and the loader plugin).
"""


def _rules(feature_count: int) -> str:
    return f"""\
NON-NEGOTIABLE RULES
1. Preserve form and function. The baseline build registers {feature_count} pieces of
   content, listed in .modsmith/baseline-features.json (items, blocks, entities, recipes,
   loot tables, worldgen, sounds, ...). Every id there must still exist after the port,
   with the same id, and behave the same way. Do not rename ids.
2. Never delete, stub out, comment out or disable a feature, mixin, recipe, config option,
   compat module or event handler just to make the build pass. If a Minecraft or loader
   API was removed, reimplement the same behaviour with the new API.
3. Keep config option names and defaults the same so existing config files keep working.
   A mixin may be dropped only when an equivalent loader API now does the same job;
   name the mixin class and its replacement in .modsmith/PORT_NOTES.md.
4. Optional integrations with other mods may be disabled only if that other mod has no
   build for the target version; note each one in .modsmith/PORT_NOTES.md.
5. Do not add new gameplay features.
6. Write .modsmith/PORT_NOTES.md: what changed, anything you could not port 1:1 and why.
7. Finish only when the build command succeeds. You may run it yourself.
"""


def initial_prompt(name: str, mod_id: str, from_mc: str, from_loader: str, target: TargetInfo,
                   feature_count: int, decompiled: bool, ref_note: str) -> str:
    lv = target.loader_version or "the latest available"
    decomp = ""
    if decompiled:
        decomp = """
THIS PROJECT WAS DECOMPILED FROM A JAR (no original source available).
- src/main/java is Vineflower output; src/main/resources is copied from the jar.
- There are no build files yet. Create a complete Gradle project for the target
  (settings.gradle, build.gradle, gradle.properties, Gradle wrapper) using the
  standard template for the target loader.
- Minecraft methods/fields may appear as SRG names (m_12345_, f_12345_) or
  intermediary names (method_1234, field_1234). Translate them to the official
  Mojang names used by the target version.
- Fix decompiler artefacts (bad generics, synthetic accessors, switch maps, lambdas).
"""
    convert = ""
    if from_loader != target.loader:
        convert = f"\nThe mod is a {from_loader} mod and must become a {target.loader} mod: convert the build, metadata and all loader APIs.\n"
    return f"""\
You are porting the Minecraft mod "{name}" (mod id `{mod_id}`) in this directory.

FROM: Minecraft {from_mc}, {from_loader}
TO:   Minecraft {target.minecraft}, {target.loader} {lv}, Java {target.java}
{convert}
Source checkout: {ref_note}
{decomp}
Steps:
1. Read the build files and the code to understand the mod.
2. Update the build: Gradle wrapper, loader plugin (ModDevGradle for NeoForge,
   Loom for Fabric), Minecraft/loader versions, Java toolchain {target.java}, and the mod
   metadata file. Set the Minecraft dependency range so it includes {target.minecraft}.
3. Update the mod's dependencies on other mods to versions built for {target.minecraft}.
4. Port the code, data and assets to the target APIs and data formats.
5. Build with `./gradlew build -x test` (Windows: gradlew.bat) and fix errors until it passes.

{_rules(feature_count)}
{RESOURCES}"""


def build_failed_prompt(errors: str, attempt: int, max_attempts: int) -> str:
    return f"""\
The build still fails (verification attempt {attempt}/{max_attempts}). Fix the root causes,
following the same NON-NEGOTIABLE RULES (no deleting or stubbing features).

Build errors:
```
{errors}
```
Full log: .modsmith/build.log
"""


def feature_loss_prompt(diff: FeatureDiff, limit: int = 300) -> str:
    lines = []
    shown = 0
    for cat, ids in diff.missing.items():
        for i in ids:
            if shown >= limit:
                break
            lines.append(f"- {cat}: {i}")
            shown += 1
    more = "" if diff.missing_count <= limit else f"\n... and {diff.missing_count - limit} more (see .modsmith/feature-diff.json)"
    return f"""\
The build passes but the built jar is MISSING {diff.missing_count} of {diff.baseline_total} features
that the original mod had ({diff.retained_ratio:.1%} retained). Restore every one of them with
the same ids. If a data file moved folder in the new version, move it rather than dropping it.

Missing:
{chr(10).join(lines)}{more}
"""


def metadata_prompt(problem: str) -> str:
    return f"""\
The built jar's metadata is wrong for the target: {problem}
Fix the mod metadata / build so the jar declares the correct loader and Minecraft version.
"""


def needs_loader_conversion(from_loader: str, to_loader: str, to_mc: str) -> bool:
    if from_loader == to_loader:
        return False
    # NeoForge 1.20.1 still runs Forge mods.
    return not (from_loader == "forge" and to_loader == "neoforge" and Version(to_mc) == Version("1.20.1"))
