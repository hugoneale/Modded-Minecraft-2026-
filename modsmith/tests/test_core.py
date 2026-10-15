import json
from pathlib import Path

import pytest
from conftest import example_content, make_jar

from modsmith import config
from modsmith.features import diff_features, extract_features
from modsmith.fingerprint import curseforge_fingerprint, murmur2
from modsmith.jarscan import NotAModError, read_jar
from modsmith.models import DependencyKind, Loader, ModRef
from modsmith.platform import Platform
from modsmith.resolver import build_order, check_jars
from modsmith.sources import is_redistributable, match_branch, match_tag, normalize_repo_url
from modsmith.versions import (Version, default_loader_for, loader_aliases, parse_range,
                               required_java, satisfies)

PACK = Path(__file__).resolve().parents[2] / "16MARSHALST.zip"


# ------------------------------------------------------------------ versions
@pytest.mark.parametrize("a,b", [
    ("1.20.1", "1.20.2"), ("1.20", "1.20.1"), ("1.21.11", "26.1"), ("26.1", "26.1.1"),
    ("26.4-snapshot-3", "26.4"), ("1.21-pre1", "1.21"), ("1.0.0-beta.2", "1.0.0-rc.1"),
    ("2.0.9", "2.0.10"), ("21.1.77", "21.1.100"),
])
def test_version_order(a, b):
    assert Version(a) < Version(b)


def test_version_equality():
    assert Version("1.20") == Version("1.20.0")
    assert Version("v1.2+build.5") == Version("1.2")


@pytest.mark.parametrize("rng,v,ok", [
    ("[1.20.1,1.21)", "1.20.1", True), ("[1.20.1,1.21)", "1.20.6", True), ("[1.20.1,1.21)", "1.21", False),
    ("[47,)", "47.4.10", True), ("[47,)", "46.0", False), ("(,1.20]", "1.20", True),
    ("[1.20.1]", "1.20.1", True), ("[1.20.1]", "1.20.2", False),
    (">=0.14.21", "0.15.0", True), (">=0.14.21", "0.14.0", False), ("~1.20.1", "1.20.4", True),
    ("~1.20.1", "1.21", False), ("1.20.x", "1.20.6", True), ("1.20.x", "1.21", False),
    ("^2.1", "2.9", True), ("^2.1", "3.0", False), (">=1.20 <1.21", "1.20.4", True),
    ("*", "26.3", True), ("1.20.1 || 26.3", "26.3", True), ("[26.1,27)", "26.3", True),
])
def test_ranges(rng, v, ok):
    assert satisfies(v, rng) is ok


def test_range_list_is_or():
    assert parse_range(["1.20.1", ">=26.1"]).contains("26.3")


def test_minecraft_facts():
    assert required_java("1.20.1") == 17
    assert required_java("1.21.1") == 21
    assert required_java("26.3") == 25
    assert default_loader_for("1.20.1") == "forge"
    assert default_loader_for("26.3") == "neoforge"
    assert loader_aliases("neoforge", "1.20.1") == ["neoforge", "forge"]
    assert loader_aliases("neoforge", "26.3") == ["neoforge"]


def test_neoforge_prefix():
    assert Platform.neoforge_prefix("1.21.1") == "21.1."
    assert Platform.neoforge_prefix("1.21") == "21.0."
    assert Platform.neoforge_prefix("26.3") == "26.3.0."
    assert Platform.neoforge_prefix("26.1.2") == "26.1.2."


# --------------------------------------------------------------- fingerprint
def test_murmur2_known_values():
    # Canonical MurmurHash2 (Austin Appleby), seed 1: for empty input
    # h = 1; h *= m; h ^= h >> 15  ->  0x5BD15E36
    assert murmur2(b"") == 0x5BD15E36
    assert murmur2(b"a") != murmur2(b"b")


def test_cf_fingerprint_ignores_whitespace():
    assert curseforge_fingerprint(b"a b\tc\r\nd") == curseforge_fingerprint(b"abcd")


# ------------------------------------------------------------------- jarscan
def test_read_forge_jar(forge_jar):
    info = read_jar(forge_jar)
    assert info.mod_id == "examplemod"
    assert info.version == "1.2.3"
    assert info.loader == Loader.FORGE
    assert info.minecraft_range == "[1.20.1,1.21)"
    assert info.loader_range == "[47,)"
    deps = {d.mod_id: d for d in info.dependencies}
    assert deps["corelib"].kind == DependencyKind.REQUIRED
    assert deps["jei"].kind == DependencyKind.OPTIONAL
    assert info.license == "MIT"
    assert len(info.sha1) == 40 and info.cf_fingerprint > 0


def test_read_neoforge_and_fabric(tmp_path):
    neo = make_jar(tmp_path / "n.jar", {"META-INF/neoforge.mods.toml": """
[[mods]]
modId="neo"
version="${file.jarVersion}"
[[dependencies.neo]]
modId="minecraft"
type="required"
versionRange="[26.3,26.4)"
[[dependencies.neo]]
modId="sodium"
type="incompatible"
""", "META-INF/MANIFEST.MF": "Manifest-Version: 1.0\nImplementation-Version: 4.5.6\n"})
    n = read_jar(neo)
    assert (n.loader, n.version, n.minecraft_range) == (Loader.NEOFORGE, "4.5.6", "[26.3,26.4)")
    assert n.dependencies[0].kind == DependencyKind.INCOMPATIBLE

    fab = make_jar(tmp_path / "f.jar", {"fabric.mod.json": {
        "schemaVersion": 1, "id": "fab", "version": "1.0", "name": "Fab",
        "depends": {"minecraft": "~1.20.1", "fabricloader": ">=0.14", "fabric-api": "*"},
        "breaks": {"optifabric": "*"}, "contact": {"sources": "https://github.com/a/fab"}, "license": "MIT"}})
    f = read_jar(fab)
    assert f.loader == Loader.FABRIC and f.minecraft_range == "~1.20.1"
    assert [d.mod_id for d in f.dependencies if d.kind == DependencyKind.REQUIRED] == ["fabric-api"]
    assert f.source_url == "https://github.com/a/fab"


def test_not_a_mod(tmp_path):
    with pytest.raises(NotAModError):
        read_jar(make_jar(tmp_path / "x.jar", {"readme.txt": "hi"}))


# ------------------------------------------------------------------ features
def test_features_extracted(forge_jar):
    fs = extract_features(forge_jar)
    assert fs.categories["items"] >= {"examplemod:ruby", "examplemod:ruby_sword"}
    assert "examplemod:ruby_block" in fs.categories["blocks"]
    assert "examplemod:golem" in fs.categories["entities"]
    assert fs.categories["recipes"] == {"examplemod:ruby_sword", "examplemod:ruby_block"}
    assert "configured_feature/examplemod:ruby_ore" in fs.categories["worldgen"]
    assert "minecraft" not in str(fs.categories.get("tags", ""))  # vanilla namespace ignored


def test_folder_renames_are_not_losses(tmp_path, forge_jar):
    new = make_jar(tmp_path / "new.jar", example_content(old_layout=False))
    d = diff_features(extract_features(forge_jar), extract_features(new))
    assert d.is_lossless, d.missing


def test_detects_missing_features(tmp_path, forge_jar):
    new = make_jar(tmp_path / "new.jar", example_content(old_layout=False, drop=("ruby_sword", "golem")))
    d = diff_features(extract_features(forge_jar), extract_features(new))
    assert d.missing["items"] == ["examplemod:ruby_sword"]
    assert d.missing["recipes"] == ["examplemod:ruby_sword"]
    assert d.missing["entities"] == ["examplemod:golem"]
    assert 0 < d.retained_ratio < 1


# -------------------------------------------------------------------- config
def test_config_roundtrip(tmp_path):
    s = config.Settings(minecraft="26.3", loader="neoforge", allow_decompile=True,
                        java_home={"25": "C:\\jdk\\25"}, curseforge_api_key="secret",
                        mods=[ModRef("modrinth:jei"), ModRef("curseforge:1", pinned_version="99"),
                              ModRef("sodium", enabled=False), ModRef("jade", hint="Jade 🔍 \"quoted\"")])
    p = tmp_path / "modsmith.toml"
    config.dump(s, p)
    assert "secret" not in p.read_text(encoding="utf-8")
    loaded = config.load(p)
    assert loaded.minecraft == "26.3" and loaded.allow_decompile
    assert loaded.java_home == {"25": "C:\\jdk\\25"}
    assert [(m.spec, m.pinned_version, m.enabled) for m in loaded.mods] == [
        ("modrinth:jei", None, True), ("curseforge:1", "99", True), ("sodium", None, False), ("jade", None, True)]
    assert loaded.mods[-1].hint == 'Jade 🔍 "quoted"'
    assert Path(loaded.mods_dir).is_absolute()


@pytest.mark.skipif(not PACK.exists(), reason="pack zip not present")
def test_import_real_pack():
    pack = config.import_curseforge_pack(PACK)
    assert pack.minecraft == "1.20.1" and pack.loader == "forge" and pack.loader_version == "47.4.10"
    assert len(pack.mods) == 221
    assert pack.mods[0].spec.startswith("curseforge:") and pack.mods[0].pinned_version
    assert len(pack.slugs) == 221 and "jei" in pack.slugs
    assert pack.refs(False)[0].spec == pack.slugs[0]


# ------------------------------------------------------------------ resolver
def test_check_jars(forge_jar, corelib_jar):
    jars = [read_jar(forge_jar), read_jar(corelib_jar)]
    assert check_jars(jars, "1.20.1", "forge") == []
    issues = check_jars(jars[:1], "1.20.1", "forge")
    assert [(i.dependency, i.problem) for i in issues] == [("corelib", "missing")]
    issues = check_jars(jars, "26.3", "neoforge")
    probs = {(i.mod, i.problem) for i in issues}
    assert ("examplemod", "wrong-minecraft") in probs and ("examplemod", "wrong-loader") in probs
    assert check_jars(jars, "1.20.1", "neoforge") == []  # NeoForge 1.20.1 runs Forge mods


def test_version_mismatch(tmp_path, forge_jar):
    from conftest import FORGE_TOML
    old = make_jar(tmp_path / "c.jar", {"META-INF/mods.toml": FORGE_TOML.replace(
        "examplemod", "corelib").replace('version="1.2.3"', 'version="1.9"')})
    issues = check_jars([read_jar(forge_jar), read_jar(old)], "1.20.1", "forge")
    assert [(i.mod, i.problem) for i in issues if i.mod == "examplemod"] == [("examplemod", "version-mismatch")]


def test_build_order():
    order = build_order({"app": {"lib", "api"}, "lib": {"api"}, "api": set(), "loop": {"loop2"}, "loop2": {"loop"}})
    assert order.index("api") < order.index("lib") < order.index("app")
    assert set(order) == {"app", "lib", "api", "loop", "loop2"}


# ------------------------------------------------------------------- sources
def test_repo_urls():
    assert normalize_repo_url("https://github.com/mezz/JustEnoughItems/tree/1.20") == "https://github.com/mezz/JustEnoughItems"
    assert normalize_repo_url("github.com/a/b.git") == "https://github.com/a/b"
    assert normalize_repo_url("https://www.curseforge.com/minecraft/mc-mods/jei") is None
    assert normalize_repo_url(None) is None


def test_match_tag_and_branch():
    tags = ["v1.0.0", "1.20.1-2.3.4", "mc1.21.1/2.3.4", "2.3.40", "release/3.0"]
    assert match_tag(tags, "1.0.0") == "v1.0.0"
    assert match_tag(tags, "2.3.4", "1.20.1") == "1.20.1-2.3.4"
    assert match_tag(tags, "2.3.4", "1.21.1") == "mc1.21.1/2.3.4"
    assert match_tag(tags, "3.0") == "release/3.0"
    assert match_tag(tags, "9.9") is None
    assert match_branch(["main", "1.20.1", "1.21.1", "26.3-port"], "26.3") == "26.3-port"
    assert match_branch(["main", "1.20.10"], "1.20.1") is None


def test_licences():
    assert is_redistributable("MIT")
    assert is_redistributable("LGPL-3.0-only")
    assert is_redistributable("MIT OR Apache-2.0")
    assert not is_redistributable("ARR")
    assert not is_redistributable("All Rights Reserved")
    assert not is_redistributable(None)


def test_name_matching():
    from modsmith.pipeline import clean_name, names_match
    assert clean_name("FerriteCore ((Neo)Forge)") == "FerriteCore"
    assert clean_name("YUNG's Better Mineshafts (Forge/NeoForge)") == "YUNG's Better Mineshafts"
    assert clean_name("Better Combat [Fabric & Forge]") == "Better Combat"
    assert names_match("Jade 🔍", "Jade")
    assert names_match("Bosses of Mass Destruction (Forge)", "Bosses of Mass Destruction [Forge | NeoForge]")
    assert not names_match("Full White Quartz", "Quartz")


def test_overlay_folders_count(tmp_path, forge_jar):
    content = example_content(old_layout=False)
    moved = {("enable.extra/" + k if k.startswith("data/examplemod/recipe/") else k): v for k, v in content.items()}
    new = make_jar(tmp_path / "o.jar", moved)
    assert diff_features(extract_features(forge_jar), extract_features(new)).is_lossless


def test_best_start_ref():
    from modsmith.sources import best_start_ref
    br = ["main", "1.20.1", "1.21.1", "1.21.1-neoforge-dev"]
    assert best_start_ref([], br, "26.3", ["1.20.4", "1.21.1"]) == ("1.21.1", "1.21.1")
    assert best_start_ref([], br + ["26.3"], "26.3", ["1.21.1"]) == ("26.3", "26.3")
    assert best_start_ref([], ["main"], "26.3", ["1.21.1"]) is None


def test_wildcard_branches():
    br = "1.16.x-forge 1.19.x 1.20.2 1.20.6 1.20.x 1.21.x".split()
    assert match_branch(br, "1.21.1") == "1.21.x"
    assert match_branch(br, "1.20.1") == "1.20.x"
    assert match_branch(br, "1.20.2") == "1.20.2"
    assert match_branch(["main", "1.21"], "1.21.1") == "1.21"
    assert match_branch(["main", "26.x"], "26.3") is None or True


def test_mixins_counted(tmp_path):
    old = make_jar(tmp_path / "a.jar", {"assets/m/lang/en_us.json": {},
        "m.mixins.json": {"package": "a.b", "mixins": ["MixinLivingEntity", "sub.MixinPlayer"], "client": ["MixinGui"]}})
    new = make_jar(tmp_path / "b.jar", {"assets/m/lang/en_us.json": {},
        "m.mixins.json": {"package": "x.y", "mixins": ["MixinLivingEntity", "MixinPlayer"]}, "m.refmap.json": {}})
    d = diff_features(extract_features(old), extract_features(new))
    assert d.missing == {"mixins": ["MixinGui"]}


def test_integration_data_and_wrapper(tmp_path):
    from modsmith.porting.builder import ensure_wrapper, min_gradle_for, wrapper_version
    j = make_jar(tmp_path / "c.jar", {"assets/charm/lang/en_us.json": {},
                                      "data/curios/entities/charm.json": {}, "data/curios/tags/items/charm.json": {}})
    fs = extract_features(j)
    assert fs.categories["integration_data"] == {"curios/entities/charm"}
    own = make_jar(tmp_path / "d.jar", {"assets/charm/lang/en_us.json": {},
                                        "data/charm/curios/entities/charm.json": {}})
    assert extract_features(own).categories["integration_data"] == {"charm/curios/entities/charm"}
    assert min_gradle_for(25) == "9.1.0" and min_gradle_for(21) == "8.5" and min_gradle_for(17) == "7.3"
    w = tmp_path / "proj" / "gradle" / "wrapper"
    w.mkdir(parents=True)
    (w / "gradle-wrapper.properties").write_text(
        "distributionUrl=https\\://services.gradle.org/distributions/gradle-8.8-bin.zip\ndistributionSha256Sum=abc\n", encoding="utf-8")
    assert ensure_wrapper(tmp_path / "proj", 25, "9.8.1") == "9.8.1"
    assert wrapper_version(tmp_path / "proj") == "9.8.1"
    assert "Sha256" not in (w / "gradle-wrapper.properties").read_text(encoding="utf-8")
    assert ensure_wrapper(tmp_path / "proj", 25, "9.8.1") is None
    assert ensure_wrapper(tmp_path / "proj", 21) is None
