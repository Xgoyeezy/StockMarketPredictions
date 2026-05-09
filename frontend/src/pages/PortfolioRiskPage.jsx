import { useCallback, useEffect, useMemo, useState } from 'react'
import Button from '../components/Button'
import ErrorState from '../components/ErrorState'
import ListTable from '../components/ListTable'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import StatusBadge from '../components/StatusBadge'
import { getPortfolioRiskSummary } from '../api/client'

function formatMoney(value) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '--'
  return numeric.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
}

function formatNumber(value, digits = 2) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '--'
  return numeric.toFixed(digits)
}

function formatPercent(value) {
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

function exposureTone(value) {
  const numeric = Math.abs(Number(value))
  if (!Number.isFinite(numeric)) return 'neutral'
  if (numeric >= 0.75) return 'negative'
  if (numeric >= 0.45) return 'warning'
  return 'positive'
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
            <tr key={row.symbol || row.sector || row.engine || row.setup_type || row.scenario || row.bucket || row.field || index}>
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

export default function PortfolioRiskPage() {
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setReport(await getPortfolioRiskSummary())
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load Portfolio Risk Intelligence.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const summary = report?.summary || {}
  const aggregations = report?.aggregations || {}
  const concentration = aggregations.concentration || {}
  const correlation = aggregations.correlation_heat || {}
  const liquidity = aggregations.liquidity_exposure || {}
  const drawdown = aggregations.drawdown_state || {}
  const riskBudget = aggregations.daily_risk_budget_usage || {}
  const openHeat = aggregations.open_heat || {}
  const forecastConfidence = aggregations.forecast_confidence_exposure || {}
  const warnings = report?.warnings || []
  const safetyNotes = report?.safety_notes || []
  const stressTests = report?.stress_tests || []
  const missingFieldRows = useMemo(() => missingRows(report?.missing_fields), [report?.missing_fields])

  return (
    <div className="ui-shell__page">
      <PageIntro
        kicker="Risk visibility"
        title="Portfolio Risk Intelligence"
        description="Shows paper portfolio exposure, concentration, correlation heat, liquidity, drawdown state, and stress scenarios. This analytics page does not change gates, routes, ranking, or orders."
        badge="Read-only analytics"
      />
      {error ? <ErrorState description={error} onAction={load} /> : null}

      <div className="ui-action-row">
        <StatusBadge tone={statusTone(report?.status)}>{humanize(report?.status || 'empty')}</StatusBadge>
        <StatusBadge tone="neutral">Research only</StatusBadge>
        <StatusBadge tone="neutral">Paper only</StatusBadge>
        <StatusBadge tone="neutral">Does not change risk gates</StatusBadge>
        <StatusBadge tone="neutral">Does not place or block orders</StatusBadge>
        <StatusBadge tone="warning">No guaranteed returns</StatusBadge>
        <Button type="button" variant="ghost" size="sm" onClick={load} disabled={loading}>
          Refresh
        </Button>
      </div>

      <SectionCard title="Portfolio Risk Summary" subtitle="Exposure is calculated from paper route records and available evidence. Missing data is reported instead of inferred.">
        <div className="ui-dashboard-grid">
          <MetricCard label="Gross exposure" value={formatMoney(summary.gross_exposure)} helper={`${summary.position_count ?? 0} paper records`} />
          <MetricCard label="Net exposure" value={formatMoney(summary.net_exposure)} helper={`${formatMoney(summary.long_exposure)} long / ${formatMoney(summary.short_or_proxy_exposure)} proxy`} />
          <MetricCard label="Symbol concentration" value={formatPercent(summary.symbol_concentration)} helper="Largest symbol share of gross exposure" />
          <MetricCard label="Sector concentration" value={formatPercent(summary.sector_concentration)} helper="Largest sector share of gross exposure" />
          <MetricCard label="Correlation heat" value={formatNumber(summary.correlation_heat, 1)} helper="Bucket crowding score" />
          <MetricCard label="Open heat" value={formatPercent(summary.open_heat)} helper={humanize(openHeat.open_heat_state || 'unknown')} />
          <MetricCard label="Daily risk budget" value={formatPercent(summary.daily_risk_budget_usage)} helper={`${formatMoney(riskBudget.open_risk_estimate)} of ${formatMoney(riskBudget.daily_risk_budget)}`} />
          <MetricCard label="Drawdown state" value={humanize(summary.drawdown_state)} helper={`${formatPercent(drawdown.current_drawdown_pct)} current drawdown`} />
        </div>
      </SectionCard>

      <SectionCard title="Exposure Breakdown" subtitle="Sector, engine, setup, strategy, regime, and confidence exposure are visibility metrics only.">
        <div className="ui-dashboard-grid">
          <DataTable
            rows={(aggregations.sector_exposure || []).slice(0, 8)}
            empty={loading ? 'Loading sector exposure...' : 'No sector exposure data.'}
            columns={[
              { key: 'sector', label: 'Sector', render: (row) => humanize(row.sector) },
              { key: 'gross_exposure', label: 'Gross', render: (row) => formatMoney(row.gross_exposure) },
              { key: 'net_exposure', label: 'Net', render: (row) => formatMoney(row.net_exposure) },
              { key: 'exposure_share', label: 'Share', render: (row) => <StatusBadge tone={exposureTone(row.exposure_share)}>{formatPercent(row.exposure_share)}</StatusBadge> },
            ]}
          />
          <DataTable
            rows={(aggregations.engine_exposure || []).slice(0, 8)}
            empty={loading ? 'Loading engine exposure...' : 'No engine exposure data.'}
            columns={[
              { key: 'engine', label: 'Engine', render: (row) => humanize(row.engine) },
              { key: 'count', label: 'Rows' },
              { key: 'gross_exposure', label: 'Gross', render: (row) => formatMoney(row.gross_exposure) },
              { key: 'exposure_share', label: 'Share', render: (row) => formatPercent(row.exposure_share) },
            ]}
          />
          <DataTable
            rows={(aggregations.setup_exposure || []).slice(0, 8)}
            empty={loading ? 'Loading setup exposure...' : 'No setup exposure data.'}
            columns={[
              { key: 'setup_type', label: 'Setup', render: (row) => humanize(row.setup_type) },
              { key: 'count', label: 'Rows' },
              { key: 'gross_exposure', label: 'Gross', render: (row) => formatMoney(row.gross_exposure) },
              { key: 'exposure_share', label: 'Share', render: (row) => formatPercent(row.exposure_share) },
            ]}
          />
          <DataTable
            rows={(aggregations.regime_exposure || []).slice(0, 8)}
            empty={loading ? 'Loading regime exposure...' : 'No regime exposure data.'}
            columns={[
              { key: 'regime', label: 'Regime', render: (row) => humanize(row.regime) },
              { key: 'gross_exposure', label: 'Gross', render: (row) => formatMoney(row.gross_exposure) },
              { key: 'average_beta_to_SPY', label: 'SPY beta', render: (row) => formatNumber(row.average_beta_to_SPY) },
              { key: 'average_forecast_confidence', label: 'Confidence', render: (row) => formatPercent(row.average_forecast_confidence) },
            ]}
          />
        </div>
      </SectionCard>

      <SectionCard title="Concentration And Correlation" subtitle="Correlation heat uses configured/default symbol buckets, not broker-side enforcement.">
        <div className="ui-dashboard-grid">
          <DataTable
            rows={(concentration.top_symbols || []).slice(0, 10)}
            empty={loading ? 'Loading symbol concentration...' : 'No symbol concentration data.'}
            columns={[
              { key: 'symbol', label: 'Symbol' },
              { key: 'gross_exposure', label: 'Gross', render: (row) => formatMoney(row.gross_exposure) },
              { key: 'net_exposure', label: 'Net', render: (row) => formatMoney(row.net_exposure) },
              { key: 'exposure_share', label: 'Share', render: (row) => formatPercent(row.exposure_share) },
            ]}
          />
          <DataTable
            rows={(correlation.buckets || []).slice(0, 10)}
            empty={loading ? 'Loading correlation buckets...' : 'No correlation bucket data.'}
            columns={[
              { key: 'correlation_bucket', label: 'Bucket', render: (row) => humanize(row.correlation_bucket) },
              { key: 'count', label: 'Rows' },
              { key: 'gross_exposure', label: 'Gross', render: (row) => formatMoney(row.gross_exposure) },
              { key: 'exposure_share', label: 'Share', render: (row) => formatPercent(row.exposure_share) },
            ]}
          />
          <DataTable
            rows={forecastConfidence.buckets || []}
            empty={loading ? 'Loading forecast confidence exposure...' : 'No forecast confidence data.'}
            columns={[
              { key: 'bucket', label: 'Confidence', render: (row) => humanize(row.bucket) },
              { key: 'count', label: 'Rows' },
              { key: 'gross_exposure', label: 'Gross', render: (row) => formatMoney(row.gross_exposure) },
              { key: 'exposure_share', label: 'Share', render: (row) => formatPercent(row.exposure_share) },
            ]}
          />
        </div>
      </SectionCard>

      <SectionCard title="Stress Tests" subtitle="Scenario analytics are diagnostics only and do not alter risk gates or broker routes.">
        <DataTable
          rows={stressTests}
          empty={loading ? 'Loading stress tests...' : 'No stress scenarios available.'}
          columns={[
            { key: 'scenario', label: 'Scenario', render: (row) => humanize(row.scenario) },
            { key: 'estimated_pnl', label: 'Estimated P&L', render: (row) => row.estimated_pnl === null || row.estimated_pnl === undefined ? '--' : formatMoney(row.estimated_pnl) },
            { key: 'estimated_return_on_gross', label: 'Gross return', render: (row) => formatPercent(row.estimated_return_on_gross) },
            { key: 'analytics_only', label: 'Authority', render: () => <StatusBadge tone="neutral">Analytics only</StatusBadge> },
          ]}
        />
      </SectionCard>

      <SectionCard title="Liquidity And Missing Data" subtitle="Missing beta, sector, liquidity, or confidence fields reduce risk visibility but do not change trading automatically.">
        <div className="ui-dashboard-grid">
          <MetricCard label="Liquidity exposure" value={formatMoney(liquidity.liquidity_exposure)} helper={`${formatPercent(liquidity.liquidity_exposure_share)} of gross`} />
          <MetricCard label="Average liquidity score" value={formatNumber(liquidity.average_liquidity_score)} helper={`${liquidity.liquidity_warning_count || 0} warnings`} />
          <MetricCard label="Average SPY beta" value={formatNumber(summary.beta_to_SPY)} helper="Weighted by exposure when present" />
          <MetricCard label="Average QQQ beta" value={formatNumber(summary.beta_to_QQQ)} helper="Weighted by exposure when present" />
          <DataTable
            rows={warnings.map((warning, index) => ({ warning, index }))}
            empty="No portfolio risk warnings."
            columns={[{ key: 'warning', label: 'Warning' }]}
          />
          <DataTable
            rows={missingFieldRows}
            empty="No missing fields reported."
            columns={[
              { key: 'field', label: 'Missing field', render: (row) => humanize(row.field) },
              { key: 'count', label: 'Count' },
            ]}
          />
        </div>
      </SectionCard>

      <SectionCard title="Safety Boundary" subtitle="Portfolio Risk Intelligence is separate from risk enforcement.">
        <DataTable
          rows={safetyNotes.map((note, index) => ({ note, index }))}
          empty="No safety notes returned."
          columns={[{ key: 'note', label: 'Safety note' }]}
        />
      </SectionCard>
    </div>
  )
}
