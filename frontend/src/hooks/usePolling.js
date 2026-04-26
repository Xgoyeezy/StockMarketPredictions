import { useEffect, useRef } from 'react'

export default function usePolling(callback, delay, enabled = true) {
  const savedCallback = useRef(callback)

  useEffect(() => {
    savedCallback.current = callback
  }, [callback])

  useEffect(() => {
    if (!enabled || !delay || delay < 500) {
      return undefined
    }
    const id = window.setInterval(() => {
      savedCallback.current?.()
    }, delay)
    return () => window.clearInterval(id)
  }, [delay, enabled])
}
