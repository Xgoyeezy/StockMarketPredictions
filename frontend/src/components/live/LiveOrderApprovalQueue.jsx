import Button from '../Button'
import EmptyState from '../EmptyState'
import StatusBadge from '../StatusBadge'

export default function LiveOrderApprovalQueue({
  items = [],
  busy = false,
  onApprove,
  onReject,
  onRiskCheck,
}) {
  if (!items.length) {
    return (
      <EmptyState
        title="No live order intents pending."
        description="Next setup action: connect an account, sign authorization, pass readiness, and let a stored decision reach the approval queue."
        eyebrow="Approval queue"
        compact
      />
    )
  }
  return (
    <div className="ui-list-shell">
      {items.map((item) => (
        <div key={item.id} className="ui-list-row ui-list-row--multi">
          <div>
            <strong>{item.symbol}</strong>
            <div className="ui-muted">{item.side} {item.quantity} {item.instrument_type} - ${Number(item.notional_value || 0).toLocaleString()}</div>
            <div className="ui-muted">Risk check: {item.risk_status || item.risk_check_status || 'not run'}{item.expected_price ? ` - expected ${Number(item.expected_price).toLocaleString()}` : ''}</div>
          </div>
          <StatusBadge value={item.status} tone={item.status === 'blocked' ? 'negative' : item.status === 'approved' ? 'positive' : 'warning'} />
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => onRiskCheck?.(item)}>Risk Check</Button>
          <Button variant="solid" size="sm" disabled={busy || item.status === 'blocked'} onClick={() => onApprove?.(item)}>Approve</Button>
          <Button variant="subtle" size="sm" disabled={busy} onClick={() => onReject?.(item)}>Reject</Button>
        </div>
      ))}
    </div>
  )
}
