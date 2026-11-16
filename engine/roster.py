# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The roster runner: read new messages from a source, find facts, hand them to a sink, and move a cursor safely.

    run_roster(source, sink, state_dir, model_cmd=...) -> status dict

source  an object with read(cursor) -> messages newer than the cursor, and cards() -> person cards.
        A message is {id, sender, said_at, text} and may carry sender_id, room, thread.
        The cursor is the dict kept in STATE/cursor.json ({} the first time); the source decides what "newer" means.
sink    an object with write(facts, suggestions) -> {stored, skipped, rejected: [{item, reason}], deferred}
        (whole numbers, and a list). It may add facts_stored and suggestions_stored.
        Writing the same item twice must be safe: the sink counts the second one as skipped.

The runner keeps these files in STATE:
    cursor.json    {last_said_at, last_id, last_ids, updated_at}
    status.json    the last run, written whole every run
    rejected.jsonl one line for every item that was turned away (by the engine's checks or by the sink), with the reason
    roster.lock    one run at a time

The cursor moves only when no person failed, every message had a time, the sink answered in the right shape and
deferred nothing. Items the engine or the sink rejected do not hold it: they are kept in rejected.jsonl.
"""
import datetime as dt
import importlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import write_memories as wm  # noqa: E402
import found_in_chats as fc  # noqa: E402

STATUS_FILE, CURSOR_FILE, REJECTED_FILE, LOCK_FILE = "status.json", "cursor.json", "rejected.jsonl", "roster.lock"


class RosterError(Exception):
    pass


class LockHeld(RosterError):
    pass


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as e:
        raise RosterError("cannot read %s: %s" % (path, e))


# ---- the lock -------------------------------------------------------------------------------------------------

STALE_LOCK_SECONDS = 6 * 3600


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True                     # exists but is not ours (or the check is unavailable): treat as alive
    return True


def _lock_is_stale(path):
    """A leftover run-file is stale when its pid is gone, or (pid unreadable) when it is older than six hours."""
    try:
        age = time.time() - os.path.getmtime(path)
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        return False
    try:
        pid = int(text)
    except ValueError:
        return age > STALE_LOCK_SECONDS
    if os.name == "nt":
        return age > STALE_LOCK_SECONDS
    return not _pid_alive(pid)


def create_run_file(path):
    """Create-exclusive <path> holding our pid; a stale one (crashed run) is removed first. FileExistsError if live."""
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _lock_is_stale(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
                continue
            raise
        os.write(fd, str(os.getpid()).encode())
        return fd
    raise FileExistsError(path)


class Lock:
    """One run at a time: an exclusive lock on <path>, released by the system if the run dies."""

    def __init__(self, path):
        self.path, self.fd, self.fallback = path, None, False

    def __enter__(self):
        try:
            import fcntl
        except ImportError:                     # no fcntl (Windows): a plain create-exclusive file
            self.fallback = True
            try:
                self.fd = create_run_file(self.path + ".run")
            except FileExistsError:
                raise LockHeld("another run holds the lock (%s.run)" % self.path)
            return self
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.fd)
            self.fd = None
            raise LockHeld("another run is in progress (lock: %s)" % self.path)
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
            if self.fallback:
                try:
                    os.remove(self.path + ".run")
                except OSError:
                    pass


# ---- plugging in a source or a sink ---------------------------------------------------------------------------

def load_ref(ref, needs):
    """'package.module:attr' -> the object it names. If that is a class, or a factory function (it has none of the
    `needs` methods but can be called), it is called once with no arguments."""
    mod, sep, attr = str(ref).partition(":")
    if not (mod and sep and attr):
        raise RosterError("%r is not a module:attr reference" % ref)
    try:
        obj = getattr(importlib.import_module(mod), attr)
    except (ImportError, AttributeError) as e:
        raise RosterError("cannot load %s: %s" % (ref, e))
    if isinstance(obj, type) or (callable(obj) and not any(hasattr(obj, n) for n in needs)):
        obj = obj()
    for n in needs:
        if not callable(getattr(obj, n, None)):
            raise RosterError("%s has no %s() method" % (ref, n))
    return obj


# ---- the run --------------------------------------------------------------------------------------------------

def _key(m):
    return (fc._when(m), str(m["id"]))


def check_answer(ans, expected=None):
    """The sink's answer, or RosterError when it is not the agreed shape.

    expected: how many items were sent (facts + suggestions). When given, stored + skipped + rejected +
    deferred must add up to it, so an all-zero answer cannot pass and let the cursor drop items."""
    if not isinstance(ans, dict):
        raise RosterError("the sink did not answer with a dict")
    for k in ("stored", "skipped", "deferred"):
        v = ans.get(k)
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise RosterError("the sink's answer has no whole number %r" % k)
    rej = ans.get("rejected")
    if not isinstance(rej, list) or not all(isinstance(r, dict) and "item" in r and str(r.get("reason") or "")
                                            for r in rej):
        raise RosterError("the sink's answer has no rejected list of {item, reason}")
    for k in ("facts_stored", "suggestions_stored"):
        if k in ans and (not isinstance(ans[k], int) or isinstance(ans[k], bool) or ans[k] < 0):
            raise RosterError("the sink's answer has a bad %r" % k)
    if expected is not None:
        got = ans["stored"] + ans["skipped"] + len(rej) + ans["deferred"]
        if got != expected:
            raise RosterError("the sink accounted for %d of the %d items sent" % (got, expected))
    return ans


def _rejected_key(r):
    """What makes two quarantine lines the same item: where it was turned away, why, and the item itself (not the time)."""
    return json.dumps([r.get("source"), r.get("reason"), r.get("item")], ensure_ascii=False, sort_keys=True, default=str)


def _append_rejected(path, lines):
    """Append rejected items, skipping any already in the file, so a retried run does not repeat its lines."""
    if not lines:
        return
    seen = set()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    seen.add(_rejected_key(json.loads(line)))
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    with open(path, "a", encoding="utf-8") as f:
        for r in lines:
            k = _rejected_key(r)
            if k in seen:
                continue
            seen.add(k)
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def _engine(cards, per_person, model_cmd, timeout, owners, prompt_path):
    """Run the engine one person at a time on the messages chosen for each; merge the answers."""
    res = {"people_seen": 0, "facts": [], "field_suggestions": [], "rejected": [], "errors": []}
    for i, msgs in per_person.items():
        r = fc.find(cards, msgs, model_cmd, timeout, only=[cards[i].get("id")], owners=owners, prompt_path=prompt_path)
        for k in res:
            res[k] += r[k]
    counts = {}
    for r in res["rejected"]:
        counts[r["why"]] = counts.get(r["why"], 0) + 1
    res["rejected_by_reason"] = counts
    return res


def run_roster(source, sink, state_dir, *, model_cmd, owners=(), prompt_path=None, settle_minutes=10,
               per_person_cap=60, timeout=600, log=print, lock_path=None, now=None):
    """One run. Returns the status dict (also written to STATE/status.json). A source or sink problem that is not
    the sink's own answer (an unreadable file, a bad reference) raises RosterError after the status is written.
    settle_minutes: messages newer than now minus this wait for a later run (0 = none).
    per_person_cap: most messages one person's turn reads in one run; the rest wait (None = no cap).
    now: a datetime, for tests."""
    if per_person_cap is not None and per_person_cap < 1:
        raise RosterError("per_person_cap must be 1 or more (or None for no cap)")
    os.makedirs(state_dir, exist_ok=True)
    path = lambda n: os.path.join(state_dir, n)
    now = now or dt.datetime.now(dt.timezone.utc)
    st = {"ok": False, "state": "failed", "reason": "", "at": now.isoformat(timespec="seconds"), "messages": 0,
          "undated": 0, "people_seen": 0, "facts_stored": None, "suggestions_stored": None, "stored": 0, "skipped": 0,
          "deferred": 0, "held": 0, "waiting_to_settle": 0, "rejected": 0, "rejected_by_reason": {},
          "cursor_before": None, "cursor_after": None, "cursor_moved": False, "errors": [], "notes": [], "error": None}
    try:
        lock = Lock(lock_path or path(LOCK_FILE))
        lock.__enter__()
    except LockHeld as e:
        st.update(state="locked", reason="locked", error=str(e))
        log("roster: %s" % e)
        return st                                   # a run is already going: nothing of ours is changed
    pending_raise = None
    try:
        try:
            _run(st, source, sink, path, model_cmd, owners, prompt_path, settle_minutes, per_person_cap, timeout, now)
        except RosterError as e:
            st.update(ok=False, state="failed", reason=st["reason"] or "error", error=str(e))
            pending_raise = e
        except Exception as e:                      # a sink or source that blew up: the run fails, the cursor stays
            st.update(ok=False, state="failed", reason="error", error="%s: %s" % (type(e).__name__, e))
        wm.write_atomic(path(STATUS_FILE), st)
    finally:
        lock.__exit__(None, None, None)
    if pending_raise:
        raise pending_raise
    return st


def _run(st, source, sink, path, model_cmd, owners, prompt_path, settle_minutes, per_person_cap, timeout, now):
    cursor = read_json(path(CURSOR_FILE), None)
    cursor = cursor if isinstance(cursor, dict) else {}
    last = cursor.get("last_said_at")
    last_ids = cursor.get("last_ids") if isinstance(cursor.get("last_ids"), list) else []
    st["cursor_before"] = last
    raw = source.read(dict(cursor))
    cards = source.cards()
    cutoff = now - dt.timedelta(minutes=settle_minutes)
    new, undated, waiting = [], 0, 0
    for m in raw:
        when = fc._parse_when(m.get("said_at"))
        if when is None:
            undated += 1
        elif when > cutoff:
            waiting += 1                            # inside the settle window: eligible on a later run
        else:
            new.append(m)
    st.update(messages=len(new), undated=undated, waiting_to_settle=waiting)
    if waiting:
        st["notes"].append("%d messages are newer than %d minutes and wait for the next run" % (waiting, settle_minutes))
    if undated:
        st["notes"].append("%d messages had no time and were not read; cursor not moved" % undated)
    if not new:
        st.update(ok=not undated, state="ok" if not undated else "failed", reason="nothing_new" if not undated else "undated")
        return
    if not cards:
        st.update(reason="no_cards", error="no person cards")
        return
    per_person = fc.group_messages(cards, new, owners, cap=None)
    held = []
    if per_person_cap is not None:
        for i, msgs in list(per_person.items()):
            if len(msgs) > per_person_cap:
                held += msgs[per_person_cap:]
                per_person[i] = msgs[:per_person_cap]
                st["notes"].append("%s: %d messages wait for the next run (cap %d per run)" % (
                    cards[i].get("name"), len(msgs) - per_person_cap, per_person_cap))
        st["held"] = len({str(m["id"]) for m in held})
    res = _engine(cards, per_person, model_cmd, timeout, owners, prompt_path)
    # engine rejects are quarantined with their reason, so a night of silent zeros shows why
    stamp = st["at"]
    quarantine = [{"at": stamp, "source": "engine", "reason": r.get("why"), "item": r} for r in res["rejected"]]
    st.update(people_seen=res["people_seen"], rejected=len(res["rejected"]), errors=res["errors"],
              rejected_by_reason=dict(res["rejected_by_reason"]))
    answer = sink.write(res["facts"], res["field_suggestions"])      # an exception here fails the run, cursor held
    try:
        ans = check_answer(answer, len(res["facts"]) + len(res["field_suggestions"]))
    except RosterError as e:
        _append_rejected(path(REJECTED_FILE), quarantine)
        st.update(reason="sink_malformed", error=str(e))     # the answer is unusable: the run fails, the cursor stays
        return
    for r in ans["rejected"]:
        quarantine.append({"at": stamp, "source": "sink", "reason": str(r["reason"]), "item": r["item"]})
        why = str(r["reason"])
        st["rejected_by_reason"][why] = st["rejected_by_reason"].get(why, 0) + 1
    st["rejected"] += len(ans["rejected"])
    _append_rejected(path(REJECTED_FILE), quarantine)
    st.update(stored=ans["stored"], skipped=ans["skipped"], deferred=ans["deferred"],
              facts_stored=ans.get("facts_stored"), suggestions_stored=ans.get("suggestions_stored"))
    if res["errors"]:
        st.update(reason="engine_errors", error="engine failed for %s" % ", ".join(str(e.get("name")) for e in res["errors"]))
        return
    if undated:
        st.update(reason="undated", error="%d messages had no time" % undated)
        return
    if ans["deferred"]:
        st["notes"].append("%d items deferred by the sink; cursor not moved" % ans["deferred"])
        st.update(reason="deferred", error="%d items deferred" % ans["deferred"])
        return
    done = [m for m in new if not held or _key(m) < min(_key(h) for h in held)]
    st["ok"], st["state"], st["reason"] = True, "ok", "ok"
    if not done:
        return
    top = max(fc._when(m) for m in done)
    at_top = [m for m in done if fc._when(m) == top]
    newest = at_top[0]["said_at"]
    ids = {str(m["id"]) for m in at_top}
    if last and fc._parse_when(last) == top:        # the boundary did not move: keep the ids already read
        ids |= {str(i) for i in last_ids}
    newest_msg = max(at_top, key=_key)
    wm.write_atomic(path(CURSOR_FILE), {"last_said_at": newest, "last_id": str(newest_msg["id"]),
                                        "last_ids": sorted(ids), "updated_at": dt.datetime.now(
                                            dt.timezone.utc).isoformat(timespec="seconds")})
    st.update(cursor_after=newest, cursor_moved=True)
