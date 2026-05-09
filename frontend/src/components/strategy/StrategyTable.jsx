import Button from '../Button'
import EmptyState from '../EmptyState'
import InlineMeta from '../InlineMeta'
import ListTable, { ListCell } from '../ListTable'
import LoadingBlock from '../LoadingBlock'
import StatusBadge from '../StatusBadge'

function formatCurrency(value) {
  const numeric = Number(value || 0)
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(numeric)
}

export default function StrategyTable({
  items = [],
  selectedStrategyId = '',
  loading = false,
  onSelect,
  onEvaluateReadiness,
  onOpenAudit,
}) {
  if (loading) return <LoadingBlock label="Loading strategies..." compact />
  if (!items.length) {
    return (
      <EmptyState
        title="No strategy lanes yet."
        description="First action: create one paper-first strategy lane so readiness, versioning, and audit evidence have something to measure."
        eyebrow="Strategies"
        compact
      />
    )
  }

  return (
    <ListTable>
      {items.map((item) => {
        const readiness = item.readiness || {}
        return (
          <div
            key={item.id}
            className="ui-list-row"
            aria-current={selectedStrategyId === item.id ? 'true' : undefined}
          >
            <ListCell
              kicker={item.desk_key}
              title={item.name}
              meta={item.description || 'Paper-first strategy lifecycle'}
              stack={[
                <InlineMeta
                  key="meta"
                  items={[
                    `mode ${item.mode || item.trading_mode || 'paper'}`,
                    `cap ${formatCurrency(item.allocation_cap)}`,
                    `${(item.symbols || []).length} symbols`,
                  ]}
                />,
              ]}
              badges={[
                <StatusBadge key="status" value={item.status || item.lifecycle_stage} />,
                <StatusBadge
                  key="score"
                  value={`${readiness.score ?? 0}%`}
                  tone={(readiness.score || 0) >= 75 ? 'positive' : 'neutral'}
                />,
              ]}
            />
            <div className="ui-list-row__actions">
              <Button size="sm" onClick={() => onSelect?.(item)}>Open</Button>
              <Button size="sm" onClick={() => onEvaluateReadiness?.(item)}>Evaluate</Button>
              <Button size="sm" onClick={() => onOpenAudit?.(item)}>Audit</Button>
            </div>
          </div>
        )
      })}
    </ListTable>
  )
}
