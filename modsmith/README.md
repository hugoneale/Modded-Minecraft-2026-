# Modsmith

A desktop app (plus command line) that **installs, checks, updates and force-ports Minecraft mods** to the Minecraft version you choose.

```
Select mods ─► pick Minecraft version ─► check dependencies & versions
   │
   ├─ official build for that version? ─► download it, then compare its content with yours
   ├─ no official build, source code available ─► AI agent ports it, builds it, checks content
   └─ no source code (opt-in, warning) ─► decompile ─► AI agent ports it, builds it, checks content
   │
   └─► install into your mods folder (old jars backed up) + report
```

## Quick start

1. Install **Python 3.11 or newer** from [python.org](https://www.python.org/downloads/). On Linux, also install Tk: `sudo apt install python3-tk`.
2. Download this repository: **[Download ZIP](https://github.com/hugoneale/Modded-Minecraft-2026-/archive/refs/heads/main.zip)**. Unzip it and open the `modsmith` folder.
3. Start the app:
   - **Windows:** double-click `run-modsmith.bat`
   - **macOS:** double-click `run-modsmith.command` (the first time, right-click it and choose **Open**)
   - **Linux:** run `./run-modsmith.sh`

Modsmith has no dependencies beyond Python's standard library. Checking and installing mods works straight away.

### In the app
1. **Choose mods.** Use **Scan folder** (your current `mods` folder), **Add by ID** (`modrinth:jei`, `curseforge:238222`, or just `sodium`), **Import pack** (CurseForge `.zip` or Modrinth `.mrpack`), or **Open config** (a `modsmith.toml`). Tick the ones you want.
2. **Pick the Minecraft version and loader.** The list comes straight from Mojang. The default is the latest release; NeoForge is the default loader for modern versions.
3. Click **1. Check** to see what's available. Nothing is changed yet.
4. Click **2. Install / Update** to download official builds into the mods folder. Jars being replaced are moved to `mods/.modsmith-backup/<date>/`.
5. Click **3. Force port missing** to port mods that have no official build (see below).
6. **Open report** shows the full table. Double-click a row for its details: missing content, licence, source repository and warnings.

## What each result means

| Result | Meaning |
|---|---|
| Up to date | You already have the newest build for that version. |
| Official build available | The author released a build for that version, and its content matches yours. |
| Official build, missing content | The author's build exists, but some items, blocks, recipes, worldgen etc. from your version are gone. Modsmith lists exactly what's missing. |
| No official build yet | Nothing released for that version. A port candidate. |
| Ported from source | Modsmith rebuilt it from the author's source code, and the content check passed. |
| Ported from decompiled jar | The same, but from decompiled code (see the warning below). |
| Port failed | The agent couldn't produce a build that passes every check. Logs are kept. |
| No build, no source | No official build, no source code found, and decompiling is off. |
| Not found | The mod couldn't be found on Modrinth or CurseForge. |

## Keeping form and function

The same mod can have different features in different Minecraft versions, so Modsmith never just trusts a version number. For every candidate build (official or ported) it compares the content against **your** current build:

- items, blocks, entities, effects, enchantments, fluids, biomes, paintings, sounds
- recipes, loot tables, advancements, structures, all worldgen, damage types, functions

Data folders that Minecraft renamed (for example `recipes` → `recipe` in 1.21) and content moved into data-pack overlay folders are matched up, so a rename doesn't count as a loss.

- **Official builds:** missing content is reported. The setting *When an official build is missing content* controls what happens next:
  - `warn` (default): install it anyway and tell you what's missing.
  - `prefer-port`: try to port your version instead, and fall back to the official build if that fails.
  - `strict`: never install a build with missing content.
- **Ports:** a port is only accepted when all three are true:
  1. The Gradle build passes.
  2. The jar declares the right loader and Minecraft version.
  3. **Every** piece of content from your build is still there.

  If anything is missing, the agent is shown the exact list and sent back to restore it. Its instructions forbid deleting or stubbing out features to make the code compile.

The content check can't prove that *behaviour* is identical, for example damage numbers or AI logic. Ported mods are marked "not play-tested": try them in a copy of your world first.

## Force-porting: what you need

| Requirement | Why |
|---|---|
| An AI coding agent: [Claude Code](https://claude.com/claude-code) (default), signed in once by running `claude` | Edits the mod's code for the new version |
| A JDK for the **target** version: Java 25 for 26.x, 21 for 1.20.5–1.21.x, 17 for 1.18–1.20.4 ([Adoptium](https://adoptium.net/)) | Compiles the mod |
| Git | Fetches the mod's source code |
| Disk space and time | Each port downloads Minecraft and the loader toolchain; a small mod takes minutes, a big one much longer |

To use another agent, set **Porting agent** to `command` and give a command template, for example `my-agent --prompt-file {prompt_file} --cwd {workdir}`.

How a port works:
1. Modsmith finds the mod's source repository (from Modrinth/CurseForge or the jar's metadata).
2. It checks out the **closest starting point**: the author's own branch for the target version if one exists, otherwise their newest version branch, otherwise the tag matching your build.
3. The agent gets the source and target versions, the JDK, a list of your build's content, and links to the official migration guides. It updates the build and the code.
4. Modsmith builds it, checks the metadata and content, and sends any problems back to the agent, for up to *Max agent passes* rounds.
5. A successful jar goes to `modsmith-out/ported/` and, if you asked, into your mods folder. The working copy, build log, agent log and the agent's `PORT_NOTES.md` stay in the cache folder (`%LOCALAPPDATA%\modsmith`, `~/Library/Caches/modsmith` or `~/.cache/modsmith`).

Dependencies are ported first. For example, a library has to exist for the target before the mods that use it can build.

### Decompiling (off by default)
With **Allow decompiling** on, mods with no public source are decompiled with [Vineflower](https://github.com/Vineflower/vineflower) and then ported the same way. **Warning:**
- Decompiled code is an approximation. Comments and names are lost, and success rates are much lower.
- Most closed-source mods are "All Rights Reserved". A decompiled port is for **your personal use only**: never share it, and never report its bugs to the original author.

### Licences
Ports of mods under licences that allow it (MIT, LGPL, GPL, Apache…) can be shared, with credit. Everything else is marked *personal use only* in the report.

## Config file (`modsmith.toml`)

Use this to pick mods by ID without the GUI, or to keep a modpack definition in git:

```toml
minecraft = "26.3"          # target; empty = latest release
loader = "neoforge"         # empty = default for that version
from_minecraft = "1.20.1"   # what mods given by id are on now (for comparison/porting)
from_loader = "forge"
mods_dir = "mods"
feature_policy = "warn"     # warn | prefer-port | strict
allow_decompile = false

mods = [
  "modrinth:jei",
  "curseforge:238222",
  "sodium",
  { spec = "modrinth:create", version = "abc123" },   # pin the build you're on now
  { spec = "file:mods/old-mod.jar" },
  { spec = "terralith", name = "Terralith" },          # name double-checks the match
]
```

A CurseForge API key (free, from [console.curseforge.com](https://console.curseforge.com)) enables CurseForge lookups. Set it with `CURSEFORGE_API_KEY` or in Settings. Without one, Modsmith uses Modrinth and matches CurseForge mods by name.

## Command line

```sh
modsmith gui                                       # the desktop app (default)
modsmith import ../16MARSHALST.zip -o pack.toml --mc 26.3
modsmith check  -c pack.toml                       # what's available, with content checks
modsmith update -c pack.toml                       # install official builds
modsmith port   -c pack.toml --install             # also port what has no official build
modsmith port   -c pack.toml --decompile           # ... including closed-source mods (warning!)
modsmith install modrinth:jei sodium --mc 26.3 --mods ./mods
modsmith scan   ./mods
modsmith versions
```

Run these from the `modsmith` folder as `./run-modsmith.sh <command>` (or `run-modsmith.bat <command>`), or install the package with `pip install ./modsmith` to get the `modsmith` command.

Reports are written to `modsmith-out/report.md` and `report.json`. They include a *"Which Minecraft version has the most of these mods?"* section, which helps when picking a target.

## Development

```sh
cd modsmith
python -m pip install pytest
python -m pytest
```

Source layout (`src/modsmith/`): `pipeline.py` (workflow), `providers/` (Modrinth, CurseForge), `features.py` (content inventory and diff), `resolver.py` (dependencies), `porting/` (agent, Gradle builder, decompiler, port loop), `gui.py`, `cli.py`.
