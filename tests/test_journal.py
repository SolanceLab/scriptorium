# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The journal toolset: journal_* names with pen_* aliases, entry fields, hidden history, on both servers."""
import importlib.util
import json
import math
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'mcp'), str(ROOT / 'engine')]
import server
from engine import pen as pen_module
from engine.cloud_pen import SqlPenStore, Sqlite3Sql
from engine.pen import LocalFilesPenStore
spec = importlib.util.spec_from_file_location('cloud_entry', ROOT / 'cloud' / 'entry.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)

OLD_NAMES = ('pen_collections', 'pen_write', 'pen_edit', 'pen_read', 'pen_list', 'pen_history', 'pen_delete')
KEY = 'j' * 32
FIELDS = {'*': {'journal': {'emotions': 'list', 'keynotes': 'text', 'mood': {'type': 'number', 'required': True},
                            'markers': 'object', 'seen': 'boolean'}},
          'beta': {'journal': {'decision': 'text'}}}
ONE_AGENT_FIELDS = {'*': FIELDS['*']}


def rpc(name, arguments):
    return {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}}


class Base(unittest.TestCase):
    def configure(self, settings=None, agents=None, pen_ref=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        names = ('keys', 'shared_tools', 'pen', 'surfaces', 'policy', 'fields', 'history', 'key_refs', 'root', 'key')
        saved = {name: getattr(server.Config, name) for name in names}
        self.addCleanup(lambda: [setattr(server.Config, name, value) for name, value in saved.items()])
        values = {'TEST_J_KEY': KEY}
        values.update(settings or {})
        for name in ('SCRIPTORIUM_AGENTS', 'SCRIPTORIUM_WRITERS', 'MEMORY_ENTRY_WRITERS', 'SCRIPTORIUM_HISTORY',
                     'SCRIPTORIUM_FIELDS'):
            values.setdefault(name, '')
        patch = mock.patch.dict(os.environ, values)
        patch.start()
        self.addCleanup(patch.stop)
        for name in [n for n, v in values.items() if v == '' and n.startswith(('SCRIPTORIUM_H', 'SCRIPTORIUM_F'))]:
            os.environ.pop(name)
        server.configure(self.tmp.name, 'c' * 32, {'TEST_J_KEY': agents or 'alpha'}, pen_ref)

    def tool(self, name, args, agent=('alpha',)):
        answer = server.handle(rpc(name, args), agent)
        if 'error' in answer:
            return True, answer['error']['message']
        result = answer['result']
        text = result['content'][0]['text']
        return result['isError'], text if result['isError'] else json.loads(text)

    def ok(self, name, args, agent=('alpha',)):
        error, value = self.tool(name, args, agent)
        self.assertFalse(error, value)
        return value

    def listed(self, agent=('alpha',)):
        out = server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, agent)
        return [t['name'] for t in out['result']['tools']]


class ToolsetTests(Base):
    def test_one_journal_toolset_is_listed_and_the_old_names_are_not(self):
        self.configure()
        names = self.listed()
        self.assertEqual([n for n in names if n not in ('write_notes', 'write_memories', 'write_people')],
                         ['journals', 'journal_write', 'journal_edit', 'journal_read', 'journal_list', 'journal_delete'])
        self.assertFalse([n for n in names if n.startswith('pen_')])

    def test_old_pen_names_still_work_through_the_same_gates(self):
        self.configure()
        id = self.ok('pen_write', {'collection': 'journal', 'body': 'old name'})['id']
        self.assertEqual(self.ok('journal_read', {'id': id})['body'], 'old name')
        self.assertEqual(self.ok('pen_collections', {})[0]['count'], 1)
        self.assertEqual(self.ok('pen_edit', {'id': id, 'based_on': 1, 'body': 'x'}), {'version': 2})
        self.assertTrue(self.tool('pen_read', {'id': id, 'who': 'x'})[0])    # unknown argument, as for journal_read
        self.assertEqual(self.ok('pen_delete', {'id': id}), {'ok': True})

    def test_journal_descriptions_say_what_a_journal_is(self):
        self.configure()
        tools = {t['name']: t for t in server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, ('alpha',))['result']['tools']}
        self.assertIn('by your own hand', tools['journal_write']['description'])
        self.assertIn('journals', tools['journal_write']['description'])    # points at the declared fields
        self.assertIn('fields', tools['journal_write']['inputSchema']['properties'])

    def test_a_store_is_checked_by_the_methods_not_the_tool_names(self):
        self.assertEqual(server.STORE_METHODS, ('collections', 'write', 'edit', 'read', 'list', 'history', 'delete'))
        self.assertEqual(set(server.PEN_ALIASES.values()), set(server.JOURNAL_METHODS))


class HistoryTests(Base):
    def test_history_is_hidden_by_default(self):
        self.configure()
        id = self.ok('journal_write', {'collection': 'journal', 'body': 'one'})['id']
        self.ok('journal_edit', {'id': id, 'based_on': 1, 'body': 'two'})
        schema = next(t for t in server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, ('alpha',))['result']['tools']
                      if t['name'] == 'journal_read')['inputSchema']
        self.assertNotIn('version', schema['properties'])
        self.assertEqual(self.tool('journal_read', {'id': id, 'version': 1}), (True, 'unknown argument'))
        for name in ('journal_history', 'pen_history'):
            self.assertEqual(self.tool(name, {'id': id}), (True, 'unknown tool: ' + name))
        self.assertEqual(self.ok('journal_read', {'id': id})['body'], 'two')

    def test_history_on_marks_older_versions_in_the_answer_only(self):
        self.configure({'SCRIPTORIUM_HISTORY': 'on'})
        self.assertIn('journal_history', self.listed())
        id = self.ok('journal_write', {'collection': 'journal', 'body': 'one'})['id']
        self.ok('journal_edit', {'id': id, 'based_on': 1, 'body': 'two'})
        self.ok('journal_edit', {'id': id, 'based_on': 2, 'body': 'three'})
        history = self.ok('pen_history', {'id': id})
        self.assertEqual([r['body'] for r in history], ['one', 'two', 'three'])
        self.assertEqual([r.get('current') for r in history], [False, False, None])
        self.assertEqual(history[0]['replaced_on'], history[1]['edited'])
        self.assertEqual(history[1]['replaced_on'], history[2]['edited'])
        self.assertNotIn('replaced_on', history[2])
        first = self.ok('journal_read', {'id': id, 'version': 1})
        self.assertEqual((first['current'], first['replaced_on']), (False, history[1]['edited']))
        self.assertNotIn('current', self.ok('journal_read', {'id': id, 'version': 3}))
        self.assertNotIn('current', self.ok('journal_read', {'id': id}))
        for kept in Path(self.tmp.name, 'pen', 'alpha', 'journal', id).glob('*.md'):
            self.assertNotIn('replaced_on', kept.read_text())    # never written to the stored versions

    def test_damaged_newer_version_is_an_error_not_current(self):
        self.configure({'SCRIPTORIUM_HISTORY': 'on'})
        id = self.ok('journal_write', {'collection': 'journal', 'body': 'one'})['id']
        self.ok('journal_edit', {'id': id, 'based_on': 1, 'body': 'two'})
        Path(self.tmp.name, 'pen', 'alpha', 'journal', id, '2.md').write_text('garbage\n', encoding='utf-8')
        failed, text = self.tool('journal_read', {'id': id, 'version': 1})
        self.assertTrue(failed)
        self.assertNotEqual(text, 'version not found')

    def test_object_field_depth_agrees_with_stored_fields(self):
        def nested(levels):
            value = {}
            for _ in range(levels - 1):
                value = {'a': value}
            return value
        for levels in range(1, pen_module.MAX_FIELD_DEPTH + 3):
            value = nested(levels)
            try:
                pen_module.stored_fields({'markers': value})
                stored = True
            except ValueError:
                stored = False
            self.assertEqual(pen_module._value_fits('object', value), stored, levels)

    def test_only_unset_off_or_on_start(self):
        self.configure({'SCRIPTORIUM_HISTORY': 'off'})
        self.assertFalse(server.Config.history)
        for bad in ('ON', 'yes', '1', ' on', 'true'):
            with self.subTest(value=bad), self.assertRaisesRegex(SystemExit, 'SCRIPTORIUM_HISTORY must be on or off'):
                self.configure({'SCRIPTORIUM_HISTORY': bad})
        with self.assertRaisesRegex(SystemExit, 'SCRIPTORIUM_HISTORY'):
            self.configure({'SCRIPTORIUM_HISTORY': ' '})


class FieldTests(Base):
    def setUp(self):
        self.configure({'SCRIPTORIUM_FIELDS': json.dumps(FIELDS)}, agents=['alpha', 'beta'])
        self.alpha = ('alpha',)

    def write(self, fields, agent='alpha', collection='journal'):
        return self.tool('journal_write', {'agent': agent, 'collection': collection, 'body': 'b', 'fields': fields},
                         ('alpha', 'beta'))

    def test_fields_are_stored_under_one_key_and_read_back(self):
        fields = {'emotions': ['calm', 'tired'], 'keynotes': 'slept', 'mood': 3.5, 'markers': {'a': [1, None]}, 'seen': True}
        error, written = self.write(fields)
        self.assertFalse(error, written)
        record = self.tool('journal_read', {'agent': 'alpha', 'id': written['id']}, ('alpha', 'beta'))[1]
        self.assertEqual(record['fields'], fields)
        listed = self.tool('journal_list', {'agent': 'alpha', 'collection': 'journal'}, ('alpha', 'beta'))[1]
        self.assertEqual(listed[0]['fields'], fields)
        stored = (Path(self.tmp.name) / 'pen' / 'alpha' / 'journal' / written['id'] / '1.md').read_text()
        self.assertEqual(json.loads(stored.splitlines()[0][len('## pen-entry '):])['fields'], fields)

    def test_an_entry_without_fields_has_no_fields_key(self):
        error, written = self.tool('journal_write', {'agent': 'alpha', 'collection': 'plain', 'body': 'x'}, ('alpha', 'beta'))
        self.assertNotIn('fields', self.tool('journal_read', {'agent': 'alpha', 'id': written['id']}, ('alpha', 'beta'))[1])
        error, written = self.tool('journal_write', {'agent': 'alpha', 'collection': 'plain', 'body': 'x', 'fields': {}},
                                   ('alpha', 'beta'))
        self.assertFalse(error)
        self.assertNotIn('fields', self.tool('journal_read', {'agent': 'alpha', 'id': written['id']}, ('alpha', 'beta'))[1])

    def test_omitting_fields_still_enforces_required_ones(self):
        error, message = self.tool('journal_write', {'agent': 'alpha', 'collection': 'journal', 'body': 'b'}, ('alpha', 'beta'))
        self.assertEqual((error, message), (True, 'missing required fields: mood'))
        self.assertFalse(self.tool('journal_write', {'agent': 'alpha', 'collection': 'plain', 'body': 'b'}, ('alpha', 'beta'))[0])

    def test_unwritten_journal_declaring_nothing_is_not_listed(self):
        self.configure({'SCRIPTORIUM_FIELDS': json.dumps({'alpha': {'empty': {}}})})
        self.assertEqual(self.ok('journals', {}), [])

    def test_undeclared_fields_are_refused_naming_the_allowed_ones(self):
        error, message = self.write({'mood': 1, 'secret': 'x'})
        self.assertTrue(error)
        self.assertIn("'secret'", message)
        self.assertIn('emotions, keynotes, markers, mood, seen', message)
        error, message = self.write({'anything': 1}, collection='plain')
        self.assertTrue(error)
        self.assertIn('declares no fields', message)

    def test_a_named_agent_beats_the_star(self):
        self.assertFalse(self.write({'decision': 'ship'}, agent='beta')[0])
        self.assertTrue(self.write({'mood': 1}, agent='beta')[0])    # beta's own declaration replaces *
        self.assertTrue(self.write({'decision': 'x', 'mood': 1})[0])

    def test_types_are_checked(self):
        for bad in ({'mood': True}, {'mood': 'high'}, {'mood': math.inf}, {'mood': math.nan}, {'mood': 1, 'emotions': 'calm'},
                    {'mood': 1, 'emotions': [1]}, {'mood': 1, 'keynotes': 5}, {'mood': 1, 'markers': []},
                    {'mood': 1, 'seen': 1}, {'mood': 1, 'keynotes': '\ud800'}, {'mood': 1, 'markers': {'x': math.nan}},
                    {'mood': 1, 'markers': {'x': '\ud800'}}, {'mood': 1, 'emotions': ['\ud800']}):
            with self.subTest(fields=bad):
                self.assertTrue(self.write(bad)[0])
        self.assertTrue(self.tool('journal_write', {'agent': 'alpha', 'collection': 'journal', 'body': 'b', 'fields': []},
                                  ('alpha', 'beta'))[0])
        self.assertFalse(self.write({'mood': 0})[0])

    def test_required_fields_must_be_present_and_cannot_be_nulled(self):
        error, message = self.write({'keynotes': 'x'})
        self.assertEqual((error, message), (True, 'missing required fields: mood'))
        self.assertTrue(self.write({'mood': None})[0])
        id = self.write({'mood': 2, 'keynotes': 'k'})[1]['id']
        error, message = self.tool('journal_edit', {'agent': 'alpha', 'id': id, 'based_on': 1, 'fields': {'mood': None}},
                                   ('alpha', 'beta'))
        self.assertEqual((error, message), (True, "field 'mood' is required and cannot be cleared"))

    def test_edit_merges_and_null_clears_an_optional_field(self):
        id = self.write({'mood': 2, 'keynotes': 'k', 'emotions': ['a']})[1]['id']

        def edit(based_on, fields, **rest):
            return self.tool('journal_edit', dict({'agent': 'alpha', 'id': id, 'based_on': based_on, 'fields': fields}, **rest),
                             ('alpha', 'beta'))
        self.assertEqual(edit(1, {'keynotes': 'new'}), (False, {'version': 2}))
        record = self.tool('journal_read', {'agent': 'alpha', 'id': id}, ('alpha', 'beta'))[1]
        self.assertEqual(record['fields'], {'mood': 2, 'keynotes': 'new', 'emotions': ['a']})
        self.assertEqual(edit(2, {'keynotes': None, 'emotions': None}), (False, {'version': 3}))
        record = self.tool('journal_read', {'agent': 'alpha', 'id': id}, ('alpha', 'beta'))[1]
        self.assertEqual(record['fields'], {'mood': 2})
        self.assertTrue(edit(3, {'unknown': 1})[0])
        self.assertTrue(edit(3, {'mood': 'x'})[0])
        self.assertEqual(edit(3, {}, body='only body'), (False, {'version': 4}))    # nothing to merge
        self.assertEqual(self.tool('journal_read', {'agent': 'alpha', 'id': id}, ('alpha', 'beta'))[1]['fields'], {'mood': 2})

    def test_clearing_the_last_field_drops_the_key(self):
        id = self.write({'mood': 2})[1]['id']
        self.assertEqual(self.tool('journal_edit', {'agent': 'alpha', 'id': id, 'based_on': 1, 'fields': {'mood': None}},
                                   ('alpha', 'beta'))[0], True)    # required: refused
        store = server.Config.pen
        store.edit('alpha', id, 1, fields={'mood': None})    # the store itself clears
        self.assertNotIn('fields', store.read('alpha', id))

    def test_journals_lists_declared_fields_including_unwritten_ones(self):
        self.write({'mood': 1})
        listed = self.tool('journals', {'agent': 'alpha'}, ('alpha', 'beta'))[1]
        self.assertEqual([j['collection'] for j in listed], ['journal'])
        self.assertEqual(listed[0]['fields']['mood'], {'type': 'number', 'required': True})
        self.assertEqual(listed[0]['fields']['emotions'], {'type': 'list', 'required': False})
        # a journal declared by name and not yet written appears with count 0
        self.configure({'SCRIPTORIUM_FIELDS': json.dumps({'alpha': {'lessons': {'rule': 'text'}}})})
        listed = self.ok('journals', {})
        self.assertEqual(listed, [{'collection': 'lessons', 'count': 0, 'fields': {'rule': {'type': 'text', 'required': False}}}])

    def test_the_store_checks_what_it_keeps(self):
        store = server.Config.pen
        for bad in ({}, {'': 'x'}, {'a': None}, {'a': math.nan}, {'a': object()}, []):
            with self.subTest(fields=bad), self.assertRaises(ValueError):
                store.write('alpha', 'journal', 'x', fields=bad)
        id = store.write('alpha', 'journal', 'x', fields={'a': 1})
        path = Path(self.tmp.name) / 'pen' / 'alpha' / 'journal' / id / '1.md'
        path.write_text(path.read_text().replace('"fields": {"a": 1}', '"fields": []'))
        with self.assertRaises(ValueError):
            store.read('alpha', id)


class SettingsTests(Base):
    def test_bad_declarations_refuse_to_start(self):
        for bad in ('[]', '{"alpha": []}', '{"nobody": {"j": {"a": "text"}}}', '{"*": {"j": {"a": "tuple"}}}',
                    '{"*": {"j": {"a": {"type": "text", "required": "yes"}}}}', '{"*": {"j": {"a": {"type": "text", "x": 1}}}}',
                    '{"*": {"j": {"": "text"}}}', '{"*": {"j": "text"}}',
                    '{"*": {"a/b": {"x": "text"}}}', '{"*": {"j": {"a": 5}}}', 'nope'):
            with self.subTest(value=bad), self.assertRaises(SystemExit):
                self.configure({'SCRIPTORIUM_FIELDS': bad})

    def test_colliding_journal_names_are_refused(self):
        with self.assertRaisesRegex(SystemExit, 'collection names collide'):
            self.configure({'SCRIPTORIUM_FIELDS': json.dumps({'*': {'Journal': {}, 'journal': {}}})})

    def test_a_v2_store_still_starts_but_cannot_take_fields(self):
        class Old(LocalFilesPenStore):
            contract_version = 2
        module = type(sys)('old_fixture')
        with tempfile.TemporaryDirectory() as tmp:
            module.store = Old(tmp)
            with mock.patch.dict(sys.modules, {'old_fixture': module}):
                self.configure(pen_ref='old_fixture:store')
                self.assertIs(server.Config.pen, module.store)
                with self.assertRaisesRegex(SystemExit, 'SCRIPTORIUM_FIELDS needs a pen store'):
                    self.configure({'SCRIPTORIUM_FIELDS': json.dumps(ONE_AGENT_FIELDS)}, pen_ref='old_fixture:store')

    def test_the_contract_is_version_3(self):
        self.assertEqual(pen_module.PEN_CONTRACT_VERSION, 3)
        self.assertEqual(LocalFilesPenStore.contract_version, 3)
        self.assertEqual(SqlPenStore.contract_version, 3)

    def test_local_server_refuses_history_on_with_keeps_history_false(self):
        class CurrentOnly:
            contract_version = 3
            keeps_history = False
            def collections(self, writer, *, visible=None): return []
            def write(self, writer, collection, body, title=None, pinned=False, by_day=None, *, surface=None, fields=None): return 'a' * 32
            def edit(self, writer, id, based_on, body=None, title=None, pinned=None, *, surface=None, fields=None): return 2
            def read(self, writer, id, version=None, *, visible=None): raise ValueError('entry not found')
            def list(self, writer, collection, pinned_only=False, day=None, limit=20, *, surface=None, visible=None): return []
            def delete(self, writer, id): return None

        module = type(sys)('current_only_local')
        module.store = CurrentOnly()
        with mock.patch.dict(sys.modules, {'current_only_local': module}):
            self.configure(pen_ref='current_only_local:store')
            self.assertNotIn('journal_history', self.listed())
            with self.assertRaisesRegex(SystemExit, 'SCRIPTORIUM_HISTORY=on'):
                self.configure({'SCRIPTORIUM_HISTORY': 'on'}, pen_ref='current_only_local:store')


class CloudParityTests(unittest.TestCase):
    """The Worker runs the same handle(), so fields and history behave the same there."""
    def setUp(self):
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        self.store = SqlPenStore(Sqlite3Sql(connection))
        env = {entry.KEY_ENV: 'm' * 20, 'A': 'a' * 20, entry.AGENTS_ENV: json.dumps({'A': 'alpha'}),
               'SCRIPTORIUM_FIELDS': json.dumps(ONE_AGENT_FIELDS), 'SCRIPTORIUM_HISTORY': 'on'}
        self.keys, problem = entry.load_keys(env.get, with_policy=True)
        self.assertIsNone(problem)
        self.agents, self.surface, self.policy, self.fields, self.history = self.keys['a' * 20]

    def go(self, name, args, **override):
        context = dict(surface=self.surface, policy=self.policy, fields=self.fields, history=self.history)
        context.update(override)
        status, text = entry.serve(self.store, json.dumps(rpc(name, args)), self.agents, **context)
        result = json.loads(text)['result']
        body = result['content'][0]['text']
        return result['isError'], body if result['isError'] else json.loads(body)

    def test_load_keys_carries_fields_and_history(self):
        self.assertTrue(self.history)
        self.assertEqual(self.fields['*']['journal']['mood'], {'type': 'number', 'required': True})
        bad = {entry.KEY_ENV: 'm' * 20, 'SCRIPTORIUM_HISTORY': 'maybe'}
        keys, problem = entry.load_keys(bad.get)
        self.assertEqual((keys, problem), ({}, 'SCRIPTORIUM_HISTORY must be on or off'))
        keys, problem = entry.load_keys({entry.KEY_ENV: 'm' * 20, 'SCRIPTORIUM_FIELDS': '{"*": {"j": {"a": "tuple"}}}'}.get)
        self.assertEqual(keys, {})
        self.assertIn('SCRIPTORIUM_FIELDS', problem)

    def test_fields_work_through_the_worker_and_survive_in_sql(self):
        error, written = self.go('journal_write', {'collection': 'journal', 'body': 'x', 'fields': {'mood': 4, 'emotions': ['a']}})
        self.assertFalse(error, written)
        self.assertEqual(self.go('pen_read', {'id': written['id']})[1]['fields'], {'mood': 4, 'emotions': ['a']})
        self.assertEqual(self.go('journal_edit', {'id': written['id'], 'based_on': 1, 'fields': {'emotions': None}})[1], {'version': 2})
        self.assertEqual(self.store.read('alpha', written['id'])['fields'], {'mood': 4})
        self.assertTrue(self.go('journal_write', {'collection': 'journal', 'body': 'x', 'fields': {'nope': 1}})[0])
        self.assertEqual(self.go('journals', {})[1][0]['fields']['mood'], {'type': 'number', 'required': True})

    def test_history_is_on_or_off_in_the_worker_like_the_server(self):
        id = self.go('journal_write', {'collection': 'journal', 'body': 'one', 'fields': {'mood': 1}})[1]['id']
        self.go('journal_edit', {'id': id, 'based_on': 1, 'body': 'two'})
        history = self.go('journal_history', {'id': id})[1]
        self.assertEqual([r.get('current') for r in history], [False, None])
        self.assertEqual(self.go('journal_read', {'id': id, 'version': 1})[1]['current'], False)
        self.assertEqual(self.go('pen_history', {'id': id, 'x': 1}), (True, 'unknown argument'))
        _, text = entry.serve(self.store, json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}), self.agents,
                              history=True, fields=self.fields)
        self.assertIn('journal_history', [t['name'] for t in json.loads(text)['result']['tools']])
        # history off (the default): not listed, refused, no version argument
        _, text = entry.serve(self.store, json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}), self.agents)
        self.assertNotIn('journal_history', [t['name'] for t in json.loads(text)['result']['tools']])
        for name in ('journal_history', 'pen_history'):
            status, text = entry.serve(self.store, json.dumps(rpc(name, {'id': id})), self.agents)
            self.assertEqual(json.loads(text)['error']['message'], 'unknown tool: ' + name)
        self.assertEqual(self.go('journal_read', {'id': id, 'version': 1}, history=False), (True, 'unknown argument'))

    def test_journal_writes_are_retryable_like_pen_writes(self):
        body = json.dumps(rpc('journal_write', {'collection': 'journal', 'body': 'once', 'fields': {'mood': 1}}))
        context = dict(fields=self.fields, history=True)
        first = entry.serve(self.store, body, self.agents, 'req-1', **context)
        again = entry.serve(self.store, body, self.agents, 'req-1', **context)
        self.assertEqual(first, again)
        self.assertEqual(len(self.store.export_pen('alpha')), 1)

    def test_append_retry_with_x_request_id_adds_once(self):
        context = dict(fields=self.fields, history=True, surface='web')
        # First append creates today's entry; retry replays without duplicating.
        req1 = json.dumps(rpc('journal_write', {
            'collection': 'journal', 'body': 'morning', 'append': True, 'fields': {'mood': 4, 'emotions': ['calm']}}, ))
        first = entry.serve(self.store, req1, self.agents, 'app-1', **context)
        replay = entry.serve(self.store, req1, self.agents, 'app-1', **context)
        self.assertEqual(first, replay)
        created = json.loads(json.loads(first[1])['result']['content'][0]['text'])
        self.assertEqual(created['action'], 'created')
        self.assertEqual(created['version'], 1)
        self.assertEqual(self.store.read('alpha', created['id'])['body'], 'morning')
        self.assertEqual(self.store.read('alpha', created['id'])['surface'], 'web')

        # Second append adds to today's entry; retry with same X-Request-Id must NOT add the text twice.
        req2 = json.dumps(rpc('journal_write', {
            'collection': 'journal', 'body': 'afternoon', 'append': True, 'fields': {'emotions': ['calm', 'bright']}}))
        second = entry.serve(self.store, req2, self.agents, 'app-2', **context)
        replay2 = entry.serve(self.store, req2, self.agents, 'app-2', **context)
        self.assertEqual(second, replay2)
        appended = json.loads(json.loads(second[1])['result']['content'][0]['text'])
        self.assertEqual(appended, {'id': created['id'], 'version': 2, 'action': 'appended'})
        rec = self.store.read('alpha', created['id'])
        self.assertEqual(rec['body'], 'morning\n\nafternoon')
        self.assertEqual(rec['fields'], {'mood': 4, 'emotions': ['calm', 'bright']})
        # Different body with same X-Request-Id conflicts with 409.
        conflict = entry.serve(self.store, json.dumps(rpc('journal_write', {
            'collection': 'journal', 'body': 'different', 'append': True})), self.agents, 'app-2', **context)
        self.assertEqual(conflict[0], 409)


class AppendTests(Base):
    def test_append_creates_today_and_appends_via_mcp(self):
        self.configure()
        # Use a deterministic clock + timezone on the local store.
        import datetime as dt
        now = [dt.datetime(2026, 11, 22, 18, 0, tzinfo=dt.timezone.utc)]    # 2026-11-23 01:00+07:00
        server.Config.pen = LocalFilesPenStore(self.tmp.name, 'Asia/Jakarta', lambda: now[0])

        first = self.ok('journal_write', {'collection': 'daily', 'body': 'first note', 'append': True})
        self.assertEqual(first['action'], 'created')
        self.assertEqual(first['version'], 1)
        rec1 = self.ok('journal_read', {'id': first['id']})
        self.assertEqual(rec1['title'], '2026-11-23')
        self.assertEqual(rec1['body'], 'first note')

        now[0] = dt.datetime(2026, 11, 23, 3, 0, tzinfo=dt.timezone.utc)    # still 2026-11-23 in Asia/Jakarta
        second = self.ok('pen_write', {'collection': 'daily', 'body': 'second note', 'append': True})
        self.assertEqual(second, {'id': first['id'], 'version': 2, 'action': 'appended'})
        self.assertEqual(self.ok('journal_read', {'id': first['id']})['body'], 'first note\n\nsecond note')

    def test_append_by_id_and_refusals(self):
        self.configure()
        flat_id = self.ok('journal_write', {'collection': 'notes', 'body': 'base', 'title': 'Note'})['id']
        # Refused on non-by-day journal without id.
        err, msg = self.tool('journal_write', {'collection': 'notes', 'body': 'nope', 'append': True})
        self.assertTrue(err)
        self.assertIn('by_day', msg)
        # Works with id on flat journal.
        res = self.ok('journal_write', {'collection': 'notes', 'body': 'added', 'append': True, 'id': flat_id})
        self.assertEqual(res, {'id': flat_id, 'version': 2, 'action': 'appended'})
        self.assertEqual(self.ok('journal_read', {'id': flat_id})['body'], 'base\n\nadded')
        # Invalid option combinations are refused.
        self.assertTrue(self.tool('journal_write', {'collection': 'notes', 'body': 'x', 'id': flat_id})[0])
        self.assertTrue(self.tool('journal_write', {'collection': 'notes', 'body': 'x', 'append': True, 'pinned': True})[0])
        self.assertTrue(self.tool('journal_write', {'collection': 'daily', 'body': 'x', 'append': True, 'by_day': False})[0])

    def test_append_merges_declared_fields_and_stamps_surface(self):
        self.configure({'SCRIPTORIUM_FIELDS': json.dumps(ONE_AGENT_FIELDS)})
        # Stamp surface via handle(..., surface=...).
        def call_surface(args, surface):
            answer = server.handle(rpc('journal_write', args), ('alpha',), surface=surface)
            res = answer['result']
            text = res['content'][0]['text']
            return res['isError'], text if res['isError'] else json.loads(text)

        err, created = call_surface({
            'collection': 'journal', 'body': 'one', 'append': True,
            'fields': {'mood': 3, 'emotions': ['calm'], 'markers': {'a': 1}, 'keynotes': 'k1', 'seen': False},
        }, 'desktop')
        self.assertFalse(err, created)
        self.assertEqual(self.ok('journal_read', {'id': created['id']})['surface'], 'desktop')

        err, appended = call_surface({
            'collection': 'journal', 'body': 'two', 'append': True,
            'fields': {'emotions': ['calm', 'focused'], 'markers': {'b': 2}, 'seen': True},
        }, 'mobile')
        self.assertFalse(err, appended)
        self.assertEqual(appended, {'id': created['id'], 'version': 2, 'action': 'appended'})
        rec = self.ok('journal_read', {'id': created['id']})
        self.assertEqual(rec['surface'], 'mobile')
        self.assertEqual(rec['fields'], {
            'mood': 3, 'emotions': ['calm', 'focused'], 'markers': {'a': 1, 'b': 2}, 'keynotes': 'k1', 'seen': True,
        })
        # Undeclared field and null field are refused on append.
        self.assertTrue(call_surface({'collection': 'journal', 'body': 'bad', 'append': True, 'fields': {'nope': 1}}, None)[0])
        self.assertTrue(call_surface({'collection': 'journal', 'body': 'bad', 'append': True, 'fields': {'keynotes': None}}, None)[0])

    def test_append_enforces_required_fields_only_on_create(self):
        self.configure({'SCRIPTORIUM_FIELDS': json.dumps(ONE_AGENT_FIELDS)})
        # Creating today's entry via append without fields or without required `mood` is refused and creates nothing.
        err, msg = self.tool('journal_write', {'collection': 'journal', 'body': 'no fields', 'append': True})
        self.assertTrue(err)
        self.assertIn('missing required fields: mood', msg)
        err, msg = self.tool('journal_write', {
            'collection': 'journal', 'body': 'partial', 'append': True, 'fields': {'emotions': ['calm']},
        })
        self.assertTrue(err)
        self.assertIn('missing required fields: mood', msg)
        self.assertEqual(self.ok('journal_list', {'collection': 'journal'}), [])

        # Creating with required `mood` succeeds; subsequent appends allow partial or omitted fields.
        created = self.ok('journal_write', {
            'collection': 'journal', 'body': 'first', 'append': True, 'fields': {'mood': 4},
        })
        self.assertEqual(created['action'], 'created')
        appended_partial = self.ok('journal_write', {
            'collection': 'journal', 'body': 'second', 'append': True, 'fields': {'emotions': ['steady']},
        })
        self.assertEqual(appended_partial, {'id': created['id'], 'version': 2, 'action': 'appended'})
        appended_none = self.ok('journal_write', {'collection': 'journal', 'body': 'third', 'append': True})
        self.assertEqual(appended_none, {'id': created['id'], 'version': 3, 'action': 'appended'})
        appended_by_id = self.ok('journal_write', {
            'collection': 'journal', 'body': 'fourth', 'append': True, 'id': created['id'],
        })
        self.assertEqual(appended_by_id, {'id': created['id'], 'version': 4, 'action': 'appended'})
        rec = self.ok('journal_read', {'id': created['id']})
        self.assertEqual(rec['fields'], {'mood': 4, 'emotions': ['steady']})

        # Async path enforces the same rule on SqlPenStore.
        import asyncio
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        sql_store = SqlPenStore(Sqlite3Sql(connection))
        parsed_fields = server.Config.fields
        text, is_err = asyncio.run(server.pen_tool_async(
            'journal_write', {'collection': 'journal', 'body': 'missing', 'append': True},
            ('alpha',), store=sql_store, fields=parsed_fields))
        self.assertTrue(is_err)
        self.assertIn('missing required fields: mood', text)
        self.assertEqual(sql_store.list('alpha', 'journal'), [])
        text, is_err = asyncio.run(server.pen_tool_async(
            'journal_write', {'collection': 'journal', 'body': 'ok', 'append': True, 'fields': {'mood': 5}},
            ('alpha',), store=sql_store, fields=parsed_fields))
        self.assertFalse(is_err, text)
        text, is_err = asyncio.run(server.pen_tool_async(
            'journal_write', {'collection': 'journal', 'body': 'more', 'append': True},
            ('alpha',), store=sql_store, fields=parsed_fields))
        self.assertFalse(is_err, text)
        self.assertEqual(json.loads(text)['action'], 'appended')

    def test_custom_store_without_append_refuses_and_never_emulates(self):
        edit_calls = []

        class NoAppendStore:
            contract_version = 3
            def collections(self, writer, *, visible=None): return []
            def write(self, writer, collection, body, title=None, pinned=False, by_day=None, *, surface=None, fields=None):
                return 'a' * 32
            def edit(self, writer, id, based_on, body=None, title=None, pinned=None, *, surface=None, fields=None):
                edit_calls.append(id)
                return 2
            def read(self, writer, id, version=None, *, visible=None): raise ValueError('entry not found')
            def list(self, writer, collection, pinned_only=False, day=None, limit=20, *, surface=None, visible=None): return []
            def history(self, writer, id, *, visible=None): raise ValueError('entry not found')
            def delete(self, writer, id): return None

        module = type(sys)('no_append_fixture')
        module.store = NoAppendStore()
        with mock.patch.dict(sys.modules, {'no_append_fixture': module}):
            self.configure(pen_ref='no_append_fixture:store')
            self.assertEqual(self.ok('journal_write', {'collection': 'journal', 'body': 'ok'}), {'id': 'a' * 32})
            err, msg = self.tool('journal_write', {'collection': 'journal', 'body': 'nope', 'append': True})
            self.assertTrue(err)
            self.assertIn('does not support append', msg)
            self.assertEqual(edit_calls, [])

    def test_current_entry_only_store_replaces_on_append(self):
        backing = {}

        class CurrentOnlyWithAppend:
            contract_version = 3
            keeps_history = False
            def collections(self, writer, *, visible=None): return []
            def write(self, writer, collection, body, title=None, pinned=False, by_day=None, *, surface=None, fields=None):
                eid = 'b' * 32
                backing[(writer, eid)] = {'id': eid, 'collection': collection, 'writer': writer, 'body': body,
                                          'version': 1, 'by_day': bool(by_day), 'pinned': pinned}
                return eid
            def append(self, writer, collection, body, *, id=None, day=None, fields=None, title=None, surface=None, on_create=None):
                target = backing.get((writer, id)) if id else next(
                    (r for (w, _), r in backing.items() if w == writer and r['collection'] == collection), None)
                if target is None:
                    if on_create is not None:
                        on_create()
                    eid = 'c' * 32
                    backing[(writer, eid)] = {'id': eid, 'collection': collection, 'writer': writer, 'body': body,
                                              'title': title or (day or '2026-11-22'), 'version': 1, 'by_day': True,
                                              'pinned': False}
                    if surface is not None:
                        backing[(writer, eid)]['surface'] = surface
                    return {'id': eid, 'version': 1, 'action': 'created'}
                target['body'] = pen_module.append_body(target['body'], body)
                target['version'] += 1
                if surface is not None:
                    target['surface'] = surface
                return {'id': target['id'], 'version': target['version'], 'action': 'appended'}
            def edit(self, writer, id, based_on, body=None, title=None, pinned=None, *, surface=None, fields=None): return 2
            def read(self, writer, id, version=None, *, visible=None):
                if (writer, id) not in backing:
                    raise ValueError('entry not found')
                return dict(backing[(writer, id)])
            def list(self, writer, collection, pinned_only=False, day=None, limit=20, *, surface=None, visible=None): return []
            def delete(self, writer, id): backing.pop((writer, id), None)

        module = type(sys)('current_only_append')
        module.store = CurrentOnlyWithAppend()
        with mock.patch.dict(sys.modules, {'current_only_append': module}):
            self.configure(pen_ref='current_only_append:store')
            r1 = self.ok('journal_write', {'collection': 'daily', 'body': 'one', 'append': True})
            r2 = self.ok('journal_write', {'collection': 'daily', 'body': 'two', 'append': True})
            self.assertEqual(r1['action'], 'created')
            self.assertEqual(r2, {'id': r1['id'], 'version': 2, 'action': 'appended'})
            self.assertEqual(len(backing), 1)
            self.assertEqual(self.ok('journal_read', {'id': r1['id']})['body'], 'one\n\ntwo')

    def test_custom_store_append_on_create_contract_and_kwargs_guard(self):
        # Case 1: Custom store following the documented append(..., on_create=None) contract.
        backing = {}
        on_create_calls = []

        class DocumentedAppendStore:
            contract_version = 3
            def collections(self, writer, *, visible=None): return []
            def write(self, writer, collection, body, title=None, pinned=False, by_day=None, *, surface=None, fields=None):
                return '1' * 32
            def append(self, writer, collection, body, *, id=None, day=None, fields=None, title=None, surface=None, on_create=None):
                target = backing.get((writer, id)) if id else next(
                    (r for (w, _), r in backing.items() if w == writer and r['collection'] == collection), None)
                if target is None:
                    if on_create is not None:
                        on_create_calls.append(True)
                        on_create()
                    eid = '2' * 32
                    rec = {'id': eid, 'collection': collection, 'writer': writer, 'body': body,
                           'title': title or (day or '2026-11-22'), 'version': 1, 'by_day': True, 'pinned': False}
                    if fields:
                        rec['fields'] = dict(fields)
                    backing[(writer, eid)] = rec
                    return {'id': eid, 'version': 1, 'action': 'created'}
                target['body'] = pen_module.append_body(target['body'], body)
                target['version'] += 1
                if fields:
                    target['fields'] = pen_module.append_fields(target.get('fields'), fields)
                return {'id': target['id'], 'version': target['version'], 'action': 'appended'}
            def edit(self, writer, id, based_on, body=None, title=None, pinned=None, *, surface=None, fields=None): return 2
            def read(self, writer, id, version=None, *, visible=None):
                if (writer, id) not in backing:
                    raise ValueError('entry not found')
                return dict(backing[(writer, id)])
            def list(self, writer, collection, pinned_only=False, day=None, limit=20, *, surface=None, visible=None): return []
            def history(self, writer, id, *, visible=None): raise ValueError('entry not found')
            def delete(self, writer, id): backing.pop((writer, id), None)

        mod1 = type(sys)('doc_append_store')
        mod1.store = DocumentedAppendStore()
        with mock.patch.dict(sys.modules, {'doc_append_store': mod1}):
            self.configure({'SCRIPTORIUM_FIELDS': json.dumps(ONE_AGENT_FIELDS)}, pen_ref='doc_append_store:store')
            # Creating without required `mood` invokes on_create inside the store and inserts nothing.
            err, msg = self.tool('journal_write', {'collection': 'journal', 'body': 'first', 'append': True})
            self.assertTrue(err)
            self.assertIn('missing required fields: mood', msg)
            self.assertEqual(len(on_create_calls), 1)
            self.assertEqual(backing, {})
            # Creating with required `mood` succeeds; a body-only append to the existing entry succeeds without on_create.
            created = self.ok('journal_write', {
                'collection': 'journal', 'body': 'first', 'append': True, 'fields': {'mood': 4},
            })
            self.assertEqual(created['action'], 'created')
            appended = self.ok('journal_write', {'collection': 'journal', 'body': 'second', 'append': True})
            self.assertEqual(appended, {'id': created['id'], 'version': 2, 'action': 'appended'})
            self.assertEqual(len(on_create_calls), 1)
            rec = self.ok('journal_read', {'id': created['id']})
            self.assertEqual(rec['body'], 'first\n\nsecond')
            self.assertEqual(rec['fields'], {'mood': 4})

        # Case 2: Custom store taking **kwargs and ignoring on_create must NOT create an entry without required fields.
        kwargs_backing = {}
        kwargs_calls = []

        class KwargsIgnoringAppendStore(DocumentedAppendStore):
            def append(self, writer, collection, body, **kwargs):
                kwargs_calls.append(kwargs)
                eid = '3' * 32
                kwargs_backing[(writer, eid)] = {'id': eid, 'collection': collection, 'writer': writer, 'body': body,
                                                 'version': 1, 'by_day': True, 'pinned': False}
                return {'id': eid, 'version': 1, 'action': 'created'}

        mod2 = type(sys)('kwargs_append_store')
        mod2.store = KwargsIgnoringAppendStore()
        with mock.patch.dict(sys.modules, {'kwargs_append_store': mod2}):
            self.configure({'SCRIPTORIUM_FIELDS': json.dumps(ONE_AGENT_FIELDS)}, pen_ref='kwargs_append_store:store')
            err, msg = self.tool('journal_write', {'collection': 'journal', 'body': 'no mood', 'append': True})
            self.assertTrue(err)
            self.assertIn('missing required fields: mood', msg)
            self.assertEqual(kwargs_calls, [])
            self.assertEqual(kwargs_backing, {})

        # Case 3: Custom store declaring on_create=None explicitly but ignoring it on create fails loudly.
        class BuggyExplicitOnCreateStore(DocumentedAppendStore):
            def append(self, writer, collection, body, *, id=None, day=None, fields=None, title=None, surface=None, on_create=None):
                return {'id': '4' * 32, 'version': 1, 'action': 'created'}

        mod3 = type(sys)('buggy_on_create_store')
        mod3.store = BuggyExplicitOnCreateStore()
        with mock.patch.dict(sys.modules, {'buggy_on_create_store': mod3}):
            self.configure({'SCRIPTORIUM_FIELDS': json.dumps(ONE_AGENT_FIELDS)}, pen_ref='buggy_on_create_store:store')
            err, msg = self.tool('journal_write', {'collection': 'journal', 'body': 'no mood', 'append': True})
            self.assertTrue(err)
            self.assertIn('on_create', msg)


class SharedStoreRulesTests(unittest.TestCase):
    def test_check_fields_rules_directly(self):
        declared = {'n': pen_module.field_spec('number'), 'r': pen_module.field_spec({'type': 'text', 'required': True})}
        pen_module.check_fields(declared, {'n': 1, 'r': 'x'}, True)
        pen_module.check_fields(declared, {'n': None}, False)
        for bad, creating in (({'n': 1}, True), ({'r': None}, False), ({'n': True, 'r': 'x'}, True), ({'z': 1}, False)):
            with self.subTest(given=bad), self.assertRaises(ValueError):
                pen_module.check_fields(declared, bad, creating)

    def test_transfer_keeps_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = LocalFilesPenStore(tmp)
            id = local.write('alpha', 'journal', 'x', fields={'mood': 1})
            local.edit('alpha', id, 1, fields={'extra': {'a': [1]}})
            connection = sqlite3.connect(':memory:')
            self.addCleanup(connection.close)
            cloud = SqlPenStore(Sqlite3Sql(connection))
            cloud.import_snapshot('alpha', local.export_snapshot('alpha'))
            self.assertEqual(cloud.read('alpha', id)['fields'], {'mood': 1, 'extra': {'a': [1]}})
            self.assertEqual(cloud.export_snapshot('alpha'), local.export_snapshot('alpha'))


if __name__ == '__main__':
    unittest.main()


class AppendEdgeCaseTests(unittest.TestCase):
    def test_merge_keeps_true_and_1_apart_even_nested(self):
        from engine.pen import append_fields
        merged = append_fields({'tags': [True, {'x': False}]}, {'tags': [1, {'x': 0}, True]})
        self.assertEqual(merged['tags'], [True, {'x': False}, 1, {'x': 0}])

    @unittest.skipUnless(hasattr(__import__('time'), 'tzset'), 'needs time.tzset')
    def test_new_day_entry_uses_that_days_offset_without_a_store_timezone(self):
        import datetime as dt, os, time
        from engine.pen import LocalFilesPenStore
        old = os.environ.get('TZ')
        try:
            os.environ['TZ'] = 'America/New_York'; time.tzset()
            store = LocalFilesPenStore.__new__(LocalFilesPenStore)
            store.timezone = None
            july = dt.datetime(2026, 7, 10, 12, 0).astimezone()
            self.assertTrue(store._created_for_day(july, '2026-01-15').endswith('-05:00'))
            self.assertTrue(store._created_for_day(july, '2026-07-04').endswith('-04:00'))
        finally:
            if old is None: os.environ.pop('TZ', None)
            else: os.environ['TZ'] = old
            time.tzset()
