// @vitest-environment jsdom
// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
import { beforeEach, expect, it, describe } from 'vitest'
import { resetHost, setHost } from '../host.js'
import { ANN, mount, fakeAdapter, click } from './helpers.jsx'
import People from '../pages/People.jsx'

beforeEach(() => { document.body.innerHTML = ''; resetHost() })

const SUG = { id: 's1', person_id: 'p1', person_name: 'Alma', field: 'place', value: 'Lisbon', status: 'pending' }

describe('People page', () => {
  it('lists people from listPeople and shows default labels', async () => {
    const adapter = fakeAdapter()
    setHost({ adapter })
    const { el } = await mount(People)
    expect(adapter.listPeople).toHaveBeenCalled()
    expect(el.textContent).toContain('Alma')
    expect(el.textContent).toContain('Bruno')
    expect(el.querySelector('h2').textContent).toBe('Your people')
  })

  it('uses host labels', async () => {
    setHost({ adapter: fakeAdapter(), labels: { titleLead: "Maya's", subtitle: 'Hello there' } })
    const { el } = await mount(People)
    expect(el.querySelector('h2').textContent).toBe("Maya's people")
    expect(el.textContent).toContain('Hello there')
  })

  it('Accept calls decideSuggestion then reloads the roster', async () => {
    const adapter = fakeAdapter({ listSuggestions: async () => ({ suggestions: [SUG] }) })
    setHost({ adapter })
    const { el } = await mount(People)
    const before = adapter.listPeople.mock.calls.length
    await click(el, 'Accept')
    expect(adapter.decideSuggestion).toHaveBeenCalledWith('s1', 'accept')
    expect(adapter.listPeople.mock.calls.length).toBe(before + 1)
  })

  it('Found in chats loads facts and Remove calls removeFact', async () => {
    const adapter = fakeAdapter()
    setHost({ adapter })
    const { el } = await mount(People)
    await click(el, 'Found in chats')
    expect(adapter.listFacts).toHaveBeenCalledWith('p1')
    expect(el.textContent).toContain('likes tea')
    await click(el.querySelector('.space-y-2.mt-1'), 'Remove')
    expect(adapter.removeFact).toHaveBeenCalledWith('f1')
  })

  it('Found in chats Edit then Save calls editFact', async () => {
    const adapter = fakeAdapter()
    setHost({ adapter })
    const { el } = await mount(People)
    await click(el, 'Found in chats')
    const facts = el.querySelector('.space-y-2.mt-1')
    await click(facts, 'Edit')
    await click(facts, 'Save')
    expect(adapter.editFact).toHaveBeenCalledWith('f1', 'likes tea')
  })

  it('Save on the edit form calls updatePerson with the same payload shape', async () => {
    const adapter = fakeAdapter()
    setHost({ adapter })
    const { el } = await mount(People)
    await click(el, 'Edit')
    await click(el, 'Save changes')
    expect(adapter.updatePerson).toHaveBeenCalledTimes(1)
    const [id, payload] = adapter.updatePerson.mock.calls[0]
    expect(id).toBe('p1')
    expect(payload).toMatchObject({ name: 'Alma', kind: 'human' })
    expect(Object.keys(payload)).toEqual(expect.arrayContaining(['name', 'kind', 'place', 'timezone']))
    expect(payload).not.toHaveProperty('circles')
  })

  it('an adapter error with .auth=true shows no banner', async () => {
    const e = Object.assign(new Error('Not signed in'), { auth: true })
    setHost({ adapter: fakeAdapter({ listPeople: async () => { throw e } }) })
    const { el } = await mount(People)
    expect(el.textContent).not.toContain('Not signed in')
  })

  it('another adapter error shows its message', async () => {
    setHost({ adapter: fakeAdapter({ listPeople: async () => { throw new Error('Boom') } }) })
    const { el } = await mount(People)
    expect(el.textContent).toContain('Boom')
  })
})
