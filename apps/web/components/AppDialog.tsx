'use client'
import { createContext, useContext, useMemo, useState } from 'react'

type DialogKind = 'alert' | 'confirm' | 'prompt'

interface DialogState {
  kind: DialogKind
  message: string
  defaultValue?: string
  resolve: (value: string | boolean | null) => void
}

interface DialogApi {
  alert: (message: string) => Promise<void>
  confirm: (message: string) => Promise<boolean>
  prompt: (message: string, defaultValue?: string) => Promise<string | null>
}

const DialogContext = createContext<DialogApi | null>(null)

export function useAppDialog(): DialogApi {
  const ctx = useContext(DialogContext)
  if (!ctx) throw new Error('useAppDialog must be used inside AppDialogProvider')
  return ctx
}

export default function AppDialogProvider({ children }: { children: React.ReactNode }) {
  const [dialog, setDialog] = useState<DialogState | null>(null)
  const [value, setValue] = useState('')

  const open = (kind: DialogKind, message: string, defaultValue = '') =>
    new Promise<string | boolean | null>((resolve) => {
      setValue(defaultValue)
      setDialog({ kind, message, defaultValue, resolve })
    })

  const api = useMemo<DialogApi>(() => ({
    alert: async (message) => {
      await open('alert', message)
    },
    confirm: async (message) => Boolean(await open('confirm', message)),
    prompt: async (message, defaultValue = '') => {
      const result = await open('prompt', message, defaultValue)
      return typeof result === 'string' ? result : null
    },
  }), [])

  const close = (result: string | boolean | null) => {
    const current = dialog
    setDialog(null)
    current?.resolve(result)
  }

  return (
    <DialogContext.Provider value={api}>
      {children}
      {dialog && (
        <div className="app-dialog-backdrop" role="presentation" onClick={() => close(null)}>
          <div
            className="app-dialog"
            role="dialog"
            aria-modal="true"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="app-dialog-message">
              {dialog.message}
            </div>
            {dialog.kind === 'prompt' && (
              <input
                className="app-dialog-input"
                autoFocus
                value={value}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') close(value)
                  if (e.key === 'Escape') close(null)
                }}
              />
            )}
            <div className="app-dialog-actions">
              {dialog.kind !== 'alert' && (
                <button type="button" className="btn" onClick={() => close(null)}>
                  取消
                </button>
              )}
              <button
                type="button"
                className="btn btn-primary"
                autoFocus={dialog.kind !== 'prompt'}
                onClick={() => close(dialog.kind === 'prompt' ? value : true)}
              >
                确定
              </button>
            </div>
          </div>
        </div>
      )}
    </DialogContext.Provider>
  )
}
