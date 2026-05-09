import { useCallback, useEffect, useMemo, useState } from 'react'
import Button from '../components/Button'
import ErrorState from '../components/ErrorState'
import ListTable from '../components/ListTable'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import StatusBadge from '../components/StatusBadge'
import { getResearchPromotionEntities, setResearchPromotionStatus } from '../api/client'

const MANUAL_STATUSES = ['research', 'candidate', 'walk_forward_testing', 'paper_proven', 'rejected', 'needs_more_evidence']

function humanize(value, fallback = 'Unknown') {
  const text = String(value || '').trim()
  if (!text) return fallback
  return text.replace(/[_-]+/g, ' ').replace(/\b\w/g, (match) => match.toUpperCase())
}

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

function statusTone(status) {
  if (status === 'paper_proven') return 'positive'
  if (status === 'candidate' || status === 'walk_forward_testing') return 'warning'
  if (status === 'rejected') return 'negative'
  if (status === 'needs_more_evidence') return 'neutral'
  return 'neutral'
}

function verdictTone(verdict) {
  if (verdict === 'edge_detected' || verdict === 'passed') return 'positive'
  if (verdict === 'weak_edge_detected' || verdict === 'weak_pass') return 'warning'
  if (verdict === 'no_edge_detected' || verdict === 'failed' || verdict === 'data_quality_too_weak') return 'negative'
  return 'neutral'
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
            <tr key={row.entity_id || row.criterion || row.note || row.warning || index}>
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

function criteriaRows(records, key) {
  return records.flatMap((record) => (record[key] || []).map((criterion) => ({
    ...criterion,
    entity_id: record.entity_id,
    entity_name: record.name,
    promotion_status: record.promotion_status,
  })))
}

export default function ResearchPromotionPage() {
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(true)
  const [savingEntityId, setSavingEntityId] = useState('')
  const [error, setError] = useState('')
  const [manualStatus, setManualStatus] = useState({})

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setReport(await getResearchPromotionEntities())
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load Research Promotion status.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const runManualStatus = useCallback(async (entityId) => {
    const promotionStatus = manualStatus[entityId]
    if (!promotionStatus) return
    setSavingEntityId(entityId)
    setError('')
    try {
      await setResearchPromotionStatus(entityId, {
        promotion_status: promotionStatus,
        reason: 'Manual research metadata status update from Research Promotion page.',
      })
      await load()
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Research promotion status update failed.')
    } finally {
      setSavingEntityId('')
    }
  }, [load, manualStatus])

  const summary = report?.summary || {}
  const records = report?.records || []
  const warnings = report?.warnings || []
  const safetyNotes = report?.safety_notes || []
  const passedRows = useMemo(() => criteriaRows(records, 'criteria_passed').slice(0, 14), [records])
  const failedRows = useMemo(() => criteriaRows(records, 'criteria_failed').slice(0, 14), [records])
  const statusRows = useMemo(() => Object.entries(summary.status_counts || {}).map(([status, count]) => ({ status, count })), [summary.status_counts])

  return (
    <div className="ui-shell__page">
      <PageIntro
        kicker="Research metadata"
        title="Research Promotion"
        description="Assigns research statuses to strategies, setups, engines, blockers, forecast models, AI verdict policies, ranking rules, and risk rules. Promotion status is not live approval."
        badge="No trading authority"
      />
      {error ? <ErrorState description={error} onAction={load} /> : null}

      <div className="ui-action-row">
        <StatusBadge tone="neutral">Research only</StatusBadge>
        <StatusBadge tone="neutral">Paper-proven is not live approval</StatusBadge>
        <StatusBadge tone="neutral">Does not change ranking weights</StatusBadge>
        <StatusBadge tone="neutral">Does not change risk limits</StatusBadge>
        <StatusBadge tone="neutral">Does not place orders</StatusBadge>
        <StatusBadge tone="warning">No guaranteed returns</StatusBadge>
        <Button type="button" variant="ghost" size="sm" onClick={load} disabled={loading || Boolean(savingEntityId)}>
          Refresh
        </Button>
      </div>

      <SectionCard title="Promotion Summary" subtitle="Statuses are research metadata only. They never change broker routes, execution settings, risk gates, or live-trading state.">
        <div className="ui-dashboard-grid">
          <MetricCard label="Entities" value={summary.entity_count ?? records.length} helper="Research entities evaluated" />
          <MetricCard label="Paper-proven research" value={summary.paper_proven_count ?? 0} helper="Not live approval" />
          <MetricCard label="Needs more evidence" value={summary.needs_more_evidence_count ?? 0} helper="Missing sample, baseline, reward, execution, or regime proof" />
          <MetricCard label="Rejected" value={summary.rejected_count ?? 0} helper="Research rejection only" />
          <MetricCard label="Benchmark verdict" value={humanize(summary.benchmark_verdict || 'insufficient_evidence')} helper="Professional Benchmark Suite v1" />
          <MetricCard label="Data quality" value={formatNumber(summary.data_quality_score, 0)} helper="Read-only evidence quality signal" />
        </div>
      </SectionCard>

      <SectionCard title="Research Entity List" subtitle="Manual changes write sanitized research metadata only. They do not edit strategy, risk, broker, ranking, or execution configs.">
        <DataTable
          rows={records}
          empty={loading ? 'Loading research entities...' : 'No research promotion entities were found.'}
          columns={[
            { key: 'name', label: 'Entity' },
            { key: 'entity_type', label: 'Type', render: (row) => humanize(row.entity_type) },
            { key: 'promotion_status', label: 'Research status', render: (row) => <StatusBadge tone={statusTone(row.promotion_status)}>{humanize(row.promotion_status)}</StatusBadge> },
            { key: 'sample_size', label: 'Sample', render: (row) => row.evidence_used?.sample_size ?? 0 },
            { key: 'benchmark_verdict', label: 'Benchmark', render: (row) => <StatusBadge tone={verdictTone(row.evidence_used?.benchmark_verdict)}>{humanize(row.evidence_used?.benchmark_verdict || 'insufficient_evidence')}</StatusBadge> },
            { key: 'walk_forward_status', label: 'Walk-forward', render: (row) => humanize(row.evidence_used?.walk_forward_status || 'none') },
            { key: 'completion_rate', label: 'Completeness', render: (row) => formatRatio(row.evidence_used?.completion_rate) },
            { key: 'safe_explanation', label: 'Explanation' },
            {
              key: 'manual_status',
              label: 'Manual metadata',
              render: (row) => (
                <div className="ui-action-row">
                  <select
                    aria-label={`Manual research status for ${row.name}`}
                    value={manualStatus[row.entity_id] || ''}
                    onChange={(event) => setManualStatus((current) => ({ ...current, [row.entity_id]: event.target.value }))}
                  >
                    <option value="">Select status</option>
                    {MANUAL_STATUSES.map((status) => <option key={status} value={status}>{humanize(status)}</option>)}
                  </select>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={savingEntityId === row.entity_id || !manualStatus[row.entity_id]}
                    onClick={() => runManualStatus(row.entity_id)}
                  >
                    Save
                  </Button>
                </div>
              ),
            },
          ]}
        />
      </SectionCard>

      <SectionCard title="Criteria Passed And Failed" subtitle="Criteria are transparent research checks. Passing a criterion does not authorize orders or risk changes.">
        <div className="ui-dashboard-grid">
          <DataTable
            rows={passedRows}
            empty={loading ? 'Loading passed criteria...' : 'No passed criteria yet.'}
            columns={[
              { key: 'entity_name', label: 'Entity' },
              { key: 'criterion', label: 'Passed criterion', render: (row) => humanize(row.criterion) },
              { key: 'detail', label: 'Detail' },
            ]}
          />
          <DataTable
            rows={failedRows}
            empty={loading ? 'Loading failed criteria...' : 'No failed criteria yet.'}
            columns={[
              { key: 'entity_name', label: 'Entity' },
              { key: 'criterion', label: 'Failed criterion', render: (row) => humanize(row.criterion) },
              { key: 'detail', label: 'Detail' },
            ]}
          />
        </div>
      </SectionCard>

      <SectionCard title="Status Distribution" subtitle="A research status can guide human review, but it never mutates ranking weights automatically.">
        <DataTable
          rows={statusRows}
          empty={loading ? 'Loading status counts...' : 'No status counts available.'}
          columns={[
            { key: 'status', label: 'Research status', render: (row) => <StatusBadge tone={statusTone(row.status)}>{humanize(row.status)}</StatusBadge> },
            { key: 'count', label: 'Count' },
          ]}
        />
      </SectionCard>

      <SectionCard title="Warnings And Safety Notes" subtitle="Research Promotion preserves the paper-only, no-autonomous-live-order boundary.">
        <div className="ui-dashboard-grid">
          <DataTable
            rows={warnings.map((warning, index) => ({ warning, index }))}
            empty="No research promotion warnings."
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
