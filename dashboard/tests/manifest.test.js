// Solance House — Copyright (c) 2026 House of Solance. All rights reserved. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
import { expect, it, describe } from 'vitest'
import manifest from '../manifest.js'

describe('people manifest', () => {
  it('has the module shape the registry expects', () => {
    expect(manifest.id).toBe('people')
    expect(manifest.label).toBe('People')
    expect(typeof manifest.description).toBe('string')
    expect(manifest.defaultEnabled).toBe(true)
    expect(manifest.nav.items).toEqual([{ path: 'people', label: 'People' }])
  })
  it('exports the route and setup', () => {
    expect(manifest.routes.map((r) => r.path)).toEqual(['people'])
    expect(typeof manifest.routes[0].load).toBe('function')
    expect(typeof manifest.setup).toBe('function')
  })
})
