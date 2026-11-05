// Solance House — Copyright (c) 2026 House of Solance. All rights reserved. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
import { expect, it, vi, describe } from 'vitest'
import { createLocalFilesAdapter } from '../adapters/local-files.js'
import { resetHost, setHost } from '../host.js'

const ok = (body) => ({ ok: true, status: 200, json: async () => body })

describe('local files adapter', () => {
  it('has every method the host contract needs', () => {
    resetHost()
    expect(() => setHost({ adapter: createLocalFilesAdapter({ fetchImpl: vi.fn() }) })).not.toThrow()
  })

  it('refuses card editing with a clear message', async () => {
    const a = createLocalFilesAdapter({ fetchImpl: vi.fn() })
    for (const call of [() => a.createPerson({}), () => a.updatePerson('x', {}), () => a.deletePerson('x'), () => a.addLink('x', {}), () => a.removeLink('l')]) {
      await expect(call()).rejects.toThrow('Editing cards happens in your card files in this version')
    }
    expect(await a.listLinks('x')).toEqual({ links: [] })
  })

  it('maps suggestions and facts to the API', async () => {
    const fetchImpl = vi.fn(async () => ok({ ok: true }))
    const a = createLocalFilesAdapter({ baseUrl: '/api', fetchImpl })
    await a.decideSuggestion(7, 'accept')
    await a.decideSuggestion(4, 'dismiss')
    await a.editFact('alma:2', 'new text')
    await a.removeFact('alma:1')
    await a.listFacts('alma')
    const calls = fetchImpl.mock.calls.map(([u, i]) => [i?.method || 'GET', u, i?.body])
    expect(calls).toEqual([
      ['POST', '/api/suggestions/7/accept', undefined],
      ['POST', '/api/suggestions/4/dismiss', undefined],
      ['PATCH', '/api/facts/alma/2', JSON.stringify({ fact: 'new text' })],
      ['PATCH', '/api/facts/alma/1', JSON.stringify({ remove: true })],
      ['GET', '/api/facts?person=alma', undefined],
    ])
  })

  it('turns an error body into an Error', async () => {
    const a = createLocalFilesAdapter({ fetchImpl: async () => ({ ok: false, status: 400, json: async () => ({ error: 'nope' }) }) })
    await expect(a.listPeople()).rejects.toThrow('nope')
  })
})

describe('local files adapter: fact fingerprints', () => {
  it('sends the fingerprint from the last listing with an edit or remove', async () => {
    const fetchImpl = vi.fn(async (u) => ok(u.startsWith('/api/facts?') ? { facts: [{ id: 'alma:1', rev: 'aaaaaaaaaaaa' }, { id: 'alma:2', rev: 'bbbbbbbbbbbb' }] } : { ok: true }))
    const a = createLocalFilesAdapter({ fetchImpl })
    await a.listFacts('alma')
    await a.editFact('alma:2', 'new')
    await a.removeFact('alma:1')
    const bodies = fetchImpl.mock.calls.slice(1).map(([, i]) => i.body)
    expect(bodies).toEqual([JSON.stringify({ fact: 'new', rev: 'bbbbbbbbbbbb' }), JSON.stringify({ remove: true, rev: 'aaaaaaaaaaaa' })])
  })
})
