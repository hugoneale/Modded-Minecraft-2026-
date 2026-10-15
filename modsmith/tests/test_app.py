"""The standalone app's pieces that don't need a display: pixel art, icon,
bundled pack lookup and default folders."""
import struct
import sys
from pathlib import Path

from modsmith import art, config


def test_icon_is_a_valid_ico_of_pngs():
    data = art.ico()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1) and count == 7
    sizes = []
    for i in range(count):
        w, h, _, _, planes, bpp, length, offset = struct.unpack("<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
        assert data[offset:offset + 8] == b"\x89PNG\r\n\x1a\n"
        assert offset + length <= len(data)
        width, height = struct.unpack(">II", data[offset + 16:offset + 24])
        assert width == height == (w or 256)
        sizes.append(width)
    assert sizes == [16, 24, 32, 48, 64, 128, 256]


def test_scene_fits_the_icon_grid_and_uses_the_palette():
    for px in (art.scene(), art.scene(hammer_dy=2)):
        assert all(0 <= x < 32 and 0 <= y < 32 for x, y in px)
        assert set(px.values()) <= set(art.PALETTE)


def test_logo_spells_the_name():
    px, width = art.logo("MODSMITH")
    assert width == 7 * (5 + 1) + (3 + 1)  # seven 5-wide glyphs and a 3-wide "I", each + 1 px gap
    assert max(y for _, y in px) == 7  # 7 rows plus the drop shadow


def test_bundled_pack_found_from_source_checkout():
    pack = config.bundled_pack()
    assert pack is not None and pack.name == config.BUNDLED_PACK
    imported = config.import_curseforge_pack(pack)
    assert imported.minecraft == "1.20.1" and len(imported.slugs) == 221


def test_bundled_pack_prefers_pyinstaller_dir(tmp_path, monkeypatch):
    (tmp_path / config.BUNDLED_PACK).write_bytes(b"zip")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert config.bundled_pack() == tmp_path / config.BUNDLED_PACK


def test_bundled_pack_ignores_current_directory(tmp_path, monkeypatch):
    (tmp_path / config.BUNDLED_PACK).write_bytes(b"zip")
    monkeypatch.chdir(tmp_path)
    assert config.bundled_pack() != Path(config.BUNDLED_PACK)


def test_default_mods_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "minecraft_dir", lambda: tmp_path / ".minecraft")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert config.default_mods_dir() == tmp_path / "Modsmith" / "mods"
    (tmp_path / ".minecraft").mkdir()
    assert config.default_mods_dir() == tmp_path / ".minecraft" / "mods"


def test_session_roundtrip_keeps_unticked_mods(tmp_path):
    from modsmith.models import ModRef
    s = config.Settings(minecraft="26.3", loader="neoforge", from_minecraft="1.20.1", mods_dir=str(tmp_path / "mods"))
    s.mods = [ModRef(spec="modrinth:jei", hint="JEI"), ModRef(spec="sodium", enabled=False)]
    config.dump(s, tmp_path / "session.toml")
    back = config.load(tmp_path / "session.toml")
    assert [(r.spec, r.enabled, r.hint) for r in back.mods] == [("modrinth:jei", True, "JEI"), ("sodium", False, None)]
    assert (back.minecraft, back.from_minecraft) == ("26.3", "1.20.1")
