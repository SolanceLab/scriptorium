# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""People cards — the memories of one user's many sessions in, one card file per recurring person out.

Memories are written one session at a time, so nothing joins "Nate likes coconut ice cream" (session 3) with "Nate
baked a dairy-free cake" (session 7). This pass reads every memory already written for one user and writes, for each
person who recurs, a card: a list of items, each item one aspect of the person with every fact on it and each fact's
date. Every item is checked like any memory: its line ids must exist in the export, and its excerpt must be word for
word in the lines it cites. An item that fails is reported, never kept.

    python3 write_people.py --export conversations.json --memories DIR --out DIR [--model claude-sonnet-5-5]

A card is written in the same memory-file format (conversation_id = "person:<key>"), so the search adapter reads it
like any memory file; its source lines come from many sessions (the adapter finds a line id across the export when it
is unique there). Existing cards in --out are given to the model and updated, so the same pass serves one new session
in daily use and a whole history in a batch.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import write_memories as wm  # noqa: E402

SYSTEM = "You join memories into people cards. Output only valid JSON."
PROMPT_FILE = os.path.join(HERE, "prompt-people.md")
# Who gets a card is decided HERE, not by the model (benchmark 18 Oct: the same input gave 6 cards one run and 1 the
# next, all the difference in one-off side characters). The model writes items for everyone it finds; a card is kept
# only for someone whose items rest on at least MIN_MEMORIES memories from at least MIN_SESSIONS sessions.
MIN_SESSIONS, MIN_MEMORIES = 2, 3
MODEL_CMD_TEMPLATE = wm.MODEL_CMD_TEMPLATE.replace(
    "You write memories from one conversation. Output only valid JSON.", SYSTEM)


def person_key(name):
    """A file-safe key that keeps letters of every script (李明 stays 李明), so different names never collapse into
    one card. Capital letters, spacing and punctuation are ignored on purpose: "Jean-Luc" and "Jean Luc" are one
    person spelled two ways. A name with no letters at all gets a short fingerprint of its own."""
    import hashlib
    import unicodedata
    s = unicodedata.normalize("NFKC", str(name)).lower()
    slug = re.sub(r"[\W_]+", "-", s, flags=re.UNICODE).strip("-")
    if not slug:
        return "person-" + hashlib.sha1(s.encode()).hexdigest()[:8]
    if len(slug) > 80:                                   # cut, plus a fingerprint so two long names never collide
        return slug[:70].rstrip("-_") + "-" + hashlib.sha1(s.encode()).hexdigest()[:8]
    return slug


def export_index(export_path, tz=None):
    """({line id: text} for ids that are unique across the export, {line id: its day YYYY-MM-DD}). A line's day is the
    one the memory writer gave its piece: the line's own timestamp in the chosen time zone, else the conversation's."""
    with open(export_path, encoding="utf-8") as f:
        convs = json.load(f)
    seen, text, when = {}, {}, {}
    for conv in convs:
        _cid, _title, lines = wm.claude_lines(conv)
        date = wm.session_date(conv, lines, tz)
        for day, piece in wm.split_pieces(lines, date, tz=tz):
            for lid, _sender, body, _at in piece:
                seen[lid] = seen.get(lid, 0) + 1
                text[lid], when[lid] = body, day
    unique = {k: v for k, v in text.items() if seen[k] == 1}   # an ambiguous id can never back a fact
    return unique, when


def load_memories(mem_dir):
    """Every memory of one user, oldest session first, as compact records for the prompt."""
    out = []
    for name in sorted(os.listdir(mem_dir)):
        # a linked file could point anywhere on the machine, and its text would go to the model: real files only
        if not name.endswith(".json") or os.path.islink(os.path.join(mem_dir, name)):
            continue
        try:
            with open(os.path.join(mem_dir, name), encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            continue
        for m in doc.get("memories") or []:
            if isinstance(m, dict) and m.get("summary") and m.get("source_line_ids"):
                # each memory's own day (a long chat's later pieces carry later days), and its own conversation
                out.append({"date": m.get("source_session_date") or doc.get("session_date") or "",
                            "cid": str(doc.get("conversation_id") or name),
                            "lines": [str(i) for i in m["source_line_ids"]], "summary": m["summary"],
                            "excerpt": m.get("excerpt", "")})
    out.sort(key=lambda r: r["date"])
    for n, r in enumerate(out, 1):
        r["mid"] = f"M{n}"
    return out


def load_cards(out_dir):
    cards = []
    if os.path.isdir(out_dir):
        for name in sorted(os.listdir(out_dir)):
            if (name.startswith("person-") and name.endswith(".json")
                    and not os.path.islink(os.path.join(out_dir, name))):   # real files only, as above
                try:
                    with open(os.path.join(out_dir, name), encoding="utf-8") as f:
                        doc = json.load(f)
                    for m in doc.get("memories") or []:
                        cards.append({"person": m.get("person") or doc.get("title"), "aspect": m.get("aspect"),
                                      "summary": m.get("summary"), "excerpt": m.get("excerpt"),
                                      "source_line_ids": m.get("source_line_ids")})
                except (OSError, ValueError):
                    continue
    return cards


def build_prompt(template, memories, cards):
    parts = [template, "\n---\n"]
    if cards:
        parts.append("Existing cards (update these):\n" + json.dumps(cards, ensure_ascii=False) + "\n")
    parts.append("Memories, oldest first:\n")
    for r in memories:
        parts.append(f"{r['mid']} [{r['date']}] {r['summary']}\n")
    return "".join(parts)


def dates_backed(summary, used):
    """None when every calendar date in a card's summary is backed by the memories it cites (a memory's own day, or a
    date written in its summary or excerpt); otherwise the reason. A card may only repeat dates, never make one."""
    import datetime as dt
    allowed = set()
    for r in used:
        try:
            day = dt.date.fromisoformat(str(r["date"])[:10])
        except ValueError:
            continue
        allowed.add(day)
        source = f"{r.get('summary', '')} {r.get('excerpt', '')}"
        for d, *_ in wm.dates_in(source, day):
            allowed.add(d)
        for m in _NUMERIC_DATE.finditer(source):          # a numeric date written in the memory is backed too
            allowed.update(_numeric_readings(m))
    if not allowed:
        return None
    latest = max(allowed)
    for d, start, end, _wd in wm.dates_in(summary, latest):
        yearless = not any(m.start() == start and m.group("y") for rx in wm._DATE_RES for m in rx.finditer(summary))
        year_before = None
        if yearless:                                      # only a date written without its year may sit a year back
            try:
                year_before = d.replace(year=d.year - 1)
            except ValueError:                            # 29 February
                pass
        if d not in allowed and year_before not in allowed:
            return f"date {wm._day(d)} is not in the memories this item cites"
    for m in _NUMERIC_DATE.finditer(summary or ""):                          # ISO and numeric dates are checked too
        readings = _numeric_readings(m)
        if not readings:
            return f"{m.group(0)} is not a real date"
        if not any(d in allowed for d in readings):
            return f"date {wm._day(readings[0])} is not in the memories this item cites"
    return None


_NUMERIC_DATE = re.compile(r"\b(\d{1,4})[/.-](\d{1,2})[/.-](\d{1,4})\b")


def _numeric_readings(m):
    """Every real calendar date a numeric token like 05/08/2024, 2024-05-08 or 8-5-2024 can mean."""
    a, b, c = m.group(1), m.group(2), m.group(3)
    combos = []
    if len(a) == 4:
        combos.append((a, b, c))                                     # year, month, day
    elif len(c) == 4:
        combos += [(c, b, a), (c, a, b)]                             # day first, then month first
    out = []
    for y, mo, d in combos:
        try:
            day = dt.date(int(y), int(mo), int(d))
        except ValueError:
            continue
        if day not in out:
            out.append(day)
    return out


def has_date(summary):
    """True when the summary carries at least one date: month-name, ISO or numeric."""
    if wm.dates_in(summary or "", dt.date(2000, 1, 1)):
        return True
    return any(_numeric_readings(m) for m in _NUMERIC_DATE.finditer(summary or ""))


def check_item(item, by_id, when, by_mid):
    """(memory dict, None) or (None, reason). The model names the memories an item rests on; the excerpt and line
    ids are ASSEMBLED here from those memories (already checked word for word), so a quote cannot be paraphrased.
    Then the same checks as any memory run across sessions."""
    if not isinstance(item, dict) or not item.get("person") or not item.get("aspect"):
        return None, "an item needs a person and an aspect"
    mids = item.get("memories")
    if not isinstance(mids, list) or not mids:
        return None, "memories must name at least one memory (M1, M2, ...)"
    unknown = [m for m in mids if str(m) not in by_mid]
    if unknown:
        return None, "cites memories that do not exist: " + ", ".join(map(str, unknown))
    used = [by_mid[str(m)] for m in dict.fromkeys(map(str, mids))]
    ids = list(dict.fromkeys(i for r in used for i in r["lines"]))
    excerpt = f" {wm.GAP} ".join(r["excerpt"] for r in used if r["excerpt"])
    dates = sorted({when[i] for i in ids if i in when})
    thread = {"topic": "Personal", "source_line_ids": ids, "excerpt": excerpt,
              "summary": item.get("summary"), "source_session_date": dates[-1] if dates else ""}
    # relative=False: a card spans sessions, so "last Friday" in one line is not about the latest session's week
    clean, why = wm.check_thread(thread, by_id, dates[-1] if dates else "", relative=False)
    if not clean:
        return None, why
    why = dates_backed(clean["summary"], used)
    if why:
        return None, why
    # only the identity item ("who they are", as the prompt names it) may carry no date; "people who helped" may not
    identity = re.match(r"^who (?:they are|he is|she is|it is)\b", " ".join(str(item["aspect"]).lower().split()))
    if not identity and not has_date(clean["summary"]):
        return None, "a fact item must carry the date its memories give"
    person = str(item["person"]).strip()
    return {"topic": person, "person": person, "aspect": str(item["aspect"]).strip()[:80],
            "summary": clean["summary"], "excerpt": clean["excerpt"], "source_line_ids": ids,
            "memories_used": [r["mid"] for r in used], "session_dates": sorted({r["date"] for r in used}),
            # a session is one conversation on one day: two chats on the same day are two sessions, and the days of
            # one long chat are separate sessions
            "sessions": sorted({f"{r['cid']}@{r['date']}" for r in used}),
            "source_session_date": dates[-1] if dates else None, "first_session_date": dates[0] if dates else None,
            "salience": 0.5, "valence": 0.0, "emotions": [], "context_tags": ["person-card"]}, None


def write_people(export_path, mem_dir, out_dir, model_cmd, timeout=900, retries=1, tz=None):
    by_id, when = export_index(export_path, tz)
    memories = load_memories(mem_dir)
    cards = load_cards(out_dir)
    with open(PROMPT_FILE, encoding="utf-8") as f:
        template = f.read()
    prompt = build_prompt(template, memories, cards)
    calls, items, last_err = [], None, None
    for _ in range(retries + 1):
        try:
            text, rec = wm.unwrap(wm.call_model(model_cmd, prompt, timeout))
            calls.append(rec)
            items = wm.parse_answer(text)
            break
        except (ValueError, RuntimeError, OSError, wm.subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            last_err = str(e)
    if items is None:
        return {"error": last_err, "calls": calls, "people": {}, "rejected": []}
    people, rejected = {}, []
    by_mid = {r["mid"]: r for r in memories}
    for it in items:
        mem, why = check_item(it, by_id, when, by_mid)
        if mem:
            people.setdefault(person_key(mem["person"]), []).append(mem)
        else:
            rejected.append({"reason": why, "item": it})
    dropped = {}
    for key in list(people):
        mids = {m for it in people[key] for m in it["memories_used"]}
        sessions = {s for it in people[key] for s in it["sessions"]}
        if len(sessions) < MIN_SESSIONS or len(mids) < MIN_MEMORIES:
            dropped[people.pop(key)[0]["person"]] = {"memories": len(mids), "sessions": len(sessions)}
    return {"people": people, "rejected": rejected, "dropped": dropped, "calls": calls, "memories_read": len(memories),
            "cards_updated": len(cards)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--export", required=True, help="the user's Claude-format conversations.json")
    ap.add_argument("--memories", required=True, help="the folder of that user's per-session memory files")
    ap.add_argument("--out", required=True, help="folder for one person-<key>.json card per person")
    ap.add_argument("--model", default=wm.DEFAULT_MODEL, help="pin one (claude-sonnet-5-5) for a reported run")
    ap.add_argument("--model-cmd", default=None, help="a whole command of your own (overrides --model)")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--timezone", default=os.environ.get("MEMORY_WRITER_TZ") or None,
                    help="the same IANA time zone the memories were written with, e.g. Asia/Jakarta")
    a = ap.parse_args(argv)
    if a.timezone:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(a.timezone)
        except Exception:
            ap.error(f"unknown time zone {a.timezone!r} (use an IANA name such as Asia/Jakarta)")
    model_cmd = a.model_cmd or MODEL_CMD_TEMPLATE.format(model=wm.shlex.quote(a.model))
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()
    res = write_people(a.export, a.memories, a.out, model_cmd, a.timeout, tz=a.timezone)
    record = dict(wm.run_record(model_cmd, PROMPT_FILE), seconds=round(time.time() - t0, 1), timezone=a.timezone or "as exported",
                  memories_read=res.get("memories_read"), cards_updated=res.get("cards_updated"))
    if res.get("error"):
        print(json.dumps({"error": res["error"]}))
        return 1
    for key, items in res["people"].items():
        name = items[0]["person"]
        wm.write_atomic(os.path.join(a.out, f"person-{key}.json"), {
            "conversation_id": f"person:{key}", "title": f"{name} (person card)",
            "session_date": max((i["source_session_date"] or "" for i in items), default=None),
            "memories": items, "rejected": [r for r in res["rejected"]
                                            if person_key((r.get("item") or {}).get("person", "")) == key],
            "calls": res["calls"], "writer": record})
    # A card from an earlier run that this run did not write (the person now falls below the line, or was renamed)
    # is set aside as person-<key>.json.stale: kept on disk, no longer read by this writer or by a recall engine.
    stale = []
    if not res.get("error"):     # a run that answered (even with no cards) settles the folder; a failed run never does
        for name in sorted(os.listdir(a.out)):
            p = os.path.join(a.out, name)
            if (name.startswith("person-") and name.endswith(".json") and not os.path.islink(p)
                    and name[len("person-"):-len(".json")] not in res["people"]):
                target, n = p + ".stale", 1
                while os.path.exists(target):            # an earlier set-aside copy is never overwritten
                    n += 1
                    target = f"{p}.stale.{n}"
                os.replace(p, target)
                stale.append(name)
    cost = sum(c.get("total_cost_usd") or 0 for c in res["calls"])
    print(json.dumps({"people": len(res["people"]), "items": sum(len(v) for v in res["people"].values()),
                      "below_threshold": res.get("dropped", {}), "set_aside": stale,
                      "rejected": len(res["rejected"]), "cost_usd": round(cost, 4)}))
    return 0     # the model answered; zero cards (nobody recurring) is a result, not a failure


if __name__ == "__main__":
    sys.exit(main())
