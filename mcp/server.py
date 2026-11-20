# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Scriptorium's MCP server: your AI's journal (the pen), plus notes and people cards written from a chat export.

    SCRIPTORIUM_MCP_KEY=<a long random key> python3 mcp/server.py --root ~/scriptorium [--port 8795]

It listens on 127.0.0.1 only, refuses requests whose Host or Origin is not local (against DNS rebinding), and refuses
to start without a key; every request must carry `Authorization: Bearer <key>`. Every path a tool is given must sit
inside --root (checked after following links). Over MCP a caller picks a model NAME and the public prompt, never a
command or a prompt file. One write runs at a time. Standard library only.

SCRIPTORIUM_AGENTS (or its 1.3.0 name, SCRIPTORIUM_WRITERS) maps key variable names to an agent or a list of agents;
--port 0 picks a free port.

Operator setting (environment, never from a request): SCRIPTORIUM_MODEL_CMD replaces the model command for every
write, for running another provider or a local model.
"""
import argparse
import copy
import hmac
import importlib
import json
import os
import re
import subprocess
import sys
import threading
import urllib.parse
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
# The module keeps the writers in engine/writer; the public copy puts them in engine/.
MAX_LIMIT = 50
AGENTS_ENV = "SCRIPTORIUM_AGENTS"


def _plugin_version():
    """The version in plugin/.claude-plugin/plugin.json, so the server reports what is installed."""
    try:
        with open(os.path.join(HERE, "..", "plugin", ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
            return str(json.load(f)["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return "0.0.0"    # shortcut: no manifest next to the server (a copied-out file); the number is then unknown


SERVER_INFO = {"name": "scriptorium", "version": _plugin_version()}
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
MAX_PIECES_PER_CALL = 100             # model pieces one write_notes call may start (the first conversation completes)
ENGINE = next((d for d in (os.path.join(HERE, "..", "engine", "writer"), os.path.join(HERE, "..", "engine"))
               if any(os.path.exists(os.path.join(d, f)) for f in ("write_notes.py", "write_memories.py"))), None)
MAX_EXPORT = 200 * 1024 * 1024        # bytes; a bigger export is refused before any model is called
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
MAX_BODY = 1_000_000
_one_write = threading.Lock()

_NOTES_SCHEMA = {"type": "object", "properties": {
    "export": {"type": "string", "description": "Path of the conversations.json, inside the server's folder."},
    "out": {"type": "string", "description": "Folder for the notes files, inside the server's folder."},
    "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT,
              "description": f"How many conversations this call writes at most (default 10, max {MAX_LIMIT})."},
    "model": {"type": "string", "description": "Model id, e.g. claude-sonnet-5-5 (pin one for repeatable runs)."},
    "timezone": {"type": "string", "description": "Your time zone (IANA name, e.g. Asia/Jakarta), so days and "
                                                  "'yesterday' follow your clock; default the export's own (UTC)."},
}, "required": ["export", "out"]}

TOOLS = [
    {"name": "write_notes",
     "description": ("Write notes from a chat export (Claude-format conversations.json): one notes file per "
                     "conversation, each note with its exact words, source lines, real date, salience, valence "
                     "and emotions, checked against the lines before it is kept. Notes are not your AI's journal; "
                     "this tool never writes there. Finished conversations are skipped, so call again to continue "
                     "a long export."),
     "inputSchema": copy.deepcopy(_NOTES_SCHEMA)},
    {"name": "write_memories",
     "description": ("Old name of write_notes, kept so existing callers keep working. Same arguments, same files."),
     "inputSchema": copy.deepcopy(_NOTES_SCHEMA)},
    {"name": "write_people",
     "description": ("Join the notes of one user's many conversations into one card per recurring person "
                     "(at least 2 conversations and 3 notes), each fact with its date and source lines."),
     "inputSchema": {"type": "object", "properties": {
         "export": {"type": "string", "description": "Path of the same conversations.json, inside the server's folder."},
         "memories": {"type": "string", "description": "The folder write_notes wrote, inside the server's folder "
                                                       "(this argument keeps its old name)."},
         "out": {"type": "string", "description": "Folder for the person cards, inside the server's folder."},
         "model": {"type": "string", "description": "Model id, e.g. claude-sonnet-5-5."},
         "timezone": {"type": "string", "description": "The same time zone the notes were written with."},
     }, "required": ["export", "memories", "out"]}},
]


_TEXT = {"type": "string"}
_POSITIVE = {"type": "integer", "minimum": 1}
_BOOL = {"type": "boolean"}

_PEN_ARGUMENT_TYPES = {"string": str, "boolean": bool, "integer": int}
PEN_TOOLS = [
    {"name": name, "description": description,
     "inputSchema": {"type": "object", "properties": properties,
                     "required": required, "additionalProperties": False}}
    for name, description, properties, required in [
        ("pen_collections", "List your collections and counts of active entries.", {}, []),
        ("pen_write", "Write an entry. On first write, set by_day to group by creation day.",
         {"collection": _TEXT, "body": _TEXT, "title": _TEXT, "pinned": _BOOL, "by_day": _BOOL},
         ["collection", "body"]),
        ("pen_edit", "Edit an entry using its current version as based_on.",
         {"id": _TEXT, "based_on": _POSITIVE, "body": _TEXT, "title": _TEXT, "pinned": _BOOL},
         ["id", "based_on"]),
        ("pen_read", "Read the latest or a specific version of an entry.",
         {"id": _TEXT, "version": _POSITIVE}, ["id"]),
        ("pen_list", "List active entries in a collection, newest first.",
         {"collection": _TEXT, "pinned_only": _BOOL, "day": _TEXT, "limit": _POSITIVE}, ["collection"]),
        ("pen_history", "Read all versions of an entry, oldest first.", {"id": _TEXT}, ["id"]),
        ("pen_delete", "Move an entry and its history aside for recovery.", {"id": _TEXT}, ["id"]),
    ]
]
_PEN_SCHEMAS = {tool["name"]: tool["inputSchema"] for tool in PEN_TOOLS}


def _with_agent(tool, agents):
    """A copy of one pen tool whose schema also requires `agent`, one of the agents on the key."""
    tool = copy.deepcopy(tool)
    schema = tool["inputSchema"]
    schema["properties"] = dict(schema["properties"], agent={
        "type": "string", "enum": list(agents), "description": "Which agent you speak as."})
    schema["required"] = list(schema["required"]) + ["agent"]
    return tool


def _shared_pen_tools(agents):
    return [_with_agent(t, agents) for t in PEN_TOOLS]


class Config:
    root = None
    key = ""
    keys = {}
    pen = None
    pen_tools = PEN_TOOLS
    key_refs = set()
    shared_tools = {}     # agent tuple -> the pen tools a key shared by those agents lists


def inside_root(path, must_exist=None):
    """The real path when it sits inside the root (links followed), else raise ValueError."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("a path is required")
    try:
        candidate = (Path(Config.root) / os.path.expanduser(path)).resolve(strict=False)
    except (OSError, RuntimeError) as e:
        raise ValueError("cannot resolve extraction path") from e
    p = str(candidate)
    if os.path.commonpath([p, Config.root]) != Config.root:
        raise ValueError(f"{path!r} is outside the server's folder")
    pen_root = Path(Config.root) / "pen"
    for ancestor in (candidate, *candidate.parents):
        try:
            in_pen = ancestor.samefile(pen_root)
        except (FileNotFoundError, NotADirectoryError):
            in_pen = False
        except OSError as e:
            raise ValueError("cannot verify extraction path") from e
        # Reserve the top-level pen name consistently, including before the first write.
        reserved = ancestor.parent == pen_root.parent and ancestor.name.casefold() == "pen"
        if in_pen or reserved:
            raise ValueError("extraction paths must stay outside the pen folder")
    if must_exist == "file" and not os.path.isfile(p):
        raise ValueError(f"{path!r} is not a file")
    if must_exist == "file" and os.path.getsize(p) > MAX_EXPORT:
        raise ValueError(f"{path!r} is larger than {MAX_EXPORT // (1024 * 1024)} MB")
    if must_exist == "dir" and not os.path.isdir(p):
        raise ValueError(f"{path!r} is not a folder")
    return p


def _env(name):
    """An operator setting: SCRIPTORIUM_<name>, or the older MEMORY_ENTRY_<name> (the old name, still read)."""
    return os.environ.get("SCRIPTORIUM_" + name) or os.environ.get("MEMORY_ENTRY_" + name)


def model_args(args):
    cmd = _env("MODEL_CMD")
    if cmd:
        return ["--model-cmd", cmd]
    model = args.get("model")
    if model is None:
        return []
    if not isinstance(model, str) or not MODEL_RE.match(model):
        raise ValueError("model must be a model id such as claude-sonnet-5-5")
    return ["--model", model]


def tz_args(args):
    """--timezone from the request (an IANA name, checked) or the operator's SCRIPTORIUM_TZ; none = as exported."""
    tz = args.get("timezone") or _env("TZ")
    if not tz:
        return []
    from zoneinfo import ZoneInfo
    try:
        if not isinstance(tz, str) or tz.startswith("-"):
            raise ValueError
        ZoneInfo(tz)
    except Exception:
        raise ValueError("timezone must be an IANA name such as Asia/Jakarta")
    return ["--timezone", tz]


def run_tool(name, args):
    """(text, is_error)."""
    if ENGINE is None:
        return "the writers are not installed next to this server", True
    try:
        if name in ("write_notes", "write_memories"):
            limit = args.get("limit", 10)
            if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
                raise ValueError(f"limit must be a whole number from 1 to {MAX_LIMIT}")
            cmd = [sys.executable, os.path.join(ENGINE, "write_notes.py" if os.path.exists(os.path.join(ENGINE, "write_notes.py")) else "write_memories.py"),
                   "--export", inside_root(args.get("export"), "file"), "--out", inside_root(args.get("out")),
                   "--prompt", "public", "--limit", str(limit), "--skip-existing",
                   "--max-pieces", str(MAX_PIECES_PER_CALL)] + model_args(args) + tz_args(args)
        elif name == "write_people":
            cmd = [sys.executable, os.path.join(ENGINE, "write_people.py"),
                   "--export", inside_root(args.get("export"), "file"),
                   "--memories", inside_root(args.get("memories"), "dir"),
                   "--out", inside_root(args.get("out"))] + model_args(args) + tz_args(args)
        else:
            return f"unknown tool: {name}", True
    except ValueError as e:
        return str(e), True
    if not _one_write.acquire(blocking=False):
        return "another write is running; try again when it finishes", True
    try:
        child_env = os.environ.copy()
        for ref in Config.key_refs | {"SCRIPTORIUM_MCP_KEY", "MEMORY_ENTRY_MCP_KEY", "SCRIPTORIUM_WRITERS", "MEMORY_ENTRY_WRITERS", AGENTS_ENV}:
            child_env.pop(ref, None)
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=Config.root, env=child_env)
    finally:
        _one_write.release()
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    tail = "\n".join(lines[-12:]) or (proc.stderr or "").strip()[-800:] or "(no output)"
    return tail, proc.returncode != 0     # 1 = some conversations errored or came back empty; the tail says which


def _result(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def pen_tool(name, args, agents):
    if agents is None:
        return "no writer is assigned to this key", True
    if len(agents) == 1:
        writer = agents[0]
    else:
        args = dict(args)
        if "agent" not in args:
            return "agent is required: one of " + ", ".join(agents), True
        agent = args.pop("agent")
        if not isinstance(agent, str) or agent not in agents:
            return "agent must be one of " + ", ".join(agents), True
        writer = agent
    schema = _PEN_SCHEMAS[name]
    properties = schema["properties"]
    if set(args) - properties.keys():
        return "unknown argument", True
    if set(schema["required"]) - args.keys():
        return "missing required argument", True
    for argument, value in args.items():
        prop = properties[argument]
        if type(value) is not _PEN_ARGUMENT_TYPES[prop["type"]] or (
            "minimum" in prop and value < prop["minimum"]
        ):
            return f"invalid {argument}", True
    if not _one_write.acquire(blocking=False):
        return "another write is running; try again when it finishes", True
    try:
        method = getattr(Config.pen, name[4:])
        result = method(writer, **args)
        if name == "pen_write":
            result = {"id": result}
        elif name == "pen_edit":
            result = {"version": result}
        elif name == "pen_delete":
            result = {"ok": True}
        return json.dumps(result, ensure_ascii=False), False
    except (ValueError, OSError) as e:
        return str(e), True
    finally:
        _one_write.release()


def handle(msg, writer=None):
    """One JSON-RPC message -> its response dict, or None for a notification.

    `writer` is what the key is bound to: None, one agent name, or a tuple of agent names."""
    agents = (writer,) if isinstance(writer, str) else (tuple(writer) if writer else None)
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "invalid request")
    rid, method = msg.get("id"), msg["method"]
    params = msg["params"] if "params" in msg else {}     # only an absent member defaults; null, [] and 0 are refused
    if rid is None:
        return None
    if not isinstance(params, dict):
        return _error(rid, -32602, "params must be an object")
    if method == "initialize":
        asked = params.get("protocolVersion")
        return _result(rid, {"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                             "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO})
    if method == "ping":
        return _result(rid, {})
    if method == "tools/list":
        pen = (Config.shared_tools.get(agents) or _shared_pen_tools(agents)) if agents and len(agents) > 1 else Config.pen_tools
        return _result(rid, {"tools": TOOLS + pen})
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or name not in {t["name"] for t in TOOLS + Config.pen_tools}:
            return _error(rid, -32602, f"unknown tool: {name}")
        args = params["arguments"] if "arguments" in params else {}
        if not isinstance(args, dict):
            return _error(rid, -32602, "arguments must be an object")
        text, is_error = pen_tool(name, args, agents) if name in {t["name"] for t in Config.pen_tools} else run_tool(name, args)
        return _result(rid, {"content": [{"type": "text", "text": text}], "isError": bool(is_error)})
    return _error(rid, -32601, f"method not found: {method}")


def local_request(headers):
    """A local Host is required; an Origin, when present, must be local too ("null" is refused)."""
    host = (headers.get("Host") or "").strip().lower()
    name = host[:host.index("]") + 1] if host.startswith("[") else host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    if name not in LOCAL_HOSTS:
        return False
    origin = (headers.get("Origin") or "").strip().lower()
    if origin:
        o = urllib.parse.urlparse(origin).hostname or ""
        if o not in LOCAL_HOSTS and f"[{o}]" not in LOCAL_HOSTS:
            return False
    return True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj=None):
        body = b"" if obj is None else json.dumps(obj).encode()
        self.send_response(code)
        if obj is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(405, {"error": "use POST /mcp"})

    def do_POST(self):
        if not local_request(self.headers):
            return self._send(403, {"error": "not a local request"})
        if urllib.parse.urlparse(self.path).path != "/mcp":
            return self._send(404, {"error": "not found"})
        got = self.headers.get("Authorization") or ""
        authenticated = False
        writer = None
        for key, bound_writer in Config.keys.items():
            if hmac.compare_digest(got.encode(), f"Bearer {key}".encode()):
                authenticated, writer = True, bound_writer
        if not authenticated:
            return self._send(401, {"error": "unauthorized"})
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            return self._send(415, {"error": "Content-Type must be application/json"})
        try:
            size = int(self.headers.get("Content-Length") or 0)
            if size < 0:
                return self._send(400, _error(None, -32600, "bad Content-Length"))
            if size > MAX_BODY:
                return self._send(413, {"error": "too large"})
            body = json.loads(self.rfile.read(size) or b"null")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, _error(None, -32700, "parse error"))
        if isinstance(body, list):
            if not body:
                return self._send(400, _error(None, -32600, "empty batch"))
            answers = [a for a in (handle(m, writer) for m in body) if a is not None]
            return self._send(202) if not answers else self._send(200, answers)
        answer = handle(body, writer)
        return self._send(202) if answer is None else self._send(200, answer)


def configure(root, key, writers=None, pen_ref=None, timezone=None):
    """Validate the settings; raise SystemExit with a plain reason when the server must not start."""
    if not key or len(key) < 16:
        raise SystemExit("refusing to start: set SCRIPTORIUM_MCP_KEY to a random key of at least 16 characters")
    if not root:
        raise SystemExit("refusing to start: --root is required (the one folder the tools may read and write)")
    real = os.path.realpath(os.path.expanduser(root))
    if not os.path.isdir(real):
        raise SystemExit(f"refusing to start: {root} is not a folder")
    if real == os.path.realpath(os.path.expanduser("~")) or real == os.path.sep:
        raise SystemExit("refusing to start: --root must be a dedicated folder, not your home folder or /")
    Config.root, Config.key = real, key
    Config.keys = {key: None}
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
    from engine.pen import LocalFilesPenStore, PenStore, validate_writer
    writers = {} if writers is None else writers
    if not isinstance(writers, dict): raise SystemExit("writers must be an object mapping environment key references to writers")
    Config.key_refs = set(writers)
    Config.shared_tools = {}
    writer_names = {}
    for ref, writer in writers.items():
        if not isinstance(ref, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", ref): raise SystemExit("invalid key reference")
        secret = os.environ.get(ref, "")
        if len(secret) < 16: raise SystemExit("writer key must be set and at least 16 characters")
        names = [writer] if isinstance(writer, str) else writer
        if not isinstance(names, list) or not names:
            raise SystemExit("each key maps to an agent name or a non-empty list of agent names")
        for name in names:
            try: validate_writer(name)
            except ValueError as e: raise SystemExit(str(e))
        if len(set(names)) != len(names): raise SystemExit("an agent is listed twice on one key")
        for name in names:
            # the folded form is only the collision key; the name keeps its own spelling
            normalized = unicodedata.normalize("NFD", name).casefold()
            if normalized in writer_names and writer_names[normalized] != name: raise SystemExit("writer names collide on case-insensitive filesystems")
            writer_names[normalized] = name
        grant = tuple(names)
        current = Config.keys.get(secret)
        if current is None: Config.keys[secret] = grant
        elif set(current) != set(grant): raise SystemExit("one key cannot map to different agents")
    Config.shared_tools = {v: _shared_pen_tools(v) for v in Config.keys.values() if v is not None and len(v) > 1}
    if pen_ref:
        mod, sep, attr = pen_ref.partition(":")
        if not mod or not sep or not attr: raise SystemExit("pen store must be module:attr")
        try:
            store = getattr(importlib.import_module(mod), attr)
            methods = [tool["name"][4:] for tool in PEN_TOOLS]
            if isinstance(store, type) or (callable(store) and not any(hasattr(store, n) for n in methods)):
                store = store()

            for name in methods:
                method = getattr(store, name, None)
                if not callable(method) or getattr(method, "__func__", method) is getattr(PenStore, name):
                    raise ValueError(f"pen store lacks required method: {name}")
            Config.pen_tools = PEN_TOOLS
            Config.pen = store
        except (ImportError, AttributeError, ValueError) as e: raise SystemExit(str(e))
    else:
        Config.pen_tools = PEN_TOOLS
        try: Config.pen = LocalFilesPenStore(real, timezone)
        except (ValueError, KeyError) as e: raise SystemExit("invalid pen timezone: " + str(e))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True, help="the one folder the tools may read and write")
    ap.add_argument("--port", type=int, default=8795)
    a = ap.parse_args(argv)
    agents_set, writers_raw = AGENTS_ENV in os.environ, _env("WRITERS")
    writers_set = "SCRIPTORIUM_WRITERS" in os.environ or "MEMORY_ENTRY_WRITERS" in os.environ
    if agents_set and writers_set:
        raise SystemExit("set SCRIPTORIUM_AGENTS or SCRIPTORIUM_WRITERS (or MEMORY_ENTRY_WRITERS), not both")
    # a set SCRIPTORIUM_AGENTS is read as given, so an empty value is refused instead of falling back
    raw, label = (os.environ[AGENTS_ENV], AGENTS_ENV) if agents_set else ((writers_raw or "{}"), "SCRIPTORIUM_WRITERS")
    try: writers = json.loads(raw)
    except ValueError: raise SystemExit(label + " must be a JSON object")
    if not isinstance(writers, dict): raise SystemExit(label + " must be a JSON object")
    configure(a.root, (_env("MCP_KEY") or ""), writers, _env("PEN_STORE"), _env("PEN_TZ"))
    # each request on its own thread, so a refused or checked request is answered while a write runs;
    # _one_write still lets only one write run at a time
    server = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"scriptorium MCP on http://127.0.0.1:{server.server_port}/mcp (folder {Config.root})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
