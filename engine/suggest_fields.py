# Solance House — Copyright (c) 2026 House of Solance. All rights reserved. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""People cards: suggest which fixed facts (birthday, care, place, ...) are already written in a card's notes.

The model reads each card's notes and standing and names the facts it finds, each with the exact words that state it.
This file then CHECKS every answer in code (the quote must be word for word in the card's text, a birthday must be a
real day named in the quote, a time zone must be a real zone the quote points at, ...). A suggestion that fails is
listed under `rejected` with its reason, never dropped silently. Nothing here writes to any database: the output is a
file of suggestions for a person to accept or refuse.

    python3 suggest_fields.py --cards cards.json --out suggestions.json [--model claude-sonnet-5-5] [--only NAME ...] [--limit N]

cards.json is a list of {id, name, kind, aka, pronouns, notes, standing, care, birthday, dates, accounts, runs_on,
work, place, timezone}. A card with neither notes nor standing is skipped. A failed model call is recorded under
`errors` and the run goes on.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import write_memories as wm  # noqa: E402

SYSTEM = "You find fixed facts in a people card. Output only valid JSON."
PROMPT_FILE = os.path.join(HERE, "prompt-suggest.md")
MODEL_CMD_TEMPLATE = wm.MODEL_CMD_TEMPLATE.replace(
    "You write memories from one conversation. Output only valid JSON.", SYSTEM)

STD_PRONOUNS = {"she/her": ("she", "her"), "he/him": ("he", "him"), "they/them": ("they", "them")}
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
CAPS = {"care": 600, "work": 120, "place": 120, "runs_on": 120}
FIELDS = ("care", "birthday", "dates", "accounts", "runs_on", "work", "place", "timezone", "pronouns", "aka")
LIST_FIELDS = ("dates", "accounts", "aka")          # a card can hold several; every other field holds one
_norm = wm._norm


_STOP = {"a", "an", "the", "of", "in", "on", "at", "as", "is", "and", "to", "for", "with"}
_MON = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
        r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")


def _month_no(name):
    return next(i + 1 for i, m in enumerate(MONTHS) if m.startswith(name[:3]))


def _dates_in(quote):
    """Every date the quote writes out, as (year|None, month, day|None). Numeric day/month pairs count both ways."""
    low, out = quote.lower(), []

    def add(y, m, d):
        if 1 <= m <= 12 and (d is None or 1 <= d <= 31):
            out.append((int(y) if y else None, m, d))
    for m in re.finditer(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?%s\b(?:,?\s+(\d{4})(?!\d))?" % _MON, low):
        add(m.group(3), _month_no(m.group(2)), int(m.group(1)))
    for m in re.finditer(r"\b%s\s+(\d{1,2})(?!\d)(?:st|nd|rd|th)?(?:,?\s+(\d{4})(?!\d))?" % _MON, low):
        add(m.group(3), _month_no(m.group(1)), int(m.group(2)))
    for m in re.finditer(r"\b%s,?\s+(\d{4})(?!\d)" % _MON, low):
        add(m.group(2), _month_no(m.group(1)), None)
    for m in re.finditer(r"(?<![\d-])(\d{4})-(\d{2})(?:-(\d{2}))?(?![\d-])", low):
        add(m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else None)
    for m in re.finditer(r"(?<![\d/.-])(\d{1,2})[/-](\d{1,2})(?:[/-](\d{4}))?(?![\d/-])", low):
        add(m.group(3), int(m.group(1)), int(m.group(2)))
        add(m.group(3), int(m.group(2)), int(m.group(1)))
    return out


def _tokens(s):
    return re.findall(r"\w+", s.lower())


def _words_in(value, quote):
    """True when every content word of `value` is a whole word of `quote`."""
    toks = _tokens(value)
    need = [t for t in toks if t not in _STOP] or toks
    have = set(_tokens(quote))
    return bool(need) and all(t in have for t in need)


def _whole(needle, text, extra=""):
    """needle as a whole token (case-insensitive): not inside a longer word or handle."""
    return re.search(r"(?<![\w%s])%s(?![\w])" % (extra, re.escape(needle.lower())), text.lower()) is not None


def _handle_in(handle, quote):
    h = handle.lstrip("@").lower()
    return bool(h) and re.search(r"(?<![\w.@-])@?%s(?![\w-]|\.\w)" % re.escape(h), quote.lower()) is not None


def _parse_date(s):
    """(year|None, month|None, day|None) for YYYY, YYYY-MM, YYYY-MM-DD or MM-DD; None when not a real date."""
    if not isinstance(s, str):
        return None
    m = re.fullmatch(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", s)
    if m:
        y = int(m.group(1))
        mo = int(m.group(2)) if m.group(2) else None
        d = int(m.group(3)) if m.group(3) else None
        if mo is not None and not 1 <= mo <= 12:
            return None
        if d is not None:
            try:
                dt.date(y, mo, d)
            except ValueError:
                return None
        return (y, mo, d)
    m = re.fullmatch(r"(\d{2})-(\d{2})", s)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        try:
            dt.date(2000, mo, d)          # a leap year: 02-29 is a real birthday
        except ValueError:
            return None
        return (None, mo, d)
    return None


_BIRTH_CUE = re.compile(r"\b(?:birthday|bday|b-day|birth|born|dob|turns|turned)\b", re.I)


def _clauses(quote):
    """The quote cut into clauses (at ; ! ? newlines, sentence ends and commas that do not join a date to its year)."""
    quote = _ABBR.sub(r"\1", quote)         # "Jan. 3" keeps its period out of the sentence split
    parts = re.split(r"[;!?\n]|\.\s+(?!\d{4}(?!\d))|,(?!\s*\d{4})", quote)
    return [c for c in (p.strip() for p in parts) if c]


_NEGATIVE = re.compile(r"\b(?:not|never|no longer|isn't|wasn't|aren't|don't|doesn't|didn't|nor|neither|formerly|"
                       r"used to|previously)\b|n't\b", re.I)
_CORRECTION = re.compile(r"\b(?:misfiled|wrong|incorrect)\b", re.I)
SCALAR_FIELDS = ("birthday", "runs_on", "work", "place", "timezone", "pronouns", "care")


def _after_correction(quote):
    """(tail, corrected). Blunt rule: when the quote says something was wrong, only the words after the LAST such
    marker count, so the corrected value passes and the value being corrected does not."""
    last = None
    for last in _CORRECTION.finditer(quote):
        pass
    return (quote[last.end():], True) if last else (quote, False)


def _negative(quote):
    """Blunt rule, no grammar: a quote with any negating or past-tense word is left for a person to check."""
    return _NEGATIVE.search(quote.replace("\u2019", "'")) is not None


_ABBR = re.compile(r"\b(jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|dec)\.", re.I)


def _date_named_in(parts, quote, tie=None, tie_why=""):
    """None when the quote writes out this date as one date (year, month and day together) inside ONE clause that
    also satisfies `tie(clause)` (the label's words, a birthday cue); else the reason."""
    y, mo, d = parts
    if y is not None and not re.search(r"(?<!\d)%d(?!\d)" % y, quote):
        return "the year %d is not in the quote" % y
    found = False
    for cl in _clauses(quote):
        if mo is None:
            ok = re.search(r"(?<!\d)%d(?!\d)" % y, cl) is not None
        else:
            ok = any(cm == mo and (d is None or cd == d) and (y is None or cy == y) for cy, cm, cd in _dates_in(cl))
        if not ok:
            continue
        found = True
        if tie is None or tie(cl):
            return None
    if found:
        return tie_why
    if mo is None:
        return "the year is not in the quote"
    return "the quote does not name this month and day together as one date" if d else \
        "the quote does not name this year and month together"


def _zone_countries():
    """{zone: country name} from the system's zone.tab and iso3166.tab; empty when the files are absent."""
    import zoneinfo
    for base in zoneinfo.TZPATH:
        zt, it = os.path.join(base, "zone.tab"), os.path.join(base, "iso3166.tab")
        if os.path.exists(zt) and os.path.exists(it):
            try:
                names, out = {}, {}
                with open(it, encoding="utf-8") as f:
                    for line in f:
                        if line.strip() and not line.startswith("#"):
                            code, _, name = line.rstrip("\n").partition("\t")
                            names[code] = name
                with open(zt, encoding="utf-8") as f:
                    for line in f:
                        cols = line.rstrip("\n").split("\t")
                        if line.strip() and not line.startswith("#") and len(cols) >= 3:
                            out[cols[2]] = names.get(cols[0], "")
                return out
            except OSError:
                return {}
    return {}


def check_timezone(value, quote):
    """(ok, inferred, why). The zone is real (zoneinfo) and the quote points at it: the zone name or its city
    (explicit), or the country it lies in (inferred: the person must confirm the zone)."""
    import zoneinfo
    if not isinstance(value, str) or ("/" not in value and value != "UTC"):
        return False, False, "not an IANA zone name"
    try:
        zoneinfo.ZoneInfo(value)
    except Exception:
        return False, False, "not a real IANA zone"
    low = quote.lower()
    city = value.split("/")[-1].replace("_", " ").lower()
    if value.lower() in low or (len(city) >= 3 and re.search(r"\b%s\b" % re.escape(city), low)):
        return True, False, ""
    country = _zone_countries().get(value, "")
    if country and re.search(r"\b%s\b" % re.escape(country.lower()), low):
        return True, True, ""
    return False, False, "the quote names no zone, city or country that matches the zone"


def _same_item(a, b, keys):
    return all(_norm(str(a.get(k, ""))).lower() == _norm(str(b.get(k, ""))).lower() for k in keys)


def check_suggestion(card, sug, text):
    """(entry, why). entry = {field, value, quote, current[, inferred]} when it passes, else None and the reason.
    `text` is the card's notes + standing, whitespace-normalised: one string, or a list of strings (notes and
    standing apart) when the quote must sit inside ONE of them, never across the join."""
    if not isinstance(sug, dict):
        return None, "not an object"
    field, value, quote = sug.get("field"), sug.get("value"), sug.get("quote")
    if field not in FIELDS:
        return None, "unknown field"
    if not isinstance(quote, str) or not _norm(quote):
        return None, "no quote"
    quote = _norm(quote)
    if not any(quote in t for t in ([text] if isinstance(text, str) else text)):
        return None, "the quote is not word for word in the notes or standing"
    current = card.get(field)
    entry = {"field": field, "quote": quote}
    if field in ("dates", "accounts"):
        if not isinstance(value, dict):
            return None, "value is not an object"
        keys = ("label", "date") if field == "dates" else ("platform", "handle")
        v = {k: _norm(value.get(k)) if isinstance(value.get(k), str) else "" for k in keys}
        if not all(v.values()):
            return None, "needs " + " and ".join(keys)
        if field == "dates":
            parts = _parse_date(v["date"])
            if not parts:
                return None, "not a real date (YYYY, YYYY-MM, YYYY-MM-DD or MM-DD)"
            if not _words_in(v["label"], quote):
                return None, "the label uses words that are not in the quote"
            why = _date_named_in(parts, quote, lambda cl: _words_in(v["label"], cl),
                                 "the date and the label are not in the same part of the quote")
            if why:
                return None, why
        else:
            # Discord has its own field (discord_ids), and an email address is not a public handle (21 Oct run: 10 + 1)
            if v["platform"].strip().lower() == "discord" or re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v["handle"].strip()):
                return None, "Discord ids and email addresses are not accounts"
            if not _handle_in(v["handle"], quote):
                return None, "the handle is not in the quote as a whole handle"
            if not _whole(v["platform"], quote):
                return None, "the platform is not in the quote"
        if isinstance(current, list) and any(isinstance(c, dict) and _same_item(c, v, keys) for c in current):
            return None, "the card already holds this"
        entry["value"] = v
    else:
        if not isinstance(value, str) or not _norm(value):
            return None, "value is not text"
        value = _norm(value)
        full, (quote, corrected) = quote, _after_correction(quote)
        if corrected:
            entry["_corrects"] = True
            if not _norm(quote):
                return None, "quote is negative or past; check by hand"
        if field in CAPS and len(value) > CAPS[field]:
            return None, "longer than %d characters" % CAPS[field]
        if field in ("care", "work", "place", "runs_on") and not _words_in(value, quote):
            return None, "the value uses words that are not in the quote"
        if field != "care" and _negative(quote):
            return None, "quote is negative or past; check by hand"
        if field == "runs_on" and str(card.get("kind") or "").lower() != "ai":
            return None, "runs_on is only for an AI card"
        if field == "birthday":
            parts = _parse_date(value)
            if not parts or parts[1] is None or parts[2] is None:
                return None, "not a real day (MM-DD or YYYY-MM-DD)"
            why = _date_named_in(parts, quote, lambda cl: _BIRTH_CUE.search(cl) is not None,
                                 "the quote does not tie this date to a birthday (born, birthday, ...)")
            if why:
                return None, why
        elif field == "timezone":
            ok, inferred, why = check_timezone(value, quote)
            if not ok:
                return None, why
            if inferred:
                entry["inferred"] = True
        elif field == "pronouns":
            low = value.lower()
            if low in STD_PRONOUNS:
                a, b = STD_PRONOUNS[low]
                if re.search(r"\b%s\s*/\s*%s\b" % (a, b), quote.lower()) is None:
                    return None, "the quote does not write the pair as %s/%s" % (a, b)
                value = low
            elif not _whole(low, quote, "/"):
                return None, "the pronouns are not in the quote as written"
        elif field == "aka" and not _whole(value, quote):
            return None, "the name is not a whole word in the quote"
        if field == "aka" and isinstance(current, list):
            same = any(_norm(str(c)).lower() == value.lower() for c in current)
        else:
            same = isinstance(current, str) and _norm(current).lower() == value.lower()
        if same:
            return None, "the card already holds this"
        entry["value"] = value
        entry["quote"] = full
    entry["current"] = current
    return entry, ""


def build_prompt(template, card):
    """Notes and standing with each line numbered (the numbering runs across both)."""
    n = 0

    def block(text):
        nonlocal n
        out = []
        for line in (text or "").splitlines():
            if line.strip():
                n += 1
                out.append("%d. %s" % (n, line))
        return "\n".join(out) or "(none)"
    notes, standing = block(card.get("notes")), block(card.get("standing"))
    cur = {f: card.get(f) for f in FIELDS if card.get(f) not in (None, "", [])}
    vals = {"NAME": str(card.get("name") or ""), "KIND": str(card.get("kind") or ""),
            "CURRENT": json.dumps(cur, ensure_ascii=False, indent=1) if cur else "(nothing yet)",
            "NOTES": notes, "STANDING": standing}
    # one pass: card text is never scanned for tokens, so a literal {{STANDING}} in notes stays as written
    return re.sub(r"\{\{(NAME|KIND|CURRENT|NOTES|STANDING)\}\}", lambda m: vals[m.group(1)], template)


def parse_suggestions(raw):
    text = re.sub(r"```(?:json)?", "", raw or "").strip()
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("no JSON object in the answer")
    data = json.loads(text[a:b + 1])
    sugs = data.get("suggestions") if isinstance(data, dict) else None
    if not isinstance(sugs, list):
        raise ValueError('the answer has no "suggestions" list')
    return sugs


def suggest(cards, model_cmd, timeout=600, only=None, limit=None):
    with open(PROMPT_FILE, encoding="utf-8") as f:
        template = f.read()
    only_l = {str(o).casefold() for o in (only or [])}
    out = {"suggestions": [], "rejected": [], "errors": [], "cards": 0}
    for card in cards:
        if not isinstance(card, dict) or not ((card.get("notes") or "").strip() or (card.get("standing") or "").strip()):
            continue
        if only_l and str(card.get("name", "")).casefold() not in only_l and str(card.get("id", "")).casefold() not in only_l:
            continue
        if limit is not None and out["cards"] >= limit:
            break
        out["cards"] += 1
        who = {"id": card.get("id"), "name": card.get("name")}
        try:
            text, _rec = wm.unwrap(wm.call_model(model_cmd, build_prompt(template, card), timeout))
            sugs = parse_suggestions(text)
        except Exception as e:                    # one card failing never stops the run
            out["errors"].append(dict(who, error=str(e)[:300] or type(e).__name__))
            continue
        body = [_norm(card.get("notes") or ""), _norm(card.get("standing") or "")]
        good = []
        for s in sugs:
            entry, why = check_suggestion(card, s, body)
            if entry:
                good.append(entry)
            else:
                g = s if isinstance(s, dict) else {}
                out["rejected"].append(dict(who, field=g.get("field"), value=g.get("value"), quote=g.get("quote"), why=why))
        by_field = {}
        for e in good:
            if e["field"] in SCALAR_FIELDS:
                by_field.setdefault(e["field"], set()).add(_norm(e["value"]).lower())
        fixed = {}                                # field -> values stated by a correction
        for e in good:
            if e["field"] in SCALAR_FIELDS and e.get("_corrects"):
                fixed.setdefault(e["field"], set()).add(_norm(e["value"]).lower())
        kept = set()
        for e in good:
            f = e["field"]
            if f in SCALAR_FIELDS and len(by_field[f]) > 1:
                mine = _norm(e["value"]).lower()
                if len(fixed.get(f, ())) == 1 and mine not in fixed[f]:
                    why = "an older value; the card text corrects it"
                elif len(fixed.get(f, ())) == 1:
                    why = None                    # the one corrected value wins
                else:
                    why = "conflicting values for %s; check by hand" % f
                if why:
                    out["rejected"].append(dict(who, field=f, value=e["value"], quote=e["quote"], why=why))
                    continue
            key = (f, _norm(e["value"]).lower()) if f in SCALAR_FIELDS else None
            if key and key in kept:
                continue                          # an identical duplicate collapses to one
            if key:
                kept.add(key)
            e.pop("_corrects", None)
            out["suggestions"].append(dict(who, **e))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cards", required=True, help="a JSON list of people cards")
    ap.add_argument("--out", required=True, help="the suggestions JSON file to write")
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--model-cmd", default=None, help="a whole command of your own (overrides --model)")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--only", nargs="+", default=None, help="only cards with these names (or ids)")
    ap.add_argument("--limit", type=int, default=None, help="at most N cards go to the model")
    a = ap.parse_args(argv)
    with open(a.cards, encoding="utf-8") as f:
        cards = json.load(f)
    if not isinstance(cards, list):
        print("cards file must hold a JSON list", file=sys.stderr)
        return 2
    model_cmd = a.model_cmd or MODEL_CMD_TEMPLATE.format(model=wm.shlex.quote(a.model))
    res = suggest(cards, model_cmd, a.timeout, a.only, a.limit)
    doc = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "model": "custom command" if a.model_cmd else a.model, "cards": res["cards"],
           "suggestions": res["suggestions"], "rejected": res["rejected"], "errors": res["errors"]}
    wm.write_atomic(a.out, doc)
    print("%d cards, %d suggestions, %d rejected, %d errors -> %s" % (
        res["cards"], len(res["suggestions"]), len(res["rejected"]), len(res["errors"]), a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
