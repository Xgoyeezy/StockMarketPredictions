import { useCallback, useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { closeTrade, getFrontendFilters, getNotesSummary, getOpenTrades, getTradeSummary, syncPendingOrders } from '../api/client'
import ActionBar from '../components/ActionBar'
import Button from '../components/Button'
import Chip from '../components/Chip'
import EmptyState from '../components/EmptyState'
import ErrorState from '../components/ErrorState'
import FeedbackState from '../components/FeedbackState'
import { SelectField, TextField } from '../components/FormFields'
import { formatInlineMeta } from '../components/InlineMeta'
import ListTable from '../components/ListTable'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import LoadingBlock from '../components/LoadingBlock'
import StrategyDeskStatusPanel from '../components/StrategyDeskStatusPanel'
import DataToolbar from '../components/DataToolbar'
import ClientTradeApprovalsSection from '../components/ClientTradeApprovalsSection'
import TradeWorkflowTrustCenter from '../components/TradeWorkflowTrustCenter'
import StatusBadge from '../components/StatusBadge'
import WorkflowGuide, { buildWorkflowSteps } from '../components/WorkflowGuide'
import useDebouncedValue from '../hooks/useDebouncedValue'
import { usePreferences } from '../context/PreferencesContext'
import { useToast } from '../context/ToastContext'
import {
  buildCapitalPreservationPolicy,
  buildCapitalPreservationSummary,
  buildLivePilotAuditSummary,
  buildRolloutReadinessSummary,
} from '../utils/capitalPreservation'
import { buildTradingSessionModel } from '../utils/intradayModel'
import { buildIntradayExecutionPlan } from '../utils/intradayExecutionModel'
import { buildIntradayPresetGuide, getIntradayPresetProfile } from '../utils/intradayPresetModel'
import {
  getAccountProfileDefinition,
  normalizeAccountProfile,
  resolveAccountProfileExecutionIntent,
} from '../utils/accountProfileModel'
import { validatePositiveNumber } from '../utils/validators'

function buildReviewLoopNotesUrl(search, review, completion = 'open') {
  const params = new URLSearchParams(search || '')
  params.set('noteFocus', 'review-loop')
  params.set('noteTag', 'review-loop')
  params.set('noteCompletion', completion === 'completed' ? 'completed' : 'open')
  params.set('noteRestored', '1')
  if (review?.id) {
    params.set('noteId', String(review.id))
  } else {
    params.delete('noteId')
  }
  if (review?.ticker) {
    params.set('noteTicker', String(review.ticker).trim().toUpperCase())
  } else {
    params.delete('noteTicker')
  }
  if (review?.label) {
    params.set('noteTitle', String(review.label).trim())
  } else {
    params.delete('noteTitle')
  }
  params.delete('noteId')
  const nextQuery = params.toString()
  return `/notes${nextQuery ? `?${nextQuery}` : ''}`
}

export default function TradesPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const [rows, setRows] = useState([])
  const [monitorRows, setMonitorRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [selectedIndex, setSelectedIndex] = useState(null)
  const [summary, setSummary] = useState(null)
  const [notesSummary, setNotesSummary] = useState(null)
  const [filters, setFilters] = useState({ trade_actions: ['all'] })
  const [actionFilter, setActionFilter] = useState('all')
  const [page, setPage] = useState(0)
  const pageSize = 25
  const [total, setTotal] = useState(0)
  const [syncingOrders, setSyncingOrders] = useState(false)
  const debouncedSearch = useDebouncedValue(search, 350)
  const [closeForm, setCloseForm] = useState({ tradeIndex: '', closeUnderlyingPrice: '', closeContractMid: '' })
  const [closeErrors, setCloseErrors] = useState({})
  const { pushToast } = useToast()
  const { preferences } = usePreferences()
  const tradingStyle = String(preferences?.tradingStyle || 'intraday').trim().toLowerCase() === 'intraday' ? 'intraday' : 'swing'
  const activeAccountProfile = normalizeAccountProfile(preferences?.activeAccountProfile)
  const activeAccountProfileDefinition = getAccountProfileDefinition(activeAccountProfile)
  const effectiveExecutionIntent = resolveAccountProfileExecutionIntent({
    activeAccountProfile,
    defaultExecutionIntent: preferences?.defaultExecutionIntent,
  })
  const intradayPresetProfile = getIntradayPresetProfile(preferences?.intradayPreset)
  const intradayPresetGuide = buildIntradayPresetGuide({ preset: preferences?.intradayPreset, page: 'trades' })

  const loadTrades = useCallback(async () => {
    try {
      setError('')
      const [data, summaryData, filterData, notesSummaryData] = await Promise.all([getOpenTrades({ search: debouncedSearch, limit: pageSize, offset: page * pageSize, actionFilter }), getTradeSummary(), getFrontendFilters(), getNotesSummary()])
      setSummary(summaryData)
      setNotesSummary(notesSummaryData)
      setFilters(filterData)
      setRows(data.open_trades || [])
      setMonitorRows(data.monitor || [])
      setTotal(Number(data.total || 0))
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load open trades.')
    } finally {
      setLoading(false)
    }
  }, [actionFilter, debouncedSearch, page])

  useEffect(() => {
    loadTrades()
  }, [loadTrades])

  useEffect(() => {
    setPage(0)
  }, [debouncedSearch, actionFilter])

  const metrics = useMemo(() => {
    const totalPremium = Number(summary?.tracked_premium ?? rows.reduce((sum, row) => sum + Number(row.entry_contract_mid || 0), 0))
    const urgent = Number(summary?.urgent_actions ?? monitorRows.filter((row) => String(row.monitor_action || '').toUpperCase() !== 'HOLD').length)
    return [
      { label: 'Open Trades', value: Number(summary?.open_trades ?? rows.length) },
      { label: 'Tracked Premium', value: totalPremium.toFixed(2) },
      { label: 'Urgent Actions', value: urgent, tone: urgent > 0 ? 'negative' : 'default' },
      { label: 'Call Positions', value: Number(summary?.call_positions ?? 0), tone: 'positive' },
      { label: 'Put Positions', value: Number(summary?.put_positions ?? 0), tone: 'negative' },
    ]
  }, [rows, monitorRows, summary])

  const tradeOptions = useMemo(() => rows.map((row, index) => ({
    index,
    label: formatInlineMeta([
      index,
      row.ticker || '—',
      row.direction || '—',
      row.contract_symbol || 'No contract',
    ]),
  })), [rows])

  const selectedTrade = selectedIndex === null ? null : { ...(rows[selectedIndex] || {}), ...(monitorRows[selectedIndex] || {}) }

  const capitalPreservationPolicy = useMemo(
    () =>
      buildCapitalPreservationPolicy({
        preferences,
        tradeTicket: {
          accountSize: preferences?.defaultAccountSize,
          riskPercent: preferences?.defaultRiskPercent,
        },
        defaults: {
          accountSize: 1000,
          riskPercent: 0.5,
        },
      }),
    [preferences],
  )
  const capitalPreservationSummary = useMemo(
    () =>
      buildCapitalPreservationSummary({
        policy: capitalPreservationPolicy,
        metrics: summary?.capital_preservation || {},
      }),
    [capitalPreservationPolicy, summary?.capital_preservation],
  )
  const reviewOnlyMode = Boolean(capitalPreservationSummary.reviewOnlyMode)
  const sessionModel = useMemo(
    () =>
      buildTradingSessionModel({
        tradingStyle,
        regularHoursOnly: preferences?.regularHoursOnly === true,
        openingRangeMinutes: preferences?.openingRangeMinutes,
        flattenBeforeCloseMinutes: preferences?.flattenBeforeCloseMinutes,
      }),
    [
      preferences?.flattenBeforeCloseMinutes,
      preferences?.openingRangeMinutes,
      preferences?.regularHoursOnly,
      tradingStyle,
    ],
  )
  const workingOrders = summary?.working_orders?.items || []
  const orderEvents = summary?.order_events?.items || []
  const attributionSummary = summary?.attribution_summary || {}
  const executionReview = useMemo(() => {
    const priceControlledOrders = workingOrders.filter((row) =>
      ['limit', 'stop_limit'].includes(String(row?.order_type || '').trim().toLowerCase()),
    ).length
    const urgentRoutes = workingOrders.filter((row) =>
      ['market', 'stop_market'].includes(String(row?.order_type || '').trim().toLowerCase()),
    ).length

    let reviewedFills = 0
    let adverseFills = 0
    let routeBreaks = 0
    const slippageBpsValues = []

    for (const event of orderEvents) {
      const status = String(event?.status || '').trim().toLowerCase()
      if (['rejected', 'expired', 'failed'].includes(status)) {
        routeBreaks += 1
      }

      const payload = event?.payload || {}
      const syncedOrder = payload?.synced_order || {}
      const expectedFill = syncedOrder.expected_fill_price ?? payload.expected_fill_price ?? null
      const actualFill = syncedOrder.actual_fill_price ?? payload.actual_fill_price ?? null
      const slippageBps = Number(payload.slippage_bps ?? syncedOrder.fill_slippage_bps)
      const slippageDollars = Number(payload.slippage_dollars ?? syncedOrder.fill_slippage_dollars)

      if (expectedFill !== null || actualFill !== null || Number.isFinite(slippageBps) || Number.isFinite(slippageDollars)) {
        reviewedFills += 1
      }
      if ((Number.isFinite(slippageBps) && slippageBps >= 15) || (Number.isFinite(slippageDollars) && slippageDollars > 0)) {
        adverseFills += 1
      }
      if (Number.isFinite(slippageBps)) {
        slippageBpsValues.push(slippageBps)
      }
    }

    const averageSlippageBps =
      slippageBpsValues.length > 0
        ? slippageBpsValues.reduce((sum, value) => sum + value, 0) / slippageBpsValues.length
        : null

    return {
      detail:
        workingOrders.length || reviewedFills || routeBreaks
          ? `${workingOrders.length} working orders, ${reviewedFills} fill reviews, and ${adverseFills} adverse fills are currently shaping execution quality.`
          : 'No broker-backed working orders or reviewed fills are active right now.',
      cards: [
        { label: 'Working Orders', value: workingOrders.length },
        {
          label: 'Price-Controlled',
          value: `${priceControlledOrders}/${workingOrders.length || 0}`,
          tone: urgentRoutes > 0 ? 'warning' : 'positive',
        },
        { label: 'Fill Reviews', value: reviewedFills },
        {
          label: 'Avg Slippage',
          value: averageSlippageBps === null ? '--' : `${averageSlippageBps.toFixed(1)} bps`,
          tone: averageSlippageBps !== null && averageSlippageBps >= 15 ? 'negative' : 'default',
        },
        {
          label: 'Fragile Fills',
          value: adverseFills + routeBreaks,
          tone: adverseFills + routeBreaks > 0 ? 'negative' : 'positive',
        },
      ],
    }
  }, [orderEvents, workingOrders])
  const rolloutReadiness = useMemo(() => {
    return buildRolloutReadinessSummary(summary?.rollout_readiness)
  }, [summary?.rollout_readiness])
  const livePilotAudit = useMemo(() => {
    return buildLivePilotAuditSummary(summary?.live_pilot_audit)
  }, [summary?.live_pilot_audit])
  const intradayExecutionPlan = useMemo(
    () =>
      buildIntradayExecutionPlan({
        tradingStyle,
        sessionModel,
        regularHoursOnly: preferences?.regularHoursOnly === true,
        reviewOnlyMode,
        executionIntent: effectiveExecutionIntent,
        orderType: preferences?.defaultOrderType,
        timeInForce: preferences?.regularHoursOnly === true ? 'day' : 'day_ext',
        riskPercent: preferences?.defaultRiskPercent,
        rolloutAllowsLive: rolloutReadiness.allowsLiveRollout,
      }),
    [
      effectiveExecutionIntent,
      preferences?.defaultExecutionIntent,
      preferences?.defaultOrderType,
      preferences?.defaultRiskPercent,
      preferences?.regularHoursOnly,
      reviewOnlyMode,
      rolloutReadiness.allowsLiveRollout,
      sessionModel,
      tradingStyle,
    ],
  )
  const reviewLoopSummary = useMemo(() => {
    const latestReview = attributionSummary?.latest_review || null
    const totalReviewed = Number(attributionSummary?.total_reviewed || 0)
    return {
      detail:
        totalReviewed > 0
          ? `${totalReviewed} recent closed trades are tagged by thesis, execution, and risk quality.`
          : 'No attributed closed trades are available yet. Close a few paper trades to start the review loop.',
      cards: [
        {
          label: 'Execution Drifts',
          value: Number(attributionSummary?.execution_review_count || 0),
          tone: Number(attributionSummary?.execution_review_count || 0) > 0 ? 'warning' : 'positive',
        },
        {
          label: 'Thesis Misses',
          value: Number(attributionSummary?.thesis_review_count || 0),
          tone: Number(attributionSummary?.thesis_review_count || 0) > 0 ? 'negative' : 'positive',
        },
        {
          label: 'Risk Reviews',
          value: Number(attributionSummary?.risk_review_count || 0),
          tone: Number(attributionSummary?.risk_review_count || 0) > 0 ? 'warning' : 'positive',
        },
        {
          label: 'Clean Wins',
          value: Number(attributionSummary?.clean_win_count || 0),
          tone: Number(attributionSummary?.clean_win_count || 0) > 0 ? 'positive' : 'default',
        },
      ],
      latestReview,
    }
  }, [attributionSummary])
  const latestResolvedRepair = notesSummary?.review_loop_summary?.latest_resolved || null
  const reviewLoopAction = reviewLoopSummary.latestReview
    ? {
        label: 'Open repair notes',
        onClick: () => navigate(buildReviewLoopNotesUrl(location.search, reviewLoopSummary.latestReview, 'open')),
      }
    : latestResolvedRepair
      ? {
          label: 'Open latest clear',
          onClick: () => navigate(buildReviewLoopNotesUrl(location.search, latestResolvedRepair, 'completed')),
        }
      : {
          label: 'Open journal review',
          onClick: () => navigate('/journal'),
        }
  const rolloutPolicyAction =
    reviewOnlyMode || !rolloutReadiness.allowsLiveRollout
      ? {
          label: 'Review broker-live policy',
          onClick: () => navigate('/settings'),
        }
      : {
          label: 'Open journal review',
          onClick: () => navigate('/journal'),
        }

  async function handleSyncOrders() {
    try {
      setSyncingOrders(true)
      const payload = await syncPendingOrders()
      const processed = Number(payload?.summary?.processed || 0)
      const changed = Number(payload?.summary?.changed || 0)
      const fills = Number(payload?.summary?.filled || 0)
      const failures = Number(payload?.summary?.failed || 0)
      if (processed < 1) {
        pushToast('No broker-backed working orders needed sync.', 'info')
      } else if (failures > 0) {
        pushToast(`Synced ${processed} working orders with ${failures} broker sync errors.`, 'error')
      } else {
        pushToast(
          `Synced ${processed} working orders. ${changed} changed, ${fills} filled.`,
          changed > 0 || fills > 0 ? 'success' : 'info',
        )
      }
      await loadTrades()
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Failed to sync paper orders.', 'error')
    } finally {
      setSyncingOrders(false)
    }
  }

  async function handleClose(event) {
    event.preventDefault()
    const tradeIndex = Number(closeForm.tradeIndex)
    const closeUnderlyingPrice = validatePositiveNumber(closeForm.closeUnderlyingPrice)
    const closeContractMid = validatePositiveNumber(closeForm.closeContractMid)
    const nextErrors = {}

    if (!Number.isInteger(tradeIndex) || tradeIndex < 0) {
      nextErrors.tradeIndex = 'Select the open trade you want to close.'
    }
    if (closeUnderlyingPrice === null || closeContractMid === null) {
      if (closeUnderlyingPrice === null) {
        nextErrors.closeUnderlyingPrice = 'Enter a positive underlying close price.'
      }
      if (closeContractMid === null) {
        nextErrors.closeContractMid = 'Enter a positive contract midpoint.'
      }
    }

    if (Object.keys(nextErrors).length) {
      setCloseErrors(nextErrors)
      pushToast('Fix the highlighted trade-close fields and try again.', 'error')
      return
    }

    try {
      setCloseErrors({})
      await closeTrade({
        trade_index: tradeIndex,
        close_underlying_price: closeUnderlyingPrice,
        close_contract_mid: closeContractMid,
      })
      pushToast('Trade closed successfully.', 'success')
      setCloseForm({ tradeIndex: '', closeUnderlyingPrice: '', closeContractMid: '' })
      setCloseErrors({})
      setSelectedIndex(null)
      await loadTrades()
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Failed to close trade.', 'error')
    }
  }

  if (loading) {
    return (
      <LoadingBlock
        label="Loading trade monitor"
        detail="Refreshing open positions, working orders, and rollout state so live risk opens with the current route story."
      />
    )
  }

  return (
    <>
      {error ? (
        <ErrorState
          title="Trade monitor unavailable"
          description={error}
          actionLabel="Reload trades"
          onAction={loadTrades}
        />
      ) : null}
      {debouncedSearch !== search ? (
        <FeedbackState
          compact
          tone="info"
          eyebrow="Search"
          title="Updating trade search"
          description="Filtering the live trade set so the current monitor view matches your latest query."
          role="status"
        />
      ) : null}
      {reviewOnlyMode ? (
        <FeedbackState
          compact
          tone="warning"
          eyebrow="Capital preservation"
          title="Review-only mode is active"
          description={capitalPreservationSummary.detail}
          actions={[
            {
              label: rolloutPolicyAction.label,
              onAction: rolloutPolicyAction.onClick,
              variant: 'ghost',
            },
          ]}
          role="alert"
        />
      ) : null}
      {tradingStyle === 'intraday' ? (
        <FeedbackState
          compact
          tone={intradayExecutionPlan.tone}
          eyebrow="Intraday execution"
          title={intradayExecutionPlan.title}
          description={intradayExecutionPlan.description}
          role="status"
        />
      ) : null}
      <PageIntro
        kicker="Trade management"
        title={tradingStyle === 'intraday' ? intradayPresetGuide.title : 'Manage open positions through the desk API'}
        description={
          reviewOnlyMode
            ? 'New entries are locked. Use this surface to review live positions, inspect fills, and close or reduce risk until the next regular session.'
            : tradingStyle === 'intraday'
              ? intradayPresetGuide.description
              : 'Review live positions, inspect a selected trade, and submit close prices from one operator surface.'
        }
        helper={tradingStyle === 'intraday' && !reviewOnlyMode ? intradayPresetGuide.helper : undefined}
        badge={`${activeAccountProfileDefinition.badgeLabel} | ${tradingStyle === 'intraday' ? `${intradayPresetProfile.shortLabel} | ` : ''}${total} tracked open trades`}
        actions={(
          <ActionBar compact>
            <Button type="button" variant="ghost" onClick={handleSyncOrders} disabled={syncingOrders}>
              {syncingOrders ? 'Syncing paper orders...' : 'Sync paper orders'}
            </Button>
            <Button type="button" variant="subtle" onClick={loadTrades}>
              Refresh trades
            </Button>
          </ActionBar>
        )}
      />
      <StrategyDeskStatusPanel
        eyebrow="Quant desks"
        title="Desk validation and route pressure"
        subtitle="The same desk runtime feeding allocator targets should stay visible while you manage open trades, so fills, exits, and new desk publications can be read together."
      />
      <WorkflowGuide
        showSteps={false}
        phaseLabel="Phase 3 - Act safely"
        phaseTone={reviewOnlyMode ? 'warning' : 'positive'}
        title={tradingStyle === 'intraday' ? `Use this page to manage ${intradayPresetProfile.shortLabel.toLowerCase()} risk before the session degrades.` : 'Use this page to manage risk and verify execution quality before looking for the next idea.'}
        description={tradingStyle === 'intraday' ? `${intradayPresetProfile.description} Use this surface to reduce same-session complexity, verify execution quality, and keep broker-live discipline visible.` : 'A live position should make the desk calmer, not more reactive. This surface is for reducing risk, checking route quality, and making sure broker-live discipline stays visible.'}
        steps={buildWorkflowSteps(2)}
        cards={[
          {
            label: 'Use this page for',
            value: tradingStyle === 'intraday' ? `Manage open ${intradayPresetProfile.shortLabel.toLowerCase()} risk, working orders, and fill quality.` : 'Manage open risk, working orders, and fill quality.',
            detail: tradingStyle === 'intraday' ? intradayPresetGuide.helper : 'This is the operational surface for what is already on, not the place to manufacture new conviction.',
            actionLabel: 'Open journal review',
            onAction: () => navigate('/journal'),
          },
          {
            label: 'Best next move',
            value: reviewOnlyMode ? 'Stay review-first until the desk unlocks again.' : (tradingStyle === 'intraday' ? `Sync, inspect, trim, or flatten before the ${intradayPresetProfile.shortLabel.toLowerCase()} window degrades.` : 'Sync, inspect, reduce, or close before adding complexity.'),
            detail: tradingStyle === 'intraday' ? 'Use broker-live readiness, execution review, and the active session window to decide whether the position needs cleanup, not more action.' : 'Use broker-live readiness and execution review to decide whether the position needs cleanup, not more action.',
            tone: 'positive',
            actionLabel: reviewLoopAction.label,
            onAction: reviewLoopAction.onClick,
          },
          {
            label: 'Do not ignore',
            value: tradingStyle === 'intraday' ? 'Route drift, cleanup pressure, and broker-live locks are part of the trade story.' : 'Route drift and broker-live locks are part of the trade story.',
            detail: tradingStyle === 'intraday' ? `A ${intradayPresetProfile.shortLabel.toLowerCase()} trade can be directionally right and still fail operationally because fills, timing, or broker-live readiness controls slipped.` : 'A setup can be directionally right and still fail operationally because fills, routing, or broker-live readiness controls slipped.',
            tone: 'warning',
            actionLabel: rolloutPolicyAction.label,
            onAction: rolloutPolicyAction.onClick,
          },
        ]}
      />
      <section className="metrics-grid">
        {metrics.map((item) => <MetricCard key={item.label} {...item} />)}
      </section>

      <ClientTradeApprovalsSection />
      <TradeWorkflowTrustCenter />

      {tradingStyle === 'intraday' ? (
        <SectionCard
          title={`${intradayPresetProfile.label} execution window`}
          subtitle={`Keep the ${intradayPresetProfile.shortLabel.toLowerCase()} route posture, cleanup bias, and risk budget visible while positions are live.`}
        >
          <section className="metrics-grid">
            {intradayExecutionPlan.cards.map((item) => <MetricCard key={item.label} {...item} />)}
          </section>
        </SectionCard>
      ) : null}
      <SectionCard
        title="Execution review"
        subtitle={executionReview.detail}
      >
        <section className="metrics-grid">
          {executionReview.cards.map((item) => <MetricCard key={item.label} {...item} />)}
        </section>
      </SectionCard>
      <SectionCard
        title="Broker-live readiness"
        subtitle={rolloutReadiness.detail}
      >
        <section className="metrics-grid">
          {rolloutReadiness.cards.map((item) => <MetricCard key={item.label} {...item} />)}
        </section>
        <div className="ui-panel ui-panel--section">
          <div className="ui-panel__kicker">Broker-live gate</div>
          <div className="ui-panel__title">{rolloutReadiness.label}</div>
          <div className="ui-panel__note">
            {rolloutReadiness.allowsLiveRollout
              ? 'Paper stability is clear enough for broker-live pilot routing.'
              : 'Broker-live routing should stay paper-first until these controls clear.'}
          </div>
          <div className="ui-panel__note">{rolloutReadiness.basis}</div>
          <div className="inline-meta-list">
            <span className="inline-meta-list__item">
              <strong>Trend:</strong> {rolloutReadiness.historyLabel}
            </span>
            <span className="inline-meta-list__item">
              <strong>History:</strong> {rolloutReadiness.historyDetail}
            </span>
          </div>
          {rolloutReadiness.historyItems.length ? (
            <div className="inline-meta-list">
              {rolloutReadiness.historyItems.map((item) => (
                <span key={item.key} className="inline-meta-list__item">
                  <strong>{item.recordedLabel}</strong> {item.label} | {item.resolvedCount}/{item.sampleCount} resolved | {item.replayWinRate} replay | {item.averageAbsSlippage}
                </span>
              ))}
            </div>
          ) : null}
          {rolloutReadiness.orderLifecycleSummary?.message ? (
            <div className="ui-panel__meta">
              {rolloutReadiness.orderLifecycleSummary.message}
            </div>
          ) : null}
        </div>
      </SectionCard>
      <SectionCard
        title="Broker-live pilot audit"
        subtitle={livePilotAudit.detail}
      >
        <section className="metrics-grid">
          {livePilotAudit.cards.map((item) => <MetricCard key={item.label} {...item} />)}
        </section>
        {livePilotAudit.latest ? (
          <div className="ui-panel ui-panel--section">
            <div className="ui-panel__kicker">{livePilotAudit.latest.routeLabel}</div>
            <div className="ui-panel__title">
              {livePilotAudit.latest.ticker} · {livePilotAudit.latest.eventLabel}
            </div>
            <div className="ui-panel__note">{livePilotAudit.latest.detail || 'Broker-live pilot event recorded.'}</div>
            <div className="ui-panel__note">{livePilotAudit.latest.basis || 'No saved gate basis recorded.'}</div>
            <div className="inline-meta-list">
              <span className="inline-meta-list__item">
                <strong>When:</strong> {livePilotAudit.latest.createdLabel}
              </span>
              <span className="inline-meta-list__item">
                <strong>Adapter:</strong> {livePilotAudit.latest.adapter}
              </span>
              <span className="inline-meta-list__item">
                <strong>Trend:</strong> {livePilotAudit.latest.historyLabel}
              </span>
            </div>
          </div>
        ) : (
          <EmptyState
            title="No broker-live pilot attempts"
            description="Once broker-live is selected and the paper gate evaluates the route, the attempt will be recorded here."
          />
        )}
        {livePilotAudit.items.length ? (
          <ListTable>
            <table className="signal-table ui-list-table">
              <caption className="ui-visually-hidden">Broker-live pilot audit history</caption>
              <thead>
                <tr><th scope="col">Attempt</th><th scope="col">Gate</th><th scope="col">Replay basis</th><th scope="col">Live drift</th></tr>
              </thead>
              <tbody>
                {livePilotAudit.items.slice(0, 6).map((item) => (
                  <tr key={item.key}>
                    <td>
                      <div className="ui-list-cell">
                        <div className="ui-list-cell__title">{item.ticker}</div>
                        <div className="ui-list-cell__meta">{item.createdLabel} | {item.eventLabel}</div>
                      </div>
                    </td>
                    <td>
                      <div className="ui-list-cell">
                        <div className="ui-list-cell__badges">
                          <StatusBadge value={item.gateLabel} tone={item.gateTone} />
                          <StatusBadge value={item.status} />
                        </div>
                        <div className="ui-list-cell__meta">{item.basis || 'No saved gate basis recorded.'}</div>
                      </div>
                    </td>
                    <td>
                      <div className="ui-list-cell">
                        <div className="ui-list-cell__title">{`${item.resolvedCount} resolved | ${item.openCount} open`}</div>
                        <div className="ui-list-cell__meta">{`${item.replayWinRate} replay | ${item.historyLabel}`}</div>
                      </div>
                    </td>
                    <td>
                      <div className="ui-list-cell">
                        <div className="ui-list-cell__title">{item.averageAbsSlippage}</div>
                        <div className="ui-list-cell__meta">{`Worst ${item.worstAbsSlippage} | ${item.slippageSampleCount} fill reviews`}</div>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ListTable>
        ) : null}
      </SectionCard>
      <SectionCard
        title="Review loop"
        subtitle={reviewLoopSummary.detail}
        actions={(
          <ActionBar compact>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => navigate(buildReviewLoopNotesUrl(location.search, reviewLoopSummary.latestReview, 'open'))}
            >
              Open repairs
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => navigate(buildReviewLoopNotesUrl(location.search, reviewLoopSummary.latestReview, 'completed'))}
            >
              Repairs cleared
            </Button>
            {latestResolvedRepair ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => navigate(buildReviewLoopNotesUrl(location.search, latestResolvedRepair, 'completed'))}
              >
                Open latest clear
              </Button>
            ) : null}
          </ActionBar>
        )}
      >
        <section className="metrics-grid">
          {reviewLoopSummary.cards.map((item) => <MetricCard key={item.label} {...item} />)}
        </section>
        {reviewLoopSummary.latestReview ? (
          <div className="ui-panel ui-panel--section">
            <div className="ui-panel__kicker">Latest attributed review</div>
            <div className="ui-panel__title">
              {reviewLoopSummary.latestReview.ticker} · {reviewLoopSummary.latestReview.label}
            </div>
            <div className="ui-panel__note">{reviewLoopSummary.latestReview.detail || 'No review detail saved.'}</div>
          </div>
        ) : null}
      </SectionCard>
      <section className="content-grid">
        <SectionCard
          title="Open trades"
          subtitle="Read and manage open positions through the API."
          actions={(
            <DataToolbar
              searchValue={search}
              onSearchChange={setSearch}
              searchPlaceholder="Search ticker or contract"
              searchDelayLabel="Search is debounced for smoother trade queries."
              actions={(<>
                <SelectField ariaLabel="Filter trades by action" value={actionFilter} onChange={(e) => setActionFilter(e.target.value)}>
                  {(filters.trade_actions || ['all']).map((option) => <option key={option} value={option}>{option}</option>)}
                </SelectField>
                <Button type="button" variant="ghost" size="sm" onClick={loadTrades}>Refresh</Button>
              </>)}
            />
          )}
        >
        <ListTable>
            <table className="signal-table ui-list-table">
              <caption className="ui-visually-hidden">Open trades routing review</caption>
              <thead>
                <tr>
                  <th scope="col">Index</th><th scope="col">Ticker</th><th scope="col">Contract</th><th scope="col">Direction</th><th scope="col">Entry Underlying</th><th scope="col">Entry Mid</th><th scope="col">Action</th><th scope="col">PnL</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, index) => (
                  <tr
                    key={`${row.contract_symbol || row.ticker}-${index}`}
                    className={selectedIndex === index ? 'table-row--selected' : ''}
                    onClick={() => setSelectedIndex(index)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault()
                        setSelectedIndex(index)
                      }
                    }}
                    tabIndex={0}
                    aria-selected={selectedIndex === index}
                  >
                    <td>{index}</td>
                    <td>{row.ticker}</td>
                    <td>{row.contract_symbol || '—'}</td>
                    <td>{row.direction || '—'}</td>
                    <td>{row.entry_underlying_price ?? '—'}</td>
                    <td>{row.entry_contract_mid ?? '—'}</td>
                    <td>{monitorRows[index]?.monitor_action ?? '—'}</td>
                    <td>{monitorRows[index]?.pnl_dollars ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ListTable>
        </SectionCard>
        <SectionCard title="Selected trade" subtitle="Readable details for the highlighted position.">
          {selectedTrade ? (
            <div className="key-value-grid">
              {Object.entries(selectedTrade).slice(0, 12).map(([key, value]) => (
                <div className="key-value-row" key={key}>
                  <span>{key}</span>
                  <strong>{String(value ?? '—')}</strong>
                </div>
              ))}
            </div>
          ) : <EmptyState title="No trade selected" description={tradingStyle === 'intraday' ? `Pick a live ${intradayPresetProfile.shortLabel.toLowerCase()} position from the table to inspect its route, fills, and cleanup posture here.` : 'Pick an open position from the table to inspect its live details here.'} />}
        </SectionCard>
      </section>
      <div className="pager-row">
        <Chip tone="neutral" size="sm">
          Showing {rows.length} of {total}
        </Chip>
        <ActionBar compact>
          <Button type="button" variant="ghost" size="sm" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>Previous</Button>
          <Button type="button" variant="ghost" size="sm" disabled={(page + 1) * pageSize >= total} onClick={() => setPage((value) => value + 1)}>Next</Button>
        </ActionBar>
      </div>
      <SectionCard
        title={tradingStyle === 'intraday' ? 'Same-session cleanup' : 'Close trade'}
        subtitle={
          tradingStyle === 'intraday'
            ? 'Select the live position, record the closing prices, and use this form to flatten or reduce same-session risk before the window closes.'
            : 'Select a readable trade label and submit closing prices.'
        }
      >
        <form className="analysis-form analysis-form--wide" onSubmit={handleClose}>
          <SelectField
            label="Open trade"
            hint="Pick the live position you are closing before entering the final prices."
            error={closeErrors.tradeIndex}
            required
            ariaLabel="Select open trade to close"
            value={closeForm.tradeIndex}
            onChange={(e) => {
              const nextValue = e.target.value
              setCloseForm((state) => ({ ...state, tradeIndex: nextValue }))
              setSelectedIndex(nextValue === '' ? null : Number(nextValue))
              setCloseErrors((state) => ({ ...state, tradeIndex: '' }))
            }}
          >
            <option value="">Select open trade</option>
            {tradeOptions.map((option) => <option key={option.index} value={option.index}>{option.label}</option>)}
          </SelectField>
          <TextField
            label="Underlying close price"
            hint="Use the final underlying price at the moment you closed or confirmed the exit."
            error={closeErrors.closeUnderlyingPrice}
            required
            ariaLabel="Close underlying price"
            type="number"
            min="0.01"
            step="0.01"
            value={closeForm.closeUnderlyingPrice}
            onChange={(e) => {
              setCloseForm((state) => ({ ...state, closeUnderlyingPrice: e.target.value }))
              setCloseErrors((state) => ({ ...state, closeUnderlyingPrice: '' }))
            }}
            placeholder="Close underlying price"
          />
          <TextField
            label="Contract midpoint"
            hint="Record the option midpoint or paper fill reference used for the close."
            error={closeErrors.closeContractMid}
            required
            ariaLabel="Close contract midpoint"
            type="number"
            min="0.01"
            step="0.01"
            value={closeForm.closeContractMid}
            onChange={(e) => {
              setCloseForm((state) => ({ ...state, closeContractMid: e.target.value }))
              setCloseErrors((state) => ({ ...state, closeContractMid: '' }))
            }}
            placeholder="Close contract mid"
          />
          <Button type="submit" variant="solid" disabled={!rows.length}>{tradingStyle === 'intraday' ? 'Close same-session trade' : 'Close trade'}</Button>
        </form>
      </SectionCard>
      <section className="content-grid">
        <SectionCard title="Working orders" subtitle="Broker-backed pending orders and current fill posture.">
          <ListTable>
            <table className="signal-table ui-list-table">
              <caption className="ui-visually-hidden">Working broker orders</caption>
              <thead>
                <tr><th scope="col">Ticker</th><th scope="col">Broker</th><th scope="col">Ticket</th><th scope="col">Fill</th><th scope="col">Status</th></tr>
              </thead>
              <tbody>
                {workingOrders.length ? workingOrders.map((row, index) => {
                  const brokerQty = Number(row.broker_qty ?? 0)
                  const brokerFilledQty = Number(row.broker_filled_qty ?? 0)
                  const hasBrokerProgress = Number.isFinite(brokerQty) && brokerQty > 0
                  const expectedFill =
                    row.limit_price ?? row.live_price_at_submit ?? row.entry_underlying_price ?? null
                  return (
                    <tr key={row.order_id || `${row.ticker}-${index}`}>
                      <td>{row.ticker || 'â€”'}</td>
                      <td>
                        <div className="ui-list-cell">
                          <div className="ui-list-cell__title">{row.broker_name || 'desk'}</div>
                          <div className="ui-list-cell__meta">{row.broker_order_id || 'No broker order id'}</div>
                        </div>
                      </td>
                      <td>
                        <div className="ui-list-cell">
                          <div className="ui-list-cell__title">
                            {[row.order_type ? String(row.order_type).toUpperCase() : null, row.time_in_force ? String(row.time_in_force).toUpperCase() : null].filter(Boolean).join(' / ') || 'â€”'}
                          </div>
                          <div className="ui-list-cell__meta">
                            {expectedFill !== null && expectedFill !== undefined ? `Expected ${expectedFill}` : 'No expected fill mapped yet'}
                          </div>
                        </div>
                      </td>
                      <td>
                        <div className="ui-list-cell">
                          <div className="ui-list-cell__title">
                            {hasBrokerProgress ? `${brokerFilledQty}/${brokerQty}` : 'Awaiting first fill'}
                          </div>
                          <div className="ui-list-cell__meta">
                            {row.broker_filled_avg_price ? `Avg ${row.broker_filled_avg_price}` : 'No average fill yet'}
                          </div>
                        </div>
                      </td>
                      <td>
                        <div className="ui-list-cell">
                          <div className="ui-list-cell__badges">
                            <StatusBadge value={row.broker_status || row.status || 'Pending'} />
                          </div>
                          <div className="ui-list-cell__meta">
                            {row.latest_order_event?.label || row.latest_order_event?.status || 'Desk-tracked working order'}
                          </div>
                        </div>
                      </td>
                    </tr>
                  )
                }) : (
                  <tr>
                    <td colSpan={5}>
                      <EmptyState
                        title="No working orders"
                        description="No broker-backed pending orders are waiting right now."
                      />
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </ListTable>
        </SectionCard>
        <SectionCard title="Recent order lifecycle" subtitle="Recent broker and desk order events, including fill review when available.">
          <ListTable>
            <table className="signal-table ui-list-table">
              <caption className="ui-visually-hidden">Order event and fill review log</caption>
              <thead>
                <tr><th scope="col">Event</th><th scope="col">Status</th><th scope="col">Detail</th><th scope="col">Fill review</th></tr>
              </thead>
              <tbody>
                {orderEvents.length ? orderEvents.slice(0, 8).map((event, index) => {
                  const payload = event.payload || {}
                  const syncedOrder = payload.synced_order || {}
                  const expectedFill = syncedOrder.expected_fill_price ?? payload.expected_fill_price ?? null
                  const actualFill = syncedOrder.actual_fill_price ?? payload.actual_fill_price ?? null
                  const slippageDollars = payload.slippage_dollars ?? syncedOrder.fill_slippage_dollars ?? null
                  const slippageBps = payload.slippage_bps ?? syncedOrder.fill_slippage_bps ?? null
                  return (
                    <tr key={event.id || `${event.trade_id || event.ticker}-${index}`}>
                      <td>
                        <div className="ui-list-cell">
                          <div className="ui-list-cell__title">{event.ticker || 'â€”'}</div>
                          <div className="ui-list-cell__meta">{event.label || event.event_key || 'Order event'}</div>
                        </div>
                      </td>
                      <td>
                        <StatusBadge value={event.status || 'recorded'} />
                      </td>
                      <td>{event.detail || 'Lifecycle event recorded for this order.'}</td>
                      <td>
                        {expectedFill !== null || actualFill !== null ? (
                          <div className="ui-list-cell">
                            <div className="ui-list-cell__title">
                              {[expectedFill !== null ? `Expected ${expectedFill}` : null, actualFill !== null ? `Actual ${actualFill}` : null].filter(Boolean).join(' / ')}
                            </div>
                            <div className="ui-list-cell__meta">
                              {slippageDollars !== null || slippageBps !== null
                                ? `Slippage ${slippageDollars ?? 'â€”'} / ${slippageBps ?? 'â€”'} bps`
                                : 'No slippage note recorded'}
                            </div>
                          </div>
                        ) : 'â€”'}
                      </td>
                    </tr>
                  )
                }) : (
                  <tr>
                    <td colSpan={4}>
                      <EmptyState
                        title="No recent order events"
                        description="The order lifecycle feed will show broker sync changes, fills, rejects, and closes here."
                      />
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </ListTable>
        </SectionCard>
      </section>
    </>
  )
}
