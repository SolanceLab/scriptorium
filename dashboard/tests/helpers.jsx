// Solance House — Copyright (c) 2026 House of Solance. All rights reserved. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
import { vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'

globalThis.IS_REACT_ACT_ENVIRONMENT = true

export const BOB = { id: 'p2', name: 'Bruno', kind: 'human', circles: [], links: [], notes: '', aka: [], connections: [], accounts: [], dates: [], discord_ids: [] }
export const ANN = { id: 'p1', name: 'Alma', kind: 'human', circles: ['Friends'], links: [], notes: '', aka: [], connections: [], accounts: [], dates: [], discord_ids: [] }

export function fakeAdapter(overrides = {}) {
  return {
    listPeople: vi.fn(async () => ({ people: [ANN, BOB] })),
    createPerson: vi.fn(async (p) => ({ person: p })),
    updatePerson: vi.fn(async (id, p) => ({ person: { id, ...p } })),
    deletePerson: vi.fn(async (id) => ({ person: { id } })),
    listLinks: vi.fn(async () => ({ links: [] })),
    addLink: vi.fn(async () => ({})),
    removeLink: vi.fn(async () => ({})),
    listSuggestions: vi.fn(async () => ({ suggestions: [] })),
    decideSuggestion: vi.fn(async () => ({})),
    listFacts: vi.fn(async () => ({ facts: [{ id: 'f1', fact: 'likes tea', said_on: '2026-10-01', source: 'chat 1' }] })),
    editFact: vi.fn(async () => ({})),
    removeFact: vi.fn(async () => ({})),
    ...overrides,
  }
}

export async function mount(Component) {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const root = createRoot(el)
  await act(async () => { root.render(<Component />) })
  await act(async () => {})
  return { el, unmount: () => act(async () => root.unmount()) }
}

export async function click(el, text) {
  const b = [...el.querySelectorAll('button')].find((x) => x.textContent.trim() === text || x.textContent.trim().startsWith(text + ' ') || x.textContent.trim().startsWith(text + '▸') || x.textContent.trim().startsWith(text + '▾'))
  if (!b) throw new Error('no button: ' + text)
  await act(async () => { b.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
  await act(async () => {})
}
