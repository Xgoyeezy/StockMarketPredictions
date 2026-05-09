import { useCallback, useEffect, useMemo, useState } from 'react'
import Button from '../components/Button'
import ErrorState from '../components/ErrorState'
import ListTable from '../components/ListTable'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import StatusBadge from '../components/StatusBadge'
import {
  getEvidenceOutcomesSummary,
  stampDueEvidenceOutcomes,
} from '../api/client'

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

function toneForStatus(status) {
  if (status === 'ready') return 'positive'
  if (status === 'needs_attention') return 'warning'
  if (status === 'empty') return 'neutral'
  return 'neutral'
}

function DataTable({ columns, rows, empty }) {
  return (
    <ListTable>
      <table className="ui-list-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key}>{column.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length ? rows.map((row, index) => (
            <tr key={row.outcome_record_id || row.idempotency_key || row.field || row.note || index}>
              {columns.map((column) => (
                <td key={column.key}>{column.render ? column.render(row) : row[column.key]}</td>
              ))}
            </tr>
          )) : (
            <tr>
              <td colSpan={columns.length}>{empty}</td>
            </tr>
          )}
        </tbody>
      </table>
    </ListTable>
  )
}

export default function EvidenceOutcomesPage() {
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(true)
  const [stamping, setStamping] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setReport(await getEvidenceOutcomesSummary())
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load Evidence Outcomes.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const stampDue = useCallback(async () => {
    setStamping(true)
    setError('')
    try {
      await stampDueEvidenceOutcomes()
      await load()
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to stamp due evidence outcomes.')
    } finally {
      setStamping(false)
    }
  }, [load])

  const summary = report?.summary || {}
  const warnings = report?.warnings || []
  const safetyNotes = report?.safety_notes || []
  const dueRows = report?.due_records || []
  const records = report?.records || []
  const missingFieldRows = useMemo(() => (
    Object.entries(report?.missing_fields || {})
      .sort((left, right) => Number(right[1]) - Number(left[1]))
      .map(([field, count]) => ({ field, count }))
  ), [report?.missing_fields])

  return (
    <div className="ui-shell__page">
      <PageIntro
        kicker="Research only"
        title="Evidence Outcomes"
        description="Stamps matured candidate horizons with forward returns, baselines, and paper-route execution cost evidence. This proof layer does not affect trading."
        badge="Paper-only evidence"
      />
      {error ? <ErrorState description={error} onAction={load} /> : null}

      <div className="ui-action-row">
        <StatusBadge tone={toneForStatus(report?.status)}>{humanize(report?.status || 'empty')}</StatusBadge>
        <StatusBadge tone="neutral">Research only</StatusBadge>
        <StatusBadge tone="neutral">Paper only</StatusBadge>
        <StatusBadge tone="neutral">Does not place orders</StatusBadge>
        <StatusBadge tone="neutral">Does not change ranking weights</StatusBadge>
        <Button type="button" variant="ghost" size="sm" onClick={load} disabled={loading || stamping}>
          Refresh
        </Button>
        <Button type="button" variant="secondary" size="sm" onClick={stampDue} disabled={loading || stamping}>
          Stamp Due
        </Button>
      </div>

      <SectionCard title="Outcome Coverage" subtitle={summary.primary_baseline_rule || 'Primary baseline defaults to random candidate, otherwise SPY.'}>
        <div className="ui-dashboard-grid">
          <MetricCard label="Lifecycle rows" value={summary.candidate_lifecycle_rows ?? 0} helper="Original candidate rows are preserved" />
          <MetricCard label="Stamped outcomes" value={summary.stamped_outcome_rows ?? 0} helper={`${summary.available_outcome_count ?? 0} available outcomes`} />
          <MetricCard label="Due horizons" value={summary.due_count ?? 0} helper="Matured windows not yet stamped" />
          <MetricCard label="Candidates with outcomes" value={summary.candidate_with_outcomes_count ?? 0} helper={`${summary.candidate_without_outcomes_count ?? 0} still missing outcomes`} />
          <MetricCard label="Baseline coverage" value={formatRatio(summary.baseline_coverage_rate)} helper="Rows with a primary baseline" />
          <MetricCard label="Execution-cost coverage" value={formatRatio(summary.execution_cost_coverage_rate)} helper="Spread, slippage, or paper fill cost evidence" />
        </div>
      </SectionCard>

      <SectionCard title="Due Horizons" subtitle="Due rows are previewed only until stamped through the append-only research endpoint. Missing prices are not fabricated.">
        <DataTable
          rows={dueRows.slice(0, 25)}
          empty={loading ? 'Loading due horizons...' : 'No matured candidate horizons are due.'}
          columns={[
            { key: 'symbol', label: 'Symbol' },
            { key: 'horizon_minutes', label: 'Horizon' },
            { key: 'available', label: 'Status', render: (row) => <StatusBadge tone={row.available ? 'positive' : 'warning'}>{row.available ? 'Available' : 'Missing data'}</StatusBadge> },
            { key: 'actual_forward_return', label: 'Actual', render: (row) => `${formatNumber(row.actual_forward_return)}%` },
            { key: 'baseline_forward_return', label: 'Baseline', render: (row) => `${formatNumber(row.baseline_forward_return)}%` },
            { key: 'reason', label: 'Reason' },
          ]}
        />
      </SectionCard>

      <SectionCard title="Stamped Records" subtitle="These records are appended separately from lifecycle and forecast records.">
        <DataTable
          rows={records.slice().reverse().slice(0, 25)}
          empty={loading ? 'Loading stamped records...' : 'No outcome records have been stamped yet.'}
          columns={[
            { key: 'symbol', label: 'Symbol' },
            { key: 'horizon_minutes', label: 'Horizon' },
            { key: 'primary_baseline', label: 'Baseline', render: (row) => humanize(row.primary_baseline || 'missing') },
            { key: 'actual_forward_return', label: 'Actual', render: (row) => `${formatNumber(row.actual_forward_return)}%` },
            { key: 'baseline_forward_return', label: 'Primary', render: (row) => `${formatNumber(row.baseline_forward_return)}%` },
            { key: 'paper_fill_status', label: 'Paper fill', render: (row) => humanize(row.paper_fill_status || 'none') },
            { key: 'available', label: 'Rewardable input', render: (row) => <StatusBadge tone={row.available ? 'positive' : 'warning'}>{row.available ? 'Ready' : 'Not ready'}</StatusBadge> },
          ]}
        />
      </SectionCard>

      <SectionCard title="Missing Data And Safety" subtitle="Missing data keeps reward, benchmark, and calibration claims honest.">
        <div className="ui-dashboard-grid">
          <DataTable
            rows={missingFieldRows}
            empty={loading ? 'Loading missing fields...' : 'No missing outcome fields reported.'}
            columns={[
              { key: 'field', label: 'Field', render: (row) => humanize(row.field) },
              { key: 'count', label: 'Count' },
            ]}
          />
          <DataTable
            rows={warnings.map((warning, index) => ({ warning, index }))}
            empty="No outcome warnings."
            columns={[{ key: 'warning', label: 'Warning' }]}
          />
          <DataTable
            rows={safetyNotes.map((note, index) => ({ note, index }))}
            empty="Safety notes unavailable."
            columns={[{ key: 'note', label: 'Boundary' }]}
          />
        </div>
      </SectionCard>
    </div>
  )
}
