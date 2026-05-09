import EmptyState from '../EmptyState'
import MetricCard from '../MetricCard'
import StatusBadge from '../StatusBadge'

export default function LiveSessionStatusPanel({ status = null, sessions = [] }) {
  const summary = status?.summary || {}
  const flags = status?.feature_flags || {}
  const rows = sessions.length ? sessions : status?.sessions || []
  const hasArmed = Number(summary.armed_count || 0) > 0
  const hasBlocked = Number(summary.blocked_count || 0) > 0
  return (
    <div className="ui-stack">
      <div className="ui-dashboard-grid">
        <MetricCard label="Active sessions" value={summary.active_session_count || 0} />
        <MetricCard label="Armed" value={summary.armed_count || 0} />
        <MetricCard label="Live" value={summary.live_count || 0} tone={summary.live_count ? 'positive' : 'neutral'} />
        <MetricCard label="Blocked" value={summary.blocked_count || 0} tone={summary.blocked_count ? 'negative' : 'neutral'} />
      </div>
      <div className="ui-list-shell">
        <div className="ui-list-row">
          <span>Live trading availability</span>
          <StatusBadge value={flags.live_trading ? 'enabled' : 'disabled'} tone={flags.live_trading ? 'positive' : 'warning'} />
        </div>
        <div className="ui-list-row">
          <span>Managed automation</span>
          <StatusBadge value={flags.managed_advisory ? 'enabled' : 'disabled'} tone={flags.managed_advisory ? 'negative' : 'neutral'} />
        </div>
        <div className="ui-list-row">
          <span>Alpaca live route</span>
          <StatusBadge value={flags.alpaca_live ? 'configured' : 'off'} tone={flags.alpaca_live ? 'positive' : 'neutral'} />
        </div>
        {!flags.live_trading ? (
          <div className="ui-list-row">
            <span>Next setup action</span>
            <span className="ui-muted">Live automation stays off until availability, authorization, readiness, risk, and approval gates pass.</span>
          </div>
        ) : null}
      </div>
      {rows.length ? (
        <div className="ui-list-shell">
          {rows.map((session) => (
            <div key={session.id} className="ui-list-row">
              <span>{session.strategy_id}</span>
              <StatusBadge value={session.status} tone={session.status === 'live' ? 'positive' : session.status === 'blocked' ? 'negative' : 'warning'} />
            </div>
          ))}
        </div>
      ) : (
        <EmptyState
          title="No active live sessions."
          description="A strategy appears here only after it is requested, authorized, armed, and explicitly started."
          eyebrow="Live sessions"
          compact
        />
      )}
      {!hasArmed ? (
        <EmptyState
          title="No strategies are armed."
          description="Armed means the gates passed and the strategy is ready for an explicit start; it does not submit live orders by itself."
          eyebrow="Armed state"
          compact
        />
      ) : null}
      {!hasBlocked ? (
        <EmptyState
          title="No blocked live orders."
          description="Blocking evidence will surface here when a readiness, risk, approval, or kill-switch gate stops an order."
          eyebrow="Blocked orders"
          compact
        />
      ) : null}
    </div>
  )
}
