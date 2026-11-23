# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The capture file: one saved chat from ChatGPT, Claude or Grok, as the browser extension writes it.

It lives at `<root>/capture-<site>-<id>.json`, beside the chat export `<root>/conversations.json`. `<id>` is the
site's conversation id, percent-encoded so any id gives one safe filename and the filename gives the id back.

    {"schema_version": 1, "site": "chatgpt" | "claude" | "grok", "conversation_id": "...", "title": "...",
     "exported_at": "ISO time", "complete": true, "branch": ["message id", ...],
     "messages": [{"id": "optional", "role": "user" | "assistant", "text": "...", "created_at": "optional"}],
     "raw": {the site's own conversation object, kept for later, never read by the importer}}

`read_capture` returns the checked file or raises CaptureError, whose message names the file. It never crashes on
a bad file.
"""
import json
import os
from urllib.parse import quote, unquote

ROLES = ("user", "assistant")
SCHEMA_VERSION = 1
SITES = ("chatgpt", "claude", "grok")
PREFIX = "capture-"


class CaptureError(ValueError):
    pass


def encode_id(conversation_id):
    """A conversation id as filename text; decode_id gives the id back exactly."""
    return quote(str(conversation_id), safe="")


def decode_id(text):
    return unquote(text)


def capture_name(site, conversation_id):
    return f"{PREFIX}{site}-{encode_id(conversation_id)}.json"


def parse_name(filename):
    """(site, conversation id) from a capture filename, or None when it is not one."""
    base = os.path.basename(filename)
    if not (base.startswith(PREFIX) and base.endswith(".json")):
        return None
    site, dash, enc = base[len(PREFIX):-len(".json")].partition("-")
    if not dash or site not in SITES or not enc:
        return None
    return site, decode_id(enc)


def read_capture(path):
    """The capture file at `path`, checked. Raises CaptureError naming the file."""
    name = os.path.basename(path)

    def bad(why):
        return CaptureError(f"{name}: {why}")

    parsed = parse_name(name)
    if parsed is None:
        raise bad("not a capture file name (capture-<chatgpt|claude|grok>-<id>.json)")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise bad(f"cannot be read ({type(e).__name__})") from None
    if not isinstance(data, dict):
        raise bad("is not a JSON object")
    if type(data.get("schema_version")) is not int or data["schema_version"] != SCHEMA_VERSION:
        raise bad(f"schema_version {data.get('schema_version')!r} is not supported (this reads {SCHEMA_VERSION})")
    if data.get("site") not in SITES:
        raise bad(f"site {data.get('site')!r} is not one of {', '.join(SITES)}")
    if data["site"] != parsed[0]:
        raise bad(f"site {data['site']!r} does not match the site in the file name ({parsed[0]})")
    cid = data.get("conversation_id")
    if not isinstance(cid, str) or not cid.strip():
        raise bad("conversation_id is missing")
    if cid != parsed[1]:
        raise bad("conversation_id does not match the id in the file name")
    if data.get("complete") is not True:
        raise bad("is not complete (the save was cut short); save the chat again")
    msgs = data.get("messages")
    if not isinstance(msgs, list):
        raise bad("messages is not a list")
    clean = []
    for i, m in enumerate(msgs):
        if not isinstance(m, dict) or m.get("role") not in ROLES or not isinstance(m.get("text"), str):
            raise bad(f"message {i + 1} needs a role (user or assistant) and text")
        for key in ("id", "created_at"):
            if m.get(key) is not None and not isinstance(m[key], str):
                raise bad(f"message {i + 1}: {key} must be text")
        clean.append({"id": m.get("id") or None, "role": m["role"], "text": m["text"],
                      "created_at": m.get("created_at") or None})
    title = data.get("title")
    return {"site": data["site"], "conversation_id": cid, "title": title if isinstance(title, str) and title.strip()
            else "Untitled conversation", "exported_at": data.get("exported_at"), "branch": data.get("branch"),
            "messages": clean}
