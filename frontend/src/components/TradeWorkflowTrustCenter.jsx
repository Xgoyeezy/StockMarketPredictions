import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  getControlChanges,
  getTradeIntents,
  getTradeScenarios,
  getTradeWorkflowOps,
  requestControlChange,
  saveTradeScenario,
  updateTradeDecisionReview,
} from '../api/client'
import { useToast } from '../context/ToastContext'
import ActionBar from './ActionBar'
import Button from './Button'
import EmptyState from './EmptyState'
import FeedbackState from './FeedbackState'
import { SelectField, TextAreaField, TextField, ToggleField } from './FormFields'
import MetricCard from './MetricCard'
import SectionCard from './SectionCard'
import StatusBadge from './StatusBadge'

const CONTROL_TYPES = [
  ['linked_account_change', 'Linked account'],
  ['withdrawal_or_payment_change', 'Payment route'],
  ['api_key_rotation', 'API key'],
  ['risk_limit_change', 'Risk limit'],
  ['automation_enablement', 'Automation'],
  ['live_mode_activation', 'Live mode'],
  ['billing_payment_change', 'Billing'],
  ['other', 'Other'],
]

const OUTCOME_OPTIONS = [
  ['current', 'Current'],
  ['win', 'Win'],
  ['loss', 'Loss'],
  ['approved', 'Approved'],
  ['rejected', 'Rejected'],
  ['blocked', 'Blocked'],
]

const EMPTY_CONTROL_FORM = {
  control_type: 'risk_limit_change',
  summary: '',
  applies_to: '',
  current_value: '',
  requested_value: '',
  rationale: '',
}

function asArray(value) {
  return Array.isArray(value) ? value : []
}

function toneForReadiness(value) {
  const normalized = String(value || '').toLowerCase()
  if (normalized === 'ready' || normalized === 'captured' || normalized === 'pass') return 'positive'
  if (normalized === 'blocked' || normalized === 'missing' || normalized === 'critical') return 'negative'
  if (normalized === 'warning' || normalized === 'high' || normalized === 'pending_review') return 'warning'
  return 'neutral'
}

function buildReviewDraft(intent) {
  const review = intent?.decision_review || {}
  return {
    standard_path: review.standard_path || 'Standard recommendation review path',
    requested_deviation: review.requested_deviation || 'No deviation requested',
    thesis_rationale: review.thesis_rationale || '',
    accepted_risk: Boolean(review.accepted_risk),
    accepted_risk_owner: review.accepted_risk_owner || '',
    accepted_risk_note: review.accepted_risk_note || '',
    challenge_raised: Boolean(review.challenge_raised),
    challenge_notes: review.challenge_notes || '',
    unresolved_conditions_text: asArray(review.unresolved_conditions).join('\n'),
  }
}

function buildScenarioDraft(intent) {
  const setup = intent?.request_payload?.thesis_direction || intent?.instrument_type || 'setup'
  return {
    name: `${intent?.ticker || 'Trade'} scenario`,
    outcome: String(intent?.status || 'current').replace('pending_approval', 'current'),
    market_regime: '',
    setup_label: setup,
    notes: '',
  }
}

function normalizeReviewPayload(draft, markReady = false) {
  return {
    standard_path: draft.standard_path,
    requested_deviation: draft.requested_deviation,
    thesis_rationale: draft.thesis_rationale,
    accepted_risk: Boolean(draft.accepted_risk),
    accepted_risk_owner: draft.accepted_risk_owner,
    accepted_risk_note: draft.accepted_risk_note,
    challenge_raised: Boolean(draft.challenge_raised),
    challenge_notes: draft.challenge_notes,
    unresolved_conditions: String(draft.unresolved_conditions_text || '')
      .split('\n')
      .map((item) => item.trim())
      .filter(Boolean),
    mark_decision_ready: markReady,
  }
}

function summarizeMissing(items, fallback = 'Ready') {
  const values = asArray(items).filter(Boolean)
  return values.length ? values.slice(0, 3).join(', ') : fallback
}

export default function TradeWorkflowTrustCenter() {
  const { pushToast } = useToast()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [ops, setOps] = useState({})
  const [intents, setIntents] = useState([])
  const [scenarios, setScenarios] = useState({ items: [], comparison_groups: [] })
  const [controls, setControls] = useState({ items: [] })
  const [reviewDrafts, setReviewDrafts] = useState({})
  const [scenarioDrafts, setScenarioDrafts] = useState({})
  const [controlForm, setControlForm] = useState(EMPTY_CONTROL_FORM)
  const [savingId, setSavingId] = useState('')
  const [creatingControl, setCreatingControl] = useState(false)

  const loadWorkflow = useCallback(async () => {
    try {
      setError('')
      const [opsPayload, scenarioPayload, controlsPayload, intentsPayload] = await Promise.all([
        getTradeWorkflowOps(),
        getTradeScenarios(),
        getControlChanges(),
        getTradeIntents({ status: 'all' }),
      ])
      const nextIntents = asArray(intentsPayload?.items)
      setOps(opsPayload || {})
      setScenarios(scenarioPayload || { items: [], comparison_groups: [] })
      setControls(controlsPayload || { items: [] })
      setIntents(nextIntents)
      setReviewDrafts((current) => {
        const next = { ...current }
        for (const intent of nextIntents.slice(0, 6)) {
          if (!next[intent.id]) next[intent.id] = buildReviewDraft(intent)
        }
        return next
      })
      setScenarioDrafts((current) => {
        const next = { ...current }
        for (const intent of nextIntents.slice(0, 6)) {
          if (!next[intent.id]) next[intent.id] = buildScenarioDraft(intent)
        }
        return next
      })
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Trade workflow data is unavailable.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadWorkflow()
  }, [loadWorkflow])

  const metrics = useMemo(() => ([
    {
      label: 'Decision Blockers',
      value: Number(ops.decision_not_ready_count || 0),
      tone: Number(ops.decision_not_ready_count || 0) > 0 ? 'warning' : 'positive',
      helper: `${Number(ops.weak_rationale_count || 0)} weak rationale`,
    },
    {
      label: 'Evidence Gaps',
      value: Number(ops.evidence_gap_count || 0),
      tone: Number(ops.evidence_gap_count || 0) > 0 ? 'warning' : 'positive',
      helper: `${Number(ops.strategy_release_blocker_count || 0)} release blockers`,
    },
    {
      label: 'Saved Scenarios',
      value: Number(ops.saved_scenario_count || 0),
      helper: `${Number(ops.contrast_ready_group_count || 0)} contrast-ready`,
    },
    {
      label: 'Control Reviews',
      value: Number(ops.pending_control_change_count || 0),
      tone: Number(ops.high_risk_control_change_count || 0) > 0 ? 'negative' : 'default',
      helper: `${Number(ops.high_risk_control_change_count || 0)} high risk`,
    },
  ]), [ops])

  function updateReviewDraft(intentId, patch) {
    setReviewDrafts((current) => ({
      ...current,
      [intentId]: { ...(current[intentId] || {}), ...patch },
    }))
  }

  function updateScenarioDraft(intentId, patch) {
    setScenarioDrafts((current) => ({
      ...current,
      [intentId]: { ...(current[intentId] || {}), ...patch },
    }))
  }

  async function handleSaveReview(intentId, markReady = false) {
    try {
      setSavingId(`${intentId}:review`)
      const draft = reviewDrafts[intentId] || {}
      await updateTradeDecisionReview(intentId, normalizeReviewPayload(draft, markReady))
      pushToast(markReady ? 'Decision review marked ready.' : 'Decision review saved.', 'success')
      await loadWorkflow()
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Decision review was not saved.', 'error')
    } finally {
      setSavingId('')
    }
  }

  async function handleSaveScenario(intentId) {
    try {
      setSavingId(`${intentId}:scenario`)
      await saveTradeScenario(intentId, scenarioDrafts[intentId] || buildScenarioDraft({}))
      pushToast('Trade scenario saved.', 'success')
      await loadWorkflow()
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Scenario was not saved.', 'error')
    } finally {
      setSavingId('')
    }
  }

  async function handleCreateControlChange(event) {
    event.preventDefault()
    if (!String(controlForm.summary || '').trim()) {
      pushToast('Add a control-change summary before creating the case.', 'error')
      return
    }
    try {
      setCreatingControl(true)
      await requestControlChange(controlForm)
      pushToast('Control change review case created.', 'success')
      setControlForm(EMPTY_CONTROL_FORM)
      await loadWorkflow()
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Control change case was not created.', 'error')
    } finally {
      setCreatingControl(false)
    }
  }

  if (loading) {
    return (
      <FeedbackState
        compact
        tone="info"
        eyebrow="Workflow"
        title="Loading trade workflow"
        description="Refreshing decision quality, evidence, scenario, and control review state."
        role="status"
      />
    )
  }

  const visibleIntents = intents.slice(0, 6)
  const comparisonGroups = asArray(scenarios.comparison_groups).slice(0, 4)
  const controlItems = asArray(controls.items).slice(0, 5)

  return (
    <SectionCard
      eyebrow="Decision quality"
      title="Trade Workflow Trust Center"
      subtitle="Decision review, evidence grounding, scenario comparison, and sensitive account-control review in one trade workspace."
      actions={(
        <ActionBar compact>
          <Button type="button" variant="ghost" size="sm" onClick={loadWorkflow}>Refresh</Button>
        </ActionBar>
      )}
    >
      {error ? (
        <FeedbackState
          compact
          tone="warning"
          eyebrow="Workflow"
          title="Workflow data needs attention"
          description={error}
          actions={[{ label: 'Retry', onAction: loadWorkflow, variant: 'ghost' }]}
          role="alert"
        />
      ) : null}

      <section className="metrics-grid trade-workflow-metrics">
        {metrics.map((item) => <MetricCard key={item.label} {...item} />)}
      </section>

      <div className="trade-workflow-grid">
        <div className="trade-workflow-stack">
          {visibleIntents.length ? visibleIntents.map((intent) => {
            const review = intent.decision_review || {}
            const readiness = review.readiness || {}
            const evidence = intent.evidence_register || {}
            const draft = reviewDrafts[intent.id] || buildReviewDraft(intent)
            const scenarioDraft = scenarioDrafts[intent.id] || buildScenarioDraft(intent)
            const isSavingReview = savingId === `${intent.id}:review`
            const isSavingScenario = savingId === `${intent.id}:scenario`

            return (
              <article className="trade-workflow-card" key={intent.id}>
                <div className="trade-workflow-card__header">
                  <div>
                    <div className="ui-panel__eyebrow">{intent.account_label || intent.execution_lane || 'Trade intent'}</div>
                    <h3>{intent.ticker || 'Trade'} decision case</h3>
                    <p>{summarizeMissing(readiness.blockers, 'No readiness blockers')}</p>
                  </div>
                  <div className="trade-workflow-card__badges">
                    <StatusBadge value={intent.status || 'pending'} />
                    <StatusBadge value={readiness.status || 'blocked'} tone={toneForReadiness(readiness.status)} />
                    <StatusBadge value={`${asArray(evidence.missing_items).length} evidence gaps`} tone={asArray(evidence.missing_items).length ? 'warning' : 'positive'} />
                  </div>
                </div>

                <div className="trade-workflow-detail-grid">
                  <div className="trade-workflow-panel">
                    <h4>Decision review</h4>
                    <div className="trade-workflow-form-grid">
                      <TextAreaField
                        label="Standard path"
                        value={draft.standard_path}
                        rows={3}
                        onChange={(event) => updateReviewDraft(intent.id, { standard_path: event.target.value })}
                      />
                      <TextAreaField
                        label="Requested deviation"
                        value={draft.requested_deviation}
                        rows={3}
                        onChange={(event) => updateReviewDraft(intent.id, { requested_deviation: event.target.value })}
                      />
                      <TextAreaField
                        label="Thesis rationale"
                        value={draft.thesis_rationale}
                        rows={4}
                        onChange={(event) => updateReviewDraft(intent.id, { thesis_rationale: event.target.value })}
                      />
                      <TextAreaField
                        label="Open conditions"
                        value={draft.unresolved_conditions_text}
                        rows={4}
                        onChange={(event) => updateReviewDraft(intent.id, { unresolved_conditions_text: event.target.value })}
                      />
                      <TextField
                        label="Accepted risk owner"
                        value={draft.accepted_risk_owner}
                        onChange={(event) => updateReviewDraft(intent.id, { accepted_risk_owner: event.target.value })}
                      />
                      <TextField
                        label="Accepted risk note"
                        value={draft.accepted_risk_note}
                        onChange={(event) => updateReviewDraft(intent.id, { accepted_risk_note: event.target.value })}
                      />
                    </div>
                    <div className="trade-workflow-toggle-row">
                      <ToggleField
                        label="Accepted risk"
                        checked={Boolean(draft.accepted_risk)}
                        onChange={(event) => updateReviewDraft(intent.id, { accepted_risk: event.target.checked })}
                      />
                      <ToggleField
                        label="Challenge raised"
                        checked={Boolean(draft.challenge_raised)}
                        onChange={(event) => updateReviewDraft(intent.id, { challenge_raised: event.target.checked })}
                      />
                    </div>
                    <TextAreaField
                      label="Challenge notes"
                      value={draft.challenge_notes}
                      rows={3}
                      onChange={(event) => updateReviewDraft(intent.id, { challenge_notes: event.target.value })}
                    />
                    <ActionBar compact>
                      <Button type="button" variant="ghost" size="sm" disabled={isSavingReview} onClick={() => handleSaveReview(intent.id, false)}>
                        {isSavingReview ? 'Saving' : 'Save review'}
                      </Button>
                      <Button type="button" variant="solid" size="sm" disabled={isSavingReview} onClick={() => handleSaveReview(intent.id, true)}>
                        Mark ready
                      </Button>
                    </ActionBar>
                  </div>

                  <div className="trade-workflow-panel">
                    <h4>Evidence register</h4>
                    <ul className="trade-workflow-list">
                      {asArray(evidence.items).slice(0, 6).map((item) => (
                        <li key={item.key || item.label}>
                          <span>
                            <strong>{item.label || item.key}</strong>
                            <small>{item.source || 'source'} | {item.detail || 'Captured for review.'}</small>
                          </span>
                          <StatusBadge value={item.status || 'unknown'} tone={toneForReadiness(item.status)} />
                        </li>
                      ))}
                    </ul>
                    <div className="trade-workflow-fingerprint">
                      <span>Evidence fingerprint</span>
                      <strong>{evidence.fingerprint ? String(evidence.fingerprint).slice(0, 18) : 'Not generated'}</strong>
                    </div>

                    <h4>Save scenario</h4>
                    <div className="trade-workflow-form-grid trade-workflow-form-grid--compact">
                      <TextField
                        label="Name"
                        value={scenarioDraft.name}
                        onChange={(event) => updateScenarioDraft(intent.id, { name: event.target.value })}
                      />
                      <SelectField
                        label="Outcome"
                        value={scenarioDraft.outcome}
                        onChange={(event) => updateScenarioDraft(intent.id, { outcome: event.target.value })}
                      >
                        {OUTCOME_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                      </SelectField>
                      <TextField
                        label="Market regime"
                        value={scenarioDraft.market_regime}
                        onChange={(event) => updateScenarioDraft(intent.id, { market_regime: event.target.value })}
                      />
                      <TextField
                        label="Setup label"
                        value={scenarioDraft.setup_label}
                        onChange={(event) => updateScenarioDraft(intent.id, { setup_label: event.target.value })}
                      />
                    </div>
                    <TextAreaField
                      label="Scenario notes"
                      value={scenarioDraft.notes}
                      rows={3}
                      onChange={(event) => updateScenarioDraft(intent.id, { notes: event.target.value })}
                    />
                    <ActionBar compact>
                      <Button type="button" variant="ghost" size="sm" disabled={isSavingScenario} onClick={() => handleSaveScenario(intent.id)}>
                        {isSavingScenario ? 'Saving' : 'Save scenario'}
                      </Button>
                    </ActionBar>
                  </div>
                </div>
              </article>
            )
          }) : (
            <EmptyState
              title="No trade intents"
              description="Decision review cases will appear here after a staged trade or linked-account recommendation is created."
            />
          )}
        </div>

        <aside className="trade-workflow-side">
          <div className="trade-workflow-panel trade-workflow-panel--side">
            <h4>Scenario comparison</h4>
            {comparisonGroups.length ? (
              <div className="trade-workflow-comparison-list">
                {comparisonGroups.map((group) => (
                  <div className="trade-workflow-comparison" key={group.key}>
                    <div className="trade-workflow-comparison__header">
                      <strong>{group.label}</strong>
                      <StatusBadge value={group.contrast_ready ? 'contrast ready' : 'same state'} tone={group.contrast_ready ? 'positive' : 'neutral'} />
                    </div>
                    {asArray(group.items).slice(0, 3).map((item) => (
                      <div className="trade-workflow-scenario-row" key={item.id}>
                        <span>{item.name || item.ticker}</span>
                        <StatusBadge value={item.decision_state || 'current'} />
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState
                title="No saved scenarios"
                description="Save scenarios from active cases to compare wins, losses, approvals, rejects, and regime changes."
              />
            )}
          </div>

          <div className="trade-workflow-panel trade-workflow-panel--side">
            <h4>Control changes</h4>
            <form className="trade-workflow-control-form" onSubmit={handleCreateControlChange}>
              <SelectField
                label="Control type"
                value={controlForm.control_type}
                onChange={(event) => setControlForm((state) => ({ ...state, control_type: event.target.value }))}
              >
                {CONTROL_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </SelectField>
              <TextField
                label="Summary"
                required
                value={controlForm.summary}
                onChange={(event) => setControlForm((state) => ({ ...state, summary: event.target.value }))}
              />
              <TextField
                label="Applies to"
                value={controlForm.applies_to}
                onChange={(event) => setControlForm((state) => ({ ...state, applies_to: event.target.value }))}
              />
              <TextField
                label="Current"
                value={controlForm.current_value}
                onChange={(event) => setControlForm((state) => ({ ...state, current_value: event.target.value }))}
              />
              <TextField
                label="Requested"
                value={controlForm.requested_value}
                onChange={(event) => setControlForm((state) => ({ ...state, requested_value: event.target.value }))}
              />
              <TextAreaField
                label="Rationale"
                value={controlForm.rationale}
                rows={3}
                onChange={(event) => setControlForm((state) => ({ ...state, rationale: event.target.value }))}
              />
              <Button type="submit" variant="solid" size="sm" disabled={creatingControl}>
                {creatingControl ? 'Creating' : 'Create review case'}
              </Button>
            </form>
            {controlItems.length ? (
              <ul className="trade-workflow-list trade-workflow-list--controls">
                {controlItems.map((item) => (
                  <li key={item.id}>
                    <span>
                      <strong>{item.summary}</strong>
                      <small>{item.control_type} | {item.applies_to || 'General'}</small>
                    </span>
                    <StatusBadge value={item.risk_band || item.status} tone={toneForReadiness(item.risk_band || item.status)} />
                  </li>
                ))}
              </ul>
            ) : null}
          </div>

          <div className="trade-workflow-panel trade-workflow-panel--side">
            <h4>Recent workflow events</h4>
            <ul className="trade-workflow-list">
              {asArray(ops.recent_audit_events).slice(0, 6).map((item, index) => (
                <li key={`${item.event_type}-${index}`}>
                  <span>
                    <strong>{item.event_type}</strong>
                    <small>{item.actor_email || 'system'} | {item.created_at || 'recent'}</small>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </aside>
      </div>
    </SectionCard>
  )
}
