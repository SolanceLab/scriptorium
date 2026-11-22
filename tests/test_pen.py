# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Pen behavior and separation from extraction."""
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
import engine.pen as pen_module
import roster
import nightly


def append_many(root):
    store = LocalFilesPenStore(root, 'UTC', lambda: dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc))
    for _ in range(12): store.write('parallel', 'notes', 'entry', by_day=True)


def race_edit(root, id, body, start, outcomes):
    store = LocalFilesPenStore(root, 'UTC')
    start.wait(10)
    try:
        outcomes.put(store.edit('parallel', id, 1, body=body))
    except ValueError as error:
        outcomes.put(str(error))


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

    def success(self, writer, name, **args):
        error, result = self.call(writer, name, args)
        self.assertFalse(error, result)
        return json.loads(result)

    @mock.patch.object(server.Config, 'history', True)
    def test_tools_and_versions(self):
        writer = 'alpha'
        id = self.success(writer, 'pen_write', collection='daily', body='First', by_day=True)['id']
        self.assertEqual(self.success(writer, 'pen_edit', id=id, based_on=1,
                                      body='Second', title='Title', pinned=True), {'version': 2})
        self.assertTrue(self.call(writer, 'pen_edit', {'id': id, 'based_on': 1, 'body': 'Stale'})[0])
        first = self.success(writer, 'pen_read', id=id, version=1)
        latest = self.success(writer, 'pen_read', id=id)
        self.assertEqual((first['body'], first['pinned']), ('First', False))
        self.assertEqual((latest['body'], latest['title'], latest['pinned']), ('Second', 'Title', True))
        self.assertEqual(self.success(writer, 'pen_history', id=id), [first, latest])
        newer = dt.datetime(2026, 1, 2, 19, tzinfo=dt.timezone.utc)
        self.store.clock = lambda: newer
        other = self.success(writer, 'pen_write', collection='daily', body='Another')['id']
        self.assertEqual(self.success(writer, 'pen_collections'),
                         [{'collection': 'daily', 'count': 2, 'by_day': True}])
        self.assertEqual(self.success(writer, 'pen_list', collection='daily', day='2026-01-02'), [latest])
        self.assertEqual(self.success(writer, 'pen_list', collection='daily', pinned_only=True), [latest])
        self.assertEqual(self.success(writer, 'pen_list', collection='daily', limit=1)[0]['id'], other)
        self.assertTrue(self.call(writer, 'pen_write', {'collection': 'daily', 'body': 'x', 'by_day': False})[0])
        self.assertEqual(self.success(writer, 'pen_delete', id=id), {'ok': True})
        self.assertTrue(self.call(writer, 'pen_read', {'id': id})[0])
        directory = self.store.root / writer / 'daily'
        (directory / '.deleted' / id).rename(directory / id)
        self.assertEqual(self.success(writer, 'pen_history', id=id), [first, latest])

    def seed_transfer(self):
        for writer in ('alpha', 'beta'):
            for day in range(1, 4):
                self.store.clock = lambda: dt.datetime(2026, 1, day, 18, tzinfo=dt.timezone.utc)
                self.store.write(writer, 'daily', f'{writer} day {day}', by_day=True)
            for collection in ('notes', 'decisions'):
                id = self.store.write(writer, collection, f'{writer} original', title='First', pinned=True)
                self.store.clock = lambda: dt.datetime(2026, 1, 5, 18, tzinfo=dt.timezone.utc)
                self.store.edit(writer, id, 1, body=f'{writer} revision', title='Second')
                version = self.store.root / writer / collection / id / '2.md'
                version.write_text(version.read_text().replace('"version": 2', '"version": 2, "extra": {"value": 7}'))
                self.store.edit(writer, id, 2, pinned=False)
                if collection == 'decisions':
                    self.store.delete(writer, id)

    def transfer_args(self, dest, writer):
        return ['transfer', '--from', 'local:' + self.tmp.name, '--to', 'local:' + dest, '--writer', writer]

    def test_transfer_preserves_fields_and_order_for_both_writers(self):
        self.seed_transfer()
        with tempfile.TemporaryDirectory() as dest:
            copied = LocalFilesPenStore(dest)
            for writer in ('alpha', 'beta'):
                result = subprocess.run([sys.executable, str(ROOT / 'engine/pen.py'), *self.transfer_args(dest, writer)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                source = self.store.export_pen(writer)
                self.assertEqual(copied.export_pen(writer), source)
                self.assertEqual(copied.collections(writer), self.store.collections(writer))
                self.assertEqual(copied.list(writer, 'daily', day='2026-01-02'),
                                 self.store.list(writer, 'daily', day='2026-01-02'))
                for record in source:
                    if record['deleted']:
                        self.assertEqual(record['collection'], 'decisions')
                        with self.assertRaises(ValueError):
                            copied.read(writer, record['id'])
                    else:
                        self.assertEqual(copied.history(writer, record['id']), record['versions'])
                        for version in record['versions']:
                            self.assertEqual(copied.read(writer, record['id'], version['version']), version)
                for record in (r for r in source if r['collection'] != 'daily'):
                    self.assertEqual([r['version'] for r in record['versions']], [1, 2, 3])
                    self.assertEqual([r['pinned'] for r in record['versions']], [True, True, False])
                    self.assertEqual(record['versions'][2]['extra'], {'value': 7})

    def test_transfer_resumes_after_interrupted_version_copy(self):
        self.seed_transfer()
        writer = 'alpha'
        source = self.store.export_pen(writer)
        save = LocalFilesPenStore._save

        def fail_partway(store, path, record):
            if path.name == '2.md':
                raise OSError('simulated interruption')
            save(store, path, record)

        with tempfile.TemporaryDirectory() as dest:
            stderr = io.StringIO()
            args = self.transfer_args(dest, writer)
            with mock.patch.object(LocalFilesPenStore, '_save', fail_partway), contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as failure:
                    main(args)
            self.assertEqual(failure.exception.code, 1)
            self.assertIn('3/5', stderr.getvalue())
            copied = LocalFilesPenStore(dest)
            before = copied.export_pen(writer)
            self.assertEqual(before, source[:3])
            self.assertFalse(list(copied.root.rglob('.pen-*')))
            main(args)
            with mock.patch.object(LocalFilesPenStore, '_save', side_effect=AssertionError('retry rewrites data')):
                main(args)
            self.assertEqual(copied.export_pen(writer), source)

    def test_transfer_refuses_conflicts_before_copying(self):
        self.seed_transfer()
        for bad in ('occupied', 'foreign', 'duplicate', 'invalid encoding'):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as dest:
                writer = 'alpha'
                copied = LocalFilesPenStore(dest)
                source = self.store.export_pen(writer)
                if bad == 'occupied':
                    copied.write(writer, 'notes', 'destination-only')
                elif bad == 'foreign':
                    source[-1]['versions'][0]['writer'] = 'beta'
                elif bad == 'duplicate':
                    source.append(source[0])
                else:
                    source[-1]['versions'][0]['body'] = chr(0xD800)
                before = copied.export_pen(writer)
                with self.assertRaises(ValueError):
                    copied.import_pen(writer, source)
                self.assertEqual(copied.export_pen(writer), before)

    def test_transfer_verifies_destination_snapshot(self):
        self.seed_transfer()
        with tempfile.TemporaryDirectory() as dest:
            stderr = io.StringIO()
            with mock.patch.object(LocalFilesPenStore, 'import_pen'), contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit):
                    main(self.transfer_args(dest, 'alpha'))
            self.assertIn('destination does not match the source snapshot', stderr.getvalue())

    def test_transfer_verifies_collection_metadata_on_readback(self):
        self.store.write('alpha', 'notes', 'pooled')
        install = LocalFilesPenStore.import_snapshot

        def omit_default(store, writer, snapshot):
            install(store, writer, snapshot)
            (store.root / writer / 'notes' / '.pool').unlink()

        with tempfile.TemporaryDirectory() as dest, contextlib.redirect_stderr(io.StringIO()) as stderr:
            with mock.patch.object(LocalFilesPenStore, 'import_snapshot', omit_default), self.assertRaises(SystemExit):
                main(self.transfer_args(dest, 'alpha'))
            self.assertIn('destination does not match the source snapshot', stderr.getvalue())

    def test_transfer_accepts_legacy_source_but_requires_metadata_capable_destination(self):
        id = self.store.write('alpha', 'notes', 'source')
        legacy = mock.Mock(spec=['export_pen', 'import_pen'])
        legacy.export_pen.return_value = self.store.export_pen('alpha')
        with tempfile.TemporaryDirectory() as dest, contextlib.redirect_stderr(io.StringIO()) as stderr:
            target = LocalFilesPenStore(dest)
            with mock.patch.object(pen_module, 'load_store', side_effect=[legacy, target]):
                main(self.transfer_args(dest, 'alpha'))
            self.assertEqual(target.export_snapshot('alpha')['collections'], {'notes': 'silo'})
            self.assertEqual(target.read('alpha', id)['body'], 'source')
            with mock.patch.object(pen_module, 'load_store', side_effect=[target, legacy]):
                with self.assertRaises(SystemExit):
                    main(self.transfer_args(dest, 'alpha'))
            self.assertIn('destination must declare contract_version = 3', stderr.getvalue())
            legacy.import_pen.assert_not_called()

    def test_transfer_accepts_different_entry_order(self):
        self.seed_transfer()
        with tempfile.TemporaryDirectory() as dest:
            target = LocalFilesPenStore(dest)
            source = mock.Mock()
            source.contract_version = pen_module.PEN_CONTRACT_VERSION
            snapshot = self.store.export_snapshot('alpha')
            source.export_snapshot.return_value = dict(snapshot, entries=list(reversed(snapshot['entries'])))
            with mock.patch.object(pen_module, 'load_store', side_effect=[source, target]):
                main(self.transfer_args(dest, 'alpha'))
            self.assertEqual(target.export_snapshot('alpha'), snapshot)

    def test_explicit_read_rejects_a_gap_in_version_history(self):
        id = self.store.write('alpha', 'notes', 'first')
        self.store.edit('alpha', id, 1, body='second')
        self.store.edit('alpha', id, 2, body='third')
        (self.store.root / 'alpha' / 'notes' / id / '2.md').unlink()
        with self.assertRaisesRegex(ValueError, 'invalid version history'):
            self.store.read('alpha', id, version=3)

    def test_record_writes_and_delete_are_atomic(self):
        writer = 'alpha'
        id = self.store.write(writer, 'notes', 'original')
        self.store.edit(writer, id, 1, body='second')
        root = self.store.root
        def files():
            return {path.relative_to(root): path.read_bytes() for path in root.rglob('*') if path.is_file()}

        before = files()
        operations = [
            (self.store.write, ('new', 'body')),
            (self.store.edit, (id, 2, 'new')),
            (self.store.delete, (id,)),
        ]
        real_replace = os.replace

        def fail_publish(source, target):
            if Path(target).parent.name.startswith('.pen-'):
                return real_replace(source, target)
            raise OSError('simulated rename failure')

        for operation, args in operations:
            for replace in (mock.Mock(side_effect=OSError('simulated rename failure')), fail_publish):
                with self.subTest(operation=operation.__name__):
                    with mock.patch('engine.pen.os.replace', side_effect=replace):
                        with self.assertRaisesRegex(OSError, 'simulated rename failure'):
                            operation(writer, *args)
                    self.assertEqual(files(), before)
                    self.assertFalse(list(root.rglob('.pen-*')))

    @mock.patch.object(server.Config, 'history', True)
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
        a_key, b_key = 'a' * 32, 'b' * 32
        id = self.store.write('beta', 'private-notes', 'Beta only')
        own = self.store.write('alpha', 'notes', 'Alpha only')
        before = self.store.export_pen('beta')
        cases = [
            ('pen_collections', {}),
            ('pen_write', {'collection': 'private-notes', 'body': 'Alpha only'}),
            ('pen_edit', {'id': id, 'based_on': 1, 'body': 'Changed'}),
            ('pen_read', {'id': id, 'version': 1}),
            ('pen_list', {'collection': 'private-notes'}),
            ('pen_history', {'id': id}),
            ('pen_delete', {'id': id}),
        ]
        self.assertEqual({server.PEN_ALIASES[name] for name, _ in cases}, {tool['name'] for tool in server._journal_tools(True)})
        for name, args in cases:
            with self.subTest(tool=name):
                spoofed = call(a_key, name, dict(args, writer='beta'))
                self.assertTrue(spoofed['isError'])
                self.assertEqual(spoofed['content'][0]['text'], 'unknown argument')
                self.assertTrue(call('c' * 32, name, args)['isError'])
                result = call(a_key, name, args)
                if 'id' in args:
                    self.assertTrue(result['isError'])
                    self.assertFalse(call(a_key, name, dict(args, id=own))['isError'])
                else:
                    self.assertFalse(result['isError'])
                    value = json.loads(result['content'][0]['text'])
                    if name == 'pen_collections':
                        self.assertEqual(value, [{'collection': 'notes', 'count': 1, 'by_day': False}])
                    elif name == 'pen_write':
                        self.assertEqual(self.store.read('alpha', value['id'])['body'], 'Alpha only')
                    else:
                        self.assertTrue(all(record['writer'] == 'alpha' for record in value))
                self.assertEqual(self.store.export_pen('beta'), before)
        self.assertFalse(call(b_key, 'pen_read', {'id': id})['isError'])

    def test_extraction_refuses_case_alias_of_pen(self):
        self.store.write('alpha', 'notes', 'entry')
        root = Path(self.tmp.name)
        if not (root / 'PEN').exists() or not (root / 'PEN').samefile(root / 'pen'):
            self.skipTest('test filesystem is case-sensitive')
        with self.assertRaisesRegex(ValueError, 'outside the pen folder'):
            server.inside_root('PEN/alpha/notes/new-output')

    def test_extraction_refuses_symlink_into_pen(self):
        self.store.write('alpha', 'notes', 'entry')
        root = Path(self.tmp.name).resolve()
        (root / 'alias').symlink_to(root / 'pen' / 'alpha', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'outside the pen folder'):
            server.inside_root('alias/notes/new-output')
        self.assertEqual(server.inside_root('new-output'), str(root / 'new-output'))

        # The resolved path need not contain a component spelled "pen".
        (root / 'pen').rename(root / 'storage')
        (root / 'pen').symlink_to(root / 'storage', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'outside the pen folder'):
            server.inside_root('storage/alpha/notes/new-output')

    def test_extraction_reserves_pen_name_before_first_write(self):
        for spelling in ('pen', 'PEN'):
            with self.assertRaisesRegex(ValueError, 'outside the pen folder'):
                server.inside_root(spelling + '/new-output')

    def test_local_store_refuses_directory_aliases(self):
        self.store.write('Alpha', 'Notes', 'original')
        if not (self.store.root / 'alpha').exists():
            self.skipTest('test filesystem is case-sensitive')
        with self.assertRaises(ValueError):
            self.store.write('alpha', 'Notes', 'other')
        with self.assertRaises(ValueError):
            self.store.write('Alpha', 'notes', 'other')

    def test_body_roundtrip_and_corrupt_header_refused(self):
        body = 'line\r\nsecond\rthird\n## pen-entry {bad}\n\\escaped'
        id = self.store.write('alpha', 'notes', body)
        self.assertEqual(self.store.read('alpha', id)['body'], body)
        path = self.store.root / 'alpha' / 'notes' / id / '1.md'
        path.write_text('## pen-entry {bad}\nBody')
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.edit('alpha', id, 1, body='x')
        self.assertEqual(path.read_bytes(), before)

    def test_symlinks_refused(self):
        id = self.store.write('beta', 'notes', 'Beta')
        folder = self.store.root / 'beta' / 'notes'
        alpha = self.store.root / 'alpha'
        alpha.symlink_to(folder.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.store.write('alpha', 'notes', 'x')
        alpha.unlink()
        alpha.mkdir()
        (alpha / 'notes').symlink_to(folder, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.store.write('alpha', 'notes', 'x')
        (alpha / 'notes').unlink()
        own = self.store.write('alpha', 'notes', 'Alpha')
        version = alpha / 'notes' / own / '1.md'
        version.unlink()
        version.symlink_to(folder / id / '1.md')
        with self.assertRaises(ValueError):
            self.store.read('alpha', own)

    def test_concurrent_processes_do_not_lose_entries(self):
        processes = [multiprocessing.Process(target=append_many, args=(self.tmp.name,)) for _ in range(3)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(10)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(len(self.store.list('parallel', 'notes', day='2026-01-01', limit=40)), 36)

    def test_concurrent_edits_accept_exactly_one_base(self):
        id = self.store.write('parallel', 'notes', 'original')
        start, outcomes = multiprocessing.Event(), multiprocessing.Queue()
        self.addCleanup(outcomes.close)
        processes = [multiprocessing.Process(target=race_edit, args=(self.tmp.name, id, body, start, outcomes))
                     for body in ('first edit', 'second edit')]
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(10)
            self.assertEqual(process.exitcode, 0)
        self.assertCountEqual([outcomes.get(timeout=2) for _ in processes], [2, 'version conflict'])
        self.assertEqual([r['version'] for r in self.store.history('parallel', id)], [1, 2])

    def test_windows_lock_releases_the_same_byte_on_failed_write(self):
        windows = mock.Mock(LK_LOCK=1, LK_UNLCK=2)
        with mock.patch.object(pen_module, 'fcntl', None), mock.patch.object(pen_module, 'msvcrt', windows, create=True):
            with mock.patch.object(pen_module.os, 'replace', side_effect=OSError('write failed')):
                with self.assertRaisesRegex(OSError, 'write failed'):
                    self.store.write('alpha', 'notes', 'entry')
        fd = windows.locking.call_args_list[0].args[0]
        self.assertEqual(windows.locking.call_args_list, [mock.call(fd, 1, 1), mock.call(fd, 2, 1)])
        with self.assertRaises(OSError):
            os.fstat(fd)

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

    def test_old_custom_store_contract_is_refused(self):
        module = type(sys)('pen_fixture')
        module.store = self.store
        with mock.patch.dict(sys.modules, {'pen_fixture': module}):
            with mock.patch.object(self.store, 'contract_version', 1):
                with self.assertRaisesRegex(SystemExit, 'contract_version = 3'):
                    server.configure(self.tmp.name, 'c' * 32, {}, 'pen_fixture:store')

    def test_incomplete_custom_store_refused(self):
        module = type(sys)('pen_fixture')
        module.store = PenStore
        with mock.patch.dict(sys.modules, {'pen_fixture': module}):
            with self.assertRaisesRegex(SystemExit, 'lacks required method'):
                server.configure(self.tmp.name, 'c' * 32, {}, 'pen_fixture:store')

    def test_bad_names(self):
        for name in ('', '../x', '.hidden', '-x', 'x/y', 'x\\y', 'x.', 'x ', 'NUL', 'C:'):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self.store.write(name, 'notes', 'x')
                with self.assertRaises(ValueError):
                    self.store.write('alpha', name, 'x')

    def test_extraction_does_not_import_pen(self):
        for name in ('write_memories.py', 'write_people.py', 'found_in_chats.py', 'nightly.py', 'roster.py', 'review.py'):
            tree = ast.parse((ROOT / 'engine' / name).read_text())
            imports = [n.module or '' for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
            imports += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
            self.assertFalse(any('pen' in x.split('.') for x in imports), name)

    def test_extraction_paths_do_not_touch_pen(self):
        self.seed_transfer()
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
        self.assertTrue(self.call('alpha', 'write_memories', {'export': 'export.json', 'out': 'pen/alpha/notes'})[0])


if __name__ == '__main__': unittest.main()
