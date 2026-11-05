// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
// node --test dashboard/tests/serve.test.mjs  (the local server's mapping and routes, with temp folders and a fake review.py runner)
import { after, before, test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, writeFileSync, symlinkSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createLocalHost, safeKey, loadSuggestions, loadFacts, sameAuthority, loadPeople } from '../example-host/serve.mjs'

let dir, cards, out, server, base
const calls = []
let nextResult = { code: 0, stdout: 'ok', stderr: '' }
const j = (p, v) => { mkdirSync(join(p, '..'), { recursive: true }); writeFileSync(p, JSON.stringify(v)) }

before(async () => {
  dir = mkdtempSync(join(tmpdir(), 'people-serve-'))
  cards = join(dir, 'cards'); out = join(dir, 'out')
  j(join(cards, 'person-alma.json'), { title: 'Alma (person card)', kind: 'human', aka: ['Al'], fields: { work: 'baker', aka: ['Mimi'] },
    memories: [{ person: 'Alma', summary: 'Bakes bread.' }] })
  j(join(cards, 'person-rex.json'), { title: 'Rex (person card)', kind: 'pet', notes: 'A dog.', place: 'Porto', memories: [] })
  j(join(cards, 'person-ghost.json'), { memories: [] })                       // no name: skipped
  writeFileSync(join(cards, 'notes.txt'), 'not a card')
  symlinkSync(join(cards, 'person-alma.json'), join(cards, 'person-linked.json'))
  j(join(out, 'fields', 'alma.json'), { place: 'Lisbon', aka: ['Mimi', 'Alma B'], work: 'chef' })
  j(join(out, 'facts', 'alma.json'), [{ fact: 'Keeps bees.', quote: 'I keep bees', source: 'chat 1', said_on: '2026-10-01' }, { fact: 'Has a cat.', quote: 'my cat', said_on: '2026-10-02' }])
  j(join(out, 'suggestions.json'), [
    { n: 3, status: 'accepted', person_id: 'alma', name: 'Alma', field: 'work', value: 'x' },
    { n: 4, status: 'pending', person_id: 'alma', name: 'Alma', field: 'birthday', value: '1990-04-01', quote: 'born on the first of April' },
    { n: 7, status: 'pending', person_id: 'rex', name: 'Rex', field: 'work', value: 'guard dog' },
  ])
  const run = async (args) => { calls.push(args); return nextResult }
  server = createLocalHost({ cards, out, engine: '/engine/review.py', root: dir, run })
  await new Promise((r) => server.listen(0, '127.0.0.1', r))
  base = 'http://127.0.0.1:' + server.address().port
})
after(() => { server.close(); rmSync(dir, { recursive: true, force: true }) })

const api = (path, init) => fetch(base + path, init)
const patch = (path, body) => api(path, { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })

test('people: card key is the id, accepted fields win, aka merged, symlinks and nameless cards skipped', async () => {
  const people = await loadPeople(cards, out)
  assert.deepEqual(people.map((p) => p.id), ['alma', 'rex'])
  const alma = people[0]
  assert.equal(alma.name, 'Alma')
  assert.deepEqual(alma.aka, ['Al', 'Mimi', 'Alma B'])
  assert.equal(alma.work, 'chef')
  assert.equal(alma.place, 'Lisbon')
  assert.equal(alma.notes, 'Bakes bread.')
  assert.deepEqual(alma.links, [])
  const rex = people[1]
  assert.equal(rex.name, 'Rex'); assert.equal(rex.kind, 'pet'); assert.equal(rex.notes, 'A dog.'); assert.equal(rex.place, 'Porto')
})

test('people: GET /api/people', async () => {
  const body = await (await api('/api/people')).json()
  assert.equal(body.people.length, 2)
})

test('suggestions: pending only, id is the number review.py uses, person name set', async () => {
  const s = await loadSuggestions(out)
  assert.deepEqual(s.map((x) => [x.id, x.person_name]), [[4, 'Alma'], [7, 'Rex']])
  const body = await (await api('/api/suggestions')).json()
  assert.equal(body.suggestions.length, 2)
})

test('suggestions: accept and dismiss call review.py with that number', async () => {
  calls.length = 0
  assert.equal((await api('/api/suggestions/7/accept', { method: 'POST' })).status, 200)
  assert.equal((await api('/api/suggestions/4/dismiss', { method: 'POST' })).status, 200)
  assert.deepEqual(calls, [
    ['/engine/review.py', '--out', out, '--cards', cards, 'accept', '7'],
    ['/engine/review.py', '--out', out, '--cards', cards, 'dismiss', '4'],
  ])
})

test('suggestions: a failing review.py shows its message; busy is 409', async () => {
  nextResult = { code: 1, stdout: '', stderr: 'review: suggestion 9 is already accepted\n' }
  let r = await api('/api/suggestions/9/accept', { method: 'POST' })
  assert.equal(r.status, 400); assert.equal((await r.json()).error, 'suggestion 9 is already accepted')
  nextResult = { code: 1, stdout: '', stderr: 'review: the nightly run is busy; try again\n' }
  r = await api('/api/suggestions/9/accept', { method: 'POST' })
  assert.equal(r.status, 409)
  nextResult = { code: 0, stdout: 'ok', stderr: '' }
})

test('facts: ids are KEY:N counting from 1', async () => {
  const facts = await loadFacts(out, 'alma')
  assert.deepEqual(facts.map((f) => f.id), ['alma:1', 'alma:2'])
  const body = await (await api('/api/facts?person=alma')).json()
  assert.equal(body.facts[1].fact, 'Has a cat.')
  assert.deepEqual((await (await api('/api/facts?person=nobody')).json()).facts, [])
  assert.equal((await api('/api/facts?person=..%2Fx')).status, 400)
})

test('facts: edit and remove go through review.py (dash-guarded)', async () => {
  calls.length = 0
  assert.equal((await patch('/api/facts/alma/2', { fact: '-Has two cats.' })).status, 200)
  assert.equal((await patch('/api/facts/alma/1', { remove: true })).status, 200)
  assert.deepEqual(calls, [
    ['/engine/review.py', '--out', out, '--cards', cards, 'edit-fact', '--', 'alma', '2', '-Has two cats.'],
    ['/engine/review.py', '--out', out, '--cards', cards, 'remove-fact', '--', 'alma', '1'],
  ])
})

test('facts: bad input never reaches review.py', async () => {
  calls.length = 0
  assert.equal((await patch('/api/facts/alma/1', {})).status, 400)
  assert.equal((await patch('/api/facts/alma/1', { fact: '   ' })).status, 400)
  assert.equal((await patch('/api/facts/-x/1', { fact: 'a' })).status, 400)
  assert.ok((await patch('/api/facts/%2e%2e/1', { fact: 'a' })).status >= 400)   // the URL parser folds it away (404)
  assert.equal((await api('/api/facts/alma/1', { method: 'PATCH', headers: { 'content-type': 'text/plain' }, body: '{"remove":true}' })).status, 415)
  assert.equal(calls.length, 0)
})

test('only this computer: a foreign Host or Origin is refused', async () => {
  const { request } = await import('node:http')
  const status = await new Promise((resolve) => {
    const req = request({ host: '127.0.0.1', port: server.address().port, path: '/api/people', headers: { host: 'example.com' } }, (res) => { res.resume(); resolve(res.statusCode) })
    req.end()
  })
  assert.equal(status, 421)
  const r = await api('/api/suggestions/4/accept', { method: 'POST', headers: { origin: 'http://example.com' } })
  assert.equal(r.status, 403)
  // another app on a different localhost port may not write either
  const other = await api('/api/suggestions/4/accept', { method: 'POST', headers: { origin: 'http://localhost:3000' } })
  assert.equal(other.status, 403)
  assert.ok(sameAuthority('http://127.0.0.1:8788', '127.0.0.1:8788'))
  assert.ok(!sameAuthority('http://127.0.0.1:3000', '127.0.0.1:8788'))
})

test('safeKey', () => {
  assert.ok(safeKey('alma')); assert.ok(!safeKey('')); assert.ok(!safeKey('../a')); assert.ok(!safeKey('.a')); assert.ok(!safeKey('-a')); assert.ok(!safeKey('a/b'))
})

test('facts: each fact carries a fingerprint and a change passes it to review.py as --expect', async () => {
  const facts = await loadFacts(out, 'alma')
  assert.match(facts[1].rev, /^[0-9a-f]{12}$/)
  assert.notEqual(facts[0].rev, facts[1].rev)
  calls.length = 0
  assert.equal((await patch('/api/facts/alma/2', { remove: true, rev: facts[1].rev })).status, 200)
  assert.equal((await patch('/api/facts/alma/2', { fact: 'x', rev: facts[1].rev })).status, 200)
  assert.deepEqual(calls, [
    ['/engine/review.py', '--out', out, '--cards', cards, '--expect', facts[1].rev, 'remove-fact', '--', 'alma', '2'],
    ['/engine/review.py', '--out', out, '--cards', cards, '--expect', facts[1].rev, 'edit-fact', '--', 'alma', '2', 'x'],
  ])
  calls.length = 0
  assert.equal((await patch('/api/facts/alma/2', { remove: true, rev: '--out' })).status, 400)
  assert.equal(calls.length, 0)
})
