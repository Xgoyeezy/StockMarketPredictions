import { useEffect } from 'react'

function isEditableTarget(target) {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  const tagName = target.tagName.toLowerCase()
  return tagName === 'input' || tagName === 'textarea' || tagName === 'select'
}

function focusNode(node) {
  if (!(node instanceof HTMLElement)) return false
  node.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  node.focus()
  if (
    node instanceof HTMLInputElement ||
    node instanceof HTMLTextAreaElement
  ) {
    if (typeof node.select === 'function') {
      node.select()
    }
  }
  return true
}

function hasBlockingDialog() {
  return Boolean(document.querySelector('[role="dialog"][aria-modal="true"]'))
}

export default function usePageActionShortcuts({
  focusInput = null,
  focusResult = null,
  enabled = true,
}) {
  useEffect(() => {
    if (!enabled) return undefined

    function handleKeyDown(event) {
      if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return
      if (hasBlockingDialog()) return

      if (event.key === '/' && !isEditableTarget(event.target)) {
        if (typeof focusInput === 'function' && focusInput()) {
          event.preventDefault()
        }
        return
      }

      if (event.key === 'J' && event.shiftKey && !isEditableTarget(event.target)) {
        if (typeof focusResult === 'function' && focusResult()) {
          event.preventDefault()
        }
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [enabled, focusInput, focusResult])
}

export function focusFirstMatching(selectors = []) {
  for (const selector of selectors) {
    const node = document.querySelector(selector)
    if (focusNode(node)) return true
  }
  return false
}
