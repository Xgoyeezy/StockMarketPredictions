import { useCallback, useEffect, useMemo, useState } from 'react'
import Button from '../components/Button'
import ErrorState from '../components/ErrorState'
import ListTable from '../components/ListTable'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import StatusBadge from '../components/StatusBadge'
import { getDataCompletenessSummary, getEvidenceOutcomesSummary } from '../api/client'

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
  if (status === 'needs_attention') return 'warning'
  if (status === 'empty') return 'neutral'
  return 'warning'
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
            <tr key={row.record_id || row.source_type || row.field || row.note || row.action || index}>
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

function sourceRows(summary) {
  return Object.values(summary?.source_summaries || {}).sort((left, right) => (
    String(left.source_type || '').localeCompare(String(right.source_type || ''))
  ))
}

function missingRows(missingFields) {
  return Object.entries(missingFields || {})
    .sort((left, right) => Number(right[1]) - Number(left[1]))
    .map(([field, count]) => ({ field, count }))
}

function sourceRecordRows(recordsBySource) {
  const rows = []
  Object.entries(recordsBySource || {}).forEach(([source, records]) => {
    const list = Array.isArray(records) ? records : []
    rows.push({
      source_type: source,
      total_records: list.length,
      complete_records: list.filter((record) => record.complete).length,
      rewardable_records: list.filter((record) => record.rewardable).length,
      sample_missing: list.find((record) => record.missing_fields?.length)?.missing_fields || [],
    })
  })
  return rows.sort((left, right) => String(left.source_type).localeCompare(String(right.source_type)))
}

export default function DataCompletenessPage() {
  const [report, setReport] = useState(null)
  const [outcomeReport, setOutcomeReport] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [completenessPayload, outcomePayload] = await Promise.all([
        getDataCompletenessSummary(),
        getEvidenceOutcomesSummary(),
      ])
      setReport(completenessPayload)
      setOutcomeReport(outcomePayload)
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load Data Completeness diagnostics.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const summary = report?.summary || {}
  const outcomeSummary = outcomeReport?.summary || {}
  const aggregations = report?.aggregations || {}
  const warnings = report?.warnings || []
  const safetyNotes = report?.safety_notes || []
  const safeNextActions = report?.safe_next_actions || []
  const priorityFields = summary.highest_priority_missing_fields || aggregations.highest_priority_missing_fields || []
  const benchmarkBlockers = summary.benchmark_blockers || aggregations.benchmark_blockers || []
  const missingFieldRows = useMemo(() => missingRows(report?.missing_fields), [report?.missing_fields])
  const categoryRows = useMemo(() => sourceRows(summary), [summary])
  const recordRows = useMemo(() => sourceRecordRows(report?.records_by_source), [report?.records_by_source])

  return (
    <div className="ui-shell__page">
      <PageIntro
        kicker="Research only"
        title="Data Completeness"
        description="Explains whether evidence records are complete enough for reward, forecast, and benchmark attribution. Diagnostics do not affect execution."
        badge="No trading authority"
      />
      {error ? <ErrorState description={error} onAction={load} /> : null}

      <div className="ui-action-row">
        <StatusBadge tone={statusTone(report?.status || summary.status)}>{humanize(report?.status || summary.status || 'empty')}</StatusBadge>
        <StatusBadge tone="neutral">Research only</StatusBadge>
        <StatusBadge tone="neutral">Does not change ranking weights</StatusBadge>
        <StatusBadge tone="neutral">Does not place orders</StatusBadge>
        <StatusBadge tone="warning">No guaranteed returns</StatusBadge>
        <Button type="button" variant="ghost" size="sm" onClick={load} disabled={loading}>
          Refresh
        </Button>
      </div>

      <SectionCard title="Completeness Summary" subtitle="A record must satisfy its contract before it can support reward or benchmark claims.">
        <div className="ui-dashboard-grid">
          <MetricCard label="Completion rate" value={formatRatio(summary.completion_rate)} helper={`${summary.complete_records ?? 0} complete of ${summary.total_records ?? 0} records`} />
          <MetricCard label="Rewardability rate" value={formatRatio(summary.rewardability_rate)} helper={`${summary.rewardable_records ?? 0} rewardable records`} />
          <MetricCard label="Incomplete records" value={summary.incomplete_records ?? 0} helper="Visible with exact missing fields" />
          <MetricCard label="Benchmark readiness" value={summary.benchmark_ready ? 'Ready' : 'Needs data'} helper="Requires rewardable candidates plus benchmark fields" />
        </div>
      </SectionCard>

      <SectionCard title="Evidence Outcome Readiness" subtitle="Candidate rewardability depends on stamped forward outcomes, explicit baselines, execution costs, and regime labels.">
        <div className="ui-dashboard-grid">
          <MetricCard label="Due horizons" value={outcomeSummary.due_count ?? 0} helper="Matured windows that can be stamped" />
          <MetricCard label="Stamped outcomes" value={outcomeSummary.stamped_outcome_rows ?? 0} helper={`${outcomeSummary.available_outcome_count ?? 0} available outcomes`} />
          <MetricCard label="Baseline coverage" value={formatRatio(outcomeSummary.baseline_coverage_rate)} helper="Primary baseline present" />
          <MetricCard label="Execution-cost coverage" value={formatRatio(outcomeSummary.execution_cost_coverage_rate)} helper="Spread, slippage, or paper fill evidence" />
        </div>
      </SectionCard>

      <SectionCard title="Safety Boundary" subtitle="Completeness findings are diagnostics only and never change paper/live trading authority.">
        <div className="ui-dashboard-grid">
          <DataTable
            rows={safetyNotes.map((note, index) => ({ note, index }))}
            empty="Safety notes unavailable."
            columns={[{ key: 'note', label: 'Boundary' }]}
          />
          <DataTable
            rows={warnings.map((warning, index) => ({ warning, index }))}
            empty="No data completeness warnings."
            columns={[{ key: 'warning', label: 'Warning' }]}
          />
        </div>
      </SectionCard>

      <SectionCard title="Missing Data Priority" subtitle="Highest-impact gaps for rewardability and professional benchmark readiness.">
        <div className="ui-dashboard-grid">
          <DataTable
            rows={priorityFields}
            empty={loading ? 'Loading missing-field counts...' : 'No missing fields found.'}
            columns={[
              { key: 'field', label: 'Field' },
              { key: 'count', label: 'Missing count' },
            ]}
          />
          <DataTable
            rows={benchmarkBlockers}
            empty={loading ? 'Loading benchmark blockers...' : 'No benchmark blockers found.'}
            columns={[
              { key: 'field', label: 'Benchmark blocker' },
              { key: 'count', label: 'Missing count' },
            ]}
          />
        </div>
      </SectionCard>

      <SectionCard title="Completeness By Category" subtitle="Candidate, forecast, AI, blocker, missed-move, execution, and benchmark records are audited separately.">
        <DataTable
          rows={categoryRows}
          empty={loading ? 'Loading category completeness...' : 'No source records found.'}
          columns={[
            { key: 'source_label', label: 'Source' },
            { key: 'total_records', label: 'Records' },
            { key: 'complete_records', label: 'Complete' },
            { key: 'rewardable_records', label: 'Rewardable' },
            { key: 'completion_rate', label: 'Completion', render: (row) => formatRatio(row.completion_rate) },
            { key: 'rewardability_rate', label: 'Rewardability', render: (row) => formatRatio(row.rewardability_rate) },
          ]}
        />
      </SectionCard>

      <SectionCard title="Contract Coverage" subtitle="Rows without required data remain visible, but they are not counted as rewardable.">
        <DataTable
          rows={recordRows}
          empty={loading ? 'Loading source records...' : 'No audited records available.'}
          columns={[
            { key: 'source_type', label: 'Source', render: (row) => humanize(row.source_type) },
            { key: 'total_records', label: 'Records' },
            { key: 'complete_records', label: 'Complete' },
            { key: 'rewardable_records', label: 'Rewardable' },
            { key: 'sample_missing', label: 'Example missing fields', render: (row) => (row.sample_missing?.length ? row.sample_missing.join(', ') : 'None') },
          ]}
        />
      </SectionCard>

      <SectionCard title="Missing Fields" subtitle="Specific fields preventing rewardability or benchmark evidence.">
        <DataTable
          rows={missingFieldRows}
          empty={loading ? 'Loading missing fields...' : 'No missing fields were reported.'}
          columns={[
            { key: 'field', label: 'Field' },
            { key: 'count', label: 'Count' },
          ]}
        />
      </SectionCard>

      <SectionCard title="Safe Next Actions" subtitle="Manual research tasks only. They do not trigger orders, routing changes, or automatic ranking changes.">
        <DataTable
          rows={safeNextActions}
          empty={loading ? 'Loading next actions...' : 'No next actions are needed.'}
          columns={[
            { key: 'field', label: 'Field' },
            { key: 'count', label: 'Count' },
            { key: 'action', label: 'Action' },
            { key: 'manual_review_only', label: 'Authority', render: (row) => <StatusBadge tone="neutral">{row.manual_review_only ? 'Manual review only' : 'Review'}</StatusBadge> },
          ]}
        />
      </SectionCard>
    </div>
  )
}
