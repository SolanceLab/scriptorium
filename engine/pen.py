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
import shutil
import unicodedata
from pathlib import Path, PureWindowsPath
from zoneinfo import ZoneInfo


class PenStore:
    """Hand-written record store contract; implementations scope every operation to writer."""
    def collections(self, writer): raise NotImplementedError
    def write(self, writer, collection, body, title=None, pinned=False, by_day=None): raise NotImplementedError
    def edit(self, writer, id, based_on, body=None, title=None, pinned=None): raise NotImplementedError
    def read(self, writer, id, version=None): raise NotImplementedError
    def list(self, writer, collection, pinned_only=False, day=None, limit=20): raise NotImplementedError
    def history(self, writer, id): raise NotImplementedError
    def delete(self, writer, id): raise NotImplementedError
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

    @staticmethod
    def _checked(path):
        if path.is_symlink() or path.resolve() != path:
            raise ValueError('invalid pen path')
        if path.exists() and not any(child.name == path.name for child in path.parent.iterdir()):
            raise ValueError('pen path spelling does not match its directory entry')
        return path

    def _directory(self, writer):
        validate_writer(writer)
        return self._checked(self._checked(self.root) / writer)

    @staticmethod
    def _name_key(name):
        return unicodedata.normalize('NFD', name).casefold()

    def _collection(self, writer, collection):
        try:
            validate_writer(collection)
        except ValueError:
            raise ValueError('invalid collection name') from None
        directory = self._directory(writer)
        key = self._name_key(collection)
        if any(p.name != collection and self._name_key(p.name) == key for p in directory.iterdir()):
            raise ValueError('collection names collide')
        return self._checked(directory / collection)

    @contextlib.contextmanager
    def _lock(self, writer):
        with _thread_lock:
            directory = self._directory(writer)
            directory.mkdir(parents=True, exist_ok=True)
            self._directory(writer)  # Recheck after a concurrent writer may have created a directory.
            lock = directory / '.lock'
            if lock.is_symlink(): raise ValueError('invalid pen lock')
            fd = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            try:
                if fcntl:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                else:
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    if fcntl:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    else:
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            finally:
                os.close(fd)

    def _save(self, path, record):
        header = dict(record)
        body = header.pop('body')
        fd, tmp = tempfile.mkstemp(prefix='.pen-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
                f.write('## pen-entry ' + json.dumps(header, ensure_ascii=False) + '\n')
                f.write(body)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _now(self):
        return self.clock().astimezone(self.timezone).isoformat()

    @staticmethod
    def _valid_id(id):
        return isinstance(id, str) and re.fullmatch(r'[0-9a-f]{32}', id) is not None

    @staticmethod
    def _fields(body, title, pinned):
        if not isinstance(body, str) or title is not None and not isinstance(title, str) or type(pinned) is not bool:
            raise ValueError('invalid entry fields')

    def _collections(self, writer):
        return sorted((p for p in self._directory(writer).iterdir()
                       if not p.name.startswith('.') and p.is_dir()), key=lambda p: p.name)

    def _entries(self, directory):
        return sorted(self._checked(p) for p in directory.iterdir() if self._valid_id(p.name))

    def _versions(self, path):
        numbers = sorted(int(p.stem) for p in path.glob('*.md')
                         if re.fullmatch(r'[1-9][0-9]*', p.stem))
        if not numbers or any(number != index for index, number in enumerate(numbers, 1)):
            raise ValueError('invalid version history')
        return numbers

    def _record(self, writer, collection, path, version):
        file = self._checked(path / f'{version}.md')
        try:
            with file.open(encoding='utf-8', newline='') as f:
                line = f.readline()
                body = f.read()
        except FileNotFoundError:
            raise ValueError('version not found') from None
        if not line.startswith('## pen-entry ') or not line.endswith('\n'):
            raise ValueError('invalid pen header')
        header = json.loads(line[len('## pen-entry '):])
        self._validate_record(writer, collection, path.name, version, header, body)
        return dict(header, body=body)

    def _validate_record(self, writer, collection, id, version, header, body):
        if (not isinstance(header, dict) or 'body' in header or header.get('writer') != writer
                or header.get('collection') != collection
                or header.get('id') != id or type(header.get('version')) is not int
                or header['version'] != version or type(header.get('by_day')) is not bool):
            raise ValueError('invalid entry header')
        self._fields(body, header.get('title'), header.get('pinned'))
        for field in ('created', 'edited'):
            value = header.get(field)
            if not isinstance(value, str) or dt.datetime.fromisoformat(value).utcoffset() is None:
                raise ValueError('invalid entry timestamp')

    def _find(self, writer, id):
        if not self._valid_id(id):
            raise ValueError('invalid id')
        for directory in self._collections(writer):
            path = self._checked(self._collection(writer, directory.name) / id)
            if path.exists():
                return path
        raise ValueError('entry not found')

    def _collection_records(self, writer, directory):
        active = self._entries(directory) if directory.exists() else []
        archive = self._checked(directory / '.deleted')
        deleted = self._entries(archive) if archive.exists() else []
        records = [self._record(writer, directory.name, p, self._versions(p)[-1])
                   for p in active + deleted]
        if len({r['by_day'] for r in records}) > 1:
            raise ValueError('inconsistent collection mode')
        return records[:len(active)], records[0]['by_day'] if records else None

    def collections(self, writer):
        with self._lock(writer):
            result = []
            for directory in self._collections(writer):
                entries, by_day = self._collection_records(writer, self._collection(writer, directory.name))
                if by_day is not None:
                    result.append({'collection': directory.name, 'count': len(entries), 'by_day': by_day})
            return result

    def _install_entry(self, path, versions):
        # Publish an entire entry (including any imported history) with one rename.
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() or path.is_symlink():
            raise ValueError('entry already exists')
        staging = Path(tempfile.mkdtemp(prefix='.pen-', dir=path.parent))
        try:
            for version, record in enumerate(versions, 1):
                self._save(staging / f'{version}.md', record)
            os.replace(staging, path)
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    def write(self, writer, collection, body, title=None, pinned=False, by_day=None):
        self._fields(body, title, pinned)
        if by_day is not None and type(by_day) is not bool:
            raise ValueError('by_day must be a boolean')
        with self._lock(writer):
            directory = self._collection(writer, collection)
            _, mode = self._collection_records(writer, directory)
            if mode is not None and by_day is not None and mode != by_day:
                raise ValueError('collection mode is already set')
            id = uuid.uuid4().hex
            now = self._now()
            record = {'id': id, 'collection': collection, 'writer': writer, 'body': body,
                      'created': now, 'edited': now, 'pinned': pinned, 'version': 1,
                      'by_day': mode if mode is not None else bool(by_day)}
            if title is not None:
                record['title'] = title
            self._install_entry(directory / id, [record])
            return id

    def edit(self, writer, id, based_on, body=None, title=None, pinned=None):
        self._fields('' if body is None else body, title, False if pinned is None else pinned)
        if type(based_on) is not int or based_on < 1:
            raise ValueError('invalid based_on')
        with self._lock(writer):
            path = self._find(writer, id)
            latest = self._versions(path)[-1]
            if based_on != latest:
                raise ValueError('version conflict')
            record = self._record(writer, path.parent.name, path, latest)
            if body is not None:
                record['body'] = body
            if title is not None:
                record['title'] = title
            if pinned is not None:
                record['pinned'] = pinned
            record.update(version=latest + 1, edited=self._now())
            self._save(path / f'{latest + 1}.md', record)
            return latest + 1

    def read(self, writer, id, version=None):
        if version is not None and (type(version) is not int or version < 1):
            raise ValueError('invalid version')
        with self._lock(writer):
            path = self._find(writer, id)
            latest = self._versions(path)[-1]
            if version is not None and version > latest:
                raise ValueError('version not found')
            return self._record(writer, path.parent.name, path, version or latest)

    def list(self, writer, collection, pinned_only=False, day=None, limit=20):
        if type(pinned_only) is not bool or type(limit) is not int or limit < 1:
            raise ValueError('invalid list filters')
        if day is not None and (not isinstance(day, str) or dt.date.fromisoformat(day).isoformat() != day):
            raise ValueError('day must be YYYY-MM-DD')
        with self._lock(writer):
            entries, by_day = self._collection_records(writer, self._collection(writer, collection))
            if day is not None and by_day is False:
                raise ValueError('day filter requires a by_day collection')
            matches = (r for r in entries if (not pinned_only or r['pinned'])
                       and (day is None or r['created'][:10] == day))
            return sorted(matches, key=lambda r: dt.datetime.fromisoformat(r['created']), reverse=True)[:limit]

    def history(self, writer, id):
        with self._lock(writer):
            path = self._find(writer, id)
            return [self._record(writer, path.parent.name, path, v) for v in self._versions(path)]

    def delete(self, writer, id):
        with self._lock(writer):
            path = self._find(writer, id)
            self._record(writer, path.parent.name, path, self._versions(path)[-1])
            archive = self._checked(path.parent / '.deleted')
            archive.mkdir(exist_ok=True)
            target = self._checked(archive / id)
            if target.exists():
                raise ValueError('deleted entry already exists')
            os.replace(path, target)

    def export_pen(self, writer):
        with self._lock(writer):
            return self._export_locked(writer)

    def _export_locked(self, writer):
        result = []
        for directory in self._collections(writer):
            directory = self._collection(writer, directory.name)
            for deleted, location in [(False, directory), (True, self._checked(directory / '.deleted'))]:
                if not location.exists():
                    continue
                for path in self._entries(location):
                    versions = [self._record(writer, directory.name, path, v) for v in self._versions(path)]
                    result.append({'collection': directory.name, 'id': path.name,
                                   'deleted': deleted, 'versions': versions})
        return result

    def import_pen(self, writer, records):
        """Install missing entries after validating the snapshot and existing subset."""
        with self._lock(writer):
            incoming = {}
            paths = {}
            modes = {}
            spellings = {}
            for record in records:
                if not isinstance(record, dict) or record.keys() != {'collection', 'id', 'deleted', 'versions'}:
                    raise ValueError('invalid transfer entry')
                collection, id, deleted, versions = (record[k] for k in ('collection', 'id', 'deleted', 'versions'))
                directory = self._collection(writer, collection)
                spelling = self._name_key(collection)
                if spellings.setdefault(spelling, collection) != collection:
                    raise ValueError('collection names collide')
                if (not self._valid_id(id) or id in incoming or type(deleted) is not bool
                        or not isinstance(versions, list) or not versions):
                    raise ValueError('invalid or duplicate entry')
                for number, version in enumerate(versions, 1):
                    if not isinstance(version, dict):
                        raise ValueError('invalid entry version')
                    header = {k: v for k, v in version.items() if k != 'body'}
                    body = version.get('body')
                    self._validate_record(writer, collection, id, number, header, body)
                    json.dumps(header, ensure_ascii=False, allow_nan=False).encode('utf-8')
                    body.encode('utf-8')
                    if modes.setdefault(collection, version['by_day']) != version['by_day']:
                        raise ValueError('inconsistent collection mode')
                    if version['created'] != versions[0]['created']:
                        raise ValueError('inconsistent entry creation time')
                if deleted:
                    directory = self._checked(directory / '.deleted')
                paths[id] = self._checked(directory / id)
                incoming[id] = record
            existing = {}
            for record in self._export_locked(writer):
                id = record['id']
                if id in existing or incoming.get(id) != record:
                    raise ValueError(f'destination conflicts at {id}')
                existing[id] = record
            for id, record in incoming.items():
                if id not in existing:
                    self._install_entry(paths[id], record['versions'])


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
        # Entry ordering can differ between stores. Version ordering is part of each record.
        def by_id(record):
            return record['id']

        if sorted(destination.export_pen(args.writer), key=by_id) != sorted(records, key=by_id):
            raise ValueError('destination does not match the source snapshot')
    except Exception as e:
        try:
            present = destination.export_pen(args.writer)
            source_by_id = {record['id']: record for record in records}
            matched = sum(source_by_id.get(record['id']) == record for record in present)
            progress = f'Source entries confirmed at destination (including prior runs): {matched}/{len(records)}.'
        except Exception:
            progress = 'Could not verify copied records at the destination.'
        parser.exit(1, f'Transfer failed: {e}\n{progress}\n')


if __name__ == '__main__': main()
