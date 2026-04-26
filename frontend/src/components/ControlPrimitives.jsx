import { createElement, forwardRef } from 'react'

export function joinClasses(...values) {
  return values.filter(Boolean).join(' ')
}

function createNativeControl(tagName, baseClassName = '') {
  return forwardRef(function NativeControl({ className = '', ...props }, ref) {
    return createElement(tagName, {
      ...props,
      ref,
      className: joinClasses(baseClassName, className),
    })
  })
}

export const NativeButton = createNativeControl('button')
export const NativeInput = createNativeControl('input')
export const NativeSelect = createNativeControl('select')
export const NativeTextArea = createNativeControl('textarea')
