// @vitest-environment jsdom
// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
import { beforeEach, expect, it, describe } from 'vitest'
import { act } from 'react'
import { resetHost, setHost } from '../host.js'
import { BOB, ANN, mount, fakeAdapter, click } from './helpers.jsx'
import People from '../pages/People.jsx'
import { notesNewestFirst } from '../pages/peopleNotes.js'

beforeEach(() => { document.body.innerHTML = ''; resetHost() })

describe('undo remove', () => {
  it('leaves empty circles and discord_ids out of createPerson', async () => {
    const adapter = fakeAdapter({
      listPeople: async () => ({ people: [BOB] }),
      deletePerson: async () => ({ person: { ...BOB, circles: [], discord_ids: null, created_at: 'x' } }),
    })
    setHost({ adapter })
    const { el } = await mount(People)
    await click(el, 'Remove')
    const confirm = [...el.querySelectorAll('button')].find(b => /^(yes|confirm|remove)/i.test(b.textContent.trim()) && b.textContent.trim() !== 'Remove')
    if (confirm) await click(el, confirm.textContent.trim())
    await click(el, 'Undo')
    expect(adapter.createPerson).toHaveBeenCalledTimes(1)
    const payload = adapter.createPerson.mock.calls[0][0]
    expect(payload).not.toHaveProperty('circles')
    expect(payload).not.toHaveProperty('discord_ids')
    expect(payload.confirm_new).toBe(true)
  })
})

describe('signed-out save', () => {
  it('re-enables the form after an auth error', async () => {
    const e = Object.assign(new Error('Not signed in'), { auth: true })
    const adapter = fakeAdapter({ updatePerson: async () => { throw e } })
    setHost({ adapter })
    const { el } = await mount(People)
    await click(el, 'Edit')
    await click(el, 'Save changes')
    const save = [...el.querySelectorAll('button')].find(b => b.textContent.includes('Save changes') || b.textContent.includes('Saving'))
    expect(save.textContent).toContain('Save changes')
    expect(save.disabled).toBe(false)
  })
})

describe('stale suggestions', () => {
  it('ignores a slow listSuggestions response after a decision', async () => {
    const SUG = { id: 's1', person_id: 'p1', person_name: 'Alma', field: 'place', value: 'Lisbon', status: 'pending' }
    let calls = 0
    let late
    const adapter = fakeAdapter({
      listSuggestions: () => {
        calls++
        if (calls === 1) return Promise.resolve({ suggestions: [SUG] })
        return new Promise(r => { late = () => r({ suggestions: [SUG] }) })
      },
    })
    setHost({ adapter })
    const { el } = await mount(People)
    // second load starts (a save elsewhere), its suggestions response stays pending
    await click(el, 'Edit')
    await click(el, 'Save changes')
    expect(late).toBeTypeOf('function')
    await click(el, 'Accept')
    expect(el.textContent).not.toContain('Lisbon')
    await act(async () => { late() })
    await act(async () => {})
    expect(el.textContent).not.toContain('Lisbon')
  })
})

describe('notesNewestFirst with prepended entries', () => {
  it('puts the newer same-day line first (stored order is newest on top)', () => {
    const out = notesNewestFirst('[2026-10-14] second\n[2026-10-14] first')
    expect(out.indexOf('second')).toBeLessThan(out.indexOf('first'))
  })
  it('sorts by date and keeps the undated original last', () => {
    const out = notesNewestFirst('[2026-10-20] new\n[2026-10-01] old\n\nOriginal text')
    expect(out).toBe('[2026-10-20] new\n\n[2026-10-01] old\n\nOriginal text')
  })
  it('keeps a leading undated preamble last', () => {
    expect(notesNewestFirst('Intro\n[2026-10-01] a')).toBe('[2026-10-01] a\n\nIntro')
  })
  it('keeps continuation lines with their entry', () => {
    expect(notesNewestFirst('[2026-10-02] b\nmore b\n[2026-10-01] a')).toBe('[2026-10-02] b\nmore b\n\n[2026-10-01] a')
  })
})

describe('relation names from listPeople', () => {
  it('shows and searches the other person name, not someone', async () => {
    const A = { ...ANN, links: [{ id: 'l1', other_id: 'p2', relation: 'sister', other: { name: 'Bruno' } }] }
    setHost({ adapter: fakeAdapter({ listPeople: async () => ({ people: [A, BOB] }) }) })
    const { el } = await mount(People)
    expect(el.textContent).toContain('Bruno')
    expect(el.textContent).toContain('sister')
    expect(el.textContent).not.toContain('someone')
  })
})

describe('stale roster refresh', () => {
  it('does not restore a person deleted while a refresh was in flight', async () => {
    let calls = 0
    let late
    const adapter = fakeAdapter({
      listPeople: () => {
        calls++
        if (calls === 1) return Promise.resolve({ people: [BOB] })
        return new Promise(r => { late = () => r({ people: [BOB] }) })
      },
      deletePerson: async () => ({ person: BOB }),
    })
    setHost({ adapter })
    const { el } = await mount(People)
    await click(el, 'Refresh')
    expect(late).toBeTypeOf('function')
    await click(el, 'Remove')
    const confirm = [...el.querySelectorAll('button')].find(b => /^(yes|confirm|remove)/i.test(b.textContent.trim()) && b.textContent.trim() !== 'Remove')
    if (confirm) await click(el, confirm.textContent.trim())
    await act(async () => { late() })
    await act(async () => {})
    expect(el.textContent).toContain('No one written down yet')
  })
})
