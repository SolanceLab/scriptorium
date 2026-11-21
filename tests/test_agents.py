# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Shared keys: one key for several agents, the setup command, and the tool count."""
import contextlib
import copy
import http.client
import io
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'mcp'), str(ROOT / 'engine')]
import server
import setup_agents

CLEAR = {'SCRIPTORIUM_AGENTS': '', 'SCRIPTORIUM_WRITERS': '', 'MEMORY_ENTRY_WRITERS': ''}
PRIMARY, ONE, SHARED = 'p' * 32, 'o' * 32, 's' * 32


def listing(binding):
    return server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, binding)['result']['tools']


def pen_call(binding, name, args):
    answer = server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                            'params': {'name': name, 'arguments': args}}, binding)
    result = answer['result']
    return result['isError'], result['content'][0]['text']


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patch = mock.patch.dict(os.environ, dict(CLEAR, ONE_KEY=ONE, SHARED_KEY=SHARED))
        patch.start()
        self.addCleanup(patch.stop)
        names = ('keys', 'shared_tools', 'pen', 'surfaces', 'policy')
        saved = {name: getattr(server.Config, name) for name in names}
        self.addCleanup(lambda: [setattr(server.Config, name, value) for name, value in saved.items()])

    def configure(self, writers):
        server.configure(self.tmp, PRIMARY, writers)


class ConfigureTests(Base):
    def test_one_agent_keeps_the_1_3_0_shape(self):
        self.configure({'ONE_KEY': 'first'})
        self.assertEqual(server.Config.keys[ONE], ('first',))
        self.assertEqual(listing('first'), server.TOOLS + server.PEN_TOOLS)
        self.assertEqual(listing(('first',)), server.TOOLS + server.PEN_TOOLS)
        self.configure({'ONE_KEY': ['first']})
        self.assertEqual(server.Config.keys[ONE], ('first',))
        self.assertEqual(pen_call('first', 'pen_collections', {}), (False, '[]'))
        self.assertEqual(pen_call('first', 'pen_collections', {'agent': 'first'}), (True, 'unknown argument'))

    def test_bad_grants_are_refused(self):
        for value in ([], ['a', 'a'], [1], ['a', 'A'], 5, None, {'x': 1}):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                self.configure({'ONE_KEY': value})
        with self.assertRaisesRegex(SystemExit, 'different agents'):
            os.environ['SHARED_KEY'] = ONE
            self.configure({'ONE_KEY': ['a', 'b'], 'SHARED_KEY': ['a', 'c']})

    def test_primary_key_none_is_replaceable_and_list_binds(self):
        os.environ['PRIMARY_REF'] = PRIMARY
        self.configure({'PRIMARY_REF': ['first', 'second']})
        self.assertEqual(server.Config.keys[PRIMARY], ('first', 'second'))
        self.assertIn(('first', 'second'), server.Config.shared_tools)

    def test_notebook_setting_precedence(self):
        pooled = server.pooled
        self.assertFalse(pooled({}, 'first', 'legacy', 'silo'))
        self.assertTrue(pooled({'*': {'*': 'pool'}}, 'first', 'legacy', 'silo'))
        silo = {'*': {'*': 'pool', 'journal': 'silo'}, 'first': {'*': 'silo', 'journal': 'pool'}}
        self.assertTrue(pooled(silo, 'first', 'journal', 'silo'))
        self.assertFalse(pooled(silo, 'first', 'other', 'pool'))
        self.assertFalse(pooled(silo, 'second', 'journal', 'pool'))
        self.assertTrue(pooled(silo, 'second', 'other', 'silo'))

    def test_same_agents_in_another_order_keep_the_first_order(self):
        os.environ['SHARED_KEY'] = ONE
        self.configure({'ONE_KEY': ['a', 'b'], 'SHARED_KEY': ['b', 'a']})
        self.assertEqual(server.Config.keys[ONE], ('a', 'b'))


class SharedToolTests(Base):
    def setUp(self):
        super().setUp()
        self.configure({'SHARED_KEY': ['first', 'second']})
        self.binding = server.Config.keys[SHARED]

    def test_listing_requires_an_agent_enum_and_leaves_pen_tools_alone(self):
        before = copy.deepcopy(server.PEN_TOOLS)
        pen = [t for t in listing(self.binding) if t['name'].startswith('pen_')]
        self.assertEqual(len(pen), 7)
        for tool in pen:
            schema = tool['inputSchema']
            self.assertEqual(schema['properties']['agent']['enum'], ['first', 'second'])
            self.assertIn('agent', schema['required'])
        self.assertEqual(server.PEN_TOOLS, before)

    def test_missing_and_ungranted_agents_are_refused(self):
        self.assertEqual(pen_call(self.binding, 'pen_collections', {}), (True, 'agent is required: one of first, second'))
        for bad in ('First', 'third', 1, None):
            with self.subTest(agent=bad):
                self.assertEqual(pen_call(self.binding, 'pen_collections', {'agent': bad}),
                                 (True, 'agent must be one of first, second'))
        self.assertEqual(pen_call(self.binding, 'pen_collections', {'agent': 'first', 'writer': 'x'}), (True, 'unknown argument'))

    def test_agents_stay_separate(self):
        error, text = pen_call(self.binding, 'pen_write', {'agent': 'first', 'collection': 'notes', 'body': 'mine'})
        self.assertFalse(error)
        entry = json.loads(text)['id']
        self.assertEqual(json.loads(pen_call(self.binding, 'pen_collections', {'agent': 'first'})[1])[0]['count'], 1)
        self.assertEqual(pen_call(self.binding, 'pen_collections', {'agent': 'second'}), (False, '[]'))
        self.assertTrue(pen_call(self.binding, 'pen_read', {'agent': 'second', 'id': entry})[0])
        self.assertFalse(pen_call(self.binding, 'pen_read', {'agent': 'first', 'id': entry})[0])

    def test_extraction_only_key_cannot_use_the_pen(self):
        self.assertEqual(pen_call(None, 'pen_collections', {}), (True, 'no writer is assigned to this key'))

    def test_over_http(self):
        http_server = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(http_server.server_close)
        self.addCleanup(http_server.shutdown)

        def post(key, method, params=None):
            conn = http.client.HTTPConnection('127.0.0.1', http_server.server_port)
            try:
                body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}})
                conn.request('POST', '/mcp', body, {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
                return json.loads(conn.getresponse().read())['result']
            finally:
                conn.close()
        tools = post(SHARED, 'tools/list')['tools']
        self.assertTrue(all('agent' in t['inputSchema']['required'] for t in tools if t['name'].startswith('pen_')))
        result = post(SHARED, 'tools/call', {'name': 'pen_write', 'arguments': {'agent': 'second', 'collection': 'c', 'body': 'x'}})
        self.assertFalse(result['isError'])
        listed = post(SHARED, 'tools/call', {'name': 'pen_collections', 'arguments': {'agent': 'first'}})
        self.assertEqual(listed['content'][0]['text'], '[]')
        self.assertTrue(post(SHARED, 'tools/call', {'name': 'pen_collections', 'arguments': {}})['isError'])

    def test_pool_and_silos_over_http(self):
        settings = {
            'SCRIPTORIUM_SILO': json.dumps({'*': {'*': 'silo'}, 'second': {'shown': 'pool'}}),
            'SCRIPTORIUM_SURFACES': json.dumps({'SHARED_KEY': 'desktop'}),
        }
        with mock.patch.dict(os.environ, settings):
            self.configure({'SHARED_KEY': ['first', 'second'], 'ONE_KEY': 'first'})
        http_server = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(http_server.server_close)
        self.addCleanup(http_server.shutdown)

        def call(tool, args, key=SHARED):
            conn = http.client.HTTPConnection('127.0.0.1', http_server.server_port)
            try:
                body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                   'params': {'name': tool, 'arguments': args}})
                conn.request('POST', '/mcp', body, {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
                result = json.loads(conn.getresponse().read())['result']
                text = result['content'][0]['text']
                return text if result['isError'] else json.loads(text)
            finally:
                conn.close()

        hidden = call('pen_write', {'agent': 'second', 'collection': 'hidden', 'body': 'private'})['id']
        shown = call('pen_write', {'agent': 'second', 'collection': 'shown', 'body': 'shared'})['id']
        owner = {'of': 'second'}
        self.assertEqual(call('pen_read', dict(owner, id=shown), ONE)['surface'], 'desktop')
        self.assertEqual(call('pen_history', dict(owner, id=shown), ONE)[0]['id'], shown)
        self.assertEqual(call('pen_list', dict(owner, collection='shown', surface='desktop'), ONE)[0]['id'], shown)
        self.assertEqual([r['collection'] for r in call('pen_collections', owner, ONE)], ['shown'])
        self.assertEqual(call('pen_collections', {}, ONE), [])
        for tool in ('pen_read', 'pen_history'):
            self.assertEqual(call(tool, dict(owner, id=hidden), ONE),
                             call(tool, dict(owner, id='f' * 32), ONE))
        self.assertEqual(call('pen_list', dict(owner, collection='hidden'), ONE),
                         call('pen_list', dict(owner, collection='missing'), ONE))
        self.assertEqual(call('pen_read', dict(owner, agent='second', id=hidden))['body'], 'private')
        self.assertEqual(call('pen_collections', owner), 'agent is required: one of first, second')
        self.assertEqual(call('pen_collections', owner, PRIMARY), 'no writer is assigned to this key')
        self.assertEqual(call('pen_collections', {'of': 'Second'}, ONE), 'unknown agent')
        for tool, args in [('pen_edit', {'id': hidden, 'based_on': 1}), ('pen_delete', {'id': hidden}),
                           ('pen_write', {'collection': 'hidden', 'body': 'foreign'})]:
            self.assertEqual(call(tool, dict(owner, **args), ONE), 'unknown argument')
        self.assertEqual(call('pen_delete', {'id': hidden}, ONE), 'entry not found')
        self.assertEqual(call('pen_edit', {'id': hidden, 'based_on': 1}, ONE), 'entry not found')

        settings['SCRIPTORIUM_READS'] = json.dumps({'first': 'own', 'second': 'own'})
        with mock.patch.dict(os.environ, settings):
            self.configure({'SHARED_KEY': ['first', 'second'], 'ONE_KEY': 'first'})
        self.assertEqual(call('pen_read', dict(owner, id=shown), ONE), 'this agent can only read its own notebooks')
        self.assertEqual(call('pen_read', dict(owner, agent='second', id=hidden))['body'], 'private')
        self.assertEqual(call('pen_read', {'agent': 'second', 'id': hidden})['body'], 'private')
        self.assertEqual(call('pen_collections', {'of': 'first'}, ONE), [])



def clean_env():
    """The caller's environment without any agent setting (removed, not emptied: a set-but-empty one is refused)."""
    return {k: v for k, v in os.environ.items() if k not in CLEAR}


def start_server(case, env_extra):
    env = clean_env()
    env.update(env_extra)
    err = tempfile.TemporaryFile()
    proc = subprocess.Popen([sys.executable, str(ROOT / 'mcp' / 'server.py'), '--root', case.tmp, '--port', '0'],
                            env=env, stdout=subprocess.PIPE, stderr=err, text=True)
    lines = queue.Queue()
    reader = threading.Thread(target=lambda: [lines.put(ln) for ln in proc.stdout], daemon=True)
    reader.start()

    def stop():
        proc.terminate()
        proc.wait(10)
        reader.join(10)
        proc.stdout.close()
        err.close()
    case.addCleanup(stop)
    return proc, lines


class ProcessTests(Base):
    def run_refusal(self, env_extra):
        env = dict(clean_env(), SCRIPTORIUM_MCP_KEY=PRIMARY)
        env.update(env_extra)
        done = subprocess.run([sys.executable, str(ROOT / 'mcp' / 'server.py'), '--root', self.tmp, '--port', '0'],
                              env=env, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(done.returncode, 0)
        return done.stderr

    def test_not_both_and_non_objects(self):
        self.assertIn('not both', self.run_refusal({'SCRIPTORIUM_AGENTS': '{}', 'MEMORY_ENTRY_WRITERS': '{}'}))
        self.assertIn('not both', self.run_refusal({'SCRIPTORIUM_AGENTS': '{}', 'SCRIPTORIUM_WRITERS': '{}'}))
        for raw in ('null', '[]', '"x"', '5', '{bad'):
            with self.subTest(raw=raw):
                self.assertIn('must be a JSON object', self.run_refusal({'SCRIPTORIUM_AGENTS': raw}))

    def test_set_but_empty_settings_are_refused(self):
        self.assertIn('not both', self.run_refusal({'SCRIPTORIUM_AGENTS': '{}', 'SCRIPTORIUM_WRITERS': ''}))
        self.assertIn('not both', self.run_refusal({'SCRIPTORIUM_AGENTS': '{}', 'MEMORY_ENTRY_WRITERS': ''}))
        self.assertIn('must be a JSON object', self.run_refusal({'SCRIPTORIUM_AGENTS': ''}))

    def test_opening_needs_agents_and_known_names(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(setup_agents.main(['--opening', 'Solo=Solo:', '--out', os.path.join(self.tmp, 'a.json')]), 2)
            self.assertEqual(setup_agents.main(['--agents', 'Mira,Echo', '--opening', 'Mra=Mira:',
                                                '--out', os.path.join(self.tmp, 'b.json')]), 2)
        self.assertIn('--opening needs --agents', err.getvalue())
        self.assertIn('not in --agents: Mra', err.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'a.json')))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'b.json')))

    def test_docs_make_the_folder_first(self):
        text = (ROOT / 'docs' / 'agents.md').read_text(encoding='utf-8')
        self.assertIn('mkdir -p "$HOME/scriptorium"', text)
        self.assertLess(text.index('mkdir -p'), text.index('--out "$HOME/scriptorium/agents.json"'))

    def test_legacy_variable_accepts_lists(self):
        self.configure(json.loads('{"SHARED_KEY":["first","second"]}'))
        self.assertEqual(server.Config.keys[SHARED], ('first', 'second'))

    def test_real_server_on_port_zero(self):
        proc, lines = start_server(self, {'SCRIPTORIUM_MCP_KEY': PRIMARY, 'SHARED_KEY': SHARED,
                                          'SCRIPTORIUM_AGENTS': json.dumps({'SHARED_KEY': ['first', 'second']})})
        deadline = time.time() + 20
        port = None
        while time.time() < deadline and port is None:
            try:
                m = re.search(r'127\.0\.0\.1:(\d+)/mcp', lines.get(timeout=0.5))
                port = int(m.group(1)) if m else None
            except queue.Empty:
                self.assertIsNone(proc.poll(), 'server exited early')
        self.assertIsNotNone(port, 'server did not print its port')
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
        try:
            body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                               'params': {'name': 'pen_collections', 'arguments': {'agent': 'first'}}})
            conn.request('POST', '/mcp', body, {'Authorization': 'Bearer ' + SHARED, 'Content-Type': 'application/json'})
            self.assertFalse(json.loads(conn.getresponse().read())['result']['isError'])
        finally:
            conn.close()


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = os.path.join(self.tmp, 'agents.json')

    def run_setup(self, *argv, answers=()):
        replies = iter(answers)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = setup_agents.main(list(argv), input=lambda prompt='': next(replies))
        return code, out.getvalue(), err.getvalue()

    def settings(self):
        with open(self.out, encoding='utf-8') as f:
            return json.load(f)

    def test_non_interactive_shared_and_separate(self):
        code, out, _ = self.run_setup('--agents', 'Mira,Echo,Zed', '--shared', 'Mira,Echo', '--out', self.out,
                                      '--opening', 'Mira=Mira:')
        self.assertEqual(code, 0)
        data = self.settings()
        self.assertEqual(data['keys'], {'SCRIPTORIUM_MCP_KEY': ['Mira', 'Echo'], 'SCRIPTORIUM_AGENT_KEY_3': ['Zed']})
        self.assertEqual(data['agents'][0], {'name': 'Mira', 'opening': 'Mira:'})
        self.assertIn('every agent on it', out)
        self.assertEqual([f for f in os.listdir(self.tmp) if f.startswith('.setup-agents-')], [])

    def test_interactive_and_one_agent(self):
        code, _, _ = self.run_setup('--out', self.out, answers=['1', 'Solo'])
        self.assertEqual((code, self.settings()['keys']), (0, {'SCRIPTORIUM_MCP_KEY': ['Solo']}))
        code, _, _ = self.run_setup('--out', self.out, '--force', answers=['2', 'A, B', 'A,B', 'A:', ''])
        self.assertEqual(code, 0)
        self.assertEqual(self.settings()['agents'], [{'name': 'A', 'opening': 'A:'}, {'name': 'B', 'opening': None}])

    def test_no_shared_group_leaves_primary_unbound(self):
        self.run_setup('--agents', 'A,B', '--out', self.out)
        self.assertEqual(self.settings()['keys'], {'SCRIPTORIUM_AGENT_KEY_1': ['A'], 'SCRIPTORIUM_AGENT_KEY_2': ['B']})

    def test_refusals_and_no_overwrite(self):
        for argv in (['--agents', 'A,a'], ['--agents', 'A,B', '--shared', 'A'], ['--agents', 'A,B', '--shared', 'C,A'],
                     ['--agents', 'A/B'], ['--agents', 'A,B', '--opening', 'nope']):
            with self.subTest(argv=argv):
                self.assertEqual(self.run_setup(*argv, '--out', self.out)[0], 2)
        self.assertFalse(os.path.exists(self.out))
        self.assertEqual(self.run_setup('--agents', 'A', '--out', self.out)[0], 0)
        with open(self.out, encoding='utf-8') as f:
            before = f.read()
        code, _, err = self.run_setup('--agents', 'B', '--out', self.out)
        self.assertEqual(code, 2)
        self.assertIn('--force', err)
        with open(self.out, encoding='utf-8') as f:
            self.assertEqual(f.read(), before)
        self.assertEqual([f for f in os.listdir(self.tmp) if f.startswith('.setup-agents-')], [])

    def test_print_agents(self):
        self.run_setup('--agents', 'A,B', '--shared', 'A,B', '--out', self.out)
        code, out, _ = self.run_setup('--print-agents', self.out)
        self.assertEqual((code, json.loads(out)), (0, {'SCRIPTORIUM_MCP_KEY': ['A', 'B']}))
        with open(self.out, 'w', encoding='utf-8') as f:
            f.write('{"keys": {"X": []}}')
        self.assertEqual(self.run_setup('--print-agents', self.out)[0], 2)
        self.assertEqual(self.run_setup('--print-agents', self.out + '.missing')[0], 2)


class DocsBlockTests(unittest.TestCase):
    def block(self):
        text = (ROOT / 'docs' / 'agents.md').read_text(encoding='utf-8')
        return re.search(r'```sh\n(unset SCRIPTORIUM_AGENTS.*?)```', text, re.S).group(1)

    def test_launch_block_parses_and_runs_the_capture(self):
        block = self.block()
        self.assertNotIn("'$(", block)
        for shell in ('sh', 'zsh'):
            if shutil.which(shell):
                with self.subTest(shell=shell):
                    done = subprocess.run([shell, '-n'], input=block, capture_output=True, text=True, timeout=30)
                    self.assertEqual((done.returncode, done.stderr), (0, ''))
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        folder = Path(tmp) / 'scriptorium'
        folder.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(setup_agents.main(['--agents', 'A,B', '--shared', 'A,B', '--out', str(folder / 'agents.json')]), 0)
        capture = [ln for ln in block.splitlines() if ln.startswith('SCRIPTORIUM_AGENTS=')][0]
        env = dict(os.environ, MEMORY_ENTRY_WRITERS='{}', HOME=tmp)
        done = subprocess.run(['/bin/sh', '-c', capture + ' && export SCRIPTORIUM_AGENTS && printf %s "$SCRIPTORIUM_AGENTS"'],
                              cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(json.loads(done.stdout), {'SCRIPTORIUM_MCP_KEY': ['A', 'B']})


class CountToolsTests(unittest.TestCase):
    def test_prints_all_three_rows(self):
        done = subprocess.run([sys.executable, str(ROOT / 'mcp' / 'count_tools.py')], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        for row in ('one agent', 'extraction only', 'two agents'):
            self.assertIn('| ' + row + ' |', done.stdout)
        self.assertNotIn('refused', done.stdout)


if __name__ == '__main__':
    unittest.main()
