import Button from './Button'
import EmptyState from './EmptyState'

export default function TradeMonitorPanel({ rows = [], onSelectTicker }) {
  const visibleRows = rows.slice(0, 6)

  return (
    <div className="monitor-panel">
      {visibleRows.length ? (
        visibleRows.map((row, index) => (
          <Button
            key={`${row.ticker || 'row'}-${index}`}
            type="button"
            variant="ghost"
            size="sm"
            className="monitor-row"
            onClick={() => onSelectTicker?.(row.ticker)}
          >
            <div>
              <strong>{row.ticker || '--'}</strong>
              <div>{row.monitor_action || row.trade_decision || 'Monitor'}</div>
            </div>
            <div className="monitor-row__meta">
              <span>{row.current_underlying_price ?? row.entry_underlying_price ?? '--'}</span>
              <span>{row.pnl_dollars ?? '--'}</span>
            </div>
          </Button>
        ))
      ) : (
        <EmptyState
          title="No monitored trades"
          description="No monitored trades available."
        />
      )}
    </div>
  )
}
