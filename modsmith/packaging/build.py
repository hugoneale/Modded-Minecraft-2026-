"""Build the standalone Modsmith app (one file, no Python needed to run it).

    python -m pip install pyinstaller
    python packaging/build.py            # -> dist/Modsmith.exe on Windows

Run it on the OS you are building for: PyInstaller does not cross-compile,
so a Windows .exe has to be built on Windows (the GitHub Actions workflow
in .github/workflows/build-exe.yml does that automatically).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                      # modsmith/
REPO = ROOT.parent
PACK = REPO / "16MARSHALST.zip"


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    from modsmith import __version__, art

    work = ROOT / "build"
    work.mkdir(exist_ok=True)
    icon = work / "modsmith.ico"
    icon.write_bytes(art.ico())
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
           "--name", "Modsmith", "--icon", str(icon),
           "--paths", str(ROOT / "src"), "--collect-submodules", "modsmith",
           "--workpath", str(work / "pyinstaller"), "--specpath", str(work),
           "--distpath", str(ROOT / "dist")]
    if PACK.is_file():
        cmd += ["--add-data", f"{PACK}{os.pathsep}."]
    else:
        print(f"warning: {PACK.name} not found; the app will start with an empty list")
    if sys.platform == "win32":
        version_file = work / "version.txt"
        version_file.write_text(_version_info(__version__), encoding="utf-8")
        cmd += ["--version-file", str(version_file)]
    cmd.append(str(HERE / "modsmith_app.py"))
    print(" ".join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT)
    exe = ROOT / "dist" / ("Modsmith.exe" if sys.platform == "win32" else "Modsmith")
    if sys.platform == "darwin" and (ROOT / "dist" / "Modsmith.app").exists():
        exe = ROOT / "dist" / "Modsmith.app"
    print(f"\nBuilt {exe}")
    return 0


def _version_info(version: str) -> str:
    nums = tuple(int(p) for p in (version.split(".") + ["0"] * 4)[:4] if p.isdigit())
    nums = (nums + (0, 0, 0, 0))[:4]
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Modsmith'),
      StringStruct('FileDescription', 'Modsmith - the Minecraft mod forge'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'Modsmith'),
      StringStruct('OriginalFilename', 'Modsmith.exe'),
      StringStruct('ProductName', 'Modsmith'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
"""


if __name__ == "__main__":
    sys.exit(main())
