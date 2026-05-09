import EmptyState from '../EmptyState'
import ListTable, { ListCell } from '../ListTable'
import LoadingBlock from '../LoadingBlock'
import StatusBadge from '../StatusBadge'

export default function SlippageBreakdown({
  rows = [],
  groupBy = 'symbol',
  loading = false,
}) {
  if (loading) return <LoadingBlock label="Loading execution rows..." compact />
  if (!rows.length) {
    return <EmptyState title="No execution-quality rows yet." description="Approved paper or live fills will populate cost, spread, slippage, latency, and route-state evidence." eyebrow="Slippage evidence" compact />
  }

  return (
    <ListTable>
      {rows.map((row) => (
        <div key={row.id || row.order_event_id || row.trade_id} className="ui-list-row">
          <ListCell
            kicker={row[groupBy] || row.symbol || row.broker}
            title={`${row.symbol || 'Order'} ${row.route_state || ''}`}
            meta={row.created_at}
            stack={[
              `slippage ${Number(row.slippage_bps || 0).toFixed(2)} bps`,
              `spread ${Number(row.spread_bps || 0).toFixed(2)} bps`,
              `latency ${row.latency_ms || 0} ms`,
            ]}
            badges={[<StatusBadge key="score" value={`${Math.round(Number(row.execution_score || 0))}`} />]}
          />
        </div>
      ))}
    </ListTable>
  )
}
