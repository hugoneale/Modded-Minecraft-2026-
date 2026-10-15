"""Command line interface.

    modsmith gui                                  open the desktop app
    modsmith scan    <mods-folder>                list installed mods
    modsmith check   [-c modsmith.toml] [--mc 26.3] [--loader neoforge]
    modsmith update  [...]                        check + install official builds
    modsmith port    [...] [--decompile]          check + force-port what has no official build
    modsmith install <spec>... --mc 26.3 --mods <folder>
    modsmith import  <pack.zip|.mrpack> -o modsmith.toml
    modsmith versions                             list Minecraft versions
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, config, report
from .config import Settings
from .jarscan import scan_folder
from .models import ModRef, Status


def _settings(args) -> Settings:
    if getattr(args, "config", None):
        s = config.load(Path(args.config))
    else:
        s = Settings()
        if getattr(args, "mods", None):
            s.mods_dir = args.mods
    for attr, val in (("minecraft", getattr(args, "mc", None)), ("loader", getattr(args, "loader", None)),
                      ("mods_dir", getattr(args, "mods", None)), ("output_dir", getattr(args, "out", None)),
                      ("from_minecraft", getattr(args, "from_mc", None)),
                      ("from_loader", getattr(args, "from_loader", None))):
        if val:
            setattr(s, attr, val)
    if getattr(args, "decompile", False):
        s.allow_decompile = True
    if getattr(args, "policy", None):
        s.feature_policy = args.policy
    if getattr(args, "no_verify", False):
        s.verify_features = False
    s.validate()
    return s


def _refs(s: Settings, args) -> list[ModRef]:
    refs = list(s.mods)
    for spec in getattr(args, "spec", None) or []:
        refs.append(ModRef(spec=spec))
    if not refs and s.mods_dir and Path(s.mods_dir).is_dir():
        refs = [ModRef(spec=f"file:{p}") for p in sorted(Path(s.mods_dir).glob("*.jar"))]
    return refs


def _run(args, do_port: bool, do_install: bool) -> int:
    from .pipeline import Modsmith
    s = _settings(args)
    app = Modsmith(s)
    refs = _refs(s, args)
    if not refs:
        print("No mods selected: pass a config with mods = [...], spec arguments, or --mods <folder>")
        return 2
    results, issues = app.run(refs, do_port=do_port)
    md = report.write(results, issues, app.target(), app.out_dir)
    if do_install:
        app.install(results, include_feature_loss=s.feature_policy != "strict")
    print()
    print(report.markdown(results, issues, app.target()))
    print(f"Report: {md}")
    bad = sum(r.status in (Status.PORT_FAILED, Status.NO_SOURCE, Status.UNRESOLVED, Status.NO_OFFICIAL)
              for r in results)
    return 1 if bad else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="modsmith", description="Install, check, update and port Minecraft mods.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")

    def common(sp, specs=False):
        sp.add_argument("-c", "--config", help="modsmith.toml project file")
        sp.add_argument("--mc", help="target Minecraft version (default: latest release)")
        sp.add_argument("--loader", choices=["neoforge", "forge", "fabric", "quilt"])
        sp.add_argument("--mods", help="mods folder")
        sp.add_argument("--from-mc", help="version the selected mods are on now (for mods given by id)")
        sp.add_argument("--from-loader", choices=["neoforge", "forge", "fabric", "quilt"])
        sp.add_argument("--out", help="output folder for reports and ports")
        sp.add_argument("--policy", choices=list(config.FEATURE_POLICIES),
                        help="when an official build drops content: warn, prefer-port, strict")
        sp.add_argument("--no-verify", action="store_true", help="skip downloading builds to compare content")
        if specs:
            sp.add_argument("spec", nargs="*", help="modrinth:<slug>, curseforge:<id>, file:<jar> or a slug")

    sub.add_parser("gui", help="open the desktop app")
    sp = sub.add_parser("scan", help="list mods in a folder")
    sp.add_argument("folder")
    common(sub.add_parser("check", help="report what's available for the target version"), specs=True)
    common(sub.add_parser("update", help="check and install official builds"), specs=True)
    sp = sub.add_parser("port", help="check, then force-port mods with no official build")
    common(sp, specs=True)
    sp.add_argument("--decompile", action="store_true", help="allow decompiling mods without source (warning!)")
    sp.add_argument("--install", action="store_true", help="install results into the mods folder")
    common(sub.add_parser("install", help="install mods by id for a version"), specs=True)
    sp = sub.add_parser("import", help="turn a CurseForge zip or .mrpack into modsmith.toml")
    sp.add_argument("pack")
    sp.add_argument("-o", "--output", default="modsmith.toml")
    sp.add_argument("--mc", help="target Minecraft version to write into the file")
    sub.add_parser("versions", help="list Minecraft release versions")

    args = p.parse_args(argv)
    if args.cmd in (None, "gui"):
        from .gui import main as gui_main
        return gui_main()
    if args.cmd == "scan":
        for j in scan_folder(Path(args.folder), on_error=lambda f, e: print(f"! {f.name}: {e}")):
            print(f"{j.mod_id:30} {j.version:20} {j.loader.value if j.loader else '?':9} mc {j.minecraft_range}")
        return 0
    if args.cmd == "versions":
        from .http import Http
        from .platform import Platform
        print("\n".join(Platform(Http()).minecraft_versions()[:40]))
        return 0
    if args.cmd == "import":
        path = Path(args.pack)
        pack = config.import_modrinth_pack(path) if path.suffix == ".mrpack" else config.import_curseforge_pack(path)
        s = Settings(minecraft=args.mc or "", from_minecraft=pack.minecraft, from_loader=pack.loader,
                     mods=pack.refs(bool(Settings().cf_key)))
        config.dump(s, Path(args.output))
        print(f"Wrote {args.output}: {len(s.mods)} mods from '{pack.name}' "
              f"(Minecraft {pack.minecraft}, {pack.loader} {pack.loader_version})")
        if pack.slugs and not Settings().cf_key:
            print("No CurseForge API key set, so mods are listed by slug and looked up on Modrinth. "
                  "Set CURSEFORGE_API_KEY and re-import for exact CurseForge matching.")
        return 0
    if args.cmd == "check":
        return _run(args, do_port=False, do_install=False)
    if args.cmd in ("update", "install"):
        return _run(args, do_port=False, do_install=True)
    if args.cmd == "port":
        return _run(args, do_port=True, do_install=args.install)
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
