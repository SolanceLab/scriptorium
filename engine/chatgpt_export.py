# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""ChatGPT's official account export: one conversation of `conversations.json` in, its chosen branch's turns out.

A ChatGPT conversation is {"id" | "conversation_id", "title", "create_time", "update_time", "current_node",
"mapping": {node id: {"id", "parent", "children", "message"}}}. The mapping is a tree (an edited message or a
regenerated answer adds a sibling); the branch the person sees is the path from `current_node` up the parents.

`conversation_turns(conv)` returns (id, title, [{"id", "role", "text", "created_at"}]) in reading order, user and
assistant turns only. Claude's export items carry `chat_messages`, ChatGPT's carry `mapping`; `is_chatgpt` tells them apart.
"""
from datetime import datetime, timezone


def is_chatgpt(conv):
    return isinstance(conv, dict) and isinstance(conv.get("mapping"), dict)


def _iso(epoch):
    if isinstance(epoch, bool) or not isinstance(epoch, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return None


def _branch(mapping, current):
    """Node ids from the root to `current`, following parent links (a loop or a missing node stops the walk)."""
    path, seen = [], set()
    while isinstance(current, str) and current in mapping and current not in seen:
        seen.add(current)
        path.append(current)
        node = mapping[current]
        current = node.get("parent") if isinstance(node, dict) else None
    return path[::-1]


def _text(content):
    """The words of a message's content; a short placeholder stands for each part that is not text."""
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if isinstance(parts, list) and parts:
        out = []
        for p in parts:
            if isinstance(p, str):
                out.append(p)
            elif isinstance(p, dict) and isinstance(p.get("text"), str):
                out.append(p["text"])
            elif isinstance(p, dict) and p.get("content_type") in ("image_asset_pointer", "audio_asset_pointer"):
                out.append("[image]" if p["content_type"].startswith("image") else "[audio]")
            elif p is not None:
                out.append("[attachment]")
        return "\n".join(s for s in out if s.strip())
    if isinstance(content.get("text"), str):      # "code" and "execution_output" carry `text`
        return content["text"]
    return ""


def conversation_turns(conv):
    mapping = conv["mapping"]
    cid = next((c.strip() for c in (conv.get("id"), conv.get("conversation_id")) if isinstance(c, str) and c.strip()), None)
    title = conv.get("title") if isinstance(conv.get("title"), str) and conv["title"].strip() else "Untitled conversation"
    turns = []
    for nid in _branch(mapping, conv.get("current_node")):
        node = mapping.get(nid)
        m = node.get("message") if isinstance(node, dict) else None
        if not isinstance(m, dict):
            continue
        role = (m.get("author") or {}).get("role") if isinstance(m.get("author"), dict) else None
        meta = m.get("metadata") if isinstance(m.get("metadata"), dict) else {}
        if role not in ("user", "assistant") or meta.get("is_visually_hidden_from_conversation"):
            continue
        if m.get("recipient") not in (None, "all"):   # assistant call to a tool (web, bio, python...): not a chat turn
            continue
        text = _text(m.get("content"))
        if text.strip():
            mid = m.get("id") if isinstance(m.get("id"), str) and m.get("id") else nid
            turns.append({"id": mid, "role": role, "text": text, "created_at": _iso(m.get("create_time"))})
    return cid, title, turns
