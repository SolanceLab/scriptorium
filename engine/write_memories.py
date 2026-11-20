# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Old name of write_notes.py. Importing it or running it gives write_notes itself, so older callers keep working."""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import write_notes  # noqa: E402

if __name__ == "__main__":
    sys.exit(write_notes.main())
sys.modules[__name__] = write_notes
