/**
 * useExtensionInstall — manages community extension install/uninstall state.
 *
 * Wraps the KampAPI IPC calls and exposes loading/error state so the UI can
 * render progress and error feedback without each component managing its own
 * async state.
 */

import { useState, useCallback } from 'react'

export type InstallStatus =
  | { status: 'idle' }
  | { status: 'installing'; label: string }
  | { status: 'error'; message: string }

export type ExtensionInstallHook = {
  npmStatus: InstallStatus
  localStatus: InstallStatus
  uninstallStatus: InstallStatus
  installByName: (name: string, onSuccess: (id: string) => void) => Promise<void>
  installByPath: (path: string, onSuccess: (id: string) => void) => Promise<void>
  uninstall: (id: string, onSuccess: () => void) => Promise<void>
}

export function useExtensionInstall(): ExtensionInstallHook {
  const [npmStatus, setNpmStatus] = useState<InstallStatus>({ status: 'idle' })
  const [localStatus, setLocalStatus] = useState<InstallStatus>({ status: 'idle' })
  const [uninstallStatus, setUninstallStatus] = useState<InstallStatus>({ status: 'idle' })

  const installByName = useCallback(
    async (name: string, onSuccess: (id: string) => void): Promise<void> => {
      const trimmed = name.trim()
      if (!trimmed) return
      setNpmStatus({ status: 'installing', label: trimmed })
      try {
        const result = await window.KampAPI.extensions.install('npm', trimmed)
        if (result.ok) {
          setNpmStatus({ status: 'idle' })
          onSuccess(result.id)
        } else {
          setNpmStatus({ status: 'error', message: result.error })
        }
      } catch (err) {
        setNpmStatus({
          status: 'error',
          message: err instanceof Error ? err.message : 'Install failed.'
        })
      }
    },
    []
  )

  const installByPath = useCallback(
    async (path: string, onSuccess: (id: string) => void): Promise<void> => {
      setLocalStatus({ status: 'installing', label: path })
      try {
        const result = await window.KampAPI.extensions.install('local', path)
        if (result.ok) {
          setLocalStatus({ status: 'idle' })
          onSuccess(result.id)
        } else {
          setLocalStatus({ status: 'error', message: result.error })
        }
      } catch (err) {
        setLocalStatus({
          status: 'error',
          message: err instanceof Error ? err.message : 'Install failed.'
        })
      }
    },
    []
  )

  const uninstall = useCallback(async (id: string, onSuccess: () => void): Promise<void> => {
    setUninstallStatus({ status: 'installing', label: id })
    try {
      const result = await window.KampAPI.extensions.uninstall(id)
      if (result.ok) {
        setUninstallStatus({ status: 'idle' })
        onSuccess()
      } else {
        setUninstallStatus({ status: 'error', message: result.error })
      }
    } catch (err) {
      setUninstallStatus({
        status: 'error',
        message: err instanceof Error ? err.message : 'Uninstall failed.'
      })
    }
  }, [])

  return { npmStatus, localStatus, uninstallStatus, installByName, installByPath, uninstall }
}
