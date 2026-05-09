import Button from '../Button'
import EmptyState from '../EmptyState'
import MetricCard from '../MetricCard'

export default function ExecutionQualityPanel({
  summary = null,
  loading = false,
  onRefresh,
}) {
  const data = summary || {}
  const hasEvidence = Boolean(summary && Object.keys(summary).length)
  return (
    <div className="ui-stack">
      <div className="ui-action-row">
        <Button disabled={loading} onClick={onRefresh}>Refresh</Button>
      </div>
      {!hasEvidence ? (
        <EmptyState
          title="No execution-quality evidence stored yet."
          description="Slippage, spread cost, fill rate, route stability, and receipt evidence appear after approved orders create execution snapshots."
          eyebrow="Execution evidence"
          compact
        />
      ) : null}
      <div className="ui-dashboard-grid">
        <MetricCard label="Execution score" value={Math.round(Number(data.execution_score || 0))} />
        <MetricCard label="Avg slippage" value={`${Number(data.avg_slippage_bps || 0).toFixed(2)} bps`} />
        <MetricCard label="Avg spread" value={`${Number(data.avg_spread_bps || 0).toFixed(2)} bps`} />
        <MetricCard label="Fill rate" value={`${Math.round(Number(data.fill_rate || 0) * 100)}%`} />
        <MetricCard label="Reject rate" value={`${Math.round(Number(data.reject_rate || 0) * 100)}%`} />
        <MetricCard label="Latency" value={`${Math.round(Number(data.avg_latency_ms || 0))} ms`} />
      </div>
    </div>
  )
}
