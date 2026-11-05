// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
import { expect, it, describe } from 'vitest'
import { mkdtempSync, statSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { buildExampleHost } from '../example-host/build.mjs'

describe('example host build', () => {
  it('a second build into the same folder leaves main.css the same size', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'people-build-'))
    try {
      await buildExampleHost(dir)
      const first = statSync(join(dir, 'main.css')).size
      await buildExampleHost(dir)
      expect(statSync(join(dir, 'main.css')).size).toBe(first)
    } finally { rmSync(dir, { recursive: true, force: true }) }
  }, 60000)
})
