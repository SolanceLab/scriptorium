// @vitest-environment jsdom
// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
import { beforeEach, expect, it, describe } from 'vitest'
import { resetHost, setHost } from '../host.js'
import { mount, fakeAdapter, click } from './helpers.jsx'
import People from '../pages/People.jsx'

beforeEach(() => { document.body.innerHTML = ''; resetHost() })

const SUG = { id: 's1', person_id: 'p1', person_name: 'Alma', field: 'place', value: 'Lisbon', status: 'pending' }
const buttons = (el) => [...el.querySelectorAll('button')].map((b) => b.textContent.trim())

describe('capabilities.editCards = false', () => {
  it('hides Add person, Edit and Remove on cards, and still lists people, suggestions and facts', async () => {
    const adapter = fakeAdapter({ listSuggestions: async () => ({ suggestions: [SUG] }) })
    setHost({ adapter, capabilities: { editCards: false, links: false } })
    const { el } = await mount(People)
    expect(el.textContent).toContain('Alma')
    expect(el.textContent).toContain('Bruno')
    expect(el.textContent).toContain('Lisbon')
    const labels = buttons(el)
    expect(labels).not.toContain('+ Add person')
    expect(labels).not.toContain('Edit')
    expect(labels).not.toContain('Remove')
    await click(el, 'Found in chats')
    expect(el.textContent).toContain('likes tea')
    expect(buttons(el)).toContain('Edit')       // a fact can still be edited and removed
    expect(buttons(el)).toContain('Remove')
    await click(el, 'Accept')
    expect(adapter.decideSuggestion).toHaveBeenCalledWith('s1', 'accept')
    expect(adapter.createPerson).not.toHaveBeenCalled()
  })

  it('a host that omits capabilities shows everything', async () => {
    setHost({ adapter: fakeAdapter() })
    const { el } = await mount(People)
    expect(buttons(el)).toContain('+ Add person')
    expect(buttons(el)).toContain('Edit')
  })

  it('links = false alone hides the relation controls in the edit form and never calls addLink or removeLink', async () => {
    const adapter = fakeAdapter({ listPeople: async () => ({ people: [{ id: 'p1', name: 'Alma', kind: 'human', circles: [], aka: [], links: [{ id: 'l1', relation: 'sister', other_id: 'p2', other: { name: 'Bruno' } }] }, { id: 'p2', name: 'Bruno', kind: 'human', circles: [], aka: [], links: [] }] }) })
    setHost({ adapter, capabilities: { links: false } })
    const { el } = await mount(People)
    await click(el, 'Edit')
    expect(el.textContent).toContain('Save')
    expect(el.textContent).not.toContain('saved straight away')
    expect(el.querySelector('[title="Remove relation"]')).toBeNull()
    expect(adapter.addLink).not.toHaveBeenCalled()
    expect(adapter.removeLink).not.toHaveBeenCalled()
  })

  it('links on (default) still shows the relation controls in the edit form', async () => {
    setHost({ adapter: fakeAdapter() })
    const { el } = await mount(People)
    await click(el, 'Edit')
    expect(el.textContent).toContain('saved straight away')
  })
})
