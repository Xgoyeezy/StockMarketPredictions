import { useId } from 'react'

function joinClasses(...values) {
  return values.filter(Boolean).join(' ')
}

export default function Chip({
  children,
  tone = 'neutral',
  active = false,
  size = 'sm',
  className = '',
  as: Component = 'span',
  tooltip,
  tooltipPlacement = 'top',
  tooltipDelayMs = 900,
  style,
  tabIndex,
  'aria-describedby': ariaDescribedBy,
  ...props
}) {
  const generatedTooltipId = useId()
  const hasTooltip = tooltip !== undefined && tooltip !== null && tooltip !== ''
  const tooltipId = hasTooltip ? `chip-tooltip-${generatedTooltipId}` : undefined
  const describedBy = [ariaDescribedBy, tooltipId].filter(Boolean).join(' ') || undefined
  const normalizedPlacement = ['top', 'bottom'].includes(String(tooltipPlacement)) ? tooltipPlacement : 'top'
  const normalizedDelay = Number.isFinite(Number(tooltipDelayMs)) ? Math.max(0, Number(tooltipDelayMs)) : 900
  const isNativeSpan = Component === 'span'

  return (
    <Component
      {...props}
      aria-describedby={describedBy}
      tabIndex={hasTooltip && isNativeSpan && tabIndex === undefined ? 0 : tabIndex}
      style={
        hasTooltip
          ? {
              ...style,
              '--ui-chip-tooltip-delay': `${normalizedDelay}ms`,
            }
          : style
      }
      className={joinClasses(
        'ui-chip',
        `ui-chip--${tone}`,
        `ui-chip--${size}`,
        active ? 'ui-chip--active' : '',
        hasTooltip ? 'ui-chip--has-tooltip' : '',
        hasTooltip ? `ui-chip--tooltip-${normalizedPlacement}` : '',
        className,
      )}
    >
      {children}
      {hasTooltip ? (
        <span id={tooltipId} role="tooltip" className="ui-chip__tooltip">
          {tooltip}
        </span>
      ) : null}
    </Component>
  )
}
