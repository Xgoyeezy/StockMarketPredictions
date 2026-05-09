import { useCallback, useEffect, useMemo, useState } from 'react'
import ErrorState from '../components/ErrorState'
import ListTable from '../components/ListTable'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import StatusBadge from '../components/StatusBadge'
import Button from '../components/Button'
import { getOrganizationTradeAutomationEvidenceRewardSummary } from '../api/client'

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
            <tr key={row.candidate_lifecycle_id || row.blocker || row.setup_type || row.engine || row.regime || row.ai_verdict || index}>
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

function DistributionBars({ distribution = {} }) {
  const rows = ['strong_positive', 'positive', 'neutral', 'negative', 'strong_negative'].map((key) => ({
    key,
    label: humanize(key),
    value: Number(distribution[key] || 0),
  }))
  const maxValue = Math.max(1, ...rows.map((row) => row.value))
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      {rows.map((row) => (
        <div key={row.key} style={{ display: 'grid', gridTemplateColumns: 'minmax(140px, 1fr) auto', gap: 10, alignItems: 'center' }}>
          <span>{row.label}</span>
          <span>{row.value}</span>
          <div aria-hidden="true" style={{ gridColumn: '1 / -1', height: 8, borderRadius: 8, overflow: 'hidden', background: 'rgba(255,255,255,0.08)' }}>
            <span style={{ display: 'block', width: `${Math.max(4, (row.value / maxValue) * 100)}%`, height: '100%', borderRadius: 8, background: 'linear-gradient(90deg, #10b981, #d6a84f)' }} />
          </div>
        </div>
      ))}
    </div>
  )
}

export default function EvidenceRewardPage() {
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setReport(await getOrganizationTradeAutomationEvidenceRewardSummary())
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load Evidence Reward analytics.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const summary = report?.summary || {}
  const contractSummary = summary.prediction_contract_summary || {}
  const safetyNotes = report?.safety_notes || []
  const warnings = report?.warnings || []
  const recommendations = report?.safe_recommendations || []
  const missedMoveRows = report?.aggregations?.missed_move_report?.penalized_rows || []
  const rewardablePredictions = report?.rewardable_predictions || []
  const incompleteEvidence = report?.incomplete_evidence || []
  const blockers = report?.blocker_rewards || []
  const setups = report?.setup_rewards || []
  const engines = report?.engine_rewards || []
  const regimes = report?.regime_rewards || []
  const aiRewards = report?.ai_rewards || {}
  const missingPredictionRows = useMemo(() => (
    Object.entries(contractSummary.missing_prediction_fields || {})
      .sort((left, right) => Number(right[1]) - Number(left[1]))
      .map(([field, count]) => ({ field, count }))
  ), [contractSummary.missing_prediction_fields])
  const bestPredictions = [...rewardablePredictions].sort((left, right) => Number(right.total_reward ?? -9999) - Number(left.total_reward ?? -9999)).slice(0, 8)
  const worstPredictions = [...rewardablePredictions].sort((left, right) => Number(left.total_reward ?? 9999) - Number(right.total_reward ?? 9999)).slice(0, 8)

  return (
    <div className="ui-shell__page">
      <PageIntro
        kicker="Research only"
        title="Evidence Reward"
        description="Only timestamped, specific predictions are rewarded. Visual labels alone do not count."
        badge="Does not affect trading"
      />
      {error ? <ErrorState description={error} onAction={load} /> : null}
      <div className="ui-action-row">
        <StatusBadge tone="neutral">Research only</StatusBadge>
        <StatusBadge tone="warning">No guaranteed returns</StatusBadge>
        <StatusBadge tone="neutral">Rewards do not affect execution</StatusBadge>
        <StatusBadge tone="neutral">No autonomous live orders</StatusBadge>
        <Button type="button" variant="ghost" size="sm" onClick={load} disabled={loading}>
          Refresh
        </Button>
      </div>

      <div className="ui-dashboard-grid">
        <MetricCard label="Average reward" value={formatNumber(summary.avg_reward)} helper={`${summary.rewardable_candidate_count ?? 0} rewardable predictions`} />
        <MetricCard label="Prediction contracts" value={`${contractSummary.rewardable ?? 0} / ${summary.candidate_count ?? 0}`} helper="Rewardable / total evidence rows" />
        <MetricCard label="Incomplete evidence" value={summary.non_rewardable_count ?? summary.incomplete_prediction_count ?? 0} helper="Visible but not rewarded" />
        <MetricCard label="Baseline missing" value={summary.baseline_missing_count ?? 0} helper={summary.next_action || 'Waiting for baseline outcome evidence'} />
      </div>

      <SectionCard title="Safety Boundary" subtitle="These analytics are manual research output only.">
        <div className="ui-dashboard-grid">
          <DataTable
            empty="Safety notes unavailable."
            rows={safetyNotes.map((note, index) => ({ note, index }))}
            columns={[
              { key: 'note', label: 'Boundary' },
            ]}
          />
          <DataTable
            empty="No missing-data warnings."
            rows={warnings.map((warning, index) => ({ warning, index }))}
            columns={[
              { key: 'warning', label: 'Warning' },
            ]}
          />
        </div>
      </SectionCard>

      <SectionCard title="Rewardable Predictions" subtitle="These rows had direction, horizon, target, invalidation, confidence, actual return, and baseline return before reward was calculated.">
        <DataTable
          empty={loading ? 'Loading rewardable predictions...' : 'No rewardable prediction contracts yet.'}
          rows={bestPredictions}
          columns={[
            { key: 'symbol', label: 'Symbol' },
            { key: 'setup_type', label: 'Setup', render: (row) => humanize(row.setup_type) },
            { key: 'predicted_direction', label: 'Direction', render: (row) => humanize(row.prediction_contract?.predicted_direction) },
            { key: 'predicted_target_pct', label: 'Target', render: (row) => `${formatNumber(row.prediction_contract?.predicted_target_pct)}%` },
            { key: 'actual_forward_return', label: 'Actual', render: (row) => `${formatNumber(row.prediction_contract?.actual_forward_return)}%` },
            { key: 'total_reward', label: 'Reward', render: (row) => formatNumber(row.total_reward) },
          ]}
        />
      </SectionCard>

      <SectionCard title="Worst Rewarded Predictions" subtitle="Low reward can come from wrong direction, invalidation hits, late timing, drawdown, or baseline underperformance.">
        <DataTable
          empty={loading ? 'Loading predictions...' : 'No negative reward rows yet.'}
          rows={worstPredictions}
          columns={[
            { key: 'symbol', label: 'Symbol' },
            { key: 'setup_type', label: 'Setup', render: (row) => humanize(row.setup_type) },
            { key: 'confidence', label: 'Confidence', render: (row) => formatRatio(row.prediction_contract?.confidence) },
            { key: 'hit_invalidation', label: 'Invalidated', render: (row) => row.prediction_contract?.hit_invalidation ? 'Yes' : 'No' },
            { key: 'confidence_error', label: 'Confidence error', render: (row) => formatNumber(row.prediction_contract?.confidence_error) },
            { key: 'total_reward', label: 'Reward', render: (row) => formatNumber(row.total_reward) },
          ]}
        />
      </SectionCard>

      <SectionCard title="Reward Distribution" subtitle="Only rewardable prediction contracts are counted here.">
        <DistributionBars distribution={summary.reward_distribution || {}} />
      </SectionCard>

      <SectionCard title="Setup, Engine, Regime, And AI Quality" subtitle="Aggregations exclude incomplete contracts so vague labels cannot inflate the reward score.">
        <div className="ui-dashboard-grid">
          <DataTable
            empty={loading ? 'Loading setup rewards...' : 'No setup reward data yet.'}
            rows={(setups || []).slice(0, 8)}
            columns={[
              { key: 'setup_type', label: 'Setup', render: (row) => humanize(row.setup_type) },
              { key: 'rewardable_candidate_count', label: 'Rewardable' },
              { key: 'avg_reward', label: 'Avg reward', render: (row) => formatNumber(row.avg_reward) },
              { key: 'consistency_score', label: 'Consistency', render: (row) => formatNumber(row.consistency_score) },
            ]}
          />
          <DataTable
            empty={loading ? 'Loading engine rewards...' : 'No engine reward data yet.'}
            rows={(engines || []).slice(0, 8)}
            columns={[
              { key: 'engine', label: 'Engine', render: (row) => humanize(row.engine) },
              { key: 'rewardable_candidate_count', label: 'Rewardable' },
              { key: 'avg_reward', label: 'Avg reward', render: (row) => formatNumber(row.avg_reward) },
              { key: 'win_rate', label: 'Win rate', render: (row) => formatRatio(row.win_rate) },
            ]}
          />
          <DataTable
            empty={loading ? 'Loading regime rewards...' : 'No regime reward data yet.'}
            rows={(regimes || []).slice(0, 8)}
            columns={[
              { key: 'regime', label: 'Regime', render: (row) => humanize(row.regime) },
              { key: 'rewardable_candidate_count', label: 'Rewardable' },
              { key: 'avg_reward', label: 'Avg reward', render: (row) => formatNumber(row.avg_reward) },
              { key: 'dispersion', label: 'Dispersion', render: (row) => formatNumber(row.dispersion) },
            ]}
          />
          <DataTable
            empty={loading ? 'Loading AI rewards...' : 'No AI reward data yet.'}
            rows={(aiRewards.items || []).slice(0, 8)}
            columns={[
              { key: 'ai_verdict', label: 'AI verdict', render: (row) => humanize(row.ai_verdict) },
              { key: 'rewardable_candidate_count', label: 'Rewardable' },
              { key: 'avg_reward', label: 'Avg reward', render: (row) => formatNumber(row.avg_reward) },
              { key: 'win_rate', label: 'Win rate', render: (row) => formatRatio(row.win_rate) },
            ]}
          />
        </div>
      </SectionCard>

      <SectionCard title="Blockers And Incomplete Evidence" subtitle="Rows without full prediction contracts stay visible so the system can improve evidence quality.">
        <div className="ui-dashboard-grid">
          <DataTable
            empty={loading ? 'Loading blockers...' : 'No blocker reward data yet.'}
            rows={(blockers || []).slice(0, 8)}
            columns={[
              { key: 'blocker', label: 'Blocker', render: (row) => humanize(row.blocker) },
              { key: 'times_seen', label: 'Seen' },
              { key: 'rewardable_candidate_count', label: 'Rewardable' },
              { key: 'blocker_value_score', label: 'Value score', render: (row) => formatNumber(row.blocker_value_score) },
            ]}
          />
          <DataTable
            empty={loading ? 'Loading incomplete evidence...' : 'No incomplete evidence rows.'}
            rows={(incompleteEvidence || []).slice(0, 8)}
            columns={[
              { key: 'symbol', label: 'Symbol' },
              { key: 'setup_type', label: 'Setup', render: (row) => humanize(row.setup_type) },
              { key: 'prediction_contract_status', label: 'Status', render: (row) => humanize(row.prediction_contract_status) },
              { key: 'not_rewarded_reason', label: 'Reason', render: (row) => humanize(row.not_rewarded_reason) },
            ]}
          />
          <DataTable
            empty="No missing prediction fields."
            rows={missingPredictionRows.slice(0, 10)}
            columns={[
              { key: 'field', label: 'Missing prediction field', render: (row) => humanize(row.field) },
              { key: 'count', label: 'Rows' },
            ]}
          />
        </div>
      </SectionCard>

      <SectionCard title="Manual Ranking Review" subtitle="Recommendations are research-only and never change ranking weights automatically.">
        <div className="ui-dashboard-grid">
          <DataTable
            empty={loading ? 'Loading recommendations...' : 'No recommendations beyond collecting more rewardable contracts.'}
            rows={recommendations.slice(0, 8)}
            columns={[
              { key: 'type', label: 'Review type', render: (row) => humanize(row.type) },
              { key: 'target', label: 'Target', render: (row) => humanize(row.target) },
              { key: 'reason', label: 'Reason' },
            ]}
          />
          <DataTable
            empty={loading ? 'Loading missed-move penalties...' : 'No missed-move penalties in rewardable rows.'}
            rows={missedMoveRows.slice(0, 8)}
            columns={[
              { key: 'symbol', label: 'Symbol' },
              { key: 'setup_type', label: 'Setup', render: (row) => humanize(row.setup_type) },
              { key: 'missed_move_penalty', label: 'Missed penalty', render: (row) => formatNumber(row.reward_components?.missed_move_penalty) },
              { key: 'total_reward', label: 'Reward', render: (row) => formatNumber(row.total_reward) },
            ]}
          />
        </div>
      </SectionCard>
    </div>
  )
}
