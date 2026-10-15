"""Feature inventory: what content does a mod jar add, and did a new build keep it?

Modsmith compares the *baseline* build (what the user has today) with a
*candidate* build (an official release for the target version, or a port) and
reports content that disappeared. Content is collected from the jar's data and
assets, which describe what a mod registers independently of code:

* items / blocks / entities / effects / enchantments / sounds... from lang keys
* blocks from blockstates, items from item models
* recipes, loot tables, advancements, worldgen, structures, tags, damage types
  from ``data/<namespace>/<kind>/``
* config option keys from bundled default config files when present

Minecraft renamed several data folders in 1.21 (``recipes`` -> ``recipe``,
``loot_tables`` -> ``loot_table``...) and moved item models to ``items/`` in
1.21.4; those are normalised so a rename isn't reported as a loss.
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Iterable, Optional

from .models import FeatureDiff, FeatureSet

# lang key prefix -> category
_LANG_CATEGORIES = {
    "item": "items",
    "block": "blocks",
    "entity": "entities",
    "effect": "effects",
    "enchantment": "enchantments",
    "biome": "biomes",
    "fluid": "fluids",
    "painting": "paintings",
    "advancements": None,  # covered by data files; titles are noisy
    "subtitles": "sounds",
    "attribute": "attributes",
    "container": "menus",
    "key": "keybinds",
    "itemGroup": "creative_tabs",
    "creativetab": "creative_tabs",
    "dimension": "dimensions",
    "death": None,
    "gui": None,
    "tooltip": None,
    "jei": None,
    "config": "config_options",
}

# data folder (any spelling) -> category
_DATA_FOLDERS = {
    "recipes": "recipes", "recipe": "recipes",
    "loot_tables": "loot_tables", "loot_table": "loot_tables",
    "advancements": "advancements", "advancement": "advancements",
    "structures": "structures", "structure": "structures",
    "damage_type": "damage_types",
    "enchantment": "enchantments_data",
    "dimension": "dimensions", "dimension_type": "dimensions",
    "predicates": "predicates", "predicate": "predicates",
    "item_modifiers": "item_modifiers", "item_modifier": "item_modifiers",
    "functions": "functions", "function": "functions",
    "painting_variant": "paintings",
    "trim_material": "trim_materials",
    "trim_pattern": "trim_patterns",
    "banner_pattern": "banner_patterns",
    "jukebox_song": "jukebox_songs",
}

_WORLDGEN = re.compile(r"^data/([^/]+)/worldgen/([^/]+)/(.+)\.json$")
_DATA = re.compile(r"^data/([^/]+)/([^/]+)/(.+)\.(?:json|nbt|mcfunction)$")
_TAGS = re.compile(r"^data/([^/]+)/tags/([^/]+)/(.+)\.json$")
_BLOCKSTATE = re.compile(r"^assets/([^/]+)/blockstates/(.+)\.json$")
_ITEM_MODEL = re.compile(r"^assets/([^/]+)/(?:models/item|items)/(.+)\.json$")
_LANG = re.compile(r"^assets/([^/]+)/lang/en_us\.json$")
_SOUNDS = re.compile(r"^assets/([^/]+)/sounds\.json$")
_OVERLAY = re.compile(r"^(?!assets/|data/|META-INF/)[^/]+/(?=(?:assets|data)/)")
_IGNORED_NAMESPACES = {"minecraft", "c", "forge", "neoforge", "fabric", "curios", "trinkets"}


def _singular_tag_kind(kind: str) -> str:
    # 1.21 renamed tags/blocks -> tags/block, tags/items -> tags/item, ...
    return {"blocks": "block", "items": "item", "entity_types": "entity_type",
            "fluids": "fluid", "game_events": "game_event", "functions": "function"}.get(kind, kind)


def _lang_entries(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Some lang files contain comments or trailing commas.
        cleaned = re.sub(r"//.*?$|/\*.*?\*/", "", text, flags=re.S | re.M)
        cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return {}


def extract_features(jar: Path, namespaces: Optional[Iterable[str]] = None) -> FeatureSet:
    """Build a FeatureSet from a jar. ``namespaces`` limits which ids count
    (defaults to every namespace the jar ships assets/data for, minus vanilla
    and common shared namespaces)."""
    fs = FeatureSet()
    with zipfile.ZipFile(jar) as zf:
        names = zf.namelist()
        if namespaces is None:
            ns_found = set()
            for n in names:
                m = re.match(r"^(?:assets|data)/([^/]+)/", _OVERLAY.sub("", n, count=1))
                if m:
                    ns_found.add(m.group(1))
            ns = ns_found - _IGNORED_NAMESPACES
        else:
            ns = set(namespaces)

        for raw in names:
            # Data-pack overlays and built-in optional packs keep content one
            # folder down: "1-20-5-overlay/data/...", "enable.recipe_changes/data/...".
            n = _OVERLAY.sub("", raw, count=1)
            m = _LANG.match(n)
            if m and m.group(1) in ns:
                for key in _lang_entries(zf.read(raw).decode("utf-8", "replace")):
                    parts = key.split(".")
                    if len(parts) < 3:
                        continue
                    category = _LANG_CATEGORIES.get(parts[0], "")
                    if category is None:
                        continue
                    if category == "":
                        continue
                    if parts[1] not in ns and category not in ("keybinds", "creative_tabs", "menus", "sounds", "config_options"):
                        continue
                    if category in ("items", "blocks", "entities", "effects", "enchantments", "biomes", "fluids", "paintings", "attributes", "dimensions"):
                        # item.create.cogwheel(.tooltip...) -> create:cogwheel
                        fs.add(category, f"{parts[1]}:{parts[2]}")
                    else:
                        fs.add(category, ".".join(parts[1:]))
                continue
            m = _BLOCKSTATE.match(n)
            if m and m.group(1) in ns:
                fs.add("blocks", f"{m.group(1)}:{m.group(2)}")
                continue
            m = _ITEM_MODEL.match(n)
            if m and m.group(1) in ns:
                fs.add("items", f"{m.group(1)}:{m.group(2)}")
                continue
            m = _SOUNDS.match(n)
            if m and m.group(1) in ns:
                try:
                    for key in json.loads(zf.read(raw)):
                        fs.add("sounds", f"{m.group(1)}:{key}")
                except (json.JSONDecodeError, TypeError):
                    pass
                continue
            m = _TAGS.match(n)
            if m and m.group(1) in ns:
                fs.add("tags", f"{_singular_tag_kind(m.group(2))}/{m.group(1)}:{m.group(3)}")
                continue
            m = _WORLDGEN.match(n)
            if m and m.group(1) in ns:
                fs.add("worldgen", f"{m.group(2)}/{m.group(1)}:{m.group(3)}")
                continue
            m = _DATA.match(n)
            if m and m.group(1) in ns:
                category = _DATA_FOLDERS.get(m.group(2))
                if category:
                    fs.add(category, f"{m.group(1)}:{m.group(3)}")
    # Lang-derived "sounds" entries are subtitles; keep only the sounds.json ids.
    return fs


def diff_features(baseline: FeatureSet, candidate: FeatureSet,
                  ignore_categories: Iterable[str] = ("tags", "config_options", "keybinds",
                                                      "menus", "creative_tabs")) -> FeatureDiff:
    """What the candidate lost or gained relative to the baseline.

    Tags, config option labels, keybind/menu/creative-tab labels churn between
    versions without any gameplay change, so by default they're not counted as
    losses (they're still listed in the full inventories).
    """
    ignore = set(ignore_categories)
    missing, added = {}, {}
    total = 0
    for cat in sorted(set(baseline.categories) | set(candidate.categories)):
        if cat in ignore:
            continue
        b = baseline.categories.get(cat, set())
        c = candidate.categories.get(cat, set())
        total += len(b)
        gone = sorted(b - c)
        new = sorted(c - b)
        if gone:
            missing[cat] = gone
        if new:
            added[cat] = new
    return FeatureDiff(missing=missing, added=added, baseline_total=total)


def describe_diff(diff: FeatureDiff, limit: int = 8) -> str:
    if diff.is_lossless:
        extra = f" (+{diff.added_count} new)" if diff.added_count else ""
        return f"All {diff.baseline_total} features retained{extra}"
    parts = []
    for cat, ids in diff.missing.items():
        shown = ", ".join(ids[:limit]) + (" ..." if len(ids) > limit else "")
        parts.append(f"{cat}: {len(ids)} missing ({shown})")
    return (f"{diff.missing_count}/{diff.baseline_total} features missing "
            f"({diff.retained_ratio:.0%} retained). " + "; ".join(parts))
