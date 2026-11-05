// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
// Serves the built People page and a small JSON API over your own files: the person cards a writer made,
// and the facts, suggestions and accepted fields the nightly run keeps. It listens on this computer only.
//   node serve.mjs --cards CARDS_DIR --out OUT_DIR [--port 8788] [--engine PATH_TO_review.py]
// Changes are made by review.py (run as a child process), so there is one implementation and one lock.
import { createServer } from 'node:http'
import { createHash } from 'node:crypto'
import { spawn } from 'node:child_process'
import { readFile, lstat, readdir } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { extname, join, resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8' }
const MAX_BODY = 1024 * 1024
const CARD_PREFIX = 'person-'
const CARD_SUFFIX = '.json'
// The card's fixed fields, the same list the nightly run suggests values for.
const FIELDS = ['care', 'birthday', 'dates', 'accounts', 'runs_on', 'work', 'place', 'timezone', 'pronouns']
const LOCAL_NAMES = new Set(['127.0.0.1', 'localhost', '[::1]'])

export function isLocalHost(host) {
  if (typeof host !== 'string' || !host) return false
  try { return LOCAL_NAMES.has(new URL(`http://${host}`).hostname) } catch { return false }
}

export function isLocalOrigin(origin) {
  try { const u = new URL(origin); return u.protocol === 'http:' && LOCAL_NAMES.has(u.hostname) } catch { return false }
}

/** True when the Origin's host:port is exactly the Host the request arrived on. */
export function sameAuthority(origin, host) {
  try { return new URL(origin).host === String(host || '') } catch { return false }
}

/** A card key that is safe to hand to review.py as one argument (mirrors nightly.safe_key, and no leading dash). */
export function safeKey(key) {
  return typeof key === 'string' && key !== '' && !/[\\/]/.test(key) && !key.startsWith('.') && !key.startsWith('-')
}

async function readJson(path, fallback) {
  try { return JSON.parse(await readFile(path, 'utf8')) } catch { return fallback }
}

const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v)

/** person-<key>.json files in the person shape the page reads. Accepted values from OUT/fields/<key>.json are merged in. */
export async function loadPeople(cardsDir, outDir) {
  let names = []
  try { names = (await readdir(cardsDir)).sort() } catch { return [] }
  const people = []
  for (const name of names) {
    if (!name.startsWith(CARD_PREFIX) || !name.endsWith(CARD_SUFFIX)) continue
    const path = join(cardsDir, name)
    try { if ((await lstat(path)).isSymbolicLink()) continue } catch { continue }
    const doc = await readJson(path, null)
    if (!isObj(doc)) continue
    const key = name.slice(CARD_PREFIX.length, -CARD_SUFFIX.length)
    const mems = (Array.isArray(doc.memories) ? doc.memories : []).filter(isObj)
    let pname = (mems.find((m) => m.person) || {}).person || ''
    if (!pname) pname = String(doc.title || '').replace(' (person card)', '').trim()
    if (!pname) continue
    const own = isObj(doc.fields) ? doc.fields : {}
    const accepted = outDir ? await readJson(join(outDir, 'fields', key + '.json'), {}) : {}
    const acc = isObj(accepted) ? accepted : {}
    const aka = []
    for (const src of [doc.aka, own.aka, acc.aka]) for (const a of Array.isArray(src) ? src : []) if (!aka.includes(a)) aka.push(a)
    const notes = typeof doc.notes === 'string' ? doc.notes
      : typeof doc.standing === 'string' ? doc.standing
      : mems.map((m) => m.summary).filter(Boolean).join('\n\n')
    const person = { id: key, name: String(pname), kind: String(doc.kind || 'human'), aka, notes, circles: [], links: [] }
    if (typeof doc.standing === 'string') person.standing = doc.standing
    for (const f of FIELDS) {
      const v = f in acc ? acc[f] : f in own ? own[f] : doc[f]
      if (v !== undefined && v !== null && v !== '' && !(Array.isArray(v) && v.length === 0)) person[f] = v
    }
    people.push(person)
  }
  return people
}

/** Pending suggestions in the shape the page reads. The id is review.py's own number. */
export async function loadSuggestions(outDir) {
  const sugs = await readJson(join(outDir, 'suggestions.json'), [])
  if (!Array.isArray(sugs)) return []
  return sugs.filter((s) => isObj(s) && s.status === 'pending' && Number.isInteger(s.n))
    .map((s) => ({ ...s, id: s.n, person_name: s.name || s.person_name || '' }))
}

/** A short fingerprint of a fact's own words; the same as review.py's, so a change can name the fact it means. */
export function factRev(f) {
  const q = typeof f.quote === 'string' ? f.quote : ''
  const t = typeof f.fact === 'string' ? f.fact : ''
  return createHash('sha1').update(q + '\0' + t, 'utf8').digest('hex').slice(0, 12)
}

/** Facts of one person; the id is KEY:N, N counting from 1 as review.py does. */
export async function loadFacts(outDir, key) {
  const facts = await readJson(join(outDir, 'facts', key + '.json'), [])
  if (!Array.isArray(facts)) return []
  return facts.map((f, i) => (isObj(f) ? { ...f, id: key + ':' + (i + 1), rev: factRev(f) } : null)).filter(Boolean)
}

/** Runs review.py with python3 and no shell. Resolves { code, stdout, stderr }. */
export function runPython(args) {
  return new Promise((done) => {
    let child
    try { child = spawn('python3', args, { stdio: ['ignore', 'pipe', 'pipe'] }) } catch (e) { done({ code: 127, stdout: '', stderr: String(e.message) }); return }
    let stdout = ''; let stderr = ''
    child.stdout.on('data', (c) => { stdout += c })
    child.stderr.on('data', (c) => { stderr += c })
    child.on('error', (e) => done({ code: 127, stdout, stderr: String(e.message) }))
    child.on('close', (code) => done({ code: code ?? 1, stdout, stderr }))
  })
}

export function findEngine() {
  for (const p of [resolve(here, '..', '..', 'engine', 'review.py'), resolve(here, '..', '..', 'engine', 'writer', 'review.py')]) if (existsSync(p)) return p
  return resolve(here, '..', '..', 'engine', 'review.py')
}

export function createLocalHost({ cards, out, engine = findEngine(), root = resolve(here, 'build'), run = runPython }) {
  if (!cards || !out) throw new Error('createLocalHost needs cards and out')
  const review = (...rest) => run([engine, '--out', out, '--cards', cards, ...rest])
  const json = (res, status, body) => res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' }).end(JSON.stringify(body))
  const failed = (res, r) => {
    const msg = (r.stderr || r.stdout || 'review.py failed').trim().replace(/^review: /, '').split('\n').pop()
    json(res, /busy/.test(msg) ? 409 : 400, { error: msg })
  }

  async function body(req) {
    const chunks = []; let size = 0
    for await (const c of req) { size += c.length; if (size > MAX_BODY) throw Object.assign(new Error('too large'), { status: 413 }); chunks.push(c) }
    if (!size) return {}
    if (!/^application\/json\b/i.test(req.headers['content-type'] || '')) throw Object.assign(new Error('JSON only'), { status: 415 })
    try { const v = JSON.parse(Buffer.concat(chunks).toString('utf8')); return isObj(v) ? v : {} } catch { throw Object.assign(new Error('bad JSON'), { status: 400 }) }
  }

  async function api(req, res, url) {
    const path = url.pathname
    const m = req.method
    if (path === '/api/people' && m === 'GET') return json(res, 200, { people: await loadPeople(cards, out) })
    if (path === '/api/suggestions' && m === 'GET') return json(res, 200, { suggestions: await loadSuggestions(out) })
    let hit = path.match(/^\/api\/suggestions\/(\d+)\/(accept|dismiss)$/)
    if (hit && m === 'POST') {
      const r = await review(hit[2], hit[1])
      return r.code === 0 ? json(res, 200, { ok: true }) : failed(res, r)
    }
    if (path === '/api/facts' && m === 'GET') {
      const key = url.searchParams.get('person') || ''
      if (!safeKey(key)) return json(res, 400, { error: 'unknown person' })
      return json(res, 200, { facts: await loadFacts(out, key) })
    }
    hit = path.match(/^\/api\/facts\/([^/]+)\/(\d+)$/)
    if (hit && m === 'PATCH') {
      let key
      try { key = decodeURIComponent(hit[1]) } catch { return json(res, 400, { error: 'bad key' }) }
      if (!safeKey(key)) return json(res, 400, { error: 'unknown person' })
      const b = await body(req)
      let r
      if (b.rev !== undefined && !/^[0-9a-f]{12}$/.test(String(b.rev))) return json(res, 400, { error: 'bad rev' })
      const expect = b.rev === undefined ? [] : ['--expect', String(b.rev)]
      if (b.remove === true) r = await review(...expect, 'remove-fact', '--', key, hit[2])
      else if (typeof b.fact === 'string' && b.fact.trim()) r = await review(...expect, 'edit-fact', '--', key, hit[2], b.fact)
      else return json(res, 400, { error: 'send { fact } or { remove: true }' })
      return r.code === 0 ? json(res, 200, { ok: true }) : failed(res, r)
    }
    return json(res, 404, { error: 'not found' })
  }

  return createServer(async (req, res) => {
    // Only requests addressed to this computer (a rebound DNS name arrives with a foreign Host and is refused).
    if (!isLocalHost(req.headers.host)) { res.writeHead(421).end(); return }
    // A write must come from this page, not from another site the browser has open.
    // A write must come from this page itself: same host AND port (Codex 21 Oct: another app on a different localhost
    // port could otherwise press Accept). A browser always sends Origin on a cross-origin write.
    if (req.method !== 'GET' && req.headers.origin && !(isLocalOrigin(req.headers.origin) && sameAuthority(req.headers.origin, req.headers.host))) {
      res.writeHead(403).end(); return
    }
    let url
    try { url = new URL(req.url || '/', 'http://localhost') } catch { res.writeHead(400).end(); return }
    try {
      if (url.pathname.startsWith('/api/')) return await api(req, res, url)
      if (req.method !== 'GET') { res.writeHead(405).end(); return }
      const name = url.pathname === '/' ? 'index.html' : url.pathname.slice(1)
      if (!['index.html', 'main.js', 'main.css'].includes(name)) { res.writeHead(404).end(); return }
      try {
        const data = await readFile(resolve(root, name))
        res.writeHead(200, { 'content-type': TYPES[extname(name)], 'cache-control': 'no-store' }).end(data)
      } catch { res.writeHead(404).end('Not built yet: run make people-page') }
    } catch (e) {
      if (!res.headersSent) json(res, e.status || 500, { error: e.status ? e.message : 'server error' })
    }
  })
}

function parseArgs(argv) {
  const o = {}
  for (let i = 0; i < argv.length; i += 2) {
    const k = argv[i]
    if (!['--cards', '--out', '--port', '--engine'].includes(k) || argv[i + 1] === undefined) throw new Error('usage: node serve.mjs --cards CARDS_DIR --out OUT_DIR [--port 8788] [--engine review.py]')
    o[k.slice(2)] = argv[i + 1]
  }
  if (!o.cards || !o.out) throw new Error('usage: node serve.mjs --cards CARDS_DIR --out OUT_DIR [--port 8788] [--engine review.py]')
  return o
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  let o
  try { o = parseArgs(process.argv.slice(2)) } catch (e) { console.error(e.message); process.exit(2) }
  const port = Number(o.port || 8788)
  createLocalHost({ cards: resolve(o.cards), out: resolve(o.out), ...(o.engine ? { engine: resolve(o.engine) } : {}) })
    .listen(port, '127.0.0.1', () => console.log(`People page: http://127.0.0.1:${port}/  (cards: ${resolve(o.cards)}, found: ${resolve(o.out)})`))
}
