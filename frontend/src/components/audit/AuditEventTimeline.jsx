import EmptyState from '../EmptyState'
import ListTable, { ListCell } from '../ListTable'

export default function AuditEventTimeline({
  items = [],
  selectedId = '',
  onSelect,
}) {
  if (!items.length) {
    return <EmptyState title="No audit events yet." description="Strategy, readiness, risk, approval, export, and execution evidence will appear here when actions are recorded." eyebrow="Audit trail" compact />
  }

  return (
    <ListTable>
      {items.map((item) => (
        <button
          key={item.id}
          className="ui-list-row"
          type="button"
          aria-current={selectedId === item.id ? 'true' : undefined}
          onClick={() => onSelect?.(item)}
        >
          <ListCell
            kicker={item.event_type}
            title={`Evidence stored: ${item.actor_email || 'system'}`}
            meta={item.created_at}
          />
        </button>
      ))}
    </ListTable>
  )
}
