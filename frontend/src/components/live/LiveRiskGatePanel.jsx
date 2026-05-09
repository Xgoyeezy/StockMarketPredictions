import MetricCard from '../MetricCard'
import StatusBadge from '../StatusBadge'

export default function LiveRiskGatePanel({ readiness = null, killSwitch = null, events = [] }) {
  const blockers = readiness?.blockers || []
  return (
    <div className="ui-stack">
      <div className="ui-dashboard-grid">
        <MetricCard label="Readiness" value={`${Number(readiness?.score || 0)}%`} helper={readiness?.status || 'not evaluated'} />
        <MetricCard label="Readiness blockers" value={blockers.length} tone={blockers.length ? 'negative' : 'positive'} />
        <MetricCard label="Kill switch" value={killSwitch?.active ? 'Active' : 'Clear'} tone={killSwitch?.active ? 'negative' : 'positive'} />
        <MetricCard label="Risk events" value={events.length} tone={events.length ? 'warning' : 'neutral'} />
      </div>
      {blockers.length ? (
        <div className="ui-list-shell">
          {blockers.map((item, index) => (
            <div key={`${item.key || index}`} className="ui-list-row">
              <span>{item.message || item.key}</span>
              <StatusBadge value={item.severity || 'blocker'} tone={item.severity === 'critical' ? 'negative' : 'warning'} />
            </div>
          ))}
        </div>
      ) : (
        <div className="ui-list-shell">
          <div className="ui-list-row">
            <span>No readiness blockers recorded</span>
            <StatusBadge value="clear" tone="positive" />
          </div>
          <div className="ui-list-row">
            <span>Next safe action</span>
            <span className="ui-muted">Confirm signed authorization, active risk policy, fresh data, and explicit start before any live run.</span>
          </div>
        </div>
      )}
      {events.length ? (
        <div className="ui-list-shell">
          {events.map((item, index) => (
            <div key={item.id || index} className="ui-list-row">
              <span>Why blocked: {item.breached_rule || item.event_type || item.reason || 'risk gate'}</span>
              <StatusBadge value={item.severity || item.status || 'event'} tone={item.severity === 'critical' ? 'negative' : 'warning'} />
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}
