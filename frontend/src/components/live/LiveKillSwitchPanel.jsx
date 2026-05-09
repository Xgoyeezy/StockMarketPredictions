import Button from '../Button'
import StatusBadge from '../StatusBadge'

export default function LiveKillSwitchPanel({ state = null, busy = false, onActivate, onClear }) {
  const active = Boolean(state?.active)
  return (
    <div className="ui-stack">
      <div className="ui-list-row">
        <span>Global live kill switch</span>
        <StatusBadge value={active ? 'active' : 'clear'} tone={active ? 'negative' : 'positive'} />
      </div>
      <div className="ui-list-row">
        <span>{active ? 'Live order approval is blocked' : 'Live kill switch is clear'}</span>
        <span className="ui-muted">{active ? 'Clear only after reviewing the risk event trail.' : 'Emergency stop controls remain available for every live session.'}</span>
      </div>
      <div className="ui-action-row">
        <Button variant="solid" disabled={busy || active} onClick={onActivate}>Kill All Live</Button>
        <Button variant="ghost" disabled={busy || !active} onClick={onClear}>Clear Kill Switch</Button>
      </div>
    </div>
  )
}
