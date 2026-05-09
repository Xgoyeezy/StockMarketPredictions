import Button from '../Button'
import EmptyState from '../EmptyState'
import ListTable, { ListCell } from '../ListTable'
import StatusBadge from '../StatusBadge'

function formatCurrency(value) {
  const numeric = Number(value || 0)
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(numeric)
}

export default function RiskPolicyPanel({
  policies = [],
  loading = false,
  onSave,
}) {
  if (!policies.length && !loading) {
    return (
      <EmptyState
        title="No risk policies yet."
        description="First action: create an active policy with daily loss, order size, drawdown, and symbol limits before promotion or live requests."
        eyebrow="Risk policy"
        actionLabel="Create Default Policy"
        onAction={() => onSave?.({ scope: 'strategy', status: 'active' })}
        compact
      />
    )
  }

  return (
    <div className="ui-stack">
      <div className="ui-action-row">
        <Button variant="solid" onClick={() => onSave?.({ scope: 'strategy', status: 'active' })}>Create Policy</Button>
      </div>
      <ListTable>
        {policies.map((policy) => (
          <div key={policy.id} className="ui-list-row">
            <ListCell
              kicker={policy.scope}
              title={policy.strategy_id || 'Tenant policy'}
              meta={`max order ${formatCurrency(policy.max_order_notional)} / daily loss ${formatCurrency(policy.max_daily_loss)}`}
              stack={[`open positions ${policy.max_open_positions || 0}`, `drawdown ${policy.max_drawdown_pct || 0}%`]}
              badges={[<StatusBadge key="status" value={policy.status} />]}
            />
          </div>
        ))}
      </ListTable>
    </div>
  )
}
