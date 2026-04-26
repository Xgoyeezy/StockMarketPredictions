import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getPortfolio, getPortfolioEquity, getPortfolioPerformance, syncPendingOrders } from '../api/client'
import ActionBar from '../components/ActionBar'
import Button from '../components/Button'
import Chip from '../components/Chip'
import EducationCallout from '../components/EducationCallout'
import EmptyState from '../components/EmptyState'
import ErrorState from '../components/ErrorState'
import EquityCurveChart from '../components/EquityCurveChart'
import { ToggleField } from '../components/FormFields'
import InlineMeta from '../components/InlineMeta'
import ListTable from '../components/ListTable'
import LoadingBlock from '../components/LoadingBlock'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import StatusBadge from '../components/StatusBadge'
import StrategyDeskStatusPanel from '../components/StrategyDeskStatusPanel'
import WorkflowGuide, { buildWorkflowSteps } from '../components/WorkflowGuide'
import { usePreferences } from '../context/PreferencesContext'
import { useToast } from '../context/ToastContext'
import useKeyboardListNavigation from '../hooks/useKeyboardListNavigation'
import usePageActionShortcuts, { focusFirstMatching } from '../hooks/usePageActionShortcuts'
import usePolling from '../hooks/usePolling'
import { buildCapitalPreservationPolicy, buildPromotionGateSummary } from '../utils/capitalPreservation'
import { buildIntradayReviewLens } from '../utils/intradayReviewModel'

const moneyFormatter = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 2,
})

function toNumber(value) {
  const normalized = Number(value)
  return Number.isFinite(normalized) ? normalized : null
}

function formatDollars(value) {
  const normalized = toNumber(value)
  if (normalized === null) return '--'
  return moneyFormatter.format(normalized)
}

function formatPercent(value, { ratio = false } = {}) {
  const normalized = toNumber(value)
  if (normalized === null) return '--'
  const percentage = ratio ? normalized * 100 : normalized
  return `${percentage.toFixed(1)}%`
}

function formatPrice(value) {
  const normalized = toNumber(value)
  if (normalized === null) return '--'
  return normalized.toFixed(normalized >= 100 ? 2 : 3)
}

function formatTimestamp(value) {
  if (!value) return '--'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return '--'
  return parsed.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function formatBasisPoints(value) {
  const normalized = toNumber(value)
  if (normalized === null) return '--'
  return `${normalized.toFixed(1)} bps`
}

function formatSignedBasisPoints(value) {
  const normalized = toNumber(value)
  if (normalized === null) return '--'
  return `${normalized > 0 ? '+' : ''}${normalized.toFixed(1)} bps`
}

function buildStatusTone(status) {
  const normalized = String(status || '').trim().toUpperCase()
  if (['OVEREXPOSED', 'STOP HIT', 'EXIT FULLY NOW'].includes(normalized)) return 'negative'
  if (['ELEVATED', 'SELL MORE NOW', 'SELL 50% NOW'].includes(normalized)) return 'warning'
  if (['OK', 'HOLD'].includes(normalized)) return 'positive'
  return 'neutral'
}

function formatTradeCell(row) {
  const instrumentLabel = row.instrument_label || (row.instrument_type === 'equity' ? 'Equity' : 'Listed option')
  const contractLabel = row.contract_symbol || `Spot ${row.ticker || ''}`.trim()
  return { instrumentLabel, contractLabel }
}

function applyReplayQueryParams(
  params,
  { workflowFrom = 'portfolio', replaySource = '', replayTitle = '', replayStatus = '' } = {},
) {
  if (workflowFrom) {
    params.set('workflowFrom', String(workflowFrom).trim().toLowerCase())
  } else {
    params.delete('workflowFrom')
  }
  if (replaySource) {
    params.set('replaySource', String(replaySource).trim().toLowerCase())
  } else {
    params.delete('replaySource')
  }
  if (replayTitle) {
    params.set('replayTitle', String(replayTitle).trim())
  } else {
    params.delete('replayTitle')
  }
  if (replayStatus) {
    params.set('replayStatus', String(replayStatus).trim().toLowerCase())
  } else {
    params.delete('replayStatus')
  }
  return params
}

function buildDeskTickerUrl(ticker = '', options = {}) {
  const normalized = String(ticker || '').trim().toUpperCase()
  if (!normalized) return '/'
  const params = new URLSearchParams()
  params.set('ticker', normalized)
  applyReplayQueryParams(params, options)
  return `/?${params.toString()}`
}

function buildJournalReviewUrl({ repairView = 'open' } = {}) {
  const params = new URLSearchParams()
  if (repairView === 'completed') {
    params.set('journalRepairView', 'completed')
  }
  const query = params.toString()
  return `/journal${query ? `?${query}` : ''}`
}

function buildReviewLoopNotesUrl({
  ticker = '',
  completion = 'open',
  replaySource = '',
  replayTitle = '',
  replayStatus = '',
} = {}) {
  const params = new URLSearchParams()
  params.set('noteFocus', 'review-loop')
  params.set('noteTag', 'review-loop')
  params.set('noteCompletion', completion === 'completed' ? 'completed' : 'open')
  params.set('journalReturn', '1')
  applyReplayQueryParams(params, { workflowFrom: 'portfolio', replaySource, replayTitle, replayStatus })
  const normalizedTicker = String(ticker || '').trim().toUpperCase()
  if (normalizedTicker) {
    params.set('noteTicker', normalizedTicker)
  }
  return `/notes?${params.toString()}`
}

export default function PortfolioPage() {
  const navigate = useNavigate()
  const [portfolio, setPortfolio] = useState(null)
  const [equity, setEquity] = useState([])
  const [performance, setPerformance] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [lastUpdated, setLastUpdated] = useState('')
  const [syncingOrders, setSyncingOrders] = useState(false)
  const { preferences } = usePreferences()
  const { pushToast } = useToast()
  const savedBoardsNavigation = useKeyboardListNavigation({ selector: '.table-row-action', layout: 'list' })
  const boardReplayNavigation = useKeyboardListNavigation({ selector: '.table-row-action', layout: 'list' })
  const openTradesNavigation = useKeyboardListNavigation({ selector: '.table-row-action', layout: 'list' })

  usePageActionShortcuts({
    focusResult: () => focusFirstMatching(['.ui-list-table .table-row-action', '.metric-card-button']),
  })

  const load = useCallback(async () => {
    try {
      const [portfolioData, equityData, performanceData] = await Promise.all([
        getPortfolio(),
        getPortfolioEquity(),
        getPortfolioPerformance(),
      ])
      setPortfolio(portfolioData)
      setEquity(equityData.points || [])
      setPerformance(performanceData)
      setLastUpdated(new Date().toLocaleTimeString())
      setError('')
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load portfolio.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  usePolling(load, preferences?.pollingMs || 15000, autoRefresh)

  const handleSyncOrders = useCallback(async () => {
    try {
      setSyncingOrders(true)
      const payload = await syncPendingOrders()
      const processed = Number(payload?.summary?.processed || 0)
      const changed = Number(payload?.summary?.changed || 0)
      const fills = Number(payload?.summary?.filled || 0)
      if (processed < 1) {
        pushToast('No broker-backed working orders needed sync.', 'info')
      } else {
        pushToast(
          `Synced ${processed} working orders. ${changed} changed, ${fills} filled.`,
          changed > 0 || fills > 0 ? 'success' : 'info',
        )
      }
      await load()
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Failed to sync paper orders.', 'error')
    } finally {
      setSyncingOrders(false)
    }
  }, [load, pushToast])

  const summary = portfolio?.summary || {}
  const tradeSummary = portfolio?.trade_summary || {}
  const analytics = portfolio?.analytics || {}
  const riskDashboard = portfolio?.risk_dashboard || {}
  const openTrades = portfolio?.open_trades || []
  const monitoredTrades = portfolio?.monitored_open_trades || []
  const pendingOrders = portfolio?.pending_orders || []
  const validationSnapshot = portfolio?.validation_snapshot || {}
  const validationScorecards = Array.isArray(validationSnapshot.scorecards) ? validationSnapshot.scorecards : []
  const routeQuality = validationSnapshot.route_quality || {}
  const boardSnapshotHistory = validationSnapshot.board_snapshot_history || { count: 0, items: [] }
  const boardSnapshotItems = Array.isArray(boardSnapshotHistory.items) ? boardSnapshotHistory.items : []
  const replayComparisons = validationSnapshot.replay_comparisons || {}
  const boardOutcomeReplay = replayComparisons.board_outcomes || { count: 0, resolved_count: 0, open_count: 0, items: [] }
  const boardOutcomeItems = Array.isArray(boardOutcomeReplay.items) ? boardOutcomeReplay.items : []
  const paperLiveReplay = replayComparisons.paper_live_slippage || { count: 0, items: [] }
  const paperLiveItems = Array.isArray(paperLiveReplay.items) ? paperLiveReplay.items : []
  const capitalPreservationPolicy = useMemo(
    () =>
      buildCapitalPreservationPolicy({
        preferences,
        tradeTicket: null,
        defaults: {
          accountSize: preferences?.defaultAccountSize,
          riskPercent: preferences?.defaultRiskPercent,
        },
      }),
    [preferences],
  )
  const promotionGateSummary = useMemo(
    () =>
      buildPromotionGateSummary({
        validationSnapshot,
        policy: capitalPreservationPolicy.promotionGate,
      }),
    [capitalPreservationPolicy, validationSnapshot],
  )
  const intradayReview = useMemo(
    () =>
      buildIntradayReviewLens({
        tradingStyle: preferences?.tradingStyle,
        preferences,
        validationSnapshot,
        monitoredTrades,
        openTrades,
      }),
    [monitoredTrades, openTrades, preferences, validationSnapshot],
  )

  const instrumentMix = useMemo(() => {
    const listedOptions = openTrades.filter((row) => String(row.instrument_type || '').trim().toLowerCase() === 'listed_option').length
    const equityRows = openTrades.filter((row) => String(row.instrument_type || '').trim().toLowerCase() === 'equity').length
    const eventRiskRows = openTrades.filter((row) => Boolean(row.event_risk)).length
    const extendedHoursRows = openTrades.filter(
      (row) =>
        Boolean(row.extended_hours) ||
        String(row.time_in_force || '').trim().toLowerCase() === 'day_ext',
    ).length
    return {
      listedOptions,
      equityRows,
      eventRiskRows,
      extendedHoursRows,
    }
  }, [openTrades])

  const urgentActions = useMemo(
    () => monitoredTrades.filter((row) => String(row.monitor_action || '').trim().toUpperCase() !== 'HOLD').length,
    [monitoredTrades],
  )
  const priorityDeskTicker =
    monitoredTrades.find((row) => String(row?.monitor_action || '').trim().toUpperCase() !== 'HOLD')?.ticker ||
    boardOutcomeItems[0]?.leader_ticker ||
    boardSnapshotItems[0]?.leader_ticker ||
    openTrades[0]?.ticker ||
    ''
  const resolvedReplayTicker =
    boardOutcomeItems.find((item) => Boolean(item?.resolved_at))?.leader_ticker || ''
  const priorityDeskContext =
    monitoredTrades.find((row) => String(row?.monitor_action || '').trim().toUpperCase() !== 'HOLD')?.ticker
      ? {
          ticker: monitoredTrades.find((row) => String(row?.monitor_action || '').trim().toUpperCase() !== 'HOLD')?.ticker,
          replaySource: 'live_position',
          replayTitle: monitoredTrades.find((row) => String(row?.monitor_action || '').trim().toUpperCase() !== 'HOLD')?.latest_order_event?.label
            || monitoredTrades.find((row) => String(row?.monitor_action || '').trim().toUpperCase() !== 'HOLD')?.latest_order_event?.status
            || 'Open risk review',
          replayStatus: 'open',
        }
      : boardOutcomeItems[0]?.leader_ticker
        ? {
            ticker: boardOutcomeItems[0].leader_ticker,
            replaySource: 'board_replay',
            replayTitle: boardOutcomeItems[0].board_name || 'Board replay',
            replayStatus: boardOutcomeItems[0].resolved_at ? 'resolved' : 'open',
          }
        : boardSnapshotItems[0]?.leader_ticker
          ? {
              ticker: boardSnapshotItems[0].leader_ticker,
              replaySource: 'board_snapshot',
              replayTitle: boardSnapshotItems[0].board_name || boardSnapshotItems[0].name || 'Saved board',
              replayStatus: 'saved',
            }
          : openTrades[0]?.ticker
            ? {
                ticker: openTrades[0].ticker,
                replaySource: 'live_position',
                replayTitle: openTrades[0].contract_symbol || 'Open position',
                replayStatus: 'open',
              }
            : null
  const portfolioNextAction = urgentActions > 0
    ? {
        label: 'Open live risk on trades',
        onClick: () => navigate('/trades'),
      }
    : priorityDeskContext?.ticker
      ? {
          label: `Open ${String(priorityDeskContext.ticker).trim().toUpperCase()} on desk`,
          onClick: () => navigate(buildDeskTickerUrl(priorityDeskContext.ticker, priorityDeskContext)),
        }
      : {
          label: 'Open journal review',
          onClick: () => navigate(buildJournalReviewUrl()),
        }
  const portfolioRepairAction = {
    label:
      boardOutcomeReplay.open_count > 0
        ? 'Open repair notes'
        : resolvedReplayTicker
          ? 'Open resolved replay'
          : 'Open journal review',
    onClick: () =>
      navigate(
        boardOutcomeReplay.open_count > 0
          ? buildReviewLoopNotesUrl({
              ticker: boardOutcomeItems.find((item) => !item?.resolved_at)?.leader_ticker || priorityDeskTicker,
              completion: 'open',
              replaySource: 'board_replay',
              replayTitle: boardOutcomeItems.find((item) => !item?.resolved_at)?.board_name || 'Board replay',
              replayStatus: 'open',
            })
          : resolvedReplayTicker
            ? buildReviewLoopNotesUrl({
                ticker: resolvedReplayTicker,
                completion: 'completed',
                replaySource: 'board_replay',
                replayTitle: boardOutcomeItems.find((item) => Boolean(item?.resolved_at))?.board_name || 'Resolved board replay',
                replayStatus: 'resolved',
              })
            : buildJournalReviewUrl(),
      ),
  }

  const metricCards = useMemo(
    () => [
      {
        label: 'Realized PnL',
        value: formatDollars(summary.realized_pnl),
        tone: Number(summary.realized_pnl) >= 0 ? 'positive' : 'negative',
        helper: `${tradeSummary.closed_trades ?? 0} closed trades`,
      },
      {
        label: 'Unrealized PnL',
        value: formatDollars(summary.unrealized_pnl),
        tone: Number(summary.unrealized_pnl) >= 0 ? 'positive' : 'negative',
        helper: `${summary.active_trade_count ?? 0} active positions`,
      },
      {
        label: 'Open risk',
        value: formatDollars(riskDashboard.open_risk ?? summary.open_risk),
        tone: buildStatusTone(riskDashboard.status),
        helper: formatPercent(riskDashboard.risk_pct_of_account, { ratio: true }),
      },
      {
        label: 'Open cost',
        value: formatDollars(riskDashboard.open_cost ?? summary.open_cost),
        helper: formatPercent(riskDashboard.cost_pct_of_account, { ratio: true }),
      },
      {
        label: 'Urgent actions',
        value: urgentActions,
        tone: urgentActions > 0 ? 'negative' : 'positive',
        helper: `${pendingOrders.length} working orders`,
      },
      {
        label: 'Win rate',
        value: formatPercent((tradeSummary.win_rate ?? analytics.win_rate ?? 0) * 100),
        tone: Number(tradeSummary.win_rate ?? analytics.win_rate ?? 0) >= 0.5 ? 'positive' : 'neutral',
        helper: `${tradeSummary.wins ?? 0} wins / ${tradeSummary.losses ?? 0} losses`,
      },
    ],
    [summary, tradeSummary, analytics, riskDashboard, urgentActions, pendingOrders.length],
  )

  const riskRows = useMemo(
    () => [
      { label: 'Risk status', value: riskDashboard.status || 'OK' },
      { label: 'Risk budget used', value: formatPercent(riskDashboard.risk_pct_of_account, { ratio: true }) },
      { label: 'Capital deployed', value: formatPercent(riskDashboard.cost_pct_of_account, { ratio: true }) },
      { label: 'Listed options', value: instrumentMix.listedOptions },
      { label: 'Equity positions', value: instrumentMix.equityRows },
      { label: 'Event risk trades', value: instrumentMix.eventRiskRows },
      { label: 'Extended-hours exposure', value: instrumentMix.extendedHoursRows },
      { label: 'Working orders', value: pendingOrders.length },
    ],
    [riskDashboard, instrumentMix, pendingOrders.length],
  )

  const performanceRows = useMemo(
    () => [
      { label: 'Expectancy', value: formatDollars(performance?.expectancy ?? analytics.expectancy) },
      { label: 'Average winner', value: formatDollars(performance?.average_win ?? analytics.average_winner) },
      { label: 'Average loser', value: formatDollars(performance?.average_loss ?? analytics.average_loser) },
      { label: 'Profit factor', value: toNumber(performance?.profit_factor ?? analytics.profit_factor)?.toFixed(2) ?? '--' },
      { label: 'Best trade', value: formatDollars(analytics.best_trade) },
      { label: 'Worst trade', value: formatDollars(analytics.worst_trade) },
      { label: 'Current streak', value: performance?.streaks?.current ?? '--' },
      { label: 'Best win streak', value: performance?.streaks?.best_win ?? '--' },
    ],
    [performance, analytics],
  )

  const validationRouteRows = useMemo(
    () => [
      { label: 'Clean fills', value: routeQuality.clean_fill_count ?? 0 },
      { label: 'Slipped fills', value: routeQuality.slipped_fill_count ?? 0 },
      { label: 'Fragile fills', value: routeQuality.fragile_fill_count ?? 0 },
      { label: 'Rejected routes', value: routeQuality.rejected_route_count ?? 0 },
      { label: 'Partial fills', value: routeQuality.partial_fill_count ?? 0 },
      { label: 'Avg abs slippage', value: formatBasisPoints(routeQuality.average_abs_slippage_bps) },
      {
        label: 'Latest execution review',
        value: routeQuality.latest_execution_review
          ? `${routeQuality.latest_execution_review.ticker || '--'} - ${routeQuality.latest_execution_review.label || 'Review'}`
          : 'No saved review',
      },
    ],
    [routeQuality],
  )

  const monitorRows = useMemo(() => {
    const openLookup = new Map(openTrades.map((row) => [String(row.trade_id || ''), row]))
    return monitoredTrades.map((row, index) => {
      const tradeId = String(row.trade_id || '')
      const baseRow = openLookup.get(tradeId) || row
      const { instrumentLabel, contractLabel } = formatTradeCell(baseRow)
      return {
        key: tradeId || `${row.ticker || 'trade'}-${index}`,
        ticker: row.ticker || baseRow.ticker || '--',
        instrumentLabel,
        contractLabel,
        verdict: baseRow.verdict || '--',
        setupGrade: baseRow.setup_grade || '--',
        interval: baseRow.interval || '--',
        maxRiskLabel: formatDollars(baseRow.max_risk_dollars),
        positionCostLabel: formatDollars(baseRow.position_cost),
        targetLabel: formatPrice(baseRow.target_price),
        invalidationLabel: formatPrice(baseRow.invalidation_price),
        action: row.monitor_action || 'HOLD',
        actionTone: buildStatusTone(row.monitor_action),
        unrealizedLabel: formatDollars(row.unrealized_pnl),
        returnLabel: formatPercent(row.option_return_pct, { ratio: true }),
        currentPriceLabel: formatPrice(row.current_underlying ?? row.current_underlying_price),
        latestEvent: row.latest_order_event?.label || row.latest_order_event?.status || 'Desk-tracked',
        eventRisk: Boolean(baseRow.event_risk),
        orderDetailsParts: [
          baseRow.order_type ? String(baseRow.order_type).toUpperCase() : null,
          baseRow.time_in_force ? String(baseRow.time_in_force).toUpperCase() : null,
        ].filter(Boolean),
      }
    })
  }, [monitoredTrades, openTrades])

  if (loading) {
    return (
      <LoadingBlock
        label="Loading portfolio surface"
        detail="Refreshing live exposure, replay evidence, and working-order state so the book opens with current pressure."
      />
    )
  }

  return (
    <>
      {error ? (
        <ErrorState
          title="Portfolio surface unavailable"
          description={error}
          actionLabel="Reload portfolio"
          onAction={load}
        />
      ) : null}
      <PageIntro
        kicker="Portfolio risk"
        title="Read the live book before you read the PnL"
        description={
          intradayReview.active
            ? 'Track open risk, same-session cleanup pressure, route drift, and replay quality from one portfolio surface built for intraday decisions.'
            : 'Track open risk, active exposure, working orders, and recovery quality from one portfolio surface built for real desk decisions.'
        }
        helper={
          intradayReview.active
            ? 'Read this page in layers: live exposure first, then same-session replay, then working orders and monitored trades.'
            : 'Read this page in layers: live exposure first, then replay evidence, then working orders and monitored trades.'
        }
        badge={`Updated ${lastUpdated || '--'}`}
        actions={(
          <ActionBar compact>
            <Chip tone="neutral" size="sm">Shift+J jump to replay</Chip>
            <Button type="button" variant="ghost" onClick={handleSyncOrders} disabled={syncingOrders}>
              {syncingOrders ? 'Syncing paper orders...' : 'Sync paper orders'}
            </Button>
            <Button type="button" variant="subtle" onClick={load}>
              Refresh portfolio
            </Button>
          </ActionBar>
        )}
      />
      <StrategyDeskStatusPanel
        eyebrow="Quant desks"
        title="Desk allocator and exposure"
        subtitle="Use the shared desk snapshot to see which internal desks are publishing targets, how allocator risk compresses them, and whether research desks have moved into validated backtest coverage."
      />
      <WorkflowGuide
        showSteps={false}
        phaseLabel="Phase 3 - Act safely"
        phaseTone={urgentActions > 0 ? 'warning' : 'positive'}
        title={
          intradayReview.active
            ? 'Use portfolio to decide whether the desk can press, pause, or clean up the same-session book.'
            : 'Use portfolio to decide whether the desk can press, pause, or repair.'
        }
        description={
          intradayReview.active
            ? 'This page works best when exposure, same-session replay, and route quality turn into the next intraday action instead of sitting as passive PnL.'
            : 'This page works best when exposure, replay evidence, and route quality turn into the next action instead of sitting as passive PnL.'
        }
        steps={buildWorkflowSteps(2)}
        cards={[
          {
            label: 'Use this page for',
            value: intradayReview.active
              ? 'Read exposure, same-session replay, and route quality together.'
              : 'Read exposure, replay evidence, and route quality together.',
            detail: intradayReview.active
              ? 'Portfolio should tell you whether the same-session book supports more action or whether cleanup, fills, and replay drift need attention first.'
              : 'Portfolio should tell you whether the book supports more action or whether risk, fills, and replay drift need attention first.',
            actionLabel: 'Open trades',
            onAction: () => navigate('/trades'),
          },
          {
            label: 'Best next move',
            value: urgentActions > 0 ? 'Reduce live complexity before adding new decisions.' : 'Promote only what still looks clean after portfolio pressure.',
            detail: 'The next move should come from open risk, execution drift, and replay quality, not just from green PnL.',
            tone: 'positive',
            actionLabel: portfolioNextAction.label,
            onAction: portfolioNextAction.onClick,
          },
          {
            label: 'Do not ignore',
            value: intradayReview.active ? 'Green PnL does not clear the same-session repair loop.' : 'Portfolio green does not clear the repair loop.',
            detail: intradayReview.active
              ? 'If same-session replay, route drift, or cleanup pressure still look weak, the right move is review or repair, not a fresh route.'
              : 'If replay evidence, route drift, or open risk still look weak, the right move is review or repair, not a new route.',
            tone: 'warning',
            actionLabel: portfolioRepairAction.label,
            onAction: portfolioRepairAction.onClick,
          },
        ]}
      />
      <section className="metrics-grid metrics-grid--triple">
        {metricCards.map((item) => <MetricCard key={item.label} {...item} />)}
      </section>
      <EducationCallout
        topic="portfolio-risk"
        title={intradayReview.active ? 'Read same-session cleanup before you celebrate open PnL.' : 'Read open risk before you read open PnL.'}
        body={
          intradayReview.active
            ? 'This page should answer an intraday survival question first: how much same-session risk is still live, which routes drifted, and what still needs cleanup before the close.'
            : 'This page is meant to answer a survival question first: how much capital and downside are live right now, and which positions or orders need attention?'
        }
        bullets={intradayReview.active
          ? [
              'A green intraday book can still hide late cleanup pressure and route drift.',
              'The review loop should tell you what changes before the next opening range, not just whether today made money.',
            ]
          : [
              'Open risk is your live downside budget, not just a chart number.',
              'Urgent actions and working orders matter even when the book is green.',
            ]}
        linkLabel="Open portfolio guide"
      />
      {intradayReview.active ? (
        <SectionCard
          eyebrow="Same-session review"
          title="Intraday review loop"
          subtitle={intradayReview.guideDetail}
        >
          <section className="metrics-grid">
            {intradayReview.portfolioCards.map((item) => (
              <MetricCard key={item.label} {...item} />
            ))}
          </section>
        </SectionCard>
      ) : null}
      <section className="content-grid content-grid--wide">
        <SectionCard
          eyebrow="Live path"
          title="Equity curve"
          subtitle="Closed-trade replay with the current portfolio risk state."
          actions={(
            <ActionBar>
              <ToggleField
                label="Auto refresh"
                hint="Keep the book and curve polling during the session."
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
              />
              <StatusBadge value={`Risk ${riskDashboard.status || 'OK'}`} />
              <Chip tone="neutral" size="sm">Updated {lastUpdated || '--'}</Chip>
              <Button type="button" variant="ghost" onClick={load}>Refresh</Button>
            </ActionBar>
          )}
        >
          <EquityCurveChart points={equity} />
        </SectionCard>
        <SectionCard eyebrow="Exposure" title="Risk posture" subtitle="Account usage, exposure mix, and live portfolio pressure.">
          <div className="key-value-grid">
            {riskRows.map((item) => (
              <div className="key-value-row" key={item.label}>
                <span>{item.label}</span>
                <strong>{String(item.value)}</strong>
              </div>
            ))}
          </div>
        </SectionCard>
      </section>
      <section className="content-grid">
        <SectionCard eyebrow="Recovery" title="Performance breakdown" subtitle="Closed-trade performance and recovery profile.">
          <div className="key-value-grid">
            {performanceRows.map((item) => (
              <div className="key-value-row" key={item.label}>
                <span>{item.label}</span>
                <strong>{String(item.value)}</strong>
              </div>
            ))}
          </div>
        </SectionCard>
        <SectionCard eyebrow="Position mix" title="Active exposure" subtitle="What is live in the book right now.">
          <div className="key-value-grid">
            <div className="key-value-row"><span>Open trades</span><strong>{openTrades.length}</strong></div>
            <div className="key-value-row"><span>Pending orders</span><strong>{pendingOrders.length}</strong></div>
            <div className="key-value-row"><span>Listed options live</span><strong>{instrumentMix.listedOptions}</strong></div>
            <div className="key-value-row"><span>Equity live</span><strong>{instrumentMix.equityRows}</strong></div>
            <div className="key-value-row"><span>Urgent actions</span><strong>{urgentActions}</strong></div>
            <div className="key-value-row"><span>Event risk tags</span><strong>{instrumentMix.eventRiskRows}</strong></div>
          </div>
        </SectionCard>
      </section>
      <section className="content-grid content-grid--wide">
        <SectionCard
          eyebrow="Validation layer"
          title={intradayReview.active ? intradayReview.labels.replayEvidence : 'Replay evidence'}
          subtitle={
            intradayReview.active
              ? 'Closed-trade scorecards tied to same-session board leaders, event windows, fill drift, and cleanup discipline.'
              : 'Closed-trade scorecards tied to saved boards, event windows, execution quality, and simple benchmark checks.'
          }
          actions={(
            <ActionBar compact>
              <StatusBadge value={`${validationScorecards.length} scorecards`} />
              <Button type="button" variant="ghost" size="sm" onClick={() => navigate(buildJournalReviewUrl())}>
                Open journal review
              </Button>
              <Button type="button" variant="ghost" size="sm" onClick={portfolioRepairAction.onClick}>
                {portfolioRepairAction.label}
              </Button>
            </ActionBar>
          )}
        >
          <section className="metrics-grid">
            <MetricCard
              label="Paper gate"
              value={promotionGateSummary.label}
              tone={promotionGateSummary.tone}
              helper={promotionGateSummary.action}
            />
            <MetricCard
              label="Replay sample"
              value={`${promotionGateSummary.resolvedCount ?? 0} resolved`}
              tone={promotionGateSummary.tone === 'negative' ? 'negative' : promotionGateSummary.tone === 'warning' ? 'warning' : 'neutral'}
              helper={`${promotionGateSummary.openCount ?? 0} open | ${promotionGateSummary.winRateLabel || '--'} win`}
            />
            <MetricCard
              label="Live drift"
              value={promotionGateSummary.averageAbsSlippageLabel || '--'}
              tone={promotionGateSummary.tone === 'negative' ? 'negative' : promotionGateSummary.tone === 'warning' ? 'warning' : 'positive'}
              helper={`Worst ${promotionGateSummary.worstAbsSlippageLabel || '--'} | ${promotionGateSummary.policySummary || 'Policy unavailable'}`}
            />
          </section>
          <div className="chart-market-panel__footnote">
            {promotionGateSummary.basis}
          </div>
          <section className="metrics-grid">
            {validationScorecards.length ? validationScorecards.map((card) => (
              <MetricCard
                key={card.key || card.label}
                label={card.label || 'Validation'}
                value={card.value || '--'}
                tone={card.tone || 'neutral'}
                helper={card.helper || card.detail || ''}
              />
            )) : (
              <EmptyState
                title="No replay scorecards yet"
                description={
                  intradayReview.active
                    ? 'Start here by closing intraday trades and saving boards. This section turns them into same-session replay evidence.'
                    : 'Start here by closing reviewed trades and saving boards. This section turns them into replay evidence.'
                }
                actionLabel="Open trades"
                onAction={() => navigate('/trades')}
                secondaryActionLabel="Open watchlist"
                onSecondaryAction={() => navigate('/watchlist')}
              />
            )}
          </section>
        </SectionCard>
      <SectionCard
        eyebrow="Execution lens"
        title="Route quality"
          subtitle="Execution-quality rollup from closed trades with saved fill-review detail."
        >
          <div className="key-value-grid">
            {validationRouteRows.map((item) => (
              <div className="key-value-row" key={item.label}>
                <span>{item.label}</span>
                <strong>{String(item.value)}</strong>
              </div>
            ))}
          </div>
        </SectionCard>
      </section>
      <SectionCard
        eyebrow="Saved context"
        title={intradayReview.active ? intradayReview.labels.savedBoards : 'Saved board history'}
        subtitle={
          intradayReview.active
            ? 'Recent saved intraday boards that preserve which names were ready now, patience only, guarded, or cleanup biased.'
            : 'Recent saved candidate boards that preserve why names were promoted, reviewed, or stood down.'
        }
        actions={(
          <ActionBar compact>
            <StatusBadge value={`${boardSnapshotHistory.count ?? 0} saved`} />
            {boardSnapshotItems[0]?.leader_ticker ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() =>
                  navigate(
                    buildDeskTickerUrl(boardSnapshotItems[0].leader_ticker, {
                      workflowFrom: 'portfolio',
                      replaySource: 'board_snapshot',
                      replayTitle: boardSnapshotItems[0].board_name || boardSnapshotItems[0].name || 'Saved board',
                      replayStatus: 'saved',
                    }),
                  )
                }
              >
                Open leader on desk
              </Button>
            ) : null}
          </ActionBar>
        )}
      >
        <ListTable>
          <table
            ref={savedBoardsNavigation.containerRef}
            className="signal-table ui-list-table"
            onKeyDown={savedBoardsNavigation.onKeyDown}
          >
            <caption className="ui-visually-hidden">Saved board history leaders</caption>
            <thead>
              <tr>
                <th scope="col">Board</th>
                <th scope="col">Leader</th>
                <th scope="col">Mix</th>
                <th scope="col">Pressure</th>
                <th scope="col">Updated</th>
              </tr>
            </thead>
            <tbody>
              {boardSnapshotItems.length ? boardSnapshotItems.map((item, index) => (
                <tr key={item.id || `${item.name || 'snapshot'}-${index}`}>
                  <td>
                    <div className="ui-list-cell">
                      <div className="ui-list-cell__title">{item.board_name || item.name || 'Saved board'}</div>
                      <div className="ui-list-cell__meta">
                        {[item.page ? String(item.page).toUpperCase() : null, item.interval ? String(item.interval).toUpperCase() : null].filter(Boolean).join(' / ') || 'Saved workspace'}
                      </div>
                    </div>
                  </td>
                  <td>
                    <div className="ui-list-cell">
                      <div className="ui-list-cell__title">
                        {item.leader_ticker ? (
                          <button
                            type="button"
                            className="table-link table-row-action"
                            onClick={() =>
                              navigate(
                                buildDeskTickerUrl(item.leader_ticker, {
                                  workflowFrom: 'portfolio',
                                  replaySource: 'board_snapshot',
                                  replayTitle: item.board_name || item.name || 'Saved board',
                                  replayStatus: 'saved',
                                }),
                              )
                            }
                          >
                            {item.leader_ticker}
                          </button>
                        ) : '--'}
                      </div>
                      <div className="ui-list-cell__meta">
                        {item.leader_label || 'Leader snapshot'}
                        {item.leader_score !== null && item.leader_score !== undefined ? ` | ${Number(item.leader_score).toFixed(1)}` : ''}
                      </div>
                    </div>
                  </td>
                  <td>
                    <div className="ui-list-cell">
                      <div className="ui-list-cell__title">
                        {`${item.promote_count ?? 0} promote / ${item.review_count ?? 0} review / ${item.stand_down_count ?? 0} stand down`}
                      </div>
                      <div className="ui-list-cell__meta">{`${item.candidate_count ?? 0} candidates captured`}</div>
                    </div>
                  </td>
                  <td>
                    <div className="ui-list-cell">
                      <div className="ui-list-cell__title">{`${item.event_window_count ?? 0} event / ${item.fragile_execution_count ?? 0} fragile exec`}</div>
                      <div className="ui-list-cell__meta">{item.source || 'board artifact'}</div>
                    </div>
                  </td>
                  <td>{formatTimestamp(item.updated_at)}</td>
                </tr>
              )) : (
                <tr>
                  <td colSpan={5}>
                    <EmptyState
                        title={intradayReview.active ? 'No saved intraday boards yet' : 'No saved boards yet'}
                        description={
                          intradayReview.active
                            ? 'Start here by saving an intraday board from Watchlist or Compare. This section keeps the same-session board history.'
                            : 'Start here by saving a board from Watchlist or Compare. This section keeps the saved-board history.'
                        }
                      actionLabel="Open watchlist"
                      onAction={() => navigate('/watchlist')}
                      secondaryActionLabel="Open compare"
                      onSecondaryAction={() => navigate('/compare')}
                    />
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </ListTable>
      </SectionCard>
      <section className="content-grid content-grid--wide">
        <SectionCard
          eyebrow="Outcome replay"
          title={intradayReview.active ? intradayReview.labels.boardReplay : 'Board replay'}
          subtitle={
            intradayReview.active
              ? 'Compare saved intraday leaders against later same-session outcomes and track how expected fills drifted into live execution.'
              : 'Compare saved board leaders against later outcomes and track how expected fills drifted into live execution.'
          }
          actions={(
            <ActionBar compact>
              <StatusBadge value={`${boardOutcomeReplay.resolved_count ?? 0} resolved`} />
              <Chip tone="neutral" size="sm">{`${boardOutcomeReplay.open_count ?? 0} awaiting resolution`}</Chip>
              <Button type="button" variant="ghost" size="sm" onClick={() => navigate(buildJournalReviewUrl())}>
                Open journal review
              </Button>
            </ActionBar>
          )}
        >
          <ListTable>
            <table
              ref={boardReplayNavigation.containerRef}
              className="signal-table ui-list-table"
              onKeyDown={boardReplayNavigation.onKeyDown}
            >
              <caption className="ui-visually-hidden">Board replay comparisons</caption>
              <thead>
                <tr>
                  <th scope="col">Board leader</th>
                  <th scope="col">Outcome</th>
                  <th scope="col">Review</th>
                  <th scope="col">Saved / resolved</th>
                </tr>
              </thead>
              <tbody>
                {boardOutcomeItems.length ? boardOutcomeItems.map((item, index) => (
                  <tr key={`${item.leader_ticker || 'leader'}-${item.saved_at || index}`}>
                  <td>
                    <div className="ui-list-cell">
                      <div className="ui-list-cell__title">
                        {item.leader_ticker ? (
                          <button
                            type="button"
                            className="table-link table-row-action"
                            onClick={() =>
                              navigate(
                                buildDeskTickerUrl(item.leader_ticker, {
                                  workflowFrom: 'portfolio',
                                  replaySource: 'board_replay',
                                  replayTitle: item.board_name || 'Board replay',
                                  replayStatus: item.resolved_at ? 'resolved' : 'open',
                                }),
                              )
                            }
                          >
                            {item.leader_ticker}
                          </button>
                        ) : '--'}
                      </div>
                      <div className="ui-list-cell__meta">{item.board_name || 'Saved board'}</div>
                    </div>
                  </td>
                    <td>
                      <div className="ui-list-cell">
                        <div className="ui-list-cell__badges">
                          <StatusBadge value={item.status_label || 'Replay'} />
                        </div>
                        <div className="ui-list-cell__meta">
                          {item.result_label ? `${item.result_label} | ${formatDollars(item.pnl_dollars)}` : 'No resolved close yet'}
                        </div>
                      </div>
                    </td>
                    <td>
                      <div className="ui-list-cell">
                        <div className="ui-list-cell__title">{item.attribution_label || item.execution_review_label || 'Awaiting review'}</div>
                        <div className="ui-list-cell__meta">{item.detail || 'Replay detail will show up once the board leader resolves into a closed trade.'}</div>
                      </div>
                    </td>
                    <td>
                      <div className="ui-list-cell">
                        <div className="ui-list-cell__meta">Saved {formatTimestamp(item.saved_at)}</div>
                        <div className="ui-list-cell__meta">Resolved {formatTimestamp(item.resolved_at)}</div>
                      </div>
                    </td>
                  </tr>
                )) : (
                  <tr>
                    <td colSpan={4}>
                      <EmptyState
                        title={intradayReview.active ? 'No same-session replay yet' : 'No board replay yet'}
                        description={
                          intradayReview.active
                            ? 'Start here by saving an intraday board, then let a leader resolve into a closed trade so the same-session replay can begin.'
                            : 'Start here by saving a board, then let a leader resolve into a closed trade so the replay evidence can begin.'
                        }
                        actionLabel="Open journal review"
                        onAction={() => navigate(buildJournalReviewUrl())}
                        secondaryActionLabel="Open trades"
                        onSecondaryAction={() => navigate('/trades')}
                      />
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </ListTable>
        </SectionCard>
        <SectionCard
          eyebrow="Fill drift"
          title="Paper vs live slippage"
          subtitle="Expected route prices versus realized fill prices from closed trades that carried comparable fill records."
          actions={(
            <ActionBar compact>
              <Chip tone="neutral" size="sm">{`${paperLiveReplay.count ?? 0} comparable fills`}</Chip>
              <StatusBadge value={`Avg ${formatSignedBasisPoints(paperLiveReplay.average_signed_slippage_bps)}`} />
            </ActionBar>
          )}
        >
          <div className="key-value-grid">
            <div className="key-value-row"><span>Average signed drift</span><strong>{formatSignedBasisPoints(paperLiveReplay.average_signed_slippage_bps)}</strong></div>
            <div className="key-value-row"><span>Average absolute drift</span><strong>{formatBasisPoints(paperLiveReplay.average_abs_slippage_bps)}</strong></div>
            <div className="key-value-row"><span>Worst absolute drift</span><strong>{formatBasisPoints(paperLiveReplay.worst_abs_slippage_bps)}</strong></div>
          </div>
          <ListTable>
            <table className="signal-table ui-list-table">
              <caption className="ui-visually-hidden">Paper versus live slippage replay</caption>
              <thead>
                <tr>
                  <th scope="col">Ticker</th>
                  <th scope="col">Expected</th>
                  <th scope="col">Actual</th>
                  <th scope="col">Delta</th>
                  <th scope="col">Review</th>
                </tr>
              </thead>
              <tbody>
                {paperLiveItems.length ? paperLiveItems.map((item, index) => (
                  <tr key={`${item.ticker || 'fill'}-${item.closed_at || index}`}>
                    <td>{item.ticker || '--'}</td>
                    <td>{formatPrice(item.expected_fill_price)}</td>
                    <td>{formatPrice(item.actual_fill_price)}</td>
                    <td>
                      <div className="ui-list-cell">
                        <div className="ui-list-cell__title">{formatSignedBasisPoints(item.slippage_bps)}</div>
                        <div className="ui-list-cell__meta">{formatDollars(item.slippage_dollars)}</div>
                      </div>
                    </td>
                    <td>
                      <div className="ui-list-cell">
                        <div className="ui-list-cell__title">{item.execution_review_label || 'Fill review'}</div>
                        <div className="ui-list-cell__meta">{formatTimestamp(item.closed_at)}</div>
                      </div>
                    </td>
                  </tr>
                )) : (
                  <tr>
                    <td colSpan={5}>
                      <EmptyState
                        title="No comparable fill deltas"
                        description="Once expected and actual fill prices are both saved on closed trades, this section will quantify paper-vs-live drift."
                      />
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </ListTable>
        </SectionCard>
      </section>
      <section className="content-grid">
        <SectionCard eyebrow="History" title="Monthly PnL" subtitle="Aggregated by calendar month from closed trades.">
          <ListTable>
            <table className="signal-table ui-list-table">
              <caption className="ui-visually-hidden">Monthly portfolio performance summary</caption>
              <thead>
                <tr><th scope="col">Month</th><th scope="col">PnL</th><th scope="col">Trades</th><th scope="col">Wins</th><th scope="col">Losses</th></tr>
              </thead>
              <tbody>
                {(performance?.monthly || []).map((row) => (
                  <tr key={row.month}>
                    <td>{row.month}</td>
                    <td>{formatDollars(row.pnl)}</td>
                    <td>{row.trades}</td>
                    <td>{row.wins}</td>
                    <td>{row.losses}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ListTable>
        </SectionCard>
        <SectionCard eyebrow="Order book" title="Working orders" subtitle="Orders still consuming attention and routing risk.">
          <ListTable>
            <table className="signal-table ui-list-table">
              <caption className="ui-visually-hidden">Working portfolio orders</caption>
              <thead>
                <tr><th scope="col">Ticker</th><th scope="col">Instrument</th><th scope="col">Order</th><th scope="col">Fill</th><th scope="col">Status</th></tr>
              </thead>
              <tbody>
                {pendingOrders.length ? pendingOrders.map((row, index) => {
                  const { instrumentLabel, contractLabel } = formatTradeCell(row)
                  const brokerQty = Number(row.broker_qty ?? 0)
                  const brokerFilledQty = Number(row.broker_filled_qty ?? 0)
                  return (
                    <tr key={row.order_id || `${row.ticker}-${index}`}>
                      <td>{row.ticker || '--'}</td>
                      <td>
                        <div className="ui-list-cell">
                          <div className="ui-list-cell__kicker">{instrumentLabel}</div>
                          <div className="ui-list-cell__meta">{contractLabel}</div>
                        </div>
                      </td>
                      <td>
                        <div className="ui-list-cell">
                          <div className="ui-list-cell__title">
                            {[row.order_type ? String(row.order_type).toUpperCase() : null, row.time_in_force ? String(row.time_in_force).toUpperCase() : null].filter(Boolean).join(' / ') || '--'}
                          </div>
                          <div className="ui-list-cell__meta">
                            {row.broker_name ? `${row.broker_name} | ${row.broker_order_id || 'No broker id'}` : 'Desk-managed order'}
                          </div>
                        </div>
                      </td>
                      <td>
                        <div className="ui-list-cell">
                          <div className="ui-list-cell__title">
                            {Number.isFinite(brokerQty) && brokerQty > 0 ? `${brokerFilledQty}/${brokerQty}` : 'Awaiting first fill'}
                          </div>
                          <div className="ui-list-cell__meta">
                            {row.broker_filled_avg_price ? `Avg ${formatPrice(row.broker_filled_avg_price)}` : `Risk ${formatDollars(row.max_risk_dollars)}`}
                          </div>
                        </div>
                      </td>
                      <td>
                        <div className="ui-list-cell">
                          <div className="ui-list-cell__badges">
                            <StatusBadge value={row.broker_status || row.latest_order_event?.status || row.status || 'Pending'} />
                          </div>
                          <div className="ui-list-cell__meta">
                            {row.latest_order_event?.label || row.latest_order_event?.status || 'Working order'}
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
                        description="No working orders are queued right now."
                      />
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </ListTable>
        </SectionCard>
      </section>
      <SectionCard eyebrow="Live review" title="Monitored open trades" subtitle="Instrument-aware risk review for every live position still shaping the desk.">
        <ListTable>
          <table
            ref={openTradesNavigation.containerRef}
            className="signal-table ui-list-table"
            onKeyDown={openTradesNavigation.onKeyDown}
          >
            <caption className="ui-visually-hidden">Open trade exposure review</caption>
            <thead>
              <tr>
                <th scope="col">Trade</th>
                <th scope="col">Thesis</th>
                <th scope="col">Risk</th>
                <th scope="col">Action</th>
                <th scope="col">PnL</th>
              </tr>
            </thead>
            <tbody>
              {monitorRows.length ? monitorRows.map((row) => (
                <tr key={row.key}>
                  <td>
                    <div className="ui-list-cell">
                      <div className="ui-list-cell__kicker">{row.instrumentLabel}</div>
                      <div className="ui-list-cell__title">
                        {row.ticker && row.ticker !== '--' ? (
                          <button
                            type="button"
                            className="table-link table-row-action"
                            onClick={() =>
                              navigate(
                                buildDeskTickerUrl(row.ticker, {
                                  workflowFrom: 'portfolio',
                                  replaySource: 'live_position',
                                  replayTitle: row.contractLabel || row.instrumentLabel || 'Open position',
                                  replayStatus: 'open',
                                }),
                              )
                            }
                          >
                            {row.ticker}
                          </button>
                        ) : row.ticker}
                      </div>
                      <div className="ui-list-cell__meta">{row.contractLabel}</div>
                    </div>
                  </td>
                  <td>
                    <div className="ui-list-cell">
                      <div className="ui-list-cell__title">{row.verdict}</div>
                      <InlineMeta as="div" className="ui-list-cell__meta" items={[row.setupGrade, row.interval]} />
                      <div className="ui-list-cell__badges">
                        {row.eventRisk ? <StatusBadge value="Event risk" /> : null}
                        {row.orderDetailsParts.length ? <StatusBadge value={row.orderDetailsParts.join(' / ')} /> : null}
                      </div>
                    </div>
                  </td>
                  <td>
                    <div className="ui-list-cell">
                      <div className="ui-list-cell__title">Risk {row.maxRiskLabel}</div>
                      <div className="ui-list-cell__meta">Cost {row.positionCostLabel}</div>
                      <div className="ui-list-cell__stack">
                        <span>Target {row.targetLabel}</span>
                        <span>Invalidation {row.invalidationLabel}</span>
                      </div>
                    </div>
                  </td>
                  <td>
                    <div className="ui-list-cell">
                      <div className="ui-list-cell__badges">
                        <StatusBadge value={row.action} />
                      </div>
                      <div className="ui-list-cell__meta">{row.latestEvent}</div>
                    </div>
                  </td>
                  <td>
                    <div className="ui-list-cell">
                      <div className={`journal-pnl ${row.actionTone === 'negative' ? 'journal-pnl--negative' : row.unrealizedLabel.startsWith('-') ? 'journal-pnl--negative' : 'journal-pnl--positive'}`}>
                        {row.unrealizedLabel}
                      </div>
                      <div className="ui-list-cell__stack">
                        <span>Return {row.returnLabel}</span>
                        <span>Underlying {row.currentPriceLabel}</span>
                      </div>
                    </div>
                  </td>
                </tr>
              )) : (
                <tr>
                  <td colSpan={5}>
                    <EmptyState
                      title="No monitored open trades"
                      description="No monitored open trades are active right now."
                    />
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </ListTable>
      </SectionCard>
    </>
  )
}
