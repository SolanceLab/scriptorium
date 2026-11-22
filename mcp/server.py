# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Scriptorium's MCP server: your AI's journal (journal_* tools), plus notes and people cards written from a chat export.

    SCRIPTORIUM_MCP_KEY=<a long random key> python3 mcp/server.py --root ~/scriptorium [--port 8795]

It listens on 127.0.0.1 only, refuses requests whose Host or Origin is not local (against DNS rebinding), and refuses
to start without a key; every request must carry `Authorization: Bearer <key>`. Every path a tool is given must sit
inside --root (checked after following links). Over MCP a caller picks a model NAME and the public prompt, never a
command or a prompt file. One write runs at a time. Standard library only.

SCRIPTORIUM_AGENTS (or its 1.3.0 name, SCRIPTORIUM_WRITERS) maps key variable names to an agent or a list of agents;
--port 0 picks a free port.

Operator settings (environment, never from a request): SCRIPTORIUM_MODEL_CMD replaces the model command for every
write, for running another provider or a local model. SCRIPTORIUM_FIELDS declares the fields a journal's entries
may carry; SCRIPTORIUM_HISTORY=on offers journal_history (older versions, marked as replaced).
"""
import argparse
import copy
import hmac
import importlib
import inspect
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
FIELDS_ENV = "SCRIPTORIUM_FIELDS"
MAX_LIMIT = 50
AGENTS_ENV = "SCRIPTORIUM_AGENTS"
HISTORY_ENV = "SCRIPTORIUM_HISTORY"
READS_ENV = "SCRIPTORIUM_READS"
SURFACES_ENV = "SCRIPTORIUM_SURFACES"
SILO_ENV = "SCRIPTORIUM_SILO"


def pen_settings(env_get, agents, references):
    from engine.pen import field_spec, validate_writer

    def settings(name):
        raw = env_get(name)
        try:
            value = {} if raw is None else json.loads(raw)
        except ValueError:
            value = None
        if not isinstance(value, dict):
            raise ValueError(name + " must be a JSON object")
        return value

    silo, reads, surfaces, fields = (settings(name) for name in (SILO_ENV, READS_ENV, SURFACES_ENV, FIELDS_ENV))

    def per_journal(name, setting, check):
        """Walk {agent or *: {journal or *: value}}: known agents, valid journal names that do not collide."""
        for agent, collections in setting.items():
            if agent != "*" and agent not in agents:
                raise ValueError(name + ": unknown agent")
            if not isinstance(collections, dict):
                raise ValueError(name + ": expected collection settings")
            spellings = set()
            for collection, state in collections.items():
                if collection != "*":
                    validate_writer(collection).encode("utf-8")
                    spelling = unicodedata.normalize("NFD", collection).casefold()
                    if spelling in spellings:
                        raise ValueError(name + ": collection names collide")
                    spellings.add(spelling)
                collections[collection] = check(state)

    def silo_state(state):
        if state not in ("pool", "silo"):
            raise ValueError(SILO_ENV + ": expected pool or silo")
        return state

    def declared(state):
        if not isinstance(state, dict):
            raise ValueError(FIELDS_ENV + ": expected a journal's fields as an object")
        result = {}
        for field, spec in state.items():
            if not isinstance(field, str) or not field:
                raise ValueError(FIELDS_ENV + ": a field needs a name")
            field.encode("utf-8")
            try:
                result[field] = field_spec(spec)
            except ValueError:
                raise ValueError(FIELDS_ENV + ": a field is a type (text, list, number, boolean, object) "
                                 "or {\"type\": ..., \"required\": true}") from None
        return result

    per_journal(SILO_ENV, silo, silo_state)
    per_journal(FIELDS_ENV, fields, declared)
    history = env_get(HISTORY_ENV)
    if history not in (None, "on", "off"):
        raise ValueError(HISTORY_ENV + " must be on or off")
    for agent, state in reads.items():
        if agent not in agents or state not in ("own", "pool"):
            raise ValueError(READS_ENV + ": expected a configured agent and own or pool")
    for ref, surface in surfaces.items():
        if ref not in references or not isinstance(surface, str) or not surface.strip():
            raise ValueError(SURFACES_ENV + ": expected a known key reference and a surface name")
        surface.encode("utf-8")
    by_secret = {}
    for ref, secret in references.items():
        surface = surfaces.get(ref)
        if by_secret.setdefault(secret, surface) != surface:
            raise ValueError(SURFACES_ENV + ": one key cannot have different surfaces")
    return (frozenset(agents), silo, reads), by_secret, fields, history == "on"


def pooled(silo, owner, collection, default):
    named = silo.get(owner, {})
    everyone = silo.get("*", {})
    return named.get(collection, everyone.get(collection, named.get("*", everyone.get("*", default)))) == "pool"


def declared_fields(fields, owner, journal):
    """The fields declared for one agent's journal: a named agent beats *, a named journal beats * (as for the silo)."""
    named = fields.get(owner, {})
    everyone = fields.get("*", {})
    return named.get(journal, everyone.get(journal, named.get("*", everyone.get("*", {}))))



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
_OF = {"type": "string", "description": "Notebook owner. Defaults to the acting agent."}
_SURFACE = {"type": "string", "description": "Surface of the current version."}
_BOOL = {"type": "boolean"}

_OBJECT = {"type": "object"}
# What a journal is, said once in every description that creates or finds one.
_HISTORY_HIDDEN = " Older versions of an entry are kept by the store and are not offered."
_JOURNAL = ("A journal is what you write by your own hand, as your own record; name one for any purpose "
            "(a journal, reflections, lessons, a summary). ")

_PEN_ARGUMENT_TYPES = {"string": str, "boolean": bool, "integer": int, "object": dict}
# tool name -> (store method, a read?); the listed names. Every old pen_* name is an unlisted alias of one of these.
PEN_ALIASES = {"pen_collections": "journals", "pen_write": "journal_write", "pen_edit": "journal_edit",
               "pen_read": "journal_read", "pen_list": "journal_list", "pen_history": "journal_history",
               "pen_delete": "journal_delete"}
JOURNAL_METHODS = {"journals": ("collections", True), "journal_write": ("write", False), "journal_edit": ("edit", False),
                   "journal_read": ("read", True), "journal_list": ("list", True), "journal_history": ("history", True),
                   "journal_delete": ("delete", False)}
STORE_METHODS = tuple(method for method, _ in JOURNAL_METHODS.values())    # what a PenStore must provide


def _is_async_callable(fn):
    return inspect.iscoroutinefunction(fn) or inspect.iscoroutinefunction(getattr(fn, "__call__", None))


def _no_awaitable(value):
    if inspect.isawaitable(value):
        if inspect.iscoroutine(value):
            value.close()
        raise TypeError("async pen store requires an async server")
    return value


async def _maybe_await(value):
    return await value if inspect.isawaitable(value) else value


def check_store(store, *, min_version=None, fields=None, history=False, allow_async=False):
    """Validate that a PenStore implements the required methods, contract version, and history mode."""
    from engine.pen import PEN_CONTRACT_VERSION, PenStore
    if min_version is None:
        min_version = PEN_CONTRACT_VERSION
    keeps_history = getattr(store, "keeps_history", True)
    if type(keeps_history) is not bool:
        raise ValueError("pen store keeps_history must be a boolean")
    if not keeps_history and history:
        raise ValueError(f"{HISTORY_ENV}=on cannot be used with a store that does not keep history")
    required = STORE_METHODS if keeps_history else tuple(m for m in STORE_METHODS if m != "history")
    has_async = False
    for name in required:
        method = getattr(store, name, None)
        if not callable(method) or getattr(method, "__func__", method) is getattr(PenStore, name):
            raise ValueError(f"pen store lacks required method: {name}")
        if _is_async_callable(method):
            if not allow_async:
                raise ValueError(f"pen store method {name} cannot be async in this server")
            has_async = True
    mutate = getattr(store, "mutate", None)
    if has_async and callable(mutate) and not _is_async_callable(mutate):
        raise ValueError("pen store with async methods must declare async mutate")
    version = getattr(store, "contract_version", None)
    if type(version) is not int or version < min_version or version > PEN_CONTRACT_VERSION:
        raise ValueError(f"pen store must declare contract_version = {PEN_CONTRACT_VERSION}")
    if version < PEN_CONTRACT_VERSION and fields and any(fields.values()):
        raise ValueError(f"{FIELDS_ENV} needs a pen store that declares contract_version = {PEN_CONTRACT_VERSION}; "
                         f"this one declares {version}")
    return store


def _store_keeps_history(store):
    pen = Config.pen if store is None else store
    return getattr(pen, "keeps_history", True) is not False


def _effective_history(store, history):
    wanted = Config.history if history is None else history
    return bool(wanted) and _store_keeps_history(store)


def _journal_tools(history, keeps_history=True):
    """The listed journal tools. With history off there is no journal_history and journal_read has no version."""
    history = bool(history) and bool(keeps_history)
    read = {"id": _TEXT, "of": _OF}
    if history:
        read["version"] = _POSITIVE
    if history:
        read_desc = "Read the current version of a journal entry, or an older one with version."
    elif keeps_history:
        read_desc = "Read the current version of a journal entry." + _HISTORY_HIDDEN
    else:
        read_desc = "Read a journal entry. This store keeps only the current version."
    delete_desc = ("Move a journal entry and its history aside for recovery."
                   if keeps_history else "Delete a journal entry from the store.")
    rows = [
        ("journals", "List your journals (each with the fields it declares) and the counts of active entries. "
                     "Use of for another agent's pooled journals. " + _JOURNAL, {"of": _OF}, []),
        ("journal_write", "Write a journal entry. " + _JOURNAL + "On first write to a journal, set by_day to group by "
                          "creation day. A journal may declare its own fields: check `journals`, then pass them in `fields`.",
         {"collection": dict(_TEXT, description="The journal's name."), "body": _TEXT, "title": _TEXT, "pinned": _BOOL,
          "by_day": _BOOL, "fields": dict(_OBJECT, description="Values for the fields this journal declares.")},
         ["collection", "body"]),
        ("journal_edit", "Edit a journal entry using its current version as based_on. `fields` merges into the entry's "
                         "fields; a null clears an optional one.",
         {"id": _TEXT, "based_on": _POSITIVE, "body": _TEXT, "title": _TEXT, "pinned": _BOOL,
          "fields": dict(_OBJECT, description="Fields to set or change; null clears an optional field.")},
         ["id", "based_on"]),
        ("journal_read", read_desc, read, ["id"]),
        ("journal_list", "List active journal entries in one journal, newest first.",
         {"collection": dict(_TEXT, description="The journal's name."), "pinned_only": _BOOL, "day": _TEXT,
          "limit": _POSITIVE, "of": _OF, "surface": _SURFACE}, ["collection"]),
        ("journal_history", "Read all versions of a journal entry, oldest first. Each older version is marked "
                            "replaced_on and current: false.", {"id": _TEXT, "of": _OF}, ["id"]),
        ("journal_delete", delete_desc, {"id": _TEXT}, ["id"]),
    ]
    return [{"name": name, "description": description,
             "inputSchema": {"type": "object", "properties": properties, "required": required,
                             "additionalProperties": False}}
            for name, description, properties, required in rows if history or name != "journal_history"]


PEN_TOOLS = _journal_tools(False)       # the default listing; SCRIPTORIUM_HISTORY=on adds journal_history


def _with_agent(tool, agents):
    """A copy of one pen tool whose schema also requires `agent`, one of the agents on the key."""
    tool = copy.deepcopy(tool)
    schema = tool["inputSchema"]
    schema["properties"] = dict(schema["properties"], agent={
        "type": "string", "enum": list(agents), "description": "Which agent you speak as."})
    schema["required"] = list(schema["required"]) + ["agent"]
    return tool


def _shared_pen_tools(agents, history=False, keeps_history=True):
    return [_with_agent(t, agents) for t in _journal_tools(history, keeps_history=keeps_history)]


class Config:
    root = None
    key = ""
    keys = {}
    pen = None
    key_refs = set()
    surfaces = {}
    policy = (frozenset(), {}, {})
    fields = {}           # SCRIPTORIUM_FIELDS, checked: {agent or *: {journal or *: {field: {type, required}}}}
    history = False       # SCRIPTORIUM_HISTORY=on
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


def _older(record, newer):
    """A copy of an older version marked, in the answer only, with when it was replaced."""
    return dict(record, replaced_on=newer["edited"], current=False)


def _prepare_pen_call(name, args, agents, *, surface=None, policy=None, store=None, fields=None, history=None):
    """Validate and resolve a journal tool call -> (error_text, None) or (None, prepared)."""
    name = PEN_ALIASES.get(name, name)
    fields = Config.fields if fields is None else fields
    keeps_history = _store_keeps_history(store)
    history = _effective_history(store, history)
    if agents is None:
        return "no writer is assigned to this key", None
    if len(agents) == 1:
        writer = agents[0]
    else:
        args = dict(args)
        if "agent" not in args:
            return "agent is required: one of " + ", ".join(agents), None
        agent = args.pop("agent")
        if not isinstance(agent, str) or agent not in agents:
            return "agent must be one of " + ", ".join(agents), None
        writer = agent
    if name == "journal_history" and not history:
        return "history is not offered", None
    schema = {t["name"]: t["inputSchema"] for t in _journal_tools(history, keeps_history=keeps_history)}[name]
    properties = schema["properties"]
    if set(args) - properties.keys():
        return "unknown argument", None
    if set(schema["required"]) - args.keys():
        return "missing required argument", None
    for argument, value in args.items():
        prop = properties[argument]
        if type(value) is not _PEN_ARGUMENT_TYPES[prop["type"]] or (
            "minimum" in prop and value < prop["minimum"]
        ):
            return f"invalid {argument}", None
    args = dict(args)
    owner = writer
    method_name, is_read = JOURNAL_METHODS[name]
    if is_read:
        owner = args.pop("of", writer)
        if owner != writer:
            configured, silo, reads = policy or Config.policy
            if owner not in configured:
                return "unknown agent", None
            if reads.get(writer) == "own":
                return "this agent can only read its own notebooks", None
            args["visible"] = lambda collection, default: pooled(silo, owner, collection, default)
    elif name in ("journal_write", "journal_edit"):
        args["surface"] = surface
    return None, (name, args, writer, owner, method_name, is_read, fields)


def _format_pen_result(name, result, fields, owner, writer):
    if name == "journal_write":
        result = {"id": result}
    elif name == "journal_edit":
        result = {"version": result}
    elif name == "journal_delete":
        result = {"ok": True}
    elif name == "journals":
        result = _with_declared_fields(result, fields, owner, owner == writer)
    elif name == "journal_history":
        result = [_older(r, result[i + 1]) if i + 1 < len(result) else r for i, r in enumerate(result)]
    return json.dumps(result, ensure_ascii=False)


def pen_tool(name, args, agents, *, surface=None, policy=None, store=None, fields=None, history=None):
    """One journal tool call (a listed name or an old pen_* alias) -> (text, is_error)."""
    err, prepared = _prepare_pen_call(name, args, agents, surface=surface, policy=policy,
                                      store=store, fields=fields, history=history)
    if err is not None:
        return err, True
    name, args, writer, owner, method_name, is_read, fields = prepared
    if not _one_write.acquire(blocking=False):
        return "another write is running; try again when it finishes", True
    try:
        pen = Config.pen if store is None else store
        if "fields" in args or name == "journal_write":
            from engine.pen import check_fields
            journal = (args.get("collection") if name == "journal_write"
                       else _no_awaitable(pen.read(owner, args["id"]))["collection"])
            declared = declared_fields(fields, owner, journal)
            if "fields" in args or declared:    # an omitted `fields` is still checked against required ones
                check_fields(declared, args.get("fields", {}), name == "journal_write")
            if not args.get("fields"):
                args.pop("fields", None)    # stores take `fields` only when there is something to store
        method = getattr(pen, method_name)
        result = _no_awaitable(method(owner, **args))
        if name == "journal_read" and args.get("version"):
            try:
                newer = _no_awaitable(method(owner, args["id"], args["version"] + 1,
                                             **({"visible": args["visible"]} if "visible" in args else {})))
                result = _older(result, newer)
            except ValueError as e:
                if str(e) != 'version not found':
                    raise    # a damaged newer version is an error, never passed off as 'current'
                # no newer version: this one is current
        return _format_pen_result(name, result, fields, owner, writer), False
    except OSError as e:
        if store is not None and not is_read:
            # Cloud writes share a transaction with the retry record. Let it roll back.
            raise
        return str(e), True
    except ValueError as e:
        return str(e), True
    finally:
        _one_write.release()


async def pen_tool_async(name, args, agents, *, surface=None, policy=None, store=None, fields=None, history=None):
    """Async journal tool call for stores whose methods may be async (e.g. fetch() on Cloudflare Workers)."""
    err, prepared = _prepare_pen_call(name, args, agents, surface=surface, policy=policy,
                                      store=store, fields=fields, history=history)
    if err is not None:
        return err, True
    name, args, writer, owner, method_name, is_read, fields = prepared
    try:
        pen = Config.pen if store is None else store
        if "fields" in args or name == "journal_write":
            from engine.pen import check_fields
            journal = (args.get("collection") if name == "journal_write"
                       else (await _maybe_await(pen.read(owner, args["id"])))["collection"])
            declared = declared_fields(fields, owner, journal)
            if "fields" in args or declared:    # an omitted `fields` is still checked against required ones
                check_fields(declared, args.get("fields", {}), name == "journal_write")
            if not args.get("fields"):
                args.pop("fields", None)    # stores take `fields` only when there is something to store
        method = getattr(pen, method_name)
        result = await _maybe_await(method(owner, **args))
        if name == "journal_read" and args.get("version"):
            try:
                newer = await _maybe_await(method(owner, args["id"], args["version"] + 1,
                                                  **({"visible": args["visible"]} if "visible" in args else {})))
                result = _older(result, newer)
            except ValueError as e:
                if str(e) != 'version not found':
                    raise    # a damaged newer version is an error, never passed off as 'current'
                # no newer version: this one is current
        return _format_pen_result(name, result, fields, owner, writer), False
    except OSError as e:
        if store is not None and not is_read:
            raise
        return str(e), True
    except ValueError as e:
        return str(e), True


def _with_declared_fields(journals, fields, owner, own):
    """`journals` answer: each journal with the fields it declares; an own journal not yet written to is listed too."""
    result = []
    for entry in journals:
        declared = declared_fields(fields, owner, entry["collection"])
        result.append(dict(entry, fields=declared) if declared else entry)
    if own:
        known = {entry["collection"] for entry in journals}
        named = {**fields.get("*", {}), **fields.get(owner, {})}
        for journal in sorted(named):
            declared = declared_fields(fields, owner, journal)
            if journal != "*" and journal not in known and declared:
                result.append({"collection": journal, "count": 0, "fields": declared})
    return result


def _listed_pen_tools(agents, history, keeps_history=True):
    if agents and len(agents) > 1:
        cached = Config.shared_tools.get((agents, history)) if keeps_history == _store_keeps_history(None) else None
        return cached or _shared_pen_tools(agents, history, keeps_history=keeps_history)
    return _journal_tools(history, keeps_history=keeps_history)


def _callable_names(history):
    """Every name tools/call accepts: the chat tools, the journal tools (journal_history only when history is on)
    and the old pen_* names as unlisted aliases of them."""
    journal = {t["name"] for t in _journal_tools(history)}
    return {t["name"] for t in TOOLS} | journal | {old for old, new in PEN_ALIASES.items() if new in journal}


def _dispatch_meta(msg, agents, *, store=None, history=None):
    """Handle JSON-RPC framing and non-tool methods, or return ('call', (rid, name, args, effective_history))."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return "done", _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "invalid request")
    rid, method = msg.get("id"), msg["method"]
    params = msg["params"] if "params" in msg else {}     # only an absent member defaults; null, [] and 0 are refused
    if rid is None:
        return "done", None
    if not isinstance(params, dict):
        return "done", _error(rid, -32602, "params must be an object")
    if method == "initialize":
        asked = params.get("protocolVersion")
        return "done", _result(rid, {"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                                     "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO})
    if method == "ping":
        return "done", _result(rid, {})
    effective_history = _effective_history(store, history)
    keeps_history = _store_keeps_history(store)
    if method == "tools/list":
        return "done", _result(rid, {"tools": TOOLS + _listed_pen_tools(agents, effective_history, keeps_history=keeps_history)})
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or name not in _callable_names(effective_history):
            return "done", _error(rid, -32602, f"unknown tool: {name}")
        args = params["arguments"] if "arguments" in params else {}
        if not isinstance(args, dict):
            return "done", _error(rid, -32602, "arguments must be an object")
        return "call", (rid, name, args, effective_history)
    return "done", _error(rid, -32601, f"method not found: {method}")


def handle(msg, writer=None, *, surface=None, policy=None, store=None, fields=None, history=None):
    """One JSON-RPC message -> its response dict, or None for a notification.

    `writer` is what the key is bound to: None, one agent name, or a tuple of agent names."""
    agents = (writer,) if isinstance(writer, str) else (tuple(writer) if writer else None)
    kind, payload = _dispatch_meta(msg, agents, store=store, history=history)
    if kind == "done":
        return payload
    rid, name, args, effective_history = payload
    text, is_error = (pen_tool(name, args, agents, surface=surface, policy=policy, store=store,
                               fields=fields, history=effective_history)
                      if name in JOURNAL_METHODS or name in PEN_ALIASES else run_tool(name, args))
    return _result(rid, {"content": [{"type": "text", "text": text}], "isError": bool(is_error)})


async def handle_async(msg, writer=None, *, surface=None, policy=None, store=None, fields=None, history=None):
    """Async counterpart to handle() for servers whose pen store methods may be async."""
    agents = (writer,) if isinstance(writer, str) else (tuple(writer) if writer else None)
    kind, payload = _dispatch_meta(msg, agents, store=store, history=history)
    if kind == "done":
        return payload
    rid, name, args, effective_history = payload
    text, is_error = (await pen_tool_async(name, args, agents, surface=surface, policy=policy, store=store,
                                           fields=fields, history=effective_history)
                      if name in JOURNAL_METHODS or name in PEN_ALIASES else run_tool(name, args))
    return _result(rid, {"content": [{"type": "text", "text": text}], "isError": bool(is_error)})


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
        writer = surface = None
        for key, bound_writer in Config.keys.items():
            if hmac.compare_digest(got.encode(), f"Bearer {key}".encode()):
                authenticated, writer = True, bound_writer
                surface = Config.surfaces.get(key)
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
            answers = [a for a in (handle(m, writer, surface=surface) for m in body) if a is not None]
            return self._send(202) if not answers else self._send(200, answers)
        answer = handle(body, writer, surface=surface)
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
    from engine.pen import PEN_CONTRACT_VERSION, LocalFilesPenStore, PenStore, validate_writer
    writers = {} if writers is None else writers
    if not isinstance(writers, dict): raise SystemExit("writers must be an object mapping environment key references to writers")
    Config.key_refs = set(writers)
    Config.shared_tools = {}
    writer_names = {}
    references = {}
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
        references[ref] = secret
        grant = tuple(names)
        current = Config.keys.get(secret)
        if current is None: Config.keys[secret] = grant
        elif set(current) != set(grant): raise SystemExit("one key cannot map to different agents")
    if "SCRIPTORIUM_MCP_KEY" not in references and key not in references.values():
        references["SCRIPTORIUM_MCP_KEY"] = key
    try:
        Config.policy, Config.surfaces, Config.fields, Config.history = pen_settings(os.environ.get, writer_names.values(), references)
    except ValueError as e:
        raise SystemExit(str(e)) from e
    if pen_ref:
        mod, sep, attr = pen_ref.partition(":")
        if not mod or not sep or not attr: raise SystemExit("pen store must be module:attr")
        try:
            store = getattr(importlib.import_module(mod), attr)
            if isinstance(store, type) or (callable(store) and not any(hasattr(store, n) for n in STORE_METHODS)):
                store = store()
            Config.pen = check_store(store, min_version=2, fields=Config.fields, history=Config.history)
        except (ImportError, AttributeError, ValueError) as e: raise SystemExit(str(e))
    else:
        try: Config.pen = LocalFilesPenStore(real, timezone)
        except (ValueError, KeyError) as e: raise SystemExit("invalid pen timezone: " + str(e))
    keeps = _store_keeps_history(Config.pen)
    Config.shared_tools = {(v, Config.history): _shared_pen_tools(v, Config.history, keeps_history=keeps)
                           for v in Config.keys.values() if v is not None and len(v) > 1}


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
