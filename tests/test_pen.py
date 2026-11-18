# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Journal behavior and separation from extraction."""
import ast
import datetime as dt
import hashlib
import http.client
import threading
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'mcp'), str(ROOT / 'engine')]
from engine.pen import LocalFilesPenStore
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
        written = call('a' * 32, 'journal_write', {'body': 'own'})
        self.assertFalse(written['isError'])
        id = json.loads(written['content'][0]['text'])['id']
        self.assertTrue(call('b' * 32, 'journal_read', {'id': id})['isError'])
        self.assertTrue(call('b' * 32, 'journal_edit', {'id': id, 'body': 'bad'})['isError'])
        self.assertTrue(call('b' * 32, 'journal_read', {'id': id, 'writer': 'alpha'})['isError'])
        self.assertTrue(call('c' * 32, 'journal_recent', {})['isError'])
        self.assertFalse(call('a' * 32, 'journal_read', {'id': id})['isError'])

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

    def test_bad_names(self):
        for name in ('', '../x', '.hidden', '-x', 'x/y', 'x\\y'):
            with self.subTest(name=name), self.assertRaises(ValueError): self.store.append(name, {'body': 'x'})

    def test_extraction_does_not_import_pen(self):
        for name in ('write_memories.py', 'write_people.py', 'found_in_chats.py', 'nightly.py', 'roster.py', 'review.py'):
            tree = ast.parse((ROOT / 'engine' / name).read_text())
            imports = [n.module or '' for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
            imports += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
            self.assertFalse(any('pen' in x.split('.') for x in imports), name)

    def test_extraction_paths_do_not_touch_pen(self):
        for writer in ('alpha', 'beta'): self.store.append(writer, {'body': 'private fixture'})
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
