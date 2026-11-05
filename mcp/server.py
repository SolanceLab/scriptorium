# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Scriptorium's MCP server: write memories from a chat export, and people cards from those memories.

    SCRIPTORIUM_MCP_KEY=<a long random key> python3 mcp/server.py --root ~/scriptorium [--port 8795]

It listens on 127.0.0.1 only, refuses requests whose Host or Origin is not local (against DNS rebinding), and refuses
to start without a key; every request must carry `Authorization: Bearer <key>`. Every path a tool is given must sit
inside --root (checked after following links). Over MCP a caller picks a model NAME and the public prompt, never a
command or a prompt file. One write runs at a time. Standard library only.

Operator setting (environment, never from a request): SCRIPTORIUM_MODEL_CMD replaces the model command for every
write, for running another provider or a local model.
"""
import argparse
import hmac
import json
import os
import re
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
# The module keeps the writers in engine/writer; the public copy puts them in engine/.
MAX_LIMIT = 50
SERVER_INFO = {"name": "scriptorium", "version": "1.0.0"}
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
MAX_PIECES_PER_CALL = 100             # model pieces one write_memories call may start (the first conversation completes)
ENGINE = next((d for d in (os.path.join(HERE, "..", "engine", "writer"), os.path.join(HERE, "..", "engine"))
               if os.path.exists(os.path.join(d, "write_memories.py"))), None)
MAX_EXPORT = 200 * 1024 * 1024        # bytes; a bigger export is refused before any model is called
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
MAX_BODY = 1_000_000
_one_write = threading.Lock()

TOOLS = [
    {"name": "write_memories",
     "description": ("Write memories from a chat export (Claude-format conversations.json): one memory file per "
                     "conversation, each memory with its exact words, source lines, real date, salience, valence "
                     "and emotions, checked against the lines before it is kept. Finished conversations are skipped, "
                     "so call again to continue a long export."),
     "inputSchema": {"type": "object", "properties": {
         "export": {"type": "string", "description": "Path of the conversations.json, inside the server's folder."},
         "out": {"type": "string", "description": "Folder for the memory files, inside the server's folder."},
         "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT,
                   "description": f"How many conversations this call writes at most (default 10, max {MAX_LIMIT})."},
         "model": {"type": "string", "description": "Model id, e.g. claude-sonnet-5-5 (pin one for repeatable runs)."},
         "timezone": {"type": "string", "description": "Your time zone (IANA name, e.g. Asia/Jakarta), so days and "
                                                       "'yesterday' follow your clock; default the export's own (UTC)."},
     }, "required": ["export", "out"]}},
    {"name": "write_people",
     "description": ("Join the memories of one user's many conversations into one card per recurring person "
                     "(at least 2 conversations and 3 memories), each fact with its date and source lines."),
     "inputSchema": {"type": "object", "properties": {
         "export": {"type": "string", "description": "Path of the same conversations.json, inside the server's folder."},
         "memories": {"type": "string", "description": "The folder write_memories wrote, inside the server's folder."},
         "out": {"type": "string", "description": "Folder for the person cards, inside the server's folder."},
         "model": {"type": "string", "description": "Model id, e.g. claude-sonnet-5-5."},
         "timezone": {"type": "string", "description": "The same time zone the memories were written with."},
     }, "required": ["export", "memories", "out"]}},
]


class Config:
    root = None
    key = ""


def inside_root(path, must_exist=None):
    """The real path when it sits inside the root (links followed), else raise ValueError."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("a path is required")
    p = os.path.realpath(os.path.join(Config.root, os.path.expanduser(path)))
    if os.path.commonpath([p, Config.root]) != Config.root:
        raise ValueError(f"{path!r} is outside the server's folder")
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
        if name == "write_memories":
            limit = args.get("limit", 10)
            if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
                raise ValueError(f"limit must be a whole number from 1 to {MAX_LIMIT}")
            cmd = [sys.executable, os.path.join(ENGINE, "write_memories.py"),
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
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=Config.root)
    finally:
        _one_write.release()
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    tail = "\n".join(lines[-12:]) or (proc.stderr or "").strip()[-800:] or "(no output)"
    return tail, proc.returncode != 0     # 1 = some conversations errored or came back empty; the tail says which


def _result(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle(msg):
    """One JSON-RPC message -> its response dict, or None for a notification."""
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
        return _result(rid, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or name not in {t["name"] for t in TOOLS}:
            return _error(rid, -32602, f"unknown tool: {name}")
        args = params["arguments"] if "arguments" in params else {}
        if not isinstance(args, dict):
            return _error(rid, -32602, "arguments must be an object")
        text, is_error = run_tool(name, args)
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
        if not hmac.compare_digest(got.encode(), f"Bearer {Config.key}".encode()):
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
            answers = [a for a in (handle(m) for m in body) if a is not None]
            return self._send(202) if not answers else self._send(200, answers)
        answer = handle(body)
        return self._send(202) if answer is None else self._send(200, answer)


def configure(root, key):
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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True, help="the one folder the tools may read and write")
    ap.add_argument("--port", type=int, default=8795)
    a = ap.parse_args(argv)
    configure(a.root, (_env("MCP_KEY") or ""))
    # each request on its own thread, so a refused or checked request is answered while a write runs;
    # _one_write still lets only one write run at a time
    server = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"scriptorium MCP on http://127.0.0.1:{a.port}/mcp (folder {Config.root})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
