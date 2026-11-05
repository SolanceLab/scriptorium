// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitest/config'

// Rooted at the module so jsdom and react resolve from its node_modules.
export default defineConfig({
  root: fileURLToPath(new URL('..', import.meta.url)),
  test: {
    environment: 'node',
    include: ['dashboard/tests/**/*.test.js'],
  },
})
