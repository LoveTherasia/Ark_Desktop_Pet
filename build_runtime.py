from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / "vendor" / "spine-runtimes"
RUNTIME = VENDOR / "spine-c" / "spine-c"
BUILD = ROOT / ".build"
OBJECTS = BUILD / "objects"
BRIDGE = ROOT / "spine_bridge.cpp"
RUNTIME_COMMIT = "8b4844bd4b193ba9e54487ed397a777993cbad56"


def run(command: list[str]) -> None:
    print("+", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    gcc = shutil.which("gcc")
    gxx = shutil.which("g++")
    git = shutil.which("git")
    missing = [name for name, path in (("gcc", gcc), ("g++", gxx), ("git", git)) if path is None]
    if missing:
        print(f"Missing required commands on PATH: {', '.join(missing)}", file=sys.stderr)
        return 1

    if not (RUNTIME / "src" / "spine" / "SkeletonBinary.c").is_file():
        VENDOR.parent.mkdir(parents=True, exist_ok=True)
        run([
            git,
            "clone",
            "--depth",
            "1",
            "--branch",
            "3.8",
            "https://github.com/EsotericSoftware/spine-runtimes.git",
            str(VENDOR),
        ])
        run([git, "-C", str(VENDOR), "checkout", RUNTIME_COMMIT])

    BUILD.mkdir(parents=True, exist_ok=True)
    OBJECTS.mkdir(parents=True, exist_ok=True)
    # A source-specific DLL name lets a new build run while an older app still has its DLL loaded.
    bridge_hash = hashlib.sha256(BRIDGE.read_bytes()).hexdigest()[:12]
    output = BUILD / f"arkpet_spine_{bridge_hash}.dll"
    include = RUNTIME / "include"
    sources = sorted((RUNTIME / "src" / "spine").glob("*.c"))
    if not sources:
        print(f"No Spine C runtime sources found under {RUNTIME}", file=sys.stderr)
        return 1

    object_files: list[str] = []
    for source in sources:
        target = OBJECTS / f"{source.stem}.o"
        if not target.exists() or source.stat().st_mtime > target.stat().st_mtime:
            run([
                gcc,
                "-c",
                "-O2",
                "-fPIC",
                "-DSPINE_SHORT_NAMES",
                "-I",
                str(include),
                str(source),
                "-o",
                str(target),
            ])
        object_files.append(str(target))

    run([
        gxx,
        "-shared",
        "-O2",
        "-DSPINE_SHORT_NAMES",
        "-I",
        str(include),
        str(BRIDGE),
        *object_files,
        "-Wl,--export-all-symbols",
        "-static-libgcc",
        "-static-libstdc++",
        "-o",
        str(output),
    ])
    print(f"Built {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
