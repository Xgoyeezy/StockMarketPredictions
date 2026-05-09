import Button from '../Button'
import StatusBadge from '../StatusBadge'

export default function KillSwitchPanel({
  active = false,
  scope = 'global',
  onActivate,
  onClear,
}) {
  return (
    <div className="ui-stack">
      <div className="ui-action-row">
        <StatusBadge value={active ? 'active' : 'clear'} tone={active ? 'negative' : 'positive'} />
        <span className="ui-note">{active ? `Scope: ${scope}. Review risk events before clearing.` : `Scope: ${scope}. Neutral state; emergency stop remains ready.`}</span>
      </div>
      <div className="ui-action-row">
        <Button variant="danger" onClick={onActivate}>Activate Kill Switch</Button>
        <Button onClick={onClear}>Clear Paper/Allowed Scope</Button>
      </div>
    </div>
  )
}
