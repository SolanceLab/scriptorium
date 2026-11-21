# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The pen contract (docs/your-own-store.md) run against SqlPenStore, and against LocalFilesPenStore as the reference."""
import datetime as dt
import json
import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'mcp'), str(ROOT / 'engine')]
from engine.cloud_pen import SqlPenStore, Sqlite3Sql
from engine.pen import LocalFilesPenStore


def _us(created):
    return (dt.datetime.fromisoformat(created) - dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)) // dt.timedelta(microseconds=1)


class Clock:
    """A clock the test moves by hand."""
    def __init__(self, *start):
        self.now = dt.datetime(*(start or (2026, 1, 1, 18)), tzinfo=dt.timezone.utc)

    def __call__(self):
        return self.now

    def set(self, *when):
        self.now = dt.datetime(*when, tzinfo=dt.timezone.utc)


class Contract:
    """Every store-agnostic behaviour of the pen. Subclasses say how to make a store."""
    def make(self, clock):
        raise NotImplementedError

    def setUp(self):
        self.clock = Clock()
        self.store = self.make(self.clock)

    def test_write_read_list_collections(self):
        id = self.store.write('alpha', 'notes', 'First', title='T')
        self.assertRegex(id, r'^[0-9a-f]{32}$')
        record = self.store.read('alpha', id)
        self.assertEqual((record['body'], record['title'], record['version'], record['pinned'], record['by_day']),
                         ('First', 'T', 1, False, False))
        self.assertEqual(record['created'], record['edited'])
        self.assertTrue(record['created'].endswith('+07:00'))
        self.assertEqual(self.store.list('alpha', 'notes'), [record])
        self.assertEqual(self.store.collections('alpha'), [{'collection': 'notes', 'count': 1, 'by_day': False}])
        self.assertEqual(self.store.list('alpha', 'missing'), [])
        self.assertEqual(self.store.collections('beta'), [])

    def test_edit_appends_versions_and_refuses_a_stale_base(self):
        id = self.store.write('alpha', 'notes', 'one', title='A')
        self.clock.set(2026, 1, 3, 1)
        self.assertEqual(self.store.edit('alpha', id, 1, body='two', pinned=True), 2)
        with self.assertRaisesRegex(ValueError, 'version conflict'):
            self.store.edit('alpha', id, 1, body='stale')
        self.assertEqual(self.store.edit('alpha', id, 2, title='B'), 3)
        first, second, third = self.store.history('alpha', id)
        self.assertEqual((first['body'], first['pinned'], first['title']), ('one', False, 'A'))
        self.assertEqual((second['body'], second['pinned'], second['title']), ('two', True, 'A'))
        self.assertEqual((third['body'], third['pinned'], third['title']), ('two', True, 'B'))
        self.assertEqual(second['created'], first['created'])
        self.assertNotEqual(second['edited'], first['edited'])
        self.assertEqual(self.store.read('alpha', id), third)
        self.assertEqual(self.store.read('alpha', id, 1), first)
        with self.assertRaisesRegex(ValueError, 'version not found'):
            self.store.read('alpha', id, 4)

    def test_pinned_day_limit_and_order(self):
        ids = []
        for day, pinned in ((1, False), (2, True), (3, False)):
            self.clock.set(2026, 1, day, 5)
            ids.append(self.store.write('alpha', 'daily', f'day {day}', pinned=pinned, by_day=True))
        self.assertEqual([r['id'] for r in self.store.list('alpha', 'daily')], ids[::-1])
        self.assertEqual([r['id'] for r in self.store.list('alpha', 'daily', limit=2)], ids[:0:-1])
        self.assertEqual([r['id'] for r in self.store.list('alpha', 'daily', pinned_only=True)], [ids[1]])
        self.assertEqual([r['id'] for r in self.store.list('alpha', 'daily', day='2026-01-03')], [ids[0 + 2]])
        self.store.edit('alpha', ids[0], 1, pinned=True)
        self.assertEqual([r['id'] for r in self.store.list('alpha', 'daily', pinned_only=True)], [ids[1], ids[0]])
        self.store.edit('alpha', ids[1], 1, pinned=False)
        self.assertEqual([r['id'] for r in self.store.list('alpha', 'daily', pinned_only=True)], [ids[0]])

    def test_by_day_mode_is_fixed_on_first_write(self):
        self.store.write('alpha', 'daily', 'x', by_day=True)
        self.store.write('alpha', 'daily', 'inherits')
        self.assertEqual(self.store.collections('alpha'), [{'collection': 'daily', 'count': 2, 'by_day': True}])
        with self.assertRaisesRegex(ValueError, 'collection mode is already set'):
            self.store.write('alpha', 'daily', 'no', by_day=False)
        self.store.write('alpha', 'flat', 'x')
        with self.assertRaisesRegex(ValueError, 'collection mode is already set'):
            self.store.write('alpha', 'flat', 'no', by_day=True)
        with self.assertRaisesRegex(ValueError, 'day filter requires a by_day collection'):
            self.store.list('alpha', 'flat', day='2026-01-02')
        self.assertEqual(len(self.store.list('alpha', 'daily', day='2026-01-02')), 2)
        self.assertEqual(self.store.list('alpha', 'daily', day='2026-01-05'), [])

    def test_delete_moves_aside_but_keeps_the_collection(self):
        id = self.store.write('alpha', 'notes', 'gone', by_day=False)
        keep = self.store.write('alpha', 'notes', 'kept')
        self.assertIsNone(self.store.delete('alpha', id))
        for call in (lambda: self.store.read('alpha', id), lambda: self.store.history('alpha', id),
                     lambda: self.store.edit('alpha', id, 1, body='x'), lambda: self.store.delete('alpha', id)):
            with self.assertRaisesRegex(ValueError, 'entry not found'):
                call()
        self.assertEqual([r['id'] for r in self.store.list('alpha', 'notes')], [keep])
        self.assertEqual(self.store.collections('alpha'), [{'collection': 'notes', 'count': 1, 'by_day': False}])
        self.store.delete('alpha', keep)
        self.assertEqual(self.store.collections('alpha'), [{'collection': 'notes', 'count': 0, 'by_day': False}])
        self.assertEqual({r['id']: r['deleted'] for r in self.store.export_pen('alpha')}, {id: True, keep: True})

    def test_every_method_is_scoped_to_its_writer(self):
        id = self.store.write('beta', 'private', 'Beta only')
        before = self.store.export_pen('beta')
        for call in (lambda: self.store.read('alpha', id), lambda: self.store.history('alpha', id),
                     lambda: self.store.edit('alpha', id, 1, body='x'), lambda: self.store.delete('alpha', id)):
            with self.assertRaisesRegex(ValueError, 'entry not found'):
                call()
        self.assertEqual(self.store.list('alpha', 'private'), [])
        self.assertEqual(self.store.collections('alpha'), [])
        self.assertEqual(self.store.export_pen('alpha'), [])
        self.store.write('alpha', 'private', 'mine')
        self.assertEqual(self.store.export_pen('beta'), before)
        self.assertEqual(self.store.read('beta', id)['body'], 'Beta only')

    def test_bad_input_is_refused(self):
        id = self.store.write('alpha', 'notes', 'x')
        bad_names = ('', '../x', '.hidden', '-x', 'x/y', 'x\\y', 'x.', 'x ', 'NUL', 'C:')
        for name in bad_names:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self.store.write(name, 'notes', 'x')
                with self.assertRaises(ValueError):
                    self.store.write('alpha', name, 'x')
        refused = [
            lambda: self.store.write('alpha', 'notes', 5),
            lambda: self.store.write('alpha', 'notes', 'x', title=5),
            lambda: self.store.write('alpha', 'notes', 'x', pinned='yes'),
            lambda: self.store.write('alpha', 'notes', 'x', by_day='yes'),
            lambda: self.store.write('alpha', 'notes', chr(0xD800)),
            lambda: self.store.edit('alpha', id, 0, body='x'),
            lambda: self.store.edit('alpha', id, True, body='x'),
            lambda: self.store.edit('alpha', 'nope', 1, body='x'),
            lambda: self.store.edit('alpha', 'f' * 32, 1, body='x'),
            lambda: self.store.read('alpha', 'NOPE'),
            lambda: self.store.read('alpha', id, version=0),
            lambda: self.store.read('alpha', id, version='1'),
            lambda: self.store.history('alpha', 5),
            lambda: self.store.delete('alpha', 'nope'),
            lambda: self.store.list('alpha', 'notes', limit=0),
            lambda: self.store.list('alpha', 'notes', pinned_only='no'),
            lambda: self.store.list('alpha', 'notes', day='2026-1-2'),
            lambda: self.store.list('alpha', '../x'),
            lambda: self.store.collections(''),
        ]
        for index, call in enumerate(refused):
            with self.subTest(case=index), self.assertRaises(ValueError):
                call()
        self.assertEqual(self.store.read('alpha', id)['version'], 1)
        self.assertEqual(len(self.store.export_pen('alpha')), 1)

    def test_collection_names_that_differ_only_by_case_collide(self):
        self.store.write('alpha', 'Notes', 'x')
        with self.assertRaisesRegex(ValueError, 'collection names collide'):
            self.store.write('alpha', 'notes', 'x')
        with self.assertRaisesRegex(ValueError, 'collection names collide'):
            self.store.list('alpha', 'notes')
        self.store.write('beta', 'notes', 'other writer, no clash')

    def test_body_roundtrip_keeps_every_character(self):
        body = 'line\r\nsecond\rthird\n## pen-entry {bad}\n\\escaped é \U0001f4dc'
        id = self.store.write('alpha', 'notes', body)
        self.assertEqual(self.store.read('alpha', id)['body'], body)

    def seed(self):
        for writer in ('alpha', 'beta'):
            for day in range(1, 4):
                self.clock.set(2026, 1, day, 18)
                self.store.write(writer, 'daily', f'{writer} day {day}', by_day=True)
            for collection in ('notes', 'decisions'):
                self.clock.set(2026, 1, 4, 18)
                id = self.store.write(writer, collection, f'{writer} original', title='First', pinned=True)
                self.clock.set(2026, 1, 5, 18)
                self.store.edit(writer, id, 1, body=f'{writer} revision', title='Second')
                self.store.edit(writer, id, 2, pinned=False)
                if collection == 'decisions':
                    self.store.delete(writer, id)

    def test_export_import_round_trip(self):
        self.seed()
        snapshot = self.store.export_pen('alpha')
        self.assertEqual(sum(r['deleted'] for r in snapshot), 1)
        self.assertEqual(sorted(len(r['versions']) for r in snapshot), [1, 1, 1, 3, 3])
        for record in snapshot:
            self.assertEqual(set(record), {'collection', 'id', 'deleted', 'versions'})
            self.assertEqual([v['version'] for v in record['versions']], list(range(1, len(record['versions']) + 1)))
        copy = self.make(Clock())
        copy.import_pen('alpha', snapshot)
        self.assertEqual(sorted(copy.export_pen('alpha'), key=lambda r: r['id']), sorted(snapshot, key=lambda r: r['id']))
        self.assertEqual(copy.collections('alpha'), self.store.collections('alpha'))
        self.assertEqual(copy.list('alpha', 'daily', day='2026-01-02'), self.store.list('alpha', 'daily', day='2026-01-02'))
        for record in snapshot:
            if record['deleted']:
                with self.assertRaises(ValueError):
                    copy.read('alpha', record['id'])
            else:
                self.assertEqual(copy.history('alpha', record['id']), record['versions'])
        copy.import_pen('alpha', snapshot)    # the same snapshot again is a harmless retry
        resumed = self.make(Clock())
        resumed.import_pen('alpha', snapshot[:2])    # an interrupted import leaves a subset; the retry completes it
        resumed.import_pen('alpha', snapshot)
        self.assertEqual(sorted(resumed.export_pen('alpha'), key=lambda r: r['id']), sorted(snapshot, key=lambda r: r['id']))
        self.assertEqual(len(copy.export_pen('alpha')), len(snapshot))
        self.assertEqual(copy.export_pen('beta'), [])

    def test_import_keeps_unknown_header_fields(self):
        id = self.store.write('alpha', 'notes', 'x')
        snapshot = self.store.export_pen('alpha')
        snapshot[0]['versions'][0]['extra'] = {'value': 7}
        copy = self.make(Clock())
        copy.import_pen('alpha', snapshot)
        self.assertEqual(copy.read('alpha', id)['extra'], {'value': 7})
        self.assertEqual(copy.export_pen('alpha'), snapshot)

    def test_import_refuses_conflicts_before_copying(self):
        self.seed()
        for bad in ('occupied', 'foreign', 'duplicate', 'invalid encoding', 'gap', 'mode', 'shape'):
            with self.subTest(bad=bad):
                copy = self.make(Clock())
                source = self.store.export_pen('alpha')
                if bad == 'occupied':
                    copy.write('alpha', 'notes', 'destination-only')
                elif bad == 'foreign':
                    source[-1]['versions'][0]['writer'] = 'beta'
                elif bad == 'duplicate':
                    source.append(source[0])
                elif bad == 'invalid encoding':
                    source[-1]['versions'][0]['body'] = chr(0xD800)
                elif bad == 'gap':
                    source[-1]['versions'].pop(0)
                elif bad == 'mode':
                    source[0]['versions'][0]['by_day'] = not source[0]['versions'][0]['by_day']
                else:
                    source[0].pop('deleted')
                before = copy.export_pen('alpha')
                with self.assertRaises(ValueError):
                    copy.import_pen('alpha', source)
                self.assertEqual(copy.export_pen('alpha'), before)


class SqlStoreTests(Contract, unittest.TestCase):
    def make(self, clock):
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        return SqlPenStore(Sqlite3Sql(connection), 'Asia/Jakarta', clock)

    def test_newest_first_within_one_millisecond_and_on_an_equal_clock(self):
        base = dt.datetime(2026, 1, 1, 18, tzinfo=dt.timezone.utc)
        self.clock.now = base + dt.timedelta(microseconds=200)
        first = self.store.write('alpha', 'notes', 'first')
        self.clock.now = base + dt.timedelta(microseconds=100)    # an older instant, written later, within one millisecond
        self.store.write('alpha', 'notes', 'older instant')
        self.assertEqual([r['id'] for r in self.store.list('alpha', 'notes', limit=1)], [first])
        self.clock.now = base + dt.timedelta(seconds=5)
        tie_one = self.store.write('alpha', 'notes', 'tie one')
        tie_two = self.store.write('alpha', 'notes', 'tie two')
        self.assertEqual([r['id'] for r in self.store.list('alpha', 'notes', limit=1)], [min(tie_one, tie_two)])

    def test_the_same_microsecond_lists_the_lower_id_first(self):
        self.clock.now = dt.datetime(2026, 1, 1, 18, 0, 0, 123456, tzinfo=dt.timezone.utc)
        first = self.store.write('alpha', 'notes', 'first')
        later = self.store.write('alpha', 'notes', 'second')
        self.assertEqual([r['id'] for r in self.store.list('alpha', 'notes', limit=1)], [min(first, later)])
        stored = self.store.adapter.run('SELECT created_us FROM pen_versions')[0]['created_us']
        self.assertEqual(stored, 1767290400123456)    # integer microseconds since the UTC epoch

    def test_entries_written_with_different_utc_offsets_order_by_true_time(self):
        a = self.store.write('alpha', 'notes', 'a')
        b = self.store.write('alpha', 'notes', 'b')
        records = {r['id']: r for r in self.store.export_pen('alpha')}
        # a: 03:00 UTC, but its text sorts after b's; b: 07:00 UTC, the true later one
        records[a]['versions'][0].update(created='2026-01-01T10:00:00+07:00', edited='2026-01-01T10:00:00+07:00')
        records[b]['versions'][0].update(created='2026-01-01T02:00:00-05:00', edited='2026-01-01T02:00:00-05:00')
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        fresh = SqlPenStore(Sqlite3Sql(connection), 'Asia/Jakarta', self.clock)
        fresh.import_pen('alpha', list(records.values()))
        self.assertEqual([r['id'] for r in fresh.list('alpha', 'notes')], [b, a])

    def test_equal_created_times_list_in_id_order(self):
        ids = [self.store.write('alpha', 'notes', f'n{i}') for i in range(15)]    # the clock never moves
        self.assertEqual([r['id'] for r in self.store.list('alpha', 'notes', limit=1)], [min(ids)])
        self.assertEqual([r['id'] for r in self.store.list('alpha', 'notes', limit=3)], sorted(ids)[:3])

    def test_list_orders_by_the_instant_not_the_text_across_a_dst_fall_back(self):
        old = self.store.write('alpha', 'notes', 'older')
        self.store.adapter.run("UPDATE pen_versions SET created = ?, created_us = ?, record = json_set(record, '$.created', ?) WHERE id = ?",
                               ('2025-11-02T01:55:00-04:00', _us('2025-11-02T01:55:00-04:00'), '2025-11-02T01:55:00-04:00', old))
        new = self.store.write('alpha', 'notes', 'newer')
        self.store.adapter.run("UPDATE pen_versions SET created = ?, created_us = ?, record = json_set(record, '$.created', ?) WHERE id = ?",
                               ('2025-11-02T01:05:00-05:00', _us('2025-11-02T01:05:00-05:00'), '2025-11-02T01:05:00-05:00', new))    # 06:05 UTC, ten minutes later
        self.assertEqual([r['id'] for r in self.store.list('alpha', 'notes', limit=1)], [new])

    def test_an_edit_does_not_change_id_order_at_the_same_time(self):
        a = self.store.write('alpha', 'notes', 'A')
        b = self.store.write('alpha', 'notes', 'B')    # same clock value
        self.store.edit('alpha', a, 1, body='A2')
        self.assertEqual([r['id'] for r in self.store.list('alpha', 'notes', limit=1)], [min(a, b)])

    def test_storage_failure_is_an_oserror(self):
        connection = sqlite3.connect(':memory:')
        store = SqlPenStore(Sqlite3Sql(connection))
        store.write('alpha', 'notes', 'x')
        connection.close()
        with self.assertRaises(OSError):
            store.write('alpha', 'notes', 'y')
        with self.assertRaises(OSError):
            store.collections('alpha')

    def test_the_schema_is_made_on_first_use_only(self):
        statements = []

        class Spy(Sqlite3Sql):
            def run(self, query, params=()):
                statements.append(query)
                return super().run(query, params)

        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        store = SqlPenStore(Spy(connection))
        self.assertEqual(statements, [])
        store.collections('alpha')
        store.collections('alpha')
        self.assertEqual(sum(s.startswith('CREATE TABLE') for s in statements), 1)

    def test_list_loads_only_the_limited_rows_not_the_whole_collection(self):
        clock = Clock(2026, 1, 1, 9)
        store = self.make(clock)
        for n in range(5):
            clock.set(2026, 1, 1, 9 + n)
            store.write('alpha', 'notes', f'body {n}')
        loaded = []
        real = store.adapter.run

        def spy(query, params=()):
            rows = real(query, params)
            if 'v.record' in query:
                loaded.append(len(rows))
            return rows
        store.adapter.run = spy
        newest = store.list('alpha', 'notes', limit=1)
        self.assertEqual([r['body'] for r in newest], ['body 4'])
        self.assertEqual(loaded, [1])

    def test_an_edit_whose_base_went_stale_between_read_and_write_conflicts(self):
        id = self.store.write('alpha', 'notes', 'one')
        real = self.store._versions

        def stale_read(*args, **kwargs):
            result = real(*args, **kwargs)
            other = dict(result[-1], version=2, body='raced')
            self.store.adapter.run(
                "INSERT INTO pen_versions (writer, collection, id, version, deleted, created, created_us, by_day, pinned, record) "
                "VALUES (?, ?, ?, ?, 0, ?, ?, 0, 0, ?)",
                ('alpha', 'notes', id, 2, other['created'], _us(other['created']), json.dumps(other)))
            return result

        self.store._versions = stale_read
        with self.assertRaisesRegex(ValueError, 'version conflict'):
            self.store.edit('alpha', id, 1, body='mine')
        self.store._versions = real
        self.assertEqual([r['body'] for r in self.store.history('alpha', id)], ['one', 'raced'])


class LocalStoreTests(Contract, unittest.TestCase):
    """The same contract on the local store: the expectations above are the local store's own behaviour."""
    def make(self, clock):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return LocalFilesPenStore(tmp.name, 'Asia/Jakarta', clock)


class ParityTests(unittest.TestCase):
    """The same calls into both stores give the same answers, ignoring ids and timestamps."""
    def stores(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        return (LocalFilesPenStore(tmp.name, 'Asia/Jakarta', Clock()),
                SqlPenStore(Sqlite3Sql(connection), 'Asia/Jakarta', Clock()))

    @staticmethod
    def norm(value, ids):
        if isinstance(value, dict):
            return {k: ('T' if k in ('created', 'edited') else ids.setdefault(v, f'#{len(ids)}') if k == 'id' else
                        ParityTests.norm(v, ids)) for k, v in value.items()}
        if isinstance(value, list):
            return [ParityTests.norm(v, ids) for v in value]
        return value

    def run_script(self, store):
        ids, out, clock = {}, [], store.clock

        def step(name, *args, **kwargs):
            clock.now += dt.timedelta(hours=7)
            args = [ids_by_name.get(a, a) if isinstance(a, str) and a.startswith('@') else a for a in args]
            try:
                result = getattr(store, name)(*args, **kwargs)
                if name == 'write':
                    ids_by_name['@' + str(len(ids_by_name))] = result
                    result = 'id'
                out.append(('ok', name, self.norm(result, ids)))
            except (ValueError, OSError) as error:
                out.append(('error', name, type(error).__name__, str(error)))
        ids_by_name = {}
        step('write', 'alpha', 'daily', 'one', by_day=True)          # @0
        step('write', 'alpha', 'daily', 'two', title='Two', pinned=True)       # @1
        step('write', 'alpha', 'notes', 'flat')                                # @2
        step('write', 'alpha', 'daily', 'clash', by_day=False)
        step('write', 'beta', 'daily', 'other writer')                         # @3
        step('write', 'alpha', 'Daily', 'case clash')
        step('edit', 'alpha', '@0', 1, body='one, revised')
        step('edit', 'alpha', '@0', 1, body='stale')
        step('edit', 'alpha', '@0', 2, pinned=True, title='One')
        step('edit', 'beta', '@0', 1, body='not yours')
        step('read', 'alpha', '@0')
        step('read', 'alpha', '@0', 1)
        step('read', 'alpha', '@0', 9)
        step('read', 'beta', '@0')
        step('history', 'alpha', '@0')
        step('list', 'alpha', 'daily')
        step('list', 'alpha', 'daily', pinned_only=True, limit=1)
        step('list', 'alpha', 'daily', day='2026-01-02')
        step('list', 'alpha', 'notes', day='2026-01-02')
        step('list', 'alpha', 'nothing')
        step('collections', 'alpha')
        step('delete', 'alpha', '@1')
        step('delete', 'alpha', '@1')
        step('read', 'alpha', '@1')
        step('list', 'alpha', 'daily')
        step('collections', 'alpha')
        step('collections', 'beta')
        step('delete', 'alpha', '@2')
        step('collections', 'alpha')
        clock.now += dt.timedelta(hours=1)
        exported = self.norm(store.export_pen('alpha'), ids)
        out.append(('export', sorted(exported, key=lambda r: (r['collection'], r['deleted'], r['versions'][0]['body']))))
        return out

    def test_equal_creation_times_use_entry_id_order_in_both_stores(self):
        local, sql = self.stores()
        ids = ['f' * 32, 'c' * 32, '0' * 32]
        for store in (local, sql):
            for id in ids:
                with mock.patch('uuid.uuid4', return_value=uuid.UUID(hex=id)):
                    store.write('alpha', 'notes', id)
            self.assertEqual([r['id'] for r in store.list('alpha', 'notes', limit=2)], sorted(ids)[:2])

    def test_the_same_calls_give_the_same_answers(self):
        local, sql = self.stores()
        self.assertEqual(self.run_script(sql), self.run_script(local))


if __name__ == '__main__':
    unittest.main()
