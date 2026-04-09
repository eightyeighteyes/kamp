/**
 * Persistence for user-installed community extensions.
 *
 * Stores the install manifest at userData/community-extensions.json so that
 * installed extensions survive app restarts (packaged builds do not retain
 * node_modules between launches).
 */

import { app } from 'electron'
import { join } from 'path'
import { readFileSync, writeFileSync, existsSync } from 'fs'

export type ManifestEntry =
  | { source: 'npm'; name: string }
  | { source: 'local'; name: string; path: string }

type Manifest = { installed: ManifestEntry[] }

function manifestPath(): string {
  return join(app.getPath('userData'), 'community-extensions.json')
}

export function readManifest(): ManifestEntry[] {
  try {
    if (!existsSync(manifestPath())) return []
    const raw = JSON.parse(readFileSync(manifestPath(), 'utf8')) as Manifest
    return Array.isArray(raw.installed) ? raw.installed : []
  } catch {
    return []
  }
}

export function writeManifest(entries: ManifestEntry[]): void {
  writeFileSync(manifestPath(), JSON.stringify({ installed: entries }, null, 2))
}
