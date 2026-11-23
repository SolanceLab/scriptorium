# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Import saved chats: capture files and account exports (Claude's or ChatGPT's conversations.json, Grok's prod-grok-backend.json) in, notes for only the new messages out.

    python3 import_captures.py --root FOLDER [--out DIR] [--model M | --model-cmd "<command>"] [--prompt public]

One conversation is keyed (site, conversation id). A Claude capture and a `conversations.json` record with the same
id are one conversation; a ChatGPT or Grok id equal to it is another. For each conversation the importer works out
the messages not yet given notes and hands the notes writer ONE batch of them.

What the writer accepts (engine/write_notes.py `write_conversation`): a Claude-export conversation dict, the same
one `claude_lines` reads: {"uuid", "name", "created_at", "chat_messages": [{"uuid", "sender": "human"|"assistant",
"text", "created_at"}]}, and it returns {"conversation_id", "title", "session_date", "memories", "rejected",
"calls"[, "error"]}. Notes cite message uuids, so a message without an id gets a stable made-up one.
The writer's file for a conversation is REPLACED when it is called again, so this importer keeps the earlier
notes: each batch's result is merged into the notes file (memories, rejected and calls are appended).

State lives under `<root>/.scriptorium-import/`, one file per conversation:
  processed  the messages already given notes (their id and their role+text)
  pending    the batch being written, saved BEFORE the writer runs
A batch has an id. The notes file lists the batch ids it holds, so a crash after the notes were written re-runs
nothing (the state is just committed), and a crash before re-runs the batch once. Nothing is skipped or doubled.
"""
import argparse
import hashlib
import json
import os
import re
import shlex
import sys
import time

import capture_file as cf
import chatgpt_export as gpt
import grok_export as grok
import write_notes as wn

try:
    import fcntl
except ImportError:      # shortcut: no file lock on Windows; run one import at a time there
    fcntl = None

STATE_DIR = ".scriptorium-import"
CHATGPT_EXPORT = "chatgpt-conversations.json"
EXPORT = "conversations.json"
ADOPTED = "notes exist for this chat but this importer has no record of them; move them away or remove the notes file to import it from the start"


def _norm(text):
    return re.sub(r"\s+", " ", text or "").strip()


def content_key(role, text):
    return role + "\0" + _norm(text)


def _synthetic_id(role, text, occurrence):
    return f"seq-{hashlib.sha256(content_key(role, text).encode('utf-8')).hexdigest()[:10]}-{occurrence}"


def _prepare(messages):
    """Messages with empty text dropped, each given `ck` (role+text key with its occurrence number, so two identical
    turns stay two) and an `id` (the site's own, or a stable made-up one)."""
    seen, out = {}, []
    for m in messages:
        if not _norm(m["text"]):
            continue
        key = content_key(m["role"], m["text"])
        seen[key] = seen.get(key, 0) + 1
        out.append({"id": m.get("id") or _synthetic_id(m["role"], m["text"], seen[key]), "has_id": bool(m.get("id")),
                    "role": m["role"], "text": m["text"], "created_at": m.get("created_at"),
                    "ck": f"{key}\0{seen[key]}"})
    return out


def _from_claude_export(conv):
    cid, title, lines = wn.claude_lines(conv)
    return cid, title, [{"id": lid, "role": sender, "text": text, "created_at": at} for lid, sender, text, at in lines]


def _export_files(root):
    """Paths (relative to root) of the account-export files: conversations.json and chatgpt-conversations.json in
    the root, and conversations.json one folder down (where an unzipped ChatGPT export lands)."""
    names = [n for n in (EXPORT, CHATGPT_EXPORT) if os.path.isfile(os.path.join(root, n))]
    for d in sorted(os.listdir(root)):
        if d.startswith(".") or d == "notes" or not os.path.isdir(os.path.join(root, d)):
            continue
        if os.path.isfile(os.path.join(root, d, EXPORT)):
            names.append(os.path.join(d, EXPORT))
    return names


def _grok_files(root):
    """Paths of prod-grok-backend.json under root (an unzipped Grok export, any depth); no other file of it is opened."""
    return grok.find_backend(root)


def find_sources(root):
    """({(site, id): [source, ...]}, [problem text]) from the capture files and conversations.json in root."""
    found, problems = {}, []
    for name in sorted(os.listdir(root)):
        if cf.parse_name(name) is None:
            continue
        try:
            cap = cf.read_capture(os.path.join(root, name))
        except cf.CaptureError as e:
            problems.append(str(e))
            continue
        msgs = cap["messages"]
        if isinstance(cap["branch"], list) and cap["branch"]:
            keep = set(map(str, cap["branch"]))
            msgs = [m for m in msgs if not m["id"] or m["id"] in keep]
        found.setdefault((cap["site"], cap["conversation_id"]), []).append(
            {"from": name, "title": cap["title"], "messages": _prepare(msgs)})
    for rel in _export_files(root):
        try:
            with open(os.path.join(root, rel), encoding="utf-8") as f:
                convs = json.load(f)
            if not isinstance(convs, list):
                raise ValueError("not a list")
        except (OSError, ValueError) as e:
            problems.append(f"{rel}: cannot be read ({e})")
            continue
        for conv in convs:
            if not isinstance(conv, dict):
                continue
            if gpt.is_chatgpt(conv):                      # ChatGPT's items carry `mapping`, Claude's `chat_messages`
                site, (cid, title, msgs) = "chatgpt", gpt.conversation_turns(conv)
            elif isinstance(conv.get("chat_messages"), list):
                site, (cid, title, msgs) = "claude", _from_claude_export(conv)
            else:
                continue
            if cid:
                found.setdefault((site, cid), []).append(
                    {"from": rel, "title": title, "messages": _prepare(msgs), "export": True})
    for rel in _grok_files(root):
        try:
            with open(os.path.join(root, rel), encoding="utf-8") as f:
                items = json.load(f)["conversations"]
            if not isinstance(items, list):
                raise ValueError("conversations is not a list")
        except (OSError, ValueError, KeyError, TypeError) as e:
            problems.append(f"{rel}: cannot be read ({e})")
            continue
        for item in items:
            if not grok.is_grok(item) or item["conversation"].get("temporary") is True:
                continue                                  # temporary chats were not meant to be kept
            cid, title, msgs = grok.conversation_turns(item)
            if cid:
                found.setdefault(("grok", cid), []).append(
                    {"from": rel, "title": title, "messages": _prepare(msgs), "export": True})
    return found, problems


def pick_source(sources):
    """The one source to read for a chat. shortcut: the fuller of two copies wins (a tie goes to the capture file);
    messages only the smaller copy holds show up next run as a conflict, never as silent loss."""
    return max(sources, key=lambda s: (len(s["messages"]), not s.get("export")))


def notes_name(site, cid):
    # one injective name per (site, id), the same encoding the state file uses, so no two chats share a notes file
    return f"{site}-{cf.encode_id(cid)}.json"


def _state_path(root, site, cid):
    return os.path.join(root, STATE_DIR, f"{site}-{cf.encode_id(cid)}.state.json")


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


class LockedError(RuntimeError):
    pass


class _Lock:
    """One import at a time per conversation."""
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.f = open(self.path, "a+")
        if fcntl:
            try:
                fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self.f.close()
                raise LockedError("another import is running for this chat") from None
        return self

    def __exit__(self, *exc):
        self.f.close()


def _is_done(state, m):
    return any(r["id"] == m["id"] or r["ck"] == m["ck"] for r in state["processed"])


def _record(m):
    return {"id": m["id"], "ck": m["ck"]}


def _conv_dict(cid, title, batch):
    first = next((m["created_at"] for m in batch if m.get("created_at")), None)
    chat = []
    for m in batch:
        d = {"uuid": m["id"], "sender": "human" if m["role"] == "user" else "assistant", "text": m["text"]}
        if m.get("created_at"):
            d["created_at"] = m["created_at"]
        chat.append(d)
    conv = {"uuid": cid, "name": title, "chat_messages": chat}
    if first:
        conv["created_at"] = first
    return conv


def _batches(notes):
    b = (notes or {}).get("batches")
    return b if isinstance(b, list) else []


def merge_notes(existing, result, batch_id):
    """The notes file with this batch's notes added; earlier notes stay. A batch already in the file is not added twice."""
    if existing is None:
        return dict(result, batches=[batch_id])
    if batch_id in _batches(existing):
        return existing
    merged = dict(existing)
    for key in ("memories", "rejected", "calls"):
        before, new = existing.get(key), result.get(key)
        merged[key] = (before if isinstance(before, list) else []) + (new if isinstance(new, list) else [])
    merged["title"] = result.get("title") or existing.get("title")
    merged["writer"] = result.get("writer", existing.get("writer"))
    merged["batches"] = _batches(existing) + [batch_id]
    merged.pop("error", None)
    return merged


def _state_ok(state):
    """True when a state file has the shape this importer writes."""
    if not isinstance(state, dict) or not isinstance(state.get("processed"), list):
        return False
    def row_ok(r):
        return isinstance(r, dict) and isinstance(r.get("id"), str) and isinstance(r.get("ck"), str)
    if not all(row_ok(r) for r in state["processed"]):
        return False
    p = state.get("pending")
    return p is None or (isinstance(p, dict) and isinstance(p.get("id"), str) and isinstance(p.get("messages"), list)
                         and all(row_ok(r) and isinstance(r.get("role"), str) and isinstance(r.get("text"), str)
                                 for r in p["messages"]))


def _missing(state, source):
    """How many already-processed messages the source no longer holds."""
    msgs = source["messages"]
    return sum(1 for r in state["processed"] if not any(m["id"] == r["id"] or m["ck"] == r["ck"] for m in msgs))


def import_conversation(root, out, site, cid, source, write):
    """Give notes to the new messages of one chat. Returns (status, detail): 'nothing new', 'written' or 'conflict'
    / 'error' with the reason. `write(conv)` is the notes writer (write_conversation with its settings)."""
    spath = _state_path(root, site, cid)
    npath = os.path.join(out, notes_name(site, cid))
    with _Lock(spath + ".lock"):
        if os.path.exists(spath):
            state = _load(spath, None)
            if not _state_ok(state):
                return "error", f"{os.path.basename(spath)} is not a readable state file; left untouched, nothing written"
        else:
            state = {"processed": [], "pending": None}
        if os.path.exists(npath) and not isinstance(_load(npath, None), dict):
            return "error", f"{os.path.basename(npath)} exists but is not readable notes; left untouched, nothing written"
        if not state["processed"] and not state["pending"] and os.path.exists(npath):
            return "conflict", ADOPTED
        written = 0
        from_disk = bool(state["pending"])               # a saved batch is checked once, when read back from disk
        while True:
            if state["pending"]:
                batch_id, batch = state["pending"]["id"], state["pending"]["messages"]
                if from_disk:
                    from_disk = False
                    held = batch_id in _batches(_load(npath, None))
                    if not held and not _pending_current(state, source):
                        state["pending"] = None           # the saved batch no longer matches the source: recompute it
                        wn.write_atomic(spath, state)
                        continue
            else:
                msgs = source["messages"]
                missing = _missing(state, source)
                if missing:
                    return "conflict", (f"{missing} message(s) already given notes are not in {source['from']} "
                                        f"any more; nothing was changed")
                batch = [m for m in msgs if not _is_done(state, m)]
                if not batch:
                    return ("written", written) if written else ("nothing new", 0)
                batch_id = hashlib.sha256(json.dumps([m["id"] + m["ck"] for m in batch]).encode()).hexdigest()[:16]
                state["pending"] = {"id": batch_id, "messages": [_record(m) | {"role": m["role"], "text": m["text"],
                                    "created_at": m["created_at"]} for m in batch]}
                wn.write_atomic(spath, state)             # the batch is saved BEFORE the writer runs
            existing = _load(npath, None)
            if not (existing and batch_id in _batches(existing)):
                missing = _missing(state, source)         # also for a batch read back from disk: conflict before any write
                if missing:
                    return "conflict", (f"{missing} message(s) already given notes are not in {source['from']} "
                                        f"any more; nothing was changed")
                result = write(_conv_dict(cid, source["title"], batch))
                if result.get("error"):
                    return "error", result["error"]
                os.makedirs(out, exist_ok=True)
                wn.write_atomic(npath, merge_notes(existing, result, batch_id))
            # the notes are on disk: only now does the batch count as processed
            state["processed"] += [_record(m) for m in batch]
            state["pending"] = None
            wn.write_atomic(spath, state)
            written += len(batch)


def _pending_current(state, source):
    """True when the saved pending batch still matches the source's messages (same ids and text)."""
    have = {(m["id"], m["ck"]) for m in source["messages"]}
    return all((r["id"], r["ck"]) in have for r in state["pending"]["messages"])


def import_root(root, out, write):
    """Import every chat in root. Returns {'written': n, 'nothing new': n, 'conflict': [...], 'error': [...]}."""
    if not os.path.isdir(root):
        raise SystemExit(f"{root}: not a folder")
    found, problems = find_sources(root)
    report = {"written": 0, "nothing new": 0, "conflict": [], "error": list(problems)}
    for (site, cid), sources in sorted(found.items()):
        try:
            status, detail = import_conversation(root, out, site, cid, pick_source(sources), write)
        except LockedError as e:
            status, detail = "error", str(e)
        if status in ("conflict", "error"):
            report[status].append(f"{site} {cid}: {detail}")
        else:
            report[status] += detail if status == "written" else 1
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True, help="the folder holding capture-*.json and/or conversations.json")
    ap.add_argument("--out", help="folder for the notes files (default: <root>/notes)")
    ap.add_argument("--model", default=wn.DEFAULT_MODEL, help="model id for Claude Code; pin one for a reported run")
    ap.add_argument("--model-cmd", default=os.environ.get("MEMORY_WRITER_CMD"),
                    help="a whole command of your own instead of the default (overrides --model)")
    ap.add_argument("--prompt", default=wn.DEFAULT_PROMPT, help="public | house (House copy only) | a path")
    ap.add_argument("--user-name", default=os.environ.get("SCRIPTORIUM_USER_NAME"))
    ap.add_argument("--agent-name", default=os.environ.get("SCRIPTORIUM_AGENT_NAME"))
    ap.add_argument("--platform", default=os.environ.get("SCRIPTORIUM_PLATFORM"))
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--timezone", default=os.environ.get("MEMORY_WRITER_TZ") or None)
    a = ap.parse_args(argv)
    if a.timezone:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(a.timezone)
        except Exception:
            ap.error(f"unknown time zone {a.timezone!r} (use an IANA name such as Asia/Jakarta)")
    # shortcut: no --api here; use --model-cmd, or add the same partial(call_api, ...) wiring write_notes.main has
    model_cmd = a.model_cmd or wn.MODEL_CMD_TEMPLATE.format(model=shlex.quote(a.model))
    prompt_path = wn.PROMPTS.get(a.prompt, a.prompt)
    with open(prompt_path, encoding="utf-8") as f:
        template = wn.render_instructions(f.read(), a.prompt, a.user_name, a.agent_name, a.platform)
    record = dict(wn.run_record(model_cmd, prompt_path, template), timezone=a.timezone or "as exported")

    def write(conv):
        t0 = time.time()
        res = wn.write_conversation(conv, template, model_cmd, a.timeout, tz=a.timezone,
                                    builtin_prompt=a.prompt in wn.PROMPTS)
        res["writer"] = dict(record, seconds=round(time.time() - t0, 1))
        return res

    out = a.out or os.path.join(a.root, "notes")
    report = import_root(a.root, out, write)
    for kind in ("conflict", "error"):
        for line in report[kind]:
            print(f"{kind.upper()}: {line}", file=sys.stderr)
    print(f"imported {report['written']} message(s) · unchanged chats {report['nothing new']} · "
          f"conflicts {len(report['conflict'])} · errors {len(report['error'])}")
    return 1 if (report["conflict"] or report["error"]) else 0


if __name__ == "__main__":
    sys.exit(main())
