import EmptyState from '../EmptyState'
import StatusBadge from '../StatusBadge'

export default function LiveAuditTimelinePanel({ events = [] }) {
  if (!events.length) {
    return <EmptyState title="No live audit events yet." description="Evidence stored here will include live authorization, risk, approval, route receipt, and kill-switch actions." eyebrow="Audit trail" compact />
  }
  return (
    <div className="ui-list-shell">
      {events.map((item) => (
        <div key={item.id} className="ui-list-row">
          <span>Evidence stored: {item.event_type}</span>
          <StatusBadge value={item.severity || 'recorded'} tone={item.severity === 'critical' ? 'negative' : 'neutral'} />
        </div>
      ))}
    </div>
  )
}
