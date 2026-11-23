# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Grok's official account export: one conversation of `prod-grok-backend.json` in, its chosen branch's turns out.

The file is {"conversations": [{"conversation": {"id", "title", "leaf_response_id", "temporary", ...},
"responses": [{"response": {"_id", "message", "sender", "create_time", "parent_response_id", ...}}]}], ...}.
The responses form a tree (a retry adds a sibling); the branch the person saw is the path from `leaf_response_id`
up the parents. Only this one file is ever opened: the account and billing files beside it are never read.

`conversation_turns(item)` returns (id, title, [{"id", "role", "text", "created_at"}]) in reading order;
sender "human" is the user, any other sender is the assistant.
"""
from datetime import datetime, timezone
import os

BACKEND = "prod-grok-backend.json"


def find_backend(root):
    """Paths (relative to root) of every prod-grok-backend.json under root, at any depth. Opens nothing."""
    found = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not (d == "notes" and dirpath == root))
        if BACKEND in files:
            found.append(os.path.relpath(os.path.join(dirpath, BACKEND), root))
    return sorted(found)


def is_grok(item):
    return isinstance(item, dict) and isinstance(item.get("conversation"), dict) and isinstance(item.get("responses"), list)


def _iso_epoch(s):
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).timestamp()


def _epoch(value):
    """Epoch seconds from {"$date": {"$numberLong": ms}}, {"$date": ms | iso}, an ISO string or a number; else None."""
    if isinstance(value, dict) and "$date" in value:
        value = value["$date"]
        if isinstance(value, dict):
            value = value.get("$numberLong")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value / 1000
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value / 1000 if value > 1e11 else value
    if isinstance(value, str) and value.strip():
        s = value.strip()
        try:
            return (int(s) / 1000 if abs(int(s)) > 1e11 else int(s)) if s.lstrip("-").isdigit() else _iso_epoch(s)
        except (ValueError, OverflowError):
            return None
    return None


def _iso(value):
    t = _epoch(value)
    if t is None:
        return None
    try:
        return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return None


def _branch(by_id, leaf):
    """Responses from the root to `leaf` (a loop or a missing parent stops the walk)."""
    path, seen = [], set()
    while isinstance(leaf, str) and leaf in by_id and leaf not in seen:
        seen.add(leaf)
        path.append(by_id[leaf])
        leaf = by_id[leaf].get("parent_response_id")
    return path[::-1]


def conversation_turns(item):
    conv = item["conversation"]
    cid = conv.get("id").strip() if isinstance(conv.get("id"), str) and conv["id"].strip() else None
    title = conv.get("title") if isinstance(conv.get("title"), str) and conv["title"].strip() else "Untitled conversation"
    by_id, order = {}, []
    for r in item["responses"]:
        resp = r.get("response") if isinstance(r, dict) else None
        if isinstance(resp, dict) and isinstance(resp.get("_id"), str) and resp["_id"]:
            by_id[resp["_id"]] = resp
            order.append(resp)
    leaf = conv.get("leaf_response_id")
    if (not isinstance(leaf, str) or leaf not in by_id) and order:      # no usable leaf: the latest-created message's chain (a tie goes to the later one)
        leaf = max(enumerate(order), key=lambda p: (_epoch(p[1].get("create_time")) or 0, p[0]))[1]["_id"]
    turns = []
    for resp in _branch(by_id, leaf):
        text = resp.get("message")
        if isinstance(text, str) and text.strip():
            role = "user" if resp.get("sender") == "human" else "assistant"
            turns.append({"id": resp["_id"], "role": role, "text": text, "created_at": _iso(resp.get("create_time"))})
    return cid, title, turns
