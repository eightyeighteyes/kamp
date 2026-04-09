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
import { execFile } from 'child_process'
import { readdirSync, readFileSync, existsSync } from 'fs'
import { join, resolve } from 'path'
import type {
  ExtensionInfo,
  ExtensionInstallResult,
  ExtensionSettingSchema
} from '../shared/kampAPI'
import allowlistData from './first-party-allowlist.json'
import { readManifest, writeManifest } from './communityManifest'

// Set of package names approved for Phase 1 (contextBridge) access.
const FIRST_PARTY_IDS = new Set<string>(allowlistData.extensions)

function scanDir(dir: string, installedFrom: ExtensionInfo['installedFrom']): ExtensionInfo[] {
  if (!existsSync(dir)) return []

  const results: ExtensionInfo[] = []

  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    // isDirectory() returns false for symlinks; isSymbolicLink() catches local file: installs.
    if (!entry.isDirectory() && !entry.isSymbolicLink()) continue

    const pkgPath = join(dir, entry.name, 'package.json')
    if (!existsSync(pkgPath)) continue

    try {
      const pkg = JSON.parse(readFileSync(pkgPath, 'utf8')) as {
        name?: string
        version?: string
        displayName?: string
        keywords?: string[]
        main?: string
        kamp?: { permissions?: string[]; settings?: ExtensionSettingSchema[] }
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

      // Read settings schema, validating that each entry has at minimum key, label, type.
      const rawSettings = pkg.kamp?.settings
      const settings: ExtensionSettingSchema[] | undefined = Array.isArray(rawSettings)
        ? rawSettings.filter(
            (s): s is ExtensionSettingSchema =>
              typeof s === 'object' &&
              s !== null &&
              typeof s.key === 'string' &&
              typeof s.label === 'string' &&
              typeof s.type === 'string'
          )
        : undefined

      results.push({
        id,
        name: pkg.displayName ?? pkg.name ?? entry.name,
        version: pkg.version ?? '0.0.0',
        code,
        phase,
        permissions,
        settings: settings && settings.length > 0 ? settings : undefined,
        installedFrom
      })
    } catch {
      // Skip packages with malformed package.json.
    }
  }

  return results
}

// Wrap execFile in a promise for async IPC handlers (avoids blocking the main process).
function runNpm(args: string[]): Promise<void> {
  return new Promise((resolve, reject) => {
    execFile('npm', args, { timeout: 60_000 }, (err) => {
      if (err) reject(err)
      else resolve()
    })
  })
}

/**
 * Install a community extension by npm package name or local directory path.
 *
 * For local installs, validates that a package.json with the kamp-extension
 * keyword exists before calling npm. The installed package is persisted to the
 * community-extensions manifest so it can be reinstalled on next app launch.
 */
export async function installExtension(
  source: 'npm' | 'local',
  nameOrPath: string
): Promise<ExtensionInstallResult> {
  try {
    return await _installExtension(source, nameOrPath)
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    console.error('[kamp] installExtension threw unexpectedly:', msg)
    return { ok: false, error: `Unexpected error: ${msg}` }
  }
}

async function _installExtension(
  source: 'npm' | 'local',
  nameOrPath: string
): Promise<ExtensionInstallResult> {
  const appPath = app.getAppPath()
  console.log(`[kamp] installExtension source=${source} arg=${nameOrPath} appPath=${appPath}`)

  if (source === 'local') {
    const pkgJsonPath = join(nameOrPath, 'package.json')
    if (!existsSync(pkgJsonPath)) {
      return { ok: false, error: 'No package.json found at this path.' }
    }
    try {
      const pkg = JSON.parse(readFileSync(pkgJsonPath, 'utf8')) as {
        keywords?: string[]
        name?: string
      }
      if (!Array.isArray(pkg.keywords) || !pkg.keywords.includes('kamp-extension')) {
        return {
          ok: false,
          error: 'Package does not declare the "kamp-extension" keyword — not a kamp extension.'
        }
      }
    } catch {
      return { ok: false, error: 'Could not read package.json.' }
    }
  }

  if (source === 'npm' && !/^(@[\w.-]+\/)?[\w.-]+$/.test(nameOrPath)) {
    return { ok: false, error: 'Invalid npm package name.' }
  }

  try {
    const installArg = source === 'local' ? `file:${nameOrPath}` : nameOrPath
    // --no-save: don't update package.json or package-lock.json (avoids full
    //   dependency-tree resolution against the host app's lockfile, which hangs)
    // --no-audit: skip network audit request
    // --ignore-scripts: don't run postinstall/prepare scripts from the extension
    console.log(`[kamp] running npm install ${installArg} --prefix ${appPath}`)
    await runNpm([
      'install',
      installArg,
      '--prefix',
      appPath,
      '--no-save',
      '--no-audit',
      '--ignore-scripts'
    ])
    console.log('[kamp] npm install completed')
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    console.error('[kamp] npm install failed:', msg)
    if (msg.includes('ENOENT')) {
      return { ok: false, error: 'npm must be installed and on your PATH.' }
    }
    return { ok: false, error: `Install failed: ${msg}` }
  }

  // Persist to manifest (deduplicating by name/path).
  const entries = readManifest()

  let resolvedName: string
  if (source === 'local') {
    try {
      const pkg = JSON.parse(readFileSync(join(nameOrPath, 'package.json'), 'utf8')) as {
        name?: string
      }
      resolvedName = pkg.name ?? nameOrPath
    } catch {
      resolvedName = nameOrPath
    }
    const filtered = entries.filter(
      (e) => !(e.source === 'local' && e.path === nameOrPath) && e.name !== resolvedName
    )
    writeManifest([...filtered, { source: 'local', name: resolvedName, path: nameOrPath }])
  } else {
    resolvedName = nameOrPath
    const filtered = entries.filter((e) => e.name !== resolvedName)
    writeManifest([...filtered, { source: 'npm', name: resolvedName }])
  }

  return { ok: true, id: resolvedName }
}

/**
 * Uninstall a community extension by its package id.
 *
 * First-party extensions (on the allow-list) cannot be uninstalled this way.
 */
export async function uninstallExtension(id: string): Promise<ExtensionInstallResult> {
  if (FIRST_PARTY_IDS.has(id)) {
    return { ok: false, error: 'Cannot uninstall a first-party extension.' }
  }

  const appPath = app.getAppPath()

  try {
    await runNpm(['uninstall', id, '--prefix', appPath])
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return { ok: false, error: `Uninstall failed: ${msg}` }
  }

  const entries = readManifest().filter((e) => e.name !== id)
  writeManifest(entries)

  return { ok: true, id }
}

export function discoverExtensions(): ExtensionInfo[] {
  const appPath = app.getAppPath()
  // First-party extensions ship alongside the app.
  const builtinDir = join(appPath, 'extensions')
  // Installed npm extensions live in node_modules.
  const nodeModulesDir = join(appPath, 'node_modules')

  return [...scanDir(builtinDir, 'bundled'), ...scanDir(nodeModulesDir, 'npm')]
}
