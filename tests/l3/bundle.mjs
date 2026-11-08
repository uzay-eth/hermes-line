// Bundle tests/l3/render-entry.js for Node with the same aliases the preview uses. Offline: esbuild,
// React and the UI kit come from the read-only Hermes checkout (HERMES_AGENT_DIR).
import { mkdtempSync } from 'node:fs'
import { createRequire } from 'node:module'
import { homedir, tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
export const REPO = resolve(HERE, '../..')
export const HERMES = resolve(process.env.HERMES_AGENT_DIR || join(homedir(), '.hermes/hermes-agent'))
const DESKTOP = join(HERMES, 'apps/desktop')

let cached
export async function loadRender() {
  if (cached) return cached
  const esbuild = createRequire(join(HERMES, 'package.json'))('esbuild')
  const outfile = join(mkdtempSync(join(tmpdir(), 'hermes-line-l3-')), 'render.mjs')
  await esbuild.build({
    entryPoints: [join(HERE, 'render-entry.js')],
    outfile,
    bundle: true,
    format: 'esm',
    platform: 'node',
    target: 'node20',
    jsx: 'automatic',
    logLevel: 'error',
    alias: { '@hermes/plugin-sdk': join(REPO, 'tools/pane-preview/sdk-stub.js'), '@': join(DESKTOP, 'src') },
    nodePaths: [join(DESKTOP, 'node_modules'), join(HERMES, 'node_modules')],
    resolveExtensions: ['.tsx', '.ts', '.jsx', '.js', '.mjs', '.json'],
    loader: { '.svg': 'dataurl', '.png': 'dataurl', '.css': 'empty', '.woff2': 'empty', '.ttf': 'empty', '.woff': 'empty' },
    define: {
      'process.env.NODE_ENV': '"production"',
      'import.meta.env': '{"DEV":false,"PROD":true,"MODE":"production","SSR":true}'
    },
    banner: { js: "import { createRequire as __cr } from 'node:module'; const require = __cr(import.meta.url);" }
  })
  cached = await import(pathToFileURL(outfile).href)
  return cached
}
