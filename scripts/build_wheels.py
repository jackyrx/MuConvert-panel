#!/usr/bin/env python3
"""Build universal and per-platform wheels for MuConvert Panel.

Examples:
  python3 scripts/build_wheels.py --universal
  python3 scripts/build_wheels.py --rid osx-arm64
  python3 scripts/build_wheels.py --all
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "muconvert" / "vendor" / "muconvert"
DIST = ROOT / "dist"

PLATFORMS = {
    "osx-arm64": "macosx_11_0_arm64",
    "osx-x64": "macosx_10_15_x86_64",
    "win-x64": "win_amd64",
    "linux-x64": "manylinux2014_x86_64",
}

SETUP_PY = r'''
import os
from setuptools import setup

try:
    from wheel.bdist_wheel import bdist_wheel
except Exception:
    from setuptools.command.bdist_wheel import bdist_wheel

class BDistWheel(bdist_wheel):
    def finalize_options(self):
        super().finalize_options()
        self.root_is_pure = False

    def get_tag(self):
        return "py3", "none", os.environ["MUCONVERT_WHEEL_PLAT"]

setup(cmdclass={"bdist_wheel": BDistWheel})
'''


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    print("$", " ".join(str(x) for x in cmd))
    subprocess.run(cmd, cwd=str(cwd), env=env, check=True)


def copy_common(stage: Path) -> None:
    for name in ("pyproject.toml", "README.md", "RELEASE.md", "VERSION", "MANIFEST.in", "LICENSE", "NOTICE"):
        shutil.copy2(ROOT / name, stage / name)
    pkg = stage / "muconvert"
    pkg.mkdir(parents=True, exist_ok=True)
    for name in ("__init__.py", "__main__.py"):
        shutil.copy2(ROOT / "muconvert" / name, pkg / name)


def build_universal() -> Path:
    DIST.mkdir(parents=True, exist_ok=True)
    out = DIST / "_universal"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    run(
        [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", str(out)],
        ROOT,
    )
    wheels = sorted(out.glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected one universal wheel, got {wheels}")
    target = DIST / wheels[0].name
    shutil.copy2(wheels[0], target)
    print(f"universal: {target} ({target.stat().st_size / 1024 / 1024:.1f} MB)")
    return target


def build_rid(rid: str) -> Path:
    if rid not in PLATFORMS:
        raise SystemExit(f"unsupported RID: {rid}")
    src = ASSETS / rid
    if not src.is_dir():
        raise SystemExit(f"missing binary directory: {src}")

    with tempfile.TemporaryDirectory(prefix=f"muconvert-wheel-{rid}-") as tmp:
        stage = Path(tmp)
        copy_common(stage)
        dest = stage / "muconvert" / "vendor" / "muconvert" / rid
        shutil.copytree(src, dest)
        (stage / "setup.py").write_text(SETUP_PY, encoding="utf-8")

        out = stage / "wheelout"
        out.mkdir()
        env = os.environ.copy()
        env["MUCONVERT_WHEEL_PLAT"] = PLATFORMS[rid]
        run(
            [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", str(out)],
            stage,
            env,
        )
        wheels = sorted(out.glob("*.whl"))
        if len(wheels) != 1:
            raise SystemExit(f"expected one wheel for {rid}, got {wheels}")
        wheel = wheels[0]
        target = DIST / wheel.name
        shutil.copy2(wheel, target)

        with zipfile.ZipFile(target) as zf:
            binaries = [
                name
                for name in zf.namelist()
                if "/vendor/muconvert/" in name and not name.endswith("/")
            ]
        if len(binaries) != 1:
            raise SystemExit(f"{target.name} contains {len(binaries)} binaries: {binaries}")
        print(
            f"{rid}: {target.name} "
            f"({target.stat().st_size / 1024 / 1024:.1f} MB), binary={binaries[0]}"
        )
        return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universal", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--rid", choices=sorted(PLATFORMS))
    args = parser.parse_args()

    if not (args.universal or args.all or args.rid):
        parser.print_help()
        return 2

    DIST.mkdir(parents=True, exist_ok=True)
    if args.universal or args.all:
        build_universal()
    if args.all:
        for rid in PLATFORMS:
            build_rid(rid)
    elif args.rid:
        build_rid(args.rid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
