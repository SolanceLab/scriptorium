# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The Cloudflare entry's logic (cloud/entry.py) without Cloudflare: keys, agents, request ids, pen tools only."""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'mcp'), str(ROOT / 'engine')]
import server
from engine.cloud_pen import SqlPenStore, Sqlite3Sql
spec = importlib.util.spec_from_file_location('cloud_entry', ROOT / 'cloud' / 'entry.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)

MAIN, A_KEY, B_KEY = 'm' * 20, 'a' * 20, 'b' * 20


def env(**values):
    return values.get


def rpc(method, params=None, id=1):
    msg = {'jsonrpc': '2.0', 'id': id, 'method': method}
    if params is not None:
        msg['params'] = params
    return msg


def call(name, arguments, id=1):
    return json.dumps(rpc('tools/call', {'name': name, 'arguments': arguments}, id))


class KeyTests(unittest.TestCase):
    def setUp(self):
        self.get = env(SCRIPTORIUM_MCP_KEY=MAIN, A=A_KEY, B=B_KEY, SCRIPTORIUM_AGENTS=json.dumps({'A': 'alpha', 'B': ['beta', 'gamma']}))

    def admit(self, get=None, method='POST', path='/mcp', key=None, content='application/json', request_id=None):
        headers = {'authorization': 'Bearer ' + (key or A_KEY), 'content-type': content}
        if request_id is not None:
            headers['x-request-id'] = request_id
        return entry.admit(get or self.get, method, path, headers.get)

    def test_a_missing_or_short_secret_means_nothing_is_served(self):
        for get in (env(), env(SCRIPTORIUM_MCP_KEY='short')):
            status, message = self.admit(get)
            self.assertEqual(status, 503)
            self.assertIn('SCRIPTORIUM_MCP_KEY', message)
        status, _ = self.admit(env(SCRIPTORIUM_MCP_KEY=MAIN, SCRIPTORIUM_AGENTS=json.dumps({'A': 'alpha'}), A='tiny'))
        self.assertEqual(status, 503)

    def test_invalid_pool_and_surface_settings_are_refused_on_both_servers(self):
        base = {entry.KEY_ENV: MAIN, 'A': A_KEY, 'ALIAS': A_KEY,
                entry.AGENTS_ENV: json.dumps({'A': 'alpha', 'ALIAS': 'alpha'})}
        cases = [
            ('SCRIPTORIUM_SILO', []),
            ('SCRIPTORIUM_SILO', {'ghost': {'*': 'silo'}}),
            ('SCRIPTORIUM_SILO', {'alpha': 'pool'}),
            ('SCRIPTORIUM_SILO', {'alpha': {'notes': 'default'}}),
            ('SCRIPTORIUM_SILO', {'alpha': {'Notes': 'silo', 'notes': 'pool'}}),
            ('SCRIPTORIUM_READS', {'ghost': 'own'}),
            ('SCRIPTORIUM_READS', {'alpha': 'silo'}),
            ('SCRIPTORIUM_SURFACES', {'UNKNOWN': 'web'}),
            ('SCRIPTORIUM_SURFACES', {'A': 4}),
            ('SCRIPTORIUM_SURFACES', {'A': ''}),
            ('SCRIPTORIUM_SURFACES', {'A': 'web', 'ALIAS': 'desktop'}),
            ('SCRIPTORIUM_SURFACES', {'A': 'web'}),
        ]
        for name, value in cases:
            with self.subTest(name=name, value=value), tempfile.TemporaryDirectory() as tmp:
                values = dict(base, **{name: json.dumps(value)})
                self.assertEqual(self.admit(values.get)[0], 503)
                with mock.patch.dict(os.environ, values), self.assertRaises(SystemExit):
                    server.configure(tmp, MAIN, json.loads(base[entry.AGENTS_ENV]))
        valid = dict(base, SCRIPTORIUM_SURFACES=json.dumps({'A': 'web', 'ALIAS': 'web'}))
        keys, problem = entry.load_keys(valid.get, with_policy=True)
        self.assertIsNone(problem)
        self.assertEqual(keys[A_KEY][:2], (('alpha',), 'web'))

    def test_wrong_and_missing_keys_are_refused_and_right_ones_pass(self):
        self.assertEqual(self.admit(key='x' * 20)[0], 401)
        self.assertEqual(entry.admit(self.get, 'POST', '/mcp', {'content-type': 'application/json'}.get)[0], 401)
        self.assertEqual(self.admit(key=A_KEY[:-1])[0], 401)
        self.assertEqual(self.admit(key=A_KEY)[0], None)
        self.assertEqual(self.admit(method='GET')[0], 405)
        self.assertEqual(self.admit(path='/other')[0], 404)
        self.assertEqual(self.admit(content='text/plain')[0], 415)
        self.assertEqual(self.admit(request_id='x' * 201)[0], 400)

    def test_keys_map_to_their_agents(self):
        self.assertEqual(self.admit(key=A_KEY), (None, ('alpha',)))
        self.assertEqual(self.admit(key=B_KEY), (None, ('beta', 'gamma')))
        self.assertEqual(self.admit(key=MAIN), (None, None))

    def test_the_agent_rules_match_the_local_server(self):
        cases = {
            'one key on two agent sets': ({'A': 'alpha', 'B': 'beta'}, {'B': A_KEY}),
            'an agent listed twice': ({'A': ['alpha', 'alpha']}, {}),
            'names that differ by case': ({'A': 'Alpha', 'B': 'alpha'}, {}),
            'a bad name': ({'A': '../x'}, {}),
            'an empty list': ({'A': []}, {}),
            'not an object': (['A'], {}),
        }
        for label, (agents, secrets) in cases.items():
            with self.subTest(label):
                values = dict(SCRIPTORIUM_MCP_KEY=MAIN, A=A_KEY, B=B_KEY, SCRIPTORIUM_AGENTS=json.dumps(agents))
                values.update(secrets)
                self.assertEqual(entry.load_keys(env(**values))[0], {})
                with mock.patch.dict('os.environ', {'A': values['A'], 'B': values['B']}), \
                        tempfile.TemporaryDirectory() as tmp, self.assertRaises(SystemExit):
                    server.configure(tmp, MAIN, agents)

    def test_renaming_adding_or_replacing_a_key_never_moves_or_hides_entries(self):
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        store = SqlPenStore(Sqlite3Sql(connection))    # the one store of the deployment

        def go(refs, key, body, request_id=''):
            values = dict(SCRIPTORIUM_MCP_KEY=MAIN, SCRIPTORIUM_AGENTS=json.dumps({ref: 'alpha' for ref in refs}))
            values.update({ref: key for ref in refs})
            refused, agents = entry.admit(env(**values), 'POST', '/mcp',
                                          {'authorization': 'Bearer ' + key, 'content-type': 'application/json'}.get)
            self.assertIsNone(refused)
            return json.loads(entry.serve(store, body, agents, request_id)[1])
        write = call('pen_write', {'collection': 'notes', 'body': 'kept'})
        go(['B'], A_KEY, write, 'req-1')
        for refs, key in ((['A'], A_KEY),                # renamed reference
                          (['A', 'B'], A_KEY),           # a second reference with the same key
                          (['C'], B_KEY)):               # a replaced key value
            listed = go(refs, key, call('pen_list', {'collection': 'notes'}))
            self.assertIn('kept', listed['result']['content'][0]['text'])
            go(refs, key, write, 'req-1')    # the saved request id replays: no second write
            self.assertEqual(len(store.export_pen('alpha')), 1)

    def test_the_main_key_can_be_bound_to_an_agent_itself(self):
        keys, problem = entry.load_keys(env(SCRIPTORIUM_MCP_KEY=MAIN, SCRIPTORIUM_AGENTS=json.dumps({'SCRIPTORIUM_MCP_KEY': 'alpha'})))
        self.assertIsNone(problem)
        self.assertEqual(keys, {MAIN: ('alpha',)})

    def test_one_key_can_serve_one_agent_or_a_list(self):
        keys, problem = entry.load_keys(self.get)
        self.assertIsNone(problem)
        self.assertEqual(keys, {MAIN: None, A_KEY: ('alpha',), B_KEY: ('beta', 'gamma')})


class ServeTests(unittest.TestCase):
    def setUp(self):
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        self.store = SqlPenStore(Sqlite3Sql(connection))

    def serve(self, body, agents=('alpha',), request_id=''):
        status, text = entry.serve(self.store, body, agents, request_id)
        return status, (json.loads(text) if text else None)

    def assertRpc(self, response, rid):
        self.assertEqual(response['jsonrpc'], '2.0')
        self.assertEqual(response['id'], rid)
        self.assertTrue('result' in response or 'error' in response)

    def test_every_replayed_or_refused_reply_is_json_rpc_with_the_request_id(self):
        body = call('pen_write', {'collection': 'notes', 'body': 'x'}, 7)
        first = self.serve(body, request_id='r')
        self.assertRpc(first[1], 7)
        replay = self.serve(body, request_id='r')
        self.assertEqual(replay, first)
        self.assertRpc(self.serve(call('pen_write', {'collection': 'notes', 'body': 'y'}, 8), request_id='r')[1], 8)

    def test_integer_fractional_and_string_ids_are_echoed_on_every_path(self):
        for rid in (7, 1.5, 'abc-1', 0):
            with self.subTest(rid=rid):
                body = call('pen_write', {'collection': 'notes', 'body': f'x{rid}'}, rid)
                first = self.serve(body, request_id=f'r{rid}')
                self.assertEqual(first[1]['id'], rid)
                self.assertIs(type(first[1]['id']), type(rid))
                replay = self.serve(body, request_id=f'r{rid}')
                self.assertEqual(replay, first)
                self.assertIs(type(replay[1]['id']), type(rid))
                other = call('pen_write', {'collection': 'notes', 'body': 'other'}, rid)
                status, conflict = self.serve(other, request_id=f'r{rid}')
                self.assertEqual(status, 409)
                self.assertEqual(conflict['id'], rid)
                self.assertIs(type(conflict['id']), type(rid))
                big = call('pen_write', {'collection': 'notes', 'body': 'z' * 1_000_100}, rid)
                status, oversized = self.serve(big, request_id='big')
                self.assertEqual(status, 413)
                self.assertEqual(oversized['id'], rid)
                self.assertIs(type(oversized['id']), type(rid))

    def test_one_request_id_used_as_another_agent_is_refused_and_writes_once(self):
        agents = ('alpha', 'beta')
        status, _ = self.serve(call('pen_write', {'collection': 'notes', 'body': 'x', 'agent': 'alpha'}), agents=agents, request_id='r')
        self.assertEqual(status, 200)
        status, refused = self.serve(call('pen_write', {'collection': 'notes', 'body': 'x', 'agent': 'beta'}), agents=agents, request_id='r')
        self.assertEqual(status, 409)
        self.assertRpc(refused, 1)
        self.assertEqual(len(self.store.export_pen('alpha')) + len(self.store.export_pen('beta')), 1)

    def test_actorless_requests_do_not_reserve_request_ids(self):
        first = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
        other = json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'initialize', 'params': {}})
        self.assertEqual(self.serve(first, agents=('alpha',), request_id='same')[0], 200)
        self.assertEqual(self.serve(other, agents=('beta',), request_id='same')[0], 200)
        self.assertEqual(self.serve(first, agents=('alpha',), request_id='same')[0], 200)
        self.assertEqual(self.store.adapter.run("SELECT name FROM sqlite_master WHERE name = 'cloud_requests'"), [])

    def test_a_request_without_a_parseable_id_is_answered_with_null(self):
        for body in ('{not json', '[]', json.dumps([json.loads(call('pen_list', {'collection': 'notes'}))]), '"text"'):
            self.assertIsNone(entry._request_rpc_id(body))
        self.assertIsNone(self.serve('{not json', request_id='r')[1]['id'])
        self.assertIs(entry._request_rpc_id('{"id": true}'), None)

    def tool_result(self, response):
        return json.loads(response['result']['content'][0]['text'])

    def test_a_pen_tool_writes_and_reads_through_the_unchanged_handler(self):
        status, response = self.serve(call('pen_write', {'collection': 'notes', 'body': 'hello'}))
        self.assertEqual(status, 200)
        id = self.tool_result(response)['id']
        _, response = self.serve(call('pen_read', {'id': id}))
        self.assertEqual(self.tool_result(response)['body'], 'hello')
        self.assertEqual(self.store.read('alpha', id)['writer'], 'alpha')

    def test_only_pen_tools_are_listed(self):
        _, response = self.serve(json.dumps(rpc('tools/list')))
        names = {t['name'] for t in response['result']['tools']}
        self.assertEqual(names, entry.PEN_NAMES)
        self.assertEqual(len(names), 6)
        _, response = self.serve(json.dumps(rpc('tools/list')), agents=('beta', 'gamma'))
        self.assertEqual({t['name'] for t in response['result']['tools']}, entry.PEN_NAMES)
        self.assertTrue(all('agent' in t['inputSchema']['properties'] for t in response['result']['tools']))

    def test_chat_tools_are_not_callable(self):
        for name in sorted(entry.CHAT_NAMES):
            with self.subTest(name=name), mock.patch.object(server.subprocess, 'run') as run:
                _, response = self.serve(call(name, {'export': 'x.json', 'out': 'o', 'memories': 'm'}))
                self.assertEqual(response['error']['code'], -32602)
                run.assert_not_called()
        status, batch = self.serve(json.dumps([json.loads(call('write_notes', {}, 1)), rpc('tools/list', id=2)]))
        self.assertEqual(status, 400)
        self.assertRpc(batch, None)
        self.assertEqual(batch['error']['code'], -32600)

    def test_a_key_without_an_agent_cannot_use_the_pen(self):
        _, response = self.serve(call('pen_write', {'collection': 'notes', 'body': 'x'}), agents=None)
        self.assertTrue(response['result']['isError'])
        self.assertEqual(self.store.export_pen('alpha'), [])

    def test_a_repeated_request_id_returns_the_saved_answer_and_writes_once(self):
        body = call('pen_write', {'collection': 'notes', 'body': 'once'})
        first = self.serve(body, request_id='req-1')
        again = self.serve(body, request_id='req-1')
        self.assertEqual(first, again)
        self.assertEqual(len(self.store.export_pen('alpha')), 1)
        other = self.serve(body, request_id='req-2')
        self.assertNotEqual(self.tool_result(other[1])['id'], self.tool_result(first[1])['id'])
        self.assertEqual(len(self.store.export_pen('alpha')), 2)
        no_id = self.serve(body)
        self.assertEqual(len(self.store.export_pen('alpha')), 3)
        self.assertEqual(no_id[0], 200)

    def test_the_same_request_id_with_another_body_is_refused(self):
        self.serve(call('pen_write', {'collection': 'notes', 'body': 'one'}), request_id='req-1')
        status, response = self.serve(call('pen_write', {'collection': 'notes', 'body': 'two'}), request_id='req-1')
        self.assertEqual(status, 409)
        self.assertRpc(response, 1)
        self.assertIn('X-Request-Id', response['error']['message'])
        self.assertEqual(len(self.store.export_pen('alpha')), 1)

    def test_failures_are_not_saved(self):
        bad = call('pen_edit', {'id': 'f' * 32, 'based_on': 1, 'body': 'missing'})
        _, first = self.serve(bad, request_id='bad')
        self.assertTrue(first['result']['isError'])
        self.assertEqual(self.store.adapter.run('SELECT COUNT(*) AS n FROM cloud_requests WHERE rid = ?', ('bad',))[0]['n'], 0)

    def test_a_retry_still_matches_after_the_keys_agent_list_grows(self):
        body = call('pen_write', {'collection': 'notes', 'body': 'x', 'agent': 'alpha'})
        self.serve(body, agents=('alpha', 'beta'), request_id='r')
        self.serve(body, agents=('alpha', 'beta', 'gamma'), request_id='r')
        self.assertEqual(len(self.store.export_pen('alpha')), 1)

    def test_an_answer_too_big_to_keep_still_succeeds_and_a_retry_does_not_write_again(self):
        body = call('pen_write', {'collection': 'notes', 'body': 'x'})
        with mock.patch.object(entry, 'MAX_SAVED', 10):
            first = self.serve(body, request_id='big')
            again = self.serve(body, request_id='big')
        self.assertEqual(first[0], 200)
        self.assertEqual(len(self.store.export_pen('alpha')), 1)
        self.assertRpc(again[1], 1)    # the retry is refused or replayed, always as a JSON-RPC object carrying the id

    def test_a_batch_with_an_unauthorized_agent_is_refused_on_every_retry(self):
        agents = ('alpha', 'beta')
        batch = json.dumps([json.loads(call('pen_write', {'collection': 'notes', 'body': 'x', 'agent': 'alpha'}, 1)),
                            json.loads(call('pen_write', {'collection': 'notes', 'body': 'y', 'agent': 'gamma'}, 2))])
        first = self.serve(batch, agents=agents, request_id='b2')
        self.assertEqual(first[0], 400)
        self.assertRpc(first[1], None)
        self.assertEqual(first[1]['error']['code'], -32600)
        self.assertEqual(self.serve(batch, agents=agents, request_id='b2'), first)
        self.assertEqual(self.store.export_pen('alpha'), [])
        self.assertEqual(self.store.export_pen('gamma'), [])

    def test_a_batch_with_valid_and_invalid_calls_neither_writes_nor_reserves_an_id(self):
        batch = json.dumps([json.loads(call('pen_write', {'collection': 'notes', 'body': 'x'}, 1)),
                            json.loads(call('pen_read', {'id': 'f' * 32}, 2))])
        first = self.serve(batch, request_id='b1')
        again = self.serve(batch, request_id='b1')
        self.assertEqual(first, again)
        self.assertEqual(first[0], 400)
        self.assertRpc(first[1], None)
        self.assertEqual(first[1]['error']['code'], -32600)
        self.assertEqual(self.store.export_pen('alpha'), [])
        self.assertEqual(self.serve(call('pen_write', {'collection': 'notes', 'body': 'x'}), request_id='b1')[0], 200)
        self.assertEqual(len(self.store.export_pen('alpha')), 1)

    def test_a_saved_answer_is_shown_only_while_its_agent_is_still_on_the_key(self):
        body = call('pen_write', {'collection': 'notes', 'body': 'x', 'agent': 'beta'})
        first = self.serve(body, agents=('alpha', 'beta'), request_id='r')
        self.assertEqual(first[0], 200)
        status, response = self.serve(body, agents=('alpha',), request_id='r')    # beta was removed from the key
        self.assertEqual(status, 403)
        self.assertNotIn('result', response)
        self.assertRpc(response, 1)
        self.assertEqual(self.serve(body, agents=None, request_id='r')[0], 403)
        self.assertEqual(self.serve(body, agents=('beta', 'gamma'), request_id='r'), first)
        self.assertEqual(len(self.store.export_pen('beta')), 1)

    def test_a_key_cannot_probe_another_agents_saved_request(self):
        secret = call('pen_write', {'collection': 'notes', 'body': 'private', 'agent': 'beta'})
        self.assertEqual(self.serve(secret, agents=('beta', 'gamma'), request_id='r')[0], 200)
        guess = call('pen_write', {'collection': 'notes', 'body': 'wrong guess', 'agent': 'beta'})
        right, wrong = (self.serve(b, agents=('alpha', 'delta'), request_id='r') for b in (secret, guess))
        self.assertEqual(right[0], 403)
        self.assertEqual(right, wrong)    # same answer whether or not the guess matched
        self.assertEqual(len(self.store.export_pen('beta')), 1)

    def test_a_huge_body_is_refused_without_being_read(self):
        status, response = self.serve(call('pen_write', {'collection': 'notes', 'body': 'z' * (3 * server.MAX_BODY)}, 7))
        self.assertEqual(status, 413)
        self.assertRpc(response, None)

    def test_a_failed_retry_record_rolls_the_write_back_so_a_retry_writes_once(self):
        body = call('pen_write', {'collection': 'notes', 'body': 'x'})
        real = self.store.adapter.run

        def run(query, params=()):
            if query.startswith('INSERT INTO cloud_requests'):
                raise OSError('storage failed: injected')
            return real(query, params)
        with mock.patch.object(self.store.adapter, 'run', run):
            with self.assertRaises(OSError):
                self.serve(body, request_id='r')
        self.assertEqual(self.store.export_pen('alpha'), [])    # nothing was kept
        self.assertEqual(self.serve(body, request_id='r')[0], 200)
        self.assertEqual(self.serve(body, request_id='r')[0], 200)
        self.assertEqual(len(self.store.export_pen('alpha')), 1)

    def test_failed_first_entry_rolls_back_collection_metadata(self):
        body = call('pen_write', {'collection': 'notes', 'body': 'x'})
        real = self.store.adapter.run

        def fail_entry(query, params=()):
            if query.startswith('INSERT INTO pen_versions'):
                raise OSError('injected entry failure')
            return real(query, params)

        with mock.patch.object(self.store.adapter, 'run', fail_entry):
            with self.assertRaisesRegex(OSError, 'injected entry failure'):
                self.serve(body, request_id='first')
        self.assertEqual(self.store.export_snapshot('alpha'), {'version': 1, 'collections': {}, 'entries': []})
        self.assertEqual(self.serve(body, request_id='first')[0], 200)
        self.assertEqual(self.store.export_snapshot('alpha')['collections'], {'notes': 'pool'})

    def test_canonical_retry_ignores_json_order_formatting_and_rpc_id(self):
        args = {'collection': 'notes', 'body': 'one'}
        first = self.serve(call('pen_write', args, 3), request_id='canonical')
        reordered = json.dumps(rpc('tools/call', {'arguments': args, 'name': 'pen_write'}, 4.5),
                               indent=4, sort_keys=True)
        self.assertEqual(self.serve(reordered, request_id='canonical'),
                         (first[0], dict(first[1], id=4.5)))
        self.assertEqual(len(self.store.export_pen('alpha')), 1)

    def test_reads_with_request_ids_always_return_current_data(self):
        args = {'collection': 'notes'}
        read = call('pen_list', args)
        self.assertEqual(self.tool_result(self.serve(read, request_id='shared')[1]), [])
        self.serve(call('pen_write', dict(args, body='fresh')), request_id='shared')
        status, response = self.serve(read, request_id='shared')
        self.assertEqual(status, 200)
        self.assertEqual([record['body'] for record in self.tool_result(response)], ['fresh'])
        self.assertEqual(self.store.adapter.run("SELECT rid FROM cloud_requests"), [{'rid': 'shared'}])

    def test_edit_and_delete_replay_and_a_different_tool_conflicts(self):
        written = self.store.write('alpha', 'notes', 'initial')
        args = {'id': written, 'based_on': 1, 'body': 'edited'}
        edit = call('pen_edit', args)
        first = self.serve(edit, request_id='edit')
        self.assertEqual(self.tool_result(first[1]), {'version': 2})
        self.assertEqual(self.serve(edit, request_id='edit'), first)
        self.assertEqual(len(self.store.history('alpha', written)), 2)
        self.assertEqual(self.serve(call('pen_delete', args), request_id='edit')[0], 409)
        delete = call('pen_delete', {'id': written})
        first = self.serve(delete, request_id='delete')
        self.assertEqual(self.tool_result(first[1]), {'ok': True})
        self.assertEqual(self.serve(delete, request_id='delete'), first)
        self.assertEqual(self.store.list('alpha', 'notes'), [])

    def test_expiry_keeps_the_boundary_and_purges_on_a_write_without_an_id(self):
        body = call('pen_write', {'collection': 'notes', 'body': 'x'})
        with mock.patch.object(entry.time, 'time', return_value=100):
            first = self.serve(body, request_id='expiring')
        with mock.patch.object(entry.time, 'time', return_value=100 + 24 * 60 * 60):
            self.assertEqual(self.serve(body, request_id='expiring'), first)
        with mock.patch.object(entry.time, 'time', return_value=101 + 24 * 60 * 60):
            self.serve(body)
            self.assertEqual(self.store.adapter.run("SELECT rid FROM cloud_requests"), [])
            again = self.serve(body, request_id='expiring')
            self.assertNotEqual(self.tool_result(first[1]), self.tool_result(again[1]))
        self.assertEqual(len(self.store.export_pen('alpha')), 3)

    def test_bad_bodies(self):
        self.assertEqual(self.serve('{nope')[0], 400)
        self.assertEqual(self.serve('[]')[0], 400)
        status, response = self.serve('x' * (server.MAX_BODY + 1))
        self.assertEqual(status, 413)
        self.assertRpc(response, None)
        self.assertEqual(self.serve(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}))[0], 202)
        _, response = self.serve(json.dumps(rpc('initialize', {})))
        self.assertEqual(response['result']['serverInfo']['name'], 'scriptorium')


class HttpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        self.pen = entry.PenObject.__new__(entry.PenObject)
        self.pen.store = SqlPenStore(Sqlite3Sql(connection))
        self.binding = mock.Mock()
        self.binding.getByName.return_value = self.pen
        self.worker = entry.Default()
        self.worker.env = SimpleNamespace(SCRIPTORIUM_MCP_KEY=MAIN, A=A_KEY, B=B_KEY,
                                          SCRIPTORIUM_AGENTS=json.dumps({'A': 'alpha'}), PEN=self.binding)
        response = mock.patch.object(entry, 'Response',
                                     lambda body, *, status, headers=None: SimpleNamespace(
                                         body=body, status=status, headers=headers))
        response.start()
        self.addCleanup(response.stop)

    async def fetch(self, body, *, key=A_KEY, method='POST', path='/mcp', request_id=None):
        headers = {'content-type': 'application/json'}
        if key is not None:
            headers['authorization'] = 'Bearer ' + key
        if request_id is not None:
            headers['x-request-id'] = request_id
        request = SimpleNamespace(method=method, url='https://example.test' + path,
                                  headers=headers, text=mock.AsyncMock(return_value=body))
        return await self.worker.fetch(request)

    async def test_only_post_mcp_is_admitted_and_wrong_keys_have_no_body(self):
        for key in (None, 'wrong'):
            result = await self.fetch('invalid JSON', key=key)
            self.assertEqual((result.status, result.body), (401, None))
            self.assertEqual(result.headers, {"www-authenticate": "Bearer"})
        self.assertEqual((await self.fetch('{}', method='GET')).status, 405)
        self.assertEqual((await self.fetch('{}', path='/other')).status, 404)
        self.binding.getByName.assert_not_called()

    async def test_batches_are_refused_and_notifications_have_no_body(self):
        write = call('pen_write', {'collection': 'notes', 'body': 'never'})
        batch = await self.fetch('[' + write + ']', request_id='batch')
        self.assertEqual(batch.status, 400)
        self.assertEqual(json.loads(batch.body)['error']['code'], -32600)
        self.assertIsNone(json.loads(batch.body)['id'])
        notification = json.loads(write)
        del notification['id']
        result = await self.fetch(json.dumps(notification), request_id='notification')
        self.assertEqual((result.status, result.body), (202, None))
        self.assertEqual(self.pen.store.export_pen('alpha'), [])
        self.assertEqual(self.pen.store.adapter.run("SELECT name FROM sqlite_master WHERE name = 'cloud_requests'"), [])

    async def test_initialize_tools_list_and_pen_call_share_the_server_protocol(self):
        for protocol in (*server.PROTOCOLS, 'unsupported'):
            request = rpc('initialize', {'protocolVersion': protocol}, id='init')
            response = await self.fetch(json.dumps(request))
            expected = protocol if protocol in server.PROTOCOLS else server.PROTOCOLS[0]
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.body)['result']['protocolVersion'], expected)
            self.assertEqual(json.loads(response.body)['id'], 'init')
        response = await self.fetch(json.dumps(rpc('tools/list', id=1.5)))
        answer = json.loads(response.body)
        self.assertEqual(response.status, 200)
        self.assertEqual(answer['id'], 1.5)
        self.assertEqual({t['name'] for t in answer['result']['tools']}, entry.PEN_NAMES)
        response = await self.fetch(call('pen_write', {'collection': 'notes', 'body': 'hello'}, id=0))
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.body)['id'], 0)
        self.assertEqual(self.pen.store.list('alpha', 'notes')[0]['body'], 'hello')
        self.binding.getByName.assert_called_with('pen')

    async def test_pool_reads_and_surface_retries_at_worker_boundary(self):
        self.worker.env.SHARED = 's' * 20
        self.worker.env.SCRIPTORIUM_AGENTS = json.dumps({'A': 'alpha', 'B': 'alpha',
                                                        'SHARED': ['alpha', 'beta']})
        self.worker.env.SCRIPTORIUM_SURFACES = json.dumps({'A': 'desktop', 'SHARED': 'web'})
        self.worker.env.SCRIPTORIUM_SILO = json.dumps({'*': {'*': 'pool'}, 'beta': {'hidden': 'silo'}})
        self.worker.env.SCRIPTORIUM_HISTORY = 'on'

        async def tool(name, args, key=A_KEY, request_id=None):
            answer = await self.fetch(call(name, args), key=key, request_id=request_id)
            if answer.status != 200:
                return answer.status
            result = json.loads(answer.body)['result']
            text = result['content'][0]['text']
            return text if result['isError'] else json.loads(text)

        shared = self.worker.env.SHARED
        shown = (await tool('pen_write', {'agent': 'beta', 'collection': 'shown', 'body': 'shared'}, shared))['id']
        hidden = (await tool('pen_write', {'agent': 'beta', 'collection': 'hidden', 'body': 'private'}, shared))['id']
        owner = {'of': 'beta'}
        self.assertEqual((await tool('pen_read', dict(owner, id=shown)))['surface'], 'web')
        self.assertEqual((await tool('pen_history', dict(owner, id=shown)))[0]['id'], shown)
        self.assertEqual((await tool('pen_list', dict(owner, collection='shown', surface='web')))[0]['id'], shown)
        self.assertEqual([r['collection'] for r in await tool('pen_collections', owner)], ['shown'])
        for name in ('pen_read', 'pen_history'):
            self.assertEqual(await tool(name, dict(owner, id=hidden)),
                             await tool(name, dict(owner, id='f' * 32)))
        self.assertEqual(await tool('pen_list', dict(owner, collection='hidden')),
                         await tool('pen_list', dict(owner, collection='missing')))
        self.assertEqual(await tool('pen_collections', owner, MAIN), 'no writer is assigned to this key')
        self.assertEqual(await tool('pen_collections', owner, shared), 'agent is required: one of alpha, beta')
        self.assertEqual(await tool('pen_collections', dict(owner, agent='gamma'), shared),
                         'agent must be one of alpha, beta')
        self.assertEqual(await tool('pen_collections', {'of': 'Beta'}), 'unknown agent')
        self.worker.env.SCRIPTORIUM_READS = json.dumps({'alpha': 'own', 'beta': 'own'})
        self.assertEqual(await tool('pen_collections', owner), 'this agent can only read its own notebooks')
        self.assertEqual((await tool('pen_read', dict(owner, agent='beta', id=hidden), shared))['body'], 'private')
        self.assertEqual((await tool('pen_read', {'agent': 'beta', 'id': hidden}, shared))['body'], 'private')

        invalid = {'collection': 'notes', 'body': 'hello', 'visibility': 'silo'}
        self.assertEqual(await tool('pen_write', invalid, request_id='write'), 'unknown argument')
        del invalid['visibility']
        first = await tool('pen_write', invalid, request_id='write')
        self.assertEqual(await tool('pen_write', invalid, request_id='write'), first)
        self.assertEqual(await tool('pen_write', invalid, B_KEY, request_id='write'), 409)
        edit = {'id': first['id'], 'based_on': 1, 'body': 'plain'}
        self.assertEqual(await tool('pen_edit', edit, B_KEY, request_id='edit'), {'version': 2})
        self.assertEqual(await tool('pen_edit', edit, B_KEY, request_id='edit'), {'version': 2})
        self.assertEqual(await tool('pen_edit', edit, A_KEY, request_id='edit'), 409)
        self.assertEqual([r.get('surface') for r in await tool('pen_history', {'id': first['id']})],
                         ['desktop', None])
        self.assertEqual(len(self.pen.store.export_pen('alpha')), 1)

    async def test_key_rotation_overlaps_then_removes_the_old_key(self):
        body = call('pen_write', {'collection': 'notes', 'body': 'kept'})
        first = await self.fetch(body, key=A_KEY, request_id='rotate')
        self.assertEqual(first.status, 200)
        self.worker.env.SCRIPTORIUM_AGENTS = json.dumps({'A': 'alpha', 'B': 'alpha'})
        for key in (A_KEY, B_KEY):
            replay = await self.fetch(body, key=key, request_id='rotate')
            self.assertEqual((replay.status, replay.body), (200, first.body))
        self.worker.env.SCRIPTORIUM_AGENTS = json.dumps({'B': 'alpha'})
        del self.worker.env.A
        self.assertEqual((await self.fetch(body, key=A_KEY, request_id='rotate')).status, 401)
        replay = await self.fetch(body, key=B_KEY, request_id='rotate')
        self.assertEqual((replay.status, replay.body), (200, first.body))
        self.assertEqual(len(self.pen.store.export_pen('alpha')), 1)


class FakeHttpCurrentOnlyStore:
    """Fake HTTP-like backend: keeps only the current row per entry (replace on edit, remove on delete, no history)."""
    contract_version = 3
    keeps_history = False

    def __init__(self, env, *, backing=None, with_mutate=False):
        self.env = env
        self.secret = getattr(env, 'BACKEND_SECRET', None)
        self.rows = backing if backing is not None else {}       # (writer, id) -> current record dict
        self.replays = {}                                       # request_id -> (fingerprint, reply_json)
        self.writes = 0
        self.mutate_calls = 0
        if with_mutate:
            self.mutate = self._mutate

    def _mutate(self, request_id, fingerprint, apply):
        self.mutate_calls += 1
        if request_id in self.replays:
            saved_fp, saved_reply = self.replays[request_id]
            if saved_fp != fingerprint:
                raise ValueError('this X-Request-Id was already used with a different request')
            return saved_reply
        reply = apply()
        if reply is not None:
            self.replays[request_id] = (fingerprint, reply)
        return reply

    def collections(self, writer, *, visible=None):
        counts = {}
        for (w, _), rec in self.rows.items():
            if w == writer:
                counts[rec['collection']] = counts.get(rec['collection'], 0) + 1
        return [{'collection': name, 'count': count, 'by_day': False}
                for name, count in sorted(counts.items())
                if visible is None or visible(name, 'pool')]

    def write(self, writer, collection, body, title=None, pinned=False, by_day=None, *, surface=None, fields=None):
        self.writes += 1
        entry_id = f'{self.writes:032x}'
        now = '2026-11-22T12:00:00+00:00'
        record = {'id': entry_id, 'collection': collection, 'writer': writer, 'body': body,
                  'created': now, 'edited': now, 'pinned': pinned, 'version': 1, 'by_day': bool(by_day)}
        if title is not None:
            record['title'] = title
        if surface is not None:
            record['surface'] = surface
        if fields:
            record['fields'] = fields
        self.rows[(writer, entry_id)] = record
        return entry_id

    def edit(self, writer, id, based_on, body=None, title=None, pinned=None, *, surface=None, fields=None):
        current = self.rows.get((writer, id))
        if current is None:
            raise ValueError('entry not found')
        if based_on != current['version']:
            raise ValueError('version conflict')
        updated = dict(current, version=current['version'] + 1, edited='2026-11-22T12:05:00+00:00')
        if body is not None:
            updated['body'] = body
        if title is not None:
            updated['title'] = title
        if pinned is not None:
            updated['pinned'] = pinned
        if surface is not None:
            updated['surface'] = surface
        if fields is not None:
            merged = dict(updated.get('fields') or {})
            for k, v in fields.items():
                if v is None:
                    merged.pop(k, None)
                else:
                    merged[k] = v
            if merged:
                updated['fields'] = merged
            else:
                updated.pop('fields', None)
        # Replace in place: no prior version is kept anywhere.
        self.rows[(writer, id)] = updated
        return updated['version']

    def read(self, writer, id, version=None, *, visible=None):
        if version is not None:
            raise ValueError('this store does not keep history')
        rec = self.rows.get((writer, id))
        if rec is None or (visible is not None and not visible(rec['collection'], 'pool')):
            raise ValueError('entry not found')
        return dict(rec)

    def list(self, writer, collection, pinned_only=False, day=None, limit=20, *, surface=None, visible=None):
        if visible is not None and not visible(collection, 'pool'):
            raise ValueError('collection not found')
        items = [dict(rec) for (w, _), rec in self.rows.items()
                 if w == writer and rec['collection'] == collection
                 and (not pinned_only or rec['pinned'])
                 and (surface is None or rec.get('surface') == surface)]
        return items[:limit]

    def delete(self, writer, id):
        if (writer, id) not in self.rows:
            raise ValueError('entry not found')
        # Remove permanently: no set-aside.
        del self.rows[(writer, id)]


class CustomStoreWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.worker = entry.Default()
        self.worker.env = SimpleNamespace(
            SCRIPTORIUM_MCP_KEY=MAIN, A=A_KEY, B=B_KEY, BACKEND_SECRET='tok-123',
            SCRIPTORIUM_AGENTS=json.dumps({'A': 'alpha', 'B': ['beta', 'gamma']}),
        )
        response = mock.patch.object(entry, 'Response',
                                     lambda body, *, status, headers=None: SimpleNamespace(
                                         body=body, status=status, headers=headers))
        response.start()
        self.addCleanup(response.stop)

    async def fetch(self, body, *, key=A_KEY, request_id=None):
        headers = {'content-type': 'application/json', 'authorization': 'Bearer ' + key}
        if request_id is not None:
            headers['x-request-id'] = request_id
        request = SimpleNamespace(method='POST', url='https://example.test/mcp',
                                  headers=headers, text=mock.AsyncMock(return_value=body))
        return await self.worker.fetch(request)

    async def test_worker_builds_custom_store_from_env_without_durable_object(self):
        backing = {}
        seen_secrets = []

        class ConfiguredStore(FakeHttpCurrentOnlyStore):
            def __init__(self, env):
                super().__init__(env, backing=backing)
                seen_secrets.append(self.secret)

        mod = type(sys)('fake_cloud_store')
        mod.ConfiguredStore = ConfiguredStore
        self.worker.env.SCRIPTORIUM_STORE = 'fake_cloud_store:ConfiguredStore'
        with mock.patch.dict(sys.modules, {'fake_cloud_store': mod}):
            res = await self.fetch(call('journal_write', {'collection': 'journal', 'body': 'hello'}))
            self.assertEqual(res.status, 200)
            entry_id = json.loads(json.loads(res.body)['result']['content'][0]['text'])['id']
            res = await self.fetch(call('journal_read', {'id': entry_id}))
            self.assertEqual(res.status, 200)
            self.assertEqual(json.loads(json.loads(res.body)['result']['content'][0]['text'])['body'], 'hello')
        self.assertEqual(seen_secrets, ['tok-123', 'tok-123'])
        self.assertFalse(hasattr(self.worker.env, 'PEN'))

    async def test_worker_refuses_unknown_or_invalid_store_at_start(self):
        class MissingWrite(FakeHttpCurrentOnlyStore):
            write = None

        class NeedsHistory(FakeHttpCurrentOnlyStore):
            keeps_history = True  # lacks history()

        class OldVersion(FakeHttpCurrentOnlyStore):
            contract_version = 2

        class BadKeepsHistory(FakeHttpCurrentOnlyStore):
            keeps_history = 'no'

        class ExplodingInit(FakeHttpCurrentOnlyStore):
            def __init__(self, env):
                raise ValueError('missing backend url')

        mod = type(sys)('bad_cloud_stores')
        mod.MissingWrite = MissingWrite
        mod.NeedsHistory = NeedsHistory
        mod.OldVersion = OldVersion
        mod.BadKeepsHistory = BadKeepsHistory
        mod.ExplodingInit = ExplodingInit
        mod.NOT_CALLABLE = 42

        bad_refs = [
            '',
            'no_colon',
            'bad_cloud_stores: MissingWrite',
            'no_such_module_xyz:Store',
            'bad_cloud_stores:DoesNotExist',
            'bad_cloud_stores:NOT_CALLABLE',
            'bad_cloud_stores:MissingWrite',
            'bad_cloud_stores:NeedsHistory',
            'bad_cloud_stores:OldVersion',
            'bad_cloud_stores:BadKeepsHistory',
            'bad_cloud_stores:ExplodingInit',
        ]
        with mock.patch.dict(sys.modules, {'bad_cloud_stores': mod}):
            for ref in bad_refs:
                with self.subTest(ref=ref):
                    self.worker.env.SCRIPTORIUM_STORE = ref
                    res = await self.fetch(json.dumps(rpc('tools/list')))
                    self.assertEqual(res.status, 503)
                    self.assertIn('SCRIPTORIUM_STORE', json.loads(res.body)['error'])

    async def test_x_request_id_refused_without_mutate_and_replayed_once_with_mutate(self):
        # 1. Without mutate(): X-Request-Id on writes is refused with HTTP 400 and does not write.
        plain_store = FakeHttpCurrentOnlyStore(self.worker.env, with_mutate=False)
        status, text = entry.serve(plain_store, call('journal_write', {'collection': 'journal', 'body': 'nope'}, 7),
                                   ('alpha',), request_id='req-1')
        self.assertEqual(status, 400)
        body = json.loads(text)
        self.assertEqual(body['id'], 7)
        self.assertIn('X-Request-Id', body['error']['message'])
        self.assertEqual(plain_store.writes, 0)
        # Reads with X-Request-Id and writes without X-Request-Id still work.
        status, _ = entry.serve(plain_store, call('journal_list', {'collection': 'journal'}), ('alpha',), request_id='req-r')
        self.assertEqual(status, 200)
        status, _ = entry.serve(plain_store, call('journal_write', {'collection': 'journal', 'body': 'ok'}), ('alpha',))
        self.assertEqual(status, 200)
        self.assertEqual(plain_store.writes, 1)

        # 2. With mutate(): replay returns saved answer with current id, writes once, conflicts return 409.
        retry_store = FakeHttpCurrentOnlyStore(self.worker.env, with_mutate=True)
        write_1 = call('journal_write', {'collection': 'journal', 'body': 'once'}, 10)
        write_1_retry = call('journal_write', {'collection': 'journal', 'body': 'once'}, 11)
        first = entry.serve(retry_store, write_1, ('alpha',), request_id='req-ok')
        again = entry.serve(retry_store, write_1_retry, ('alpha',), request_id='req-ok')
        self.assertEqual(first[0], 200)
        self.assertEqual(again[0], 200)
        self.assertEqual(json.loads(again[1])['id'], 11)
        self.assertEqual(json.loads(again[1])['result'], json.loads(first[1])['result'])
        self.assertEqual(retry_store.writes, 1)

        # Different request with same X-Request-Id -> HTTP 409, no second write.
        conflict = entry.serve(retry_store, call('journal_write', {'collection': 'journal', 'body': 'other'}, 12),
                               ('alpha',), request_id='req-ok')
        self.assertEqual(conflict[0], 409)
        self.assertEqual(json.loads(conflict[1])['id'], 12)
        self.assertEqual(retry_store.writes, 1)

        # Failed write is not saved by mutate, so a later valid write with the same id succeeds.
        bad_edit = call('journal_edit', {'id': 'f' * 32, 'based_on': 1, 'body': 'missing'}, 13)
        failed = entry.serve(retry_store, bad_edit, ('alpha',), request_id='req-fail')
        self.assertEqual(failed[0], 200)
        self.assertTrue(json.loads(failed[1])['result']['isError'])
        alpha_fail_key = entry._scoped_request_id('alpha', 'req-fail')
        self.assertNotIn(alpha_fail_key, retry_store.replays)
        good_after = entry.serve(retry_store, call('journal_write', {'collection': 'journal', 'body': 'good'}, 14),
                                 ('alpha',), request_id='req-fail')
        self.assertEqual(good_after[0], 200)
        self.assertIn(alpha_fail_key, retry_store.replays)

        # Two writers using the same X-Request-Id do not collide or reveal each other's retries.
        alpha_shared = entry.serve(retry_store, call('journal_write', {'collection': 'journal', 'body': 'from-alpha'}, 16),
                                   ('alpha',), request_id='shared-id')
        beta_shared = entry.serve(retry_store, call('journal_write', {'collection': 'journal', 'body': 'from-beta'}, 17),
                                  ('beta',), request_id='shared-id')
        self.assertEqual(alpha_shared[0], 200)
        self.assertEqual(beta_shared[0], 200)
        alpha_id = json.loads(json.loads(alpha_shared[1])['result']['content'][0]['text'])['id']
        beta_id = json.loads(json.loads(beta_shared[1])['result']['content'][0]['text'])['id']
        self.assertNotEqual(alpha_id, beta_id)
        # Replaying beta's shared-id returns beta's answer, not alpha's.
        beta_replay = entry.serve(retry_store, call('journal_write', {'collection': 'journal', 'body': 'from-beta'}, 18),
                                  ('beta',), request_id='shared-id')
        self.assertEqual(beta_replay[0], 200)
        self.assertEqual(json.loads(json.loads(beta_replay[1])['result']['content'][0]['text'])['id'], beta_id)

        # Multi-agent key omitting `agent` returns a tool error without calling mutate().
        before_missing = retry_store.mutate_calls
        missing_agent = entry.serve(retry_store, call('journal_write', {'collection': 'journal', 'body': 'who'}, 19),
                                    ('beta', 'gamma'), request_id='req-missing')
        self.assertEqual(missing_agent[0], 200)
        self.assertTrue(json.loads(missing_agent[1])['result']['isError'])
        self.assertEqual(retry_store.mutate_calls, before_missing)

        # Unauthorized agent is refused with 403 before mutate() is called (no probing).
        before = retry_store.mutate_calls
        denied = entry.serve(retry_store, call('journal_write', {'collection': 'journal', 'body': 'x', 'agent': 'beta'}, 15),
                             ('alpha',), request_id='req-ok')
        self.assertEqual(denied[0], 403)
        self.assertEqual(retry_store.mutate_calls, before)

    async def test_keeps_history_false_refuses_history_on_and_replaces_and_removes(self):
        backing = {}

        class CurrentOnly(FakeHttpCurrentOnlyStore):
            def __init__(self, env):
                super().__init__(env, backing=backing)

        mod = type(sys)('current_only_store')
        mod.CurrentOnly = CurrentOnly
        self.worker.env.SCRIPTORIUM_STORE = 'current_only_store:CurrentOnly'

        with mock.patch.dict(sys.modules, {'current_only_store': mod}):
            # SCRIPTORIUM_HISTORY=on with keeps_history=False refuses to start (503).
            self.worker.env.SCRIPTORIUM_HISTORY = 'on'
            refused = await self.fetch(json.dumps(rpc('tools/list')))
            self.assertEqual(refused.status, 503)
            self.assertIn('SCRIPTORIUM_HISTORY=on', json.loads(refused.body)['error'])

            # With history off, journal_history is hidden and uncallable; edit replaces; delete removes.
            self.worker.env.SCRIPTORIUM_HISTORY = 'off'
            listed = json.loads((await self.fetch(json.dumps(rpc('tools/list')))).body)['result']['tools']
            by_name = {t['name']: t for t in listed}
            self.assertNotIn('journal_history', by_name)
            self.assertNotIn('version', by_name['journal_read']['inputSchema']['properties'])
            self.assertNotIn('kept by the store', by_name['journal_read']['description'])
            self.assertNotIn('aside for recovery', by_name['journal_delete']['description'])

            for hist_name in ('journal_history', 'pen_history'):
                res = await self.fetch(call(hist_name, {'id': '0' * 32}))
                self.assertEqual(json.loads(res.body)['error']['message'], 'unknown tool: ' + hist_name)

            # Write -> edit replaces in place -> read returns only new version, version arg refused.
            written = json.loads(json.loads((await self.fetch(
                call('journal_write', {'collection': 'journal', 'body': 'first'})
            )).body)['result']['content'][0]['text'])
            entry_id = written['id']
            edited = json.loads(json.loads((await self.fetch(
                call('journal_edit', {'id': entry_id, 'based_on': 1, 'body': 'replaced'})
            )).body)['result']['content'][0]['text'])
            self.assertEqual(edited, {'version': 2})
            self.assertEqual(len(backing), 1)
            self.assertEqual(backing[('alpha', entry_id)]['body'], 'replaced')

            read_back = json.loads(json.loads((await self.fetch(
                call('journal_read', {'id': entry_id})
            )).body)['result']['content'][0]['text'])
            self.assertEqual(read_back['body'], 'replaced')
            self.assertEqual(read_back['version'], 2)

            old_req = json.loads((await self.fetch(
                call('journal_read', {'id': entry_id, 'version': 1})
            )).body)['result']
            self.assertTrue(old_req['isError'])
            self.assertEqual(old_req['content'][0]['text'], 'unknown argument')

            # Even a direct serve(..., history=True) cannot expose history on a keeps_history=False store.
            direct_store = CurrentOnly(self.worker.env)
            _, hist_text = entry.serve(direct_store, call('journal_history', {'id': entry_id}), ('alpha',), history=True)
            self.assertEqual(json.loads(hist_text)['error']['message'], 'unknown tool: journal_history')

            # Delete removes permanently (no set-aside).
            deleted = json.loads(json.loads((await self.fetch(
                call('journal_delete', {'id': entry_id})
            )).body)['result']['content'][0]['text'])
            self.assertEqual(deleted, {'ok': True})
            self.assertEqual(backing, {})
            after_delete = json.loads((await self.fetch(call('journal_read', {'id': entry_id}))).body)['result']
            self.assertTrue(after_delete['isError'])
            self.assertEqual(after_delete['content'][0]['text'], 'entry not found')

    async def test_auth_and_route_run_before_store_init_and_init_errors_never_leak_config(self):
        init_calls = []

        class LeakyInitStore(FakeHttpCurrentOnlyStore):
            def __init__(self, env):
                init_calls.append(True)
                raise ValueError('cannot connect to postgres://admin:supersecret@db.internal:5432/notes')

        mod = type(sys)('leaky_init_store')
        mod.LeakyInitStore = LeakyInitStore
        self.worker.env.SCRIPTORIUM_STORE = 'leaky_init_store:LeakyInitStore'

        with mock.patch.dict(sys.modules, {'leaky_init_store': mod}):
            # Unauthenticated request gets 401 without constructing the store.
            unauth = await self.fetch(json.dumps(rpc('tools/list')), key='wrong-key-0000000000000000')
            self.assertEqual(unauth.status, 401)
            self.assertEqual(init_calls, [])

            # Wrong method and wrong path fail before constructing the store.
            for method, path, expected in (('GET', '/mcp', 405), ('POST', '/nope', 404)):
                req = SimpleNamespace(method=method, url='https://example.test' + path,
                                      headers={'content-type': 'application/json', 'authorization': 'Bearer ' + A_KEY},
                                      text=mock.AsyncMock(return_value='{}'))
                res = await self.worker.fetch(req)
                self.assertEqual(res.status, expected)
            self.assertEqual(init_calls, [])

            # Authenticated valid request gets a generic 503 without leaking backend URL or credentials.
            authed = await self.fetch(json.dumps(rpc('tools/list')))
            self.assertEqual(authed.status, 503)
            self.assertEqual(len(init_calls), 1)
            err_text = json.loads(authed.body)['error']
            self.assertIn('SCRIPTORIUM_STORE', err_text)
            self.assertIn('failed to initialize', err_text)
            for secret in ('postgres://', 'supersecret', 'db.internal', 'admin'):
                self.assertNotIn(secret, err_text)

    async def test_async_custom_store_methods_and_async_mutate_are_awaited(self):
        backing = {}
        replays = {}

        class AsyncHttpStore:
            contract_version = 3
            keeps_history = False

            def __init__(self, env):
                self._inner = FakeHttpCurrentOnlyStore(env, backing=backing)
                self.replays = replays

            async def mutate(self, request_id, fingerprint, apply):
                if request_id in self.replays:
                    saved_fp, saved_reply = self.replays[request_id]
                    if saved_fp != fingerprint:
                        raise ValueError('this X-Request-Id was already used with a different request')
                    return saved_reply
                reply = await apply()
                if reply is not None:
                    self.replays[request_id] = (fingerprint, reply)
                return reply

            async def collections(self, writer, *, visible=None):
                return self._inner.collections(writer, visible=visible)

            async def write(self, writer, collection, body, title=None, pinned=False, by_day=None, *, surface=None, fields=None):
                return self._inner.write(writer, collection, body, title=title, pinned=pinned,
                                         by_day=by_day, surface=surface, fields=fields)

            async def edit(self, writer, id, based_on, body=None, title=None, pinned=None, *, surface=None, fields=None):
                return self._inner.edit(writer, id, based_on, body=body, title=title,
                                        pinned=pinned, surface=surface, fields=fields)

            async def read(self, writer, id, version=None, *, visible=None):
                return self._inner.read(writer, id, version=version, visible=visible)

            async def list(self, writer, collection, pinned_only=False, day=None, limit=20, *, surface=None, visible=None):
                return self._inner.list(writer, collection, pinned_only=pinned_only, day=day,
                                        limit=limit, surface=surface, visible=visible)

            async def delete(self, writer, id):
                return self._inner.delete(writer, id)

        class AsyncStoreWithSyncMutate(AsyncHttpStore):
            def mutate(self, request_id, fingerprint, apply):
                return apply()

        mod = type(sys)('async_cloud_store')
        mod.AsyncHttpStore = AsyncHttpStore
        mod.AsyncStoreWithSyncMutate = AsyncStoreWithSyncMutate
        self.worker.env.SCRIPTORIUM_STORE = 'async_cloud_store:AsyncHttpStore'
        self.worker.env.SCRIPTORIUM_FIELDS = json.dumps({'alpha': {'journal': {'mood': 'text'}}})

        with mock.patch.dict(sys.modules, {'async_cloud_store': mod}):
            # Write with X-Request-Id (awaits async mutate + async write) and replay once.
            w_body = call('journal_write', {'collection': 'journal', 'body': 'async hi', 'fields': {'mood': 'calm'}}, 1)
            res1 = await self.fetch(w_body, request_id='async-1')
            self.assertEqual(res1.status, 200)
            entry_id = json.loads(json.loads(res1.body)['result']['content'][0]['text'])['id']
            res2 = await self.fetch(call('journal_write', {'collection': 'journal', 'body': 'async hi', 'fields': {'mood': 'calm'}}, 2),
                                    request_id='async-1')
            self.assertEqual(res2.status, 200)
            self.assertEqual(json.loads(res2.body)['id'], 2)
            self.assertEqual(len(backing), 1)

            # Edit with fields (awaits both pen.read for field check and pen.edit).
            edit_res = await self.fetch(call('journal_edit', {'id': entry_id, 'based_on': 1, 'body': 'edited',
                                                              'fields': {'mood': 'bright'}}))
            self.assertEqual(edit_res.status, 200)
            self.assertEqual(json.loads(json.loads(edit_res.body)['result']['content'][0]['text']), {'version': 2})

            # Read, list, journals all await async methods.
            read_res = await self.fetch(call('journal_read', {'id': entry_id}))
            self.assertEqual(json.loads(json.loads(read_res.body)['result']['content'][0]['text'])['body'], 'edited')
            list_res = await self.fetch(call('journal_list', {'collection': 'journal'}))
            self.assertEqual(len(json.loads(json.loads(list_res.body)['result']['content'][0]['text'])), 1)
            cols_res = await self.fetch(call('journals', {}))
            self.assertEqual(json.loads(json.loads(cols_res.body)['result']['content'][0]['text'])[0]['collection'], 'journal')

            # Delete MUST be awaited and actually remove the entry from backing.
            del_res = await self.fetch(call('journal_delete', {'id': entry_id}))
            self.assertEqual(del_res.status, 200)
            self.assertEqual(json.loads(json.loads(del_res.body)['result']['content'][0]['text']), {'ok': True})
            self.assertEqual(backing, {})

            # Async store with sync mutate is refused (503).
            self.worker.env.SCRIPTORIUM_STORE = 'async_cloud_store:AsyncStoreWithSyncMutate'
            bad = await self.fetch(json.dumps(rpc('tools/list')))
            self.assertEqual(bad.status, 503)

        # Sync check_store (local server) rejects async stores, and sync pen_tool never discards async delete().
        async_store = AsyncHttpStore(self.worker.env)
        with self.assertRaises(ValueError):
            server.check_store(async_store)
        with self.assertRaises(TypeError):
            server.pen_tool('journal_delete', {'id': '0' * 32}, ('alpha',), store=async_store)

    async def test_append_on_custom_store_refused_without_append_and_replayed_once_with_mutate(self):
        # 1. Custom store without append() refuses append=True and never emulates it via edit().
        no_append = FakeHttpCurrentOnlyStore(self.worker.env, with_mutate=True)
        status, text = await entry.serve_async(
            no_append, call('journal_write', {'collection': 'daily', 'body': 'hi', 'append': True}, 1), ('alpha',))
        self.assertEqual(status, 200)
        res = json.loads(text)['result']
        self.assertTrue(res['isError'])
        self.assertIn('does not support append', res['content'][0]['text'])
        self.assertEqual(no_append.writes, 0)

        # 2. Async keeps_history=False store with append() + mutate(): replaces in place and X-Request-Id retry adds once.
        backing = {}
        replays = {}

        class AsyncAppendStore(FakeHttpCurrentOnlyStore):
            def __init__(self, env):
                super().__init__(env, backing=backing, with_mutate=False)
                self.replays = replays
                self.append_calls = 0

            async def mutate(self, request_id, fingerprint, apply):
                if request_id in self.replays:
                    saved_fp, saved_reply = self.replays[request_id]
                    if saved_fp != fingerprint:
                        raise entry.RequestIdConflict()
                    return saved_reply
                reply = await apply()
                if reply is not None:
                    self.replays[request_id] = (fingerprint, reply)
                return reply

            async def append(self, writer, collection, body, *, id=None, day=None, fields=None, title=None, surface=None, on_create=None):
                self.append_calls += 1
                from engine.pen import append_body
                target = backing.get((writer, id)) if id else next(
                    (r for (w, _), r in backing.items() if w == writer and r['collection'] == collection), None)
                if target is None:
                    if on_create is not None:
                        on_create()
                    eid = 'd' * 32
                    rec = {'id': eid, 'collection': collection, 'writer': writer, 'body': body,
                           'title': title or (day or '2026-11-22'), 'version': 1, 'by_day': True, 'pinned': False}
                    if surface is not None:
                        rec['surface'] = surface
                    backing[(writer, eid)] = rec
                    return {'id': eid, 'version': 1, 'action': 'created'}
                target['body'] = append_body(target['body'], body)
                target['version'] += 1
                if surface is not None:
                    target['surface'] = surface
                return {'id': target['id'], 'version': target['version'], 'action': 'appended'}

        store = AsyncAppendStore(self.worker.env)
        s1, t1 = await entry.serve_async(
            store, call('journal_write', {'collection': 'daily', 'body': 'first', 'append': True}, 10),
            ('alpha',), request_id='custom-app-1', surface='web')
        s2, t2 = await entry.serve_async(
            store, call('journal_write', {'collection': 'daily', 'body': 'first', 'append': True}, 11),
            ('alpha',), request_id='custom-app-1', surface='web')
        self.assertEqual((s1, s2), (200, 200))
        self.assertEqual(json.loads(t2)['id'], 11)
        self.assertEqual(store.append_calls, 1)

        s3, t3 = await entry.serve_async(
            store, call('journal_write', {'collection': 'daily', 'body': 'second', 'append': True}, 12),
            ('alpha',), request_id='custom-app-2', surface='mobile')
        s4, t4 = await entry.serve_async(
            store, call('journal_write', {'collection': 'daily', 'body': 'second', 'append': True}, 13),
            ('alpha',), request_id='custom-app-2', surface='mobile')
        self.assertEqual((s3, s4), (200, 200))
        self.assertEqual(store.append_calls, 2)
        self.assertEqual(len(backing), 1)
        rec = backing[('alpha', 'd' * 32)]
        self.assertEqual(rec['body'], 'first\n\nsecond')
        self.assertEqual(rec['version'], 2)
        self.assertEqual(rec['surface'], 'mobile')


class CustomStoreErrorTests(unittest.TestCase):
    def test_a_custom_store_read_error_never_reaches_the_model(self):
        class LeakyRead(FakeHttpCurrentOnlyStore):
            def read(self, writer, id, version=None, *, visible=None):
                raise OSError('backend said token=PLANTED-VALUE-123 at db.example.internal')
        store = LeakyRead(SimpleNamespace())
        with mock.patch('sys.stderr'):
            text, is_error = server.pen_tool('journal_read', {'id': '0' * 32}, ('alpha',), store=store)
        self.assertTrue(is_error)
        self.assertNotIn('PLANTED-VALUE-123', text)
        self.assertNotIn('db.example.internal', text)
        self.assertIn('storage error', text)


class BuildTests(unittest.TestCase):
    def _load_build(self):
        spec = importlib.util.spec_from_file_location('cloud_build', ROOT / 'cloud' / 'build.py')
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        return build

    def test_the_build_copies_the_sources_unchanged(self):
        build = self._load_build()
        with tempfile.TemporaryDirectory() as tmp:
            files = build.build(Path(tmp) / 'out')
            self.assertEqual(files, ['engine/__init__.py', 'engine/cloud_pen.py', 'engine/pen.py', 'entry.py', 'server.py'])
            for source, target in build.FILES.items():
                self.assertEqual((ROOT / source).read_bytes(), (Path(tmp) / 'out' / target).read_bytes())

    def test_the_build_store_flag_bundles_only_that_file_and_refuses_others(self):
        build = self._load_build()
        with tempfile.TemporaryDirectory() as tmp:
            src_dir = Path(tmp) / 'src'
            src_dir.mkdir()
            store_file = src_dir / 'my_store.py'
            store_file.write_text('class MyStore:\n    pass\n', encoding='utf-8')
            sibling = src_dir / 'secret_sibling.py'
            sibling.write_text('SECRET = 1\n', encoding='utf-8')

            out = Path(tmp) / 'out'
            files = build.build(out, store=store_file)
            self.assertEqual(files, ['engine/__init__.py', 'engine/cloud_pen.py', 'engine/pen.py',
                                     'entry.py', 'my_store.py', 'server.py'])
            self.assertEqual((out / 'my_store.py').read_bytes(), store_file.read_bytes())
            self.assertFalse((out / 'secret_sibling.py').exists())

            # Refusals: directory, missing file, symlink, non-.py, bad identifier, built-in names.
            link = src_dir / 'link_store.py'
            link.symlink_to(store_file)
            txt = src_dir / 'store.txt'
            txt.write_text('hi', encoding='utf-8')
            bad_ident = src_dir / 'bad-store.py'
            bad_ident.write_text('x = 1\n', encoding='utf-8')
            reserved = []
            for name in ('entry.py', 'server.py', 'engine.py'):
                p = src_dir / name
                p.write_text('x = 1\n', encoding='utf-8')
                reserved.append(p)

            for upper in ('Entry.py', 'SERVER.py'):    # case-insensitive filesystems: must not overwrite entry.py/server.py
                d = src_dir / ('case_' + upper.split('.')[0]); d.mkdir()
                p = d / upper; p.write_text('x = 1\n', encoding='utf-8'); reserved.append(p)
            for bad in (src_dir, src_dir / 'missing.py', link, txt, bad_ident, *reserved):
                with self.subTest(bad=bad), self.assertRaises(SystemExit):
                    build.build(out, store=bad)
            # Refusal: --store inside the resolved output folder must fail before clearing out.
            with self.assertRaises(SystemExit):
                build.build(out, store=out / 'my_store.py')
            nested = out / 'engine' / 'nested_store.py'
            nested.write_text('class Nested:\n    pass\n', encoding='utf-8')
            with self.assertRaises(SystemExit):
                build.build(out, store=nested)
            # An existing valid build folder is left intact when --store is refused.
            self.assertTrue((out / 'my_store.py').is_file())
            self.assertTrue(nested.is_file())

            # CLI --store works too.
            out_cli = Path(tmp) / 'out-cli'
            with mock.patch('builtins.print'):
                build.main([str(out_cli), '--store', str(store_file)])
            self.assertTrue((out_cli / 'my_store.py').is_file())

    def test_the_build_refuses_a_folder_it_did_not_make(self):
        build = self._load_build()
        with tempfile.TemporaryDirectory() as tmp:
            keep = Path(tmp) / 'precious.txt'
            keep.write_text('mine')
            with self.assertRaises(SystemExit):
                build.build(tmp)
            with self.assertRaises(SystemExit):
                build.build(ROOT)
            self.assertTrue(keep.exists())
            out = Path(tmp) / 'out'
            build.build(out)
            build.build(out)    # its own folder is replaced

    def test_the_build_refuses_a_link_even_when_it_points_at_an_empty_or_marked_folder(self):
        build = self._load_build()
        with tempfile.TemporaryDirectory() as tmp:
            empty, marked = Path(tmp) / 'empty', Path(tmp) / 'marked'
            empty.mkdir()
            build.build(marked)
            for target in (empty, marked):
                link = Path(tmp) / ('link-' + target.name)
                link.symlink_to(target, target_is_directory=True)
                with self.assertRaises(SystemExit):
                    build.build(link)
                self.assertTrue(link.is_symlink())
                self.assertTrue(target.is_dir())
            self.assertTrue((marked / build.MARK).is_file())
            dangling = Path(tmp) / 'dangling'
            dangling.symlink_to(Path(tmp) / 'nowhere')
            with self.assertRaises(SystemExit):
                build.build(dangling)


if __name__ == '__main__':
    unittest.main()
