import { useEffect, useState } from 'react'

export default function useDebouncedValue(value, delay = 350) {
  const [debouncedValue, setDebouncedValue] = useState(value)

  useEffect(() => {
    const id = window.setTimeout(() => setDebouncedValue(value), delay)
    return () => window.clearTimeout(id)
  }, [value, delay])

  return debouncedValue
}
