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


class BuildTests(unittest.TestCase):
    def test_the_build_copies_the_sources_unchanged(self):
        spec = importlib.util.spec_from_file_location('cloud_build', ROOT / 'cloud' / 'build.py')
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        with tempfile.TemporaryDirectory() as tmp:
            files = build.build(Path(tmp) / 'out')
            self.assertEqual(files, ['engine/__init__.py', 'engine/cloud_pen.py', 'engine/pen.py', 'entry.py', 'server.py'])
            for source, target in build.FILES.items():
                self.assertEqual((ROOT / source).read_bytes(), (Path(tmp) / 'out' / target).read_bytes())

    def test_the_build_refuses_a_folder_it_did_not_make(self):
        spec = importlib.util.spec_from_file_location('cloud_build', ROOT / 'cloud' / 'build.py')
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
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
        spec = importlib.util.spec_from_file_location('cloud_build', ROOT / 'cloud' / 'build.py')
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
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
