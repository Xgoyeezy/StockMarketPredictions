import Button from '../Button'
import StatusBadge from '../StatusBadge'

export default function KillSwitchPanel({
  active = false,
  scope = 'global',
  activeCount = 0,
  strategyCount = 0,
  latestEvent = null,
  onActivate,
  onClear,
}) {
  const latestReason = latestEvent?.reason || latestEvent?.event_type
  const latestText = latestEvent?.created_at
    ? `Last event: ${latestEvent.event_type || 'kill switch'} at ${latestEvent.created_at}${latestReason ? ` (${latestReason})` : ''}.`
    : 'No kill-switch event has been recorded yet.'

  return (
    <div className="ui-stack">
      <div className="ui-action-row">
        <StatusBadge value={active ? 'active' : 'clear'} tone={active ? 'negative' : 'positive'} />
        <span className="ui-note">{active ? `Scope: ${scope}. ${activeCount} of ${strategyCount} strategies stopped. Review risk events before clearing.` : `Scope: ${scope}. Neutral state; emergency stop remains ready.`}</span>
      </div>
      <p className="ui-note">{latestText}</p>
      <div className="ui-action-row">
        <Button variant="danger" onClick={onActivate}>Activate Kill Switch</Button>
        <Button onClick={onClear}>Clear Paper/Allowed Scope</Button>
      </div>
    </div>
  )
}
