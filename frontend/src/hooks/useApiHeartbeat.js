import { useCallback, useEffect, useState } from 'react'
import { getHealth } from '../api/client'
import usePolling from './usePolling'

export default function useApiHeartbeat(delay = 15000) {
  const [health, setHealth] = useState(null)
  const [status, setStatus] = useState('checking')
  const [lastCheckedAt, setLastCheckedAt] = useState('')

  const load = useCallback(async () => {
    try {
      const payload = await getHealth()
      setHealth(payload)
      setStatus('connected')
      setLastCheckedAt(new Date().toLocaleTimeString())
    } catch (error) {
      setHealth({ status: 'degraded', detail: error?.message || 'API health check failed.' })
      setStatus('degraded')
      setLastCheckedAt(new Date().toLocaleTimeString())
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  usePolling(load, delay, true)

  return { health, status, lastCheckedAt, refresh: load }
}
