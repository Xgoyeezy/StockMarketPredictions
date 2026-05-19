import { useCallback, useEffect, useMemo, useState } from 'react'
import Button from '../components/Button'
import ErrorState from '../components/ErrorState'
import FinishTrackerSection from '../components/FinishTrackerSection'
import ListTable from '../components/ListTable'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import StatusBadge from '../components/StatusBadge'
import { getProofMetricsSummary } from '../api/client'

function humanize(value, fallback = 'Unknown') {
  const text = String(value || '').trim()
  if (!text) return fallback
  return text.replace(/[_-]+/g, ' ').replace(/\b\w/g, (match) => match.toUpperCase())
}

function statusTone(status) {
  const text = String(status || '').toLowerCase()
  if (text.includes('ready')) return 'positive'
  if (text.includes('unavailable') || text.includes('empty') || text.includes('no_records')) return 'neutral'
  if (text.includes('blocked') || text.includes('needs')) return 'warning'
  return 'neutral'
}

function formatMetric(value, target) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '--'
  const targetNumber = Number(target)
  const asPct = Number.isFinite(targetNumber) && targetNumber <= 1
  return asPct ? `${(numeric * 100).toFixed(1)}%` : numeric.toFixed(2)
}

function compactList(items, fallback = 'None') {
  const list = Array.isArray(items) ? items.filter(Boolean) : []
  if (!list.length) return fallback
  return list.slice(0, 3).map(humanize).join(', ')
}

function formatPlanBlockers(row) {
  const openItems = row.proof_plan_open_items ?? row.proof_plan?.open_item_count
  const criticalItems = row.proof_plan_critical_open_items ?? row.proof_plan?.critical_open_items
  const topItem = row.proof_plan_top_item ?? row.proof_plan?.top_item
  if (openItems == null) return 'No proof plan attached'
  const criticalText = criticalItems ? `${criticalItems} critical` : 'no critical'
  return `${openItems} open, ${criticalText}${topItem ? `: ${topItem}` : ''}`
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
            <tr key={`${row.key || row.source || row.gate || row.id || 'row'}-${index}`}>
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

export default function ProofMetricsPage() {
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setReport(await getProofMetricsSummary())
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load Proof Metrics.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const summary = report?.summary || {}
  const metrics = report?.metrics || []
  const gateGroups = report?.gate_groups || []
  const sourceReports = report?.source_reports || []
  const warnings = report?.warnings || []
  const safetyNotes = report?.safety_notes || []
  const safeNextActions = report?.safe_next_actions || []
  const deferredScope = report?.deferred_scope || []
  const openMetrics = useMemo(() => metrics.filter((row) => row.status !== 'ready'), [metrics])

  return (
    <div className="ui-shell__page">
      <PageIntro
        kicker="Proof visibility"
        title="Proof Metrics"
        description="Aggregates proof gaps across data completeness, outcomes, benchmarks, walk-forward, execution quality, review layers, and safety gates. This page is read-only and does not affect trading."
        badge="No trading authority"
      />
      {error ? <ErrorState description={error} onAction={load} /> : null}

      <div className="ui-action-row">
        <StatusBadge tone={statusTone(report?.status || summary.status)}>{humanize(report?.status || summary.status || 'blocked_by_evidence')}</StatusBadge>
        <StatusBadge tone="neutral">Proof decides priority</StatusBadge>
        <StatusBadge tone="neutral">Research only</StatusBadge>
        <StatusBadge tone="neutral">Read-only visibility</StatusBadge>
        <StatusBadge tone="warning">No proof of alpha</StatusBadge>
        <StatusBadge tone="warning">No live-trading readiness</StatusBadge>
        <Button type="button" variant="ghost" size="sm" onClick={load} disabled={loading}>
          {loading ? 'Refreshing...' : 'Refresh'}
        </Button>
      </div>

      <SectionCard title="Proof Chain Summary" subtitle={summary.claim_boundary || 'Proof metrics are visibility only and do not approve expansion or execution changes.'}>
        <div className="ui-dashboard-grid">
          <MetricCard label="Metrics ready" value={`${summary.ready_metric_count ?? 0}/${summary.metric_count ?? metrics.length}`} helper={`${summary.open_metric_count ?? openMetrics.length} open proof metrics`} />
          <MetricCard label="Critical open" value={summary.critical_open_metric_count ?? 0} helper={`${summary.high_open_metric_count ?? 0} high-priority open metrics`} tone={summary.critical_open_metric_count ? 'warning' : 'default'} />
          <MetricCard label="Gates blocked" value={summary.gates_blocked_count ?? 0} helper={`${summary.gates_ready_count ?? 0}/${summary.gate_count ?? gateGroups.length} gates ready`} />
          <MetricCard label="Unavailable sources" value={summary.source_unavailable_count ?? 0} helper={`${summary.source_count ?? sourceReports.length} source reports checked`} tone={summary.source_unavailable_count ? 'warning' : 'default'} />
          <MetricCard label="Deferred expansion" value={summary.deferred_expansion_count ?? deferredScope.length} helper="Future scope remains gated by proof" />
        </div>
      </SectionCard>

      <SectionCard title="Safety Boundary">
        <div className="ui-action-row">
          {(safetyNotes.length ? safetyNotes : ['Proof metrics are read-only visibility.', 'Does not place orders.', 'Does not change broker routes.', 'Does not bypass risk gates.', 'Does not clear kill switches.']).map((note) => (
            <StatusBadge key={note} tone="neutral">{note}</StatusBadge>
          ))}
        </div>
      </SectionCard>

      <SectionCard title="Proof Metrics" subtitle="Each row shows the gate blocked by the current evidence gap and the next manual research action.">
        <DataTable
          rows={metrics}
          empty={loading ? 'Loading proof metrics...' : 'No proof metrics are available.'}
          columns={[
            { key: 'label', label: 'Metric' },
            { key: 'gate', label: 'Gate' },
            { key: 'status', label: 'Status', render: (row) => <StatusBadge tone={statusTone(row.status)}>{humanize(row.status)}</StatusBadge> },
            { key: 'proof_plan', label: 'Plan blockers', render: formatPlanBlockers },
            { key: 'value', label: 'Value', render: (row) => formatMetric(row.value, row.target) },
            { key: 'target', label: 'Target', render: (row) => formatMetric(row.target, row.target) },
            { key: 'blocked_claims', label: 'Blocked claims', render: (row) => compactList(row.blocked_claims) },
            { key: 'action', label: 'Next safe action', render: (row) => row.safe_next_action },
          ]}
        />
      </SectionCard>

      <SectionCard title="Gate Groups" subtitle="Gates remain blocked while any required metric is missing, stale, unavailable, or below threshold.">
        <DataTable
          rows={gateGroups}
          empty={loading ? 'Loading gate groups...' : 'No gate groups are available.'}
          columns={[
            { key: 'gate', label: 'Gate' },
            { key: 'status', label: 'Status', render: (row) => <StatusBadge tone={statusTone(row.status)}>{humanize(row.status)}</StatusBadge> },
            { key: 'ready_metric_count', label: 'Ready metrics', render: (row) => `${row.ready_metric_count ?? 0}/${row.metric_count ?? 0}` },
            { key: 'critical_open_metric_count', label: 'Critical open' },
            { key: 'top_gap', label: 'Top gap', render: (row) => row.top_gap || 'None' },
            { key: 'blocked_claims', label: 'Blocked claims', render: (row) => compactList(row.blocked_claims) },
          ]}
        />
      </SectionCard>

      <div className="ui-dashboard-grid ui-dashboard-grid--two">
        <SectionCard title="Source Reports">
          <DataTable
            rows={sourceReports}
            empty={loading ? 'Loading source reports...' : 'No source report status is available.'}
            columns={[
              { key: 'label', label: 'Source' },
              { key: 'status', label: 'Status', render: (row) => <StatusBadge tone={statusTone(row.status)}>{humanize(row.status)}</StatusBadge> },
              { key: 'warning_count', label: 'Warnings' },
              { key: 'missing_field_count', label: 'Missing fields' },
              { key: 'tracker', label: 'Tracker', render: (row) => row.finish_tracker_present ? 'Present' : 'Missing' },
            ]}
          />
        </SectionCard>

        <SectionCard title="Open Proof Actions">
          <DataTable
            rows={safeNextActions}
            empty={loading ? 'Loading safe next actions...' : 'No open proof actions are available.'}
            columns={[
              { key: 'gate', label: 'Gate' },
              { key: 'priority', label: 'Priority', render: (row) => humanize(row.priority) },
              { key: 'action', label: 'Manual action' },
            ]}
          />
        </SectionCard>
      </div>

      <div className="ui-dashboard-grid ui-dashboard-grid--two">
        <SectionCard title="Deferred Scope">
          <DataTable
            rows={deferredScope}
            empty="No deferred scope rows are available."
            columns={[
              { key: 'title', label: 'Deferred item' },
              { key: 'status', label: 'Status', render: (row) => <StatusBadge tone="neutral">{humanize(row.status)}</StatusBadge> },
              { key: 'safe_boundary', label: 'Boundary' },
            ]}
          />
        </SectionCard>

        <SectionCard title="Warnings">
          <DataTable
            rows={warnings.map((warning, index) => ({ warning, index }))}
            empty="No proof metric warnings."
            columns={[{ key: 'warning', label: 'Warning' }]}
          />
        </SectionCard>
      </div>

      <FinishTrackerSection tracker={report?.finish_tracker} loading={loading} />
    </div>
  )
}
