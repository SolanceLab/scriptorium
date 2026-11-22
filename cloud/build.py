# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Build the Worker folder: python3 cloud/build.py [--store FILE] [out]  ->  cloud/build/

Copies the one source of the code (cloud/entry.py, mcp/server.py, engine/pen.py, engine/cloud_pen.py) into the folder
wrangler deploys, plus an optional --store module file. Nothing is edited on the way."""
import argparse
import os
import re
import shutil
from pathlib import Path

FILES = {"cloud/entry.py": "entry.py", "mcp/server.py": "server.py",
         "engine/pen.py": "engine/pen.py", "engine/cloud_pen.py": "engine/cloud_pen.py"}
MARK = ".scriptorium-build"
ROOT = Path(__file__).resolve().parents[1]
RESERVED_STORE_NAMES = {"entry.py", "server.py", "engine.py", MARK}


def _checked_store(store):
    """Verify --store points directly at one regular .py file (not a symlink or reserved name)."""
    path = Path(os.path.abspath(store))    # not resolved: a link must be seen as a link before anything follows it
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f"refusing --store {store}: must be a regular Python file, not a link or folder")
    if path.suffix != ".py" or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", path.stem):
        raise SystemExit(f"refusing --store {store}: filename must be a valid Python module name ending in .py")
    if path.name in RESERVED_STORE_NAMES:
        raise SystemExit(f"refusing --store {store}: {path.name} collides with a built-in Worker file")
    return path


def build(out, *, store=None):
    store_path = _checked_store(store) if store is not None else None
    out = Path(os.path.abspath(out))    # not resolved: a link must be seen as a link before anything follows it
    if os.path.lexists(out):
        # only ever clear a folder this script made (it carries the mark) or an empty one; anything else is someone's files
        if out.is_symlink() or not out.is_dir():
            raise SystemExit(f"refusing to replace {out}: it is a link or not a folder; pick a new or empty folder")
        real = out.resolve()
        if (any(real.iterdir()) and not (real / MARK).is_file()) or real == ROOT or real in ROOT.parents:
            raise SystemExit(f"refusing to replace {out}: it is not a folder this build made; pick a new or empty folder")
        if store_path is not None:
            real_store = store_path.resolve()
            if real_store == real or real in real_store.parents:
                raise SystemExit(f"refusing --store {store}: store file cannot sit inside the output folder {out}")
        shutil.rmtree(out)
    for source, target in FILES.items():
        (out / target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source, out / target)
    if store_path is not None:
        shutil.copyfile(store_path, out / store_path.name)
    (out / "engine" / "__init__.py").write_text("")
    (out / MARK).write_text("made by cloud/build.py; safe to replace\n")
    return sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file() and p.name != MARK)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the Cloudflare Worker folder.")
    parser.add_argument("out", nargs="?", default=str(ROOT / "cloud" / "build"),
                        help="Output folder (default: cloud/build)")
    parser.add_argument("--store", default=None,
                        help="Copy one custom store module (.py file) into the Worker bundle")
    args = parser.parse_args(argv)
    for name in build(args.out, store=args.store):
        print(name)


if __name__ == "__main__":
    main()
