# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Print how many tools, and how many characters of tool text, a client sees for three kinds of key.

    python3 mcp/count_tools.py

Uses dummy keys made on the spot; nothing is printed that looks like a key. Standard library only.
"""
import json
import os
import secrets
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import server  # noqa: E402

REFUSED = "refused at start (not supported)"


def _description_chars(node):
    if isinstance(node, dict):
        return sum(len(v) if k == "description" and isinstance(v, str) else _description_chars(v)
                   for k, v in node.items())
    if isinstance(node, list):
        return sum(_description_chars(v) for v in node)
    return 0


def _row(label, tools):
    serialized = len(json.dumps(tools, ensure_ascii=False, separators=(",", ":")))
    return f"| {label} | {len(tools)} | {_description_chars(tools)} | {serialized} |"


def main():
    saved = dict(os.environ)
    rows = []
    try:
        for name in ("SCRIPTORIUM_AGENTS", "SCRIPTORIUM_WRITERS", "MEMORY_ENTRY_WRITERS"):
            os.environ.pop(name, None)
        primary = secrets.token_hex(24)
        os.environ["COUNT_ONE_KEY"] = secrets.token_hex(24)
        os.environ["COUNT_TWO_KEY"] = secrets.token_hex(24)
        cases = [("one agent", {"COUNT_ONE_KEY": "first"}, "COUNT_ONE_KEY", False),
                 ("extraction only", {}, None, False),
                 ("two agents", {"COUNT_TWO_KEY": ["first", "second"]}, "COUNT_TWO_KEY", True)]
        with tempfile.TemporaryDirectory() as tmp:
            for label, writers, var, may_refuse in cases:
                try:
                    server.configure(tmp, primary, writers)
                except SystemExit as e:
                    if may_refuse and str(e) == "invalid writer name":
                        rows.append(f"| {label} | {REFUSED} | | |")
                        continue
                    print(str(e), file=sys.stderr)
                    return 1
                binding = server.Config.keys[os.environ[var]] if var else None
                tools = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, binding)["result"]["tools"]
                rows.append(_row(label, tools))
    finally:
        os.environ.clear()
        os.environ.update(saved)
    print("| Key | Tools | Description chars | Serialized chars |")
    print("| --- | --- | --- | --- |")
    print("\n".join(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
