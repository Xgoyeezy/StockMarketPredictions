import { useCallback, useEffect, useMemo, useState } from 'react'
import ActionBar from './ActionBar'
import Button from './Button'
import FeedbackState from './FeedbackState'
import { TextAreaField } from './FormFields'
import MetricCard from './MetricCard'
import SectionCard from './SectionCard'
import StatusBadge from './StatusBadge'
import {
  approveTradeIntent,
  conditionallyApproveTradeIntent,
  expireTradeIntent,
  getTradeIntents,
  getTradeTrustPacket,
  rejectTradeIntent,
} from '../api/client'
import { useToast } from '../context/ToastContext'

function formatDateTime(value) {
  if (!value) return 'Pending'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString()
}

function formatDirection(payload) {
  const thesis = String(payload?.thesis_direction || '').trim()
  if (thesis) return thesis
  const right = String(payload?.option_right || '').trim().toUpperCase()
  if (right === 'PUT') return 'Bearish'
  if (right === 'CALL') return 'Bullish'
  return 'Review'
}

function formatMoney(value) {
  const amount = Number(value)
  if (!Number.isFinite(amount)) return '--'
  return amount.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 })
}

function formatCompact(value) {
  if (value === null || value === undefined || value === '') return '--'
  const numeric = Number(value)
  if (Number.isFinite(numeric)) return numeric.toLocaleString(undefined, { maximumFractionDigits: 2 })
  return String(value)
}

function humanize(value) {
  return String(value || '--').replace(/_/g, ' ')
}

function statusTone(status) {
  const normalized = String(status || '').trim().toLowerCase()
  if (normalized === 'submitted') return 'positive'
  if (normalized === 'rejected' || normalized === 'expired' || normalized === 'submission_failed') return 'negative'
  return 'warning'
}

function riskTone(riskBand) {
  const normalized = String(riskBand || '').trim().toLowerCase()
  if (normalized === 'critical' || normalized === 'high') return 'negative'
  if (normalized === 'moderate') return 'warning'
  if (normalized === 'low') return 'positive'
  return 'neutral'
}

function checklistTone(status) {
  const normalized = String(status || '').trim().toLowerCase()
  if (normalized === 'pass') return 'positive'
  if (normalized === 'blocked' || normalized === 'missing') return 'negative'
  return 'warning'
}

function splitConditions(value) {
  return String(value || '')
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 8)
}

function buildDefaultDraft(intent) {
  return {
    note: intent?.approval_note || '',
    reason: intent?.rejection_reason || '',
    conditions: (intent?.broker_case?.conditions || []).join('\n'),
  }
}

function downloadJson(filename, payload) {
  if (typeof window === 'undefined') return
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
  const url = window.URL.createObjectURL(blob)
  const anchor = window.document.createElement('a')
  anchor.href = url
  anchor.download = filename || 'broker-trust-packet.json'
  window.document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.URL.revokeObjectURL(url)
}

function sortApprovalItems(items) {
  const priorityOrder = {
    submit_repair: 0,
    refresh_required: 1,
    senior_review: 2,
    ready_now: 3,
    condition_follow_up: 4,
    monitor: 5,
  }
  return [...items].sort((left, right) => {
    const leftPriority = priorityOrder[left?.broker_case?.queue_priority] ?? 9
    const rightPriority = priorityOrder[right?.broker_case?.queue_priority] ?? 9
    if (leftPriority !== rightPriority) return leftPriority - rightPriority
    return String(right?.created_at || '').localeCompare(String(left?.created_at || ''))
  })
}

function BrokerCaseCard({
  intent,
  draft,
  busyKey,
  onDraftChange,
  onApprove,
  onConditionalApprove,
  onReject,
  onExpire,
  onDownloadPacket,
}) {
  const brokerCase = intent?.broker_case || {}
  const riskFactors = brokerCase.risk_factors || []
  const checklist = brokerCase.verification_checklist || []
  const preTradeRisk = brokerCase.pre_trade_risk || {}
  const strategy = intent?.strategy_release_snapshot || {}
  const recommendation = strategy.recommendation_basis || {}
  const request = intent?.request_payload || {}
  const pendingLike = ['pending_approval', 'conditionally_approved', 'submission_failed'].includes(String(intent?.status || '').toLowerCase())
  const approveLabel = intent?.status === 'conditionally_approved' ? 'Submit after conditions' : 'Approve and submit'

  return (
    <article className="broker-case-card">
      <header className="broker-case-card__header">
        <div>
          <div className="ui-kicker">{brokerCase.case_id || 'Client trade case'}</div>
          <h3 className="broker-case-card__title">
            {intent.ticker} {formatDirection(request)}
          </h3>
          <div className="ui-muted">
            {intent.account_label} | {String(intent.account_environment || 'paper').toUpperCase()} | {humanize(brokerCase.queue_priority)}
          </div>
        </div>
        <div className="broker-case-card__badges">
          <StatusBadge tone={statusTone(intent.status)}>{humanize(intent.status)}</StatusBadge>
          <StatusBadge tone={riskTone(brokerCase.risk_band)}>
            {humanize(brokerCase.risk_band || 'risk')} {formatCompact(brokerCase.risk_score)}
          </StatusBadge>
        </div>
      </header>

      <div className="broker-case-card__metrics">
        <MetricCard label="Instrument" value={intent.instrument_type === 'equity' ? 'Equity' : 'Listed option'} />
        <MetricCard label="Position cost" value={formatMoney(preTradeRisk.position_cost)} />
        <MetricCard label="Target" value={formatCompact(recommendation.expected_underlying_target || preTradeRisk.target_price)} />
        <MetricCard label="Invalidation" value={formatCompact(recommendation.invalidation_price || preTradeRisk.invalidation_price)} />
      </div>

      <div className="broker-case-card__grid">
        <section className="broker-case-panel">
          <h4>Review blockers</h4>
          {riskFactors.length ? (
            <ul className="broker-case-list">
              {riskFactors.slice(0, 5).map((factor) => (
                <li key={`${factor.code}-${factor.label}`}>
                  <span>{factor.label}</span>
                  <strong>{factor.points > 0 ? `+${factor.points}` : factor.points}</strong>
                </li>
              ))}
            </ul>
          ) : (
            <p className="ui-muted">No elevated risk factors captured for this case.</p>
          )}
        </section>

        <section className="broker-case-panel">
          <h4>Verification checklist</h4>
          <div className="broker-checklist">
            {checklist.map((item) => (
              <div key={item.key || item.label} className="broker-checklist__item">
                <StatusBadge tone={checklistTone(item.status)}>{humanize(item.status)}</StatusBadge>
                <div>
                  <strong>{item.label}</strong>
                  <span>{item.detail}</span>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>

      <section className="broker-case-panel broker-case-panel--wide">
        <h4>Strategy release ledger</h4>
        <div className="broker-ledger">
          <span><strong>Release</strong>{strategy.release_id || '--'}</span>
          <span><strong>App</strong>{strategy.app_version || '--'}</span>
          <span><strong>Model</strong>{strategy.prediction_stack_version || '--'}</span>
          <span><strong>Route</strong>{strategy.route_version || strategy.route_family || '--'}</span>
          <span><strong>Requested</strong>{formatDateTime(intent.created_at)}</span>
        </div>
      </section>

      {intent.similar_cases?.length ? (
        <section className="broker-case-panel broker-case-panel--wide">
          <h4>Similar case signals</h4>
          <div className="broker-similar-cases">
            {intent.similar_cases.map((item) => (
              <span key={item.intent_id}>
                {item.ticker} | {humanize(item.status)} | {humanize(item.relationship)}
              </span>
            ))}
          </div>
        </section>
      ) : null}

      <section className="broker-case-panel broker-case-panel--wide">
        <h4>Decision console</h4>
        <div className="broker-decision-grid">
          <TextAreaField
            label="Decision rationale"
            value={draft?.note || ''}
            onChange={(event) => onDraftChange(intent.id, { note: event.target.value })}
            rows={3}
            placeholder="State why this recommendation is appropriate for the client account and risk frame."
          />
          <TextAreaField
            label="Conditions"
            value={draft?.conditions || ''}
            onChange={(event) => onDraftChange(intent.id, { conditions: event.target.value })}
            rows={3}
            placeholder="One condition per line for conditional approval."
          />
          <TextAreaField
            label="Rejection reason"
            value={draft?.reason || ''}
            onChange={(event) => onDraftChange(intent.id, { reason: event.target.value })}
            rows={3}
            placeholder="Required when rejecting."
          />
        </div>
        <p className="ui-muted">{brokerCase.next_action}</p>
        <ActionBar compact>
          <Button
            type="button"
            variant="solid"
            onClick={() => onApprove(intent.id)}
            disabled={!pendingLike || busyKey === `approve:${intent.id}`}
          >
            {busyKey === `approve:${intent.id}` ? 'Submitting...' : approveLabel}
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={() => onConditionalApprove(intent.id)}
            disabled={!pendingLike || busyKey === `conditional:${intent.id}`}
          >
            {busyKey === `conditional:${intent.id}` ? 'Saving...' : 'Conditional approval'}
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={() => onReject(intent.id)}
            disabled={!pendingLike || busyKey === `reject:${intent.id}`}
          >
            Reject
          </Button>
          <Button
            type="button"
            variant="subtle"
            onClick={() => onExpire(intent.id)}
            disabled={!pendingLike || busyKey === `expire:${intent.id}`}
          >
            Expire
          </Button>
          <Button
            type="button"
            variant="subtle"
            onClick={() => onDownloadPacket(intent.id)}
            disabled={busyKey === `packet:${intent.id}`}
          >
            Trust packet
          </Button>
        </ActionBar>
      </section>
    </article>
  )
}

export default function ClientTradeApprovalsSection() {
  const { pushToast } = useToast()
  const [snapshot, setSnapshot] = useState({ items: [], count: 0, status_counts: {}, broker_ops: {} })
  const [drafts, setDrafts] = useState({})
  const [loading, setLoading] = useState(true)
  const [busyKey, setBusyKey] = useState('')

  const loadApprovals = useCallback(async () => {
    try {
      const payload = await getTradeIntents({ status: 'all' })
      setSnapshot(payload)
      setDrafts((current) => {
        const next = { ...current }
        for (const item of payload?.items || []) {
          next[item.id] = {
            ...buildDefaultDraft(item),
            ...(next[item.id] || {}),
          }
        }
        return next
      })
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to load client trade approvals.', 'error')
    } finally {
      setLoading(false)
    }
  }, [pushToast])

  useEffect(() => {
    loadApprovals()
  }, [loadApprovals])

  function updateDraft(intentId, patch) {
    setDrafts((current) => ({
      ...current,
      [intentId]: {
        ...(current[intentId] || {}),
        ...patch,
      },
    }))
  }

  async function handleApprove(intentId) {
    try {
      setBusyKey(`approve:${intentId}`)
      await approveTradeIntent(intentId, { note: drafts[intentId]?.note || undefined })
      pushToast('Client trade approved and submitted.', 'success')
      await loadApprovals()
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to approve the client trade.', 'error')
    } finally {
      setBusyKey('')
    }
  }

  async function handleConditionalApprove(intentId) {
    try {
      setBusyKey(`conditional:${intentId}`)
      await conditionallyApproveTradeIntent(intentId, {
        note: drafts[intentId]?.note || undefined,
        conditions: splitConditions(drafts[intentId]?.conditions),
      })
      pushToast('Conditional approval saved.', 'success')
      await loadApprovals()
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to save conditional approval.', 'error')
    } finally {
      setBusyKey('')
    }
  }

  async function handleReject(intentId) {
    try {
      setBusyKey(`reject:${intentId}`)
      await rejectTradeIntent(intentId, {
        note: drafts[intentId]?.note || undefined,
        reason: drafts[intentId]?.reason || undefined,
      })
      pushToast('Client trade rejected.', 'info')
      await loadApprovals()
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to reject the client trade.', 'error')
    } finally {
      setBusyKey('')
    }
  }

  async function handleExpire(intentId) {
    try {
      setBusyKey(`expire:${intentId}`)
      await expireTradeIntent(intentId)
      pushToast('Client trade approval expired.', 'info')
      await loadApprovals()
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to expire the client trade.', 'error')
    } finally {
      setBusyKey('')
    }
  }

  async function handleDownloadPacket(intentId) {
    try {
      setBusyKey(`packet:${intentId}`)
      const payload = await getTradeTrustPacket(intentId)
      const filename = payload?.summary?.export_filename || `broker-trust-packet-${intentId}.json`
      downloadJson(filename, payload)
      pushToast('Broker trust packet exported.', 'success')
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to export trust packet.', 'error')
    } finally {
      setBusyKey('')
    }
  }

  const metrics = useMemo(() => {
    const statusCounts = snapshot?.status_counts || {}
    const ops = snapshot?.broker_ops || {}
    return [
      { label: 'Client cases', value: Number(snapshot?.count || 0) },
      { label: 'Pending', value: Number(statusCounts.pending_approval || 0), tone: Number(statusCounts.pending_approval || 0) > 0 ? 'warning' : 'positive' },
      { label: 'Conditional', value: Number(statusCounts.conditionally_approved || 0), tone: Number(statusCounts.conditionally_approved || 0) > 0 ? 'warning' : 'default' },
      { label: 'High-risk', value: Number(ops.blocked_or_high_risk_count || 0), tone: Number(ops.blocked_or_high_risk_count || 0) > 0 ? 'negative' : 'positive' },
      { label: 'Trust packets', value: Number(ops.audit_export_ready_count || 0), tone: Number(ops.audit_export_ready_count || 0) > 0 ? 'positive' : 'default' },
    ]
  }, [snapshot])

  const visibleItems = useMemo(() => sortApprovalItems(snapshot?.items || []), [snapshot])
  const activeItems = useMemo(
    () => visibleItems.filter((item) => ['pending_approval', 'conditionally_approved', 'submission_failed'].includes(String(item?.status || '').toLowerCase())),
    [visibleItems],
  )

  return (
    <SectionCard
      title="Client approval inbox 2.0"
      subtitle="Broker-serviced trades are handled as client trade cases with risk scoring, verification, strategy basis, rationale, and exportable trust packets."
      actions={(
        <ActionBar compact>
          <Button type="button" variant="ghost" onClick={loadApprovals} disabled={loading}>
            Refresh cases
          </Button>
        </ActionBar>
      )}
    >
      <section className="metrics-grid metrics-grid--compact">
        {metrics.map((item) => <MetricCard key={item.label} {...item} />)}
      </section>

      {loading ? (
        <FeedbackState tone="info" title="Loading broker cases" description="Refreshing linked-client approval and trust-packet state." />
      ) : null}

      {!loading && activeItems.length === 0 ? (
        <FeedbackState
          tone="positive"
          title="No active broker cases waiting"
          description="Client-linked trade cases will appear here once a trade is staged against a linked account."
        />
      ) : null}

      {snapshot?.broker_ops?.next_actions?.length ? (
        <section className="broker-ops-strip">
          {snapshot.broker_ops.next_actions.slice(0, 4).map((item) => (
            <div key={item.intent_id} className="broker-ops-strip__item">
              <strong>{item.ticker} | {humanize(item.priority)}</strong>
              <span>{item.detail}</span>
            </div>
          ))}
        </section>
      ) : null}

      <div className="broker-case-list-shell">
        {visibleItems.map((intent) => (
          <BrokerCaseCard
            key={intent.id}
            intent={intent}
            draft={drafts[intent.id] || buildDefaultDraft(intent)}
            busyKey={busyKey}
            onDraftChange={updateDraft}
            onApprove={handleApprove}
            onConditionalApprove={handleConditionalApprove}
            onReject={handleReject}
            onExpire={handleExpire}
            onDownloadPacket={handleDownloadPacket}
          />
        ))}
      </div>
    </SectionCard>
  )
}
