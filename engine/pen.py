# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Hand-written journal storage, independent of chat extraction."""
import datetime as dt
import contextlib
try:
    import fcntl
except ImportError:  # Windows: no fcntl, lock one byte with msvcrt instead
    fcntl = None
    import msvcrt
import threading
import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo


class PenStore:
    """Journal store contract; implementations scope every operation to writer."""
    def append(self, writer, entry): raise NotImplementedError
    def edit(self, writer, id, text): raise NotImplementedError
    def read(self, writer, id): raise NotImplementedError
    def day(self, writer, date): raise NotImplementedError
    def recent(self, writer, n): raise NotImplementedError


def validate_writer(writer):
    if not isinstance(writer, str) or not writer or writer[0] in '.-' or '/' in writer or '\\' in writer or any(ord(c) < 32 for c in writer):
        raise ValueError('invalid writer name')
    return writer


_thread_lock = threading.RLock()


class LocalFilesPenStore(PenStore):
    def __init__(self, root, timezone=None, clock=None):
        self.install_root = Path(root).resolve()
        self.root = self.install_root / 'pen'
        self.timezone = ZoneInfo(timezone) if timezone else None
        self.clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))

    def _directory(self, writer):
        validate_writer(writer)
        path = self.root / writer / 'journal'
        # Never follow a pre-existing symlink out of the pen root.
        if any(p.is_symlink() for p in (self.root, self.root / writer, path)) or not path.resolve().is_relative_to(self.root):
            raise ValueError('journal path escapes pen folder')
        return path

    @contextlib.contextmanager
    def _lock(self, writer):
        with _thread_lock:
            directory = self._directory(writer)
            directory.mkdir(parents=True, exist_ok=True)
            lock = directory / '.lock'
            if lock.is_symlink(): raise ValueError('invalid journal lock')
            fd = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            try:
                if fcntl: fcntl.flock(fd, fcntl.LOCK_EX)
                else: msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                yield
            finally:
                if fcntl: fcntl.flock(fd, fcntl.LOCK_UN)
                else:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                os.close(fd)

    def _parse(self, path):
        if path.is_symlink(): raise ValueError('invalid journal file')
        try:
            with path.open(encoding='utf-8', newline='') as f: lines = f.read().splitlines(keepends=True)
        except FileNotFoundError: return []
        entries = []
        for line in lines:
            if line.startswith('## pen-entry '):
                try: header = json.loads(line[len('## pen-entry '):])
                except ValueError: header = None
                if not isinstance(header, dict) or not all(k in header for k in ('id', 'time', 'writer')):
                    raise ValueError('invalid journal header')
                entries.append([header, ''])
                continue
            if not entries: raise ValueError('invalid journal file')
            if line.startswith('\\'): line = line[1:]
            entries[-1][1] += line
        for item in entries:
            if not item[0].get('_ends_newline', False) and item[1].endswith('\n'):
                item[1] = item[1][:-1]
        return entries

    def _save(self, path, entries):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix='.pen-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
                for header, body in entries:
                    header['_ends_newline'] = body.endswith('\n')
                    f.write('## pen-entry ' + json.dumps(header, ensure_ascii=False) + '\n')
                    for line in body.splitlines(keepends=True):
                        if line.startswith(('\\', '## pen-entry ')): f.write('\\')
                        f.write(line)
                    if body and not body.endswith('\n'): f.write('\n')
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)

    def append(self, writer, entry):
        if not isinstance(entry, dict) or not isinstance(entry.get('body'), str): raise ValueError('body must be text')
        title = entry.get('title')
        if title is not None and not isinstance(title, str): raise ValueError('title must be text')
        now = self.clock().astimezone(self.timezone)
        header = {'id': uuid.uuid4().hex, 'time': now.isoformat(), 'writer': writer}
        if title is not None: header['title'] = title
        path = self._directory(writer) / (now.date().isoformat() + '.md')
        with self._lock(writer):
            entries = self._parse(path)
            if any(h['writer'] != writer for h, _ in entries): raise ValueError('journal contains a different writer')
            entries.append([header, entry['body']])
            self._save(path, entries)
        return header['id']

    def _find(self, writer, id):
        if not isinstance(id, str) or not re.fullmatch(r'[0-9a-f]{32}', id): raise ValueError('invalid id')
        for path in sorted(self._directory(writer).glob('????-??-??.md'), reverse=True):
            entries = self._parse(path)
            for index, (header, body) in enumerate(entries):
                if header['id'] == id and header['writer'] == writer: return path, entries, index
        raise ValueError('entry not found')

    def edit(self, writer, id, text):
        if not isinstance(text, str): raise ValueError('body must be text')
        with self._lock(writer):
            path, entries, index = self._find(writer, id)
            if any(h['writer'] != writer for h, _ in entries): raise ValueError('journal contains a different writer')
            entries[index][1] = text
            self._save(path, entries)

    def read(self, writer, id):
        _, entries, i = self._find(writer, id)
        return dict(entries[i][0], body=entries[i][1])

    def day(self, writer, date):
        if not isinstance(date, str): raise ValueError('date must be YYYY-MM-DD')
        try:
            if dt.date.fromisoformat(date).isoformat() != date: raise ValueError
        except ValueError: raise ValueError('date must be YYYY-MM-DD')
        return [dict(h, body=b) for h, b in self._parse(self._directory(writer) / (date + '.md')) if h['writer'] == writer]

    def recent(self, writer, n):
        if not isinstance(n, int) or isinstance(n, bool) or n < 1: raise ValueError('n must be a positive integer')
        result = []
        for path in self._directory(writer).glob('????-??-??.md'):
            result.extend(self.day(writer, path.stem))
        return sorted(result, key=lambda entry: dt.datetime.fromisoformat(entry['time']), reverse=True)[:n]
