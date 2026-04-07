/**
 * Extension discovery.
 *
 * Scans two locations for packages that declare the "kamp-extension" keyword:
 *   1. <appPath>/extensions/  — first-party extensions bundled with the app
 *   2. <appPath>/node_modules/ — installed npm extensions
 *
 * A package qualifies when its package.json has "kamp-extension" in `keywords`
 * and its declared entry point (`main`) exists on disk.
 *
 * Security phases:
 *   Phase 1 – First-party: must appear in first-party-allowlist.json AND carry
 *             the keyword. Receives full contextBridge (KampAPI) access.
 *   Phase 2 – Community: keyword present but not on the allow-list. Rendered
 *             in a sandboxed iframe with no contextBridge access.
 *
 * The allow-list is the mechanism that prevents arbitrary npm packages from
 * claiming the keyword and escalating into contextBridge access.
 */

import { app } from 'electron'
import { readdirSync, readFileSync, existsSync } from 'fs'
import { join, resolve } from 'path'
import type { ExtensionInfo } from '../shared/kampAPI'
import allowlistData from './first-party-allowlist.json'

// Set of package names approved for Phase 1 (contextBridge) access.
const FIRST_PARTY_IDS = new Set<string>(allowlistData.extensions)

function scanDir(dir: string): ExtensionInfo[] {
  if (!existsSync(dir)) return []

  const results: ExtensionInfo[] = []

  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue

    const pkgPath = join(dir, entry.name, 'package.json')
    if (!existsSync(pkgPath)) continue

    try {
      const pkg = JSON.parse(readFileSync(pkgPath, 'utf8')) as {
        name?: string
        displayName?: string
        keywords?: string[]
        main?: string
        kamp?: { permissions?: string[] }
      }

      if (!Array.isArray(pkg.keywords) || !pkg.keywords.includes('kamp-extension')) continue

      const mainFile = pkg.main ?? 'index.js'
      const entryPath = resolve(dir, entry.name, mainFile)
      if (!existsSync(entryPath)) continue

      // Read code here in the main process (which has fs access) so the
      // renderer never needs a file:// import — it loads via a Blob URL instead.
      const code = readFileSync(entryPath, 'utf8')

      const id = pkg.name ?? entry.name
      // Phase 1 requires the package to be explicitly on the allow-list;
      // everything else is Phase 2 (community / sandboxed).
      const phase: 1 | 2 = FIRST_PARTY_IDS.has(id) ? 1 : 2

      const permissions: string[] = Array.isArray(pkg.kamp?.permissions)
        ? (pkg.kamp.permissions as string[]).filter((p) => typeof p === 'string')
        : []

      // Warn when an extension combines library.read and network.fetch —
      // this pairing can exfiltrate library metadata to an external server.
      if (permissions.includes('library.read') && permissions.includes('network.fetch')) {
        console.warn(
          `[kamp] extension "${id}" declares both library.read and network.fetch. ` +
            'Review this extension carefully — it can read your library and contact external servers.'
        )
      }

      results.push({
        id,
        name: pkg.displayName ?? pkg.name ?? entry.name,
        code,
        phase,
        permissions
      })
    } catch {
      // Skip packages with malformed package.json.
    }
  }

  return results
}

export function discoverExtensions(): ExtensionInfo[] {
  const appPath = app.getAppPath()
  // First-party extensions ship alongside the app.
  const builtinDir = join(appPath, 'extensions')
  // Installed npm extensions live in node_modules.
  const nodeModulesDir = join(appPath, 'node_modules')

  return [...scanDir(builtinDir), ...scanDir(nodeModulesDir)]
}
