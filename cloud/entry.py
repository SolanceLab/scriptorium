# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Scriptorium's pen on Cloudflare: a Worker that checks the key, and one Durable Object that keeps every agent's pen.

The Worker runs the unchanged server.handle (mcp/server.py) over a SqlPenStore (engine/cloud_pen.py). Only the pen tools
are offered here: the chat-reading tools run model commands and read files, which a Worker cannot do.
The logic sits in plain functions; only the two classes at the bottom need the Cloudflare runtime.
"""
import hashlib
import hmac
import json
import re
import time
import unicodedata

import server
from engine.cloud_pen import DurableObjectSql, SqlPenStore
from engine.pen import validate_writer

try:
    from workers import DurableObject, Response, WorkerEntrypoint
except ImportError:  # plain Python: the logic below is importable and testable without Cloudflare
    DurableObject = WorkerEntrypoint = object
    Response = None

KEY_ENV, AGENTS_ENV, TZ_ENV = "SCRIPTORIUM_MCP_KEY", "SCRIPTORIUM_AGENTS", "SCRIPTORIUM_PEN_TZ"
RETRY_TTL = 24 * 60 * 60
PEN_NAMES = {tool["name"] for tool in server.PEN_TOOLS}
CHAT_NAMES = {tool["name"] for tool in server.TOOLS}
MAX_REQUEST_ID = 200
WRITE_NAMES = ("pen_write", "pen_edit", "pen_delete")
PEN_OBJECT = "pen"    # the one Durable Object of this deployment; keys only admit a request and name its agents
MAX_SAVED = 1_000_000    # bytes of one answer kept for a retry; a Durable Object row holds at most 2 MB
_REQUESTS = ("CREATE TABLE IF NOT EXISTS cloud_requests (rid TEXT PRIMARY KEY, writer TEXT NOT NULL, hash TEXT NOT NULL, "
             "created_at REAL NOT NULL, response TEXT NOT NULL)")


def load_keys(env_get):
    """({key: agents tuple or None}, None) for a good setup, or ({}, plain reason) when the Worker must not serve."""
    key = env_get(KEY_ENV) or ""
    if len(key) < 16:
        return {}, f"not configured: set the secret {KEY_ENV} to a random key of at least 16 characters"
    keys = {key: None}
    raw = env_get(AGENTS_ENV)
    try:
        writers = json.loads(raw) if raw else {}
    except ValueError:
        return {}, AGENTS_ENV + " must be a JSON object"
    if not isinstance(writers, dict):
        return {}, AGENTS_ENV + " must be a JSON object"
    folded = {}
    for ref, writer in writers.items():
        if not isinstance(ref, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", ref):
            return {}, "invalid key reference"
        secret = env_get(ref) or ""
        if len(secret) < 16:
            return {}, "writer key must be set and at least 16 characters"
        names = [writer] if isinstance(writer, str) else writer
        if not isinstance(names, list) or not names:
            return {}, "each key maps to an agent name or a non-empty list of agent names"
        for name in names:
            try:
                validate_writer(name)
            except ValueError as e:
                return {}, str(e)
        if len(set(names)) != len(names):
            return {}, "an agent is listed twice on one key"
        for name in names:
            normal = unicodedata.normalize("NFD", name).casefold()
            if folded.setdefault(normal, name) != name:
                return {}, "writer names collide"
        current = keys.get(secret)
        if current is not None and set(current) != set(names):
            return {}, "one key cannot map to different agents"
        if current is None:
            keys[secret] = tuple(names)
    return keys, None


def admit(env_get, method, path, header_get):
    """(None, agents) when the request may go on, else (status, plain message)."""
    keys, problem = load_keys(env_get)
    if problem:
        return 503, problem
    if method != "POST":
        return 405, "use POST /mcp"
    if path != "/mcp":
        return 404, "not found"
    got = (header_get("authorization") or "").encode()
    agents, found = None, False
    for key, bound in keys.items():
        if hmac.compare_digest(got, f"Bearer {key}".encode()):
            agents, found = bound, True
    if not found:
        return 401, "unauthorized"
    if (header_get("content-type") or "").split(";")[0].strip().lower() != "application/json":
        return 415, "Content-Type must be application/json"
    request_id = header_get("x-request-id") or ""
    if len(request_id) > MAX_REQUEST_ID or (request_id and not request_id.isprintable()):
        return 400, "X-Request-Id must be printable text of at most 200 characters"
    return None, agents


def _error(rid, code, message):
    return server._error(rid, code, message)


def cloud_handle(msg, agents):
    """server.handle, with the chat tools hidden from tools/list and refused in tools/call."""
    method = msg.get("method") if isinstance(msg, dict) else None
    params = msg.get("params") if isinstance(msg, dict) else None
    if method == "tools/call" and isinstance(params, dict) and isinstance(params.get("name"), str) \
            and params["name"] in CHAT_NAMES and msg.get("id") is not None:
        return _error(msg["id"], -32602, f"unknown tool: {params['name']}")
    answer = server.handle(msg, agents)
    if method == "tools/list" and isinstance(answer, dict) and "result" in answer:
        answer["result"]["tools"] = [t for t in answer["result"]["tools"] if t["name"] in PEN_NAMES]
    return answer


def _failed(answer):
    return "error" in answer or bool(answer.get("result", {}).get("isError"))


def _handle(store, body, agents):
    previous = server.Config.pen
    server.Config.pen = store
    try:
        return cloud_handle(body, agents)
    finally:
        server.Config.pen = previous


def _reply(answer):
    return (202, "") if answer is None else (200, json.dumps(answer))


def _write_call(body):
    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or body.get("id") is None:
        return None
    params = body.get("params")
    if body.get("method") == "tools/call" and isinstance(params, dict) and params.get("name") in WRITE_NAMES:
        return params
    return None


def _rpc_id(body):
    rid = body.get("id") if isinstance(body, dict) else None
    return rid if isinstance(rid, (str, int, float)) and not isinstance(rid, bool) else None


def _request_rpc_id(body_text):
    try:
        return _rpc_id(json.loads(body_text))
    except ValueError:
        return None


def serve(store, body_text, agents, request_id=""):
    """Handle an admitted request, committing each retryable write and its reply together."""
    size = len(body_text.encode("utf-8"))
    if size > server.MAX_BODY:
        # echo the id when the body is only a little too large; never parse a huge one just to find it
        rid = _request_rpc_id(body_text) if size <= 2 * server.MAX_BODY else None
        return 413, json.dumps(_error(rid, -32600, "request too large"))
    try:
        body = json.loads(body_text)
    except ValueError:
        return 400, json.dumps(_error(None, -32700, "parse error"))
    if isinstance(body, list):
        return 400, json.dumps(_error(None, -32600, "batches are not supported"))
    params = _write_call(body)
    if params is None:
        return _reply(_handle(store, body, agents))

    args = params.get("arguments", {})
    named = args.get("agent") if isinstance(args, dict) else None
    writer = agents[0] if agents and len(agents) == 1 else named
    def fingerprint(actor):
        canonical = json.dumps({"name": params["name"], "arguments": args, "agent": actor},
                               sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    digest = fingerprint(writer) if request_id else None
    adapter = store.adapter

    def once():
        now = time.time()
        adapter.run(_REQUESTS)
        adapter.run("DELETE FROM cloud_requests WHERE created_at < ?", (now - RETRY_TTL,))
        if request_id:
            rid = _rpc_id(body)
            # refuse before looking anything up, so a key cannot learn what another agent saved
            if agents is None or (named is not None and named not in agents):
                return 403, json.dumps(_error(rid, -32600, "this key cannot write as that agent"))
            rows = adapter.run("SELECT writer, hash, response FROM cloud_requests WHERE rid = ?", (request_id,))
            if rows:
                saved = rows[0]
                if saved["hash"] != digest:
                    return 409, json.dumps(_error(rid, -32600, "this X-Request-Id was already used with a different request"))
                answer = json.loads(saved["response"])
                answer["id"] = rid
                return _reply(answer)
        answer = _handle(store, body, agents)
        status, text = _reply(answer)
        if request_id and answer is not None and not _failed(answer):
            saved = text if len(text.encode("utf-8")) <= MAX_SAVED else json.dumps(_error(
                None, -32000, "the first answer was too large to keep; the request did go through, read the pen to see it"))
            adapter.run("INSERT INTO cloud_requests (rid, writer, hash, created_at, response) VALUES (?, ?, ?, ?, ?)",
                        (request_id, writer, digest, now, saved))
        return status, text

    store.ensure_ready()
    return adapter.transaction(once)


class PenObject(DurableObject):
    """The pen of the whole deployment: its SQLite database lives in this one object, entries separated by agent."""
    def __init__(self, ctx, env):
        self.ctx, self.env = ctx, env
        tz = getattr(env, TZ_ENV, None)
        self.store = SqlPenStore(DurableObjectSql(ctx.storage.sql, ctx.storage), str(tz) if tz else None)

    async def call(self, body, agents_json, request_id):
        agents = tuple(json.loads(agents_json)) or None
        try:
            status, text = serve(self.store, body, agents, request_id)
        except Exception:
            status, text = 500, json.dumps(_error(_request_rpc_id(body), -32603, "the pen could not answer"))
        return json.dumps({"status": status, "text": text})


def _plain(status, message):
    return Response(json.dumps({"error": message}), status=status, headers={"content-type": "application/json"})


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        from urllib.parse import urlparse
        env_get = lambda name: (lambda v: str(v) if v is not None else None)(getattr(self.env, name, None))
        refused, found = admit(env_get, request.method, urlparse(request.url).path, request.headers.get)
        if refused == 401:
            return Response(None, status=401, headers={"www-authenticate": "Bearer"})
        if refused:
            return _plain(refused, found)
        agents = found
        body = await request.text()
        stub = self.env.PEN.getByName(PEN_OBJECT)
        out = json.loads(await stub.call(body, json.dumps(list(agents or ())),
                                    request.headers.get("x-request-id") or ""))
        if out["status"] == 202:
            return Response(None, status=202)
        return Response(out["text"], status=out["status"], headers={"content-type": "application/json"})
