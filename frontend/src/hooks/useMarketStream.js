import { useEffect, useMemo, useRef, useState } from 'react'
import { appConfig } from '../config/appConfig'

const LOCAL_DEV_WS_API_BASE = 'ws://127.0.0.1:8000/api'
const POLLING_FALLBACK_MESSAGE = 'Snapshot polling is active while realtime reconnects in the background.'

function isLocalViteDev(origin) {
  const pageUrl = new URL(origin)
  const localHost = pageUrl.hostname === 'localhost' || pageUrl.hostname === '127.0.0.1'
  return localHost && pageUrl.port === '5173'
}

function isRelativeApiBase(baseUrl) {
  const normalized = String(baseUrl || '').trim()
  return !normalized || normalized === '/api' || normalized.startsWith('/api/')
}

function toWebSocketBaseUrl(baseUrl, origin) {
  if (baseUrl.startsWith('ws://') || baseUrl.startsWith('wss://')) {
    return baseUrl.replace(/\/$/, '')
  }

  const resolved = new URL(baseUrl, origin)
  resolved.protocol = resolved.protocol === 'https:' ? 'wss:' : 'ws:'
  return resolved.toString().replace(/\/$/, '')
}

function buildStreamBaseUrl() {
  const origin = typeof window !== 'undefined' ? window.location.origin : 'http://localhost:5173'
  const websocketBase = import.meta.env.VITE_WS_BASE_URL || ''
  const apiBase = appConfig.apiBaseUrl || ''

  if (isLocalViteDev(origin) && isRelativeApiBase(websocketBase || apiBase)) {
    return LOCAL_DEV_WS_API_BASE
  }

  if (websocketBase) {
    return toWebSocketBaseUrl(websocketBase, origin)
  }

  if (apiBase) {
    return toWebSocketBaseUrl(apiBase, origin)
  }

  const resolved = new URL('/api', origin)
  resolved.protocol = resolved.protocol === 'https:' ? 'wss:' : 'ws:'
  return resolved.toString().replace(/\/$/, '')
}

function normalizeTickers(tickers) {
  const normalized = []
  const seen = new Set()

  for (const ticker of tickers || []) {
    const cleaned = String(ticker || '').trim().toUpperCase()
    if (!cleaned || seen.has(cleaned)) continue
    seen.add(cleaned)
    normalized.push(cleaned)
  }

  return normalized
}

function normalizeChannels(channels) {
  const normalized = []
  const seen = new Set()

  for (const channel of channels || []) {
    const cleaned = String(channel || '').trim().toLowerCase()
    if (!cleaned || seen.has(cleaned)) continue
    seen.add(cleaned)
    normalized.push(cleaned)
  }

  return normalized
}

function isFatalStreamError(message) {
  const normalized = String(message || '').toLowerCase()
  return (
    normalized.includes('connection limit exceeded') ||
    normalized.includes('not authorized') ||
    normalized.includes('forbidden') ||
    normalized.includes('one ticker is required') ||
    normalized.includes('disabled')
  )
}

function mergeStreamMeta(current, payload) {
  const next = { ...(current || {}), ...(payload || {}) }
  if (payload?.status && payload.status !== 'fallback') {
    delete next.paused_reason
  }
  return next
}

export default function useMarketStream({
  tickers = [],
  channels = ['trades', 'quotes'],
  enabled = true,
  onEvent,
}) {
  const callbackRef = useRef(onEvent)
  const socketRef = useRef(null)
  const reconnectTimerRef = useRef(null)
  const reconnectAttemptRef = useRef(0)
  const fatalErrorRef = useRef('')
  const [status, setStatus] = useState('idle')
  const [error, setError] = useState('')
  const [meta, setMeta] = useState(null)
  const [lastMessageAt, setLastMessageAt] = useState(null)

  useEffect(() => {
    callbackRef.current = onEvent
  }, [onEvent])

  const normalizedTickers = useMemo(() => normalizeTickers(tickers), [tickers])
  const normalizedChannels = useMemo(() => normalizeChannels(channels), [channels])
  const url = useMemo(() => {
    if (!normalizedTickers.length) return ''

    const baseUrl = buildStreamBaseUrl()
    const params = new URLSearchParams({
      tickers: normalizedTickers.join(','),
      channels: normalizedChannels.join(','),
    })
    return `${baseUrl}/market/stream?${params.toString()}`
  }, [normalizedChannels, normalizedTickers])

  useEffect(() => {
    if (!enabled || !url) {
      setStatus('idle')
      setError('')
      setMeta(null)
      fatalErrorRef.current = ''
      return undefined
    }

    let disposed = false
    fatalErrorRef.current = ''

    const cleanupSocket = () => {
      if (socketRef.current) {
        try {
          socketRef.current.close(1000, 'cleanup')
        } catch {
          // ignore shutdown errors
        }
        socketRef.current = null
      }
    }

    const scheduleReconnect = () => {
      if (disposed) return
      if (fatalErrorRef.current) {
        setStatus('fallback')
        return
      }
      const attempt = reconnectAttemptRef.current
      if (attempt >= 4) {
        setStatus('fallback')
        setError((current) => current || POLLING_FALLBACK_MESSAGE)
        setMeta((current) => ({
          ...(current || {}),
          paused_reason: POLLING_FALLBACK_MESSAGE,
          retrying: true,
        }))
        reconnectTimerRef.current = window.setTimeout(connect, 15000)
        return
      }
      const delay = Math.min(1000 * (2 ** attempt), 8000)
      reconnectAttemptRef.current += 1
      reconnectTimerRef.current = window.setTimeout(connect, delay)
      setStatus('reconnecting')
    }

    const connect = () => {
      if (disposed) return

      cleanupSocket()
      setStatus('connecting')
      setError('')

      const socket = new WebSocket(url)
      socketRef.current = socket

      socket.onopen = () => {
        reconnectAttemptRef.current = 0
        setStatus('connected')
      }

      socket.onmessage = (event) => {
        setLastMessageAt(new Date())

        let payload = null
        try {
          payload = JSON.parse(event.data)
        } catch {
          return
        }

        if (payload?.type === 'stream_capabilities') {
          setMeta(payload)
          return
        }

        if (payload?.type === 'stream_status') {
          if (payload.status === 'fallback') {
            const reason =
              payload.reason || POLLING_FALLBACK_MESSAGE
            fatalErrorRef.current = reason
            setMeta((current) => ({ ...(current || {}), ...payload, paused_reason: reason }))
            setError(reason)
            setStatus('fallback')
            return
          }
          fatalErrorRef.current = ''
          setError('')
          setMeta((current) => mergeStreamMeta(current, payload))
          setStatus(payload.status || 'connected')
          return
        }

        if (payload?.type === 'stream_error') {
          const message = payload.message || 'Realtime stream failed.'
          setError(message)
          if (isFatalStreamError(message)) {
            fatalErrorRef.current = message
            setMeta((current) => ({ ...(current || {}), paused_reason: message }))
            setStatus('fallback')
            try {
              socket.close(4001, 'fatal-stream-error')
            } catch {
              // ignore local close failures
            }
            return
          }
          setError(message)
          setStatus('error')
          return
        }

        if (payload?.type === 'market_event') {
          callbackRef.current?.(payload.event, payload)
        }
      }

      socket.onerror = () => {
        setStatus('error')
      }

      socket.onclose = (closeEvent) => {
        socketRef.current = null
        if (disposed) return
        if (fatalErrorRef.current) {
          setStatus('fallback')
          return
        }
        if (closeEvent.code === 1000) {
          setStatus('closed')
          return
        }
        scheduleReconnect()
      }
    }

    connect()

    return () => {
      disposed = true
      window.clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
      cleanupSocket()
    }
  }, [enabled, url])

  return {
    status,
    error,
    meta,
    lastMessageAt,
    isLive: status === 'live' || status === 'connected',
  }
}
