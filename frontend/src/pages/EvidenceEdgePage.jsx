import { useCallback, useEffect, useState } from 'react'
import ErrorState from '../components/ErrorState'
import ListTable from '../components/ListTable'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import StatusBadge from '../components/StatusBadge'
import Button from '../components/Button'
import {
  getOrganizationTradeAutomationEvidenceEdgeSummary,
} from '../api/client'

function formatNumber(value, digits = 2) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '--'
  return numeric.toFixed(digits)
}

function formatPct(value) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '--'
  return `${numeric.toFixed(2)}%`
}

function humanize(value, fallback = 'Unknown') {
  const text = String(value || '').trim()
  if (!text) return fallback
  return text.replace(/[_-]+/g, ' ').replace(/\b\w/g, (match) => match.toUpperCase())
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
            <tr key={row.id || row.key || `${index}-${columns[0]?.key}`}>
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

export default function EvidenceEdgePage() {
  const [summary, setSummary] = useState(null)
  const [blockers, setBlockers] = useState([])
  const [setups, setSetups] = useState([])
  const [engines, setEngines] = useState([])
  const [regimes, setRegimes] = useState([])
  const [recommendations, setRecommendations] = useState([])
  const [positiveFeatures, setPositiveFeatures] = useState([])
  const [negativeFeatures, setNegativeFeatures] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const summaryPayload = await getOrganizationTradeAutomationEvidenceEdgeSummary()
      setSummary(summaryPayload.summary || {})
      setBlockers(summaryPayload.blocker_effectiveness || [])
      setSetups(summaryPayload.setup_forward_return_stats || [])
      setEngines(summaryPayload.engine_forward_return_stats || [])
      setRegimes(summaryPayload.regime_forward_return_stats || [])
      setRecommendations(summaryPayload.recommended_ranking_adjustments || [])
      setPositiveFeatures(summaryPayload.top_positive_features || [])
      setNegativeFeatures(summaryPayload.top_negative_features || [])
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load Evidence Edge analytics.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const missingFields = summary?.missing_fields || {}
  const missingRows = Object.entries(missingFields)
    .sort((left, right) => Number(right[1]) - Number(left[1]))
    .map(([field, count]) => ({ field, count }))

  return (
    <div className="ui-shell__page">
      <PageIntro
        kicker="Paper-only research"
        title="Evidence Edge"
        description="Outcome attribution for candidate blockers, setups, engines, regimes, and manual ranking recommendations. It cannot submit orders or change risk gates."
        badge="No guaranteed returns"
      />
      {error ? <ErrorState description={error} onAction={load} /> : null}
      <div className="ui-action-row">
        <StatusBadge tone="neutral">Research only</StatusBadge>
        <StatusBadge tone="warning">Alpaca paper evidence</StatusBadge>
        <StatusBadge tone="neutral">No autonomous live orders</StatusBadge>
        <Button type="button" variant="ghost" size="sm" onClick={load} disabled={loading}>
          Refresh
        </Button>
      </div>
      <div className="ui-dashboard-grid">
        <MetricCard label="Candidates" value={summary?.candidate_count ?? 0} helper={`${summary?.observed_outcome_count ?? 0} observed outcomes`} />
        <MetricCard label="Allowed / blocked" value={`${summary?.allowed_count ?? 0} / ${summary?.blocked_count ?? 0}`} helper="Decision-boundary attribution" />
        <MetricCard label="Missed winners" value={summary?.missed_move_count ?? 0} helper="Blocked candidates with favorable follow-through" />
        <MetricCard label="Data status" value={humanize(summary?.data_status, 'Loading')} helper={summary?.next_action || 'Collecting evidence.'} />
      </div>

      <SectionCard title="Blocker Effectiveness" subtitle="Positive blocker value means the gate likely avoided losing outcomes; negative value means it may have blocked winners.">
        <DataTable
          empty={loading ? 'Loading blockers...' : 'No blocker outcomes are available yet.'}
          rows={blockers.slice(0, 12)}
          columns={[
            { key: 'blocker', label: 'Blocker', render: (row) => humanize(row.blocker) },
            { key: 'times_seen', label: 'Seen' },
            { key: 'average_forward_return_after_block', label: 'Avg after block', render: (row) => formatPct(row.average_forward_return_after_block) },
            { key: 'estimated_blocker_value', label: 'Estimated value', render: (row) => formatPct(row.estimated_blocker_value) },
            { key: 'false_block_rate', label: 'False block', render: (row) => formatPct(Number(row.false_block_rate) * 100) },
            { key: 'confidence_bucket', label: 'Confidence', render: (row) => humanize(row.confidence_bucket) },
            { key: 'recommendation', label: 'Action', render: (row) => humanize(row.recommendation) },
          ]}
        />
      </SectionCard>

      <SectionCard title="Setup Outcomes" subtitle="Forward-return evidence by setup type. These rows are recommendations only and do not mutate ranking weights.">
        <DataTable
          empty={loading ? 'Loading setups...' : 'No setup outcomes are available yet.'}
          rows={setups.slice(0, 12)}
          columns={[
            { key: 'setup_type', label: 'Setup', render: (row) => humanize(row.setup_type) },
            { key: 'candidate_count', label: 'Candidates' },
            { key: 'observed_outcome_count', label: 'Observed' },
            { key: 'average_forward_return_pct', label: 'Avg return', render: (row) => formatPct(row.average_forward_return_pct) },
            { key: 'win_rate', label: 'Win rate', render: (row) => formatPct(Number(row.win_rate) * 100) },
            { key: 'confidence_bucket', label: 'Confidence', render: (row) => humanize(row.confidence_bucket) },
          ]}
        />
      </SectionCard>

      <SectionCard title="Engine Performance By Regime" subtitle="Engine and regime attribution from paper evidence and candidate lifecycle records.">
        <div className="ui-dashboard-grid">
          <DataTable
            empty={loading ? 'Loading engines...' : 'No engine outcomes are available yet.'}
            rows={engines.slice(0, 8)}
            columns={[
              { key: 'engine', label: 'Engine', render: (row) => humanize(row.engine) },
              { key: 'candidate_count', label: 'Candidates' },
              { key: 'average_forward_return_pct', label: 'Avg return', render: (row) => formatPct(row.average_forward_return_pct) },
              { key: 'win_rate', label: 'Win rate', render: (row) => formatPct(Number(row.win_rate) * 100) },
            ]}
          />
          <DataTable
            empty={loading ? 'Loading regimes...' : 'No regime outcomes are available yet.'}
            rows={regimes.slice(0, 8)}
            columns={[
              { key: 'regime', label: 'Regime', render: (row) => humanize(row.regime) },
              { key: 'candidate_count', label: 'Candidates' },
              { key: 'average_forward_return_pct', label: 'Avg return', render: (row) => formatPct(row.average_forward_return_pct) },
              { key: 'confidence_bucket', label: 'Confidence', render: (row) => humanize(row.confidence_bucket) },
            ]}
          />
        </div>
      </SectionCard>

      <SectionCard title="Ranking Recommendations" subtitle="Manual review output only. Evidence Edge does not change paper or live execution behavior automatically.">
        <DataTable
          empty={loading ? 'Loading recommendations...' : 'No ranking recommendations are ready yet.'}
          rows={recommendations}
          columns={[
            { key: 'type', label: 'Type', render: (row) => humanize(row.type) },
            { key: 'target', label: 'Target', render: (row) => humanize(row.target) },
            { key: 'basis', label: 'Basis', render: (row) => humanize(row.basis) },
            { key: 'confidence_bucket', label: 'Confidence', render: (row) => humanize(row.confidence_bucket) },
            { key: 'detail', label: 'Detail' },
          ]}
        />
      </SectionCard>

      <SectionCard title="Feature Signals And Missing Data" subtitle="The layer reports missing data instead of inferring edge from incomplete evidence.">
        <div className="ui-dashboard-grid">
          <DataTable
            empty="No positive features yet."
            rows={positiveFeatures.slice(0, 8)}
            columns={[
              { key: 'feature', label: 'Positive feature', render: (row) => humanize(row.feature) },
              { key: 'observed_outcome_count', label: 'Observed' },
              { key: 'average_forward_return_pct', label: 'Avg return', render: (row) => formatPct(row.average_forward_return_pct) },
            ]}
          />
          <DataTable
            empty="No negative features yet."
            rows={negativeFeatures.slice(0, 8)}
            columns={[
              { key: 'feature', label: 'Negative feature', render: (row) => humanize(row.feature) },
              { key: 'observed_outcome_count', label: 'Observed' },
              { key: 'average_forward_return_pct', label: 'Avg return', render: (row) => formatPct(row.average_forward_return_pct) },
            ]}
          />
          <DataTable
            empty="No missing-data warnings."
            rows={missingRows.slice(0, 8)}
            columns={[
              { key: 'field', label: 'Missing field', render: (row) => humanize(row.field) },
              { key: 'count', label: 'Rows' },
            ]}
          />
        </div>
      </SectionCard>
    </div>
  )
}
