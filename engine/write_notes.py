# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Notes writer: a chat export in, one notes file per conversation out.

Each conversation is one session. Its lines go to a writer model with `prompt.md`; the model's answer is checked
before anything is kept: every note must cite line ids that exist in that session, and its excerpt must appear
word for word in those lines. A note that fails is reported, never kept.

    python3 write_notes.py --export conversations.json --out DIR [--limit N] [--model claude-sonnet-5-5]
                           [--prompt public] [--api | --model-cmd "<command>"] [--skip-existing]

The model is whatever command `--model-cmd` names: it receives the full prompt on stdin and prints its answer (plain,
or Claude Code's `--output-format json` envelope, whose model, usage and cost are then recorded). Every output file
carries a `writer` block: the exact command, `claude --version`, the resolved model, usage and cost per call, the
writer's commit, and the prompt file with the SHA-256 of the rendered instructions.

This writer is one notes METHOD (notes carry a verbatim excerpt). Other methods (e.g. generic summaries) do not
pass through it: they write the same file format directly, and the search adapter treats every file alike.

Each file keeps its notes under the key "memories", the old name, so files written before still read the same.
write_memories.py is the old name of this file and still works.
"""
import argparse
from collections import Counter
from functools import partial
import hashlib
from http.client import HTTPException
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from urllib import error, request

HERE = os.path.dirname(os.path.abspath(__file__))
# The default command switches off every tool, so a hostile line in an export cannot make the model read, run or
# fetch anything; it loads no settings, MCP servers, skills or saved session, and uses the writer's own system
# prompt, so the runner's CLAUDE.md, memory and hooks do not reach the memories (checked 16 Oct: it knew no House
# name; Claude Code still passes it the account email). It is NOT a sandbox: the process inherits the environment
# and runs as the user. A --model-cmd of your own gets none of these protections unless you add them.
# {model} is filled from --model. Pass a PINNED id (claude-sonnet-5-5) for any run that is reported: an alias
# (sonnet) moves when a new model ships, and the recorded command would then not say which model wrote the memories.
API_PRICES = {"claude-sonnet-5-5": (2, 10), "claude-haiku-5-5": (0.10, 0.50), "claude-opus-5-5": (4, 20)}
DEFAULT_MODEL = "sonnet"
SYSTEM_PROMPT = "You write notes from one conversation. Output only valid JSON."
DEFAULT_API_MODEL = "claude-sonnet-5-5"
MODEL_CMD_TEMPLATE = ('claude -p --model {model} --output-format json --tools "" --setting-sources "" --strict-mcp-config '
                      f'--disable-slash-commands --no-session-persistence --system-prompt "{SYSTEM_PROMPT}"')
DEFAULT_MODEL_CMD = MODEL_CMD_TEMPLATE.format(model=DEFAULT_MODEL)
PROMPTS = {"house": os.path.join(HERE, "prompt.md"),            # the House's own (carries the House notice)
           "public": os.path.join(HERE, "prompt-public.md")}    # same method, no House notice or vocabulary
# The House prompt is private and never published; the public copy of this file runs on the public prompt.
DEFAULT_PROMPT = "house" if os.path.exists(PROMPTS["house"]) else "public"
PROMPT_FILE = PROMPTS[DEFAULT_PROMPT]
# What the model process may see of the environment. Nothing else is passed: no House keys, tokens or settings paths
# ride along. HOME and USER are what claude needs to find its own login; the rest is locale and temp space.
ENV_KEEP = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TERM")

GAP = "[...]"
TAG_CAP = 10           # emotions / context tags kept, each at most 60 characters
REQUIRED = ("topic", "excerpt", "summary", "source_line_ids", "source_session_date")
SUMMARY_CAP = 1500     # characters; a summary is one to three sentences
TOPICS = ["Family", "Work", "Education", "Health", "Creative", "Support", "Plans", "Home", "Leisure",
          "Relationships", "Milestone", "Conflict", "Logistics", "Personal"]


def claude_lines(conv):
    """(conversation id, title, [(line id, sender, text, created_at)]) for one Claude-format export conversation."""
    cid = str(conv.get("uuid") or conv.get("id") or "")
    title = conv.get("name") or conv.get("title") or "Untitled conversation"
    out = []
    for m in conv.get("chat_messages") or []:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if isinstance(content, list) and content:
            text = "\n".join(p.get("text") or "" for p in content
                             if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str))
        else:
            text = m.get("text") if isinstance(m.get("text"), str) else ""
        text = text.strip()
        sender = {"human": "user", "user": "user", "assistant": "assistant"}.get(m.get("sender"))
        if not text or not sender or not m.get("uuid"):
            continue
        out.append((str(m["uuid"]), sender, text, m.get("created_at")))
    return cid, title, out


_FRACTION = re.compile(r"(T\d{2}:\d{2}:\d{2})\.(\d+)")


def parse_iso(stamp):
    """An ISO timestamp as a datetime. Python 3.9 and 3.10 fromisoformat take only 3 or 6 digits after the seconds,
    but Postgres trims trailing zeros (.020 becomes .02), so the fraction is padded or cut to 6 digits first.
    Raises ValueError when the text is not a time."""
    import datetime as dt
    s = str(stamp or "").replace("Z", "+00:00")
    s = _FRACTION.sub(lambda m: m.group(1) + "." + (m.group(2) + "000000")[:6], s, count=1)
    return dt.datetime.fromisoformat(s)


def local_day(stamp, tz=None):
    """The calendar day of a timestamp. Without a time zone it is the day as the export writes it (Claude exports
    write UTC); with one (an IANA name such as Asia/Jakarta) the timestamp is moved to that zone first, so a message
    sent at 06:30 in Jakarta counts on that day, not the UTC day before."""
    s = str(stamp or "")
    if tz:
        import datetime as dt
        from zoneinfo import ZoneInfo
        try:
            t = parse_iso(s)
            if t.tzinfo is None:
                t = t.replace(tzinfo=dt.timezone.utc)
            return t.astimezone(ZoneInfo(tz)).date().isoformat()
        except ValueError:
            pass
    return s[:10]


def session_date(conv, lines, tz=None):
    stamp = conv.get("created_at") or (lines[0][3] if lines else None) or ""
    return local_day(stamp, tz)


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
CAL_BEFORE, CAL_AFTER = 14, 7


def _day(d):
    return f"{WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]} {d.year}"


def relative_labels(session):
    """{date: [labels]} for the calendar: yesterday/tomorrow, N days ago, last/next <weekday>. 'last Friday' is the
    most recent Friday BEFORE the session day; 'next Friday' the first one AFTER it (benchmark review 17 Oct: the
    writer counted this in its head and slipped a week)."""
    import datetime as dt
    out = {}
    for k in range(1, CAL_BEFORE + 1):
        d = session - dt.timedelta(days=k)
        out.setdefault(d, []).append("yesterday" if k == 1 else f"{k} days ago")
        if k <= 7:
            out[d].append(f"last {WEEKDAYS[d.weekday()]}")
    for k in range(1, CAL_AFTER + 1):
        d = session + dt.timedelta(days=k)
        out.setdefault(d, []).append("tomorrow" if k == 1 else f"in {k} days")
        out[d].append(f"next {WEEKDAYS[d.weekday()]}")
    return out


def calendar_block(date):
    """The calendar the writer reads relative dates off, or '' when the session date is not a plain YYYY-MM-DD."""
    import datetime as dt
    try:
        session = dt.date.fromisoformat(date)
    except (TypeError, ValueError):
        return ""
    labels = relative_labels(session)
    rows = []
    for k in range(-CAL_BEFORE, CAL_AFTER + 1):
        d = session + dt.timedelta(days=k)
        tag = "THIS SESSION" if k == 0 else ", ".join(labels.get(d, []))
        rows.append(f"{_day(d)} — {tag}")
    key = []
    for d in sorted(labels):
        for l in labels[d]:
            if l in ("yesterday", "tomorrow") or l.startswith(("last ", "next ")):
                key.append(f"{l} = {_day(d)}")
    return (f"Session weekday: {WEEKDAYS[session.weekday()]}\n"
            f"Relative-date key (use these exact dates):\n" + "\n".join(key) + "\n"
            f"Calendar (read relative dates off this list; do not compute them):\n" + "\n".join(rows) + "\n")


def build_prompt(template, title, date, lines):
    body = "\n".join(f"[{lid}] {sender}: {text}" for lid, sender, text, _ in lines)
    cal = calendar_block(date)
    return f"{template}\n\n---\n\nSession: {title}\nSession date: {date}\n{cal}\n{body}\n"


def _norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def parse_answer(raw):
    """The model's text → a list of thread dicts. Raises ValueError when there is no JSON array in it."""
    text = re.sub(r"```(?:json)?", "", raw or "").strip()
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("no JSON array in the answer")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, list):
        raise ValueError("the answer is not a JSON array")
    return data


_HEDGE = re.compile(r"\b(likely|probably|possibly|presumably|perhaps|approximately|roughly|around|maybe)\b",
                    re.IGNORECASE)
_MONTH_RE = "|".join(MONTHS + [m[:3] for m in MONTHS])
_DATE_RES = [
    re.compile(rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)? (?P<m>{_MONTH_RE})\.?,? ?(?P<y>\d{{4}})?\b"),
    re.compile(rf"\b(?P<m>{_MONTH_RE})\.? (?P<d>\d{{1,2}})(?:st|nd|rd|th)?,? ?(?P<y>\d{{4}})?\b"),
]
_REL = re.compile(r"\b(yesterday|tomorrow|(?:last|next) (?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday))\b",
                  re.IGNORECASE)


def dates_in(text, session):
    """[(date, start, end, weekday word just before it or None)] for every calendar date written in the text."""
    import datetime as dt
    found = []
    for rx in _DATE_RES:
        for m in rx.finditer(text or ""):
            mon = next(i for i, n in enumerate(MONTHS) if n.lower().startswith(m.group("m").lower()[:3])) + 1
            year = int(m.group("y")) if m.group("y") else session.year
            try:
                d = dt.date(year, mon, int(m.group("d")))
            except ValueError:
                continue
            before = (text or "")[max(0, m.start() - 12):m.start()]
            wd = next((w for w in WEEKDAYS if re.search(rf"\b{w}\b,?\s*$", before)), None)
            if not any(f[1] <= m.start() < f[2] for f in found):
                found.append((d, m.start(), m.end(), wd))
    return found


def check_dates(summary, cited_text, date, relative=True):
    """None when the summary's dates hold up against the calendar; otherwise the reason. Only what can be PROVEN
    wrong is rejected: a hedged date, a weekday that does not match its date, or a relative day from the cited lines
    ('last Friday') turned into the same weekday in a different week."""
    import datetime as dt
    try:
        session = dt.date.fromisoformat(date)
    except (TypeError, ValueError):
        return None
    found = dates_in(summary, session)
    for d, start, _end, wd in found:
        if _HEDGE.search(summary[max(0, start - 14):start]):
            return f"hedged date ({summary[max(0, start - 14):_end][:60]!r}); read it off the calendar or quote the line"
        if wd and WEEKDAYS[d.weekday()] != wd:
            return f"{wd} does not fall on {_day(d)}"
    if not relative:
        return None
    labels = relative_labels(session)
    want = {}
    for d, ls in labels.items():
        for l in ls:
            want[l.lower()] = d
    targets = {}
    for m in _REL.finditer(cited_text or ""):
        target = want.get(m.group(1).lower())
        if target:
            targets.setdefault(target, m.group(1))
    stated = {d for d, *_ in dates_in(cited_text, session)}   # a date the speaker says outright is never a slip
    for d, *_ in found:
        if d in targets or d in stated:
            continue
        for target, phrase in targets.items():
            if d.weekday() == target.weekday() and abs((d - session).days) <= CAL_BEFORE + CAL_AFTER:
                return f"'{phrase}' in the lines is {_day(target)}, but the summary says {_day(d)}"
    return None


def check_thread(t, by_id, date, relative=True):
    """Return (cleaned thread, None) or (None, reason). Never invents: only trims and normalises."""
    return _validate_thread(t, by_id, date, relative)


_QUOTE_ERROR = "excerpt not word for word in one of its lines: "


def _validate_thread(t, by_id, date, relative, added_ids=()):
    if not isinstance(t, dict):
        return None, "not an object"
    missing = [k for k in REQUIRED if not t.get(k)]
    if missing:
        return None, "missing " + ", ".join(missing)
    ids = t["source_line_ids"]
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        return None, "source_line_ids must be a non-empty list of line ids"
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        return None, "cites lines not in this session: " + ", ".join(unknown)
    cited = [_norm(by_id[i]) for i in (*ids, *added_ids)]
    parts = [_norm(p) for p in str(t["excerpt"]).split(GAP)]
    if not any(len(p.split()) >= 2 for p in parts):
        return None, "excerpt has no words of its own"
    for part in parts:
        # each piece must sit inside ONE cited line: pieces may not be stitched across lines into a new sentence
        if part and not any(part in line for line in cited):
            return None, f"{_QUOTE_ERROR}{part[:80]!r}"
    clean = dict(t)
    if clean["topic"] not in TOPICS:
        clean["topic"] = "Personal"
    clean["source_session_date"] = date
    for key, lo, hi in (("salience", 0.0, 1.0), ("valence", -1.0, 1.0)):
        try:
            clean[key] = min(hi, max(lo, float(clean.get(key, 0.5 if key == "salience" else 0.0))))
        except (TypeError, ValueError):
            return None, f"{key} is not a number"
    if len(str(clean["summary"])) > SUMMARY_CAP:
        return None, f"summary longer than {SUMMARY_CAP} characters"
    why = check_dates(str(clean["summary"]), " ".join(by_id[i] for i in ids), date, relative=relative)
    if why:
        return None, why
    for key in ("emotions", "context_tags"):
        v = clean.get(key) or []
        clean[key] = [str(x)[:60] for x in v][:TAG_CAP] if isinstance(v, list) else []
    return clean, None


def _absolute_args(args):
    """Paths in a custom command (./model.py, scripts/run.py) are resolved against where the writer was started,
    before the command moves to its empty folder."""
    out = []
    for i, a in enumerate(args):
        if i == 0:
            # the program: a written path (./model.py, bin/run) resolves where the writer started; a bare name
            # (claude) only ever comes from PATH, so a file in the current folder can never stand in for it
            if os.sep in a or a.startswith("."):
                a = os.path.abspath(a)
            else:
                found = shutil.which(a)
                a = os.path.abspath(found) if found else a
        elif a and not os.path.isabs(a) and os.path.exists(a):
            a = os.path.abspath(a)
        out.append(a)
    return out


def minimal_env(keep=ENV_KEEP):
    return {k: os.environ[k] for k in keep if k in os.environ}


def call_model(model_cmd, prompt, timeout):
    """Run the model command from a fresh empty working folder (its own relative reads find nothing), with only the
    ENV_KEEP variables. Not a sandbox: absolute paths and ../ still reach the machine; the default command's
    protection is that it has no tools."""
    args = _absolute_args(shlex.split(model_cmd))
    cwd = tempfile.mkdtemp(prefix="memory-writer-")
    try:
        proc = subprocess.run(args, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                              env=minimal_env())
    finally:
        shutil.rmtree(cwd, ignore_errors=True)
    if proc.returncode != 0:
        raise RuntimeError(f"model command exited {proc.returncode}: {proc.stderr.strip()[:300]}")
    return proc.stdout


def _redact(value, key):
    if isinstance(value, str):
        return value.replace(key, "<redacted>")
    if isinstance(value, dict):
        return {_redact(k, key): _redact(v, key) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    return value


class ApiCallError(RuntimeError):
    def __init__(self, message, record=None):
        super().__init__(message)
        self.record = record if record is not None else {"cost_incomplete": True}


def _http_error_detail(exc, key):
    status = f"HTTP {exc.code}"
    limit = 64 * 1024
    try:
        with exc:
            body = exc.read(limit + 1)
        if len(body) > limit:
            return status
        payload = json.loads(body)
    except (OSError, HTTPException, ValueError, RecursionError):
        return status
    detail = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(detail, dict):
        fields = [_redact(detail[name], key)[:300] for name in ("type", "message")
                  if isinstance(detail.get(name), str)]
        if fields:
            return f"{status}: {': '.join(fields)}"
    return status


def call_api(model, key, prompt, timeout):
    if key in prompt or key in model:
        raise RuntimeError("Anthropic API request contains the API key outside its header")
    body = {"model": model, "max_tokens": 16000, "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}]}
    req = request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode("utf-8"),
                          headers={"anthropic-version": "2026-10-21", "content-type": "application/json"},
                          method="POST")
    req.add_unredirected_header("X-api-key", key)
    start = time.monotonic()
    try:
        with request.urlopen(req, timeout=timeout) as response:
            message = json.load(response)
    except error.HTTPError as exc:
        raise ApiCallError(_redact(_http_error_detail(exc, key), key)) from None
    except HTTPException as exc:
        raise ApiCallError(_redact(f"Anthropic API request failed: {type(exc).__name__}", key)) from None
    except (OSError, ValueError) as exc:
        raise ApiCallError(_redact(f"Anthropic API request failed: {exc}", key)) from None
    if not isinstance(message, dict):
        raise ApiCallError("Anthropic API returned an invalid message")
    try:
        usage = message["usage"]
        actual_model = message["model"]
        rates = API_PRICES.get(actual_model)
        record = {"models": [actual_model], "usage": usage, "duration_ms": round((time.monotonic() - start) * 1000)}
        if rates is not None:
            record["total_cost_usd"] = (
                usage["input_tokens"] * rates[0] + usage["output_tokens"] * rates[1]) / 1_000_000
    except (KeyError, TypeError):
        raise ApiCallError("Anthropic API returned an invalid message") from None
    record = _redact(record, key)
    reason = message.get("stop_reason")
    if reason not in ("end_turn", "stop_sequence"):
        if reason == "refusal":
            detail = "Anthropic API refused the request"
        elif reason == "max_tokens":
            detail = "Anthropic API response reached the token limit"
        else:
            detail = "Anthropic API returned a non-completion stop reason"
        raise ApiCallError(detail, record)
    try:
        answer = "".join(block["text"] for block in message["content"] if block["type"] == "text")
    except (KeyError, TypeError):
        raise ApiCallError("Anthropic API returned an invalid message", record) from None
    return _redact(answer, key), record


def unwrap(stdout):
    """(answer text, call record). Claude Code's --output-format json envelope gives the answer in `result` and says
    which model ran and what it cost; any other output is the answer itself, with an empty record."""
    try:
        env = json.loads(stdout)
    except (ValueError, TypeError):
        return stdout, {}
    if not (isinstance(env, dict) and env.get("type") == "result" and "result" in env):
        return stdout, {}
    if env.get("is_error"):
        raise RuntimeError(f"model reported an error: {str(env.get('result'))[:300]}")
    rec = {"models": sorted((env.get("modelUsage") or {}).keys()) or None,
           "usage": env.get("usage"), "total_cost_usd": env.get("total_cost_usd"),
           "duration_ms": env.get("duration_ms"), "session_id": env.get("session_id")}
    return str(env.get("result") or ""), {k: v for k, v in rec.items() if v is not None}


def _repair_quote(thread, messages, id_counts):
    """Find unique missing citations, with all fragments in transcript order."""
    cited = set(thread["source_line_ids"])
    if any(id_counts[lid] != 1 for lid in cited):
        return []
    added = {}
    position, end = 0, 0
    for part in filter(None, (_norm(p) for p in str(thread["excerpt"]).split(GAP))):
        candidates = [i for i, (lid, line) in enumerate(messages) if lid in cited and part in line]
        pattern = re.escape(part)
        if not candidates:
            if len(part.split()) < 6:    # words as spoken: "don't" and a timestamp count once
                return []
            if re.match(r"\w", part):
                pattern = r"(?<!\w)" + pattern
            if re.search(r"\w$", part):
                pattern += r"(?!\w)"
            candidates = [i for i, (_, line) in enumerate(messages) if re.search(pattern, line)]
            if len(candidates) != 1:
                return []
            i = candidates[0]
            lid = messages[i][0]
            if id_counts[lid] != 1:
                return []
            added[lid] = None

        # Use the earliest available occurrence so later fragments have the most room to match.
        for i in candidates:
            if i >= position:
                match = re.compile(pattern).search(messages[i][1], end if i == position else 0)
                if match:
                    position, end = i, match.end()
                    break
        else:
            return []
    return list(added)


def _check_all(threads, lines, date):
    by_id = {lid: text for lid, _, text, _ in lines}
    messages = [(lid, _norm(text)) for lid, _, text, _ in lines]
    id_counts = Counter(lid for lid, _ in messages)
    order = {lid: i for i, (lid, _) in enumerate(messages)}
    kept, rejected = [], []
    for t in threads:
        if isinstance(t, dict) and "added_line_ids" in t:
            t = {key: value for key, value in t.items() if key != "added_line_ids"}
        clean, why = check_thread(t, by_id, date)
        if why and why.startswith(_QUOTE_ERROR):
            added = _repair_quote(t, messages, id_counts)
            if added:
                clean, _ = _validate_thread(t, by_id, date, relative=True, added_ids=added)
                if clean:
                    clean["added_line_ids"] = added
                    clean["source_line_ids"] = sorted(t["source_line_ids"] + added, key=order.__getitem__)
                # A failed repair keeps the original rejection and its retry behavior.
        (kept.append(clean) if clean else rejected.append({"reason": why, "thread": t}))
    return kept, rejected


def _is_date_reason(why):
    return bool(why) and (why.startswith("hedged date") or " in the lines is " in why or "does not fall on" in why)


MAX_PIECE_CHARS = 400_000     # about 100,000 tokens of conversation per model call
MAX_PIECES = 200              # a conversation needing more pieces is refused before any model call


def split_pieces(lines, date, max_chars=MAX_PIECE_CHARS, tz=None):
    """[(date, lines)]: a conversation cut at every new day (a line's own created_at; a line without one stays with
    the day before it), and again inside a day when the lines run past max_chars. Each piece is one model call with
    its own date and calendar, so a chat that runs over a week, or is too long for one call, is written correctly
    (measured on one real history of 774 chats: 23 were over 150,000 tokens, and 344 ran over more than one day).
    A one-day conversation under the size is one piece, exactly as before."""
    pieces, cur, cur_day, size = [], [], None, 0
    for ln in lines:
        stamp = local_day(ln[3], tz)
        day = stamp if re.match(r"^\d{4}-\d{2}-\d{2}$", stamp) else (cur_day or date)
        n = len(ln[2]) + len(ln[0]) + 16
        if cur and (day != cur_day or size + n > max_chars):
            pieces.append((cur_day, cur))
            cur, size = [], 0
        cur.append(ln)
        cur_day, size = day, size + n
    if cur:
        pieces.append((cur_day, cur))
    return pieces or [(date, [])]


def write_conversation(conv, template, model_cmd, timeout, retries=1, max_chars=MAX_PIECE_CHARS, tz=None,
                       api_call=None):
    def fetch(prompt):
        if api_call is not None:
            return api_call(prompt, timeout)
        return unwrap(call_model(model_cmd, prompt, timeout))

    cid, title, lines = claude_lines(conv)
    date = session_date(conv, lines, tz)
    pieces = split_pieces(lines, date, max_chars, tz)
    if len(pieces) > MAX_PIECES:
        return {"conversation_id": cid, "title": title, "session_date": date, "memories": [], "rejected": [],
                "calls": [], "error": f"{len(pieces)} pieces (more than {MAX_PIECES}); nothing was sent to the model"}
    if len(pieces) == 1:
        date = pieces[0][0]       # the day its messages carry (the same as the conversation's, unless they differ)
        kept, rejected, calls, err = _write_piece(title, date, lines, template, fetch, retries)
        out = {"conversation_id": cid, "title": title, "session_date": date, "memories": kept,
               "rejected": rejected, "calls": calls}
        if err is not None:
            out["error"] = err
        return out
    kept, rejected, calls, errors = [], [], [], []
    for i, (day, part) in enumerate(pieces, 1):
        k, r, c, err = _write_piece(f"{title} (part {i} of {len(pieces)})", day, part, template, fetch, retries)
        kept += k
        rejected += r
        calls += [dict(x, piece=i) for x in c]
        if err is not None:
            errors.append(f"part {i} of {len(pieces)} ({day}): {err}")
    out = {"conversation_id": cid, "title": title, "session_date": date, "memories": kept, "rejected": rejected,
           "calls": calls, "pieces": [{"date": day, "lines": len(part)} for day, part in pieces]}
    if errors:   # the parts that worked are kept; the file counts as unfinished, so --skip-existing redoes it
        out["error"] = "; ".join(errors)
    return out


def _write_piece(title, date, lines, template, fetch, retries):
    """One model call (plus retries) for one piece: (kept, rejected, calls, error or None)."""
    prompt = build_prompt(template, title, date, lines)
    last_err = None
    calls = []
    for _ in range(retries + 1):
        try:
            text, rec = fetch(prompt)
            calls.append(rec)
            threads = parse_answer(text)
            break
        except ApiCallError as e:
            calls.append(e.record)
            last_err = str(e)
        except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            last_err = str(e)
    else:
        return [], [], calls, last_err
    kept, rejected = _check_all(threads, lines, date)
    # One corrective retry when a memory was rejected for a DATE the calendar proves wrong: the model is told which
    # date it got wrong and asked again. The answer that keeps more proven memories wins (ties: the retry).
    date_errors = [r["reason"] for r in rejected if _is_date_reason(r["reason"])]
    if date_errors:
        fix = ("\n\nYour previous answer had these date errors. Read each date off the relative-date key above and "
               "answer again in full:\n- " + "\n- ".join(date_errors) + "\n")
        try:
            text, rec = fetch(prompt + fix)
            calls.append(dict(rec, retry="date"))
            k2, r2 = _check_all(parse_answer(text), lines, date)
            if len(k2) >= len(kept):
                kept, rejected = k2, r2
        except ApiCallError as e:
            calls.append(dict(e.record, retry="date"))
        except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            pass
    return kept, rejected, calls, None


def sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def run_record(model_cmd, prompt_path, rendered=None):
    """What made this run: recorded once, copied into every output file."""
    def out(cmd, cwd=None):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=cwd,
                                  env=minimal_env()).stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired):
            return None
    prog = shlex.split(model_cmd)[0] if model_cmd and model_cmd.strip() else ""
    commit = out(["git", "rev-parse", "HEAD"], cwd=HERE)
    dirty = out(["git", "status", "--porcelain", "--", "."], cwd=HERE)
    return {"model_cmd": model_cmd,
            "claude_version": out(["claude", "--version"]) if os.path.basename(prog) == "claude" else None,
            "writer_commit": commit, "writer_dirty": bool(dirty),
            "prompt_file": os.path.basename(prompt_path),
            "prompt_sha256": (sha256_file(prompt_path) if rendered is None else
                              hashlib.sha256(rendered.encode("utf-8")).hexdigest()),
            "env_passed": sorted(minimal_env().keys())}


def finished(path):
    """A file counts as written only when it parses, has no error and holds at least one note."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return False
    return isinstance(d, dict) and not d.get("error") and bool(d.get("memories"))


def write_atomic(path, obj):
    fd, tmp = tempfile.mkstemp(prefix=".writing-", suffix=".json", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def render_instructions(template, prompt, user_name, agent_name):
    if prompt not in PROMPTS:
        return template
    labels = {"{{USER_NAME}}": user_name or "the user", "{{AGENT_NAME}}": agent_name or "the assistant"}
    if prompt == "house" and (user_name or agent_name):
        # The private template can predate the public placeholders.
        template += ('\n\nWhen a message begins with an explicit speaker name, use that name. '
                     'Otherwise use "{{USER_NAME}}" for the user and "{{AGENT_NAME}}" for the assistant.')
    return re.sub(r"\{\{(?:USER|AGENT)_NAME\}\}", lambda match: labels[match[0]], template)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--export", required=True, help="a Claude-format conversations.json")
    ap.add_argument("--out", required=True, help="folder for one notes file per conversation")
    ap.add_argument("--limit", type=int, default=0, help="only the first N conversations")
    ap.add_argument("--model",
                    help="model id for Claude Code or the API; pin one (claude-sonnet-5-5) for a reported run")
    ap.add_argument("--api", action="store_true",
                    help="call Anthropic directly and bill ANTHROPIC_API_KEY instead of the Claude Code login")
    ap.add_argument("--model-cmd", default=os.environ.get("MEMORY_WRITER_CMD"),
                    help="a whole command of your own instead of the default (overrides --model)")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT, help="public | house (House copy only) | a path to a prompt file")
    ap.add_argument("--user-name", default=os.environ.get("SCRIPTORIUM_USER_NAME"),
                    help="user name to send to the model in built-in prompts")
    ap.add_argument("--agent-name", default=os.environ.get("SCRIPTORIUM_AGENT_NAME"),
                    help="assistant name to send to the model in built-in prompts")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--timezone", default=os.environ.get("MEMORY_WRITER_TZ") or None,
                    help="IANA time zone for calendar days, e.g. Asia/Jakarta (default: the export's own, UTC)")
    ap.add_argument("--skip-existing", action="store_true",
                    help="leave conversations whose file is finished; files with an error or no notes are redone "
                         "(with --limit, the limit then counts conversations written, so each run continues)")
    ap.add_argument("--max-pieces", type=int, default=0,
                    help="stop before a conversation that would take this run past N model pieces (0 = no cap); "
                         "the first conversation of a run always completes")
    a = ap.parse_args(argv)
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if a.api:
        if a.model_cmd is not None:
            ap.error("--api cannot be used with --model-cmd or MEMORY_WRITER_CMD")
        if not key:
            ap.error("--api requires ANTHROPIC_API_KEY to be set")
    elif key and not a.model_cmd:
        print("ANTHROPIC_API_KEY is set, but the default runs through your Claude login; "
              "add --api to bill the API key instead.", file=sys.stderr)
    if a.timezone:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(a.timezone)
        except Exception:
            ap.error(f"unknown time zone {a.timezone!r} (use an IANA name such as Asia/Jakarta)")
    model = a.model if a.model is not None else (DEFAULT_API_MODEL if a.api else DEFAULT_MODEL)
    model_cmd = None if a.api else a.model_cmd or MODEL_CMD_TEMPLATE.format(model=shlex.quote(model))
    api_call = partial(call_api, model, key) if a.api else None
    prompt_path = PROMPTS.get(a.prompt, a.prompt)
    with open(prompt_path, encoding="utf-8") as f:
        template = render_instructions(f.read(), a.prompt, a.user_name, a.agent_name)
    with open(a.export, encoding="utf-8") as f:
        convs = json.load(f)
    if a.limit and not a.skip_existing:
        convs = convs[:a.limit]
    os.makedirs(a.out, exist_ok=True)
    record = dict(run_record(model_cmd, prompt_path, template), timezone=a.timezone or "as exported")
    if a.api:
        record.update(transport="anthropic_api", model=model)
    total = {"expected": len(convs), "skipped_finished": 0, "written": 0, "errored": 0, "empty": 0,
             "memories": 0, "rejected": 0, "cost_usd": 0.0}
    pieces_used, stopped = 0, None
    for conv in convs:
        cid = str(conv.get("uuid") or conv.get("id") or "")
        path = os.path.join(a.out, re.sub(r"[^A-Za-z0-9._:-]", "_", cid) + ".json")
        if a.skip_existing and finished(path):
            total["skipped_finished"] += 1
            continue
        if a.skip_existing and a.limit and total["written"] >= a.limit:
            stopped = f"limit of {a.limit} conversations written"
            break
        _c, _t, _lines = claude_lines(conv)
        n_pieces = len(split_pieces(_lines, session_date(conv, _lines, a.timezone), tz=a.timezone))
        if a.max_pieces and pieces_used and pieces_used + n_pieces > a.max_pieces:
            stopped = f"piece cap of {a.max_pieces} reached"
            break
        pieces_used += n_pieces
        t0 = time.time()
        res = write_conversation(conv, template, model_cmd, a.timeout, tz=a.timezone, api_call=api_call)
        res["writer"] = dict(record, seconds=round(time.time() - t0, 1))
        if a.api:
            res = _redact(res, key)
        write_atomic(path, res)
        total["written"] += 1
        total["memories"] += len(res["memories"])
        total["rejected"] += len(res["rejected"])
        for call in res.get("calls", []):
            cost = call.get("total_cost_usd")
            if a.api and cost is None:
                total["cost_incomplete"] = True
            total["cost_usd"] += cost or 0
        if res.get("error"):
            total["errored"] += 1
        elif not res["memories"]:
            total["empty"] += 1
        print(f"{_redact(cid, key) if a.api else cid}: {len(res['memories'])} kept, {len(res['rejected'])} rejected"
              + (f", ERROR {res['error']}" if res.get("error") else ""), flush=True)
    total["cost_usd"] = round(total["cost_usd"], 4)
    total["finished"] = total["skipped_finished"] + total["written"] - total["errored"] - total["empty"]
    total["pieces"] = pieces_used
    if stopped:
        total["stopped"] = stopped + "; run again to continue"
    print(json.dumps(total))
    print(f"expected {total['expected']} · finished {total['finished']} · errored {total['errored']} · "
          f"empty {total['empty']}" + (" · cost may be incomplete" if total.get("cost_incomplete") else "")
          + (f" · stopped: {total['stopped']}" if stopped else ""), flush=True)
    return 1 if (total["errored"] or total["empty"]) else 0


if __name__ == "__main__":
    sys.exit(main())
