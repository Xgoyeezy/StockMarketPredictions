import EmptyState from '../EmptyState'
import ListTable, { ListCell } from '../ListTable'
import LoadingBlock from '../LoadingBlock'
import StatusBadge from '../StatusBadge'

export default function RiskEventTable({
  items = [],
  loading = false,
}) {
  if (loading) return <LoadingBlock label="Loading risk events..." compact />
  if (!items.length) {
    return <EmptyState title="No risk events recorded." description="Breaches, advisory checks, and why-blocked evidence will appear here when a gate stops or warns on an action." eyebrow="Risk events" compact />
  }
  return (
    <ListTable>
      {items.map((item) => (
        <div key={item.id} className="ui-list-row">
          <ListCell
            kicker={item.event_type}
            title={item.breached_rule || 'risk event'}
            meta={item.created_at}
            stack={[item.action_taken, item.strategy_id]}
            badges={[<StatusBadge key="severity" value={item.severity} />]}
          />
        </div>
      ))}
    </ListTable>
  )
}
