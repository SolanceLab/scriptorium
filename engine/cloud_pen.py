# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The pen kept in a SQL database: the same store contract as LocalFilesPenStore, for a cloud account.

SqlPenStore talks to storage through one tiny adapter, `run(query, params) -> list of dicts`.
Two adapters live here: Sqlite3Sql (Python's own sqlite3, used by the tests) and DurableObjectSql
(Cloudflare's Durable Object SQLite). One row holds one version of one entry. Standard library only.
"""
import datetime as dt
import json
import unicodedata
import uuid
from zoneinfo import ZoneInfo

from engine.pen import LocalFilesPenStore, PenStore, validate_writer

_SCHEMA = [
    "CREATE TABLE IF NOT EXISTS pen_versions ("
    "writer TEXT NOT NULL, collection TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL, "
    "deleted INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL, created_us INTEGER NOT NULL, by_day INTEGER NOT NULL, "
    "pinned INTEGER NOT NULL, record TEXT NOT NULL, PRIMARY KEY (writer, id, version))",
    "CREATE INDEX IF NOT EXISTS pen_versions_collection ON pen_versions (writer, collection, deleted)",
]
_COLUMNS = "writer, collection, id, version, deleted, created, created_us, by_day, pinned, record"


_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)


def _microseconds(created):
    """An ISO time with a UTC offset as whole microseconds since the UTC epoch (exact, whatever the offset)."""
    return (dt.datetime.fromisoformat(created) - _EPOCH) // dt.timedelta(microseconds=1)


class Sqlite3Sql:
    """Adapter over a sqlite3 connection with explicit transactions for grouped writes."""
    def __init__(self, connection):
        self.connection = connection
        self.open = False    # inside transaction(): statements wait for one commit

    def transaction(self, work):
        """Run work() so that every statement in it is kept together, or none is (an error rolls them all back)."""
        if self.open:
            return work()
        self.connection.execute('BEGIN')
        self.open = True
        try:
            result = work()
            self.connection.commit()
            return result
        except BaseException:
            self.connection.rollback()
            raise
        finally:
            self.open = False

    def run(self, query, params=()):
        import sqlite3
        try:
            cursor = self.connection.execute(query, tuple(int(p) if isinstance(p, bool) else p for p in params))
            names = [d[0] for d in cursor.description] if cursor.description else []
            rows = [dict(zip(names, row)) for row in cursor.fetchall()]
            if not self.open:
                self.connection.commit()
            return rows
        except sqlite3.Error as e:
            if not self.open:    # inside transaction() the caller's rollback undoes everything
                try:
                    self.connection.rollback()
                except sqlite3.Error:
                    pass
            raise OSError('storage failed: ' + str(e)) from e


class DurableObjectSql:
    """Adapter over a Durable Object's `ctx.storage.sql` (the Python Workers runtime)."""
    def __init__(self, sql, storage):
        self.sql, self.storage = sql, storage

    def transaction(self, work):
        """Run work() all-or-nothing through ctx.storage.transactionSync (an error inside rolls every statement back)."""
        from pyodide.ffi import create_proxy    # the Python Workers runtime; not importable in plain Python
        result = None

        def run():
            nonlocal result
            result = work()

        proxy = create_proxy(run)
        try:
            self.storage.transactionSync(proxy)
        finally:
            proxy.destroy()
        return result

    def run(self, query, params=()):
        try:
            cursor = self.sql.exec(query, *(int(p) if isinstance(p, bool) else p for p in params))
            return [dict(row) for row in cursor.toArray().to_py()]
        except Exception as e:
            raise OSError('storage failed: ' + str(e)) from e


class SqlPenStore(PenStore):
    # Reused from the local store so both stores judge an entry by the same rules.
    _valid_id = staticmethod(LocalFilesPenStore._valid_id)
    _fields = staticmethod(LocalFilesPenStore._fields)
    _validate_record = LocalFilesPenStore._validate_record
    _name_key = staticmethod(LocalFilesPenStore._name_key)

    def __init__(self, adapter, timezone=None, clock=None):
        self.adapter = adapter
        self.timezone = ZoneInfo(timezone) if timezone else None
        self.clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self._ready = False

    def ensure_ready(self):
        """Make the tables. Call it before a transaction, so a rollback never undoes tables this store thinks it made."""
        if not self._ready:
            for statement in _SCHEMA:
                self.adapter.run(statement, ())
            self._ready = True

    def _run(self, query, params=()):
        self.ensure_ready()
        return self.adapter.run(query, params)

    def _now(self):
        return self.clock().astimezone(self.timezone).isoformat()

    @staticmethod
    def _text(body, title):
        body.encode('utf-8')    # a lone surrogate is refused here, as the local store refuses it
        if title is not None:
            title.encode('utf-8')

    def _collection(self, writer, collection):
        try:
            validate_writer(collection)
        except ValueError:
            raise ValueError('invalid collection name') from None
        validate_writer(writer)
        key = self._name_key(collection)
        for row in self._run("SELECT DISTINCT collection FROM pen_versions WHERE writer = ?", (writer,)):
            if row['collection'] != collection and self._name_key(row['collection']) == key:
                raise ValueError('collection names collide')
        return collection

    def _mode(self, writer, collection):
        rows = self._run("SELECT DISTINCT by_day FROM pen_versions WHERE writer = ? AND collection = ?",
                         (writer, collection))
        if len(rows) > 1:
            raise ValueError('inconsistent collection mode')
        return bool(rows[0]['by_day']) if rows else None

    def _versions(self, writer, id, version=None):
        """Parsed records of one active entry, ascending (one version when asked), or 'entry not found'."""
        if not self._valid_id(id):
            raise ValueError('invalid id')
        query = "SELECT collection, version, record FROM pen_versions WHERE writer = ? AND id = ? AND deleted = 0"
        params = [writer, id]
        if version is not None:
            query += " AND version = ?"
            params.append(version)
        rows = self._run(query + " ORDER BY version", params)
        if not rows:
            if version is not None and self._run(
                    "SELECT 1 AS found FROM pen_versions WHERE writer = ? AND id = ? AND deleted = 0 LIMIT 1", (writer, id)):
                raise ValueError('version not found')
            raise ValueError('entry not found')
        result = []
        for row in rows:
            record = json.loads(row['record'])
            self._validate_record(writer, row['collection'], id, row['version'], {k: v for k, v in record.items() if k != 'body'}, record.get('body'))
            result.append(record)
        return result

    @staticmethod
    def _row(record, deleted=False):
        return (record['writer'], record['collection'], record['id'], record['version'], int(deleted),
                record['created'], _microseconds(record['created']), int(record['by_day']), int(record['pinned']), json.dumps(record, ensure_ascii=False))

    def collections(self, writer):
        validate_writer(writer)
        rows = self._run(
            "SELECT collection, MAX(by_day) AS by_day, COUNT(DISTINCT CASE WHEN deleted = 0 THEN id END) AS count "
            "FROM pen_versions WHERE writer = ? GROUP BY collection ORDER BY collection", (writer,))
        return [{'collection': r['collection'], 'count': r['count'], 'by_day': bool(r['by_day'])} for r in rows]

    def write(self, writer, collection, body, title=None, pinned=False, by_day=None):
        self._fields(body, title, pinned)
        if by_day is not None and type(by_day) is not bool:
            raise ValueError('by_day must be a boolean')
        self._text(body, title)
        self._collection(writer, collection)
        mode = self._mode(writer, collection)
        if mode is not None and by_day is not None and mode != by_day:
            raise ValueError('collection mode is already set')
        id = uuid.uuid4().hex
        now = self._now()
        record = {'id': id, 'collection': collection, 'writer': writer, 'body': body,
                  'created': now, 'edited': now, 'pinned': pinned, 'version': 1,
                  'by_day': mode if mode is not None else bool(by_day)}
        if title is not None:
            record['title'] = title
        # One statement: refuses when another entry has set the collection's mode the other way meanwhile.
        done = self._run(
            f"INSERT INTO pen_versions ({_COLUMNS}) SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ? "
            "WHERE NOT EXISTS (SELECT 1 FROM pen_versions WHERE writer = ? AND collection = ? AND by_day <> ?) RETURNING id",
            self._row(record) + (writer, collection, int(record['by_day'])))
        if not done:
            raise ValueError('collection mode is already set')
        return id

    def edit(self, writer, id, based_on, body=None, title=None, pinned=None):
        self._fields('' if body is None else body, title, False if pinned is None else pinned)
        if type(based_on) is not int or based_on < 1:
            raise ValueError('invalid based_on')
        validate_writer(writer)
        self._text('' if body is None else body, title)
        versions = self._versions(writer, id)
        latest = versions[-1]
        if based_on != latest['version']:
            raise ValueError('version conflict')
        record = dict(latest)
        if body is not None:
            record['body'] = body
        if title is not None:
            record['title'] = title
        if pinned is not None:
            record['pinned'] = pinned
        record.update(version=latest['version'] + 1, edited=self._now())
        # One statement: appends only while the version we read is still the newest one (the atomic base check).
        done = self._run(
            f"INSERT INTO pen_versions ({_COLUMNS}) SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ? "
            "WHERE (SELECT MAX(version) FROM pen_versions WHERE writer = ? AND id = ? AND deleted = 0) = ? RETURNING version",
            self._row(record) + (writer, id, based_on))
        if not done:
            raise ValueError('version conflict')
        return record['version']

    def read(self, writer, id, version=None):
        if version is not None and (type(version) is not int or version < 1):
            raise ValueError('invalid version')
        validate_writer(writer)
        return self._versions(writer, id, version)[-1]

    def list(self, writer, collection, pinned_only=False, day=None, limit=20):
        if type(pinned_only) is not bool or type(limit) is not int or limit < 1:
            raise ValueError('invalid list filters')
        if day is not None and (not isinstance(day, str) or dt.date.fromisoformat(day).isoformat() != day):
            raise ValueError('day must be YYYY-MM-DD')
        self._collection(writer, collection)
        if day is not None and self._mode(writer, collection) is False:
            raise ValueError('day filter requires a by_day collection')
        query = ("SELECT v.collection, v.id, v.version, v.record FROM pen_versions v "
                 "WHERE v.writer = ? AND v.collection = ? AND v.deleted = 0 "
                 "AND v.version = (SELECT MAX(version) FROM pen_versions m WHERE m.writer = v.writer AND m.id = v.id AND m.deleted = 0)")
        params = [writer, collection]
        if pinned_only:
            query += " AND v.pinned = 1"
        if day is not None:
            query += " AND substr(v.created, 1, 10) = ?"
            params.append(day)
        # Newest first and the limit are applied in SQL, so only `limit` bodies are ever loaded (a Worker has 128 MB).
        # Match the local store's entry ID order when creation instants tie.
        rows = self._run(query + " ORDER BY v.created_us DESC, v.id ASC LIMIT ?", params + [limit])
        records = []
        for row in rows:
            record = json.loads(row['record'])
            self._validate_record(writer, collection, row['id'], row['version'], {k: v for k, v in record.items() if k != 'body'}, record.get('body'))
            records.append(record)
        return records

    def history(self, writer, id):
        validate_writer(writer)
        return self._versions(writer, id)

    def delete(self, writer, id):
        validate_writer(writer)
        self._versions(writer, id)
        # Kept in the table, marked deleted: it leaves list/read/history and still counts as a collection.
        if not self._run("UPDATE pen_versions SET deleted = 1 WHERE writer = ? AND id = ? AND deleted = 0 RETURNING version",
                         (writer, id)):
            raise ValueError('entry not found')

    def export_pen(self, writer):
        validate_writer(writer)
        entries = {}
        for row in self._run("SELECT collection, id, deleted, version, record FROM pen_versions WHERE writer = ? "
                             "ORDER BY collection, deleted, id, version", (writer,)):
            entry = entries.setdefault(row['id'], {'collection': row['collection'], 'id': row['id'],
                                                   'deleted': bool(row['deleted']), 'versions': []})
            record = json.loads(row['record'])
            self._validate_record(writer, row['collection'], row['id'], row['version'], {k: v for k, v in record.items() if k != 'body'}, record.get('body'))
            entry['versions'].append(record)
        return list(entries.values())

    def import_pen(self, writer, records):
        """Install missing entries after validating the snapshot and the existing subset (same rules as the local store)."""
        validate_writer(writer)
        incoming, modes, spellings = {}, {}, {}
        for record in records:
            if not isinstance(record, dict) or record.keys() != {'collection', 'id', 'deleted', 'versions'}:
                raise ValueError('invalid transfer entry')
            collection, id, deleted, versions = (record[k] for k in ('collection', 'id', 'deleted', 'versions'))
            self._collection(writer, collection)
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
            incoming[id] = record
        existing = {}
        for record in self.export_pen(writer):
            id = record['id']
            if id in existing or incoming.get(id) != record:
                raise ValueError(f'destination conflicts at {id}')
            existing[id] = record
        for id, record in incoming.items():
            if id in existing:
                continue
            # One statement per entry, so an entry arrives with its whole history or not at all.
            rows = [dict(zip(_COLUMNS.split(', '), self._row(v, record['deleted']))) for v in record['versions']]
            self._run(f"INSERT INTO pen_versions ({_COLUMNS}) SELECT "
                      + ", ".join(f"json_extract(value, '$.{c}')" for c in _COLUMNS.split(', '))
                      + " FROM json_each(?)", (json.dumps(rows, ensure_ascii=False),))
