// Solance House — Copyright (c) 2026 House of Solance. All rights reserved. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
import { beforeEach, expect, it, describe } from 'vitest'
import { resetHost, useHost, DEFAULT_LABELS, setHost } from '../host.js'
import { fakeAdapter } from './helpers.jsx'

beforeEach(() => resetHost())

describe('people host', () => {
  it('useHost throws before setup', () => {
    expect(() => useHost()).toThrow(/no host set/)
  })
  it('setHost needs an adapter with every method', () => {
    expect(() => setHost({})).toThrow(/adapter/)
    expect(() => setHost({ adapter: { listPeople() {} } })).toThrow(/missing/)
  })
  it('merges labels over neutral defaults', () => {
    const h = setHost({ adapter: fakeAdapter(), labels: { titleLead: "Maya's" } })
    expect(h.labels).toEqual({ ...DEFAULT_LABELS, titleLead: "Maya's" })
    expect(JSON.stringify(DEFAULT_LABELS)).not.toMatch(new RegExp(['An' + 'ne', 'Sol' + 'ance'].join('|')))   // built from pieces so this file names no House name
  })
})
