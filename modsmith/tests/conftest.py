import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def make_jar(path: Path, files: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            if isinstance(content, (dict, list)):
                content = json.dumps(content)
            zf.writestr(name, content)
    return path


FORGE_TOML = """
modLoader="javafml"
loaderVersion="[47,)"
license="MIT"
[[mods]]
modId="examplemod"
version="1.2.3"
displayName="Example Mod"
displayURL="https://github.com/example/examplemod"
[[dependencies.examplemod]]
    modId="forge"
    mandatory=true
    versionRange="[47,)"
[[dependencies.examplemod]]
    modId="minecraft"
    mandatory=true
    versionRange="[1.20.1,1.21)"
[[dependencies.examplemod]]
    modId="corelib"
    mandatory=true
    versionRange="[2.0,3.0)"
[[dependencies.examplemod]]
    modId="jei"
    mandatory=false
    versionRange="*"
"""


def example_content(ns="examplemod", old_layout=True, drop=()):
    rec = "recipes" if old_layout else "recipe"
    loot = "loot_tables/blocks" if old_layout else "loot_table/blocks"
    files = {
        f"assets/{ns}/lang/en_us.json": {
            f"item.{ns}.ruby": "Ruby", f"item.{ns}.ruby_sword": "Ruby Sword",
            f"block.{ns}.ruby_block": "Block of Ruby", f"entity.{ns}.golem": "Ruby Golem",
            f"item.{ns}.ruby.tooltip": "Shiny", "itemGroup.examplemod": "Example",
        },
        f"assets/{ns}/blockstates/ruby_block.json": {},
        f"assets/{ns}/models/item/ruby.json": {},
        f"data/{ns}/{rec}/ruby_sword.json": {},
        f"data/{ns}/{rec}/ruby_block.json": {},
        f"data/{ns}/{loot}/ruby_block.json": {},
        f"data/{ns}/worldgen/configured_feature/ruby_ore.json": {},
        f"data/minecraft/tags/{'blocks' if old_layout else 'block'}/mineable/pickaxe.json": {},
    }
    for d in drop:
        files = {k: v for k, v in files.items() if d not in k}
        lang = files.get(f"assets/{ns}/lang/en_us.json")
        if lang:
            files[f"assets/{ns}/lang/en_us.json"] = {k: v for k, v in lang.items() if d not in k}
    return files


@pytest.fixture
def forge_jar(tmp_path):
    return make_jar(tmp_path / "mods" / "examplemod-1.2.3.jar",
                    {"META-INF/mods.toml": FORGE_TOML, **example_content()})


@pytest.fixture
def corelib_jar(tmp_path):
    toml = FORGE_TOML.replace("examplemod", "corelib").replace('version="1.2.3"', 'version="2.5.0"')
    return make_jar(tmp_path / "mods" / "corelib-2.5.0.jar", {"META-INF/mods.toml": toml})
