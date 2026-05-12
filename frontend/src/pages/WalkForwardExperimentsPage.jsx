import { useCallback, useEffect, useMemo, useState } from 'react'
import Button from '../components/Button'
import ErrorState from '../components/ErrorState'
import FinishTrackerSection from '../components/FinishTrackerSection'
import ListTable from '../components/ListTable'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import StatusBadge from '../components/StatusBadge'
import {
  cloneWalkForwardExperiment,
  createWalkForwardExperiment,
  freezeWalkForwardExperiment,
  getWalkForwardExperiments,
} from '../api/client'

const DEFAULT_WINDOWS = {
  train_window: { start: '2026-05-01', end: '2026-05-03' },
  validation_window: { start: '2026-05-04', end: '2026-05-04' },
  test_window: { start: '2026-05-05', end: '2026-05-05' },
  paper_forward_window: { start: '2026-05-06', end: '2026-05-10' },
}

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

function formatPercent(value) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '--'
  return `${(numeric * 100).toFixed(1)}%`
}

function formatRequirementValue(row, field = 'value') {
  const value = row?.[field]
  if (row?.metric === 'pass_rate') return formatPercent(value)
  return formatNumber(value)
}

function statusTone(status) {
  if (status === 'completed' || status === 'passed') return 'positive'
  if (status === 'ready_for_human_review') return 'positive'
  if (status === 'needs_evidence') return 'warning'
  if (status === 'blocked_by_evidence') return 'warning'
  if (status === 'draft' || status === 'frozen' || status === 'running' || status === 'weak_pass') return 'warning'
  if (status === 'no_records') return 'neutral'
  if (status === 'failed' || status === 'rejected' || status === 'data_quality_too_weak') return 'negative'
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
            <tr key={row.experiment_id || row.key || row.note || row.warning || index}>
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

function WindowText({ value }) {
  const start = value?.start || value?.from || '--'
  const end = value?.end || value?.to || '--'
  return <span>{start} to {end}</span>
}

export default function WalkForwardExperimentsPage() {
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [draftName, setDraftName] = useState('Walk-forward proof run')
  const [draftDescription, setDraftDescription] = useState('Freeze candidate, forecast, blocker, and ranking research parameters before forward evaluation.')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setReport(await getWalkForwardExperiments())
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load walk-forward experiments.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const runAction = useCallback(async (action) => {
    setSaving(true)
    setError('')
    try {
      await action()
      await load()
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Walk-forward action failed.')
    } finally {
      setSaving(false)
    }
  }, [load])

  const createDraft = () => runAction(() => createWalkForwardExperiment({
    name: draftName,
    description: draftDescription,
    ...DEFAULT_WINDOWS,
    strategy_config_version: 'strategy_config_v1',
    risk_config_version: 'risk_config_snapshot_v1',
    ranking_formula_version: 'ranked_entry_v1',
    reward_formula_version: 'evidence_reward_prediction_contract_v1',
    forecast_model_version: 'forecast_validation_contract_v1',
    baseline_definition_version: 'professional_benchmark_baselines_v1',
    feature_version: 'candidate_feature_snapshot_v1',
    market_universe: 'configured_trade_universe',
    data_source: 'local_evidence_artifacts',
  }))

  const summary = report?.summary || {}
  const proofSummary = report?.proof_summary || {}
  const proofRows = proofSummary?.requirements || []
  const validationPlan = report?.walk_forward_validation_plan || {}
  const validationSummary = validationPlan?.summary || {}
  const validationRows = validationPlan?.items || []
  const recordReadiness = proofSummary?.record_readiness || []
  const rows = report?.records || []
  const warnings = report?.warnings || []
  const safetyNotes = report?.safety_notes || []
  const latest = useMemo(() => rows.find((row) => row.experiment_id === summary.latest_experiment_id) || rows[rows.length - 1], [rows, summary.latest_experiment_id])

  return (
    <div className="ui-shell__page">
      <PageIntro
        kicker="Research metadata"
        title="Walk-Forward Experiments"
        description="Freeze research configurations and check whether signals, forecasts, blockers, and rankings still work after the rules are locked."
        badge="No trading authority"
      />
      {error ? <ErrorState description={error} onAction={load} /> : null}

      <div className="ui-action-row">
        <StatusBadge tone="neutral">Research only</StatusBadge>
        <StatusBadge tone="neutral">Metadata writes only</StatusBadge>
        <StatusBadge tone="neutral">Does not place orders</StatusBadge>
        <StatusBadge tone="neutral">Does not change ranking weights</StatusBadge>
        <StatusBadge tone="warning">No guaranteed returns</StatusBadge>
        <Button type="button" variant="ghost" size="sm" onClick={load} disabled={loading || saving}>
          Refresh
        </Button>
      </div>

      <SectionCard title="Registry Summary" subtitle="Experiments freeze research parameters; frozen records must be cloned before changing parameters.">
        <div className="ui-dashboard-grid">
          <MetricCard label="Experiments" value={summary.experiment_count ?? rows.length} helper={`${summary.draft_count ?? 0} drafts`} />
          <MetricCard label="Frozen or locked" value={summary.frozen_or_locked_count ?? 0} helper="Frozen/running/completed/rejected/needs-more-evidence" />
          <MetricCard label="Latest verdict" value={humanize(latest?.metrics?.verdict || 'insufficient_evidence')} helper="Mapped from Professional Benchmark Suite v1" />
          <MetricCard label="Latest baseline edge" value={formatNumber(latest?.metrics?.baseline_relative_edge)} helper="Research-only benchmark metric" />
          <MetricCard label="Score bucket lift" value={formatNumber(latest?.metrics?.score_bucket_lift)} helper="High-score buckets minus low-score buckets" />
          <MetricCard label="Execution adjusted" value={formatNumber(latest?.metrics?.execution_adjusted_reward)} helper="After slippage/spread evidence when present" />
        </div>
      </SectionCard>

      <SectionCard title="Walk-Forward Proof Gate" subtitle="Repeatability claims require frozen out-of-sample experiments with chronological windows, version snapshots, benchmark linkage, pass-rate evidence, and after-cost support.">
        <div className="ui-dashboard-grid">
          <MetricCard label="Proof status" value={humanize(proofSummary.status || summary.walk_forward_proof_status || 'needs_evidence')} helper={`${summary.walk_forward_requirements_passed ?? 0}/${summary.walk_forward_requirements_total ?? 6} requirements passed`} />
          <MetricCard label="Pass rate" value={formatPercent(proofSummary.summary?.pass_rate ?? summary.walk_forward_pass_rate)} helper={`Minimum ${formatPercent(proofSummary.summary?.minimum_pass_rate ?? 0.6)}`} />
          <MetricCard label="Frozen records" value={proofSummary.summary?.frozen_record_count ?? 0} helper="Frozen or locked snapshots" />
          <MetricCard label="After-cost support" value={proofSummary.summary?.after_cost_supported_record_count ?? 0} helper="Records with execution-adjusted reward" />
        </div>
        <DataTable
          rows={proofRows}
          empty={loading ? 'Loading walk-forward proof requirements...' : 'No walk-forward proof requirements are available.'}
          columns={[
            { key: 'label', label: 'Requirement' },
            { key: 'status', label: 'Status', render: (row) => <StatusBadge tone={statusTone(row.status)}>{humanize(row.status)}</StatusBadge> },
            { key: 'value', label: 'Value', render: (row) => formatRequirementValue(row) },
            { key: 'threshold', label: 'Threshold', render: (row) => `${row.comparison === 'greater_than' ? '>' : '>='} ${formatRequirementValue(row, 'threshold')}` },
            { key: 'safe_next_action', label: 'Safe next action' },
          ]}
        />
      </SectionCard>

      <SectionCard title="Walk-Forward Validation Plan" subtitle="Proof-first blockers for frozen out-of-sample validation. Repeatability and live-readiness claims stay blocked until evidence exists.">
        <div className="ui-dashboard-grid">
          <MetricCard label="Validation status" value={humanize(validationPlan.status || summary.walk_forward_validation_status || 'needs_evidence')} helper={`${validationSummary.open_item_count ?? summary.walk_forward_validation_open_items ?? 0} open validation items`} />
          <MetricCard label="Critical blockers" value={validationSummary.critical_open_items ?? summary.walk_forward_validation_critical_open_items ?? 0} helper={validationSummary.top_validation_item || summary.top_validation_item || 'No top validation item'} />
          <MetricCard label="Internal review" value={validationSummary.claim_permissions?.cautious_internal_repeatability_review || summary.claim_permissions?.cautious_internal_repeatability_review ? 'Allowed' : 'Blocked'} helper="Human research review only" />
          <MetricCard label="Blocked claims" value={(validationSummary.blocked_claims || []).length || 0} helper={(validationSummary.blocked_claims || []).slice(0, 3).map(humanize).join(', ') || 'None'} />
        </div>
        <DataTable
          rows={validationRows}
          empty={loading ? 'Loading walk-forward validation plan...' : 'No walk-forward validation plan is available.'}
          columns={[
            { key: 'title', label: 'Validation item' },
            { key: 'priority', label: 'Priority', render: (row) => humanize(row.priority) },
            { key: 'status', label: 'Status', render: (row) => <StatusBadge tone={statusTone(row.status)}>{humanize(row.status)}</StatusBadge> },
            { key: 'missing', label: 'Missing evidence', render: (row) => row.missing_fields?.length ? row.missing_fields.join(', ') : 'None' },
            { key: 'blocked_claims', label: 'Blocked claims', render: (row) => row.blocked_claims?.length ? row.blocked_claims.map(humanize).join(', ') : 'None' },
            { key: 'action', label: 'Safe next action', render: (row) => row.safe_next_action },
          ]}
        />
      </SectionCard>

      <SectionCard title="Create Draft" subtitle="Draft creation writes sanitized research metadata only. It does not change strategy, risk, broker, or ranking configuration.">
        <div className="ui-form-grid">
          <label className="ui-field">
            <span>Name</span>
            <input value={draftName} onChange={(event) => setDraftName(event.target.value)} />
          </label>
          <label className="ui-field">
            <span>Description</span>
            <input value={draftDescription} onChange={(event) => setDraftDescription(event.target.value)} />
          </label>
        </div>
        <div className="ui-action-row">
          <Button type="button" onClick={createDraft} disabled={saving || !draftName.trim()}>
            Create draft experiment
          </Button>
          <StatusBadge tone="neutral">Default windows can be refined by cloning future versions</StatusBadge>
        </div>
      </SectionCard>

      <SectionCard title="Experiment List" subtitle="Freeze locks parameters. Clone creates a new draft version for changes.">
        <DataTable
          rows={rows}
          empty={loading ? 'Loading experiments...' : 'No walk-forward experiments have been created yet.'}
          columns={[
            { key: 'name', label: 'Experiment' },
            { key: 'status', label: 'Status', render: (row) => <StatusBadge tone={statusTone(row.status)}>{humanize(row.status)}</StatusBadge> },
            { key: 'verdict', label: 'Verdict', render: (row) => <StatusBadge tone={statusTone(row.metrics?.verdict)}>{humanize(row.metrics?.verdict || 'insufficient_evidence')}</StatusBadge> },
            { key: 'sample_size', label: 'Sample', render: (row) => row.metrics?.sample_size ?? 0 },
            { key: 'rewardable_count', label: 'Rewardable', render: (row) => row.metrics?.rewardable_count ?? 0 },
            { key: 'baseline_relative_edge', label: 'Baseline edge', render: (row) => formatNumber(row.metrics?.baseline_relative_edge) },
            { key: 'score_bucket_lift', label: 'Bucket lift', render: (row) => formatNumber(row.metrics?.score_bucket_lift) },
            { key: 'forecast_accuracy', label: 'Forecast', render: (row) => formatNumber(row.metrics?.forecast_accuracy) },
            { key: 'execution_adjusted_reward', label: 'Execution adj.', render: (row) => formatNumber(row.metrics?.execution_adjusted_reward) },
            {
              key: 'actions',
              label: 'Actions',
              render: (row) => (
                <div className="ui-action-row">
                  <Button type="button" size="sm" variant="ghost" disabled={saving || row.status !== 'draft'} onClick={() => runAction(() => freezeWalkForwardExperiment(row.experiment_id))}>
                    Freeze
                  </Button>
                  <Button type="button" size="sm" variant="ghost" disabled={saving} onClick={() => runAction(() => cloneWalkForwardExperiment(row.experiment_id))}>
                    Clone
                  </Button>
                </div>
              ),
            },
          ]}
        />
      </SectionCard>

      <SectionCard title="Frozen Parameter Summary" subtitle="These snapshots are metadata only and do not mutate the live app settings.">
        <DataTable
          rows={rows.slice(0, 12)}
          empty={loading ? 'Loading frozen parameters...' : 'No frozen parameter snapshots found.'}
          columns={[
            { key: 'name', label: 'Experiment' },
            { key: 'train_window', label: 'Train', render: (row) => <WindowText value={row.train_window} /> },
            { key: 'validation_window', label: 'Validation', render: (row) => <WindowText value={row.validation_window} /> },
            { key: 'test_window', label: 'Test', render: (row) => <WindowText value={row.test_window} /> },
            { key: 'ranking_formula_version', label: 'Ranking formula' },
            { key: 'reward_formula_version', label: 'Reward formula' },
            { key: 'parameter_digest', label: 'Digest' },
          ]}
        />
      </SectionCard>

      <SectionCard title="Record Readiness" subtitle="Each experiment is checked for frozen status, chronological windows, version snapshots, benchmark linkage, after-cost support, and evaluated verdicts.">
        <DataTable
          rows={recordReadiness.slice(0, 12)}
          empty={loading ? 'Loading record readiness...' : 'No experiment readiness records found.'}
          columns={[
            { key: 'name', label: 'Experiment' },
            { key: 'status', label: 'Status', render: (row) => <StatusBadge tone={statusTone(row.status)}>{humanize(row.status)}</StatusBadge> },
            { key: 'verdict', label: 'Verdict', render: (row) => <StatusBadge tone={statusTone(row.verdict)}>{humanize(row.verdict)}</StatusBadge> },
            { key: 'frozen_snapshot', label: 'Frozen', render: (row) => row.frozen_snapshot ? 'Yes' : 'No' },
            { key: 'no_lookahead_windows', label: 'No lookahead', render: (row) => row.no_lookahead_windows ? 'Yes' : 'No' },
            { key: 'version_snapshot_complete', label: 'Versions', render: (row) => row.version_snapshot_complete ? 'Complete' : 'Missing' },
            { key: 'benchmark_linked', label: 'Benchmark', render: (row) => row.benchmark_linked ? 'Linked' : 'Missing' },
            { key: 'after_cost_supported', label: 'After cost', render: (row) => row.after_cost_supported ? 'Available' : 'Missing' },
          ]}
        />
      </SectionCard>

      <SectionCard title="Warnings And Safety Notes" subtitle="Experiment outputs are proof metadata only. They cannot trigger trading or config changes.">
        <div className="ui-dashboard-grid">
          <DataTable
            rows={warnings.map((warning, index) => ({ warning, index }))}
            empty="No registry warnings."
            columns={[{ key: 'warning', label: 'Warning' }]}
          />
          <DataTable
            rows={safetyNotes.map((note, index) => ({ note, index }))}
            empty="Safety notes unavailable."
            columns={[{ key: 'note', label: 'Boundary' }]}
          />
        </div>
      </SectionCard>

      <FinishTrackerSection tracker={report?.finish_tracker} loading={loading} />
    </div>
  )
}
