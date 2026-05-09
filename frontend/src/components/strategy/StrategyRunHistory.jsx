import Button from '../Button'
import EmptyState from '../EmptyState'
import ListTable, { ListCell } from '../ListTable'
import StatusBadge from '../StatusBadge'

export default function StrategyRunHistory({
  runs = [],
  onOpenReplay,
}) {
  if (!runs.length) {
    return (
      <EmptyState
        title="No paper or live sessions recorded."
        description="Run history appears after a strategy collects paper evidence or authorized live session records."
        eyebrow="Run history"
        compact
      />
    )
  }

  return (
    <ListTable>
      {runs.map((run) => (
        <div key={run.id} className="ui-list-row">
          <ListCell
            kicker={run.run_type || 'strategy run'}
            title={run.id}
            meta={run.started_at || run.created_at}
            stack={[`PnL ${run.pnl ?? 0}`, `orders ${run.order_count ?? 0}`]}
            badges={[<StatusBadge key="status" value={run.status} />]}
          />
          <div className="ui-list-row__actions">
            <Button size="sm" onClick={() => onOpenReplay?.(run)}>Replay</Button>
          </div>
        </div>
      ))}
    </ListTable>
  )
}
