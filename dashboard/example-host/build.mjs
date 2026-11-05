// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
// Bundles the example host into ./build (or the folder given as the first argument).
// React and the module pages are bundled, and Tailwind is compiled into main.css, so the page loads no outside script.
import { build } from 'esbuild'
import { mkdirSync, writeFileSync, copyFileSync, existsSync, rmSync, readFileSync } from 'node:fs'
import postcss from 'postcss'
import tailwindcss from 'tailwindcss'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))

export async function buildExampleHost(outDir = resolve(here, 'build')) {
  mkdirSync(outDir, { recursive: true })
  // Start from no stylesheet, so a rebuild never stacks the last build's styles under the new ones.
  rmSync(resolve(outDir, 'main.css'), { force: true })
  await build({
    entryPoints: { main: resolve(here, 'main.jsx') },
    outdir: outDir,
    bundle: true,
    format: 'iife',
    jsx: 'automatic',
    loader: { '.js': 'jsx' },
    define: { 'process.env.NODE_ENV': '"production"' },
    logLevel: 'warning',
  })
  // Tailwind utilities used by the pages, compiled locally and appended to the page styles.
  const input = resolve(here, 'tailwind.css')
  const tw = await postcss([tailwindcss(resolve(here, 'tailwind.config.cjs'))]).process(readFileSync(input, 'utf8'), { from: input })
  const cssPath = resolve(outDir, 'main.css')
  writeFileSync(cssPath, tw.css + '\n' + (existsSync(cssPath) ? readFileSync(cssPath, 'utf8') : ''))
  copyFileSync(resolve(here, 'index.html'), resolve(outDir, 'index.html'))
  return outDir
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  console.log('built', await buildExampleHost(process.argv[2] ? resolve(process.argv[2]) : undefined))
}
