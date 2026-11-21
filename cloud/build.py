# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Build the Worker folder: python3 cloud/build.py  ->  cloud/build/

Copies the one source of the code (cloud/entry.py, mcp/server.py, engine/pen.py, engine/cloud_pen.py) into the folder
wrangler deploys. Nothing is edited on the way."""
import os
import shutil
import sys
from pathlib import Path

FILES = {"cloud/entry.py": "entry.py", "mcp/server.py": "server.py",
         "engine/pen.py": "engine/pen.py", "engine/cloud_pen.py": "engine/cloud_pen.py"}
MARK = ".scriptorium-build"
ROOT = Path(__file__).resolve().parents[1]


def build(out):
    out = Path(os.path.abspath(out))    # not resolved: a link must be seen as a link before anything follows it
    if os.path.lexists(out):
        # only ever clear a folder this script made (it carries the mark) or an empty one; anything else is someone's files
        if out.is_symlink() or not out.is_dir():
            raise SystemExit(f"refusing to replace {out}: it is a link or not a folder; pick a new or empty folder")
        real = out.resolve()
        if (any(real.iterdir()) and not (real / MARK).is_file()) or real == ROOT or real in ROOT.parents:
            raise SystemExit(f"refusing to replace {out}: it is not a folder this build made; pick a new or empty folder")
        shutil.rmtree(out)
    for source, target in FILES.items():
        (out / target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source, out / target)
    (out / "engine" / "__init__.py").write_text("")
    (out / MARK).write_text("made by cloud/build.py; safe to replace\n")
    return sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file() and p.name != MARK)


if __name__ == "__main__":
    for name in build(sys.argv[1] if len(sys.argv) > 1 else ROOT / "cloud" / "build"):
        print(name)
