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
import math
import ntpath
import os
import re
import tempfile
import uuid
import shutil
import unicodedata
from pathlib import Path, PureWindowsPath
from zoneinfo import ZoneInfo


PEN_CONTRACT_VERSION = 3    # 3 adds `fields` (customisable entry fields) to write and edit
MAX_FIELD_DEPTH = 32
FIELD_TYPES = ('text', 'list', 'number', 'boolean', 'object')


def validate_surface(surface):
    if not isinstance(surface, str) or not surface.strip():
        raise ValueError('invalid surface')
    surface.encode('utf-8')


def stamp_surface(record, surface):
    if surface is None:
        record.pop('surface', None)
    else:
        validate_surface(surface)
        record['surface'] = surface


def field_spec(spec):
    """One declared field, "<type>" or {"type": "<type>", "required": true}, as {"type", "required"}."""
    if isinstance(spec, str):
        spec = {'type': spec}
    if (not isinstance(spec, dict) or not spec.keys() <= {'type', 'required'}
            or spec.get('type') not in FIELD_TYPES or type(spec.get('required', False)) is not bool):
        raise ValueError('invalid field declaration')
    return {'type': spec['type'], 'required': spec.get('required', False)}


def _json_safe(value, depth=0):
    if depth > MAX_FIELD_DEPTH:
        raise ValueError('too deeply nested')
    if value is None or type(value) is bool or type(value) is int:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError('not a finite number')
        return
    if isinstance(value, str):
        value.encode('utf-8')
    elif isinstance(value, list):
        for item in value:
            _json_safe(item, depth + 1)
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError('object keys must be text')
            key.encode('utf-8')
            _json_safe(item, depth + 1)
    else:
        raise ValueError('not JSON')


def _value_fits(kind, value):
    try:
        if kind == 'text':
            return isinstance(value, str) and value.encode('utf-8') is not None
        if kind == 'list':
            return isinstance(value, list) and all(isinstance(i, str) and i.encode('utf-8') is not None for i in value)
        if kind == 'number':
            return type(value) in (int, float) and math.isfinite(value)
        if kind == 'boolean':
            return type(value) is bool
        _json_safe(value, 1)    # a field value sits one level down, as stored_fields counts it
        return isinstance(value, dict)
    except (ValueError, UnicodeError):
        return False


def check_fields(declared, given, creating):
    """Check the `fields` of a write (creating) or an edit against a journal's declaration (field -> field_spec).

    A null in an edit clears an optional field; a required field is never cleared and must be present on a write."""
    if not isinstance(given, dict):
        raise ValueError('fields must be an object')
    allowed = ', '.join(sorted(declared)) or 'none: this journal declares no fields'
    for name, value in given.items():
        if name not in declared:
            raise ValueError(f'field {name!r} is not declared for this journal; allowed fields: {allowed}')
        spec = declared[name]
        if value is None:
            if creating:
                raise ValueError(f'field {name!r} needs a value; null only clears a field when editing')
            if spec['required']:
                raise ValueError(f'field {name!r} is required and cannot be cleared')
        elif not _value_fits(spec['type'], value):
            raise ValueError(f'field {name!r} must be {spec["type"]}')
    if creating:
        missing = sorted(n for n, spec in declared.items() if spec['required'] and n not in given)
        if missing:
            raise ValueError('missing required fields: ' + ', '.join(missing))


def stored_fields(fields):
    """The rule for the `fields` a stored entry carries: a non-empty object of JSON-safe values (never null)."""
    if (not isinstance(fields, dict) or not fields
            or any(not isinstance(k, str) or not k or v is None for k, v in fields.items())):
        raise ValueError('invalid entry fields')
    try:
        _json_safe(fields)
        json.dumps(fields, allow_nan=False)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError('invalid entry fields') from None


def merged_fields(current, incoming):
    """The fields after an edit: incoming values replace or add, a null removes. {} or None when nothing is left."""
    merged = dict(current or {})
    for name, value in incoming.items():
        if value is None:
            merged.pop(name, None)
        else:
            merged[name] = value
    return merged


def snapshot_parts(snapshot):
    if not isinstance(snapshot, dict) or snapshot.keys() != {'version', 'collections', 'entries'}:
        raise ValueError('invalid transfer snapshot')
    metadata = snapshot['collections']
    entries = snapshot['entries']
    if (type(snapshot['version']) is not int or snapshot['version'] != 1
            or not isinstance(entries, list) or not isinstance(metadata, dict)
            or any(not isinstance(entry, dict) or not isinstance(entry.get('collection'), str) for entry in entries)
            or any(value not in ('pool', 'silo') for value in metadata.values())
            or not {entry['collection'] for entry in entries} <= metadata.keys()):
        raise ValueError('invalid collection metadata')
    return entries, metadata


class RequestIdConflict(Exception):
    """Raised by a custom store's mutate() when an X-Request-Id is reused with a different request."""


class PenStore:
    """Hand-written record store contract; implementations scope every operation to writer."""
    keeps_history = True

    def collections(self, writer, *, visible=None): raise NotImplementedError
    def write(self, writer, collection, body, title=None, pinned=False, by_day=None, *, surface=None, fields=None): raise NotImplementedError
    def edit(self, writer, id, based_on, body=None, title=None, pinned=None, *, surface=None, fields=None): raise NotImplementedError
    def read(self, writer, id, version=None, *, visible=None): raise NotImplementedError
    def list(self, writer, collection, pinned_only=False, day=None, limit=20, *, surface=None, visible=None): raise NotImplementedError
    def history(self, writer, id, *, visible=None): raise NotImplementedError
    def delete(self, writer, id): raise NotImplementedError
    def export_pen(self, writer): raise NotImplementedError
    def import_pen(self, writer, records, *, metadata=None): raise NotImplementedError
    def export_snapshot(self, writer): raise NotImplementedError

    def import_snapshot(self, writer, snapshot):
        entries, metadata = snapshot_parts(snapshot)
        self.import_pen(writer, entries, metadata=metadata)


def validate_writer(writer):
    if not isinstance(writer, str) or not writer or writer[0] in '.-' or '/' in writer or '\\' in writer or any(ord(c) < 32 for c in writer):
        raise ValueError('invalid writer name')
    reserved = ntpath.isreserved(writer) if hasattr(ntpath, 'isreserved') else PureWindowsPath(writer).is_reserved()
    if reserved or writer.endswith(('.', ' ')) or any(c in writer for c in ':*?"<>|'):
        raise ValueError('invalid writer name')
    return writer


_thread_lock = threading.RLock()


class LocalFilesPenStore(PenStore):
    contract_version = PEN_CONTRACT_VERSION

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
        if 'surface' in header:
            validate_surface(header['surface'])
        if 'fields' in header:
            stored_fields(header['fields'])
        for field in ('created', 'edited'):
            value = header.get(field)
            if not isinstance(value, str) or dt.datetime.fromisoformat(value).utcoffset() is None:
                raise ValueError('invalid entry timestamp')

    def _find(self, writer, id, visible=None):
        if not self._valid_id(id):
            raise ValueError('invalid id' if visible is None else 'entry not found')
        for directory in self._collections(writer):
            if visible is not None and not visible(directory.name, self._default(directory)):
                continue
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

    def _default(self, directory):
        marker = self._checked(directory / '.pool')
        return 'pool' if marker.is_file() else 'silo'

    def _exists(self, directory):
        if not directory.is_dir():
            return False
        archive = self._checked(directory / '.deleted')
        return bool(self._entries(directory) or (archive.is_dir() and self._entries(archive)))

    def collections(self, writer, *, visible=None):
        with self._lock(writer):
            result = []
            for directory in self._collections(writer):
                if visible is not None and not visible(directory.name, self._default(directory)):
                    continue
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

    def write(self, writer, collection, body, title=None, pinned=False, by_day=None, *, surface=None, fields=None):
        self._fields(body, title, pinned)
        if fields is not None:
            stored_fields(fields)
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
            stamp_surface(record, surface)
            if fields:
                record['fields'] = fields
            if mode is None and not directory.exists():
                staging = Path(tempfile.mkdtemp(prefix='.pen-', dir=directory.parent))
                try:
                    (staging / '.pool').touch()
                    self._install_entry(staging / id, [record])
                    os.replace(staging, directory)
                finally:
                    if staging.exists():
                        shutil.rmtree(staging)
            else:
                if mode is None and not self._checked(directory / '.silo').exists():
                    self._checked(directory / '.pool').touch()
                self._install_entry(directory / id, [record])
            return id

    def edit(self, writer, id, based_on, body=None, title=None, pinned=None, *, surface=None, fields=None):
        self._fields('' if body is None else body, title, False if pinned is None else pinned)
        if fields is not None and not isinstance(fields, dict):
            raise ValueError('invalid entry fields')
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
            stamp_surface(record, surface)
            if fields:
                self._merge_fields(record, fields)
            self._save(path / f'{latest + 1}.md', record)
            return latest + 1

    @staticmethod
    def _merge_fields(record, fields):
        merged = merged_fields(record.get('fields'), fields)
        if merged:
            stored_fields(merged)
            record['fields'] = merged
        else:
            record.pop('fields', None)

    def read(self, writer, id, version=None, *, visible=None):
        invalid = version is not None and (type(version) is not int or version < 1)
        if invalid and visible is None:
            raise ValueError('invalid version')
        with self._lock(writer):
            path = self._find(writer, id, visible)
            if invalid:
                raise ValueError('invalid version')
            latest = self._versions(path)[-1]
            if version is not None and version > latest:
                raise ValueError('version not found')
            return self._record(writer, path.parent.name, path, version or latest)

    def list(self, writer, collection, pinned_only=False, day=None, limit=20, *, surface=None, visible=None):
        if type(pinned_only) is not bool or type(limit) is not int or limit < 1:
            raise ValueError('invalid list filters')
        if day is not None and (not isinstance(day, str) or dt.date.fromisoformat(day).isoformat() != day):
            raise ValueError('day must be YYYY-MM-DD')
        with self._lock(writer):
            try:
                directory = self._collection(writer, collection)
            except ValueError:
                if visible is None:
                    raise
                raise ValueError('collection not found') from None
            if visible is not None and (not visible(collection, self._default(directory))
                                        or not self._exists(directory)):
                raise ValueError('collection not found')
            entries, by_day = self._collection_records(writer, directory)
            if day is not None and by_day is False:
                raise ValueError('day filter requires a by_day collection')
            matches = (r for r in entries if (not pinned_only or r['pinned'])
                       and (day is None or r['created'][:10] == day)
                       and (surface is None or r.get('surface') == surface))
            return sorted(matches, key=lambda r: dt.datetime.fromisoformat(r['created']), reverse=True)[:limit]

    def history(self, writer, id, *, visible=None):
        with self._lock(writer):
            path = self._find(writer, id, visible)
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

    def _metadata(self, writer):
        return {directory.name: self._default(directory) for directory in self._collections(writer)
                if self._exists(directory) or self._checked(directory / '.silo').is_file()
                or self._checked(directory / '.pool').is_file()}

    def export_snapshot(self, writer):
        with self._lock(writer):
            return {'version': 1, 'collections': self._metadata(writer),
                    'entries': self._export_locked(writer)}

    def import_pen(self, writer, records, *, metadata=None):
        """Install missing entries after validating the snapshot and existing subset."""
        if metadata is not None:
            snapshot_parts({'version': 1, 'collections': metadata, 'entries': records})
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
            if metadata is not None:
                current = self._metadata(writer)
                if any(metadata.get(name) != state for name, state in current.items()):
                    raise ValueError('destination conflicts in collection metadata')
                directories = {}
                for name in metadata:
                    directory = self._collection(writer, name)
                    spelling = self._name_key(name)
                    if spellings.setdefault(spelling, name) != name:
                        raise ValueError('collection names collide')
                    directories[name] = directory
                for name, directory in directories.items():
                    if name not in current:
                        directory.mkdir(exist_ok=True)
                        self._checked(directory / ('.' + metadata[name])).touch()
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
        source = load_store(args.source)
        if getattr(source, 'contract_version', None) in (2, PEN_CONTRACT_VERSION):
            snapshot = source.export_snapshot(args.writer)
        else:
            records = source.export_pen(args.writer)
            snapshot = {'version': 1, 'entries': records,
                        'collections': {record['collection']: 'silo' for record in records}}
        records = snapshot['entries']
        destination = load_store(args.destination)
        if getattr(destination, 'contract_version', None) != PEN_CONTRACT_VERSION:
            raise ValueError(f'destination must declare contract_version = {PEN_CONTRACT_VERSION} to preserve collection metadata')
    except Exception as e:
        parser.exit(1, f'Transfer failed before copying: {e}\n')
    try:
        destination.import_snapshot(args.writer, snapshot)
        # Entry ordering can differ between stores. Version ordering is part of each record.
        def by_id(record):
            return record['id']

        copied = destination.export_snapshot(args.writer)
        if dict(copied, entries=sorted(copied['entries'], key=by_id)) != dict(snapshot, entries=sorted(records, key=by_id)):
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
