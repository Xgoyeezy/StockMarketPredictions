import { useEffect, useMemo, useState } from 'react'
import {
  createAiLiveIntent,
  executeAiPaperExecution,
  getAiDeskManagerSnapshot,
  previewAiTradePlan,
  runAiAutonomousCycle,
  runAiDeskControl,
} from '../api/client'
import { useToast } from '../context/ToastContext'
import Button from './Button'
import Chip from './Chip'
import SectionCard from './SectionCard'
import { TextField } from './FormFields'

function humanize(value, fallback = '--') {
  const normalized = String(value || '').trim()
  if (!normalized) return fallback
  return normalized
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function toneForState(value) {
  const normalized = String(value || '').trim().toLowerCase()
  if (normalized === 'ready') return 'positive'
  if (normalized === 'blocked') return 'negative'
  if (normalized === 'stale') return 'warning'
  return 'neutral'
}

function firstTargetSymbol(snapshot) {
  const targets = snapshot?.trade_planner?.latest_targets?.targets
  return Array.isArray(targets) && targets.length ? String(targets[0]?.symbol || '').trim().toUpperCase() : ''
}

export default function AiDeskManagerPanel({ selectedDeskKey = '', onChanged }) {
  const { pushToast } = useToast()
  const [snapshot, setSnapshot] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busyKey, setBusyKey] = useState('')
  const [ticker, setTicker] = useState('')
  const [planPreview, setPlanPreview] = useState(null)
  const [liveIntent, setLiveIntent] = useState(null)
  const [cycleResult, setCycleResult] = useState(null)

  const command = snapshot?.command_center || {}
  const liveGate = snapshot?.live_gate || {}
  const paper = snapshot?.paper_execution || {}
  const policy = snapshot?.policy?.manifest || {}
  const autonomy = snapshot?.autonomy || {}
  const agents = Array.isArray(snapshot?.agents) ? snapshot.agents : []
  const activeBlockers = Array.isArray(snapshot?.active_blockers) ? snapshot.active_blockers : []
  const latestTargets = snapshot?.trade_planner?.latest_targets || {}
  const targetSymbol = useMemo(() => ticker.trim().toUpperCase() || firstTargetSymbol(snapshot), [snapshot, ticker])
  const selectedDeskState = useMemo(
    () => (snapshot?.desk_states || []).find((item) => item.desk_key === selectedDeskKey) || null,
    [snapshot, selectedDeskKey],
  )

  async function loadSnapshot() {
    setLoading(true)
    try {
      const payload = await getAiDeskManagerSnapshot()
      setSnapshot(payload)
      if (!ticker && firstTargetSymbol(payload)) {
        setTicker(firstTargetSymbol(payload))
      }
    } catch (error) {
      pushToast(error?.message || 'AI desk manager snapshot could not be loaded.', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadSnapshot()
  }, [])

  async function runPlanPreview() {
    setBusyKey('plan')
    try {
      const payload = await previewAiTradePlan({
        ticker: targetSymbol,
        target_symbol: targetSymbol,
        desk_key: selectedDeskKey || undefined,
      })
      setPlanPreview(payload)
      pushToast(payload.blocked ? 'AI trade plan is blocked.' : 'AI trade plan preview is ready.', payload.blocked ? 'warning' : 'success')
    } catch (error) {
      pushToast(error?.response?.data?.detail || error?.message || 'AI trade plan preview failed.', 'error')
    } finally {
      setBusyKey('')
    }
  }

  async function runPaperExecution() {
    setBusyKey('paper')
    try {
      await executeAiPaperExecution({
        portfolio_target_run_id: latestTargets.latest_run_id,
        dry_run: false,
      })
      await loadSnapshot()
      if (onChanged) await onChanged()
      pushToast('AI paper execution submitted.', 'success')
    } catch (error) {
      const blockers = error?.response?.data?.details?.blockers
      pushToast(blockers?.length ? blockers[0] : error?.response?.data?.detail || error?.message || 'AI paper execution is blocked.', 'error')
    } finally {
      setBusyKey('')
    }
  }

  async function runLiveIntent() {
    setBusyKey('live')
    try {
      const payload = await createAiLiveIntent({
        ticker: targetSymbol,
        target_symbol: targetSymbol,
        desk_key: selectedDeskKey || undefined,
        linked_account_id: liveGate.default_linked_account_id || undefined,
        frontend_confirmation: true,
      })
      setLiveIntent(payload)
      await loadSnapshot()
      if (onChanged) await onChanged()
      pushToast('Supervised live intent created for review.', 'success')
    } catch (error) {
      const blockers = error?.response?.data?.details?.blockers
      pushToast(blockers?.length ? blockers[0] : error?.response?.data?.detail || error?.message || 'Supervised live intent is blocked.', 'error')
    } finally {
      setBusyKey('')
    }
  }

  async function runControl(action, reason = '') {
    setBusyKey(action)
    try {
      const payload = await runAiDeskControl({ action, reason })
      if (payload?.status === 'blocked' || payload?.queued === false) {
        const blockers = payload?.blockers || payload?.decision?.blockers || []
        pushToast(blockers[0] || 'AI desk control action is blocked.', 'warning')
      } else if (payload?.queued) {
        pushToast('Autonomous AI cycle queued.', 'success')
      } else {
        pushToast('AI desk control updated.', 'success')
      }
      await loadSnapshot()
      if (onChanged) await onChanged()
    } catch (error) {
      pushToast(error?.response?.data?.detail || error?.message || 'AI desk control action failed.', 'error')
    } finally {
      setBusyKey('')
    }
  }

  async function runCycle({ enqueue = false } = {}) {
    setBusyKey(enqueue ? 'queue-cycle' : 'run-cycle')
    try {
      const payload = await runAiAutonomousCycle({ trigger: 'manual', enqueue, dry_run: false })
      setCycleResult(payload)
      if (payload.status === 'blocked' || payload.queued === false) {
        pushToast(payload.blockers?.[0] || payload.decision?.blockers?.[0] || 'Autonomous cycle is blocked.', 'warning')
      } else {
        pushToast(enqueue ? 'Autonomous cycle queued.' : 'Autonomous cycle completed.', 'success')
      }
      await loadSnapshot()
      if (onChanged) await onChanged()
    } catch (error) {
      pushToast(error?.response?.data?.detail || error?.message || 'Autonomous cycle failed.', 'error')
    } finally {
      setBusyKey('')
    }
  }

  if (loading) {
    return (
      <SectionCard title="AI desk manager" subtitle="Loading command center state.">
        <div className="ui-panel ui-panel--section">Reading desks, targets, paper execution, risk, and live gates.</div>
      </SectionCard>
    )
  }

  return (
    <SectionCard
      title="AI desk manager"
      subtitle="Rules-first coordinator for command center review, trade planning, paper execution, and supervised live intents."
      actions={(
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
          <Button type="button" variant="ghost" size="sm" onClick={loadSnapshot} disabled={Boolean(busyKey)}>
            Refresh AI
          </Button>
          <Button type="button" variant="solid" size="sm" onClick={runPlanPreview} disabled={Boolean(busyKey) || !targetSymbol}>
            {busyKey === 'plan' ? 'Previewing...' : 'Preview plan'}
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={runPaperExecution} disabled={Boolean(busyKey) || !paper.can_execute}>
            {busyKey === 'paper' ? 'Executing...' : 'Execute paper'}
          </Button>
          <Button type="button" variant="subtle" size="sm" onClick={runLiveIntent} disabled={Boolean(busyKey) || !liveGate.allowed || !targetSymbol}>
            {busyKey === 'live' ? 'Creating...' : 'Create live intent'}
          </Button>
          <Button type="button" variant="solid" size="sm" onClick={() => runCycle()} disabled={Boolean(busyKey) || !autonomy.enabled || !autonomy.armed || autonomy.kill_switch}>
            {busyKey === 'run-cycle' ? 'Running...' : 'Run cycle'}
          </Button>
        </div>
      )}
    >
      <div className="ui-stack-md">
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(260px, 0.9fr) minmax(0, 1.1fr)', gap: '0.75rem' }}>
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Autonomy policy</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
              <strong>{autonomy.enabled ? 'Enabled' : 'Disabled'}</strong>
              <Chip tone={autonomy.armed ? 'positive' : 'neutral'} size="sm">{autonomy.armed ? 'armed' : 'unarmed'}</Chip>
              <Chip tone={autonomy.kill_switch ? 'negative' : 'positive'} size="sm">{autonomy.kill_switch ? 'kill switch' : 'clear'}</Chip>
            </div>
            <div>Boundary {humanize(policy.autonomy_boundary || autonomy.boundary || 'paper_plus_live_intent')}</div>
            <div>Digest {snapshot?.policy_digest || snapshot?.policy?.policy_digest || '--'}</div>
            <div>Allowed desks {(policy.allowed_desks || []).join(', ') || '--'}</div>
            <div>Risk cap {policy.max_risk_percent ?? 0.5}% | Live submit blocked</div>
          </div>
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Operator control</div>
            <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
              <Button type="button" variant="ghost" size="sm" onClick={() => runControl(autonomy.enabled ? 'disable' : 'enable')} disabled={Boolean(busyKey)}>
                {autonomy.enabled ? 'Disable' : 'Enable'}
              </Button>
              <Button type="button" variant="ghost" size="sm" onClick={() => runControl(autonomy.armed ? 'disarm' : 'arm')} disabled={Boolean(busyKey) || autonomy.kill_switch}>
                {autonomy.armed ? 'Disarm' : 'Arm'}
              </Button>
              <Button type="button" variant="subtle" size="sm" onClick={() => runControl(autonomy.kill_switch ? 'clear_kill_switch' : 'kill_switch')} disabled={Boolean(busyKey)}>
                {autonomy.kill_switch ? 'Clear kill switch' : 'Kill switch'}
              </Button>
              <Button type="button" variant="ghost" size="sm" onClick={() => runCycle({ enqueue: true })} disabled={Boolean(busyKey) || !autonomy.enabled || !autonomy.armed || autonomy.kill_switch}>
                {busyKey === 'queue-cycle' ? 'Queueing...' : 'Queue next cycle'}
              </Button>
            </div>
            <div style={{ marginTop: '0.5rem' }}>
              Pending cycles {autonomy.pending_cycle_count ?? 0} | Next {snapshot?.next_scheduled_run || '--'}
            </div>
          </div>
        </div>

        {activeBlockers.length ? (
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Active autonomous blockers</div>
            {activeBlockers.slice(0, 6).map((blocker) => (
              <div key={blocker}>{blocker}</div>
            ))}
          </div>
        ) : null}

        {agents.length ? (
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">AI job roster</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: '0.5rem' }}>
              {agents.map((agent) => (
                <div key={agent.key} className="ui-panel ui-panel--section">
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.5rem', flexWrap: 'wrap' }}>
                    <strong>{agent.label}</strong>
                    <Chip tone={toneForState(agent.status === 'completed' ? 'ready' : agent.status)} size="sm">{humanize(agent.status)}</Chip>
                  </div>
                  <div>{agent.detail || 'Waiting for the next cycle.'}</div>
                  {agent.blockers?.length ? <small>{agent.blockers[0]}</small> : null}
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {cycleResult ? (
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Latest requested cycle</div>
            <strong>{humanize(cycleResult.status || (cycleResult.queued ? 'queued' : 'unknown'))}</strong>
            <div>{cycleResult.cycle_id || cycleResult.job_id || 'No cycle id'} | {cycleResult.policy_digest || '--'}</div>
            {cycleResult.blockers?.length ? <div>{cycleResult.blockers.join(' ')}</div> : null}
          </div>
        ) : null}

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: '0.75rem' }}>
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Command center</div>
            <strong>{humanize(snapshot?.status)}</strong>
            <div>{command.ready_count ?? 0} ready | {command.watch_count ?? 0} watch</div>
            <div>{command.blocked_count ?? 0} blocked | {command.stale_count ?? 0} stale</div>
          </div>
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Trade planner</div>
            <strong>{latestTargets.targets?.length || 0} candidates</strong>
            <div>{latestTargets.latest_run_id || 'No target run'}</div>
            <div>Equity | long | limit</div>
          </div>
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Paper manager</div>
            <strong>{paper.latest_execution?.status || 'idle'}</strong>
            <div>Filled {paper.latest_execution?.filled_count ?? 0} | Rejected {paper.latest_execution?.rejected_count ?? 0}</div>
            <div>{paper.can_execute ? 'Paper basket available' : 'Waiting for accepted targets'}</div>
          </div>
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Supervised live</div>
            <strong>{humanize(liveGate.status || 'blocked')}</strong>
            <div>{liveGate.connected_live_account_count ?? 0} live accounts connected</div>
            <div>{liveGate.allowed ? 'Approval intent available' : 'Blocked until all gates pass'}</div>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 0.8fr) minmax(0, 1.2fr)', gap: '0.75rem' }}>
          <div className="ui-panel ui-panel--section">
            <TextField
              label="Planner ticker"
              value={ticker}
              onChange={(event) => setTicker(event.target.value.toUpperCase())}
              placeholder={firstTargetSymbol(snapshot) || 'SPY'}
            />
            <div style={{ marginTop: '0.5rem' }}>
              <Chip tone={selectedDeskState ? toneForState(selectedDeskState.state) : 'neutral'} size="sm">
                {selectedDeskState ? `${selectedDeskState.desk_key}: ${selectedDeskState.state}` : 'all desks'}
              </Chip>
            </div>
          </div>
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Next actions</div>
            <div className="ui-stack-sm">
              {(snapshot?.next_actions || []).slice(0, 5).map((action) => (
                <div key={action.key} style={{ display: 'flex', justifyContent: 'space-between', gap: '0.5rem', flexWrap: 'wrap' }}>
                  <span>{action.label}</span>
                  <Chip tone={action.tone || 'neutral'} size="sm">{humanize(action.stage)}</Chip>
                  <small style={{ width: '100%' }}>{action.detail}</small>
                </div>
              ))}
              {!snapshot?.next_actions?.length ? <div>No AI actions available yet.</div> : null}
            </div>
          </div>
        </div>

        {Array.isArray(liveGate.blockers) && liveGate.blockers.length ? (
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Live blockers</div>
            {liveGate.blockers.map((blocker) => (
              <div key={blocker}>{blocker}</div>
            ))}
          </div>
        ) : null}

        {Array.isArray(snapshot?.conflicts) && snapshot.conflicts.length ? (
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Conflicts</div>
            {snapshot.conflicts.map((item) => (
              <div key={item.key}>{item.detail}</div>
            ))}
          </div>
        ) : null}

        {planPreview ? (
          <div className="ui-panel ui-panel--section">
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.5rem', flexWrap: 'wrap' }}>
              <strong>{planPreview.open_trade_request?.ticker || targetSymbol || 'Trade plan'}</strong>
              <Chip tone={planPreview.blocked ? 'negative' : 'positive'} size="sm">
                {planPreview.blocked ? 'blocked' : 'preview ready'}
              </Chip>
            </div>
            <div>Order {humanize(planPreview.open_trade_request?.order_type)} | Risk {planPreview.open_trade_request?.risk_percent ?? '--'}%</div>
            <div>Route {planPreview.open_trade_request?.execution_intent || 'desk'} | Source {planPreview.open_trade_request?.source || 'ai_desk_manager'}</div>
            {planPreview.blockers?.length ? <div>Blockers: {planPreview.blockers.join(' ')}</div> : null}
            {planPreview.warnings?.length ? <div>Warnings: {planPreview.warnings.join(' ')}</div> : null}
          </div>
        ) : null}

        {liveIntent?.trade_intent ? (
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Live intent created</div>
            <strong>{liveIntent.trade_intent.ticker}</strong>
            <div>Status {liveIntent.trade_intent.status} | Account {liveIntent.trade_intent.account_label || liveIntent.trade_intent.linked_account_id}</div>
            <div>No live order was submitted. This intent is waiting for explicit approval.</div>
          </div>
        ) : null}
      </div>
    </SectionCard>
  )
}
