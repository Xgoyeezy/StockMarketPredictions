import { useCallback, useRef } from 'react'

function getFocusableItems(container, selector) {
  return Array.from(container.querySelectorAll(selector)).filter(
    (item) =>
      item instanceof HTMLElement &&
      !item.hasAttribute('disabled') &&
      item.getAttribute('aria-disabled') !== 'true' &&
      item.offsetParent !== null,
  )
}

function getGridColumns(items) {
  if (!items.length) return 1
  const firstTop = items[0].getBoundingClientRect().top
  let columns = 0
  items.forEach((item) => {
    if (Math.abs(item.getBoundingClientRect().top - firstTop) <= 12) {
      columns += 1
    }
  })
  return Math.max(1, columns)
}

export default function useKeyboardListNavigation({ selector, layout = 'list' }) {
  const containerRef = useRef(null)

  const onKeyDown = useCallback(
    (event) => {
      if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return
      if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return

      const container = containerRef.current
      if (!(container instanceof HTMLElement) || !(event.target instanceof Element)) return

      const currentItem = event.target.closest(selector)
      if (!(currentItem instanceof HTMLElement) || !container.contains(currentItem)) return

      const items = getFocusableItems(container, selector)
      const currentIndex = items.indexOf(currentItem)
      if (currentIndex === -1) return

      let nextIndex = currentIndex

      if (event.key === 'Home') {
        nextIndex = 0
      } else if (event.key === 'End') {
        nextIndex = items.length - 1
      } else if (layout === 'grid') {
        const columns = getGridColumns(items)
        if (event.key === 'ArrowLeft') nextIndex = currentIndex - 1
        if (event.key === 'ArrowRight') nextIndex = currentIndex + 1
        if (event.key === 'ArrowUp') nextIndex = currentIndex - columns
        if (event.key === 'ArrowDown') nextIndex = currentIndex + columns
      } else {
        if (event.key === 'ArrowUp') nextIndex = currentIndex - 1
        if (event.key === 'ArrowDown') nextIndex = currentIndex + 1
      }

      nextIndex = Math.max(0, Math.min(items.length - 1, nextIndex))
      if (nextIndex === currentIndex) return

      event.preventDefault()
      items[nextIndex]?.focus()
    },
    [layout, selector],
  )

  return { containerRef, onKeyDown }
}
