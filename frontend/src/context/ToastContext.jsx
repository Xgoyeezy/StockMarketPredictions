import { createContext, useCallback, useContext, useMemo, useState } from 'react'

const ToastContext = createContext(null)

function makeToast(message, tone = 'info') {
  return {
    id: `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    message,
    tone,
  }
}

function normalizeToastMessage(input) {
  if (input == null) return ''
  if (typeof input === 'string') return input
  if (typeof input === 'object') {
    const title = typeof input.display_title === 'string' ? input.display_title : ''
    const detail = typeof input.display_detail === 'string' ? input.display_detail : typeof input.message === 'string' ? input.message : ''
    const combined = title && detail ? `${title}: ${detail}` : title || detail
    return combined || 'Request failed.'
  }
  return String(input)
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])

  const dismissToast = useCallback((id) => {
    setToasts((current) => current.filter((toast) => toast.id !== id))
  }, [])

  const pushToast = useCallback((message, tone = 'info') => {
    const toast = makeToast(normalizeToastMessage(message), tone)
    setToasts((current) => [...current, toast])
    window.setTimeout(() => {
      setToasts((current) => current.filter((item) => item.id !== toast.id))
    }, 3200)
  }, [])

  const value = useMemo(() => ({ pushToast, dismissToast }), [pushToast, dismissToast])

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-stack" aria-live="polite" aria-atomic="true">
        {toasts.map((toast) => (
          <button
            key={toast.id}
            type="button"
            className={`toast toast--${toast.tone}`}
            onClick={() => dismissToast(toast.id)}
            role="status"
            aria-label={`Dismiss ${toast.tone} message: ${toast.message}`}
            title="Dismiss notification"
          >
            {toast.message}
          </button>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const context = useContext(ToastContext)
  if (!context) {
    throw new Error('useToast must be used inside a ToastProvider.')
  }
  return context
}
