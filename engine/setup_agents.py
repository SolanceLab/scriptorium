# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Set up which agents write in the pen, and which key each one uses.

    python3 engine/setup_agents.py                          asks you
    python3 engine/setup_agents.py --agents a,b --shared a,b    no questions
    python3 engine/setup_agents.py --print-agents FILE      prints the JSON the server reads as SCRIPTORIUM_AGENTS

An agent is the role or the identity a model speaks as. The settings file holds names of environment variables,
never a key. Openings are saved for later; nothing uses them yet. Standard library only.
"""
import argparse
import json
import os
import sys
import tempfile
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from pen import validate_writer  # noqa: E402


class SetupError(Exception):
    pass


def _fold(name):
    return unicodedata.normalize("NFD", name).casefold()


def _check_names(names):
    for n in names:
        try:
            validate_writer(n)
        except ValueError:
            raise SetupError(f"{n!r} is not a usable agent name")
        if "," in n:
            raise SetupError(f"{n!r} cannot contain a comma")
    if len({_fold(n) for n in names}) != len(names):
        raise SetupError("agent names must differ, even by upper or lower case")


def build_settings(names, shared, openings):
    """The settings dict; raise SetupError when the choice cannot work."""
    if not names:
        raise SetupError("at least one agent is needed")
    _check_names(names)
    for n in shared:
        if n not in names:
            raise SetupError(f"{n!r} is shared but is not one of the agents")
    if len(shared) == 1:
        raise SetupError("a shared chat needs at least two agents")
    if len(set(shared)) != len(shared):
        raise SetupError("a shared agent is listed twice")
    keys = {}
    if len(names) == 1:
        keys["SCRIPTORIUM_MCP_KEY"] = [names[0]]
    else:
        if shared:
            keys["SCRIPTORIUM_MCP_KEY"] = list(shared)
        for i, n in enumerate(names, 1):
            if n not in shared:
                keys[f"SCRIPTORIUM_AGENT_KEY_{i}"] = [n]
    return {"version": 1, "agents": [{"name": n, "opening": openings.get(n) or None} for n in names], "keys": keys}


def write_settings(settings, out, force):
    """Write atomically; return 0, or 2 when OUT exists and force is off."""
    folder = os.path.dirname(os.path.abspath(out)) or "."
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".setup-agents-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
            f.flush()
            os.fsync(f.fileno())
        if force:
            os.replace(tmp, out)
        else:
            try:
                os.link(tmp, out)
            except FileExistsError:
                print(f"{out} already exists; use --force to replace it", file=sys.stderr)
                return 2
        return 0
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_keys(path):
    """The keys mapping of a settings file; raise SetupError when it is not usable."""
    try:
        with open(path, encoding="utf-8") as f:
            keys = json.load(f)["keys"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise SetupError(f"cannot read the settings file {path}: {e}")
    if not isinstance(keys, dict) or not keys or not all(
            isinstance(k, str) and isinstance(v, list) and v and all(isinstance(a, str) for a in v)
            for k, v in keys.items()):
        raise SetupError("keys must map each variable name to a non-empty list of agent names")
    return keys


def _ask_interactive(input):
    try:
        count = int(input("How many AIs will write here? ").strip())
    except ValueError:
        raise SetupError("the number of AIs must be a whole number")
    if count < 1:
        raise SetupError("the number of AIs must be at least 1")
    if count == 1:
        names = [input("Its name: ").strip()]
        return names, [], {}
    names = [n.strip() for n in input("Their names, separated by commas: ").split(",") if n.strip()]
    if len(names) != count:
        raise SetupError(f"you said {count} AIs but gave {len(names)} names")
    shared = [n.strip() for n in input(
        "Which of them talk in the same chat? Names separated by commas, or leave empty: ").split(",") if n.strip()]
    openings = {}
    for n in shared:
        if n not in names:
            raise SetupError(f"{n!r} is shared but is not one of the agents")
        text = input(f"How does {n}'s turn start in the shared chat (for example \"Mira:\"), or which account id "
                     "is theirs? Leave empty to skip: ").strip()
        if text:
            openings[n] = text
    return names, shared, openings


def _key_lines(settings):
    lines = ["Which key each AI uses:"]
    keys = settings["keys"]
    if "SCRIPTORIUM_MCP_KEY" not in keys:
        lines.append("SCRIPTORIUM_MCP_KEY -> no agent (extraction only)")
    for var, agents in keys.items():
        lines.append(f"{var} -> {', '.join(agents)}")
        if len(agents) > 1:
            lines.append("Anyone holding this key can read, write, edit and delete entries for every agent on it. "
                         "Each AI gives its own name as agent.")
    return lines


def main(argv=None, input=input):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="scriptorium-agents.json")
    ap.add_argument("--agents", help="agent names, separated by commas (no questions are asked)")
    ap.add_argument("--shared", help="the agents that talk in the same chat, separated by commas")
    ap.add_argument("--opening", action="append", default=[], metavar="NAME=TEXT")
    ap.add_argument("--force", action="store_true", help="replace OUT if it exists")
    ap.add_argument("--print-agents", metavar="FILE", help="print the JSON the server reads as SCRIPTORIUM_AGENTS")
    a = ap.parse_args(argv)
    try:
        if a.print_agents:
            print(json.dumps(load_keys(a.print_agents), ensure_ascii=False, separators=(",", ":")))
            return 0
        if a.opening and a.agents is None:
            raise SetupError("--opening needs --agents (the questions ask for openings themselves)")
        openings = {}
        for item in a.opening:
            name, sep, text = item.partition("=")
            if not sep:
                raise SetupError("--opening must look like NAME=TEXT")
            openings[name] = text
        if a.agents is not None:
            names = [n.strip() for n in a.agents.split(",") if n.strip()]
            shared = [n.strip() for n in (a.shared or "").split(",") if n.strip()]
            unknown = [n for n in openings if n not in names]
            if unknown:
                raise SetupError("--opening names an agent not in --agents: " + ", ".join(unknown))
        else:
            names, shared, openings = _ask_interactive(input)
        settings = build_settings(names, shared, openings)
    except SetupError as e:
        print(str(e), file=sys.stderr)
        return 2
    code = write_settings(settings, a.out, a.force)
    if code:
        return code
    print(f"Wrote {a.out}.")
    print("\n".join(_key_lines(settings)))
    print("To start the server with these settings, follow docs/agents.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
