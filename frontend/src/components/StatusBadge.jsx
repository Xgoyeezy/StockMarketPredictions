import Chip from './Chip'
import { joinClasses } from './ControlPrimitives'

export function formatStatusBadgeValue(value) {
  return String(value || 'UNKNOWN').toUpperCase()
}

export function inferStatusBadgeTone(value) {
  const normalized = formatStatusBadgeValue(value)
  if (
    normalized.includes('VALID') ||
    normalized.includes('CALL') ||
    normalized.includes('BULL') ||
    normalized.includes('PROFIT')
  ) {
    return 'positive'
  }
  if (
    normalized.includes('PUT') ||
    normalized.includes('BEAR') ||
    normalized.includes('LOSS') ||
    normalized.includes('STOP') ||
    normalized.includes('ERROR')
  ) {
    return 'negative'
  }
  return 'neutral'
}

export default function StatusBadge({
  children,
  value,
  tone,
  className = '',
  size = 'sm',
  ...props
}) {
  const label = children ?? formatStatusBadgeValue(value)
  const resolvedTone = tone || inferStatusBadgeTone(label)

  return (
    <Chip
      {...props}
      tone={resolvedTone}
      size={size}
      className={joinClasses('ui-status-badge', className)}
    >
      {label}
    </Chip>
  )
}
