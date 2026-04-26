import { createElement } from 'react'
import { joinClasses } from './ControlPrimitives'

export function formatInlineMeta(items = [], separator = ' / ') {
  return items
    .filter((item) => item !== null && item !== undefined && item !== '')
    .map((item) => String(item))
    .join(separator)
}

export default function InlineMeta({
  items = [],
  as: Component = 'span',
  className = '',
  itemClassName = '',
  ...props
}) {
  const filteredItems = items.filter((item) => item !== null && item !== undefined && item !== '')

  return createElement(
    Component,
    {
      ...props,
      className: joinClasses('ui-inline-meta', className),
    },
    filteredItems.map((item, index) =>
      createElement(
        'span',
        {
          key: `${index}-${String(item)}`,
          className: joinClasses('ui-inline-meta__item', itemClassName),
        },
        item,
      ),
    ),
  )
}
