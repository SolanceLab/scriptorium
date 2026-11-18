# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Journal behavior and separation from extraction."""
import ast
import contextlib
import io
import datetime as dt
import hashlib
import http.client
import threading
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'mcp'), str(ROOT / 'engine')]
from engine.pen import LocalFilesPenStore, PenStore, main
import server
import roster
import nightly


def append_many(root):
    store = LocalFilesPenStore(root, 'UTC', lambda: dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc))
    for _ in range(12): store.append('parallel', {'body': 'entry'})


class PenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = LocalFilesPenStore(self.tmp.name, 'Asia/Jakarta', lambda: dt.datetime(2026, 1, 1, 18, tzinfo=dt.timezone.utc))
        self.env = mock.patch.dict(os.environ, {'TEST_PEN_A_KEY': 'a' * 32, 'TEST_PEN_B_KEY': 'b' * 32})
        self.env.start()
        self.addCleanup(self.env.stop)
        server.configure(self.tmp.name, 'c' * 32, {'TEST_PEN_A_KEY': 'alpha', 'TEST_PEN_B_KEY': 'beta'})
        server.Config.pen = self.store

    def call(self, writer, name, args):
        result = server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}}, writer)['result']
        return result['isError'], result['content'][0]['text']

    def test_tools_and_isolation(self):
        for writer in ('alpha', 'beta'):
            err, value = self.call(writer, 'journal_write', {'body': 'first\n', 'title': 'Title'})
            self.assertFalse(err, value)
            id = json.loads(value)['id']
            self.assertFalse(self.call(writer, 'journal_edit', {'id': id, 'body': 'changed'})[0])
            self.assertEqual(json.loads(self.call(writer, 'journal_read', {'id': id})[1])['body'], 'changed')
            self.assertEqual(len(json.loads(self.call(writer, 'journal_read', {'date': '2026-01-02'})[1])), 1)
            self.assertEqual(json.loads(self.call(writer, 'journal_recent', {})[1])[0]['id'], id)
            other = 'beta' if writer == 'alpha' else 'alpha'
            self.assertTrue(self.call(other, 'journal_read', {'id': id})[0])
            self.assertTrue(self.call(other, 'journal_edit', {'id': id, 'body': 'bad'})[0])
            self.assertTrue(self.call(other, 'journal_read', {'id': id, 'writer': writer})[0])
        self.assertTrue(self.call(None, 'journal_recent', {})[0])

    def test_core_tools(self):
        for writer in ('alpha', 'beta'):
            self.assertTrue(self.call(writer, 'core_read', {})[0])
            self.assertFalse(self.call(writer, 'core_write', {'text': writer, 'based_on': 0})[0])
            self.assertTrue(self.call(writer, 'core_write', {'text': 'stale', 'based_on': 0})[0])
            self.assertEqual(json.loads(self.call(writer, 'core_read', {'version': 1})[1])['text'], writer)
            self.assertEqual(json.loads(self.call(writer, 'core_history', {})[1])[0]['number'], 1)
            for name, args in [('core_read', {}), ('core_write', {'text': 'x', 'based_on': 1}), ('core_history', {})]:
                self.assertTrue(self.call(writer, name, dict(args, writer='other'))[0])
        with mock.patch('engine.pen.os.replace', side_effect=OSError('failure')):
            self.assertTrue(self.call('alpha', 'core_write', {'text': 'new', 'based_on': 1})[0])
        self.assertEqual(len(self.store.core_history('alpha')), 1)

    def test_legacy_version_without_saved_base(self):
        for kind in ('core', 'marrow'):
            write = getattr(self.store, kind + '_write')
            read = getattr(self.store, kind + '_read')
            extra = ([],) if kind == 'marrow' else ()
            write('alpha', 'first', 0, *extra)
            write('alpha', 'second', 1, *extra)
            path = self.store.root / 'alpha' / kind / '0002.md'
            path.write_text(path.read_text().replace('"based_on": 1, ', ''))
            self.assertNotIn('based_on', self.store._parse(path)[0][0])
            self.assertEqual(read('alpha', 2)['based_on'], 1)
            self.assertEqual(getattr(self.store, kind + '_history')('alpha')[-1]['based_on'], 1)

    def test_spine_and_marrow_tools(self):
        for writer in ('alpha', 'beta'):
            other = 'beta' if writer == 'alpha' else 'alpha'
            err, value = self.call(writer, 'spine_write', {'title': 'title', 'body': writer, 'pinned': True})
            self.assertFalse(err, value)
            id = json.loads(value)['id']
            self.assertTrue(self.call(other, 'spine_read', {'id': id})[0])
            self.assertTrue(self.call(other, 'spine_edit', {'id': id, 'body': 'bad'})[0])
            self.assertTrue(self.call(other, 'spine_delete', {'id': id})[0])
            self.assertFalse(self.call(writer, 'spine_edit', {'id': id, 'body': 'updated'})[0])
            self.assertEqual(json.loads(self.call(writer, 'spine_read', {})[1])[0]['body'], 'updated')
            self.assertFalse(self.call(writer, 'marrow_write', {'text': writer, 'based_on': 0, 'spine_ids': [id]})[0])
            self.assertTrue(self.call(writer, 'marrow_write', {'text': 'stale', 'based_on': 0, 'spine_ids': []})[0])
            self.assertEqual(json.loads(self.call(writer, 'marrow_read', {'version': 1})[1])['spine_ids'], [id])
            self.assertEqual(len(json.loads(self.call(writer, 'marrow_history', {})[1])), 1)
            self.store.core_write(writer, writer, 0)
            self.store.append(writer, {'body': writer})
            self.assertFalse(self.call(writer, 'spine_delete', {'id': id})[0])
            self.assertTrue((Path(self.tmp.name) / 'pen' / writer / 'spine' / '.deleted' / (id + '.md')).is_file())
            for name, args in [('spine_write', {'title': 't', 'body': 'b'}), ('spine_read', {}), ('spine_edit', {'id': id}), ('spine_delete', {'id': id}), ('marrow_read', {}), ('marrow_write', {'text': 'x', 'based_on': 1, 'spine_ids': []}), ('marrow_history', {})]:
                self.assertTrue(self.call(writer, name, dict(args, writer=other))[0])
    def seed_transfer(self):
        for writer in ('alpha', 'beta'):
            for day in range(1, 4):
                now = dt.datetime(2026, 1, day, 12, tzinfo=dt.timezone.utc)
                self.store.clock = lambda: now
                for index in range(2):
                    self.store.append(writer, {'title': f'Day {day}', 'body': f'{writer} journal {day}/{index}'})
            ids = [
                self.store.spine_write(writer, 'Pinned', f'{writer} original', True),
                self.store.spine_write(writer, 'Unpinned', f'{writer} active'),
                self.store.spine_write(writer, 'Deleted', f'{writer} archived'),
            ]
            self.store.clock = lambda: dt.datetime(2026, 1, 4, 12, tzinfo=dt.timezone.utc)
            self.store.spine_edit(writer, ids[0], title='Edited', body=f'{writer} revised')
            for base in range(3):
                self.store.core_write(writer, f'{writer} core {base}', base)
                self.store.marrow_write(writer, f'{writer} marrow {base}', base, ids[:base + 1])
            self.store.spine_delete(writer, ids[-1])

    def transfer_args(self, dest, writer):
        return ['transfer', '--from', 'local:' + self.tmp.name, '--to', 'local:' + dest, '--writer', writer]

    def test_transfer_preserves_fields_and_order_for_both_writers(self):
        self.seed_transfer()
        with tempfile.TemporaryDirectory() as dest:
            copied = LocalFilesPenStore(dest)
            for writer in ('alpha', 'beta'):
                with self.subTest(writer=writer):
                    result = subprocess.run([sys.executable, str(ROOT / 'engine/pen.py'), *self.transfer_args(dest, writer)],
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    # Deep equality covers ids, full headers, bodies, filenames and deleted flags.
                    source_records = self.store.export_pen(writer)
                    copied_records = copied.export_pen(writer)
                    self.assertEqual(source_records, copied_records)
                    self.assertEqual({r['file'] for r in copied_records if r['kind'] == 'journal'},
                                     {'2026-01-01.md', '2026-01-02.md', '2026-01-03.md'})
                    self.assertEqual([r['header']['title'] for r in copied_records if r['deleted']], ['Deleted'])
                    active = copied.spine_read(writer)
                    self.assertEqual(active, self.store.spine_read(writer))
                    self.assertEqual([record['pinned'] for record in active], [True, False])
                    self.assertNotEqual(active[0]['created'], active[0]['edited'])
                    for kind in ('core', 'marrow'):
                        history = getattr(copied, kind + '_history')(writer)
                        self.assertEqual(history, getattr(self.store, kind + '_history')(writer))
                        self.assertEqual([(v['number'], v['based_on']) for v in history], [(1, 0), (2, 1), (3, 2)])
                        for version in (1, 2, 3):
                            self.assertEqual(getattr(copied, kind + '_read')(writer, version),
                                             getattr(self.store, kind + '_read')(writer, version))

    def test_transfer_reports_midway_failure_and_resumes(self):
        self.seed_transfer()
        with tempfile.TemporaryDirectory() as dest:
            args = self.transfer_args(dest, 'alpha')
            copied = LocalFilesPenStore(dest)
            original_replace = os.replace

            def fail_at_core(source, target):
                if target.parent.name == 'core' and target.name == '0002.md':
                    raise OSError('simulated destination error')
                original_replace(source, target)

            stderr = io.StringIO()
            with mock.patch('engine.pen.os.replace', side_effect=fail_at_core), contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as exc:
                    main(args)
            self.assertEqual(exc.exception.code, 1)
            self.assertIn('simulated destination error', stderr.getvalue())
            self.assertIn('journal: 6, core: 1, spine: 0, marrow: 0', stderr.getvalue())
            self.assertEqual(copied.export_pen('alpha'), self.store.export_pen('alpha')[:7])
            self.assertEqual(list(Path(dest).rglob('.pen-*')), [])
            first_day = self.store.root / 'alpha' / 'journal' / '2026-01-01.md'
            unchanged = first_day.read_bytes()
            self.store.clock = lambda: dt.datetime(2026, 1, 1, 12, tzinfo=dt.timezone.utc)
            self.store.append('alpha', {'body': 'the source changed after the failure'})
            with contextlib.redirect_stderr(io.StringIO()) as conflict, self.assertRaises(SystemExit):
                main(args)
            self.assertIn('destination conflicts at 2026-01-01.md', conflict.getvalue())
            first_day.write_bytes(unchanged)
            main(args)
            self.assertEqual(copied.export_pen('alpha'), self.store.export_pen('alpha'))
            with mock.patch.object(LocalFilesPenStore, '_save', side_effect=AssertionError('retry rewrites data')):
                main(args)

    def test_transfer_conflict_preserves_destination(self):
        self.seed_transfer()
        with tempfile.TemporaryDirectory() as dest:
            copied = LocalFilesPenStore(dest)
            copied.core_write('alpha', 'different document', 0)
            before = copied.export_pen('alpha')
            with self.assertRaisesRegex(ValueError, 'destination conflicts'):
                copied.import_pen('alpha', self.store.export_pen('alpha'))
            self.assertEqual(copied.export_pen('alpha'), before)

    def test_transfer_refuses_occupied_empty_file(self):
        self.seed_transfer()
        with tempfile.TemporaryDirectory() as dest:
            copied = LocalFilesPenStore(dest)
            occupied = copied.root / 'alpha' / 'journal' / '2026-01-01.md'
            occupied.parent.mkdir(parents=True)
            occupied.touch()
            with self.assertRaisesRegex(ValueError, 'destination conflicts'):
                copied.import_pen('alpha', self.store.export_pen('alpha'))
            self.assertEqual(list(copied.root.rglob('*.md')), [occupied])
            self.assertEqual(occupied.read_bytes(), b'')

    def test_record_writes_and_delete_are_atomic(self):
        writer = 'alpha'
        journal = self.store.append(writer, {'body': 'original'})
        spine = self.store.spine_write(writer, 'title', 'original')
        self.store.core_write(writer, 'original', 0)
        self.store.marrow_write(writer, 'original', 0, [spine])
        root = Path(self.tmp.name) / 'pen'
        def files():
            return {path.relative_to(root): path.read_bytes() for path in root.rglob('*') if path.is_file()}

        before = files()
        operations = [
            ('journal append', self.store.append, ({'body': 'new'},)),
            ('journal edit', self.store.edit, (journal, 'new')),
            ('core write', self.store.core_write, ('new', 1)),
            ('spine write', self.store.spine_write, ('new', 'new')),
            ('spine edit', self.store.spine_edit, (spine, 'new', 'new', True)),
            ('spine delete', self.store.spine_delete, (spine,)),
            ('marrow write', self.store.marrow_write, ('new', 1, [spine])),
        ]
        for name, operation, args in operations:
            with self.subTest(operation=name):
                with mock.patch('engine.pen.os.replace', side_effect=OSError('simulated write failure')) as replace:
                    with self.assertRaisesRegex(OSError, 'simulated write failure'):
                        operation(writer, *args)
                    replace.assert_called_once()
                self.assertEqual(files(), before)

    def test_http_binds_each_key(self):
        http_server = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(http_server.server_close)
        self.addCleanup(http_server.shutdown)
        def call(key, name, args):
            conn = http.client.HTTPConnection('127.0.0.1', http_server.server_port)
            try:
                payload = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}})
                conn.request('POST', '/mcp', payload, {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                return json.loads(response.read())['result']
            finally: conn.close()
        self.seed_transfer()
        a_key = 'a' * 32
        b_key = 'b' * 32
        journal = self.store.recent('beta', 1)[0]['id']
        spine = self.store.spine_read('beta')[0]['id']
        # Version 4 exists only for beta. Versions 1–3 exist under both keys.
        self.store.core_write('beta', 'beta only', 3)
        self.store.marrow_write('beta', 'beta only', 3, [spine])
        before = self.store.export_pen('beta')

        cases = [
            ('journal_write', {'body': 'alpha new'}, False),
            ('journal_edit', {'id': journal, 'body': 'overwrite'}, True),
            ('journal_read', {'id': journal}, True),
            ('journal_read', {'date': '2026-01-01'}, False),
            ('journal_recent', {}, False),
            ('core_read', {'version': 4}, True),
            ('core_read', {}, False),
            ('core_write', {'text': 'alpha core', 'based_on': 3}, False),
            ('core_history', {}, False),
            ('spine_write', {'title': 'new', 'body': 'alpha new'}, False),
            ('spine_edit', {'id': spine, 'body': 'overwrite'}, True),
            ('spine_delete', {'id': spine}, True),
            ('spine_read', {'id': spine}, True),
            ('spine_read', {}, False),
            ('marrow_read', {'version': 4}, True),
            ('marrow_read', {}, False),
            ('marrow_write', {'text': 'alpha marrow', 'based_on': 3, 'spine_ids': []}, False),
            ('marrow_history', {}, False),
        ]
        self.assertEqual({name for name, _, _ in cases}, {tool['name'] for tool in server.PEN_TOOLS})
        for name, args, refused in cases:
            with self.subTest(tool=name, args=args):
                spoofed = call(a_key, name, dict(args, writer='beta'))
                self.assertTrue(spoofed['isError'])
                self.assertEqual(spoofed['content'][0]['text'], 'unknown argument')
                result = call(a_key, name, args)
                self.assertEqual(result['isError'], refused, result)
                if not refused:
                    value = json.loads(result['content'][0]['text'])
                    if name == 'journal_write':
                        self.assertEqual(self.store.read('alpha', value['id'])['body'], 'alpha new')
                    elif name == 'spine_write':
                        self.assertEqual(self.store.spine_read('alpha', value['id'])['body'], 'alpha new')
                    elif name in ('core_write', 'marrow_write'):
                        self.assertEqual(value['number'], 4)
                        read = getattr(self.store, name.replace('_write', '_read'))
                        self.assertEqual(read('alpha', 4)['text'], args['text'])
                    else:
                        records = value if isinstance(value, list) else [value]
                        self.assertTrue(records)
                        self.assertTrue(all(record['writer'] == 'alpha' for record in records))
                self.assertEqual(self.store.export_pen('beta'), before)
        self.assertFalse(call(b_key, 'spine_read', {'id': spine})['isError'])
        self.assertTrue(call('c' * 32, 'journal_recent', {})['isError'])

    def test_extraction_refuses_case_alias_of_pen(self):
        self.store.append('alpha', {'body': 'entry'})
        root = Path(self.tmp.name)
        if not (root / 'PEN').exists() or not (root / 'PEN').samefile(root / 'pen'):
            self.skipTest('test filesystem is case-sensitive')
        with self.assertRaisesRegex(ValueError, 'outside the pen folder'):
            server.inside_root('PEN/alpha/journal/new-output')

    def test_extraction_refuses_symlink_into_pen(self):
        self.store.append('alpha', {'body': 'entry'})
        root = Path(self.tmp.name).resolve()
        (root / 'alias').symlink_to(root / 'pen' / 'alpha', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'outside the pen folder'):
            server.inside_root('alias/journal/new-output')
        self.assertEqual(server.inside_root('new-output'), str(root / 'new-output'))

        # The resolved path need not contain a component spelled "pen".
        (root / 'pen').rename(root / 'storage')
        (root / 'pen').symlink_to(root / 'storage', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'outside the pen folder'):
            server.inside_root('storage/alpha/journal/new-output')

    def test_extraction_reserves_pen_name_before_first_write(self):
        for spelling in ('pen', 'PEN'):
            with self.assertRaisesRegex(ValueError, 'outside the pen folder'):
                server.inside_root(spelling + '/new-output')

    def test_local_store_refuses_writer_directory_alias(self):
        self.store.spine_write('Alpha', 'title', 'original')
        if not (self.store.root / 'alpha').exists():
            self.skipTest('test filesystem is case-sensitive')
        before = self.store.export_pen('Alpha')
        with self.assertRaises(ValueError):
            self.store.spine_write('alpha', 'title', 'other')
        self.assertEqual(self.store.export_pen('Alpha'), before)

    def test_atomic_failure_and_unknown_header(self):
        id = self.store.append('alpha', {'body': 'old'})
        path = Path(self.tmp.name) / 'pen/alpha/journal/2026-01-02.md'
        text = path.read_text().replace('"writer": "alpha"', '"writer": "alpha", "future": "kept"')
        path.write_text(text)
        with mock.patch('engine.pen.os.replace', side_effect=OSError('failure')):
            with self.assertRaises(OSError): self.store.edit('alpha', id, 'new')
        self.assertEqual(path.read_text(), text)
        self.assertEqual(list(path.parent.glob('.pen-*')), [])
        self.store.edit('alpha', id, 'new')
        self.assertEqual(self.store.read('alpha', id)['future'], 'kept')

    def test_body_roundtrip_and_corrupt_header_refused(self):
        body = 'line\r\nsecond\rthird\n## pen-entry {bad}\n\\escaped\n'
        id = self.store.append('alpha', {'body': body})
        self.assertEqual(self.store.read('alpha', id)['body'], body)
        path = Path(self.tmp.name) / 'pen/alpha/journal/2026-01-02.md'
        with path.open('a') as f: f.write('## pen-entry {bad}\n')
        before = path.read_bytes()
        with self.assertRaises(ValueError): self.store.edit('alpha', id, 'x')
        self.assertEqual(path.read_bytes(), before)

    def test_symlinks_refused(self):
        self.store.append('beta', {'body': 'b'})
        alpha = Path(self.tmp.name) / 'pen/alpha'
        alpha.symlink_to(Path(self.tmp.name) / 'pen/beta', target_is_directory=True)
        with self.assertRaises(ValueError): self.store.append('alpha', {'body': 'x'})
        alpha.unlink()
        directory = alpha / 'journal'
        directory.mkdir(parents=True)
        (directory / '2026-01-02.md').symlink_to(Path(self.tmp.name) / 'pen/beta/journal/2026-01-02.md')
        with self.assertRaises(ValueError): self.store.append('alpha', {'body': 'x'})

    def test_concurrent_processes_do_not_lose_entries(self):
        processes = [multiprocessing.Process(target=append_many, args=(self.tmp.name,)) for _ in range(3)]
        for process in processes: process.start()
        for process in processes:
            process.join(10)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(len(self.store.day('parallel', '2026-01-01')), 36)

    def test_store_reference_and_duplicate_key_binding(self):
        module = type(sys)('pen_fixture')
        module.store = self.store
        with mock.patch.dict(sys.modules, {'pen_fixture': module}):
            server.configure(self.tmp.name, 'c' * 32, {'TEST_PEN_A_KEY': 'alpha'}, 'pen_fixture:store')
            self.assertIs(server.Config.pen, self.store)
        with mock.patch.dict(os.environ, {'TEST_PEN_B_KEY': 'a' * 32}):
            with self.assertRaises(SystemExit):
                server.configure(self.tmp.name, 'c' * 32, {'TEST_PEN_A_KEY': 'alpha', 'TEST_PEN_B_KEY': 'beta'})
        with self.assertRaises(SystemExit):
            server.configure(self.tmp.name, 'c' * 32, {'TEST_PEN_A_KEY': 'Alpha', 'TEST_PEN_B_KEY': 'alpha'})

    def test_journal_only_store_keeps_its_tools(self):
        class JournalStore(PenStore):
            append = self.store.append
            edit = self.store.edit
            read = self.store.read
            day = self.store.day
            recent = self.store.recent

        module = type(sys)('pen_fixture')
        module.store = JournalStore
        with mock.patch.dict(sys.modules, {'pen_fixture': module}):
            server.configure(self.tmp.name, 'c' * 32, {'TEST_PEN_A_KEY': 'alpha'}, 'pen_fixture:store')
        tools = server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})['result']['tools']
        self.assertEqual([tool['name'] for tool in tools],
                         ['write_memories', 'write_people', 'journal_write', 'journal_edit', 'journal_read', 'journal_recent'])
        self.assertFalse(self.call('alpha', 'journal_write', {'body': 'entry'})[0])
        result = server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                'params': {'name': 'core_history'}}, 'alpha')
        self.assertEqual(result['error']['code'], -32602)

    def test_bad_names(self):
        for name in ('', '../x', '.hidden', '-x', 'x/y', 'x\\y', 'x.', 'x ', 'NUL', 'C:'):
            with self.subTest(name=name), self.assertRaises(ValueError): self.store.append(name, {'body': 'x'})

    def test_extraction_does_not_import_pen(self):
        for name in ('write_memories.py', 'write_people.py', 'found_in_chats.py', 'nightly.py', 'roster.py', 'review.py'):
            tree = ast.parse((ROOT / 'engine' / name).read_text())
            imports = [n.module or '' for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
            imports += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
            self.assertFalse(any('pen' in x.split('.') for x in imports), name)

    def test_extraction_paths_do_not_touch_pen(self):
        for writer in ('alpha', 'beta'):
            self.store.append(writer, {'body': 'fixture'})
            self.store.core_write(writer, 'core', 0)
            id = self.store.spine_write(writer, 'title', 'body')
            self.store.marrow_write(writer, 'marrow', 0, [id])
        folder = Path(self.tmp.name) / 'pen'
        def digest():
            h = hashlib.sha256()
            for p in sorted(folder.rglob('*')):
                if p.is_file(): h.update(str(p.relative_to(folder)).encode()); h.update(p.read_bytes())
            return h.digest()
        before = digest()
        spy = mock.Mock(wraps=self.store)
        server.Config.pen = spy
        export = Path(self.tmp.name) / 'export.json'
        from test_mcp import CONV, ANSWER
        export.write_text(json.dumps(CONV))
        fake = Path(self.tmp.name) / 'model.py'
        fake.write_text('import sys\nsys.stdin.read()\nprint(' + repr(json.dumps(ANSWER)) + ')\n')
        model_env = mock.patch.dict(os.environ, {'SCRIPTORIUM_MODEL_CMD': sys.executable + ' ' + str(fake)})
        model_env.start()
        self.addCleanup(model_env.stop)
        memories = Path(self.tmp.name) / 'memories'
        memories.mkdir()
        for name, args in [('write_memories', {'export': 'export.json', 'out': 'memories'}), ('write_people', {'export': 'export.json', 'memories': 'memories', 'out': 'people'})]:
            if name == 'write_people': fake.write_text("import sys\nsys.stdin.read()\nprint('[]')\n")
            err, message = self.call('alpha', name, args)
            self.assertFalse(err, message)
        class Source:
            def read(self, cursor): return [{'id': 'sample', 'sender': 'unmatched', 'said_at': '2020-01-01T00:00:00Z', 'text': 'A sample message.'}]
            def cards(self): return [{'id': 'p', 'name': 'Different'}]
        class Sink:
            def write(self, facts, suggestions): return {'stored': 0, 'skipped': 0, 'rejected': [], 'deferred': 0}
        source, sink = Source(), mock.Mock(wraps=Sink())
        self.assertTrue(roster.run_roster(source, sink, str(Path(self.tmp.name) / 'state'), model_cmd='unused', log=lambda _: None)['ok'])
        with mock.patch.object(roster, 'load_ref', side_effect=[source, sink]):
            self.assertEqual(nightly.main(['--out', str(Path(self.tmp.name) / 'night'), '--source', 'x:y', '--sink', 'x:z']), 0)
        self.assertEqual(sink.write.call_count, 2)
        self.assertEqual(before, digest())
        self.assertEqual(spy.method_calls, [])
        self.assertTrue(self.call('alpha', 'write_memories', {'export': 'export.json', 'out': 'pen/alpha/journal'})[0])


if __name__ == '__main__': unittest.main()
