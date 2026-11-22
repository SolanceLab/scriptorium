# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The pen contract (docs/your-own-store.md) run against SqlPenStore, and against LocalFilesPenStore as the reference."""
import datetime as dt
from concurrent.futures import ThreadPoolExecutor
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

    def test_pool_default_upgrade_and_owner_access(self):
        modern = self.store.write('alpha', 'new', 'shared')
        old = self.store.write('alpha', 'legacy', 'private')
        self.remove_pool_metadata('legacy')
        self.store.write('alpha', 'legacy', 'still private')
        self.store.edit('alpha', old, 1, body='also private')
        pool = lambda collection, default: default == 'pool'
        self.assertEqual(self.store.read('alpha', old)['body'], 'also private')
        self.assertEqual(self.store.read('alpha', modern, visible=pool)['body'], 'shared')
        self.assertEqual([c['collection'] for c in self.store.collections('alpha', visible=pool)], ['new'])
        for id in (old, 'f' * 32, 'invalid'):
            with self.assertRaisesRegex(ValueError, '^entry not found$'):
                self.store.read('alpha', id, version=99, visible=pool)
            with self.assertRaisesRegex(ValueError, '^entry not found$'):
                self.store.history('alpha', id, visible=pool)
        for name in ('legacy', 'missing', 'LEGACY'):
            with self.assertRaisesRegex(ValueError, '^collection not found$'):
                self.store.list('alpha', name, visible=pool)

    def test_surface_filter_uses_current_version_before_limit(self):
        keep = self.store.write('alpha', 'notes', 'old', surface='desktop')
        self.clock.set(2026, 1, 2)
        newest = self.store.write('alpha', 'notes', 'new', surface='desktop')
        self.store.edit('alpha', newest, 1, body='web', surface='web')
        self.store.edit('alpha', newest, 2, body='unlabelled')
        self.assertEqual([v.get('surface') for v in self.store.history('alpha', newest)],
                         ['desktop', 'web', None])
        self.assertNotIn('surface', self.store.read('alpha', newest))
        self.assertEqual([v['id'] for v in self.store.list('alpha', 'notes', surface='desktop', limit=1)], [keep])
        self.assertEqual(self.store.list('alpha', 'notes', surface='web'), [])
        self.assertEqual(self.store.list('alpha', 'notes', limit=1)[0]['id'], newest)

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

    def test_append_creates_today_and_appends_to_it(self):
        # 2026-01-01 18:00 UTC is 2026-01-02 01:00+07:00 in Asia/Jakarta.
        first = self.store.append('alpha', 'daily', 'morning note', surface='desktop')
        self.assertEqual(first['action'], 'created')
        self.assertEqual(first['version'], 1)
        self.assertRegex(first['id'], r'^[0-9a-f]{32}$')
        rec1 = self.store.read('alpha', first['id'])
        self.assertEqual(rec1['title'], '2026-01-02')
        self.assertEqual(rec1['body'], 'morning note')
        self.assertEqual(rec1['surface'], 'desktop')
        self.assertTrue(rec1['by_day'])

        self.clock.set(2026, 1, 2, 3)    # still 2026-01-02 in Asia/Jakarta (10:00+07:00)
        second = self.store.append('alpha', 'daily', 'afternoon note', surface='web')
        self.assertEqual(second, {'id': first['id'], 'version': 2, 'action': 'appended'})
        rec2 = self.store.read('alpha', first['id'])
        self.assertEqual(rec2['body'], 'morning note\n\nafternoon note')
        self.assertEqual(rec2['title'], '2026-01-02')
        self.assertEqual(rec2['surface'], 'web')
        self.assertEqual(rec2['created'], rec1['created'])
        self.assertNotEqual(rec2['edited'], rec1['edited'])
        self.assertEqual([v['version'] for v in self.store.history('alpha', first['id'])], [1, 2])

        # Unlabelled append clears surface on the new version, as edit does.
        third = self.store.append('alpha', 'daily', 'evening note')
        self.assertEqual(third, {'id': first['id'], 'version': 3, 'action': 'appended'})
        self.assertNotIn('surface', self.store.read('alpha', first['id']))

        # Next calendar day creates a new entry (with explicit title when given).
        self.clock.set(2026, 1, 3, 2)    # 2026-01-03 09:00+07:00
        next_day = self.store.append('alpha', 'daily', 'new day', title='Day Three')
        self.assertEqual(next_day['action'], 'created')
        self.assertEqual(next_day['version'], 1)
        self.assertNotEqual(next_day['id'], first['id'])
        self.assertEqual(self.store.read('alpha', next_day['id'])['title'], 'Day Three')

    def test_append_by_id_and_refusals(self):
        flat_id = self.store.write('alpha', 'notes', 'start', title='Flat')
        with self.assertRaisesRegex(ValueError, 'by_day'):
            self.store.append('alpha', 'notes', 'cannot without id')
        res = self.store.append('alpha', 'notes', 'more', id=flat_id, surface='mobile')
        self.assertEqual(res, {'id': flat_id, 'version': 2, 'action': 'appended'})
        rec = self.store.read('alpha', flat_id)
        self.assertEqual(rec['body'], 'start\n\nmore')
        self.assertEqual(rec['title'], 'Flat')
        self.assertEqual(rec['surface'], 'mobile')
        # Wrong collection, foreign writer, missing id, or both id and day are refused.
        with self.assertRaisesRegex(ValueError, 'collection'):
            self.store.append('alpha', 'other', 'wrong', id=flat_id)
        with self.assertRaisesRegex(ValueError, 'entry not found'):
            self.store.append('beta', 'notes', 'not yours', id=flat_id)
        with self.assertRaisesRegex(ValueError, 'entry not found'):
            self.store.append('alpha', 'notes', 'missing', id='f' * 32)
        with self.assertRaises(ValueError):
            self.store.append('alpha', 'notes', 'both', id=flat_id, day='2026-01-02')

    def test_append_field_merge_rules(self):
        first = self.store.append(
            'alpha', 'daily', 'one',
            fields={'tags': ['a', 'b'], 'meta': {'x': 1, 'y': 2}, 'note': 'keep', 'mood': 3, 'seen': False})
        self.store.append(
            'alpha', 'daily', 'two',
            fields={'tags': ['b', 'c', 'a', 'd'], 'meta': {'y': 20, 'z': 30}, 'mood': 5})
        rec = self.store.read('alpha', first['id'])
        self.assertEqual(rec['fields'], {
            'tags': ['a', 'b', 'c', 'd'],
            'meta': {'x': 1, 'y': 20, 'z': 30},
            'note': 'keep',
            'mood': 5,
            'seen': False,
        })
        self.store.append('alpha', 'daily', 'three', fields={'note': 'updated', 'seen': True})
        rec = self.store.read('alpha', first['id'])
        self.assertEqual(rec['fields']['note'], 'updated')
        self.assertEqual(rec['fields']['seen'], True)
        self.assertEqual(rec['fields']['mood'], 5)

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
    def remove_pool_metadata(self, collection):
        self.store.adapter.run("DELETE FROM pen_collections WHERE writer = ? AND collection = ?",
                               ('alpha', collection))

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
        tables = [s.split()[5] for s in statements if s.startswith('CREATE TABLE')]
        self.assertCountEqual(tables, ['pen_versions', 'pen_collections'])

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
    def remove_pool_metadata(self, collection):
        (self.store.root / 'alpha' / collection / '.pool').unlink()

    def make(self, clock):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return LocalFilesPenStore(tmp.name, 'Asia/Jakarta', clock)


class PoolTransferTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def sql(self):
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        return SqlPenStore(Sqlite3Sql(connection))

    def test_interrupted_transfer_both_ways_preserves_defaults_and_surfaces(self):
        for to_sql in (True, False):
            with self.subTest(to_sql=to_sql):
                local = LocalFilesPenStore(self.root / str(to_sql))
                sql = self.sql()
                source, destination = (local, sql) if to_sql else (sql, local)
                private = source.write('alpha', 'legacy', 'private', surface='one')
                if to_sql:
                    (local.root / 'alpha' / 'legacy' / '.pool').unlink()
                else:
                    sql.adapter.run("DELETE FROM pen_collections WHERE collection = 'legacy'")
                source.edit('alpha', private, 1, body='two', surface='two')
                source.delete('alpha', private)
                shared = source.write('alpha', 'pooled', 'shared', surface='one')
                snapshot = source.export_snapshot('alpha')
                self.assertEqual(snapshot['collections'], {'legacy': 'silo', 'pooled': 'pool'})
                if to_sql:
                    target, method = sql.adapter, 'run'
                else:
                    target, method = local, '_install_entry'
                real = getattr(target, method)

                def interrupt(query_or_path, *args):
                    if (str(query_or_path).startswith('INSERT INTO pen_versions') and shared in args[0][0]
                            if to_sql else query_or_path.name == shared):
                        raise OSError('interrupted')
                    return real(query_or_path, *args)

                with mock.patch.object(target, method, side_effect=interrupt):
                    with self.assertRaisesRegex(OSError, 'interrupted'):
                        destination.import_snapshot('alpha', snapshot)
                partial = destination.export_snapshot('alpha')
                self.assertEqual(partial['collections'], {} if to_sql else snapshot['collections'])
                self.assertEqual(len(partial['entries']), 0 if to_sql else 1)
                destination.import_snapshot('alpha', snapshot)
                destination.import_snapshot('alpha', snapshot)
                self.assertEqual(destination.export_snapshot('alpha'), snapshot)

    def test_concurrent_first_writes_publish_one_pool_default(self):
        for sql in (False, True):
            with self.subTest(sql=sql):
                local = self.root / 'local'
                database = self.root / 'test.sqlite'

                def write(body):
                    if not sql:
                        return LocalFilesPenStore(local).write('alpha', 'first', body)
                    connection = sqlite3.connect(database)
                    try:
                        return SqlPenStore(Sqlite3Sql(connection)).write('alpha', 'first', body)
                    finally:
                        connection.close()

                with ThreadPoolExecutor(max_workers=2) as pool:
                    ids = list(pool.map(write, ('one', 'two')))
                if sql:
                    connection = sqlite3.connect(database)
                    self.addCleanup(connection.close)
                    store = SqlPenStore(Sqlite3Sql(connection))
                else:
                    store = LocalFilesPenStore(local)
                seen = store.list('alpha', 'first', visible=lambda name, default: default == 'pool')
                self.assertCountEqual([r['id'] for r in seen], ids)
                self.assertEqual(store.export_snapshot('alpha')['collections'], {'first': 'pool'})

    def test_interleaved_appends_from_two_writers_keep_both_texts(self):
        for sql in (False, True):
            with self.subTest(sql=sql):
                local = self.root / f'append_local_{sql}'
                database = self.root / f'append_{sql}.sqlite'
                fixed_clock = Clock(2026, 1, 2, 10)

                def append_note(note):
                    if not sql:
                        return LocalFilesPenStore(local, 'Asia/Jakarta', fixed_clock).append('alpha', 'daily', note)
                    connection = sqlite3.connect(database)
                    try:
                        return SqlPenStore(Sqlite3Sql(connection), 'Asia/Jakarta', fixed_clock).append('alpha', 'daily', note)
                    finally:
                        connection.close()

                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(append_note, ('writer-one-note', 'writer-two-note')))

                # One created version 1, the other appended version 2 on the same entry; both texts survive.
                self.assertEqual({r['action'] for r in results}, {'created', 'appended'})
                self.assertEqual({r['version'] for r in results}, {1, 2})
                self.assertEqual(len({r['id'] for r in results}), 1)
                entry_id = results[0]['id']

                if sql:
                    connection = sqlite3.connect(database)
                    self.addCleanup(connection.close)
                    store = SqlPenStore(Sqlite3Sql(connection), 'Asia/Jakarta', fixed_clock)
                else:
                    store = LocalFilesPenStore(local, 'Asia/Jakarta', fixed_clock)
                final = store.read('alpha', entry_id)
                self.assertEqual(final['version'], 2)
                self.assertIn('writer-one-note', final['body'])
                self.assertIn('writer-two-note', final['body'])
                self.assertEqual(len(store.list('alpha', 'daily')), 1)

    def test_append_to_explicit_day_creates_and_appends_on_that_day(self):
        for sql in (False, True):
            with self.subTest(sql=sql):
                clock = Clock(2026, 11, 22, 10)
                if sql:
                    connection = sqlite3.connect(':memory:')
                    self.addCleanup(connection.close)
                    store = SqlPenStore(Sqlite3Sql(connection), 'Asia/Jakarta', clock)
                else:
                    store = LocalFilesPenStore(self.root / 'explicit_day_local', 'Asia/Jakarta', clock)
                first = store.append('alpha', 'daily', 'first note', day='2026-11-20')
                second = store.append('alpha', 'daily', 'second note', day='2026-11-20')
                self.assertEqual(first['action'], 'created')
                self.assertEqual(second, {'id': first['id'], 'version': 2, 'action': 'appended'})
                listed_20 = store.list('alpha', 'daily', day='2026-11-20')
                listed_22 = store.list('alpha', 'daily', day='2026-11-22')
                self.assertEqual(len(listed_20), 1)
                self.assertEqual(listed_20[0]['id'], first['id'])
                self.assertEqual(listed_20[0]['body'], 'first note\n\nsecond note')
                self.assertTrue(listed_20[0]['created'].startswith('2026-11-20'))
                self.assertTrue(listed_20[0]['edited'].startswith('2026-11-22'))
                self.assertEqual(listed_22, [])


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
            kwargs = {k: (ids_by_name.get(v, v) if isinstance(v, str) and v.startswith('@') else v) for k, v in kwargs.items()}
            try:
                result = getattr(store, name)(*args, **kwargs)
                if name == 'write':
                    ids_by_name['@' + str(len(ids_by_name))] = result
                    result = 'id'
                elif name == 'append' and result['action'] == 'created':
                    ids_by_name['@' + str(len(ids_by_name))] = result['id']
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
        step('append', 'alpha', 'log', 'first log', fields={'tags': ['a'], 'meta': {'k': 1}}, surface='desktop')  # @4
        step('append', 'alpha', 'log', 'second log', fields={'tags': ['a', 'b'], 'meta': {'m': 2}}, surface='web')
        step('append', 'alpha', 'log', 'backdated one', day='2026-01-01')                                      # @5
        step('append', 'alpha', 'log', 'backdated two', day='2026-01-01')
        step('list', 'alpha', 'log', day='2026-01-01')
        step('append', 'alpha', 'notes', 'cannot without id')
        step('append', 'alpha', 'notes', 'by id ok', id='@2', surface='mobile')
        step('read', 'alpha', '@4')
        step('read', 'alpha', '@2')
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
