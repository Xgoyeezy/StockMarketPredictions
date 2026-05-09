import { useCallback, useEffect, useState } from 'react'
import Button from '../components/Button'
import ErrorState from '../components/ErrorState'
import ListTable from '../components/ListTable'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import StatusBadge from '../components/StatusBadge'
import { getExecutionQualityTcaSummary } from '../api/client'

function formatNumber(value, digits = 2) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '--'
  return numeric.toFixed(digits)
}

function formatRatio(value) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '--'
  return `${(numeric * 100).toFixed(1)}%`
}

function humanize(value, fallback = 'Unknown') {
  const text = String(value || '').trim()
  if (!text) return fallback
  return text.replace(/[_-]+/g, ' ').replace(/\b\w/g, (match) => match.toUpperCase())
}

function statusTone(status) {
  if (status === 'ready') return 'positive'
  if (status === 'empty') return 'neutral'
  return 'warning'
}

function DataTable({ columns, rows, empty }) {
  return (
    <ListTable>
      <table className="ui-list-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column.key}>{column.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.length ? rows.map((row, index) => (
            <tr key={row.order_id || row.trade_id || row.symbol || row.setup_type || row.engine || row.field || row.note || index}>
              {columns.map((column) => (
                <td key={column.key}>{column.render ? column.render(row) : row[column.key]}</td>
              ))}
            </tr>
          )) : (
            <tr><td colSpan={columns.length}>{empty}</td></tr>
          )}
        </tbody>
      </table>
    </ListTable>
  )
}

function missingRows(missingFields) {
  return Object.entries(missingFields || {})
    .sort((left, right) => Number(right[1]) - Number(left[1]))
    .map(([field, count]) => ({ field, count }))
}

export default function ExecutionQualityPage() {
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setReport(await getExecutionQualityTcaSummary())
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load Execution Quality TCA.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const summary = report?.summary || {}
  const aggregations = report?.aggregations || {}
  const rows = report?.records || []
  const warnings = report?.warnings || []
  const safetyNotes = report?.safety_notes || []
  const missingFields = missingRows(report?.missing_fields)
  const setupRows = aggregations.execution_adjusted_reward_by_setup || []
  const worstSetupRows = [...setupRows].sort((left, right) => Number(left.execution_adjusted_reward ?? 999) - Number(right.execution_adjusted_reward ?? 999))

  return (
    <div className="ui-shell__page">
      <PageIntro
        kicker="Paper-only research"
        title="Execution Quality"
        description="Transaction cost analysis for paper-route evidence: spread, slippage, fill delay, alpha decay, and execution-adjusted reward. Analytics do not change routing."
        badge="No routing authority"
      />
      {error ? <ErrorState description={error} onAction={load} /> : null}

      <div className="ui-action-row">
        <StatusBadge tone={statusTone(report?.status)}>{humanize(report?.status || 'empty')}</StatusBadge>
        <StatusBadge tone="neutral">Research only</StatusBadge>
        <StatusBadge tone="neutral">Paper only</StatusBadge>
        <StatusBadge tone="neutral">Does not change routing</StatusBadge>
        <StatusBadge tone="neutral">Does not place orders</StatusBadge>
        <StatusBadge tone="warning">No guaranteed returns</StatusBadge>
        <Button type="button" variant="ghost" size="sm" onClick={load} disabled={loading}>
          Refresh
        </Button>
      </div>

      <SectionCard title="TCA Summary" subtitle="Correct forecasts still need tradable fills after spread, slippage, and delay.">
        <div className="ui-dashboard-grid">
          <MetricCard label="Execution quality" value={formatNumber(summary.execution_quality_score, 0)} helper={`${summary.trade_count ?? 0} paper-route rows`} />
          <MetricCard label="Average slippage" value={`${formatNumber(summary.average_slippage)} bps`} helper="Signed bps when available" />
          <MetricCard label="Median slippage" value={`${formatNumber(summary.median_slippage)} bps`} helper="Paper fill evidence" />
          <MetricCard label="Fill delay" value={`${formatNumber(summary.average_fill_delay_seconds)} sec`} helper="Submitted to filled, or latency evidence" />
          <MetricCard label="Alpha decay" value={formatNumber(summary.average_alpha_decay)} helper="Expected alpha minus post-fill alpha" />
          <MetricCard label="Execution-adjusted reward" value={formatNumber(summary.average_execution_adjusted_reward)} helper="Reward after spread/slippage drag" />
          <MetricCard label="Spread cost" value={`${formatNumber(summary.average_spread_cost)} bps`} helper="Spread at signal" />
          <MetricCard label="Cost-adjusted edge" value={formatNumber(summary.average_cost_adjusted_edge)} helper="Baseline-relative after costs" />
          <MetricCard label="Partial fill rate" value={formatRatio(summary.partial_fill_rate)} helper="Paper partial fills" />
          <MetricCard label="Missed fill rate" value={formatRatio(summary.missed_fill_rate)} helper="Rejected/canceled/expired/no-fill states" />
        </div>
      </SectionCard>

      <SectionCard title="Best And Worst Execution Setups" subtitle="Setup-level TCA is manual research only and cannot alter routing or ranking.">
        <div className="ui-dashboard-grid">
          <DataTable
            rows={setupRows.slice(0, 8)}
            empty={loading ? 'Loading setup TCA...' : 'No setup execution rows available.'}
            columns={[
              { key: 'setup_type', label: 'Best setup', render: (row) => humanize(row.setup_type) },
              { key: 'count', label: 'Rows' },
              { key: 'execution_adjusted_reward', label: 'Exec adj. reward', render: (row) => formatNumber(row.execution_adjusted_reward) },
              { key: 'spread_cost', label: 'Spread bps', render: (row) => formatNumber(row.spread_cost) },
              { key: 'average_slippage', label: 'Slippage bps', render: (row) => formatNumber(row.average_slippage) },
            ]}
          />
          <DataTable
            rows={worstSetupRows.slice(0, 8)}
            empty={loading ? 'Loading weak setup TCA...' : 'No weak setup execution rows available.'}
            columns={[
              { key: 'setup_type', label: 'Worst setup', render: (row) => humanize(row.setup_type) },
              { key: 'count', label: 'Rows' },
              { key: 'execution_adjusted_reward', label: 'Exec adj. reward', render: (row) => formatNumber(row.execution_adjusted_reward) },
              { key: 'missed_fill_rate', label: 'Missed fill', render: (row) => formatRatio(row.missed_fill_rate) },
              { key: 'partial_fill_rate', label: 'Partial fill', render: (row) => formatRatio(row.partial_fill_rate) },
            ]}
          />
        </div>
      </SectionCard>

      <SectionCard title="Slippage, Fill Delay, And Alpha Decay" subtitle="Costs are grouped for diagnosis only. Routing stays unchanged.">
        <div className="ui-dashboard-grid">
          <DataTable
            rows={aggregations.slippage_by_engine || []}
            empty={loading ? 'Loading engine slippage...' : 'No engine slippage evidence.'}
            columns={[
              { key: 'engine', label: 'Engine', render: (row) => humanize(row.engine) },
              { key: 'count', label: 'Rows' },
              { key: 'average', label: 'Avg slippage', render: (row) => formatNumber(row.average) },
              { key: 'median', label: 'Median', render: (row) => formatNumber(row.median) },
            ]}
          />
          <DataTable
            rows={aggregations.fill_delay_by_engine || []}
            empty={loading ? 'Loading fill delay...' : 'No fill-delay evidence.'}
            columns={[
              { key: 'engine', label: 'Engine', render: (row) => humanize(row.engine) },
              { key: 'count', label: 'Rows' },
              { key: 'average', label: 'Avg delay sec', render: (row) => formatNumber(row.average) },
              { key: 'median', label: 'Median', render: (row) => formatNumber(row.median) },
            ]}
          />
          <DataTable
            rows={aggregations.alpha_decay_by_engine || []}
            empty={loading ? 'Loading alpha decay...' : 'No alpha-decay evidence.'}
            columns={[
              { key: 'engine', label: 'Engine', render: (row) => humanize(row.engine) },
              { key: 'count', label: 'Rows' },
              { key: 'average', label: 'Alpha decay', render: (row) => formatNumber(row.average) },
              { key: 'dispersion', label: 'Dispersion', render: (row) => formatNumber(row.dispersion) },
            ]}
          />
        </div>
      </SectionCard>

      <SectionCard title="Paper Trade Rows" subtitle="Sanitized local evidence only. Raw broker records, account IDs, and local paths are not shown.">
        <DataTable
          rows={rows.slice(0, 50)}
          empty={loading ? 'Loading paper trade TCA rows...' : 'No paper execution evidence rows available.'}
          columns={[
            { key: 'symbol', label: 'Symbol' },
            { key: 'route', label: 'Route', render: (row) => humanize(row.route) },
            { key: 'setup_type', label: 'Setup', render: (row) => humanize(row.setup_type) },
            { key: 'intended_price', label: 'Intended', render: (row) => formatNumber(row.intended_price, 4) },
            { key: 'fill_price', label: 'Fill', render: (row) => formatNumber(row.fill_price, 4) },
            { key: 'slippage', label: 'Slip bps', render: (row) => formatNumber(row.slippage) },
            { key: 'fill_delay_seconds', label: 'Delay sec', render: (row) => formatNumber(row.fill_delay_seconds) },
            { key: 'execution_adjusted_reward', label: 'Adj reward', render: (row) => formatNumber(row.execution_adjusted_reward) },
            { key: 'warnings', label: 'Warnings', render: (row) => (row.warnings || []).join(', ') || 'None' },
          ]}
        />
      </SectionCard>

      <SectionCard title="Warnings And Missing Data" subtitle="Missing fill, spread, slippage, delay, or quote fields are reported honestly instead of fabricated.">
        <div className="ui-dashboard-grid">
          <DataTable
            rows={warnings.map((warning, index) => ({ warning, index }))}
            empty="No TCA warnings."
            columns={[{ key: 'warning', label: 'Warning' }]}
          />
          <DataTable
            rows={missingFields}
            empty="No missing fields reported."
            columns={[
              { key: 'field', label: 'Missing field', render: (row) => humanize(row.field) },
              { key: 'count', label: 'Count' },
            ]}
          />
          <DataTable
            rows={safetyNotes.map((note, index) => ({ note, index }))}
            empty="Safety notes unavailable."
            columns={[{ key: 'note', label: 'Safety boundary' }]}
          />
        </div>
      </SectionCard>
    </div>
  )
}
