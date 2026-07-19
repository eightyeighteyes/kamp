import React, { useLayoutEffect, useRef } from 'react'

type Props = {
  initial: string
  onCommit: (value: string) => void
  onCancel: () => void
}

// Inline single-line genre editor (KAMP-608). Enter or blur commits, Esc cancels.
// A `done` ref makes it commit-once, so the Enter->blur and Esc->blur sequences
// can't fire twice. An empty or unchanged value on blur is a cancel.
export function GenreEditInput({ initial, onCommit, onCancel }: Props): React.JSX.Element {
  const doneRef = useRef(false)
  const ref = useRef<HTMLInputElement>(null)

  useLayoutEffect(() => {
    ref.current?.focus()
    ref.current?.select()
  }, [])

  const finish = (value: string): void => {
    if (doneRef.current) return
    doneRef.current = true
    const trimmed = value.trim()
    if (trimmed && trimmed !== initial) onCommit(trimmed)
    else onCancel()
  }

  const cancel = (): void => {
    if (doneRef.current) return
    doneRef.current = true
    onCancel()
  }

  return (
    <input
      ref={ref}
      className="genre-edit-input"
      defaultValue={initial}
      aria-label="Rename genre"
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault()
          finish((e.target as HTMLInputElement).value)
        } else if (e.key === 'Escape') {
          e.preventDefault()
          cancel()
        }
      }}
      onBlur={(e) => finish(e.target.value)}
    />
  )
}
