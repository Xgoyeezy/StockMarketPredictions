import { NativeButton, joinClasses } from './ControlPrimitives'

export default function SegmentedControl({
  value,
  options = [],
  onChange,
  ariaLabel,
  className = '',
  size = 'sm',
}) {
  return (
    <div
      className={joinClasses('ui-segmented', `ui-segmented--${size}`, className)}
      role="radiogroup"
      aria-label={ariaLabel}
    >
      {options.map((option) => {
        const active = option.key === value
        return (
          <NativeButton
            key={option.key}
            type="button"
            role="radio"
            aria-checked={active}
            className={joinClasses('ui-segmented__item', active ? 'ui-segmented__item--active' : '')}
            onClick={() => onChange?.(option.key)}
          >
            {option.label}
          </NativeButton>
        )
      })}
    </div>
  )
}
