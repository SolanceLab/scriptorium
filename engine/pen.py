# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Hand-written record storage, independent of chat extraction."""
import argparse
import importlib
import datetime as dt
import contextlib
try:
    import fcntl
except ImportError:  # Windows: no fcntl, lock one byte with msvcrt instead
    fcntl = None
    import msvcrt
import threading
import json
import ntpath
import os
import re
import tempfile
import uuid
from collections import Counter
from pathlib import Path, PureWindowsPath
from zoneinfo import ZoneInfo


class PenStore:
    """Hand-written record store contract; implementations scope every operation to writer."""
    def append(self, writer, entry): raise NotImplementedError
    def edit(self, writer, id, text): raise NotImplementedError
    def read(self, writer, id): raise NotImplementedError
    def day(self, writer, date): raise NotImplementedError
    def recent(self, writer, n): raise NotImplementedError
    def core_read(self, writer, version=None): raise NotImplementedError
    def core_write(self, writer, text, based_on): raise NotImplementedError
    def core_history(self, writer): raise NotImplementedError
    def spine_write(self, writer, title, body, pinned=False): raise NotImplementedError
    def spine_edit(self, writer, id, title=None, body=None, pinned=None): raise NotImplementedError
    def spine_delete(self, writer, id): raise NotImplementedError
    def spine_read(self, writer, id=None): raise NotImplementedError
    def marrow_read(self, writer, version=None): raise NotImplementedError
    def marrow_write(self, writer, text, based_on, spine_ids): raise NotImplementedError
    def marrow_history(self, writer): raise NotImplementedError
    def export_pen(self, writer): raise NotImplementedError
    def import_pen(self, writer, records): raise NotImplementedError


def validate_writer(writer):
    if not isinstance(writer, str) or not writer or writer[0] in '.-' or '/' in writer or '\\' in writer or any(ord(c) < 32 for c in writer):
        raise ValueError('invalid writer name')
    reserved = ntpath.isreserved(writer) if hasattr(ntpath, 'isreserved') else PureWindowsPath(writer).is_reserved()
    if reserved or writer.endswith(('.', ' ')) or any(c in writer for c in ':*?"<>|'):
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
        writer_dir = self.root / writer
        path = writer_dir / 'journal'
        # Refuse redirects and check the actual directory spelling on case-insensitive filesystems.
        if any(p.is_symlink() for p in (self.root, writer_dir, path)) or path.resolve() != path:
            raise ValueError('journal path escapes pen folder')
        if writer_dir.exists() and not any(child.name == writer for child in self.root.iterdir()):
            raise ValueError('writer directory belongs to another writer')
        return path

    @contextlib.contextmanager
    def _lock(self, writer):
        with _thread_lock:
            directory = self._directory(writer)
            directory.mkdir(parents=True, exist_ok=True)
            self._directory(writer)  # Recheck after a concurrent writer may have created a directory.
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

    def _version_dir(self, writer, kind):
        self._directory(writer)
        path = self.root / writer / kind
        if path.is_symlink() or path.resolve() != path: raise ValueError('invalid pen path')
        return path

    def core_history(self, writer): return self._history(writer, 'core')

    def _history(self, writer, kind):
        directory = self._version_dir(writer, kind)
        result = []
        paths = (path for path in directory.glob('*.md') if re.fullmatch(r'[0-9]+', path.stem))
        for path in sorted(paths, key=lambda p: int(p.stem)):
            record = self._version_read(writer, kind, int(path.stem))
            result.append({k: record[k] for k in ('number', 'based_on', 'time', 'writer', 'length')})
        return result

    def core_read(self, writer, version=None): return self._version_read(writer, 'core', version)

    def _version_read(self, writer, kind, version=None):
        directory = self._version_dir(writer, kind)
        if version is None:
            numbers = [int(p.stem) for p in directory.glob('[0-9]*.md') if p.stem.isdigit()]
            if not numbers: raise ValueError('version not found')
            version = max(numbers)
        if not isinstance(version, int) or isinstance(version, bool) or version < 1: raise ValueError('invalid version')
        entries = self._parse(directory / f'{version:04d}.md')
        if len(entries) != 1 or entries[0][0].get('writer') != writer or entries[0][0].get('number') != version: raise ValueError('version not found')
        header, body = entries[0]
        return dict(header, text=body, based_on=header.get('based_on', version - 1))

    def core_write(self, writer, text, based_on): return self._version_write(writer, 'core', text, based_on)

    def _version_write(self, writer, kind, text, based_on, spine_ids=None):
        if not isinstance(text, str): raise ValueError('text must be text')
        if not isinstance(based_on, int) or isinstance(based_on, bool) or based_on < 0: raise ValueError('invalid based_on')
        with self._lock(writer):
            history = self._history(writer, kind)
            latest = history[-1]['number'] if history else 0
            if based_on != latest: raise ValueError('version conflict')
            number = latest + 1
            header = {'id': uuid.uuid4().hex, 'number': number, 'based_on': based_on, 'time': self.clock().astimezone(self.timezone).isoformat(), 'writer': writer, 'length': len(text)}
            if spine_ids is not None: header['spine_ids'] = spine_ids
            self._save(self._version_dir(writer, kind) / f'{number:04d}.md', [[header, text]])
            return number

    def marrow_read(self, writer, version=None): return self._version_read(writer, 'marrow', version)
    def marrow_history(self, writer): return self._history(writer, 'marrow')
    def marrow_write(self, writer, text, based_on, spine_ids):
        if not isinstance(spine_ids, list) or any(not isinstance(i, str) or not re.fullmatch(r'[0-9a-f]{32}', i) for i in spine_ids): raise ValueError('invalid spine ids')
        return self._version_write(writer, 'marrow', text, based_on, spine_ids)

    def _spine_path(self, writer, id, deleted=False):
        if not isinstance(id, str) or not re.fullmatch(r'[0-9a-f]{32}', id): raise ValueError('invalid id')
        directory = self._version_dir(writer, 'spine')
        if deleted:
            directory = directory / '.deleted'
            if directory.is_symlink() or directory.resolve() != directory: raise ValueError('invalid deleted path')
        return directory / (id + '.md')

    def spine_read(self, writer, id=None):
        if id is None:
            records = [self.spine_read(writer, p.stem) for p in self._version_dir(writer, 'spine').glob('*.md')]
            return sorted(records, key=lambda r: (r['pinned'], r['created']), reverse=True)
        entries = self._parse(self._spine_path(writer, id))
        if len(entries) != 1 or entries[0][0].get('writer') != writer or entries[0][0].get('id') != id: raise ValueError('spine entry not found')
        return dict(entries[0][0], body=entries[0][1])

    def spine_write(self, writer, title, body, pinned=False):
        if not isinstance(title, str) or not isinstance(body, str) or not isinstance(pinned, bool): raise ValueError('invalid spine fields')
        now = self.clock().astimezone(self.timezone).isoformat()
        header = {'id': uuid.uuid4().hex, 'created': now, 'edited': now, 'time': now, 'pinned': pinned, 'writer': writer, 'title': title}
        with self._lock(writer): self._save(self._spine_path(writer, header['id']), [[header, body]])
        return header['id']

    def spine_edit(self, writer, id, title=None, body=None, pinned=None):
        if id is None: raise ValueError('invalid id')
        if title is not None and not isinstance(title, str) or body is not None and not isinstance(body, str) or pinned is not None and not isinstance(pinned, bool): raise ValueError('invalid spine fields')
        with self._lock(writer):
            record = self.spine_read(writer, id)
            old_body = record.pop('body')
            if title is not None: record['title'] = title
            if pinned is not None: record['pinned'] = pinned
            record['edited'] = self.clock().astimezone(self.timezone).isoformat()
            self._save(self._spine_path(writer, id), [[record, old_body if body is None else body]])

    def spine_delete(self, writer, id):
        with self._lock(writer):
            source = self._spine_path(writer, id)
            self.spine_read(writer, id)
            target = self._spine_path(writer, id, True)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink(): raise ValueError('deleted entry already exists')
            os.replace(source, target)

    def export_pen(self, writer):
        """Return all records, preserving headers and deleted entries."""
        with self._lock(writer):
            return self._export_locked(writer)

    def _export_locked(self, writer, occupied=None):
        result = []
        for kind in ('journal', 'core', 'spine', 'marrow'):
            directory = self._directory(writer) if kind == 'journal' else self._version_dir(writer, kind)
            paths = directory.glob('*.md')
            if kind in ('core', 'marrow'):
                paths = sorted(paths, key=lambda path: (len(path.stem), path.name))
            else:
                paths = sorted(paths)
            for path in paths:
                if occupied is not None: occupied.add(path)
                for header, body in self._parse(path):
                    if header['writer'] != writer: raise ValueError('record contains a different writer')
                    result.append({'kind': kind, 'file': path.name, 'deleted': False, 'header': header, 'body': body})
            if kind == 'spine':
                deleted = directory / '.deleted'
                if deleted.is_symlink() or deleted.resolve() != deleted: raise ValueError('invalid deleted path')
                for path in sorted(deleted.glob('*.md')):
                    if occupied is not None: occupied.add(path)
                    for header, body in self._parse(path):
                        if header['writer'] != writer: raise ValueError('record contains a different writer')
                        result.append({'kind': kind, 'file': path.name, 'deleted': True, 'header': header, 'body': body})
        return result

    def import_pen(self, writer, records):
        """Copy missing files, accepting a previously copied identical subset."""
        with self._lock(writer):
            grouped = self._group_records(writer, records)
            occupied = set()
            existing = self._group_records(writer, self._export_locked(writer, occupied))
            for path in sorted(occupied):
                if path not in existing or grouped.get(path) != existing[path]:
                    raise ValueError(f'destination conflicts at {path.name}')
            for path, entries in grouped.items():
                if path not in existing:
                    self._save(path, entries)

    def _group_records(self, writer, records):
        grouped = {}
        seen = set()
        for record in records:
            kind, filename, deleted = record['kind'], record['file'], record['deleted']
            if kind not in ('journal', 'core', 'spine', 'marrow') or not isinstance(deleted, bool) or deleted and kind != 'spine': raise ValueError('invalid record')
            header, body = record['header'], record['body']
            if not isinstance(header, dict) or header.get('writer') != writer or not isinstance(body, str):
                raise ValueError('invalid record')
            id = header.get('id')
            if not isinstance(id, str) or not re.fullmatch(r'[0-9a-f]{32}', id) or not isinstance(header.get('time'), str):
                raise ValueError('invalid record header')
            identity = (kind, id)
            if kind == 'journal':
                if not isinstance(filename, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}\.md', filename):
                    raise ValueError('invalid journal filename')
                dt.date.fromisoformat(filename[:-3])
                directory = self._directory(writer)
            else:
                directory = self._version_dir(writer, kind)
                if kind == 'spine':
                    expected = id + '.md'
                    if deleted:
                        directory = self._spine_path(writer, id, deleted=True).parent
                else:
                    number = header.get('number')
                    if type(number) is not int or number < 1:
                        raise ValueError('invalid version number')
                    expected = f'{number:04d}.md'
                    identity = (kind, number)
                if filename != expected:
                    raise ValueError('record does not match its filename')
            path = directory / filename
            if identity in seen:
                raise ValueError('duplicate record')
            seen.add(identity)
            header = dict(header, _ends_newline=body.endswith('\n'))
            grouped.setdefault(path, []).append([header, body])
        return grouped


def load_store(ref):
    if ref.startswith('local:'): return LocalFilesPenStore(ref[6:])
    module, sep, attr = ref.partition(':')
    if not sep or not module or not attr: raise ValueError('store must be local:ROOT or module:attr')
    store = getattr(importlib.import_module(module), attr)
    return store() if isinstance(store, type) or (callable(store) and not hasattr(store, 'export_pen')) else store


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    transfer = sub.add_parser('transfer')
    transfer.add_argument('--from', dest='source', required=True)
    transfer.add_argument('--to', dest='destination', required=True)
    transfer.add_argument('--writer', required=True)
    args = parser.parse_args(argv)
    try:
        validate_writer(args.writer)
        records = load_store(args.source).export_pen(args.writer)
        destination = load_store(args.destination)
    except Exception as e:
        parser.exit(1, f'Transfer failed before copying: {e}\n')
    try:
        destination.import_pen(args.writer, records)
    except Exception as e:
        try:
            present = destination.export_pen(args.writer)
            counts = Counter(record['kind'] for record in present if record in records)
            summary = ', '.join(f'{kind}: {counts[kind]}' for kind in ('journal', 'core', 'spine', 'marrow'))
            progress = f'Source records confirmed at destination (including prior runs): {summary}.'
        except Exception:
            progress = 'Could not verify copied records at the destination.'
        parser.exit(1, f'Transfer failed: {e}\n{progress}\n')


if __name__ == '__main__': main()
