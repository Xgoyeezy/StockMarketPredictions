import EmptyState from '../EmptyState'
import StatusBadge from '../StatusBadge'

export default function LiveBrokerReceiptPanel({ receipts = [] }) {
  if (!receipts.length) {
    return <EmptyState title="No Alpaca execution receipts stored yet." description="Alpaca execution evidence appears after approval, route handling, and receipt storage." eyebrow="Receipt evidence" compact />
  }
  return (
    <div className="ui-list-shell">
      {receipts.map((item) => (
        <div key={item.id} className="ui-list-row">
          <span>Alpaca receipt: {item.broker_order_id || item.order_intent_id}</span>
          <StatusBadge value={item.status} tone={String(item.status || '').includes('not_submitted') ? 'warning' : 'positive'} />
        </div>
      ))}
    </div>
  )
}
