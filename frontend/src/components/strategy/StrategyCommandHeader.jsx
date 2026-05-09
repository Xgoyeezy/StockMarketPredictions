import Button from '../Button'
import InlineMeta from '../InlineMeta'
import StatusBadge from '../StatusBadge'

function formatCurrency(value) {
  const numeric = Number(value || 0)
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(numeric)
}

export default function StrategyCommandHeader({
  strategy,
  readiness,
  busy = false,
  onStart,
  onStop,
  onPromote,
  onRollback,
}) {
  const score = readiness?.score ?? strategy?.readiness?.score ?? 0
  const status = readiness?.status || strategy?.status || strategy?.lifecycle_stage || 'draft'
  const deployment = strategy?.latest_deployment || null

  return (
    <section className="ui-panel ui-panel--section">
      <div className="ui-panel__header">
        <div className="ui-panel__copy">
          <div className="ui-panel__eyebrow">Strategy control plane</div>
          <h1 className="ui-panel__title">{strategy?.name || 'Strategy'}</h1>
          <InlineMeta
            as="div"
            items={[
              strategy?.desk_key,
              `mode ${strategy?.mode || strategy?.trading_mode || 'paper'}`,
              `cap ${formatCurrency(strategy?.allocation_cap)}`,
              deployment ? `deployment ${deployment.status}` : 'no deployment',
            ]}
          />
        </div>
        <div className="ui-panel__actions">
          <StatusBadge value={status} />
          <StatusBadge value={`${score}% ready`} tone={score >= 75 ? 'positive' : score >= 60 ? 'warning' : 'negative'} />
        </div>
      </div>
      <div className="ui-panel__body">
        <div className="ui-action-row">
          <Button variant="solid" disabled={busy} onClick={onStart}>Start Paper</Button>
          <Button disabled={busy} onClick={onStop}>Stop</Button>
          <Button disabled={busy} onClick={onPromote}>Evaluate Promotion</Button>
          <Button disabled={busy} onClick={onRollback}>Rollback</Button>
        </div>
      </div>
    </section>
  )
}
