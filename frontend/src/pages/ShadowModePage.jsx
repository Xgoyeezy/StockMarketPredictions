import { useCallback, useEffect, useMemo, useState } from 'react'
import Button from '../components/Button'
import ErrorState from '../components/ErrorState'
import FinishTrackerSection from '../components/FinishTrackerSection'
import ListTable from '../components/ListTable'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import StatusBadge from '../components/StatusBadge'
import { createHumanShadowThesis, getShadowModeSummary } from '../api/client'

const INITIAL_FORM = {
  symbol: '',
  linked_candidate_id: '',
  human_direction: 'up',
  human_confidence: '0.60',
  human_target_pct: '0.50',
  human_invalidation_level: '',
  human_horizon_minutes: '60',
  human_reason: '',
  setup_type: '',
  engine: '',
  regime: '',
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

function formatRequirementValue(value, metric) {
  if (value === null || value === undefined) return '--'
  if (String(metric || '').includes('coverage')) return formatPercent(value)
  if (String(metric || '').includes('count')) return formatNumber(value, 0)
  return formatNumber(value)
}

function statusTone(status) {
  if (status === 'ready' || status === 'ready_for_human_review' || status === 'passed') return 'positive'
  if (status === 'empty') return 'neutral'
  return 'warning'
}

function winnerTone(winner) {
  if (winner === 'human') return 'warning'
  if (winner === 'system') return 'positive'
  if (winner === 'tie') return 'neutral'
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
            <tr key={`${row.human_thesis_id || row.linked_candidate_id || row.symbol || row.bias || row.field || row.note || 'row'}-${index}`}>
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

export default function ShadowModePage() {
  const [report, setReport] = useState(null)
  const [form, setForm] = useState(INITIAL_FORM)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setReport(await getShadowModeSummary())
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load Human vs System Shadow Mode.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  function updateField(field, value) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  async function submitThesis(event) {
    event.preventDefault()
    setSaving(true)
    setError('')
    try {
      await createHumanShadowThesis({
        ...form,
        symbol: form.symbol.trim().toUpperCase(),
        human_confidence: Number(form.human_confidence),
        human_target_pct: Number(form.human_target_pct),
        human_invalidation_level: form.human_invalidation_level === '' ? null : Number(form.human_invalidation_level),
        human_horizon_minutes: Number(form.human_horizon_minutes),
      })
      setForm(INITIAL_FORM)
      await load()
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Human thesis save failed.')
    } finally {
      setSaving(false)
    }
  }

  const summary = report?.summary || {}
  const aggregations = report?.aggregations || {}
  const rows = report?.records || []
  const proofSummary = report?.proof_summary || aggregations.shadow_proof || {}
  const proofRequirements = proofSummary.requirements || []
  const recordReadiness = proofSummary.record_readiness || []
  const validationPlan = report?.shadow_validation_plan || aggregations.shadow_validation_plan || {}
  const validationItems = validationPlan.items || []
  const validationSummary = validationPlan.summary || {}
  const claimPermissions = validationSummary.claim_permissions || summary.claim_permissions || {}
  const warnings = report?.warnings || []
  const safetyNotes = report?.safety_notes || []
  const biasItems = aggregations.bias_diagnostics?.items || []
  const biasCounts = aggregations.bias_diagnostics?.counts || {}
  const missingFieldRows = useMemo(() => missingRows(report?.missing_fields), [report?.missing_fields])
  const biasCountRows = useMemo(() => Object.entries(biasCounts).map(([bias, count]) => ({ bias, count })), [biasCounts])

  return (
    <div className="ui-shell__page">
      <PageIntro
        kicker="Research metadata"
        title="Human vs System Shadow Mode"
        description="Captures human forecast contracts and compares them against Quant Evidence OS on the same opportunity set. Shadow records never place trades or change execution."
        badge="No trading authority"
      />
      {error ? <ErrorState description={error} onAction={load} /> : null}

      <div className="ui-action-row">
        <StatusBadge tone={statusTone(report?.status)}>{humanize(report?.status || 'empty')}</StatusBadge>
        <StatusBadge tone="neutral">Research only</StatusBadge>
        <StatusBadge tone="neutral">Metadata writes only</StatusBadge>
        <StatusBadge tone="neutral">Does not place orders</StatusBadge>
        <StatusBadge tone="neutral">Does not change ranking weights</StatusBadge>
        <StatusBadge tone="warning">No guaranteed returns</StatusBadge>
        <Button type="button" variant="ghost" size="sm" onClick={load} disabled={loading || saving}>
          Refresh
        </Button>
      </div>

      <SectionCard title="Shadow Summary" subtitle="Compares rewardable human thesis contracts against rewardable system contracts when outcome data exists.">
        <div className="ui-dashboard-grid">
          <MetricCard label="Human theses" value={summary.record_count ?? 0} helper={`${summary.human_rewardable_count ?? 0} rewardable`} />
          <MetricCard label="Comparisons" value={summary.comparison_count ?? 0} helper={`${summary.system_rewardable_count ?? 0} system rewardable`} />
          <MetricCard label="Human direction" value={formatPercent(summary.human_direction_accuracy)} helper="Correct direction rate" />
          <MetricCard label="System direction" value={formatPercent(summary.system_direction_accuracy)} helper="Correct direction rate" />
          <MetricCard label="Human avg reward" value={formatNumber(summary.human_avg_reward)} helper={`${summary.human_win_count ?? 0} human wins`} />
          <MetricCard label="System avg reward" value={formatNumber(summary.system_avg_reward)} helper={`${summary.system_win_count ?? 0} system wins`} />
          <MetricCard label="Human vs system edge" value={formatNumber(summary.human_vs_system_edge)} helper="Human reward minus system reward" />
          <MetricCard label="Override quality" value={formatPercent(aggregations.override_quality?.human_override_win_rate)} helper={`${aggregations.override_quality?.override_count ?? 0} direction overrides`} />
        </div>
      </SectionCard>

      <SectionCard title="Shadow Mode Proof Gate" subtitle="Human-review checklist for same-opportunity linkage, forecast contracts, outcomes, cost/risk context, and after-cost decision quality.">
        <div className="ui-dashboard-grid">
          <MetricCard label="Proof status" value={humanize(proofSummary.status || summary.shadow_proof_status || 'needs_evidence')} helper={`${summary.shadow_requirements_passed ?? 0}/${summary.shadow_requirements_total ?? 10} requirements passed`} />
          <MetricCard label="Same-opportunity coverage" value={formatPercent(summary.same_opportunity_coverage ?? proofSummary.summary?.same_opportunity_coverage)} helper="Candidate, system prediction, and horizon linkage" />
          <MetricCard label="Human contract coverage" value={formatPercent(summary.human_contract_coverage ?? proofSummary.summary?.human_contract_coverage)} helper="Direction, confidence, target, invalidation, horizon, thesis" />
          <MetricCard label="System contract coverage" value={formatPercent(summary.system_contract_coverage ?? proofSummary.summary?.system_contract_coverage)} helper="System direction, confidence, target, invalidation, horizon" />
          <MetricCard label="Outcome coverage" value={formatPercent(summary.outcome_coverage ?? proofSummary.summary?.outcome_coverage)} helper="Forward return, baseline, target, invalidation" />
          <MetricCard label="Cost/risk coverage" value={formatPercent(summary.cost_risk_context_coverage ?? proofSummary.summary?.cost_risk_context_coverage)} helper="Spread, slippage, fill, gate, kill switch, exposure" />
          <MetricCard label="Pre-outcome capture" value={formatPercent(summary.pre_outcome_capture_coverage ?? proofSummary.summary?.pre_outcome_capture_coverage)} helper="Human thesis timestamp before outcome close" />
          <MetricCard label="System quality delta" value={formatNumber(summary.system_decision_quality_delta ?? proofSummary.summary?.system_decision_quality_delta)} helper="System minus human after costs and risk" />
        </div>
        <DataTable
          rows={proofRequirements}
          empty={loading ? 'Loading shadow proof requirements...' : 'No shadow proof requirements returned.'}
          columns={[
            { key: 'label', label: 'Requirement' },
            { key: 'status', label: 'Status', render: (row) => <StatusBadge tone={statusTone(row.status)}>{humanize(row.status)}</StatusBadge> },
            { key: 'value', label: 'Value', render: (row) => formatRequirementValue(row.value, row.metric) },
            { key: 'threshold', label: 'Threshold', render: (row) => `${row.comparison || '>='} ${formatRequirementValue(row.threshold, row.metric)}` },
            { key: 'safe_next_action', label: 'Safe next action' },
          ]}
        />
      </SectionCard>

      <SectionCard title="Human vs System Validation Plan" subtitle="Proof-first backlog items for fair same-opportunity comparison. These items are manual-review only and cannot trade, route, approve, or reweight anything.">
        <div className="ui-dashboard-grid">
          <MetricCard label="Validation status" value={humanize(validationPlan.status || summary.shadow_validation_status || 'blocked_by_evidence')} helper={`${summary.shadow_validation_open_items ?? validationSummary.open_item_count ?? 0} open items`} />
          <MetricCard label="Critical blockers" value={summary.shadow_validation_critical_open_items ?? validationSummary.critical_open_items ?? 0} helper={summary.top_validation_item || validationSummary.top_validation_item || 'No top blocker returned'} />
          <MetricCard label="Internal review" value={claimPermissions.cautious_internal_shadow_review ? 'Allowed' : 'Blocked'} helper="Requires complete same-opportunity evidence" />
          <MetricCard label="Live readiness" value={claimPermissions.live_trading_readiness ? 'Allowed' : 'Blocked'} helper="Shadow Mode never grants trading authority" />
        </div>
        <DataTable
          rows={validationItems}
          empty={loading ? 'Loading shadow validation plan...' : 'No shadow validation plan returned.'}
          columns={[
            { key: 'title', label: 'Validation item' },
            { key: 'priority', label: 'Priority', render: (row) => humanize(row.priority) },
            { key: 'status', label: 'Status', render: (row) => <StatusBadge tone={statusTone(row.status)}>{humanize(row.status)}</StatusBadge> },
            { key: 'missing_fields', label: 'Missing evidence', render: (row) => (row.missing_fields || []).slice(0, 4).map((field) => humanize(field)).join(', ') || 'None' },
            { key: 'blocked_claims', label: 'Blocked claims', render: (row) => (row.blocked_claims || []).slice(0, 3).map((claim) => humanize(claim)).join(', ') || 'None' },
            { key: 'safe_next_action', label: 'Safe next action' },
          ]}
        />
      </SectionCard>

      <SectionCard title="Capture Human Thesis" subtitle="A vague label is not rewardable. Direction, confidence, target, invalidation, and horizon are required before outcome scoring.">
        <form onSubmit={submitThesis}>
          <div className="ui-form-grid">
            <label className="ui-field">
              <span>Symbol</span>
              <input value={form.symbol} onChange={(event) => updateField('symbol', event.target.value)} placeholder="AAPL" />
            </label>
            <label className="ui-field">
              <span>Linked candidate ID</span>
              <input value={form.linked_candidate_id} onChange={(event) => updateField('linked_candidate_id', event.target.value)} placeholder="Optional candidate id" />
            </label>
            <label className="ui-field">
              <span>Direction</span>
              <select value={form.human_direction} onChange={(event) => updateField('human_direction', event.target.value)}>
                <option value="up">Up / long</option>
                <option value="down">Down / bearish</option>
                <option value="flat">Flat / range</option>
              </select>
            </label>
            <label className="ui-field">
              <span>Confidence</span>
              <input type="number" min="0" max="1" step="0.01" value={form.human_confidence} onChange={(event) => updateField('human_confidence', event.target.value)} />
            </label>
            <label className="ui-field">
              <span>Target %</span>
              <input type="number" step="0.01" value={form.human_target_pct} onChange={(event) => updateField('human_target_pct', event.target.value)} />
            </label>
            <label className="ui-field">
              <span>Invalidation level</span>
              <input type="number" step="0.01" value={form.human_invalidation_level} onChange={(event) => updateField('human_invalidation_level', event.target.value)} placeholder="Price or level" />
            </label>
            <label className="ui-field">
              <span>Horizon minutes</span>
              <input type="number" min="1" step="1" value={form.human_horizon_minutes} onChange={(event) => updateField('human_horizon_minutes', event.target.value)} />
            </label>
            <label className="ui-field">
              <span>Setup type</span>
              <input value={form.setup_type} onChange={(event) => updateField('setup_type', event.target.value)} placeholder="Optional" />
            </label>
            <label className="ui-field">
              <span>Engine</span>
              <input value={form.engine} onChange={(event) => updateField('engine', event.target.value)} placeholder="Optional" />
            </label>
            <label className="ui-field">
              <span>Regime</span>
              <input value={form.regime} onChange={(event) => updateField('regime', event.target.value)} placeholder="Optional" />
            </label>
          </div>
          <label className="ui-field">
            <span>Reason</span>
            <textarea value={form.human_reason} onChange={(event) => updateField('human_reason', event.target.value)} placeholder="Specific thesis, not just a visual label." rows={3} />
          </label>
          <div className="ui-action-row">
            <Button type="submit" disabled={saving || !form.symbol.trim()}>
              Save research thesis
            </Button>
            <StatusBadge tone="neutral">Does not create an order</StatusBadge>
          </div>
        </form>
      </SectionCard>

      <SectionCard title="Human vs System Comparisons" subtitle="Rows become complete only when outcome fields are available. Missing outcomes are shown honestly.">
        <DataTable
          rows={rows}
          empty={loading ? 'Loading shadow records...' : 'No human shadow records saved yet.'}
          columns={[
            { key: 'symbol', label: 'Symbol' },
            { key: 'human_direction', label: 'Human', render: (row) => humanize(row.human_direction) },
            { key: 'system_direction', label: 'System', render: (row) => humanize(row.system_direction) },
            { key: 'actual_forward_return', label: 'Actual', render: (row) => formatNumber(row.actual_forward_return) },
            { key: 'human_reward', label: 'Human reward', render: (row) => formatNumber(row.human_reward) },
            { key: 'system_reward', label: 'System reward', render: (row) => formatNumber(row.system_reward) },
            { key: 'winner', label: 'Winner', render: (row) => <StatusBadge tone={winnerTone(row.winner)}>{humanize(row.winner)}</StatusBadge> },
            { key: 'missing_fields', label: 'Missing', render: (row) => (row.missing_fields || []).slice(0, 4).join(', ') || 'None' },
          ]}
        />
      </SectionCard>

      <SectionCard title="Shadow Record Readiness" subtitle="Per-record readiness for same-opportunity proof only. Readiness does not place, route, approve, or configure trades.">
        <DataTable
          rows={recordReadiness}
          empty={loading ? 'Loading shadow record readiness...' : 'No shadow record readiness returned.'}
          columns={[
            { key: 'symbol', label: 'Symbol' },
            { key: 'same_opportunity_complete', label: 'Same opportunity', render: (row) => <StatusBadge tone={row.same_opportunity_complete ? 'positive' : 'warning'}>{row.same_opportunity_complete ? 'Ready' : 'Missing'}</StatusBadge> },
            { key: 'human_contract_complete', label: 'Human', render: (row) => <StatusBadge tone={row.human_contract_complete ? 'positive' : 'warning'}>{row.human_contract_complete ? 'Ready' : 'Missing'}</StatusBadge> },
            { key: 'system_contract_complete', label: 'System', render: (row) => <StatusBadge tone={row.system_contract_complete ? 'positive' : 'warning'}>{row.system_contract_complete ? 'Ready' : 'Missing'}</StatusBadge> },
            { key: 'outcome_contract_complete', label: 'Outcome', render: (row) => <StatusBadge tone={row.outcome_contract_complete ? 'positive' : 'warning'}>{row.outcome_contract_complete ? 'Ready' : 'Missing'}</StatusBadge> },
            { key: 'cost_risk_context_complete', label: 'Cost/risk', render: (row) => <StatusBadge tone={row.cost_risk_context_complete ? 'positive' : 'warning'}>{row.cost_risk_context_complete ? 'Ready' : 'Missing'}</StatusBadge> },
            { key: 'pre_outcome_capture_proven', label: 'Timing', render: (row) => <StatusBadge tone={row.pre_outcome_capture_proven ? 'positive' : 'warning'}>{row.pre_outcome_capture_proven ? 'Pre-outcome' : 'Missing'}</StatusBadge> },
          ]}
        />
      </SectionCard>

      <SectionCard title="Accuracy, Reward, And Missed Winner Review" subtitle="These metrics measure decision quality only. They cannot trigger execution or ranking changes.">
        <div className="ui-dashboard-grid">
          <MetricCard label="Human target hit" value={formatPercent(aggregations.human_target_hit_rate)} helper="Rewardable human records" />
          <MetricCard label="System target hit" value={formatPercent(aggregations.system_target_hit_rate)} helper="Rewardable system records" />
          <MetricCard label="Human invalidation hit" value={formatPercent(aggregations.human_invalidation_hit_rate)} helper="Lower is better" />
          <MetricCard label="System invalidation hit" value={formatPercent(aggregations.system_invalidation_hit_rate)} helper="Lower is better" />
          <MetricCard label="Human false positives" value={formatPercent(aggregations.human_false_positive_rate)} helper="Wrong direction rate" />
          <MetricCard label="System false positives" value={formatPercent(aggregations.system_false_positive_rate)} helper="Wrong direction rate" />
          <MetricCard label="Human caught winners" value={formatPercent(aggregations.missed_winner_comparison?.human_caught_rate)} helper={`${aggregations.missed_winner_comparison?.missed_winner_count ?? 0} winner windows`} />
          <MetricCard label="System caught winners" value={formatPercent(aggregations.missed_winner_comparison?.system_caught_rate)} helper="Same opportunity set" />
        </div>
      </SectionCard>

      <SectionCard title="Bias Diagnostics" subtitle="Bias flags are review prompts, not trading instructions.">
        <div className="ui-dashboard-grid">
          <DataTable
            rows={biasCountRows}
            empty={loading ? 'Loading bias counts...' : 'No bias diagnostics triggered.'}
            columns={[
              { key: 'bias', label: 'Bias', render: (row) => humanize(row.bias) },
              { key: 'count', label: 'Count' },
            ]}
          />
          <DataTable
            rows={biasItems.slice(0, 12)}
            empty="No individual bias flags."
            columns={[
              { key: 'symbol', label: 'Symbol' },
              { key: 'bias', label: 'Bias', render: (row) => humanize(row.bias) },
              { key: 'detail', label: 'Detail' },
            ]}
          />
        </div>
      </SectionCard>

      <SectionCard title="Warnings And Safety Notes" subtitle="Shadow Mode writes research metadata only. It does not mutate trading configuration.">
        <div className="ui-dashboard-grid">
          <DataTable
            rows={warnings.map((warning, index) => ({ warning, index }))}
            empty="No shadow-mode warnings."
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
          <DataTable
            rows={safetyNotes.map((note, index) => ({ note, index }))}
            empty="Safety notes unavailable."
            columns={[{ key: 'note', label: 'Safety note' }]}
          />
        </div>
      </SectionCard>

      <FinishTrackerSection tracker={report?.finish_tracker} loading={loading} />
    </div>
  )
}
