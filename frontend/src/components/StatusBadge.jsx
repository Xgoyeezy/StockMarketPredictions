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

export function describeStatusBadgeValue(value) {
  const normalized = formatStatusBadgeValue(value).replace(/\s+/g, ' ').trim()
  if (!normalized || normalized === 'UNKNOWN') return ''
  if (normalized === 'BEARISH') return 'Bearish model bias or put/short-side thesis.'
  if (normalized === 'BULLISH') return 'Bullish model bias or call/long-side thesis.'
  if (normalized === 'PASS') return 'This setup did not meet the current entry requirements.'
  if (normalized === 'REJECT') return 'The setup is blocked by one or more model or risk gates.'
  if (normalized === 'ENTER LONG') return 'A long or buy-side entry is allowed if the risk gates stay clear.'
  if (normalized === 'ENTER SHORT') return 'A short-side entry is indicated, subject to risk and account rules.'
  if (normalized === 'VALID TRADE') return 'The setup passed the current trade validation checks.'
  if (normalized === 'NO TRADE') return 'The desk is monitoring, but no entry is active right now.'
  if (normalized === 'MONITORING') return 'The desk is watching the setup without an entry action.'
  if (normalized === 'INSTITUTIONAL FLOW ACCEPTABLE') return 'Flow quality is supportive enough for this setup.'
  if (normalized.includes('FLOW QUALITY MIXED')) return 'Flow quality is mixed, so the setup needs more confirmation.'
  if (normalized.includes('TAKE PROFIT')) return 'Price has reached a profit-taking condition.'
  if (normalized.includes('CUT LOSS') || normalized.includes('STOP')) return 'Price has reached a defensive exit condition.'
  return ''
}

export default function StatusBadge({
  children,
  value,
  tone,
  tooltip,
  className = '',
  size = 'sm',
  ...props
}) {
  const label = children ?? formatStatusBadgeValue(value)
  const resolvedTone = tone || inferStatusBadgeTone(label)
  const resolvedTooltip = tooltip === undefined ? describeStatusBadgeValue(label) : tooltip

  return (
    <Chip
      {...props}
      tone={resolvedTone}
      size={size}
      tooltip={resolvedTooltip}
      className={joinClasses('ui-status-badge', className)}
    >
      {label}
    </Chip>
  )
}
