import axios from "axios"

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 30000,
  withCredentials: true,
})

const API_WARNING_WINDOW_MS = 15000
const TRADE_AUTOMATION_SAFETY_CACHE_TTL_MS = 15000
const apiWarningTimestamps = new Map()
let tradeAutomationSafetyStateCache = { value: null, expiresAt: 0 }
let tradeAutomationWatchdogCache = { value: null, expiresAt: 0 }

function unwrap(response) {
  return response.data.data
}

function cloneFallback(value) {
  return JSON.parse(JSON.stringify(value))
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function isRetriableApiError(error) {
  if (!error) return false
  const code = String(error.code || '').toUpperCase()
  if (code === 'ECONNREFUSED' || code === 'ECONNRESET' || code === 'ETIMEDOUT' || code === 'ERR_NETWORK') {
    return true
  }
  if (!error.response) return true
  const status = Number(error.response?.status || 0)
  return status >= 500
}

function warnApiFallback(key, error) {
  const warningKey = String(key || 'generic')
  const now = Date.now()
  const previous = apiWarningTimestamps.get(warningKey) || 0
  if (now - previous < API_WARNING_WINDOW_MS) {
    return
  }
  apiWarningTimestamps.set(warningKey, now)
  console.warn(`API fallback used (${warningKey}):`, error?.message || error)
}

function toNumber(value) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

function readSessionCache(key) {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.sessionStorage.getItem(key)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function writeSessionCache(key, value) {
  if (typeof window === 'undefined') return
  try {
    window.sessionStorage.setItem(key, JSON.stringify(value))
  } catch {
    // ignore session cache write failures
  }
}

function resolveDownloadFilename(response, fallback) {
  const header = response?.headers?.['content-disposition'] || ''
  const match = /filename=\"?([^\";]+)\"?/i.exec(header)
  return match?.[1] || fallback
}

async function safeRequest(requestFactory, fallback, options = {}) {
  const { key = 'generic', retries = 1, retryDelayMs = 300 } = options
  let lastError = null
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      return unwrap(await requestFactory())
    } catch (error) {
      lastError = error
      if (attempt < retries && isRetriableApiError(error)) {
        await sleep(retryDelayMs * (attempt + 1))
        continue
      }
      warnApiFallback(key, error)
      return cloneFallback(fallback)
    }
  }
  warnApiFallback(key, lastError)
  return cloneFallback(fallback)
}

async function strictRequest(requestFactory, options = {}) {
  const { retries = 0, retryDelayMs = 250 } = options
  let lastError = null
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      return unwrap(await requestFactory())
    } catch (error) {
      lastError = error
      if (attempt < retries && isRetriableApiError(error)) {
        await sleep(retryDelayMs * (attempt + 1))
        continue
      }
      throw error
    }
  }
  throw lastError
}

const FALLBACK_FILTERS = {
  intervals: ['1m', '5m', '15m', '30m', '1h', '4h', '1d'],
  directions: ['CALL', 'PUT'],
  journal_results: ['all', 'win', 'loss'],
  sort_fields: ['ranking_score', 'setup_score', 'verdict', 'trade_decision', 'ticker', 'live_price'],
  trade_actions: ['all', 'HOLD', 'TAKE PROFIT', 'STOP LOSS', 'DATA ISSUE'],
  compare_metrics: ['ranking_score', 'setup_score', 'probability_up', 'live_price'],
  alert_severities: ['all', 'critical', 'high', 'medium', 'low'],
  alert_sources: ['all', 'watchlist', 'trade_monitor', 'macro_calendar'],
  workspace_pages: ['all', 'dashboard', 'compare', 'watchlist', 'trades', 'journal', 'portfolio', 'alerts', 'activity', 'settings', 'notes', 'release'],
  activity_types: ['all', 'alert', 'workspace', 'portfolio'],
  note_statuses: ['all', 'active', 'archived'],
  note_priorities: ['all', 'high', 'medium', 'low'],
  note_sorts: ['updated_desc', 'updated_asc', 'created_desc', 'created_asc', 'priority', 'title', 'due_asc', 'due_desc'],
  note_types: ['all', 'general', 'trade_idea', 'risk_review', 'market_note', 'todo'],
  note_due_states: ['all', 'none', 'upcoming', 'today', 'overdue', 'completed'],
  note_completion_states: ['all', 'open', 'completed'],
  note_link_filters: ['all', 'yes', 'no'],
  note_checklist_states: ['all', 'none', 'open', 'done'],
  note_reminder_states: ['all', 'none', 'scheduled', 'today', 'due', 'upcoming'],
  note_recurrences: ['all', 'none', 'daily', 'weekly', 'weekdays', 'monthly'],
  note_blocked_states: ['all', 'ready', 'blocked'],
  note_progress_states: ['all', 'not_started', 'planned', 'in_progress', 'done'],
  note_bulk_actions: ['complete', 'reopen', 'archive', 'restore', 'pin', 'unpin', 'delete'],
  note_snooze_presets: [{ label: '30m', minutes: 30 }, { label: '2h', minutes: 120 }, { label: 'Tomorrow', minutes: 1440 }],
  ticker_hub: { favorite_limit: 24, recent_limit: 18 },
}

export const FALLBACK_BOOTSTRAP = {
  app: {
    name: 'Stock Options Signal Dashboard',
    tagline: 'Probability-driven trade ideas, live tracking, and portfolio intelligence.',
    environment: 'development',
    version: 'local',
    tenant_name: 'Systematic Equities Desk',
    logo_url: null,
    brand_settings: {},
    delivery_settings: {},
  },
  defaults: {
    default_scan_tickers: ['SPY', 'QQQ', 'AAPL', 'MSFT', 'NVDA', 'AMD', 'TSLA', 'META'],
    controlled_liquid_universe: ['SPY', 'QQQ', 'AAPL', 'MSFT', 'NVDA', 'AMD', 'META', 'AMZN', 'GOOGL', 'AVGO', 'TSLA', 'IWM'],
    supported_intervals: FALLBACK_FILTERS.intervals,
    default_interval: '5m',
    default_horizon: 5,
    live_update_seconds: 15,
  },
  alerts: { alerts: [], count: 0, total: 0 },
  presets: {
    scanner: { tickers: ['SPY', 'QQQ', 'AAPL', 'MSFT'], interval: '5m', horizon: 5, top_n: 8 },
    watchlist: { tickers: ['SPY', 'QQQ', 'AAPL', 'MSFT'], interval: '5m', horizon: 5, limit: 8, sort_by: 'ranking_score', descending: true },
  },
  watchlist_preview: {
    summary: {
      valid_trades: 0,
      high_conviction: 0,
      entry_now: 0,
      ranking_board: {
        board_name: 'Controlled liquid ranking board',
        leader: { ticker: 'SPY', ranking_label: 'Promote first' },
        promote_count: 2,
        review_count: 2,
        stand_down_count: 0,
        visible_count: 4,
      },
    },
    rows: ['SPY', 'QQQ', 'AAPL', 'MSFT'].map((ticker, index) => ({
      ticker,
      trade_decision: 'WATCH',
      verdict: 'Watching',
      conviction_label: 'FORMING',
      ranking_score: Math.max(78 - (index * 6.5), 52),
      ranking_label: index < 2 ? 'Promote first' : 'Reviewable',
      ranking_tier: index < 2 ? 'promote' : 'review',
      ranking_summary: `Bootstrap preview keeps ${ticker} on deck until the live board finishes loading.`,
      board_rank: index + 1,
      ranking_context: {
        board_name: 'Controlled liquid ranking board',
        board_short_name: 'Liquid board',
        controlled_universe: true,
        score: Math.max(78 - (index * 6.5), 52),
        tier: index < 2 ? 'promote' : 'review',
        tone: index < 2 ? 'positive' : 'warning',
        label: index < 2 ? 'Promote first' : 'Reviewable',
        summary: `Bootstrap preview rank for ${ticker}.`,
        component_summary: 'Bootstrap preview',
        board_rank: index + 1,
        board_gap: index === 0 ? 0 : Number((index * 1.4).toFixed(1)),
        leader: index === 0,
      },
      source: 'bootstrap-preview',
    })),
    results: ['SPY', 'QQQ', 'AAPL', 'MSFT'].map((ticker, index) => ({
      ticker,
      trade_decision: 'WATCH',
      verdict: 'Watching',
      conviction_label: 'FORMING',
      ranking_score: Math.max(78 - (index * 6.5), 52),
      ranking_label: index < 2 ? 'Promote first' : 'Reviewable',
      ranking_tier: index < 2 ? 'promote' : 'review',
      ranking_summary: `Bootstrap preview keeps ${ticker} on deck until the live board finishes loading.`,
      board_rank: index + 1,
      ranking_context: {
        board_name: 'Controlled liquid ranking board',
        board_short_name: 'Liquid board',
        controlled_universe: true,
        score: Math.max(78 - (index * 6.5), 52),
        tier: index < 2 ? 'promote' : 'review',
        tone: index < 2 ? 'positive' : 'warning',
        label: index < 2 ? 'Promote first' : 'Reviewable',
        summary: `Bootstrap preview rank for ${ticker}.`,
        component_summary: 'Bootstrap preview',
        board_rank: index + 1,
        board_gap: index === 0 ? 0 : Number((index * 1.4).toFixed(1)),
        leader: index === 0,
      },
      source: 'bootstrap-preview',
    })),
    count: 4,
    validation_artifact: {
      artifact_type: 'candidate_board_snapshot',
      source: 'bootstrap-preview',
      board_name: 'Controlled liquid ranking board',
      interval: '5m',
      horizon: 5,
      summary: {
        candidate_count: 4,
        leader_ticker: 'SPY',
      },
    },
    errors: [],
  },
  workspace_count: 0,
  ticker_hub: { favorites: [], recent: [], favorite_count: 0, recent_count: 0 },
}

const FALLBACK_CHART = {
  ticker: 'SPY',
  interval: '5m',
  period: '5d',
  extended_hours: true,
  point_count: 0,
  candles: [],
  overlays: {},
  available_indicators: [],
  freshness: {
    ticker: 'SPY',
    interval: '5m',
    status: 'unknown',
    warning: false,
    stale: false,
    feed_expected: false,
    session: 'unknown',
    session_label: 'Unknown',
    latest_bar_at: null,
    latest_bar_age_seconds: null,
    latest_bar_age_minutes: null,
    warning_threshold_seconds: 0,
    stale_threshold_seconds: 0,
    point_count: 0,
    source: 'chart',
    checked_at: null,
    checked_at_et: null,
    message: 'Market-data freshness is unavailable.',
  },
}

const FALLBACK_ORDER_EVENTS = { items: [], count: 0, status_counts: {} }

function buildFallbackDeskRow(ticker, index = 0) {
  const rankingScore = Math.max(82 - (index * 6), 48)
  const rankingTier = index <= 1 ? 'promote' : index <= 3 ? 'review' : 'stand_down'
  const rankingLabel = rankingTier === 'promote' ? 'Promote first' : rankingTier === 'stand_down' ? 'Stand down' : 'Reviewable'
  const boardGap = index === 0 ? null : Number((index * 1.8).toFixed(1))
  return {
    ticker,
    verdict: 'Watching',
    trade_decision: 'Monitor',
    monitor_action: 'Monitor',
    setup_score: null,
    ranking_score: rankingScore,
    ranking_label: rankingLabel,
    ranking_tier: rankingTier,
    ranking_summary: `Fallback board score ${rankingScore.toFixed(1)} under a controlled liquid basket.`,
    probability_up: null,
    live_price: null,
    close: null,
    current_underlying_price: null,
    target_price: null,
    entry_low_price: null,
    entry_high_price: null,
    stop_loss: null,
    stop_price: null,
    spread: null,
    bid_price: null,
    ask_price: null,
    contract_symbol: null,
    history: [],
    rank: index + 1,
    board_rank: index + 1,
    ranking_gap: boardGap,
    ranking_context: {
      board_name: 'Controlled liquid ranking board',
      board_short_name: 'Liquid board',
      controlled_universe: true,
      score: rankingScore,
      tier: rankingTier,
      tone: rankingTier === 'promote' ? 'positive' : rankingTier === 'stand_down' ? 'negative' : 'warning',
      label: rankingLabel,
      summary: `Fallback ranking keeps ${ticker} in the ${rankingLabel.toLowerCase()} bucket until live data is available.`,
      component_summary: 'Fallback score only',
      board_rank: index + 1,
      board_gap: boardGap,
      leader: index === 0,
    },
    source: 'client-fallback',
  }
}

const FALLBACK_DESK_ROWS = FALLBACK_BOOTSTRAP.defaults.default_scan_tickers
  .slice(0, 6)
  .map((ticker, index) => buildFallbackDeskRow(ticker, index))

const FALLBACK_EVENT_CALENDAR = {
  count: 2,
  total: 2,
  items: [
    {
      key: 'macro:FOMC:2026-05-06',
      source: 'macro_calendar',
      kind: 'rates',
      title: 'FOMC',
      ticker: '',
      event_date: '2026-05-06',
      days_until: 18,
      impact: 'high',
      tone: 'warning',
      label: 'Macro window',
      detail: 'FOMC is scheduled for 2026-05-06.',
    },
    {
      key: 'macro:CPI:2026-05-12',
      source: 'macro_calendar',
      kind: 'inflation',
      title: 'CPI',
      ticker: '',
      event_date: '2026-05-12',
      days_until: 24,
      impact: 'high',
      tone: 'warning',
      label: 'Macro window',
      detail: 'CPI is scheduled for 2026-05-12.',
    },
  ],
  summary: {
    macro_count: 2,
    ticker_count: 0,
    high_impact_count: 2,
    caution_count: 2,
    next_item: {
      key: 'macro:FOMC:2026-05-06',
      source: 'macro_calendar',
      kind: 'rates',
      title: 'FOMC',
      ticker: '',
      event_date: '2026-05-06',
      days_until: 18,
      impact: 'high',
      tone: 'warning',
      label: 'Macro window',
      detail: 'FOMC is scheduled for 2026-05-06.',
    },
    board_label: 'Macro and catalyst calendar',
  },
}

export const FALLBACK_DASHBOARD = {
  health: { status: 'degraded', service: 'Stock Options Signal Dashboard', version: 'local' },
  defaults: FALLBACK_BOOTSTRAP.defaults,
  scan: {
    interval: '5m',
    horizon: 5,
    tickers_requested: FALLBACK_BOOTSTRAP.defaults.default_scan_tickers.slice(0, 6),
    result_count: FALLBACK_DESK_ROWS.length,
    results: FALLBACK_DESK_ROWS,
    errors: [],
    source: 'client-fallback',
  },
  watchlist: {
    summary: {
      valid_trades: 0,
      high_conviction: 0,
      entry_now: 0,
      ranking_board: {
        board_name: 'Controlled liquid ranking board',
        count: FALLBACK_DESK_ROWS.length,
        promote_count: FALLBACK_DESK_ROWS.filter((row) => row.ranking_tier === 'promote').length,
        review_count: FALLBACK_DESK_ROWS.filter((row) => row.ranking_tier === 'review').length,
        stand_down_count: FALLBACK_DESK_ROWS.filter((row) => row.ranking_tier === 'stand_down').length,
        controlled_universe_count: FALLBACK_DESK_ROWS.length,
        coverage_ratio: 1,
        leader: FALLBACK_DESK_ROWS[0],
        visible_count: FALLBACK_DESK_ROWS.length,
      },
    },
    rows: FALLBACK_DESK_ROWS,
    results: FALLBACK_DESK_ROWS,
    count: FALLBACK_DESK_ROWS.length,
    errors: [],
    source: 'client-fallback',
  },
  event_calendar: FALLBACK_EVENT_CALENDAR,
  review_loop_notes: {
    items: [],
    count: 0,
    tags: [],
    tickers: [],
    owners: [],
  },
  review_loop_progress: {
    open_count: 0,
    resolved_count: 0,
    latest_resolved: null,
  },
  portfolio: {
    summary: {},
    trade_summary: {},
    capital_preservation: {
      today_realized_pnl: 0,
      today_closed_trades: 0,
      consecutive_losses: 0,
      open_position_count: 0,
      pending_order_count: 0,
      active_ticket_count: 0,
    },
    analytics: {},
    risk_dashboard: {},
    open_trades: [],
    pending_orders: [],
    closed_trades: [],
    monitored_open_trades: [],
    order_events: FALLBACK_ORDER_EVENTS,
  },
}

export function createFallbackDashboard() {
  return cloneFallback(FALLBACK_DASHBOARD)
}

async function buildLiveBatchDashboardFallback() {
  const tickers = FALLBACK_BOOTSTRAP.defaults.default_scan_tickers.slice(0, 6)
  try {
    const livePayload = unwrap(await api.post('/market/live/batch', { tickers }))
    const liveRows = Array.isArray(livePayload?.rows) ? livePayload.rows : []
    const priceMap = livePayload?.prices && typeof livePayload.prices === 'object' ? livePayload.prices : {}
    const rows = tickers.map((ticker, index) => {
      const liveRow = liveRows.find((row) => String(row?.ticker || '').trim().toUpperCase() === ticker) || null
      const mappedPrice = priceMap[ticker]
      const livePrice =
        toNumber(liveRow?.live_price ?? liveRow?.price ?? mappedPrice?.live_price ?? mappedPrice?.price ?? mappedPrice)
      const bidPrice = toNumber(liveRow?.bid_price ?? mappedPrice?.bid_price)
      const askPrice = toNumber(liveRow?.ask_price ?? mappedPrice?.ask_price)
      return {
        ...buildFallbackDeskRow(ticker, index),
        live_price: livePrice,
        close: livePrice,
        current_underlying_price: livePrice,
        bid_price: bidPrice,
        ask_price: askPrice,
        spread:
          bidPrice !== null && askPrice !== null
            ? Math.max(askPrice - bidPrice, 0)
            : toNumber(liveRow?.spread ?? mappedPrice?.spread),
        last_trade_at: liveRow?.timestamp ?? mappedPrice?.timestamp ?? null,
        source: 'live-batch-fallback',
      }
    })

    return {
      ...createFallbackDashboard(),
      scan: {
        interval: FALLBACK_BOOTSTRAP.defaults.default_interval,
        horizon: FALLBACK_BOOTSTRAP.defaults.default_horizon,
        tickers_requested: tickers,
        result_count: rows.length,
        results: rows,
        errors: [],
        source: 'live-batch-fallback',
      },
      watchlist: {
        summary: {
          valid_trades: 0,
          high_conviction: 0,
          entry_now: 0,
          ranking_board: {
            board_name: 'Controlled liquid ranking board',
            count: rows.length,
            promote_count: rows.filter((row) => row.ranking_tier === 'promote').length,
            review_count: rows.filter((row) => row.ranking_tier === 'review').length,
            stand_down_count: rows.filter((row) => row.ranking_tier === 'stand_down').length,
            controlled_universe_count: rows.length,
            coverage_ratio: 1,
            leader: rows[0] || null,
            visible_count: rows.length,
          },
        },
        rows,
        results: rows,
        count: rows.length,
        errors: [],
        source: 'live-batch-fallback',
      },
      event_calendar: FALLBACK_EVENT_CALENDAR,
    }
  } catch {
    return null
  }
}
const FALLBACK_OPEN_TRADES = { open_trades: [], monitor: [], count: 0, total: 0, limit: 250, offset: 0, action_filter: 'all', order_events: FALLBACK_ORDER_EVENTS }
const FALLBACK_PORTFOLIO = {
  summary: {},
  trade_summary: {},
  broker_account: {
    provider: 'alpaca_paper',
    label: 'Paper account',
    connected: false,
    status: 'unavailable',
    detail: '',
    equity: null,
    cash: null,
    portfolio_value: null,
    buying_power: null,
    position_market_value: null,
    daytrade_count: null,
    pattern_day_trader: null,
    position_count: 0,
    positions: [],
    last_updated_at: null,
  },
  broker_reconciliation: {
    performed: false,
    reconciled_open_trades: 0,
    items: [],
  },
  broker_pending_sync: { processed: 0, changed: 0 },
  capital_preservation: {
    today_realized_pnl: 0,
    today_closed_trades: 0,
    consecutive_losses: 0,
    open_position_count: 0,
    pending_order_count: 0,
    active_ticket_count: 0,
  },
  analytics: {},
  risk_dashboard: {},
  open_trades: [],
  pending_orders: [],
  closed_trades: [],
  monitored_open_trades: [],
  order_events: FALLBACK_ORDER_EVENTS,
  validation_snapshot: {
    scorecards: [],
    route_quality: {
      clean_fill_count: 0,
      slipped_fill_count: 0,
      fragile_fill_count: 0,
      rejected_route_count: 0,
      partial_fill_count: 0,
      average_abs_slippage_bps: null,
      latest_execution_review: null,
    },
    board_snapshot_history: {
      count: 0,
      items: [],
    },
    replay_comparisons: {
      board_outcomes: {
        count: 0,
        resolved_count: 0,
        open_count: 0,
        items: [],
      },
      paper_live_slippage: {
        count: 0,
        average_signed_slippage_bps: null,
        average_abs_slippage_bps: null,
        worst_abs_slippage_bps: null,
        items: [],
      },
    },
  },
}
const FALLBACK_JOURNAL = {
  journal: [],
  replay: [],
  count: 0,
  total: 0,
  limit: 100,
  offset: 0,
  result_filter: 'all',
  direction_filter: 'all',
  attribution_filter: 'all',
  validation_snapshot: {
    scorecards: [],
    route_quality: {
      clean_fill_count: 0,
      slipped_fill_count: 0,
      fragile_fill_count: 0,
      rejected_route_count: 0,
      partial_fill_count: 0,
      average_abs_slippage_bps: null,
      latest_execution_review: null,
    },
    board_snapshot_history: {
      count: 0,
      items: [],
    },
    replay_comparisons: {
      board_outcomes: {
        count: 0,
        resolved_count: 0,
        open_count: 0,
        items: [],
      },
      paper_live_slippage: {
        count: 0,
        average_signed_slippage_bps: null,
        average_abs_slippage_bps: null,
        worst_abs_slippage_bps: null,
        items: [],
      },
    },
  },
}
const FALLBACK_ALERTS = { alerts: [], count: 0, total: 0 }
const FALLBACK_WORKSPACES = { items: [], count: 0, page_counts: {}, top_tags: [], pinned_count: 0, tag_count: 0 }
const FALLBACK_ACTIVITY = { items: [], count: 0, workspace_count: 0, alert_count: 0 }
const FALLBACK_TICKER_HUB = { favorites: [], recent: [], favorite_count: 0, recent_count: 0 }
const FALLBACK_ORGS = { items: [], count: 0 }
const FALLBACK_BILLING = {
  tenant: { name: 'Systematic Equities Desk', slug: 'systematic-equities', plan_key: 'pro', billing_email: 'demo@example.test', status: 'active' },
  plan: {
    key: 'pro',
    name: 'Pro',
    monthly_price: 299,
    annual_price: 2990,
    seats_label: 'Up to 5 members',
    tagline: 'Assisted live trading with approval required for every order.',
    live_mode: 'Assisted live trading',
  },
  subscription: { provider: 'internal-demo', status: 'active', plan_key: 'pro', managed_mode: 'demo' },
  entitlements: { items: [], count: 0, enabled_count: 0 },
  usage: { members: { used: 1, limit: '5', remaining: 4 }, workspaces: { used: 0, limit: '10', remaining: 10 }, layouts: { used: 0, limit: '50', remaining: 50 } },
  sync: {
    status: 'demo',
    message: 'Billing is running in demo/manual mode for this tenant.',
    provider: 'internal-demo',
    last_event_key: null,
    last_event_at: null,
    last_processed_at: null,
    last_failed_at: null,
    recent_failure_count: 0,
    duplicate_count: 0,
    needs_reconciliation: false,
    available_actions: ['sync_entitlements'],
  },
  events: { items: [], count: 0, status_counts: {} },
  recovery: {
    enabled: true,
    failed_event_count: 0,
    latest_failed_event_id: null,
    latest_failed_event_at: null,
    last_reconciled_at: null,
    last_recovery_action: null,
    last_recovery_status: null,
    last_recovery_error: null,
    available_actions: ['reconcile', 'sync_entitlements'],
    pending_job_count: 0,
    attention_count: 0,
    jobs: {
      summary: {
        count: 0,
        queued: 0,
        retrying: 0,
        running: 0,
        succeeded: 0,
        dead_letter: 0,
        pending: 0,
        oldest_pending_at: null,
        recent_failure_count: 0,
        last_finished_at: null,
      },
      job_types: [],
      recent_jobs: [],
      recent_failures: [],
      dead_letters: [],
    },
    recent_jobs: [],
    failed_events: [],
  },
  portal: { available: false, mode: 'demo', message: 'Billing portal is not configured yet.' },
  checkout: { configured: false, mode: 'demo', success_url: null, cancel_url: null },
}
const FALLBACK_ONBOARDING = {
  tenant: { name: 'Systematic Equities Desk', slug: 'systematic-equities', status: 'active', plan_key: 'pro' },
  steps: [],
  completed_count: 0,
  count: 0,
  progress_percent: 0,
  workspace_count: 0,
  template_summary: { enabled: false, applied_count: 0, limit: '0', remaining: 0 },
}
const FALLBACK_TEMPLATES = {
  tenant: { name: 'Systematic Equities Desk', slug: 'systematic-equities', status: 'active', plan_key: 'pro' },
  templates: {
    enabled: false,
    limit: '0',
    source: 'plan',
    count: 0,
    applied_count: 0,
    remaining: 0,
    release_channels_enabled: false,
    items: [],
  },
}
const FALLBACK_API_TOKENS = {
  tenant: { name: 'Systematic Equities Desk', slug: 'systematic-equities', status: 'active', plan_key: 'pro' },
  tokens: {
    enabled: false,
    limit: null,
    source: 'plan',
    count: 0,
    active_count: 0,
    revoked_count: 0,
    expired_count: 0,
    remaining: null,
    scope_catalog: [
      { key: 'tenant.read', label: 'Tenant read', description: 'Read tenant settings, onboarding, and support state.' },
      { key: 'market.read', label: 'Market read', description: 'Call dashboard, chart, scan, compare, and watchlist APIs.' },
      { key: 'workspace.write', label: 'Workspace write', description: 'Create, import, duplicate, and update saved workspaces.' },
      { key: 'tenant.admin', label: 'Tenant admin', description: 'Manage branding, rollout, onboarding, and delivery controls.' },
    ],
    items: [],
  },
}
const FALLBACK_API_USAGE = {
  tenant: { name: 'Systematic Equities Desk', slug: 'systematic-equities', status: 'active', plan_key: 'pro' },
  summary: { total_requests: 0, last_request_at: null, route_group_count: 0, token_count: 0, last_14d_requests: 0, last_24h_requests: 0 },
  route_groups: [],
  methods: [],
  status_buckets: [],
  tokens: [],
  daily: [],
  recent: [],
}
const FALLBACK_WEBHOOKS = {
  tenant: { name: 'Systematic Equities Desk', slug: 'systematic-equities', status: 'active', plan_key: 'pro' },
  webhooks: {
    enabled: false,
    api_access_enabled: false,
    limit: '0',
    source: 'plan',
    count: 0,
    active_count: 0,
    remaining: 0,
    event_catalog: [],
    items: [],
    deliveries: [],
    jobs: {
      summary: {
        count: 0,
        queued: 0,
        retrying: 0,
        running: 0,
        succeeded: 0,
        dead_letter: 0,
        pending: 0,
        oldest_pending_at: null,
        recent_failure_count: 0,
        last_finished_at: null,
      },
      job_types: [],
      recent_jobs: [],
      recent_failures: [],
      dead_letters: [],
    },
  },
}
const FALLBACK_SECURITY = {
  tenant: { name: 'Systematic Equities Desk', slug: 'systematic-equities', status: 'active', plan_key: 'pro' },
  summary: {
    status: 'healthy',
    critical_count: 0,
    warning_count: 0,
    active_admin_tokens: 0,
    stale_tokens: 0,
    expiring_tokens: 0,
    failed_webhooks: 0,
    dead_letter_jobs: 0,
    auth_launch_blockers: 0,
    last_security_event_at: null,
  },
  tokens: {
    enabled: false,
    count: 0,
    active_count: 0,
    revoked_count: 0,
    expired_count: 0,
    admin_scope_count: 0,
    unused_active_count: 0,
    stale_active_count: 0,
    expiring_soon_count: 0,
    oldest_active_created_at: null,
    next_expiring_at: null,
    last_token_use_at: null,
    risk_items: [],
  },
  webhooks: {
    enabled: false,
    count: 0,
    active_count: 0,
    paused_count: 0,
    failed_delivery_count: 0,
    retrying_count: 0,
    dead_letter_count: 0,
    last_failure_at: null,
    risk_items: [],
  },
  auth: {
    configured: false,
    provider: 'none',
    auth_policy: 'default',
    preferred_provider: 'default',
    provider_record_count: 0,
    provider_domain_count: 0,
    provider_health: { ready: 0, unchecked: 0, incomplete: 0, error: 0, pending: 0 },
    launch_ready: true,
    launch_blockers: [],
    last_ready_at: null,
    last_failed_at: null,
    next_action: 'Security posture unavailable.',
    risk_items: [],
  },
  rate_limits: {
    enabled: false,
    throttle_event_count: 0,
    blocked_actor_count: 0,
    auth_lockout_count: 0,
    abuse_failure_count: 0,
    last_throttle_at: null,
    last_abuse_event_at: null,
    recent_events: [],
    recent_abuse: [],
    blocked_actors: [],
    risk_items: [],
  },
  audit: {
    count: 0,
    items: [],
    event_type_counts: [],
    last_event_at: null,
  },
  risk_items: [],
}
const FALLBACK_SUPPORT = {
  tenant: { name: 'Systematic Equities Desk', slug: 'systematic-equities', status: 'active', plan_key: 'pro', brand_settings: {} },
  status: 'active',
  billing: FALLBACK_BILLING,
  onboarding: FALLBACK_ONBOARDING,
  memberships: { items: [], count: 0 },
  invitations: { items: [], count: 0 },
  timeline: { items: [], count: 0 },
  support_actions: {
    can_pause: false,
    can_resume: false,
    can_manage_members: false,
    role_options: [
      { key: 'viewer', label: 'Viewer', assignable: true },
      { key: 'analyst', label: 'Analyst', assignable: true },
      { key: 'trader', label: 'Trader', assignable: true },
      { key: 'admin', label: 'Admin', assignable: true },
      { key: 'owner', label: 'Owner', assignable: false },
    ],
  },
}
const FALLBACK_DELIVERY = {
  tenant: { name: 'Systematic Equities Desk', slug: 'systematic-equities', status: 'active', plan_key: 'pro' },
  delivery: {
    custom_domains: {
      enabled: false,
      limit: '0',
      source: 'plan',
      configured: false,
      count: 0,
      limit_reached: false,
      primary_domain: null,
      domains: [],
      secondary_domains: [],
      domain_status: 'draft',
      verification_host: null,
      verification_value: null,
      verified_at: null,
      live_at: null,
      dns_records: [],
      checklist: [],
      next_action: 'Add a primary domain',
      actions: { request_verification: false, mark_verified: false, activate_live: false, reset_domain: false },
    },
    branded_email: {
      enabled: false,
      limit: '0',
      source: 'plan',
      configured: false,
      provider_key: 'none',
      provider_label: 'Not configured',
      provider_status: 'draft',
      template_set_name: null,
      release_channel: 'stable',
      sender_name: null,
      sender_email: null,
      reply_to_email: null,
      mail_from_subdomain: null,
      mail_from_domain: null,
      email_signature: null,
      preview_from: null,
      last_test_at: null,
      dns_records: [],
      checklist: [],
      next_action: 'Select an email provider',
      actions: { send_test: false, reset_sender: false },
    },
  },
}
const FALLBACK_ANALYTICS = {
  tenant: { name: 'Systematic Equities Desk', slug: 'systematic-equities', status: 'active', plan_key: 'pro' },
  summary: {
    adoption_score: 0,
    rollout_readiness: 0,
    activation_stage: 'Provisioning',
    member_count: 0,
    workspace_count: 0,
    recent_activity_count: 0,
    enabled_flag_count: 0,
    override_count: 0,
    last_activity_at: null,
  },
  plan: FALLBACK_BILLING.plan,
  usage: FALLBACK_BILLING.usage,
  onboarding: { progress_percent: 0, completed_count: 0, count: 0 },
  flag_summary: { count: 0, enabled_count: 0, override_count: 0 },
  rollout_funnel: [],
  recent_activity: { items: [], count: 0 },
}
const FALLBACK_FEATURE_FLAGS = {
  tenant: { name: 'Systematic Equities Desk', slug: 'systematic-equities', status: 'active', plan_key: 'pro' },
  items: [],
  count: 0,
  enabled_count: 0,
  override_count: 0,
  custom_count: 0,
}
const FALLBACK_DESK_SUMMARIES = {
  items: [
    {
      tenant_slug: 'systematic-equities',
      tenant_name: 'Systematic Equities Desk',
      paper_account_status: 'not_linked',
      live_account_status: 'not_linked',
      open_trades: 0,
      pending_orders: 0,
      alerts: 0,
      last_activity_at: null,
    },
    {
      tenant_slug: 'stat-arb',
      tenant_name: 'Stat Arb Desk',
      paper_account_status: 'not_linked',
      live_account_status: 'not_linked',
      open_trades: 0,
      pending_orders: 0,
      alerts: 0,
      last_activity_at: null,
    },
    {
      tenant_slug: 'macro',
      tenant_name: 'Macro Desk',
      paper_account_status: 'not_linked',
      live_account_status: 'not_linked',
      open_trades: 0,
      pending_orders: 0,
      alerts: 0,
      last_activity_at: null,
    },
  ],
  count: 3,
}
const FALLBACK_NOTES = { items: [], count: 0 }
const FALLBACK_NOTE_SUMMARY = {
  active_count: 0,
  overdue_count: 0,
  high_priority_count: 0,
  due_soon_count: 0,
  linked_count: 0,
  checklist_open_count: 0,
  reminder_due_count: 0,
  reminder_next_24h_count: 0,
  recurring_count: 0,
  blocked_count: 0,
  ready_count: 0,
  orphan_dependency_count: 0,
  in_progress_count: 0,
  total_estimate_minutes: 0,
  total_spent_minutes: 0,
  review_loop_summary: {
    open_count: 0,
    resolved_count: 0,
    latest_resolved: null,
  },
}

export async function getHealth() { return unwrap(await api.get('/health')) }
export async function probeBackendHealthz(options = {}) {
  const timeout = Number(options.timeoutMs || 5000)
  try {
    const response = await api.get('/healthz', {
      timeout,
      validateStatus: () => true,
    })
    const status = Number(response?.status || 0)
    const payload = response?.data && typeof response.data === 'object' ? response.data : null
    return {
      ok: status === 200 && String(payload?.status || '').toLowerCase() === 'ok',
      status,
      payload,
    }
  } catch (error) {
    return { ok: false, status: null, payload: null, error }
  }
}
export async function getBootstrap(consumer = 'full') {
  return safeRequest(
    () => api.get('/frontend/bootstrap', { params: { consumer } }),
    FALLBACK_BOOTSTRAP,
    { key: `bootstrap:${consumer}`, retries: 3, retryDelayMs: 250 },
  )
}
export async function getAuthConfig() {
  return strictRequest(() => api.get('/auth/config'), { retries: 3, retryDelayMs: 250 })
}
export async function getAuthEntry({ organizationSlug = '', tenantSlug = '', inviteToken = '', redirectPath = '', email = '' } = {}) {
  const resolvedOrganizationSlug = organizationSlug || tenantSlug
  return unwrap(await api.get('/auth/entry', {
    params: {
      tenant_slug: resolvedOrganizationSlug || undefined,
      invite_token: inviteToken || undefined,
      redirect_path: redirectPath || undefined,
      email: email || undefined,
    },
  }))
}
export async function getAuthSession() {
  return strictRequest(() => api.get('/auth/session'), { retries: 3, retryDelayMs: 250 })
}
export async function login(payload) { return unwrap(await api.post('/auth/login', payload)) }
export async function startProviderLogin({
  provider = '',
  providerRecordId = '',
  organizationSlug = '',
  tenantSlug = '',
  inviteToken = '',
  redirectPath = '',
  email = '',
} = {}) {
  const resolvedOrganizationSlug = organizationSlug || tenantSlug
  return unwrap(await api.post('/auth/start', null, {
    params: {
      provider: provider || undefined,
      provider_record_id: providerRecordId || undefined,
      tenant_slug: resolvedOrganizationSlug || undefined,
      invite_token: inviteToken || undefined,
      redirect_path: redirectPath || undefined,
      email: email || undefined,
    },
  }))
}
export async function logout() { return unwrap(await api.post('/auth/logout')) }
export async function getOrganizations() { return strictRequest(() => api.get('/orgs')) }
export async function createOrganization(payload) { return unwrap(await api.post('/orgs', payload)) }
export async function activateOrganization(organizationSlug) { return unwrap(await api.post('/orgs/activate', { tenant_slug: organizationSlug })) }
export async function getDeskSummaries() {
  return safeRequest(() => api.get('/me/desk-summaries'), FALLBACK_DESK_SUMMARIES, { key: 'me:desk-summaries', retries: 1 })
}
export async function updateOrganizationBranding(payload) { return unwrap(await api.patch('/orgs/branding', payload)) }
export async function getOrganizationTradeAutomation(options = {}) {
  const params = {}
  if (options.scope) params.scope = options.scope
  if (options.scope_key) params.scope_key = options.scope_key
  if (options.linked_account_id) params.linked_account_id = options.linked_account_id
  return strictRequest(() => api.get('/orgs/trade-automation', { params }))
}
export async function getOrganizationTradeAutomationCandidateDiagnostics(options = {}) {
  const params = {}
  if (options.scope) params.scope = options.scope
  if (options.scope_key) params.scope_key = options.scope_key
  if (options.linked_account_id) params.linked_account_id = options.linked_account_id
  return strictRequest(() => api.get('/orgs/trade-automation/candidate-diagnostics', { params }))
}
export async function getOrganizationTradeAutomationDesks() {
  return safeRequest(() => api.get('/orgs/trade-automation/desks'), { items: [], count: 0 }, { key: 'orgs:trade-automation-desks', retries: 1 })
}
export async function getOrganizationTradeAutomationSafetyState(options = {}) {
  const force = Boolean(options.force)
  const now = Date.now()
  if (!force && tradeAutomationSafetyStateCache.value && tradeAutomationSafetyStateCache.expiresAt > now) {
    return cloneFallback(tradeAutomationSafetyStateCache.value)
  }
  const payload = await safeRequest(
    () => api.get('/orgs/trade-automation/safety-state'),
    {
      status: 'degraded',
      label: 'Needs attention',
      tone: 'warning',
      blocker: 'Safety state has not loaded yet.',
      next_action: 'Open the live console or run market-open readiness before trading.',
      route: { active: 'broker_paper', provider: 'alpaca', mode: 'paper' },
      position_promotion: {
        current_max_open_positions: null,
        next_target_positions: null,
        clean_cycle_count: 0,
        required_clean_cycles: null,
        clean_session_count: 0,
        required_clean_sessions: null,
        cycle_progress_pct: 0,
        session_progress_pct: 0,
        auto_promotion_mode: 'paper_only',
        blockers: [],
      },
      links: {
        candidate_diagnostics: '/api/orgs/trade-automation/candidate-diagnostics',
        daily_ledger: '/api/orgs/trade-automation/daily-ledger',
        position_promotion: '/api/orgs/trade-automation/position-promotion',
        hft_watchdog_latest: '/api/orgs/trade-automation/hft-watchdog/latest',
      },
    },
    { key: 'orgs:trade-automation-safety-state', retries: 1 },
  )
  tradeAutomationSafetyStateCache = {
    value: payload,
    expiresAt: Date.now() + TRADE_AUTOMATION_SAFETY_CACHE_TTL_MS,
  }
  return cloneFallback(payload)
}

export async function getOrganizationTradeAutomationWatchdog(options = {}) {
  const force = Boolean(options.force)
  const now = Date.now()
  if (!force && tradeAutomationWatchdogCache.value && tradeAutomationWatchdogCache.expiresAt > now) {
    return cloneFallback(tradeAutomationWatchdogCache.value)
  }
  const payload = await safeRequest(
    () => api.get('/orgs/trade-automation/watchdog', { timeout: 90000 }),
    {
      status: 'degraded',
      label: 'Needs attention',
      tone: 'warning',
      blocker: 'Market Watchdog has not loaded yet.',
      next_action: 'Check backend /api/healthz, /api/readyz, and the managed runtime before expecting scans.',
      phase: { phase: 'unknown' },
      components: [],
      cards: [],
      component_status_counts: { ready: 0, watching: 0, degraded: 1, blocked: 0, killed: 0 },
      evidence_million_target: {
        label: 'Evidence 100M',
        observed_event_count: 0,
        live_observed_evidence: 0,
        simulation_evidence: 0,
        target_event_count: 100000000,
        remaining_event_count: 100000000,
        progress_pct: 0,
        rate_per_hour: 0,
        eta_hours: null,
        eta_days: null,
        status: 'degraded',
        usage_mode: 'evidence_memory_target',
        mutation: 'paper_evidence_state',
        simulation_counts_toward_live_million: false,
        evidence_quality: {
          useful_event_ratio: 0,
          duplicate_ratio: 0,
          stale_ratio: 0,
          simulation_counts_toward_live_million: false,
        },
        evidence_accelerator: {
          status: 'degraded',
          current_useful_event_count: 0,
          configured_max_events_per_minute: 1500,
        },
        market_possibility_engine: {
          status: 'degraded',
          current_simulation_event_count: 0,
          counts_toward_live_million: false,
        },
        can_submit_orders: false,
        can_submit_live_orders: false,
      },
      production_trust: {
        status: 'needs_attention',
        label: 'Production Trust Center',
        alert_delivery: { status: 'not_configured', enabled: false },
        onboarding: { status: 'needs_attention', items: [], completed_count: 0, total_count: 0 },
        evidence_quality: { status: 'not_configured', observed_event_count: 0, quality_score: 0 },
        replay_proof: { status: 'not_configured', evidence_only: true, can_submit_orders: false },
        provider_reliability: { status: 'degraded' },
        can_submit_orders: false,
        can_submit_live_orders: false,
      },
      links: {
        candidate_diagnostics: '/api/orgs/trade-automation/candidate-diagnostics',
        no_trade_report: '/api/orgs/trade-automation/no-trade-report',
        daily_ledger: '/api/orgs/trade-automation/daily-ledger',
        market_day_report: '/api/orgs/trade-automation/market-day-report',
        alpaca_paper_readiness: '/api/orgs/trade-automation/alpaca-paper-readiness',
        hft_watchdog_latest: '/api/orgs/trade-automation/hft-watchdog/latest',
      },
      position_promotion: {
        current_max_open_positions: null,
        next_target_positions: null,
        clean_cycle_count: 0,
        required_clean_cycles: null,
        clean_session_count: 0,
        required_clean_sessions: null,
        cycle_progress_pct: 0,
        session_progress_pct: 0,
        auto_promotion_mode: 'paper_only',
        blockers: [],
      },
      paper_route_only: true,
      read_only: true,
      writes_trade_state: false,
      can_submit_orders: false,
      can_submit_live_orders: false,
      can_clear_kill_switch: false,
      can_loosen_risk_gates: false,
    },
    { key: 'orgs:trade-automation-watchdog', retries: 1 },
  )
  tradeAutomationWatchdogCache = {
    value: payload,
    expiresAt: Date.now() + TRADE_AUTOMATION_SAFETY_CACHE_TTL_MS,
  }
  return cloneFallback(payload)
}

export async function getOrganizationTradeAutomationProductionTrust() {
  return safeRequest(
    () => api.get('/orgs/trade-automation/production-trust'),
    {
      status: 'needs_attention',
      label: 'Production Trust Center',
      alert_delivery: { status: 'not_configured', enabled: false, channels: [] },
      onboarding: { status: 'needs_attention', items: [], completed_count: 0, total_count: 0 },
      support_bundle: { status: 'not_configured', sanitized: true },
      evidence_quality: { status: 'not_configured', observed_event_count: 0, quality_score: 0, categories: {} },
      replay_proof: { status: 'not_configured', evidence_only: true, can_submit_orders: false },
      provider_reliability: { status: 'degraded' },
      release_validation: { status: 'degraded', checks: [] },
      can_submit_orders: false,
      can_submit_live_orders: false,
      next_action: 'Production Trust Center has not loaded yet.',
    },
    { key: 'orgs:trade-automation-production-trust', retries: 1 },
  )
}

export async function getOrganizationTradeAutomationAlertDeliveryStatus() {
  return safeRequest(
    () => api.get('/orgs/trade-automation/alert-delivery/status'),
    { status: 'not_configured', enabled: false, channels: [], can_submit_orders: false },
    { key: 'orgs:trade-automation-alert-delivery', retries: 1 },
  )
}

export async function testOrganizationTradeAutomationAlertDelivery() {
  return unwrap(await api.post('/orgs/trade-automation/alert-delivery/test'))
}

export async function exportOrganizationTradeAutomationSupportBundle() {
  return unwrap(await api.post('/orgs/trade-automation/support-bundle/export'))
}

export async function getOrganizationTradeAutomationEvidenceQuality() {
  return safeRequest(
    () => api.get('/orgs/trade-automation/evidence-quality'),
    { status: 'not_configured', observed_event_count: 0, quality_score: 0, categories: {}, can_submit_orders: false },
    { key: 'orgs:trade-automation-evidence-quality', retries: 1 },
  )
}

export async function getOrganizationTradeAutomationReplayReport(params = {}) {
  return safeRequest(
    () => api.get('/orgs/trade-automation/replay-report', { params }),
    { status: 'not_configured', evidence_only: true, can_submit_orders: false, can_submit_live_orders: false },
    { key: 'orgs:trade-automation-replay-report', retries: 1 },
  )
}

const FALLBACK_FINISH_TRACKER_ITEMS = [
  { id: 'post_implementation_verification', area: 'verification', title: 'Post-Implementation Verification', status: 'in_progress', priority: 'critical', remaining_work: ['Keep backend, frontend, report, route, and safety-boundary checks current after each proof-layer change.'], done_when: 'The verification report is current and lists remaining proof blockers without overclaiming readiness.' },
  { id: 'data_completeness_hardening', area: 'evidence_quality', title: 'Data completeness hardening', status: 'in_progress', priority: 'critical', remaining_work: ['Raise proof-field coverage for forward returns, baselines, actual paths, costs, regimes, and reward fields.'], done_when: 'Data Completeness reports benchmark_ready and proof_field_ready with traceable coverage.' },
  { id: 'candidate_outcome_baseline_stamping', area: 'evidence_capture', title: 'Candidate outcome and baseline stamping', status: 'in_progress', priority: 'critical', remaining_work: ['Capture entry, closed-horizon outcome, baseline, cost, and lineage fields for candidate rows.'], done_when: 'Rewardable candidate outcomes exist with actual returns, baseline returns, cost fields, and append-only lineage.' },
  { id: 'professional_benchmark_proof', area: 'benchmarking', title: 'Professional Benchmark proof gate', status: 'blocked_by_evidence', priority: 'critical', remaining_work: ['Collect enough rewardable outcomes to verify baseline-relative edge, score-bucket lift, and after-cost reward.'], done_when: 'Professional Benchmark reaches ready_for_human_review without claiming proven alpha.' },
  { id: 'walk_forward_validation', area: 'repeatability', title: 'Walk-forward validation', status: 'blocked_by_evidence', priority: 'high', remaining_work: ['Freeze experiment records and evaluate out-of-sample windows against stamped candidate outcomes.'], done_when: 'Walk-Forward shows frozen, no-lookahead, evaluated records with acceptable pass rate.' },
  { id: 'score_calibration_feature_attribution', area: 'ranking_quality', title: 'Score calibration and feature attribution', status: 'blocked_by_evidence', priority: 'high', remaining_work: ['Measure score-bucket lift, after-cost lift, monotonicity, and feature drivers on rewardable outcomes.'], done_when: 'Calibration proof is ready with sufficient feature coverage and after-cost lift.' },
  { id: 'execution_quality_tca', area: 'execution_quality', title: 'Execution Quality and TCA', status: 'in_progress', priority: 'high', remaining_work: ['Link paper fills to candidate IDs and improve cost, fill, quote, and alpha-decay coverage.'], done_when: 'Execution proof is ready with candidate-route linkage and positive after-cost evidence.' },
  { id: 'risk_gate_audit_trail_hardening', area: 'risk_and_audit', title: 'Risk Gate and Audit Trail hardening', status: 'in_progress', priority: 'critical', remaining_work: ['Verify risk gates, kill switches, broker-route boundaries, and audit records stay visible and authoritative.'], done_when: 'Risk and audit evidence confirms no proof layer can bypass controls.' },
  { id: 'portfolio_risk_intelligence', area: 'risk_visibility', title: 'Portfolio Risk Intelligence', status: 'in_progress', priority: 'high', remaining_work: ['Attach candidate, factor, liquidity, stress, drawdown, and open-heat context to risk records.'], done_when: 'Portfolio risk proof is ready with enough exposure and context coverage for review.' },
  { id: 'human_system_shadow_mode', area: 'decision_review', title: 'Human vs System Shadow Mode', status: 'blocked_by_evidence', priority: 'medium', remaining_work: ['Capture same-opportunity human and system contracts before outcomes with cost and risk context.'], done_when: 'Shadow Mode has fair same-opportunity comparisons with pre-outcome contracts.' },
  { id: 'research_promotion_rules', area: 'promotion_governance', title: 'Research promotion rules', status: 'blocked_by_evidence', priority: 'high', remaining_work: ['Require traceability to benchmark, data, walk-forward, execution, and manual-review records.'], done_when: 'Promotion proof is ready with traceability coverage and no authority crossing.' },
  { id: 'evidence_reward_and_blocker_value', area: 'reward_quality', title: 'Evidence Reward and blocker value', status: 'blocked_by_evidence', priority: 'high', remaining_work: ['Increase rewardable prediction and blocked-move coverage with actual outcomes, baselines, and costs.'], done_when: 'Evidence Reward explains rewardability, blocker value, and after-cost outcomes without fabricated data.' },
  { id: 'forecast_validation', area: 'forecast_quality', title: 'Forecast validation hardening', status: 'in_progress', priority: 'medium', remaining_work: ['Broaden actual-path coverage and preserve immutable forecast contracts separately from outcomes.'], done_when: 'Forecast Validation stays ready with broad actual-path coverage and stable reward calculations.' },
  { id: 'proof_metrics_dashboard', area: 'proof_visibility', title: 'Proof metrics dashboard planning', status: 'in_progress', priority: 'medium', remaining_work: ['Summarize proof metrics including completeness, rewardability, benchmarkable candidates, forecast contracts, costs, walk-forward, score buckets, shadow records, and audit coverage.'], done_when: 'A shared proof-metrics view shows current gaps and the gate each gap blocks.' },
  { id: 'proof_first_backlog_scoring', area: 'roadmap_discipline', title: 'Proof-first backlog scoring and expansion gates', status: 'in_progress', priority: 'high', remaining_work: ['Score future features and record safety, data, benchmark, walk-forward, and expansion-justification gates before active work.'], done_when: 'Every future feature has a proof-first decision.' },
  { id: 'technical_analysis_evidence_setup_admission', area: 'setup_research', title: 'Technical Analysis evidence setup admission', status: 'in_progress', priority: 'high', remaining_work: ['Classify TA method families into evidence-only, research-only, and avoid groups before implementation.', 'Require causal rules, executable prices, baselines, walk-forward robustness, cost survival, parameter stability, and provenance.'], done_when: 'Technical-analysis methods are documented with fields, controls, and proof gates before setup admission.' },
  { id: 'ai_committee_research_layer', area: 'ai_research', title: 'AI Committee research layer', status: 'in_progress', priority: 'medium', remaining_work: ['Tie sanitized committee conclusions to benchmark, walk-forward, calibration, forecast, execution, and data statuses.'], done_when: 'Committee reports add research context without approving trades or mutating live behavior.' },
  { id: 'operator_experience_docs', area: 'product_readiness', title: 'Operator experience, docs, and report UX', status: 'in_progress', priority: 'medium', remaining_work: ['Show the shared tracker at the end of report surfaces and keep docs aligned with live proof boundaries.'], done_when: 'Every major report ends with the shared tracker and clear next safe actions.' },
  { id: 'paper_to_live_gate', area: 'live_trading_boundary', title: 'Paper-to-live proof gate', status: 'not_started', priority: 'critical', remaining_work: ['Keep live enablement gated by paper evidence, operational runbooks, kill-switch checks, reconciliation, rollback evidence, and human approval.'], done_when: 'Live enablement remains explicitly gated by verified paper evidence and human approval.' },
  { id: 'future_market_specialist_desks', area: 'future_backlog', title: 'Market Specialist Desk registry', status: 'deferred', priority: 'future', remaining_work: ['Keep future market desks as context engines only and require proof-first scoring before implementation.'], done_when: 'Deferred until foundation proof is stronger and a context-only version is justified.' },
  { id: 'future_candidate_fusion_market_strategy_benchmark', area: 'future_backlog', title: 'Candidate Fusion and Market x Strategy Benchmark', status: 'deferred', priority: 'future', remaining_work: ['Prove current benchmark and walk-forward quality before combining market context with strategy logic.'], done_when: 'Deferred until current evidence supports market x strategy comparisons.' },
  { id: 'future_off_exchange_liquidity_research', area: 'future_backlog', title: 'Off-Exchange Liquidity Dashboard', status: 'deferred', priority: 'future', remaining_work: ['Use passive research context only and avoid dark-pool prediction or institutional-intent claims.'], done_when: 'Deferred until it solves a measured proof problem without changing ranking, routing, or order behavior.' },
  { id: 'future_broker_neutral_provider_strategy', area: 'future_backlog', title: 'Broker-neutral architecture and provider ROI gates', status: 'deferred', priority: 'future', remaining_work: ['Keep Alpaca paper as the unattended lane and require proof plus ROI before broker/provider expansion.'], done_when: 'Deferred until evidence proves a broker/provider bottleneck and ROI case.' },
  { id: 'future_visual_strategy_evidence_builder', area: 'future_backlog', title: 'Visual Strategy Evidence Builder', status: 'deferred', priority: 'future', remaining_work: ['Define visual rule contracts without auto-trading or proof-gate bypass.'], done_when: 'Deferred until current evidence contracts are mature enough.' },
  { id: 'future_governance_institutional_controls', area: 'future_backlog', title: 'Governance, RBAC, model registry, and institutional controls', status: 'deferred', priority: 'future', remaining_work: ['Add firm-facing controls only after foundation proof improves and required reviews are scoped.'], done_when: 'Deferred until the proof chain supports firm-facing control work.' },
  { id: 'future_cpp_hft_feasibility', area: 'future_backlog', title: 'C++ Core Accelerators and HFT feasibility study', status: 'deferred', priority: 'future', remaining_work: ['Use profiling before C++ and keep HFT as a separate infrastructure thesis.'], done_when: 'Deferred until profiling proves a research-only bottleneck or a separate HFT thesis is approved.' },
]

const FALLBACK_FINISH_TRACKER = {
  version: 'project_finish_tracker_v2',
  report_name: 'frontend_fallback',
  scope: 'project_wide',
  summary: {
    total_items: FALLBACK_FINISH_TRACKER_ITEMS.length,
    status_counts: { in_progress: 12, blocked_by_evidence: 6, not_started: 1, deferred: 7 },
    priority_counts: { critical: 6, high: 8, medium: 5, future: 7 },
    critical_open_items: 6,
    safe_boundary: 'Tracker items are verification, proof, review, documentation, paper-operation, or deferred roadmap work only. They do not authorize live trading or expansion implementation.',
    proof_first_rule: 'Ambition is allowed. Proof decides priority.',
  },
  items: FALLBACK_FINISH_TRACKER_ITEMS,
  source_docs: [
    'docs/PROOF_FIRST_ROADMAP_DISCIPLINE.md',
    'docs/TEN_OUT_OF_TEN_30_60_90_DAY_PLAN.md',
    'docs/TEN_OUT_OF_TEN_ROADMAP.md',
    'docs/TEN_OUT_OF_TEN_CATEGORY_UPGRADE_MASTER_PLAN.md',
    'docs/TECHNICAL_ANALYSIS_EVIDENCE_SETUP_RESEARCH.md',
    'docs/CATEGORY_READINESS_RATINGS.md',
    'docs/BENCHMARK_TRIAGE_REPORT.md',
    'docs/RISK_GATE_AUDIT_TRAIL_HARDENING.md',
    'docs/POST_IMPLEMENTATION_VERIFICATION_REPORT.md',
  ],
}

export const FALLBACK_PROOF_METRICS_DASHBOARD = {
  status: 'blocked_by_evidence',
  generated_at: null,
  summary: {
    status: 'blocked_by_evidence',
    metric_count: 14,
    ready_metric_count: 0,
    open_metric_count: 14,
    critical_open_metric_count: 4,
    high_open_metric_count: 7,
    source_count: 13,
    source_unavailable_count: 13,
    gate_count: 11,
    gates_ready_count: 0,
    gates_blocked_count: 11,
    deferred_expansion_count: 7,
    proof_ready: false,
    top_blockers: ['Proof-field coverage', 'Candidate outcome and baseline coverage', 'Professional benchmark proof'],
    claim_boundary: 'Proof metrics are visibility only. They do not prove alpha, repeatability, institutional readiness, HFT capability, compliance approval, or live-trading readiness.',
    proof_first_rule: 'Ambition is allowed. Proof decides priority.',
    research_only: true,
    paper_route_only: true,
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'none',
  },
  metrics: [
    { key: 'proof_field_coverage', label: 'Proof-field coverage', gate: 'Data Gate', source: 'data_completeness', priority: 'critical', status: 'source_unavailable', value: null, target: 0.8, blocked_claims: ['benchmark_ready', 'walk_forward_ready'], safe_next_action: 'Raise proof-field coverage before proof claims.' },
    { key: 'outcome_baseline_coverage', label: 'Candidate outcome and baseline coverage', gate: 'Evidence Outcome Gate', source: 'evidence_outcomes', priority: 'critical', status: 'source_unavailable', value: null, target: 0.8, blocked_claims: ['baseline_relative_edge'], safe_next_action: 'Stamp closed-horizon outcomes and same-window baselines.' },
    { key: 'benchmark_proof', label: 'Professional benchmark proof', gate: 'Benchmark Gate', source: 'professional_benchmark', priority: 'critical', status: 'source_unavailable', value: null, target: null, blocked_claims: ['proven_alpha'], safe_next_action: 'Collect rewardable rows with baselines and after-cost reward.' },
  ],
  gate_groups: [],
  source_reports: [],
  warnings: ['Proof Metrics source is unavailable.'],
  safe_next_actions: [],
  deferred_scope: [],
  finish_tracker: FALLBACK_FINISH_TRACKER,
  safety_notes: [
    'Proof metrics are read-only visibility.',
    'Research only. Does not affect trading.',
    'Does not place orders.',
    'Does not change broker routes.',
    'Does not bypass risk gates.',
    'Does not clear kill switches.',
    'Does not change ranking weights automatically.',
    'Does not grant AI order authority.',
  ],
  research_only: true,
  paper_only: true,
  paper_route_only: true,
  read_only: true,
  proof_visibility_only: true,
  can_submit_orders: false,
  can_submit_live_orders: false,
  can_change_broker_routes: false,
  can_bypass_risk_gates: false,
  can_clear_kill_switch: false,
  can_change_ranking_weights: false,
  mutation: 'none',
}

const FALLBACK_EVIDENCE_EDGE = {
  summary: {
    candidate_count: 0,
    allowed_count: 0,
    blocked_count: 0,
    missed_move_count: 0,
    observed_outcome_count: 0,
    data_status: 'empty',
    missing_fields: {},
    research_only: true,
    paper_route_only: true,
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'none',
    next_action: 'Collect candidate lifecycle rows or closed paper trades before Evidence Edge can estimate blocker value.',
  },
  blocker_effectiveness: [],
  setup_forward_return_stats: [],
  engine_forward_return_stats: [],
  regime_forward_return_stats: [],
  score_bucket_outcomes: [],
  top_positive_features: [],
  top_negative_features: [],
  recommended_ranking_adjustments: [],
  candidate_rows: [],
  finish_tracker: FALLBACK_FINISH_TRACKER,
  research_only: true,
  paper_route_only: true,
  can_submit_orders: false,
  can_submit_live_orders: false,
  mutation: 'none',
}

export async function getOrganizationTradeAutomationEvidenceEdgeSummary() {
  return safeRequest(
    () => api.get('/orgs/trade-automation/evidence-edge/summary'),
    FALLBACK_EVIDENCE_EDGE,
    { key: 'orgs:trade-automation-evidence-edge-summary', retries: 1 },
  )
}

export async function getOrganizationTradeAutomationEvidenceEdgeBlockers() {
  return safeRequest(
    () => api.get('/orgs/trade-automation/evidence-edge/blockers'),
    { summary: FALLBACK_EVIDENCE_EDGE.summary, items: [], research_only: true, paper_route_only: true, can_submit_orders: false, can_submit_live_orders: false, mutation: 'none' },
    { key: 'orgs:trade-automation-evidence-edge-blockers', retries: 1 },
  )
}

export async function getOrganizationTradeAutomationEvidenceEdgeSetups() {
  return safeRequest(
    () => api.get('/orgs/trade-automation/evidence-edge/setups'),
    { summary: FALLBACK_EVIDENCE_EDGE.summary, items: [], score_bucket_outcomes: [], research_only: true, paper_route_only: true, can_submit_orders: false, can_submit_live_orders: false, mutation: 'none' },
    { key: 'orgs:trade-automation-evidence-edge-setups', retries: 1 },
  )
}

export async function getOrganizationTradeAutomationEvidenceEdgeEngines() {
  return safeRequest(
    () => api.get('/orgs/trade-automation/evidence-edge/engines'),
    { summary: FALLBACK_EVIDENCE_EDGE.summary, items: [], regime_forward_return_stats: [], research_only: true, paper_route_only: true, can_submit_orders: false, can_submit_live_orders: false, mutation: 'none' },
    { key: 'orgs:trade-automation-evidence-edge-engines', retries: 1 },
  )
}

export async function getOrganizationTradeAutomationEvidenceEdgeRecommendations() {
  return safeRequest(
    () => api.get('/orgs/trade-automation/evidence-edge/recommendations'),
    { summary: FALLBACK_EVIDENCE_EDGE.summary, items: [], top_positive_features: [], top_negative_features: [], research_only: true, paper_route_only: true, can_submit_orders: false, can_submit_live_orders: false, mutation: 'none' },
    { key: 'orgs:trade-automation-evidence-edge-recommendations', retries: 1 },
  )
}

const FALLBACK_EVIDENCE_REWARD = {
  summary: {
    candidate_count: 0,
    allowed_count: 0,
    blocked_count: 0,
    trade_executed_count: 0,
    observed_outcome_count: 0,
    missed_move_count: 0,
    avg_reward: null,
    reward_distribution: {},
    reward_by_score_bucket: [],
    missing_fields: {},
    data_status: 'empty',
    research_only: true,
    paper_route_only: true,
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'none',
    next_action: 'Collect candidate lifecycle rows or closed paper trades before Evidence Reward can score outcomes.',
  },
  candidate_rows: [],
  blocker_rewards: [],
  engine_rewards: [],
  setup_rewards: [],
  ai_rewards: { verdict_count: 0, items: [], research_only: true, paper_route_only: true, can_submit_orders: false, can_submit_live_orders: false, mutation: 'none' },
  regime_rewards: [],
  reward_by_score_bucket: [],
  finish_tracker: FALLBACK_FINISH_TRACKER,
  research_only: true,
  paper_route_only: true,
  can_submit_orders: false,
  can_submit_live_orders: false,
  mutation: 'none',
}

export async function getOrganizationTradeAutomationEvidenceRewardSummary() {
  return safeRequest(
    () => api.get('/orgs/trade-automation/evidence-reward/summary'),
    FALLBACK_EVIDENCE_REWARD,
    { key: 'orgs:trade-automation-evidence-reward-summary', retries: 1 },
  )
}

export async function getOrganizationTradeAutomationEvidenceRewardCandidates() {
  return safeRequest(
    () => api.get('/orgs/trade-automation/evidence-reward/candidates'),
    { summary: FALLBACK_EVIDENCE_REWARD.summary, items: [], research_only: true, paper_route_only: true, can_submit_orders: false, can_submit_live_orders: false, mutation: 'none' },
    { key: 'orgs:trade-automation-evidence-reward-candidates', retries: 1 },
  )
}

export async function getOrganizationTradeAutomationEvidenceRewardBlockers() {
  return safeRequest(
    () => api.get('/orgs/trade-automation/evidence-reward/blockers'),
    { summary: FALLBACK_EVIDENCE_REWARD.summary, items: [], research_only: true, paper_route_only: true, can_submit_orders: false, can_submit_live_orders: false, mutation: 'none' },
    { key: 'orgs:trade-automation-evidence-reward-blockers', retries: 1 },
  )
}

export async function getOrganizationTradeAutomationEvidenceRewardEngines() {
  return safeRequest(
    () => api.get('/orgs/trade-automation/evidence-reward/engines'),
    { summary: FALLBACK_EVIDENCE_REWARD.summary, items: [], research_only: true, paper_route_only: true, can_submit_orders: false, can_submit_live_orders: false, mutation: 'none' },
    { key: 'orgs:trade-automation-evidence-reward-engines', retries: 1 },
  )
}

export async function getOrganizationTradeAutomationEvidenceRewardSetups() {
  return safeRequest(
    () => api.get('/orgs/trade-automation/evidence-reward/setups'),
    { summary: FALLBACK_EVIDENCE_REWARD.summary, items: [], research_only: true, paper_route_only: true, can_submit_orders: false, can_submit_live_orders: false, mutation: 'none' },
    { key: 'orgs:trade-automation-evidence-reward-setups', retries: 1 },
  )
}

export async function getOrganizationTradeAutomationEvidenceRewardAi() {
  return safeRequest(
    () => api.get('/orgs/trade-automation/evidence-reward/ai'),
    { summary: FALLBACK_EVIDENCE_REWARD.summary, verdict_count: 0, items: [], research_only: true, paper_route_only: true, can_submit_orders: false, can_submit_live_orders: false, mutation: 'none' },
    { key: 'orgs:trade-automation-evidence-reward-ai', retries: 1 },
  )
}

export async function getOrganizationTradeAutomationEvidenceRewardRegimes() {
  return safeRequest(
    () => api.get('/orgs/trade-automation/evidence-reward/regimes'),
    { summary: FALLBACK_EVIDENCE_REWARD.summary, items: [], research_only: true, paper_route_only: true, can_submit_orders: false, can_submit_live_orders: false, mutation: 'none' },
    { key: 'orgs:trade-automation-evidence-reward-regimes', retries: 1 },
  )
}

const FALLBACK_FORECAST_VALIDATION = {
  summary: {
    status: 'empty',
    generated_at: null,
    research_only: true,
    mode: 'research_only',
    safety: 'Forecast validation is read-only and never adjusts execution, ranking weights, or risk gates.',
    safety_notes: [
      'Research only. Does not affect trading.',
      'Does not change broker routes.',
      'Does not bypass risk gates.',
      'Does not place orders.',
      'Does not grant AI order authority.',
    ],
    count: 0,
    total_forecasts: 0,
    validated_forecasts: 0,
    non_rewardable_forecasts: 0,
    evaluated_count: 0,
    avg_reward: null,
    avg_forecast_reward: null,
    direction_accuracy: null,
    avg_mae: null,
    avg_rmse: null,
    avg_path_mae: null,
    avg_path_rmse: null,
    avg_timing_error: null,
    missing_data_count: 0,
    missing_field_counts: {},
    best_prediction: null,
    worst_prediction: null,
    reward_formula: 'direction_score + path_fit_score + timing_score - drawdown_penalty - volatility_mismatch_penalty - confidence_penalty',
    records: [],
    aggregations: {},
    missing_fields: {},
    warnings: [],
    finish_tracker: FALLBACK_FINISH_TRACKER,
  },
  predictions: { mode: 'research_only', research_only: true, items: [], records: [], count: 0, safety_notes: [] },
  models: { mode: 'research_only', research_only: true, by_engine: [], by_source: [], by_model: [], records: [], safety_notes: [] },
  regimes: { mode: 'research_only', research_only: true, items: [], records: [], safety_notes: [] },
}

export async function getForecastValidationSummary(params = {}) {
  return safeRequest(() => api.get('/forecast-validation/summary', { params }), FALLBACK_FORECAST_VALIDATION.summary, { key: 'forecast-validation:summary', retries: 1 })
}

export async function getForecastValidationPredictions(params = {}) {
  return safeRequest(() => api.get('/forecast-validation/predictions', { params }), FALLBACK_FORECAST_VALIDATION.predictions, { key: 'forecast-validation:predictions', retries: 1 })
}

export async function getForecastValidationModels(params = {}) {
  return safeRequest(() => api.get('/forecast-validation/models', { params }), FALLBACK_FORECAST_VALIDATION.models, { key: 'forecast-validation:models', retries: 1 })
}

export async function getForecastValidationRegimes(params = {}) {
  return safeRequest(() => api.get('/forecast-validation/regimes', { params }), FALLBACK_FORECAST_VALIDATION.regimes, { key: 'forecast-validation:regimes', retries: 1 })
}

const FALLBACK_PROFESSIONAL_BENCHMARK = {
  status: 'insufficient_evidence',
  generated_at: null,
  research_only: true,
  mode: 'research_only',
  summary: {
    benchmark_verdict: 'insufficient_evidence',
    verdict_reason: 'No rewardable benchmark evidence has been collected yet.',
    candidate_count: 0,
    rewardable_count: 0,
    forecast_count: 0,
    data_quality_score: 0,
    hit_rate: null,
    expected_value: null,
    average_reward: null,
    median_reward: null,
    reward_dispersion: null,
    slippage_adjusted_reward: null,
    score_bucket_lift: null,
    baseline_relative_edge: null,
    benchmark_proof_ready: false,
    benchmark_proof_status: 'needs_evidence',
    benchmark_proof_requirements_passed: 0,
    benchmark_proof_requirements_total: 6,
    benchmark_hardening_status: 'blocked_by_evidence',
    benchmark_hardening_open_items: 6,
    benchmark_hardening_critical_open_items: 2,
    top_hardening_item: 'Rewardable sample and data quality',
    claim_permissions: {
      cautious_internal_benchmark_review: false,
      public_alpha_claim: false,
      repeatability_claim: false,
      live_trading_readiness: false,
      institutional_readiness: false,
    },
    edge_after_costs: null,
    sample_size_warning: true,
    out_of_sample_status: 'missing_sample_split',
    research_only: true,
    paper_route_only: true,
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'none',
  },
  benchmark_hardening_plan: {
    status: 'blocked_by_evidence',
    summary: {
      item_count: 6,
      open_item_count: 6,
      critical_open_items: 2,
      ready_item_count: 0,
      top_hardening_item: 'Rewardable sample and data quality',
      proof_first_rule: 'Ambition is allowed. Proof decides priority.',
      claim_permissions: {
        cautious_internal_benchmark_review: false,
        public_alpha_claim: false,
        repeatability_claim: false,
        live_trading_readiness: false,
        institutional_readiness: false,
      },
      blocked_claims: ['proven_alpha', 'guaranteed_returns', 'repeatability', 'institutional_readiness', 'live_trading_readiness'],
      safe_boundary: 'Professional Benchmark hardening only records proof gaps and claim boundaries. It does not authorize orders, route changes, risk-gate changes, or ranking-weight mutation.',
    },
    items: [
      { key: 'rewardable_sample_quality', title: 'Rewardable sample and data quality', priority: 'critical', status: 'needs_evidence', missing_fields: ['actual_forward_return', 'baseline_forward_return', 'total_reward'], blocked_claims: ['benchmark_edge_review', 'score_quality'], safe_next_action: 'Collect rewardable candidate rows with closed-horizon outcomes, total reward, baseline fields, timestamps, horizons, setup, engine, and regime context.', manual_review_only: true, changes_execution: false, changes_ranking_weights: false },
      { key: 'same_window_baselines', title: 'Same-window explicit baselines', priority: 'critical', status: 'needs_evidence', missing_fields: ['baseline_forward_return'], blocked_claims: ['baseline_relative_edge', 'benchmark_edge_review'], safe_next_action: 'Attach forward-only same-window baseline returns for each evaluated record.', manual_review_only: true, changes_execution: false, changes_ranking_weights: false },
      { key: 'baseline_relative_edge', title: 'Baseline-relative edge', priority: 'high', status: 'needs_evidence', missing_fields: ['baseline_forward_return', 'actual_forward_return'], blocked_claims: ['benchmark_edge_review'], safe_next_action: 'Only evaluate edge after rewardable system returns and explicit baselines exist for the same forward window.', manual_review_only: true, changes_execution: false, changes_ranking_weights: false },
      { key: 'score_bucket_lift', title: 'Score bucket lift', priority: 'high', status: 'needs_evidence', missing_fields: ['score_bucket', 'total_reward'], blocked_claims: ['ranking_quality_claim', 'score_quality'], safe_next_action: 'Collect rewardable high-score and low-score rows before claiming the score separates outcomes.', manual_review_only: true, changes_execution: false, changes_ranking_weights: false },
      { key: 'after_cost_reward', title: 'After-cost reward', priority: 'high', status: 'needs_evidence', missing_fields: ['slippage_bps', 'spread_bps'], blocked_claims: ['tradability_claim', 'execution_adjusted_edge'], safe_next_action: 'Attach paper spread, slippage, fill delay, route, and fill-price evidence before treating reward as cost-adjusted.', manual_review_only: true, changes_execution: false, changes_ranking_weights: false },
      { key: 'out_of_sample_split', title: 'Out-of-sample split and frozen versions', priority: 'high', status: 'needs_evidence', missing_fields: ['sample_split', 'experiment_version'], blocked_claims: ['repeatability_claim', 'walk_forward_claim'], safe_next_action: 'Create frozen walk-forward experiments with sample splits, experiment versions, reward formula versions, and data filters before repeatability language.', manual_review_only: true, changes_execution: false, changes_ranking_weights: false },
    ],
    safe_next_actions: [],
    research_only: true,
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'none',
  },
  records: [],
  aggregations: {},
  baselines: { available: false, items: [], average_baseline_relative_edge: null, missing_fields: [] },
  proof_summary: {
    status: 'needs_evidence',
    proof_ready: false,
    requirements: [],
    summary: {
      requirement_count: 6,
      passed_requirement_count: 0,
      missing_requirement_count: 6,
      baseline_relative_edge: null,
      score_bucket_lift: null,
      slippage_adjusted_reward: null,
      available_baseline_count: 0,
      rewardable_count: 0,
      claim_boundary: 'Do not claim proven alpha, guaranteed returns, repeatability, institutional readiness, HFT capability, or live-trading readiness from benchmark proof alone.',
    },
    safe_next_actions: [],
    research_only: true,
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'none',
  },
  sections: {},
  warnings: [],
  missing_fields: {},
  finish_tracker: FALLBACK_FINISH_TRACKER,
  safety_notes: [
    'Research only. Does not affect trading.',
    'Does not place orders.',
    'Does not change broker routes.',
    'Does not bypass risk gates.',
    'Does not change ranking weights automatically.',
  ],
  paper_route_only: true,
  can_submit_orders: false,
  can_submit_live_orders: false,
  mutation: 'none',
}

export async function getProfessionalBenchmarkSummary(params = {}) {
  return safeRequest(() => api.get('/professional-benchmark/summary', { params, timeout: 180000 }), FALLBACK_PROFESSIONAL_BENCHMARK, { key: 'professional-benchmark:summary', retries: 0 })
}

export async function getProfessionalBenchmarkBaselines(params = {}) {
  return safeRequest(() => api.get('/professional-benchmark/baselines', { params }), { ...FALLBACK_PROFESSIONAL_BENCHMARK, records: [], baselines: FALLBACK_PROFESSIONAL_BENCHMARK.baselines }, { key: 'professional-benchmark:baselines', retries: 1 })
}

export async function getProfessionalBenchmarkScoreBuckets(params = {}) {
  return safeRequest(() => api.get('/professional-benchmark/score-buckets', { params }), { ...FALLBACK_PROFESSIONAL_BENCHMARK, records: [], aggregations: { score_bucket_separation: { available: false, items: [] } } }, { key: 'professional-benchmark:score-buckets', retries: 1 })
}

export async function getProfessionalBenchmarkBlockers(params = {}) {
  return safeRequest(() => api.get('/professional-benchmark/blockers', { params }), { ...FALLBACK_PROFESSIONAL_BENCHMARK, records: [], aggregations: { blocker_value: { available: false, items: [] } } }, { key: 'professional-benchmark:blockers', retries: 1 })
}

export async function getProfessionalBenchmarkAi(params = {}) {
  return safeRequest(() => api.get('/professional-benchmark/ai', { params }), { ...FALLBACK_PROFESSIONAL_BENCHMARK, records: [], aggregations: { ai_verdict_accuracy: { available: false, items: [] } } }, { key: 'professional-benchmark:ai', retries: 1 })
}

export async function getProfessionalBenchmarkForecast(params = {}) {
  return safeRequest(() => api.get('/professional-benchmark/forecast', { params }), { ...FALLBACK_PROFESSIONAL_BENCHMARK, records: [], aggregations: { forecast_accuracy: { available: false, items: [] } } }, { key: 'professional-benchmark:forecast', retries: 1 })
}

export async function getProfessionalBenchmarkExecution(params = {}) {
  return safeRequest(() => api.get('/professional-benchmark/execution', { params }), { ...FALLBACK_PROFESSIONAL_BENCHMARK, records: [], aggregations: { execution_quality: { available: false } } }, { key: 'professional-benchmark:execution', retries: 1 })
}

export const FALLBACK_EVIDENCE_OUTCOMES = {
  status: 'empty',
  generated_at: null,
  research_only: true,
  paper_only: true,
  summary: {
    candidate_lifecycle_rows: 0,
    stamped_outcome_rows: 0,
    candidate_with_outcomes_count: 0,
    candidate_without_outcomes_count: 0,
    due_count: 0,
    available_outcome_count: 0,
    unavailable_outcome_count: 0,
    rewardability_lift_candidates: 0,
    baseline_coverage_rate: 0,
    execution_cost_coverage_rate: 0,
    last_run_at: null,
    primary_baseline_rule: 'random_candidate_forward_return when available, otherwise SPY',
    research_only: true,
    paper_only: true,
    paper_route_only: true,
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'append_only_research_evidence',
  },
  records: [],
  due_records: [],
  due: [],
  aggregations: {
    missing_field_counts: {},
    outcomes_by_horizon: {},
    outcomes_by_candidate: {},
    baseline_coverage: { rows_with_primary_baseline: 0, coverage_rate: 0 },
    execution_cost_coverage: { rows_with_execution_cost: 0, coverage_rate: 0 },
  },
  warnings: [],
  missing_fields: {},
  finish_tracker: FALLBACK_FINISH_TRACKER,
  safety_notes: [
    'Research only. Does not affect trading.',
    'Paper-route evidence only.',
    'Does not place orders.',
    'Does not change broker routes.',
    'Does not bypass risk gates.',
    'Does not change ranking weights automatically.',
  ],
  can_submit_orders: false,
  can_submit_live_orders: false,
  mutation: 'append_only_research_evidence',
}

export async function getEvidenceOutcomesSummary(params = {}) {
  return safeRequest(() => api.get('/evidence-outcomes/summary', { params }), FALLBACK_EVIDENCE_OUTCOMES, { key: 'evidence-outcomes:summary', retries: 1 })
}

export async function getEvidenceOutcomesDue(params = {}) {
  return safeRequest(() => api.get('/evidence-outcomes/due', { params }), { ...FALLBACK_EVIDENCE_OUTCOMES, records: [], due_records: [] }, { key: 'evidence-outcomes:due', retries: 1 })
}

export async function getEvidenceOutcomesRecords(params = {}) {
  return safeRequest(() => api.get('/evidence-outcomes/records', { params }), { ...FALLBACK_EVIDENCE_OUTCOMES, records: [] }, { key: 'evidence-outcomes:records', retries: 1 })
}

export async function stampDueEvidenceOutcomes() {
  return unwrap(await api.post('/evidence-outcomes/stamp-due'))
}

const FALLBACK_DATA_COMPLETENESS = {
  status: 'empty',
  generated_at: null,
  research_only: true,
  mode: 'research_only',
  summary: {
    status: 'empty',
    total_records: 0,
    complete_records: 0,
    incomplete_records: 0,
    rewardable_records: 0,
    non_rewardable_records: 0,
    completion_rate: 0,
    rewardability_rate: 0,
    benchmark_ready: false,
    base_benchmark_ready: false,
    proof_field_ready: false,
    proof_field_coverage_rate: 0,
    proof_field_requirements_ready: 0,
    proof_field_requirements_total: 6,
    cleanup_plan_status: 'no_records',
    cleanup_plan_open_items: 6,
    cleanup_plan_critical_open_items: 3,
    top_cleanup_item: 'Missing forward returns',
    source_summaries: {},
    highest_priority_missing_fields: [],
    benchmark_blockers: [],
    research_only: true,
    paper_route_only: true,
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'none',
  },
  data_cleanup_plan: {
    status: 'no_records',
    summary: {
      item_count: 6,
      open_item_count: 6,
      critical_open_items: 3,
      ready_item_count: 0,
      top_cleanup_item: 'Missing forward returns',
      proof_first_rule: 'Ambition is allowed. Proof decides priority.',
      safe_boundary: 'Data cleanup records proof gaps only. It does not fabricate market outcomes or authorize trading changes.',
    },
    items: [
      { key: 'missing_forward_returns', title: 'Missing forward returns', priority: 'critical', status: 'no_records', affected_record_count: 0, proof_missing_record_count: 0, missing_field_counts: {}, missing_by_source: {}, safe_next_action: 'Stamp actual forward returns only after the horizon closes with observed market data.', manual_review_only: true, changes_execution: false },
      { key: 'missing_baselines', title: 'Missing baselines', priority: 'critical', status: 'no_records', affected_record_count: 0, proof_missing_record_count: 0, missing_field_counts: {}, missing_by_source: {}, safe_next_action: 'Attach same-window baseline returns at the same timestamp and horizon as each evaluated record.', manual_review_only: true, changes_execution: false },
      { key: 'missing_forecast_actuals', title: 'Missing forecast actuals', priority: 'high', status: 'no_records', affected_record_count: 0, proof_missing_record_count: 0, missing_field_counts: {}, missing_by_source: {}, safe_next_action: 'Store actual post-forecast path data separately from immutable prediction contracts.', manual_review_only: true, changes_execution: false },
      { key: 'missing_execution_evidence', title: 'Missing execution evidence', priority: 'high', status: 'no_records', affected_record_count: 0, proof_missing_record_count: 0, missing_field_counts: {}, missing_by_source: {}, safe_next_action: 'Capture spread, slippage, fill delay, route, and paper-fill linkage without changing routes.', manual_review_only: true, changes_execution: false },
      { key: 'missing_lineage_and_context', title: 'Missing lineage and context', priority: 'high', status: 'no_records', affected_record_count: 0, proof_missing_record_count: 0, missing_field_counts: {}, missing_by_source: {}, safe_next_action: 'Backfill only point-in-time metadata already known from source evidence.', manual_review_only: true, changes_execution: false },
      { key: 'missing_reward_contract_fields', title: 'Missing reward contract fields', priority: 'critical', status: 'no_records', affected_record_count: 0, proof_missing_record_count: 0, missing_field_counts: {}, missing_by_source: {}, safe_next_action: 'Complete reward contract fields from recorded candidate and paper-trade evidence before benchmark use.', manual_review_only: true, changes_execution: false },
    ],
    safe_next_actions: [],
    research_only: true,
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'none',
  },
  records: [],
  records_by_source: {},
  proof_field_coverage: {
    status: 'needs_attention',
    summary: {
      requirement_count: 6,
      ready_requirement_count: 0,
      missing_requirement_count: 6,
      average_coverage_rate: 0,
      proof_ready: false,
      claim_boundary: 'Proof-field coverage is research-only and is not proof of alpha, investor performance, repeatability, or live-trading readiness.',
    },
    records: [],
    safe_next_actions: [],
    research_only: true,
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'none',
  },
  aggregations: {
    total_records: 0,
    complete_records: 0,
    incomplete_records: 0,
    rewardable_records: 0,
    non_rewardable_records: 0,
    completion_rate: 0,
    rewardability_rate: 0,
    missing_field_counts: {},
    missing_by_source: {},
    missing_by_engine: {},
    missing_by_setup_type: {},
    missing_by_regime: {},
    highest_priority_missing_fields: [],
    benchmark_blockers: [],
  },
  missing_fields: {},
  warnings: [],
  safe_next_actions: [],
  finish_tracker: FALLBACK_FINISH_TRACKER,
  safety_notes: [
    'Research only. Does not affect trading.',
    'Does not place orders.',
    'Does not change broker routes.',
    'Does not bypass risk gates.',
    'Does not change ranking weights automatically.',
    'Does not grant AI order authority.',
  ],
  paper_route_only: true,
  can_submit_orders: false,
  can_submit_live_orders: false,
  mutation: 'none',
}

export async function getDataCompletenessSummary(params = {}) {
  return safeRequest(() => api.get('/data-completeness/summary', { params }), FALLBACK_DATA_COMPLETENESS, { key: 'data-completeness:summary', retries: 1 })
}

export async function getDataCompletenessCandidates(params = {}) {
  return safeRequest(() => api.get('/data-completeness/candidates', { params }), { ...FALLBACK_DATA_COMPLETENESS, records: [] }, { key: 'data-completeness:candidates', retries: 1 })
}

export async function getDataCompletenessForecasts(params = {}) {
  return safeRequest(() => api.get('/data-completeness/forecasts', { params }), { ...FALLBACK_DATA_COMPLETENESS, records: [] }, { key: 'data-completeness:forecasts', retries: 1 })
}

export async function getDataCompletenessAi(params = {}) {
  return safeRequest(() => api.get('/data-completeness/ai', { params }), { ...FALLBACK_DATA_COMPLETENESS, records: [] }, { key: 'data-completeness:ai', retries: 1 })
}

export async function getDataCompletenessBlockers(params = {}) {
  return safeRequest(() => api.get('/data-completeness/blockers', { params }), { ...FALLBACK_DATA_COMPLETENESS, records: [] }, { key: 'data-completeness:blockers', retries: 1 })
}

export async function getDataCompletenessExecution(params = {}) {
  return safeRequest(() => api.get('/data-completeness/execution', { params }), { ...FALLBACK_DATA_COMPLETENESS, records: [] }, { key: 'data-completeness:execution', retries: 1 })
}

export async function getDataCompletenessBenchmarkReadiness(params = {}) {
  return safeRequest(() => api.get('/data-completeness/benchmark-readiness', { params }), { ...FALLBACK_DATA_COMPLETENESS, records: [] }, { key: 'data-completeness:benchmark-readiness', retries: 1 })
}

const FALLBACK_WALK_FORWARD = {
  status: 'empty',
  generated_at: null,
  research_only: true,
  summary: {
    experiment_count: 0,
    draft_count: 0,
    frozen_or_locked_count: 0,
    status_counts: {},
    verdict_counts: {},
    latest_experiment_id: null,
    walk_forward_proof_ready: false,
    walk_forward_proof_status: 'needs_evidence',
    walk_forward_pass_rate: 0,
    walk_forward_requirements_passed: 0,
    walk_forward_requirements_total: 6,
    walk_forward_validation_status: 'blocked_by_evidence',
    walk_forward_validation_open_items: 6,
    walk_forward_validation_critical_open_items: 3,
    top_validation_item: 'Create and freeze an experiment snapshot',
    claim_permissions: {
      cautious_internal_repeatability_review: false,
      public_repeatability_claim: false,
      public_alpha_claim: false,
      live_trading_readiness: false,
      institutional_readiness: false,
    },
    research_only: true,
    storage: 'sanitized_runtime_metadata',
    paper_route_only: true,
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'research_metadata_only',
    writes_execution_config: false,
    writes_broker_config: false,
    writes_risk_config: false,
    writes_ranking_config: false,
  },
  walk_forward_validation_plan: {
    status: 'blocked_by_evidence',
    summary: {
      item_count: 6,
      open_item_count: 6,
      critical_open_items: 3,
      ready_item_count: 0,
      top_validation_item: 'Create and freeze an experiment snapshot',
      proof_first_rule: 'Ambition is allowed. Proof decides priority.',
      claim_permissions: {
        cautious_internal_repeatability_review: false,
        public_repeatability_claim: false,
        public_alpha_claim: false,
        live_trading_readiness: false,
        institutional_readiness: false,
      },
      blocked_claims: ['proven_alpha', 'guaranteed_returns', 'public_repeatability', 'institutional_readiness', 'live_trading_readiness'],
      safe_boundary: 'Walk-forward validation only records proof gaps and claim boundaries. It does not authorize orders, route changes, risk-gate changes, or ranking-weight mutation.',
    },
    items: [
      { key: 'create_frozen_experiment', title: 'Create and freeze an experiment snapshot', priority: 'critical', status: 'no_records', missing_fields: ['experiment_id', 'frozen_at', 'parameter_digest'], blocked_claims: ['repeatability_review', 'walk_forward_claim'], safe_next_action: 'Create a draft experiment with train, validation, test, and paper-forward windows, then freeze it before observing forward results.', manual_review_only: true, changes_execution: false, changes_ranking_weights: false },
      { key: 'chronological_no_lookahead_windows', title: 'Chronological no-lookahead windows', priority: 'critical', status: 'no_records', missing_fields: ['train_window', 'validation_window', 'test_window', 'paper_forward_window'], blocked_claims: ['repeatability_review', 'no_lookahead_claim'], safe_next_action: 'Define train, validation, test, and paper-forward windows in chronological order before any evaluation.', manual_review_only: true, changes_execution: false, changes_ranking_weights: false },
      { key: 'complete_version_snapshot', title: 'Complete version snapshot', priority: 'high', status: 'no_records', missing_fields: ['ranking_formula_version', 'reward_formula_version', 'forecast_model_version', 'baseline_definition_version', 'feature_version'], blocked_claims: ['auditability_claim', 'repeatability_review'], safe_next_action: 'Attach ranking, reward, forecast, baseline, feature, universe, data-source, and code-version snapshots.', manual_review_only: true, changes_execution: false, changes_ranking_weights: false },
      { key: 'out_of_sample_result', title: 'Out-of-sample result captured', priority: 'critical', status: 'no_records', missing_fields: ['verdict', 'baseline_relative_edge', 'score_bucket_lift', 'rewardable_count'], blocked_claims: ['repeatability_review', 'walk_forward_claim'], safe_next_action: 'Link at least one frozen experiment to a completed out-of-sample benchmark result after the test window closes.', manual_review_only: true, changes_execution: false, changes_ranking_weights: false },
      { key: 'after_cost_support', title: 'After-cost support', priority: 'high', status: 'no_records', missing_fields: ['execution_adjusted_reward', 'slippage_bps', 'spread_bps'], blocked_claims: ['tradability_review', 'cost_adjusted_repeatability'], safe_next_action: 'Attach slippage, spread, fill, or execution-adjusted reward evidence to evaluated frozen experiments.', manual_review_only: true, changes_execution: false, changes_ranking_weights: false },
      { key: 'pass_rate_threshold', title: 'Walk-forward pass-rate threshold', priority: 'high', status: 'no_records', missing_fields: ['passed_verdict_count', 'evaluated_record_count'], blocked_claims: ['repeatability_review', 'strategy_stability_claim'], safe_next_action: 'Run enough frozen out-of-sample experiments to measure whether the pass rate meets the configured threshold.', manual_review_only: true, changes_execution: false, changes_ranking_weights: false },
    ],
    safe_next_actions: [],
    research_only: true,
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'research_metadata_only',
  },
  proof_summary: {
    status: 'needs_evidence',
    proof_ready: false,
    requirements: [],
    record_readiness: [],
    summary: {
      record_count: 0,
      frozen_record_count: 0,
      no_lookahead_record_count: 0,
      version_complete_record_count: 0,
      evaluated_record_count: 0,
      passed_record_count: 0,
      after_cost_supported_record_count: 0,
      pass_rate: 0,
      minimum_pass_rate: 0.6,
      requirement_count: 6,
      passed_requirement_count: 0,
      missing_requirement_count: 6,
    },
    safe_next_actions: [],
    research_only: true,
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'research_metadata_only',
  },
  record: null,
  records: [],
  warnings: [],
  finish_tracker: FALLBACK_FINISH_TRACKER,
  safety_notes: [
    'Research only. Does not affect trading.',
    'Does not place orders.',
    'Does not change broker routes.',
    'Does not bypass risk gates.',
    'Does not change ranking weights automatically.',
    'Does not change risk settings automatically.',
  ],
  paper_route_only: true,
  can_submit_orders: false,
  can_submit_live_orders: false,
  mutation: 'research_metadata_only',
  writes_execution_config: false,
  writes_broker_config: false,
  writes_risk_config: false,
  writes_ranking_config: false,
}

export async function getWalkForwardSummary(params = {}) {
  return safeRequest(() => api.get('/walk-forward/summary', { params }), FALLBACK_WALK_FORWARD, { key: 'walk-forward:summary', retries: 1 })
}

export async function getWalkForwardExperiments(params = {}) {
  return safeRequest(() => api.get('/walk-forward/experiments', { params }), FALLBACK_WALK_FORWARD, { key: 'walk-forward:experiments', retries: 1 })
}

export async function getWalkForwardExperiment(experimentId) {
  return safeRequest(() => api.get(`/walk-forward/experiments/${encodeURIComponent(experimentId)}`), { ...FALLBACK_WALK_FORWARD, record: null }, { key: 'walk-forward:experiment', retries: 1 })
}

export async function createWalkForwardExperiment(payload) {
  return strictRequest(() => api.post('/walk-forward/experiments', payload), { retries: 0 })
}

export async function freezeWalkForwardExperiment(experimentId) {
  return strictRequest(() => api.post(`/walk-forward/experiments/${encodeURIComponent(experimentId)}/freeze`, {}), { retries: 0 })
}

export async function cloneWalkForwardExperiment(experimentId) {
  return strictRequest(() => api.post(`/walk-forward/experiments/${encodeURIComponent(experimentId)}/clone`, {}), { retries: 0 })
}

const FALLBACK_RESEARCH_PROMOTION_PROOF = {
  status: 'needs_evidence',
  proof_ready: false,
  requirements: [],
  summary: {
    record_count: 0,
    status_traceability_coverage: 0,
    criteria_traceability_coverage: 0,
    benchmark_traceability_coverage: 0,
    data_quality_traceability_coverage: 0,
    walk_forward_traceability_coverage: 0,
    execution_traceability_coverage: 0,
    manual_review_record_count: 0,
    manual_review_traceability_coverage: 0,
    promotion_traceability_coverage: 0,
    promotion_metadata_only: 1,
    safety_boundary_preserved: 1,
    requirement_count: 10,
    passed_requirement_count: 0,
    missing_requirement_count: 10,
  },
  record_readiness: [],
  safe_next_actions: [],
  research_only: true,
  paper_route_only: true,
  can_submit_orders: false,
  can_submit_live_orders: false,
  mutation: 'research_metadata_only',
  writes_execution_config: false,
  writes_broker_config: false,
  writes_risk_config: false,
  writes_ranking_config: false,
}

const FALLBACK_RESEARCH_PROMOTION = {
  status: 'empty',
  generated_at: null,
  research_only: true,
  summary: {
    entity_count: 0,
    status_counts: {},
    type_counts: {},
    paper_proven_count: 0,
    needs_more_evidence_count: 0,
    rejected_count: 0,
    benchmark_verdict: 'insufficient_evidence',
    walk_forward_status: null,
    walk_forward_verdict: null,
    data_quality_score: null,
    promotion_proof_ready: false,
    promotion_proof_status: 'needs_evidence',
    promotion_requirements_passed: 0,
    promotion_requirements_total: 10,
    promotion_traceability_coverage: 0,
    benchmark_traceability_coverage: 0,
    walk_forward_traceability_coverage: 0,
    execution_traceability_coverage: 0,
    manual_review_record_count: 0,
    research_only: true,
    paper_route_only: true,
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'research_metadata_only',
    writes_execution_config: false,
    writes_broker_config: false,
    writes_risk_config: false,
    writes_ranking_config: false,
  },
  promotion_status: 'summary',
  record: null,
  records: [],
  evidence_used: {},
  proof_summary: FALLBACK_RESEARCH_PROMOTION_PROOF,
  aggregations: { research_promotion_proof: FALLBACK_RESEARCH_PROMOTION_PROOF },
  warnings: [],
  finish_tracker: FALLBACK_FINISH_TRACKER,
  safety_notes: [
    'Research only. Does not affect trading.',
    'Does not place orders.',
    'Does not change broker routes.',
    'Does not bypass risk gates.',
    'Does not change ranking weights automatically.',
    'Does not change risk limits automatically.',
    'Does not grant AI order authority.',
  ],
  paper_route_only: true,
  can_submit_orders: false,
  can_submit_live_orders: false,
  mutation: 'research_metadata_only',
  writes_execution_config: false,
  writes_broker_config: false,
  writes_risk_config: false,
  writes_ranking_config: false,
}

export async function getResearchPromotionSummary(params = {}) {
  return safeRequest(() => api.get('/research-promotion/summary', { params }), FALLBACK_RESEARCH_PROMOTION, { key: 'research-promotion:summary', retries: 1 })
}

export async function getResearchPromotionEntities(params = {}) {
  return safeRequest(() => api.get('/research-promotion/entities', { params }), FALLBACK_RESEARCH_PROMOTION, { key: 'research-promotion:entities', retries: 1 })
}

export async function getResearchPromotionEntity(entityId) {
  return safeRequest(() => api.get(`/research-promotion/entities/${encodeURIComponent(entityId)}`), { ...FALLBACK_RESEARCH_PROMOTION, record: null }, { key: 'research-promotion:entity', retries: 1 })
}

export async function setResearchPromotionStatus(entityId, payload) {
  return strictRequest(() => api.post(`/research-promotion/entities/${encodeURIComponent(entityId)}/status`, payload), { retries: 0 })
}

const FALLBACK_SCORE_CALIBRATION = {
  status: 'empty',
  generated_at: null,
  research_only: true,
  summary: {
    status: 'empty',
    candidate_count: 0,
    rewardable_count: 0,
    non_rewardable_count: 0,
    score_scale: { scale: 'missing', description: 'No score fields were present.' },
    bucket_lift: null,
    monotonicity_score: null,
    calibration_warning: 'Need rewardable records in multiple score buckets before calibration can be trusted.',
    score_to_reward_correlation: null,
    score_to_forecast_accuracy_correlation: null,
    score_to_execution_adjusted_reward_correlation: null,
    calibration_proof_ready: false,
    calibration_proof_status: 'needs_evidence',
    calibration_requirements_passed: 0,
    calibration_requirements_total: 7,
    after_cost_bucket_lift: null,
    sufficient_feature_count: 0,
    score_calibration_hardening_status: 'blocked_by_evidence',
    score_calibration_hardening_open_items: 7,
    score_calibration_hardening_critical_open_items: 2,
    top_hardening_item: 'Rewardable score sample',
    claim_permissions: {
      cautious_internal_calibration_review: false,
      ranking_weight_change: false,
      automatic_ranking_mutation: false,
      public_score_quality_claim: false,
      repeatability_claim: false,
      live_trading_readiness: false,
    },
    research_only: true,
    paper_route_only: true,
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'none',
    writes_execution_config: false,
    writes_broker_config: false,
    writes_risk_config: false,
    writes_ranking_config: false,
  },
  records: [],
  proof_summary: {
    status: 'needs_evidence',
    proof_ready: false,
    requirements: [],
    summary: {
      record_count: 0,
      rewardable_count: 0,
      score_bucket_coverage: 0,
      bucket_lift: null,
      after_cost_bucket_lift: null,
      monotonicity_score: null,
      feature_count: 0,
      sufficient_feature_count: 0,
      manual_review_only_count: 0,
      requirement_count: 7,
      passed_requirement_count: 0,
      missing_requirement_count: 7,
    },
    feature_readiness: [],
    safe_next_actions: [],
    research_only: true,
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'none',
  },
  score_calibration_hardening_plan: {
    status: 'blocked_by_evidence',
    summary: {
      item_count: 7,
      open_item_count: 7,
      critical_open_items: 2,
      ready_item_count: 0,
      top_hardening_item: 'Rewardable score sample',
      proof_first_rule: 'Ambition is allowed. Proof decides priority.',
      claim_permissions: {
        cautious_internal_calibration_review: false,
        ranking_weight_change: false,
        automatic_ranking_mutation: false,
        public_score_quality_claim: false,
        repeatability_claim: false,
        live_trading_readiness: false,
      },
      blocked_claims: ['proven_score_quality', 'automatic_ranking_change', 'public_alpha_or_performance', 'repeatability', 'promotion_readiness', 'live_trading_readiness'],
      safe_boundary: 'Score Calibration hardening only records proof gaps and claim boundaries. It does not authorize ranking-weight changes, orders, broker-route changes, or risk-gate changes.',
    },
    items: [
      { key: 'rewardable_score_sample', title: 'Rewardable score sample', priority: 'critical', status: 'needs_evidence', missing_fields: ['score', 'total_reward', 'actual_forward_return', 'baseline_forward_return'], blocked_claims: ['score_quality_claim', 'ranking_quality_review'], safe_next_action: 'Collect rewardable score records with closed-horizon outcomes, total reward, same-window baselines, and score lineage before reviewing calibration.', manual_review_only: true, changes_execution: false, changes_ranking_weights: false },
      { key: 'score_bucket_coverage', title: 'Score bucket coverage', priority: 'critical', status: 'needs_evidence', missing_fields: ['score', 'score_bucket', 'total_reward'], blocked_claims: ['score_separation_claim', 'ranking_quality_review'], safe_next_action: 'Collect rewardable rows across low, middle, and high score buckets before judging score separation.', manual_review_only: true, changes_execution: false, changes_ranking_weights: false },
      { key: 'bucket_lift_monotonicity', title: 'Bucket lift and monotonicity', priority: 'high', status: 'needs_evidence', missing_fields: ['score_bucket', 'total_reward', 'actual_forward_return'], blocked_claims: ['ranking_quality_claim', 'score_formula_review'], safe_next_action: 'Verify high-score buckets beat lower buckets and adjacent buckets mostly improve before any score-quality language.', manual_review_only: true, changes_execution: false, changes_ranking_weights: false },
      { key: 'after_cost_bucket_lift', title: 'After-cost bucket lift', priority: 'high', status: 'needs_evidence', missing_fields: ['execution_adjusted_reward', 'slippage_bps', 'spread_bps', 'paper_fill_price'], blocked_claims: ['execution_adjusted_score_quality', 'tradability_claim'], safe_next_action: 'Attach paper execution cost evidence and confirm score separation survives spread and slippage.', manual_review_only: true, changes_execution: false, changes_ranking_weights: false },
      { key: 'feature_attribution_coverage', title: 'Feature attribution coverage', priority: 'high', status: 'needs_evidence', missing_fields: ['setup_type', 'engine', 'regime', 'component_scores', 'total_reward'], blocked_claims: ['feature_driver_claim', 'feature_weight_review'], safe_next_action: 'Collect repeated feature observations with outcomes before treating feature lift as more than small-sample research.', manual_review_only: true, changes_execution: false, changes_ranking_weights: false },
      { key: 'manual_review_governance', title: 'Manual review governance', priority: 'high', status: 'needs_evidence', missing_fields: ['manual_review_note'], blocked_claims: ['automatic_ranking_change', 'ai_weight_change'], safe_next_action: 'Keep all calibration recommendations as manual review notes and keep ranking config unchanged.', manual_review_only: true, changes_execution: false, changes_ranking_weights: false },
      { key: 'walk_forward_confirmation', title: 'Walk-forward confirmation', priority: 'high', status: 'needs_evidence', missing_fields: ['sample_split', 'experiment_version', 'out_of_sample_window', 'forward_only_outcome'], blocked_claims: ['repeatability_claim', 'promotion_readiness', 'public_score_quality_claim'], safe_next_action: 'Confirm any promising score separation in frozen walk-forward experiments before repeatability or promotion language.', manual_review_only: true, changes_execution: false, changes_ranking_weights: false },
    ],
    safe_next_actions: [],
    research_only: true,
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'none',
  },
  aggregations: {
    score_bucket_separation: { available: false, items: [], bucket_lift: null, monotonicity_score: null },
    feature_attribution: { available: false, items: [], top_positive_features: [], top_negative_features: [], false_positive_drivers: [], false_negative_drivers: [] },
    calibration_proof: { status: 'needs_evidence', proof_ready: false, requirements: [] },
    score_calibration_hardening_plan: {
      status: 'blocked_by_evidence',
      summary: {
        item_count: 7,
        open_item_count: 7,
        critical_open_items: 2,
        ready_item_count: 0,
        top_hardening_item: 'Rewardable score sample',
        claim_permissions: {
          cautious_internal_calibration_review: false,
          ranking_weight_change: false,
          automatic_ranking_mutation: false,
          public_score_quality_claim: false,
          repeatability_claim: false,
          live_trading_readiness: false,
        },
        blocked_claims: ['proven_score_quality', 'automatic_ranking_change', 'repeatability', 'live_trading_readiness'],
      },
      items: [],
    },
    setup_specific_lift: [],
    engine_specific_lift: [],
    regime_specific_lift: [],
    recommendations: [],
  },
  warnings: [],
  missing_fields: {},
  finish_tracker: FALLBACK_FINISH_TRACKER,
  safety_notes: [
    'Research only. Does not affect trading.',
    'Does not place orders.',
    'Does not change broker routes.',
    'Does not bypass risk gates.',
    'Does not change ranking weights automatically.',
    'Does not grant AI order authority.',
  ],
  paper_route_only: true,
  can_submit_orders: false,
  can_submit_live_orders: false,
  mutation: 'none',
  writes_execution_config: false,
  writes_broker_config: false,
  writes_risk_config: false,
  writes_ranking_config: false,
}

export async function getScoreCalibrationSummary(params = {}) {
  return safeRequest(() => api.get('/score-calibration/summary', { params }), FALLBACK_SCORE_CALIBRATION, { key: 'score-calibration:summary', retries: 1 })
}

export async function getScoreCalibrationBuckets(params = {}) {
  return safeRequest(() => api.get('/score-calibration/buckets', { params }), { ...FALLBACK_SCORE_CALIBRATION, records: [] }, { key: 'score-calibration:buckets', retries: 1 })
}

export async function getScoreCalibrationFeatures(params = {}) {
  return safeRequest(() => api.get('/score-calibration/features', { params }), { ...FALLBACK_SCORE_CALIBRATION, records: [] }, { key: 'score-calibration:features', retries: 1 })
}

export async function getScoreCalibrationRegimes(params = {}) {
  return safeRequest(() => api.get('/score-calibration/regimes', { params }), { ...FALLBACK_SCORE_CALIBRATION, records: [] }, { key: 'score-calibration:regimes', retries: 1 })
}

export async function getScoreCalibrationRecommendations(params = {}) {
  return safeRequest(() => api.get('/score-calibration/recommendations', { params }), { ...FALLBACK_SCORE_CALIBRATION, records: [] }, { key: 'score-calibration:recommendations', retries: 1 })
}

export async function getOrganizationExecutionDiagnostics() {
  return safeRequest(() => api.get('/orgs/execution/diagnostics'), { configured: {}, providers: {} }, { key: 'orgs:execution-diagnostics', retries: 2, retryDelayMs: 250 })
}
export async function getOrganizationTradeAutomationDailyLedger(params = {}) {
  return safeRequest(
    () => api.get('/orgs/trade-automation/daily-ledger', { params }),
    { items: [], count: 0, returned_count: 0 },
    { key: 'orgs:trade-automation-daily-ledger', retries: 1 },
  )
}
export async function getOrganizationTradeAutomationDailySafetySummary(params = {}) {
  return safeRequest(
    () => api.get('/orgs/trade-automation/daily-safety-summary', { params }),
    { record_count: 0, latest_status: 'unknown', strongest_status: 'unknown', status_counts: {}, event_type_counts: {} },
    { key: 'orgs:trade-automation-daily-safety-summary', retries: 1 },
  )
}
export async function getOrganizationTradeAutomationHftWatchdogLatest() {
  return safeRequest(
    () => api.get('/orgs/trade-automation/hft-watchdog/latest'),
    { available: false, status: 'not_started', message: 'No HFT watchdog summary has been written yet.' },
    { key: 'orgs:trade-automation-hft-watchdog', retries: 1 },
  )
}
export async function getOrganizationTradeAutomationAlpacaPaperReadiness() {
  return safeRequest(
    () => api.get('/orgs/trade-automation/alpaca-paper-readiness'),
    {
      status: 'degraded',
      provider: 'alpaca',
      mode: 'paper',
      route: 'broker_paper',
      credentials: { api_key_present: false, secret_key_present: false, secrets_exposed: false },
      account_heartbeat: { available: false, buying_power_is_ceiling: true },
      reconciliation: { open_count: 0, pending_count: 0, closed_count: 0, needs_review: false },
    },
    { key: 'orgs:trade-automation-alpaca-paper-readiness', retries: 1 },
  )
}
export async function getOrganizationTradeAutomationAiEvidenceReviewStatus() {
  return safeRequest(
    () => api.get('/orgs/trade-automation/ai-evidence-review/status'),
    {
      status: 'disabled',
      mode: 'shadow_review',
      settings: {
        ai_evidence_review_enabled: false,
        ai_evidence_review_mode: 'shadow_review',
        ai_evidence_min_confidence: 0.7,
        ai_evidence_max_candidates_per_cycle: 12,
      },
      safety: { paper_route_only: true, can_override_risk_gates: false, can_submit_orders: false },
    },
    { key: 'orgs:trade-automation-ai-evidence-review', retries: 1 },
  )
}
export async function getOrganizationTradeAutomationMarketSession() {
  return safeRequest(
    () => api.get('/orgs/trade-automation/market-session'),
    {
      status: 'degraded',
      label: 'Needs attention',
      tone: 'warning',
      phase: { phase: 'unknown' },
      components: [],
      desks: { items: [], count: 0 },
      no_trade_escalation: { stage: 'monitoring' },
      links: {},
      paper_route_only: true,
      mutation: 'none',
    },
    { key: 'orgs:trade-automation-market-session', retries: 1 },
  )
}
export async function getOrganizationTradeAutomationNoTradeReport() {
  return safeRequest(
    () => api.get('/orgs/trade-automation/no-trade-report'),
    {
      read_only: true,
      mutation: 'none',
      can_submit_orders: false,
      escalation_stage: 'monitoring',
      trades_today: 0,
      desk_reports: [],
      operator_actions: [],
    },
    { key: 'orgs:trade-automation-no-trade-report', retries: 1 },
  )
}
export async function getOrganizationTradeAutomationMarketDayReport() {
  return safeRequest(
    () => api.get('/orgs/trade-automation/market-day-report'),
    {
      ok: false,
      artifact: null,
      market_session: null,
      no_trade_report: null,
      close_report: null,
      paper_route_only: true,
      mutation: 'none',
    },
    { key: 'orgs:trade-automation-market-day-report', retries: 1 },
  )
}
export async function updateOrganizationTradeAutomationDesk(deskKey, payload = {}) {
  return unwrap(await api.patch(`/orgs/trade-automation/desks/${encodeURIComponent(deskKey)}`, payload || {}))
}
export async function scanOrganizationTradeAutomationDesk(deskKey, payload = {}) {
  return unwrap(await api.post(`/orgs/trade-automation/desks/${encodeURIComponent(deskKey)}/scan`, payload || {}))
}
export async function getOrganizationTradeAutomationDeskCandidateDiagnostics(deskKey, options = {}) {
  const params = {}
  if (options.refresh) params.refresh = true
  return strictRequest(() => api.get(`/orgs/trade-automation/desks/${encodeURIComponent(deskKey)}/candidate-diagnostics`, { params }))
}
export async function getInternalBrokerRouter() {
  return safeRequest(
    () => api.get('/orgs/paper-execution-router'),
    {
      status: 'unknown',
      mode: 'alpaca_paper',
      service_label: 'Alpaca paper route monitor',
      execution_router_mode: 'alpaca_paper',
      deprecated_alias: 'internal-broker-router',
      broker_mode: 'alpaca_paper',
      paper_only: true,
      regulated_broker_dealer: false,
      real_money_execution_enabled: false,
      licensed_realtime_options_data: false,
      health: {
        status: 'degraded',
        detail: 'Alpaca paper route snapshot is not available yet.',
        metrics: {},
      },
      routing: {
        equities: 'alpaca_paper',
        options: 'alpaca_paper',
        options_data: 'free_delayed',
        execution_venue: 'alpaca_paper',
        live_routing_enabled: false,
      },
      balances: {
        internal_simulated: {
          equity: 100000,
          cash: 100000,
          buying_power: 100000,
          option_buying_power: 100000,
          status: 'ready',
        },
        alpaca_paper: { status: 'disabled' },
        combined_paper: {
          equity: 100000,
          cash: 100000,
          buying_power: 100000,
          status: 'ready',
        },
      },
      orders: { open: [], rejected: [], recent_fills: [] },
      positions: [],
      audit: { hash_chain_valid: true, latest_events: [] },
    },
    { key: 'orgs:internal-broker-router', retries: 1 },
  )
}
export async function submitInternalBrokerPaperOrder(payload = {}) {
  return unwrap(await api.post('/orgs/paper-execution-router/orders', payload || {}))
}
export async function cancelInternalBrokerPaperOrder(brokerOrderId) {
  return unwrap(await api.post(`/orgs/paper-execution-router/orders/${encodeURIComponent(brokerOrderId)}/cancel`, {}))
}
export async function syncInternalBrokerRouter() {
  return unwrap(await api.post('/orgs/paper-execution-router/sync', {}))
}
export async function updateOrganizationTradeAutomation(payload, options = {}) {
  const nextPayload = {
    ...(payload || {}),
    ...(options.scope ? { scope: options.scope } : {}),
    ...(options.scope_key ? { scope_key: options.scope_key } : {}),
    ...(options.linked_account_id ? { linked_account_id: options.linked_account_id } : {}),
  }
  return unwrap(await api.patch('/orgs/trade-automation', nextPayload))
}
export async function runOrganizationTradeAutomationAction(actionOrPayload, options = {}) {
  const payload = typeof actionOrPayload === 'string'
    ? { action: actionOrPayload }
    : actionOrPayload
  return unwrap(await api.post('/orgs/trade-automation/actions', {
    ...(payload || {}),
    ...(options.scope ? { scope: options.scope } : {}),
    ...(options.scope_key ? { scope_key: options.scope_key } : {}),
    ...(options.linked_account_id ? { linked_account_id: options.linked_account_id } : {}),
  }))
}
export async function getStrategyDesks() {
  return safeRequest(() => api.get('/orgs/strategy-desks'), { items: [], count: 0 }, { key: 'orgs:strategy-desks', retries: 1 })
}
export async function getStrategyDesk(deskKey) {
  return strictRequest(() => api.get(`/orgs/strategy-desks/${encodeURIComponent(deskKey)}`), { retries: 1 })
}
export async function updateStrategyDesk(deskKey, payload) {
  return unwrap(await api.patch(`/orgs/strategy-desks/${encodeURIComponent(deskKey)}`, payload || {}))
}
export async function runStrategyDesk(deskKey, payload = {}) {
  return unwrap(await api.post(`/orgs/strategy-desks/${encodeURIComponent(deskKey)}/runs`, payload || {}))
}
export async function getStrategyDeskMetrics(deskKey) {
  return strictRequest(() => api.get(`/orgs/strategy-desks/${encodeURIComponent(deskKey)}/metrics`), { retries: 1 })
}
export async function getAiDeskManagerSnapshot() {
  return safeRequest(
    () => api.get('/orgs/ai-desk-manager'),
    {
      status: 'watch',
      command_center: {},
      desk_states: [],
      next_actions: [],
      trade_planner: { latest_targets: { targets: [] } },
      paper_execution: { latest_execution: {}, risk: {} },
      live_gate: { allowed: false, blockers: [] },
      conflicts: [],
      policy: { manifest: {}, enabled: false, armed: false, kill_switch: false },
      autonomy: { enabled: false, armed: false, kill_switch: false },
      agents: [],
      latest_cycle: {},
      active_blockers: [],
      next_scheduled_run: null,
    },
    { key: 'orgs:ai-desk-manager', retries: 1 },
  )
}
export async function getAiDeskPolicy() {
  return unwrap(await api.get('/orgs/ai-desk-manager/policy'))
}
export async function updateAiDeskPolicy(payload = {}) {
  return unwrap(await api.put('/orgs/ai-desk-manager/policy', payload || {}))
}
export async function runAiDeskControl(payload = {}) {
  return unwrap(await api.post('/orgs/ai-desk-manager/control', payload || {}))
}
export async function runAiAutonomousCycle(payload = {}) {
  return unwrap(await api.post('/orgs/ai-desk-manager/autonomous-cycle', payload || {}))
}
export async function previewAiTradePlan(payload = {}) {
  return unwrap(await api.post('/orgs/ai-desk-manager/trade-plans', payload || {}))
}
export async function executeAiPaperExecution(payload = {}) {
  return unwrap(await api.post('/orgs/ai-desk-manager/paper-executions', payload || {}))
}
export async function createAiLiveIntent(payload = {}) {
  return unwrap(await api.post('/orgs/ai-desk-manager/live-intents', payload || {}))
}
export async function runStrategyBacktest(payload) {
  return unwrap(await api.post('/orgs/backtests', payload || {}))
}
export async function getStrategyBacktest(runId) {
  return strictRequest(() => api.get(`/orgs/backtests/${encodeURIComponent(runId)}`), { retries: 1 })
}
export async function getAllocatorSnapshot() {
  return safeRequest(() => api.get('/orgs/allocator'), { latest_run_id: null, status: 'idle', targets: [], metrics: {}, risk: {} }, { key: 'orgs:allocator', retries: 1 })
}
export async function getRiskSnapshot() {
  return safeRequest(() => api.get('/orgs/risk'), { gross_exposure: 0, net_exposure: 0, symbol_count: 0, allowed: true, target_count: 0, source_run_id: null }, { key: 'orgs:risk', retries: 1 })
}
export async function getLatestPortfolioTargets() {
  return safeRequest(() => api.get('/orgs/portfolio-targets/latest'), { latest_run_id: null, status: 'idle', targets: [], metrics: {}, risk: {}, order_plan: {} }, { key: 'orgs:portfolio-targets', retries: 1 })
}
export async function getOptionsAutomationSnapshot() {
  return safeRequest(
    () => api.get('/orgs/options-automation'),
    {
      latest_scan_run_id: null,
      status: 'idle',
      feed: 'opra',
      scan_interval_seconds: 30,
      ticker_count: 0,
      candidate_count: 0,
      ready_candidate_count: 0,
      blocked_reason: null,
      summary: {},
      candidates: [],
      blockers: [],
      readiness_state: 'collecting_lifecycle_evidence',
      readiness_label: 'collecting lifecycle evidence',
      required_clean_cycles: 5,
      clean_cycle_count: 0,
      clean_entry_count: 0,
      clean_exit_count: 0,
      blocked_entry_count: 0,
      blocked_exit_count: 0,
      stale_quote_block_count: 0,
      open_position_count: 0,
      working_order_count: 0,
      last_broker_sync_at: null,
      last_clean_lifecycle_at: null,
      recent_clean_cycles: [],
      next_step: 'Keep collecting unchanged until 5 clean scheduled paper-option lifecycles are recorded.',
      validation_artifact: {
        validation_scope: 'personal_paper',
        readiness_state: 'collecting_lifecycle_evidence',
        readiness_label: 'collecting lifecycle evidence',
        required_clean_cycles: 5,
        clean_cycle_count: 0,
        clean_entry_count: 0,
        clean_exit_count: 0,
        blocked_entry_count: 0,
        blocked_exit_count: 0,
        stale_quote_block_count: 0,
        open_position_count: 0,
        working_order_count: 0,
        last_broker_sync_at: null,
        last_clean_lifecycle_at: null,
        blockers: [],
        next_step: 'Keep collecting unchanged until 5 clean scheduled paper-option lifecycles are recorded.',
        orphan_event_count: 0,
        orphan_events: [],
        recent_clean_cycles: [],
        last_broker_linked_entry: null,
        last_broker_linked_exit: null,
      },
      lifecycle: {
        opra_entitlement_status: 'unknown',
        latest_scan: null,
        latest_paper_execution: null,
        latest_quote_refresh: null,
        latest_paper_exit: null,
        latest_broker_sync: null,
        open_position_count: 0,
        sell_ready_count: 0,
        blocked_position_count: 0,
        blockers: [],
        validation_artifact: null,
      },
      latest_paper_execution: null,
      latest_quote_refresh: null,
      latest_paper_exit: null,
      latest_broker_sync: null,
      open_positions: [],
      policy: {
        scope: 'personal_paper',
        execution_intent: 'broker_paper',
        instrument_type: 'listed_option',
        option_strategy: 'long_option',
        supported_rights: ['call', 'put'],
        limit_orders_only: true,
        live_routing_enabled: false,
        brokerage_linked_routing_enabled: false,
        short_premium_enabled: false,
        spreads_enabled: false,
      },
    },
    { key: 'orgs:options-automation', retries: 1 },
  )
}
export async function scanOptionsAutomation(payload = {}) {
  return unwrap(await api.post('/orgs/options-automation/scan', payload || {}))
}
export async function executeOptionsPaper(payload = {}) {
  return unwrap(await api.post('/orgs/options-automation/execute-paper', payload || {}))
}
export async function refreshOptionsAutomationPositions(payload = {}) {
  return unwrap(await api.post('/orgs/options-automation/refresh-positions', payload || {}))
}
export async function closeOptionsPaper(payload = {}) {
  return unwrap(await api.post('/orgs/options-automation/close-paper', payload || {}))
}
export async function syncOptionsAutomation() {
  return unwrap(await api.post('/orgs/options-automation/sync', {}))
}
export async function executeLatestPortfolioTargets(payload = {}) {
  return unwrap(await api.post('/orgs/portfolio-targets/execute', payload || {}))
}
export async function getLatestPortfolioTargetExecution() {
  return safeRequest(
    () => api.get('/orgs/portfolio-targets/executions/latest'),
    {
      latest_execution_run_id: null,
      portfolio_target_run_id: null,
      status: 'idle',
      execution_intent: 'broker_paper',
      dry_run: false,
      working_count: 0,
      partial_fill_count: 0,
      filled_count: 0,
      canceled_count: 0,
      rejected_count: 0,
      orphan_event_count: 0,
      last_sync_at: null,
      validation_artifact: {
        validation_scope: 'personal_paper',
        readiness_state: 'collecting_lifecycle_evidence',
        readiness_label: 'collecting lifecycle evidence',
        submitted_count: 0,
        active_submitted_count: 0,
        working_count: 0,
        partial_fill_count: 0,
        filled_count: 0,
        canceled_count: 0,
        rejected_count: 0,
        skipped_count: 0,
        blocked_count: 0,
        orphan_event_count: 0,
        broker_linked_item_count: 0,
        clean_run: false,
        blockers: [],
        next_step: 'Run macro/stat-arb desks, execute a personal-paper basket, then refresh execution to collect lifecycle evidence.',
        orphan_events: [],
      },
      summary: {},
      items: [],
    },
    { key: 'orgs:portfolio-targets:execution', retries: 1 },
  )
}
export async function getPortfolioTargetExecution(executionRunId) {
  return strictRequest(() => api.get(`/orgs/portfolio-targets/executions/${encodeURIComponent(executionRunId)}`), { retries: 1 })
}
export async function syncPortfolioTargetExecution(executionRunId) {
  return unwrap(await api.post(`/orgs/portfolio-targets/executions/${encodeURIComponent(executionRunId)}/sync`))
}
export async function getOrganizationDelivery() { return strictRequest(() => api.get('/orgs/delivery')) }
export async function updateOrganizationDelivery(payload) { return unwrap(await api.patch('/orgs/delivery', payload)) }
export async function runOrganizationDeliveryAction(actionOrPayload, providerId) {
  const payload = typeof actionOrPayload === 'string'
    ? { action: actionOrPayload, provider_id: providerId || undefined }
    : actionOrPayload
  return unwrap(await api.post('/orgs/delivery/actions', payload))
}
export async function getOrganizationAnalytics() { return strictRequest(() => api.get('/orgs/analytics')) }
export async function inviteOrganizationMember(payload) { return unwrap(await api.post('/orgs/members/invite', payload)) }
export async function updateOrganizationMember(payload) { return unwrap(await api.patch('/orgs/members', payload)) }
export async function removeOrganizationMember(membershipId) { return unwrap(await api.post('/orgs/members/remove', { membership_id: membershipId })) }
export async function runOrganizationInvitationAction(payload) { return unwrap(await api.post('/orgs/members/invitations/actions', payload)) }
export async function getOrganizationApiTokens() { return strictRequest(() => api.get('/orgs/tokens')) }
export async function getOrganizationApiUsage() { return strictRequest(() => api.get('/orgs/api-usage')) }
export async function getOrganizationSecurity() { return strictRequest(() => api.get('/orgs/security')) }
export async function createOrganizationApiToken(payload) { return unwrap(await api.post('/orgs/tokens', payload)) }
export async function revokeOrganizationApiToken(tokenId) { return unwrap(await api.post('/orgs/tokens/revoke', { token_id: tokenId })) }
export async function getOrganizationWebhooks() { return strictRequest(() => api.get('/orgs/webhooks')) }
export async function createOrganizationWebhook(payload) { return unwrap(await api.post('/orgs/webhooks', payload)) }
export async function runOrganizationWebhookAction(payload) { return unwrap(await api.post('/orgs/webhooks/actions', payload)) }
export async function getOrganizationFeatureFlags() { return strictRequest(() => api.get('/orgs/flags')) }
export async function updateOrganizationFeatureFlag(payload) { return unwrap(await api.patch('/orgs/flags', payload)) }
export async function getOrganizationOnboarding() { return strictRequest(() => api.get('/orgs/onboarding')) }
export async function updateOrganizationOnboardingStep(payload) { return unwrap(await api.patch('/orgs/onboarding', payload)) }
export async function getOrganizationOnboardingTemplates() { return strictRequest(() => api.get('/orgs/onboarding/templates')) }
export async function applyOrganizationOnboardingTemplate(templateKey) { return unwrap(await api.post('/orgs/onboarding/templates/apply', { template_key: templateKey })) }
export async function seedOrganizationWorkspace() { return unwrap(await api.post('/orgs/onboarding/seed-workspace')) }
export async function getOrganizationSupportSnapshot() { return strictRequest(() => api.get('/orgs/support')) }
export async function updateOrganizationStatus(status) { return unwrap(await api.post('/orgs/status', { status })) }
export async function getBillingPlans() { return strictRequest(() => api.get('/billing/plans')) }
export async function getBillingSummary() { return strictRequest(() => api.get('/billing/summary')) }
export async function getBillingEntitlements() { return strictRequest(() => api.get('/billing/entitlements')) }
export async function changeBillingPlan(planKey) { return unwrap(await api.post('/billing/change-plan', { plan_key: planKey })) }
export async function createBillingCheckoutSession(payload) { return unwrap(await api.post('/billing/checkout', payload)) }
export async function openBillingPortal() { return unwrap(await api.post('/billing/portal')) }
export async function runBillingRecovery(action) { return unwrap(await api.post('/billing/recover', { action })) }
export async function getDashboard(consumer = 'desk', options = {}) {
  const normalizedConsumer = String(consumer || 'desk').trim().toLowerCase()
  const normalizedAccountProfile = String(options.account_profile || 'personal_paper').trim().toLowerCase() || 'personal_paper'
  const normalizedLinkedAccountId = String(options.linked_account_id || '').trim()
  const cacheKey = `trade-desk:dashboard:${normalizedConsumer}:${normalizedAccountProfile}:${normalizedLinkedAccountId}`
  try {
    const payload = await strictRequest(() => api.get('/market/dashboard', {
      params: {
        consumer,
        account_profile: normalizedAccountProfile,
        linked_account_id: normalizedLinkedAccountId || undefined,
      },
      timeout: 20000,
    }), { retries: 1, retryDelayMs: 400 })
    writeSessionCache(cacheKey, payload)
    return payload
  } catch (error) {
    warnApiFallback(`dashboard:${normalizedConsumer}:${normalizedAccountProfile}`, error)
    const cached = readSessionCache(cacheKey)
    if (cached) {
      return cached
    }
    const liveFallback = await buildLiveBatchDashboardFallback()
    return liveFallback || createFallbackDashboard()
  }
}
export async function analyzeTicker(payload) { return unwrap(await api.post('/market/analyze', payload, { timeout: 70000 })) }
export async function getChart(ticker, interval = '5m', pointsLimit = 300, regularHoursOnly = false) {
  return unwrap(
    await api.get(`/market/chart/${ticker}`, {
      params: { interval, points_limit: pointsLimit, regular_hours_only: regularHoursOnly },
    }),
  )
}
export async function getLiveBatch(tickers) { return safeRequest(() => api.post('/market/live/batch', { tickers }), { rows: [], prices: {}, count: 0, timestamp: null }, { key: 'market:live-batch', retries: 1 }) }
export async function getWatchlist(payload) { return safeRequest(() => api.post('/market/watchlist', payload), { summary: { valid_trades: 0, high_conviction: 0, entry_now: 0 }, rows: [], results: [], count: 0, errors: [], validation_artifact: null }) }
export async function compareTickers(payload) { return safeRequest(() => api.post('/market/compare', payload), { interval: payload?.interval || '5m', horizon: payload?.horizon || 5, tickers: payload?.tickers || [], rows: [], charts: {}, leader: null, summary: { count: 0, valid_trades: 0, bullish_count: 0, bearish_count: 0, average_setup_score: null, leader: null }, errors: [], validation_artifact: null }) }
export async function getLinkedBrokerageAccounts() {
  return safeRequest(() => api.get('/me/brokerage-accounts'), {
    items: [],
    count: 0,
    oauth_configured: false,
    provider: 'alpaca',
    automation_summary: {
      eligible_linked_account_count: 0,
      automated_linked_account_count: 0,
      blocked_linked_account_count: 0,
      last_automated_client_order: null,
      block_reasons_by_account: {},
      items: [],
    },
  })
}
export async function startAlpacaLinkedAccount(payload) { return unwrap(await api.post('/me/brokerage-accounts/alpaca/start', payload)) }
export async function refreshLinkedBrokerageAccount(linkedAccountId) { return unwrap(await api.post(`/me/brokerage-accounts/${linkedAccountId}/refresh`)) }
export async function updateLinkedBrokerageAccount(linkedAccountId, payload) { return unwrap(await api.patch(`/me/brokerage-accounts/${linkedAccountId}`, payload)) }
export async function unlinkLinkedBrokerageAccount(linkedAccountId) { return unwrap(await api.post(`/me/brokerage-accounts/${linkedAccountId}/unlink`)) }
export async function getOpenTrades({ search = '', limit = 250, offset = 0, actionFilter = 'all' } = {}) { return safeRequest(() => api.get('/trades/open', { params: { search, limit, offset, action_filter: actionFilter } }), { ...FALLBACK_OPEN_TRADES, limit, offset, action_filter: actionFilter }) }
export async function closeTrade(payload) { return unwrap(await api.post('/trades/close', payload)) }
export async function previewTrade(payload) { return unwrap(await api.post('/trades/preview', payload)) }
export async function openTrade(payload) { return unwrap(await api.post('/trades/open', payload)) }
export async function createTradeIntent(payload) { return unwrap(await api.post('/trades/intents', payload)) }
export async function getTradeIntents({ status = 'pending_approval' } = {}) { return safeRequest(() => api.get('/trades/intents', { params: { status } }), { items: [], count: 0, status_counts: {}, broker_ops: {} }) }
export async function approveTradeIntent(intentId, payload = {}) { return unwrap(await api.post(`/trades/intents/${intentId}/approve`, payload)) }
export async function conditionallyApproveTradeIntent(intentId, payload = {}) { return unwrap(await api.post(`/trades/intents/${intentId}/conditional-approve`, payload)) }
export async function rejectTradeIntent(intentId, payload = {}) { return unwrap(await api.post(`/trades/intents/${intentId}/reject`, payload)) }
export async function expireTradeIntent(intentId) { return unwrap(await api.post(`/trades/intents/${intentId}/expire`)) }
export async function getTradeTrustPacket(intentId) { return unwrap(await api.get(`/trades/intents/${intentId}/trust-packet`)) }
export async function getTradeDecisionReview(intentId) { return safeRequest(() => api.get(`/trades/intents/${intentId}/decision-review`), { decision_review: {} }, { key: `trade:decision-review:${intentId}`, retries: 0 }) }
export async function updateTradeDecisionReview(intentId, payload = {}) { return unwrap(await api.post(`/trades/intents/${intentId}/decision-review`, payload)) }
export async function getTradeEvidenceRegister(intentId) { return safeRequest(() => api.get(`/trades/intents/${intentId}/evidence-register`), { evidence_register: { items: [], missing_items: [] } }, { key: `trade:evidence:${intentId}`, retries: 0 }) }
export async function updateTradeEvidenceRegister(intentId, payload = {}) { return unwrap(await api.post(`/trades/intents/${intentId}/evidence-register`, payload)) }
export async function saveTradeScenario(intentId, payload = {}) { return unwrap(await api.post(`/trades/intents/${intentId}/scenarios`, payload)) }
export async function getTradeScenarios() { return safeRequest(() => api.get('/trades/scenarios'), { items: [], count: 0, comparison_groups: [] }, { key: 'trade:scenarios', retries: 1 }) }
export async function getTradeWorkflowOps() { return safeRequest(() => api.get('/trades/workflow-ops'), { trade_intent_count: 0, decision_not_ready_count: 0, evidence_gap_count: 0, saved_scenario_count: 0, contrast_ready_group_count: 0, pending_control_change_count: 0, high_risk_control_change_count: 0, recent_audit_events: [] }, { key: 'trade:workflow-ops', retries: 1 }) }
export async function getControlChanges() { return safeRequest(() => api.get('/trades/control-changes'), { items: [], count: 0, status_counts: {}, high_risk_count: 0 }, { key: 'trade:control-changes', retries: 1 }) }
export async function requestControlChange(payload = {}) { return unwrap(await api.post('/trades/control-changes', payload)) }
export async function getPendingOrders({ ticker = '' } = {}) { return safeRequest(() => api.get('/trades/orders', { params: { ticker } }), { items: [], count: 0, order_events: FALLBACK_ORDER_EVENTS }) }
export async function syncPendingOrders({ ticker = '' } = {}) { return unwrap(await api.post('/trades/orders/sync', null, { params: { ticker } })) }
export async function replacePendingOrder(orderId, payload) { return unwrap(await api.post(`/trades/orders/${orderId}/replace`, payload)) }
export async function cancelPendingOrder(orderId, payload = {}) { return unwrap(await api.post(`/trades/orders/${orderId}/cancel`, payload)) }
export async function fillPendingOrder(orderId, payload) { return unwrap(await api.post(`/trades/orders/${orderId}/fill`, payload)) }
export async function getPortfolio() { return safeRequest(() => api.get('/portfolio'), FALLBACK_PORTFOLIO) }
export async function getPortfolioEquity() { return safeRequest(() => api.get('/portfolio/equity'), { points: [], count: 0 }) }
export async function getTradeJournal({ search = '', limit = 100, offset = 0, resultFilter = 'all', directionFilter = 'all', attributionFilter = 'all' } = {}) { return safeRequest(() => api.get('/trades/journal', { params: { search, limit, offset, result_filter: resultFilter, direction_filter: directionFilter, attribution_filter: attributionFilter } }), { ...FALLBACK_JOURNAL, limit, offset, result_filter: resultFilter, direction_filter: directionFilter, attribution_filter: attributionFilter }) }
export async function getTradeSummary() {
  return safeRequest(() => api.get('/trades/summary'), {
    open_trades: 0,
    pending_orders: 0,
    tracked_premium: 0,
    urgent_actions: 0,
    call_positions: 0,
    put_positions: 0,
    trade_summary: {},
    attribution_summary: {
      total_reviewed: 0,
      execution_review_count: 0,
      thesis_review_count: 0,
      risk_review_count: 0,
      clean_win_count: 0,
      flat_review_count: 0,
      latest_review: null,
    },
    capital_preservation: {
      today_realized_pnl: 0,
      today_closed_trades: 0,
      consecutive_losses: 0,
      open_position_count: 0,
      pending_order_count: 0,
      active_ticket_count: 0,
    },
    validation_snapshot: {
      scorecards: [],
      route_quality: {
        clean_fill_count: 0,
        slipped_fill_count: 0,
        fragile_fill_count: 0,
        rejected_route_count: 0,
        partial_fill_count: 0,
        average_abs_slippage_bps: null,
        latest_execution_review: null,
      },
      board_snapshot_history: {
        count: 0,
        items: [],
      },
      replay_comparisons: {
        board_outcomes: {
          count: 0,
          resolved_count: 0,
          open_count: 0,
          items: [],
        },
        paper_live_slippage: {
          count: 0,
          average_signed_slippage_bps: null,
          average_abs_slippage_bps: null,
          worst_abs_slippage_bps: null,
          items: [],
        },
      },
    },
    client_trade_intents: {
      count: 0,
      pending_approval_count: 0,
      submitted_count: 0,
      rejected_count: 0,
      submission_failed_count: 0,
      automated_entry_count: 0,
      items: [],
    },
    client_automation: {
      eligible_linked_account_count: 0,
      automated_linked_account_count: 0,
      blocked_linked_account_count: 0,
      last_automated_client_order: null,
      block_reasons_by_account: {},
      items: [],
    },
    rollout_readiness: {
      status: 'locked',
      tone: 'warning',
      label: 'Paper first',
      detail: 'Paper stability evidence is still thin. Keep Alpaca live routing on paper until replay depth and fill drift improve.',
      basis: 'Need resolved board outcomes, saved fill replay, and a clean order lifecycle before scoped live routing.',
      allows_live_rollout: false,
      metrics: {
        resolved_count: 0,
        open_count: 0,
        win_count: 0,
        replay_win_rate: null,
        slippage_sample_count: 0,
        average_abs_slippage_bps: null,
        worst_abs_slippage_bps: null,
        stale_pending_count: 0,
        reject_count: 0,
        fragile_route_count: 0,
      },
      checks: [],
      history: {
        count: 0,
        trend: 'unknown',
        label: 'No live readiness history',
        tone: 'info',
        detail: 'Saved boards and fill replay will populate live readiness history once the desk records a few validation checkpoints.',
        items: [],
      },
      order_lifecycle: {
        summary: {
          status: 'healthy',
          message: 'Order lifecycle is healthy for the current live readiness snapshot.',
          pending_order_count: 0,
          stale_pending_count: 0,
          reject_count: 0,
          fill_count: 0,
          closed_count: 0,
          last_event_at: null,
          last_reject_at: null,
          last_fill_at: null,
        },
        checks: [],
        stale_pending_orders: [],
        recent_rejections: [],
        recent_fills: [],
        recent_closed: [],
      },
    },
    live_pilot_audit: {
      count: 0,
      allowed_count: 0,
      blocked_count: 0,
      label: 'No live attempt yet',
      tone: 'info',
      detail: 'Live attempts will be recorded here once the desk clears the paper gate and routes a scoped order.',
      latest: null,
      items: [],
    },
    working_orders: { items: [], count: 0, order_events: FALLBACK_ORDER_EVENTS },
    order_events: FALLBACK_ORDER_EVENTS,
  })
}
export async function exportTradeJournalCsv({ search = '', resultFilter = 'all', directionFilter = 'all', attributionFilter = 'all' } = {}) {
  const response = await api.get('/trades/journal/export', { params: { search, result_filter: resultFilter, direction_filter: directionFilter, attribution_filter: attributionFilter }, responseType: 'blob' })
  return response.data
}
export async function getFrontendFilters() { return safeRequest(() => api.get('/frontend/filters'), FALLBACK_FILTERS, { key: 'frontend:filters', retries: 1 }) }
export async function getReleaseInfo() { return safeRequest(() => api.get('/release'), { version: 'local', phase: 'preview', environment: 'development', api_prefix: '/api', highlights: [], status: 'preview' }, { key: 'release:info', retries: 1 }) }
export async function getReleaseNotes() { return safeRequest(() => api.get('/release/notes'), { version: 'local', phase: 'preview', environment: 'development', milestones: [], next_steps: [] }, { key: 'release:notes', retries: 1 }) }
export async function exportSupportDiagnostics() {
  const response = await api.get('/ops/diagnostics', { responseType: 'blob' })
  return {
    blob: response.data,
    filename: resolveDownloadFilename(response, 'pilot-diagnostics.json'),
  }
}
export async function getOpsStatus() {
  return safeRequest(() => api.get('/ops/status'), {
    counts: { alerts: 0, workspaces: 0, favorite_tickers: 0, recent_tickers: 0, active_notes: 0, overdue_notes: 0, high_priority_notes: 0, open_trades: 0 },
    portfolio: { realized_pnl: 0, win_rate: 0, profit_factor: 0 },
    observability: {
      requests: {
        window_size: 0,
        lifetime_requests: 0,
        lifetime_errors: 0,
        started_at: null,
        uptime_seconds: 0,
        summary: {
          total_requests: 0,
          error_count: 0,
          error_rate: 0,
          average_duration_ms: 0,
          p95_duration_ms: 0,
          max_duration_ms: 0,
          slow_request_count: 0,
          slow_request_threshold_ms: 0,
          timeout_warning_count: 0,
          timeout_warning_threshold_ms: 0,
          last_request_at: null,
        },
        route_groups: [],
        methods: [],
        status_buckets: [],
        recent_slow_requests: [],
        recent_timeout_risks: [],
      },
      operations: {
        window_size: 0,
        lifetime_operations: 0,
        lifetime_errors: 0,
        started_at: null,
        uptime_seconds: 0,
        summary: {
          total_operations: 0,
          error_count: 0,
          error_rate: 0,
          timeout_count: 0,
          average_duration_ms: 0,
          p95_duration_ms: 0,
          max_duration_ms: 0,
          slow_operation_count: 0,
          slow_operation_threshold_ms: 0,
          last_operation_at: null,
          cache_hit_count: 0,
          cache_miss_count: 0,
          cache_bypass_count: 0,
        },
        operations: [],
        recent_slow_operations: [],
      },
      route_profiles: {
        window_size: 0,
        lifetime_profiles: 0,
        lifetime_slow_profiles: 0,
        started_at: null,
        uptime_seconds: 0,
        summary: {
          total_profiles: 0,
          slow_profile_count: 0,
          slow_profile_threshold_ms: 0,
          timeout_profile_count: 0,
          average_total_duration_ms: 0,
          p95_total_duration_ms: 0,
          max_total_duration_ms: 0,
          last_profile_at: null,
        },
        routes: [],
        recent_profiles: [],
      },
      upstream: {
        window_size: 0,
        lifetime_calls: 0,
        lifetime_timeouts: 0,
        lifetime_errors: 0,
        started_at: null,
        uptime_seconds: 0,
        summary: {
          total_calls: 0,
          timeout_count: 0,
          error_count: 0,
          error_rate: 0,
          average_duration_ms: 0,
          p95_duration_ms: 0,
          max_duration_ms: 0,
          last_call_at: null,
        },
        targets: [],
        status_buckets: [],
        recent_calls: [],
        recent_timeouts: [],
      },
      jobs: {
        summary: {
          count: 0,
          queued: 0,
          retrying: 0,
          running: 0,
          succeeded: 0,
          dead_letter: 0,
          pending: 0,
          stuck_running_count: 0,
          oldest_pending_at: null,
          oldest_running_at: null,
          running_stale_after_minutes: 10,
          recent_failure_count: 0,
          last_finished_at: null,
        },
        job_types: [],
        recent_jobs: [],
        recent_failures: [],
        stuck_running: [],
        dead_letters: [],
        worker: {
          enabled: false,
          running: false,
          thread_name: null,
          stop_requested: false,
          poll_seconds: 0,
          batch_size: 0,
          last_loop_at: null,
          last_success_at: null,
          last_error_at: null,
          last_error_message: null,
        },
      },
    },
    deployment: {
      summary: {
        status: 'attention',
        readiness_percent: 0,
        ready_checks: 0,
        total_checks: 0,
        blockers: ['Deployment readiness snapshot is unavailable.'],
        warnings: [],
        next_action: 'Restore deployment readiness telemetry.',
      },
      deployment: { items: [], count: 0, ready_count: 0, next_action: 'No deployment artifacts recorded.' },
      backups: {
        status: 'attention',
        provider: 'unknown',
        schedule: null,
        last_success_at: null,
        last_attempt_at: null,
        restore_tested_at: null,
        retention_days: 0,
        location: null,
        notes: 'Backup readiness snapshot unavailable.',
        manifest_path: 'runtime-logs/backup-status.json',
        configured: false,
        needs_attention: true,
        restore_warning_days: 0,
        restore_age_days: null,
        warnings: [],
        validation: {
          valid: false,
          issue_count: 1,
          issues: ['Backup readiness snapshot unavailable.'],
        },
        checklist: [],
      },
      environment: {
        summary: {
          status: 'warning',
          ready_checks: 0,
          total_checks: 0,
          blockers: ['Production environment replay-evidence snapshot is unavailable.'],
          warnings: [],
          next_action: 'Restore environment replay-evidence telemetry.',
        },
        checks: [],
      },
      runbooks: { items: [], count: 0, ready_count: 0, next_action: 'No runbooks recorded.' },
    },
    market_data: {
      ticker: 'SPY',
      interval: '5m',
      status: 'unknown',
      warning: false,
      stale: false,
      feed_expected: false,
      session: 'unknown',
      session_label: 'Unknown',
      latest_bar_at: null,
      latest_bar_age_seconds: null,
      latest_bar_age_minutes: null,
      warning_threshold_seconds: 0,
      stale_threshold_seconds: 0,
      point_count: 0,
      source: 'probe',
      checked_at: null,
      checked_at_et: null,
      message: 'Market-data freshness probe is unavailable.',
    },
    release_gates: {
      summary: {
        status: 'warning',
        ready: false,
        checked_at: null,
        ready_gates: 0,
        warning_gates: 0,
        blocked_gates: 1,
        total_gates: 0,
        blockers: ['Release gate snapshot is unavailable.'],
        warnings: [],
        next_action: 'Restore release gate telemetry.',
      },
      gates: [],
      tenant: { slug: null, name: null, status: null, plan_key: null },
    },
    billing: {
      tenant: { slug: null, name: null, status: null, plan_key: null, provider: 'unknown' },
      summary: {
        status: 'unknown',
        message: 'Billing operations snapshot is unavailable.',
        needs_attention: true,
        pending_job_count: 0,
        failed_event_count: 0,
        drill_count: 0,
        replay_count: 0,
        last_drill_at: null,
        last_replay_at: null,
      },
      sync: {
        status: 'unknown',
        message: 'Billing sync state unavailable.',
        provider: 'unknown',
        last_event_key: null,
        last_event_at: null,
        last_processed_at: null,
        last_failed_at: null,
        recent_failure_count: 0,
        duplicate_count: 0,
        needs_reconciliation: false,
        available_actions: [],
      },
      recovery: {
        enabled: false,
        last_reconciled_at: null,
        last_recovery_action: null,
        last_recovery_status: null,
        last_recovery_error: null,
        latest_failed_event_id: null,
        latest_failed_event_at: null,
        pending_job_count: 0,
        failed_event_count: 0,
      },
      drills: { items: [], count: 0, replay_count: 0, last_drill_at: null, last_replay_at: null },
      recent_jobs: [],
      failed_events: [],
      events: { count: 0, status_counts: {} },
    },
    service_smoke: {
      tenant: { slug: null },
      summary: {
        status: 'warning',
        ready_checks: 0,
        warning_checks: 0,
        blocked_checks: 1,
        total_checks: 0,
        blockers: ['Service smoke snapshot is unavailable.'],
        warnings: [],
        next_action: 'Restore core service smoke telemetry.',
      },
      checks: [],
    },
    rate_limits: {
      summary: {
        enabled: false,
        throttle_event_count: 0,
        blocked_actor_count: 0,
        auth_lockout_count: 0,
        abuse_failure_count: 0,
        last_throttle_at: null,
        last_abuse_event_at: null,
      },
      recent_events: [],
      recent_abuse: [],
      blocked_actors: [],
    },
    orders: {
      summary: {
        status: 'unknown',
        message: 'Order lifecycle health snapshot is unavailable.',
        pending_order_count: 0,
        stale_pending_count: 0,
        reject_count: 0,
        fill_count: 0,
        closed_count: 0,
        last_event_at: null,
        last_reject_at: null,
        last_fill_at: null,
      },
      checks: [],
      stale_pending_orders: [],
      recent_rejections: [],
      recent_fills: [],
      recent_closed: [],
    },
    launch: {
      tenant: { slug: null, name: null, status: null, plan_key: null },
      summary: {
        status: 'unknown',
        enabled: false,
        stage: 'Unknown',
        launch_ready: false,
        release_channel: 'stable',
        blocker_count: 0,
        completed_checks: 0,
        total_checks: 0,
        last_ready_at: null,
        last_failed_at: null,
        next_action: 'Tenant launch readiness snapshot is unavailable.',
      },
      checks: {
        domain_required: false,
        domain_ready: false,
        sender_required: false,
        sender_ready: false,
        auth_required: false,
        auth_ready: false,
      },
      checklist: [],
      blockers: [],
      recent_operations: [],
    },
    phase_a: {
      tenant: { slug: null },
      summary: {
        status: 'warning',
        completed_checks: 0,
        warning_checks: 0,
        blocked_checks: 1,
        total_checks: 0,
        tracker_completed: 0,
        tracker_total: 0,
        next_action: 'Personal readiness snapshot is unavailable.',
      },
      tracker: {
        path: 'PERSONAL_USE.md',
        count: 0,
        completed_count: 0,
        in_progress_count: 0,
        queued_count: 0,
        completed_items: [],
        in_progress_items: [],
        queued_items: [],
        items: [],
      },
      docs: [],
      checklist: [],
      remaining_items: [],
      probe_endpoints: {
        liveness: '/api/healthz',
        readiness: '/api/readyz',
        diagnostics_export: '/api/ops/diagnostics',
      },
    },
    readiness: {
      summary: {
        status: 'warning',
        ready: false,
        checked_at: null,
        ready_checks: 0,
        warning_checks: 0,
        blocked_checks: 1,
        total_checks: 0,
        readiness_percent: 0,
        blockers: ['Production readiness snapshot is unavailable.'],
        warnings: [],
        next_action: 'Restore production readiness telemetry.',
      },
      checks: [],
      tenant: { slug: null, name: null, status: null, plan_key: null },
    },
    timestamp: null,
  }, { key: 'ops:status', retries: 1 })
}
export async function getTickerSuggestions(query = '', limit = 10) { return safeRequest(() => api.get('/market/tickers', { params: { query, limit } }), { query, count: 0, results: [] }) }
export async function getPortfolioPerformance() { return safeRequest(() => api.get('/portfolio/performance'), { monthly: [], streaks: { current: 0, best_win: 0, worst_loss: 0 }, expectancy: 0, average_win: 0, average_loss: 0, profit_factor: 0, trade_count: 0 }) }
export async function getFrontendAlerts({ limit = 12, minSeverity = 'all', search = '', source = 'all' } = {}) { return safeRequest(() => api.get('/frontend/alerts', { params: { limit, min_severity: minSeverity, search, source } }), FALLBACK_ALERTS) }
export async function getSavedWorkspaces({ search = '', page = 'all', pinnedOnly = false, tag = '', sortBy = 'updated_desc' } = {}) { return safeRequest(() => api.get('/frontend/workspaces', { params: { search, page, pinned_only: pinnedOnly, tag, sort_by: sortBy } }), FALLBACK_WORKSPACES) }
export async function saveWorkspace(payload) { return unwrap(await api.post('/frontend/workspaces', payload)) }
export async function updateWorkspace(workspaceId, payload) { return unwrap(await api.put(`/frontend/workspaces/${workspaceId}`, payload)) }
export async function deleteWorkspace(workspaceId) { return unwrap(await api.delete(`/frontend/workspaces/${workspaceId}`)) }
export async function duplicateWorkspace(workspaceId) { return unwrap(await api.post(`/frontend/workspaces/${workspaceId}/duplicate`)) }
export async function exportWorkspaces() { return safeRequest(() => api.get('/frontend/workspaces/export'), { items: [] }) }
export async function importWorkspaces(payload) { return unwrap(await api.post('/frontend/workspaces/import', payload)) }
export async function getFrontendActivity({ search = '', severity = 'all', type = 'all', limit = 12 } = {}) { return safeRequest(() => api.get('/frontend/activity', { params: { search, severity, type, limit } }), FALLBACK_ACTIVITY) }

export async function getTickerHub(limitRecent = 8) { return safeRequest(() => api.get('/frontend/ticker-hub', { params: { limit_recent: limitRecent } }), FALLBACK_TICKER_HUB) }
export async function recordRecentTicker(ticker) { return unwrap(await api.post('/frontend/ticker-hub/recent', { ticker })) }
export async function toggleFavoriteTicker(ticker) { return unwrap(await api.post('/frontend/ticker-hub/favorites/toggle', { ticker })) }
export async function clearRecentTickers() { return unwrap(await api.delete('/frontend/ticker-hub/recent')) }

export async function getNotes({ search = "", ticker = "", status = "active", tag = "", limit = 100, priority = 'all', pinnedOnly = false, sortBy = 'updated_desc', noteType = 'all', dueState = 'all', completed = 'all', owner = '', hasLink = 'all', checklistState = 'all', reminderState = 'all', recurrence = 'all', blockedState = 'all', progressState = 'all' } = {}) {
  return safeRequest(() => api.get('/frontend/notes', { params: { search, ticker, status, tag, limit, priority, pinned_only: pinnedOnly, sort_by: sortBy, note_type: noteType, due_state: dueState, completed, owner, has_link: hasLink, checklist_state: checklistState, reminder_state: reminderState, recurrence, blocked_state: blockedState, progress_state: progressState } }), FALLBACK_NOTES)
}
export async function getRecentNotes(limit = 8, includeArchived = false) { return safeRequest(() => api.get('/frontend/notes/recent', { params: { limit, include_archived: includeArchived } }), { items: [], count: 0 }) }
export async function getNotesSummary() { return safeRequest(() => api.get('/frontend/notes/summary'), FALLBACK_NOTE_SUMMARY) }
export async function getNotesBoard(status = 'active') { return safeRequest(() => api.get('/frontend/notes/board', { params: { status } }), { columns: {}, count: 0 }) }
export async function getNotesCalendar(days = 14, status = 'active') { return safeRequest(() => api.get('/frontend/notes/calendar', { params: { days, status } }), { items: [], count: 0 }) }
export async function getNotesAgenda(days = 7, status = 'active') { return safeRequest(() => api.get('/frontend/notes/agenda', { params: { days, status } }), { items: [], count: 0 }) }
export async function exportNotes() { return safeRequest(() => api.get('/frontend/notes/export'), { items: [] }) }
export async function importNotes(payload) { return unwrap(await api.post('/frontend/notes/import', payload)) }
export async function createNote(payload) { return unwrap(await api.post('/frontend/notes', payload)) }
export async function duplicateNote(noteId) { return unwrap(await api.post(`/frontend/notes/${noteId}/duplicate`)) }
export async function advanceNote(noteId) { return unwrap(await api.post(`/frontend/notes/${noteId}/advance`)) }
export async function updateNote(noteId, payload) { return unwrap(await api.put(`/frontend/notes/${noteId}`, payload)) }
export async function deleteNote(noteId) { return unwrap(await api.delete(`/frontend/notes/${noteId}`)) }

export async function bulkUpdateNotes(payload) { return unwrap(await api.post('/frontend/notes/bulk', payload)) }
export async function snoozeNote(noteId, minutes) { return unwrap(await api.post(`/frontend/notes/${noteId}/snooze`, { minutes })) }

export async function getStrategies(params = {}) { return safeRequest(() => api.get('/strategies', { params }), { items: [], count: 0 }, { key: 'strategies:list', retries: 1 }) }
export async function createStrategy(payload) { return unwrap(await api.post('/strategies', payload)) }
export async function getStrategy(strategyId) { return strictRequest(() => api.get(`/strategies/${encodeURIComponent(strategyId)}`), { retries: 1 }) }
export async function updateProductizedStrategy(strategyId, payload) { return unwrap(await api.patch(`/strategies/${encodeURIComponent(strategyId)}`, payload || {})) }
export async function createStrategyVersion(strategyId, payload = {}) { return unwrap(await api.post(`/strategies/${encodeURIComponent(strategyId)}/versions`, payload)) }
export async function getStrategyVersions(strategyId) { return strictRequest(() => api.get(`/strategies/${encodeURIComponent(strategyId)}/versions`), { retries: 1 }) }
export async function startStrategy(strategyId, payload = {}) { return unwrap(await api.post(`/strategies/${encodeURIComponent(strategyId)}/start`, payload)) }
export async function stopStrategy(strategyId, payload = {}) { return unwrap(await api.post(`/strategies/${encodeURIComponent(strategyId)}/stop`, payload)) }
export async function promoteStrategy(strategyId, payload = {}) { return unwrap(await api.post(`/strategies/${encodeURIComponent(strategyId)}/promote`, payload)) }
export async function rollbackStrategy(strategyId, payload = {}) { return unwrap(await api.post(`/strategies/${encodeURIComponent(strategyId)}/rollback`, payload)) }
export async function getStrategyReadiness(strategyId) { return strictRequest(() => api.get(`/readiness/strategies/${encodeURIComponent(strategyId)}`), { retries: 1 }) }
export async function evaluateStrategyReadiness(strategyId, payload = {}) { return unwrap(await api.post(`/readiness/strategies/${encodeURIComponent(strategyId)}/evaluate`, payload)) }
export async function getDeskReadiness() { return strictRequest(() => api.get('/readiness/desk'), { retries: 1 }) }

export const FALLBACK_CATEGORY_UPGRADE_READINESS = {
  status: 'unavailable',
  generated_at: null,
  summary: {
    gate_count: 9,
    passed_gate_count: 0,
    blocked_gate_count: 0,
    ready_category_count: 0,
    category_count: 6,
    documented_requirement_count: 0,
    documented_requirement_complete_count: 0,
    all_documented_scope_added: false,
    highest_priority_build: 'Post-Implementation Verification, Data Completeness cleanup, Professional Benchmark hardening, Walk-Forward validation, Score Calibration and Feature Attribution, Execution Quality and TCA, Risk Gate and Audit Trail hardening, Portfolio Risk cleanup, Human vs System validation, Research Promotion cleanup, then expansion review.',
    proof_first_rule: 'Ambition is allowed. Proof decides priority.',
    deferred_expansion_count: 0,
    top_blockers: ['Category upgrade readiness summary is unavailable.'],
    priority_backlog: [],
  },
  gates: [],
  categories: [],
  category_progress: [],
  documented_scope_coverage: { records: [], requirement_count: 0, complete_count: 0, all_documented_scope_added: false },
  backlog: [],
  claims_to_avoid: ['guaranteed_returns', 'proven_alpha', 'autonomous_money_manager', 'hft_platform'],
  finish_tracker: FALLBACK_FINISH_TRACKER,
  safety_notes: [
    'Read-only readiness evaluator. Does not affect trading.',
    'Does not place orders.',
    'Does not change broker routes.',
    'Does not bypass risk gates.',
    'Does not clear kill switches.',
    'Does not change ranking weights automatically.',
    'Does not grant AI order authority.',
  ],
  research_only: true,
  read_only: true,
  paper_route_only: true,
  can_submit_orders: false,
  can_submit_live_orders: false,
  can_change_broker_routes: false,
  can_bypass_risk_gates: false,
  can_clear_kill_switch: false,
  can_change_ranking_weights: false,
  mutation: 'none',
}

export async function getCategoryUpgradeReadiness(params = {}) {
  return safeRequest(() => api.get('/readiness/category-upgrade', { params }), FALLBACK_CATEGORY_UPGRADE_READINESS, { key: 'readiness:category-upgrade', retries: 1 })
}

export async function getCategoryUpgradeProofGates(params = {}) {
  return safeRequest(
    () => api.get('/readiness/category-upgrade/proof-gates', { params }),
    { ...FALLBACK_CATEGORY_UPGRADE_READINESS, records: [] },
    { key: 'readiness:category-upgrade-proof-gates', retries: 1 },
  )
}

export async function getCategoryUpgradeProofChain(params = {}) {
  return safeRequest(
    () => api.get('/readiness/category-upgrade/proof-chain', { params }),
    { ...FALLBACK_CATEGORY_UPGRADE_READINESS, summary: { stage_count: 9, passed_stage_count: 0, blocked_stage_count: 0 }, records: [] },
    { key: 'readiness:category-upgrade-proof-chain', retries: 1 },
  )
}

export async function getCategoryUpgradeBacklog(params = {}) {
  return safeRequest(
    () => api.get('/readiness/category-upgrade/backlog', { params }),
    { ...FALLBACK_CATEGORY_UPGRADE_READINESS, records: [] },
    { key: 'readiness:category-upgrade-backlog', retries: 1 },
  )
}

export async function getCategoryUpgradeSupportExport(params = {}) {
  return safeRequest(
    () => api.get('/readiness/category-upgrade/support-export', { params }),
    { export_type: 'category_upgrade_readiness_support_export', sanitized: true, report: FALLBACK_CATEGORY_UPGRADE_READINESS },
    { key: 'readiness:category-upgrade-support-export', retries: 1 },
  )
}

export async function writeCategoryUpgradeSupportExport(payload = {}) {
  return strictRequest(() => api.post('/readiness/category-upgrade/support-export', payload), { retries: 0 })
}

export async function getProofMetricsSummary(params = {}) {
  return safeRequest(() => api.get('/proof-metrics/summary', { params }), FALLBACK_PROOF_METRICS_DASHBOARD, { key: 'proof-metrics:summary', retries: 1 })
}

export async function getStrategyRuns(strategyId) { return strictRequest(() => api.get(`/strategies/${encodeURIComponent(strategyId)}/runs`), { retries: 1 }) }
export async function getStrategyMetrics(strategyId) { return strictRequest(() => api.get(`/strategies/${encodeURIComponent(strategyId)}/metrics`), { retries: 1 }) }

export async function getProductAutomationStatus() { return strictRequest(() => api.get('/automation/status'), { retries: 1 }) }
export async function requestStrategyLive(strategyId, payload = {}) { return unwrap(await api.post(`/automation/strategies/${encodeURIComponent(strategyId)}/live/request`, payload)) }
export async function killProductStrategy(strategyId, payload = {}) { return unwrap(await api.post(`/automation/strategies/${encodeURIComponent(strategyId)}/kill`, payload)) }
export async function getProductAutomationEvents(params = {}) { return safeRequest(() => api.get('/automation/events', { params }), { items: [], count: 0 }, { key: 'automation:events', retries: 1 }) }

export async function createLiveAuthorization(payload = {}) { return unwrap(await api.post('/live/authorizations', payload)) }
export async function getLiveAuthorizations(params = {}) { return safeRequest(() => api.get('/live/authorizations', { params }), { items: [], count: 0 }, { key: 'live:authorizations', retries: 1 }) }
export async function getLiveAuthorization(authorizationId) { return strictRequest(() => api.get(`/live/authorizations/${encodeURIComponent(authorizationId)}`), { retries: 1 }) }
export async function revokeLiveAuthorization(authorizationId, payload = {}) { return unwrap(await api.post(`/live/authorizations/${encodeURIComponent(authorizationId)}/revoke`, payload)) }
export async function requestLiveStart(strategyId, payload = {}) { return unwrap(await api.post(`/strategies/${encodeURIComponent(strategyId)}/live/request`, payload)) }
export async function armLiveStrategy(strategyId, payload = {}) { return unwrap(await api.post(`/strategies/${encodeURIComponent(strategyId)}/live/arm`, payload)) }
export async function startLiveStrategy(strategyId, payload = {}) { return unwrap(await api.post(`/strategies/${encodeURIComponent(strategyId)}/live/start`, payload)) }
export async function pauseLiveStrategy(strategyId, payload = {}) { return unwrap(await api.post(`/strategies/${encodeURIComponent(strategyId)}/live/pause`, payload)) }
export async function resumeLiveStrategy(strategyId, payload = {}) { return unwrap(await api.post(`/strategies/${encodeURIComponent(strategyId)}/live/resume`, payload)) }
export async function stopLiveStrategy(strategyId, payload = {}) { return unwrap(await api.post(`/strategies/${encodeURIComponent(strategyId)}/live/stop`, payload)) }
export async function killLiveStrategy(strategyId, payload = {}) { return unwrap(await api.post(`/strategies/${encodeURIComponent(strategyId)}/live/kill`, payload)) }
export async function getLiveStatus() { return safeRequest(() => api.get('/live/status'), { feature_flags: {}, summary: {}, sessions: [] }, { key: 'live:status', retries: 1 }) }
export async function getLiveRiskEvents(params = {}) { return safeRequest(() => api.get('/live/risk/events', { params }), { items: [], count: 0 }, { key: 'live:risk-events', retries: 1 }) }
export async function getLiveKillSwitch(params = {}) { return safeRequest(() => api.get('/live/kill-switch', { params }), { active: false, items: [], count: 0 }, { key: 'live:kill-switch', retries: 1 }) }
export async function activateLiveKillSwitch(payload = {}) { return unwrap(await api.post('/live/kill-all', payload)) }
export async function clearLiveKillSwitch(payload = {}) { return unwrap(await api.post('/live/kill-switch/clear', payload)) }
export async function getLiveOrders(params = {}) { return safeRequest(() => api.get('/live/orders', { params }), { items: [], count: 0 }, { key: 'live:orders', retries: 1 }) }
export async function getLiveOrder(orderIntentId) { return strictRequest(() => api.get(`/live/orders/${encodeURIComponent(orderIntentId)}`), { retries: 1 }) }
export async function approveLiveOrder(orderIntentId, payload = {}) { return unwrap(await api.post(`/live/orders/${encodeURIComponent(orderIntentId)}/approve`, payload)) }
export async function rejectLiveOrder(orderIntentId, payload = {}) { return unwrap(await api.post(`/live/orders/${encodeURIComponent(orderIntentId)}/reject`, payload)) }
export async function runLiveRiskCheck(orderIntentId, payload = {}) { return unwrap(await api.post(`/live/orders/${encodeURIComponent(orderIntentId)}/risk-check`, payload)) }

const FALLBACK_RISK_KILL_SWITCH = {
  active: false,
  scope: 'tenant',
  strategy_count: 0,
  active_strategy_count: 0,
  affected_count: 0,
  items: [],
  latest_event: null,
  can_submit_orders: false,
  can_submit_live_orders: false,
  can_bypass_risk_gates: false,
  can_change_broker_routes: false,
  mutation: 'risk_kill_switch_state_only',
}

export const FALLBACK_RISK_AUDIT_HARDENING = {
  status: 'blocked_by_evidence',
  generated_at: null,
  research_only: true,
  audit_only: true,
  paper_only: true,
  paper_route_only: true,
  summary: {
    status: 'blocked_by_evidence',
    risk_policy_count: 0,
    active_policy_count: 0,
    risk_event_count: 0,
    audit_event_count: 0,
    audit_export_count: 0,
    decision_replay_count: 0,
    safety_ledger_record_count: 0,
    event_type_counts: [],
    risk_audit_hardening_status: 'blocked_by_evidence',
    risk_audit_hardening_open_items: 7,
    risk_audit_hardening_critical_open_items: 3,
    top_hardening_item: 'Active risk policy evidence',
    claim_permissions: {
      cautious_internal_risk_audit_review: false,
      risk_gate_authority_claim: false,
      audit_completeness_claim: false,
      kill_switch_clearance: false,
      broker_route_change: false,
      automatic_execution_mutation: false,
      compliance_approval_claim: false,
      live_trading_readiness: false,
    },
    can_submit_orders: false,
    can_submit_live_orders: false,
    can_change_broker_routes: false,
    can_bypass_risk_gates: false,
    can_clear_kill_switch: false,
    can_change_ranking_weights: false,
    mutation: 'none',
  },
  risk_policies: [],
  risk_events: [],
  audit_events: [],
  audit_exports: [],
  trade_replays: [],
  safety_summary: {},
  risk_audit_hardening_plan: {
    status: 'blocked_by_evidence',
    summary: {
      item_count: 8,
      open_item_count: 7,
      critical_open_items: 3,
      ready_item_count: 1,
      top_hardening_item: 'Active risk policy evidence',
      proof_first_rule: 'Ambition is allowed. Proof decides priority.',
      claim_permissions: {
        cautious_internal_risk_audit_review: false,
        risk_gate_authority_claim: false,
        audit_completeness_claim: false,
        kill_switch_clearance: false,
        broker_route_change: false,
        automatic_execution_mutation: false,
        compliance_approval_claim: false,
        live_trading_readiness: false,
      },
      blocked_claims: ['risk_gate_authority_claim', 'audit_completeness_claim', 'kill_switch_recovery_claim', 'paper_to_live_readiness', 'broker_route_safety_claim', 'compliance_approval_claim', 'live_trading_readiness'],
      safe_boundary: 'Risk and audit hardening only records proof gaps and authority boundaries. It does not authorize orders, route changes, risk-gate changes, kill-switch clears, or ranking-weight mutation.',
    },
    metrics: {
      active_policy_count: 0,
      risk_event_lineage_coverage: 0,
      kill_switch_audit_event_count: 0,
      audit_event_lineage_coverage: 0,
      decision_replay_traceability_coverage: 0,
      sanitized_export_coverage: 0,
      safety_ledger_record_count: 0,
      read_only_boundary: 1,
    },
    items: [
      { key: 'active_risk_policy', title: 'Active risk policy evidence', priority: 'critical', status: 'no_records', missing_fields: ['active_policy', 'scope', 'risk_limits'], blocked_claims: ['risk_gate_authority_claim', 'promotion_review', 'paper_to_live_readiness'], safe_next_action: 'Keep at least one active tenant or strategy risk policy visible before treating risk gates as reviewable evidence.', manual_review_only: true, changes_execution: false, changes_order_submission: false, changes_broker_routes: false, changes_risk_gates: false, clears_kill_switch: false },
      { key: 'risk_event_lineage', title: 'Risk event lineage', priority: 'critical', status: 'no_records', missing_fields: ['event_type', 'severity', 'action_taken', 'created_at', 'payload'], blocked_claims: ['risk_breach_auditability', 'blocked_order_review', 'promotion_review'], safe_next_action: 'Record risk check failures and blocked actions with event type, severity, action, payload, and timestamp.', manual_review_only: true, changes_execution: false, changes_order_submission: false, changes_broker_routes: false, changes_risk_gates: false, clears_kill_switch: false },
      { key: 'kill_switch_auditability', title: 'Kill-switch auditability', priority: 'critical', status: 'no_records', missing_fields: ['kill_switch_audit_event', 'actor_email', 'reason', 'affected_count'], blocked_claims: ['kill_switch_recovery_claim', 'operator_control_claim', 'paper_to_live_readiness'], safe_next_action: 'Ensure each kill-switch activation and clear operation writes an audit event with actor, reason, affected scope, and timestamp.', manual_review_only: true, changes_execution: false, changes_order_submission: false, changes_broker_routes: false, changes_risk_gates: false, clears_kill_switch: false },
      { key: 'audit_event_lineage', title: 'Audit event lineage', priority: 'high', status: 'no_records', missing_fields: ['event_type', 'actor_email', 'created_at', 'payload'], blocked_claims: ['audit_completeness_claim', 'support_review', 'operator_accountability'], safe_next_action: 'Keep audit events actor-stamped, timestamped, typed, and payload-backed before treating the audit trail as complete.', manual_review_only: true, changes_execution: false, changes_order_submission: false, changes_broker_routes: false, changes_risk_gates: false, clears_kill_switch: false },
      { key: 'decision_replay_traceability', title: 'Decision replay traceability', priority: 'high', status: 'no_records', missing_fields: ['risk_snapshot', 'readiness_snapshot', 'market_snapshot', 'broker_snapshot', 'replay_events'], blocked_claims: ['decision_replay_claim', 'promotion_traceability', 'paper_to_live_readiness'], safe_next_action: 'Link trade decisions to risk, readiness, market, broker, and ordered replay snapshots before using replay as proof.', manual_review_only: true, changes_execution: false, changes_order_submission: false, changes_broker_routes: false, changes_risk_gates: false, clears_kill_switch: false },
      { key: 'sanitized_export_boundary', title: 'Sanitized export boundary', priority: 'high', status: 'no_records', missing_fields: ['audit_export_event', 'export_type', 'queued_status', 'no_raw_file_path', 'no_secret_payload'], blocked_claims: ['support_export_safety', 'external_review_packet', 'compliance_approval_claim'], safe_next_action: 'Queue audit exports through the control plane and keep exported metadata free of secrets, raw paths, broker account identifiers, and credentials.', manual_review_only: true, changes_execution: false, changes_order_submission: false, changes_broker_routes: false, changes_risk_gates: false, clears_kill_switch: false },
      { key: 'safety_ledger_visibility', title: 'Safety ledger visibility', priority: 'high', status: 'no_records', missing_fields: ['safety_ledger_record', 'status', 'blocker', 'next_action'], blocked_claims: ['operational_safety_claim', 'automation_recovery_claim', 'paper_to_live_readiness'], safe_next_action: 'Keep trading safety state and ledger summaries visible with status, blocker, and next action context.', manual_review_only: true, changes_execution: false, changes_order_submission: false, changes_broker_routes: false, changes_risk_gates: false, clears_kill_switch: false },
      { key: 'read_only_governance_boundary', title: 'Read-only governance boundary', priority: 'critical', status: 'ready', missing_fields: [], blocked_claims: ['risk_gate_bypass', 'broker_route_change', 'order_submission', 'ranking_mutation'], safe_next_action: 'Keep this hardening report read-only; do not let proof reports mutate execution, broker, risk, or ranking configuration.', manual_review_only: true, changes_execution: false, changes_order_submission: false, changes_broker_routes: false, changes_risk_gates: false, clears_kill_switch: false },
    ],
    safe_next_actions: [],
    safety_notes: [
      'Read-only risk and audit proof review.',
      'Does not place orders.',
      'Does not change broker routes.',
      'Does not bypass or loosen risk gates.',
      'Does not clear kill switches.',
      'Does not change ranking weights automatically.',
      'Does not grant live-trading readiness.',
    ],
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'none',
  },
  warnings: ['Risk and audit hardening still blocks risk authority, audit completeness, paper-to-live, compliance, and live-readiness claims.'],
  safety_notes: [
    'Read-only risk and audit proof review.',
    'Does not place orders.',
    'Does not change broker routes.',
    'Does not bypass or loosen risk gates.',
    'Does not clear kill switches.',
    'Does not change ranking weights automatically.',
    'Does not grant live-trading readiness.',
  ],
  can_submit_orders: false,
  can_submit_live_orders: false,
  can_change_broker_routes: false,
  can_bypass_risk_gates: false,
  can_clear_kill_switch: false,
  can_change_ranking_weights: false,
  mutation: 'none',
  finish_tracker: FALLBACK_FINISH_TRACKER,
}

export async function getRiskPolicies() { return safeRequest(() => api.get('/risk/policies'), { items: [], count: 0 }, { key: 'risk:policies', retries: 1 }) }
export async function createRiskPolicy(payload) { return unwrap(await api.post('/risk/policies', payload)) }
export async function updateRiskPolicy(policyId, payload = {}) { return unwrap(await api.patch(`/risk/policies/${encodeURIComponent(policyId)}`, payload)) }
export async function runRiskCheck(payload = {}) { return unwrap(await api.post('/risk/check', payload)) }
export async function getRiskEvents(params = {}) { return safeRequest(() => api.get('/risk/events', { params }), { items: [], count: 0 }, { key: 'risk:events', retries: 1 }) }
export async function getKillSwitchStatus(params = {}) { return safeRequest(() => api.get('/risk/kill-switch', { params }), FALLBACK_RISK_KILL_SWITCH, { key: 'risk:kill-switch', retries: 1 }) }
export async function getRiskAuditHardening(params = {}) { return safeRequest(() => api.get('/risk/audit-hardening', { params }), FALLBACK_RISK_AUDIT_HARDENING, { key: 'risk:audit-hardening', retries: 1 }) }
export async function activateKillSwitch(payload = {}) { return unwrap(await api.post('/risk/kill-switch', payload)) }
export async function clearKillSwitch(payload = {}) { return unwrap(await api.post('/risk/kill-switch/clear', payload)) }

export async function getAuditEvents(params = {}) { return safeRequest(() => api.get('/audit/events', { params }), { items: [], count: 0 }, { key: 'audit:events', retries: 1 }) }
export async function getTradeReplay(tradeId) { return strictRequest(() => api.get(`/audit/trades/${encodeURIComponent(tradeId)}/replay`), { retries: 1 }) }
export async function getStrategyAudit(strategyId) { return strictRequest(() => api.get(`/audit/strategies/${encodeURIComponent(strategyId)}`), { retries: 1 }) }
export async function exportAuditBundle(payload = {}) { return unwrap(await api.post('/audit/export', payload)) }

const FALLBACK_EXECUTION_QUALITY_TCA = {
  status: 'empty',
  generated_at: null,
  research_only: true,
  paper_only: true,
  summary: {
    status: 'empty',
    trade_count: 0,
    paper_only: true,
    average_slippage: null,
    median_slippage: null,
    average_fill_delay_seconds: null,
    average_alpha_decay: null,
    average_execution_adjusted_reward: null,
    average_spread_cost: null,
    average_cost_adjusted_edge: null,
    missed_fill_rate: null,
    partial_fill_rate: null,
    execution_quality_score: null,
    execution_proof_ready: false,
    execution_proof_status: 'needs_evidence',
    execution_requirements_passed: 0,
    execution_requirements_total: 7,
    cost_evidence_coverage: 0,
    candidate_route_linkage_coverage: 0,
    execution_quality_hardening_status: 'blocked_by_evidence',
    execution_quality_hardening_open_items: 7,
    execution_quality_hardening_critical_open_items: 3,
    top_hardening_item: 'Paper execution sample',
    claim_permissions: {
      cautious_internal_execution_review: false,
      public_execution_quality_claim: false,
      tradability_claim: false,
      route_change: false,
      broker_route_change: false,
      automatic_execution_mutation: false,
      live_trading_readiness: false,
    },
    research_only: true,
    paper_route_only: true,
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'none',
  },
  records: [],
  proof_summary: {
    status: 'needs_evidence',
    proof_ready: false,
    requirements: [],
    summary: {
      trade_count: 0,
      cost_evidence_row_count: 0,
      cost_evidence_coverage: 0,
      execution_adjusted_row_count: 0,
      execution_adjusted_coverage: 0,
      cost_adjusted_edge_row_count: 0,
      cost_adjusted_edge_coverage: 0,
      candidate_route_linked_row_count: 0,
      candidate_route_linkage_coverage: 0,
      quote_or_spread_row_count: 0,
      quote_or_spread_coverage: 0,
      missed_fill_rate: 1,
      average_execution_adjusted_reward: null,
      average_cost_adjusted_edge: null,
      requirement_count: 7,
      passed_requirement_count: 0,
      missing_requirement_count: 7,
    },
    record_readiness: [],
    safe_next_actions: [],
    research_only: true,
    paper_only: true,
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'none',
  },
  execution_quality_hardening_plan: {
    status: 'blocked_by_evidence',
    summary: {
      item_count: 7,
      open_item_count: 7,
      critical_open_items: 3,
      ready_item_count: 0,
      top_hardening_item: 'Paper execution sample',
      proof_first_rule: 'Ambition is allowed. Proof decides priority.',
      claim_permissions: {
        cautious_internal_execution_review: false,
        public_execution_quality_claim: false,
        tradability_claim: false,
        route_change: false,
        broker_route_change: false,
        automatic_execution_mutation: false,
        live_trading_readiness: false,
      },
      blocked_claims: ['proven_tradability', 'public_execution_quality', 'after_cost_edge', 'route_quality', 'paper_to_live_readiness', 'live_trading_readiness'],
      safe_boundary: 'Execution Quality hardening only records paper-route proof gaps and claim boundaries. It does not authorize orders, route changes, broker changes, risk-gate changes, or ranking-weight mutation.',
    },
    items: [
      { key: 'paper_execution_sample', title: 'Paper execution sample', priority: 'critical', status: 'needs_evidence', missing_fields: ['paper_order_id', 'paper_fill_status', 'route'], blocked_claims: ['execution_quality_review', 'tradability_review', 'benchmark_after_cost_review'], safe_next_action: 'Collect enough paper-route execution rows with order IDs, fill status, and explicit paper route evidence before treating TCA as proof.', manual_review_only: true, changes_execution: false, changes_order_submission: false, changes_broker_routes: false },
      { key: 'cost_evidence_capture', title: 'Cost evidence capture', priority: 'critical', status: 'needs_evidence', missing_fields: ['slippage', 'spread_at_signal', 'fill_delay_seconds', 'fill_price'], blocked_claims: ['after_cost_edge', 'execution_quality_review', 'tradability_claim'], safe_next_action: 'Attach slippage, spread, fill-delay, and fill-price evidence to paper rows before using execution-adjusted metrics.', manual_review_only: true, changes_execution: false, changes_order_submission: false, changes_broker_routes: false },
      { key: 'candidate_route_linkage', title: 'Candidate and route linkage', priority: 'critical', status: 'needs_evidence', missing_fields: ['linked_candidate_id', 'route', 'fill_price'], blocked_claims: ['candidate_specific_tca', 'promotion_traceability', 'paper_to_live_review'], safe_next_action: 'Link each paper fill to a candidate lifecycle ID, route, and fill evidence before attributing execution quality to candidates.', manual_review_only: true, changes_execution: false, changes_order_submission: false, changes_broker_routes: false },
      { key: 'execution_adjusted_reward', title: 'Execution-adjusted reward', priority: 'high', status: 'needs_evidence', missing_fields: ['execution_adjusted_reward', 'total_reward', 'slippage', 'spread_at_signal'], blocked_claims: ['after_cost_reward_claim', 'benchmark_execution_support'], safe_next_action: 'Verify reward remains positive after spread and slippage drag using rows with complete cost evidence.', manual_review_only: true, changes_execution: false, changes_order_submission: false, changes_broker_routes: false },
      { key: 'cost_adjusted_edge', title: 'Cost-adjusted edge', priority: 'high', status: 'needs_evidence', missing_fields: ['actual_forward_return', 'baseline_forward_return', 'slippage', 'spread_at_signal'], blocked_claims: ['baseline_relative_edge_after_costs', 'public_execution_quality_claim'], safe_next_action: 'Link same-window baselines and verify candidate edge survives paper execution costs.', manual_review_only: true, changes_execution: false, changes_order_submission: false, changes_broker_routes: false },
      { key: 'fill_quality', title: 'Fill quality', priority: 'high', status: 'needs_evidence', missing_fields: ['fill_status', 'missed_fill', 'partial_fill'], blocked_claims: ['tradability_claim', 'route_quality_claim'], safe_next_action: 'Review missed, rejected, canceled, expired, no-fill, and partial-fill evidence before tradability language.', manual_review_only: true, changes_execution: false, changes_order_submission: false, changes_broker_routes: false },
      { key: 'paper_only_governance', title: 'Paper-only governance', priority: 'high', status: 'ready', missing_fields: [], blocked_claims: ['route_change', 'broker_change', 'order_submission'], safe_next_action: 'Keep Execution Quality as read-only paper-route analytics; do not mutate routes, broker settings, risk gates, or order behavior.', manual_review_only: true, changes_execution: false, changes_order_submission: false, changes_broker_routes: false },
    ],
    safe_next_actions: [],
    research_only: true,
    paper_only: true,
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'none',
  },
  aggregations: {
    average_slippage: null,
    median_slippage: null,
    slippage_by_engine: [],
    slippage_by_setup_type: [],
    slippage_by_symbol: [],
    slippage_by_regime: [],
    fill_delay_by_engine: [],
    alpha_decay_by_engine: [],
    execution_adjusted_reward_by_setup: [],
    spread_cost_by_setup: [],
    missed_fill_rate: null,
    partial_fill_rate: null,
    execution_quality_score: null,
    execution_proof: { status: 'needs_evidence', proof_ready: false, requirements: [] },
    execution_quality_hardening_plan: {
      status: 'blocked_by_evidence',
      summary: {
        item_count: 7,
        open_item_count: 7,
        critical_open_items: 3,
        ready_item_count: 0,
        top_hardening_item: 'Paper execution sample',
        claim_permissions: {
          cautious_internal_execution_review: false,
          public_execution_quality_claim: false,
          tradability_claim: false,
          route_change: false,
          broker_route_change: false,
          automatic_execution_mutation: false,
          live_trading_readiness: false,
        },
        blocked_claims: ['proven_tradability', 'public_execution_quality', 'after_cost_edge', 'route_quality', 'paper_to_live_readiness', 'live_trading_readiness'],
      },
      items: [],
    },
  },
  warnings: [],
  missing_fields: {},
  finish_tracker: FALLBACK_FINISH_TRACKER,
  safety_notes: [
    'Research only. Does not affect trading.',
    'Paper-route evidence only.',
    'Does not place orders.',
    'Does not change order routing.',
    'Does not change broker routes.',
    'Does not bypass risk gates.',
    'Does not change ranking weights automatically.',
    'Does not grant AI order authority.',
  ],
  can_submit_orders: false,
  can_submit_live_orders: false,
  mutation: 'none',
}

export async function getExecutionQualityTcaSummary(params = {}) { return safeRequest(() => api.get('/execution-quality/summary', { params }), FALLBACK_EXECUTION_QUALITY_TCA, { key: 'execution-quality:summary', retries: 1 }) }
export async function getExecutionQualityTcaTrades(params = {}) { return safeRequest(() => api.get('/execution-quality/trades', { params }), { ...FALLBACK_EXECUTION_QUALITY_TCA, records: [] }, { key: 'execution-quality:trades', retries: 1 }) }
export async function getExecutionQualityTcaSlippage(params = {}) { return safeRequest(() => api.get('/execution-quality/slippage', { params }), { ...FALLBACK_EXECUTION_QUALITY_TCA, records: [] }, { key: 'execution-quality:slippage', retries: 1 }) }
export async function getExecutionQualityTcaAlphaDecay(params = {}) { return safeRequest(() => api.get('/execution-quality/alpha-decay', { params }), { ...FALLBACK_EXECUTION_QUALITY_TCA, records: [] }, { key: 'execution-quality:alpha-decay', retries: 1 }) }
export async function getExecutionQualityTcaEngines(params = {}) { return safeRequest(() => api.get('/execution-quality/engines', { params }), { ...FALLBACK_EXECUTION_QUALITY_TCA, records: [] }, { key: 'execution-quality:engines', retries: 1 }) }
export async function getExecutionQualityTcaSetups(params = {}) { return safeRequest(() => api.get('/execution-quality/setups', { params }), { ...FALLBACK_EXECUTION_QUALITY_TCA, records: [] }, { key: 'execution-quality:setups', retries: 1 }) }

export const FALLBACK_PORTFOLIO_RISK_INTELLIGENCE = {
  status: 'empty',
  generated_at: null,
  research_only: true,
  paper_only: true,
  summary: {
    status: 'empty',
    position_count: 0,
    gross_exposure: 0,
    net_exposure: 0,
    long_exposure: 0,
    short_or_proxy_exposure: 0,
    symbol_concentration: null,
    sector_concentration: null,
    correlation_heat: null,
    liquidity_exposure: 0,
    beta_to_SPY: null,
    beta_to_QQQ: null,
    drawdown_state: 'unknown',
    daily_risk_budget_usage: null,
    open_heat: null,
    portfolio_risk_proof_ready: false,
    portfolio_risk_proof_status: 'needs_evidence',
    portfolio_risk_requirements_passed: 0,
    portfolio_risk_requirements_total: 9,
    portfolio_risk_coverage: 0,
    exposure_context_coverage: 0,
    factor_context_coverage: 0,
    liquidity_context_coverage: 0,
    drawdown_budget_context_coverage: 0,
    candidate_strategy_context_coverage: 0,
    portfolio_risk_cleanup_status: 'blocked_by_evidence',
    portfolio_risk_cleanup_open_items: 0,
    portfolio_risk_cleanup_critical_open_items: 0,
    top_cleanup_item: null,
    claim_permissions: {
      cautious_internal_portfolio_risk_review: false,
      portfolio_readiness_claim: false,
      risk_limit_change: false,
      risk_gate_change: false,
      broker_route_change: false,
      automatic_risk_mutation: false,
      paper_to_live_readiness: false,
      live_trading_readiness: false,
    },
    research_only: true,
    paper_only: true,
    paper_route_only: true,
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'none',
  },
  records: [],
  proof_summary: {
    status: 'needs_evidence',
    proof_ready: false,
    requirements: [],
    summary: {
      record_count: 0,
      portfolio_risk_coverage: 0,
      exposure_context_coverage: 0,
      concentration_context_coverage: 0,
      factor_context_coverage: 0,
      liquidity_context_coverage: 0,
      drawdown_budget_context_coverage: 0,
      candidate_strategy_context_coverage: 0,
      stress_scenario_count: 0,
      requirement_count: 9,
      passed_requirement_count: 0,
      missing_requirement_count: 9,
    },
    record_readiness: [],
    safe_next_actions: [],
    safety_notes: [
      'Research only. Does not affect trading.',
      'Risk visibility only. Does not enforce, loosen, or change risk gates.',
      'Does not place or block orders.',
      'Does not change broker routes.',
      'Does not change ranking weights automatically.',
    ],
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'none',
    writes_risk_limits: false,
    writes_risk_config: false,
  },
  portfolio_risk_cleanup_plan: {
    status: 'blocked_by_evidence',
    summary: {
      item_count: 0,
      open_item_count: 0,
      critical_open_items: 0,
      ready_item_count: 0,
      top_cleanup_item: null,
      claim_permissions: {
        cautious_internal_portfolio_risk_review: false,
        portfolio_readiness_claim: false,
        risk_limit_change: false,
        risk_gate_change: false,
        broker_route_change: false,
        automatic_risk_mutation: false,
        paper_to_live_readiness: false,
        live_trading_readiness: false,
      },
      blocked_claims: [
        'portfolio_readiness_claim',
        'risk_limit_change',
        'risk_gate_change',
        'broker_route_change',
        'portfolio_safety_proof',
        'paper_to_live_readiness',
        'live_trading_readiness',
      ],
      safe_boundary: 'Portfolio Risk cleanup records missing risk visibility evidence and claim boundaries only.',
    },
    items: [],
    safe_next_actions: [],
    research_only: true,
    paper_only: true,
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'none',
    writes_risk_limits: false,
    writes_risk_config: false,
  },
  aggregations: {
    gross_exposure: 0,
    net_exposure: 0,
    long_exposure: 0,
    short_or_proxy_exposure: 0,
    sector_exposure: [],
    engine_exposure: [],
    setup_exposure: [],
    strategy_exposure: [],
    regime_exposure: [],
    concentration: { top_symbols: [], top_sectors: [] },
    correlation_heat: { buckets: [] },
    liquidity_exposure: { warnings: [] },
    drawdown_state: {},
    daily_risk_budget_usage: {},
    open_heat: {},
    forecast_confidence_exposure: { buckets: [] },
    portfolio_risk_proof: { status: 'needs_evidence', proof_ready: false, requirements: [] },
    portfolio_risk_cleanup_plan: { status: 'blocked_by_evidence', summary: { blocked_claims: [] }, items: [] },
  },
  stress_tests: [],
  warnings: [],
  missing_fields: {},
  finish_tracker: FALLBACK_FINISH_TRACKER,
  safety_notes: [
    'Research only. Does not affect trading.',
    'Paper-route evidence only.',
    'Risk visibility only. Does not enforce, loosen, or change risk gates.',
    'Does not place or block orders.',
    'Does not change broker routes.',
    'Does not change ranking weights automatically.',
    'Does not grant AI order authority.',
  ],
  can_submit_orders: false,
  can_submit_live_orders: false,
  mutation: 'none',
  writes_risk_limits: false,
  writes_risk_config: false,
}

export async function getPortfolioRiskSummary(params = {}) { return safeRequest(() => api.get('/portfolio-risk/summary', { params }), FALLBACK_PORTFOLIO_RISK_INTELLIGENCE, { key: 'portfolio-risk:summary', retries: 1 }) }
export async function getPortfolioRiskExposures(params = {}) { return safeRequest(() => api.get('/portfolio-risk/exposures', { params }), { ...FALLBACK_PORTFOLIO_RISK_INTELLIGENCE, records: [] }, { key: 'portfolio-risk:exposures', retries: 1 }) }
export async function getPortfolioRiskConcentration(params = {}) { return safeRequest(() => api.get('/portfolio-risk/concentration', { params }), { ...FALLBACK_PORTFOLIO_RISK_INTELLIGENCE, records: [] }, { key: 'portfolio-risk:concentration', retries: 1 }) }
export async function getPortfolioRiskCorrelation(params = {}) { return safeRequest(() => api.get('/portfolio-risk/correlation', { params }), { ...FALLBACK_PORTFOLIO_RISK_INTELLIGENCE, records: [] }, { key: 'portfolio-risk:correlation', retries: 1 }) }
export async function getPortfolioRiskStressTests(params = {}) { return safeRequest(() => api.get('/portfolio-risk/stress-tests', { params }), { ...FALLBACK_PORTFOLIO_RISK_INTELLIGENCE, records: [] }, { key: 'portfolio-risk:stress-tests', retries: 1 }) }
export async function getPortfolioRiskRegimes(params = {}) { return safeRequest(() => api.get('/portfolio-risk/regimes', { params }), { ...FALLBACK_PORTFOLIO_RISK_INTELLIGENCE, records: [] }, { key: 'portfolio-risk:regimes', retries: 1 }) }

export const FALLBACK_SHADOW_MODE = {
  status: 'empty',
  generated_at: null,
  research_only: true,
  summary: {
    status: 'empty',
    record_count: 0,
    human_rewardable_count: 0,
    system_rewardable_count: 0,
    comparison_count: 0,
    human_win_count: 0,
    system_win_count: 0,
    tie_count: 0,
    human_direction_accuracy: null,
    system_direction_accuracy: null,
    human_avg_reward: null,
    system_avg_reward: null,
    human_vs_system_edge: null,
    shadow_proof_ready: false,
    shadow_proof_status: 'needs_evidence',
    shadow_requirements_passed: 0,
    shadow_requirements_total: 10,
    same_opportunity_coverage: 0,
    human_contract_coverage: 0,
    system_contract_coverage: 0,
    outcome_coverage: 0,
    cost_risk_context_coverage: 0,
    pre_outcome_capture_coverage: 0,
    system_decision_quality_delta: null,
    shadow_validation_status: 'blocked_by_evidence',
    shadow_validation_open_items: 7,
    shadow_validation_critical_open_items: 5,
    top_validation_item: 'Same-opportunity sample',
    claim_permissions: {
      cautious_internal_shadow_review: false,
      system_beats_human_claim: false,
      human_override_quality_claim: false,
      public_alpha_claim: false,
      automatic_ranking_mutation: false,
      paper_to_live_readiness: false,
      live_trading_readiness: false,
    },
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'research_metadata_only',
  },
  records: [],
  comparisons: [],
  proof_summary: {
    status: 'needs_evidence',
    proof_ready: false,
    requirements: [],
    summary: {
      comparison_count: 0,
      same_opportunity_coverage: 0,
      human_contract_coverage: 0,
      system_contract_coverage: 0,
      outcome_coverage: 0,
      cost_risk_context_coverage: 0,
      decision_quality_metric_count: 0,
      system_decision_quality_delta: null,
      pre_outcome_capture_coverage: 0,
      shadow_mode_safety_boundary: 1,
      reward_comparable_count: 0,
      requirement_count: 10,
      passed_requirement_count: 0,
      missing_requirement_count: 10,
    },
    record_readiness: [],
    safe_next_actions: [],
    safety_notes: [
      'Research only. Does not affect trading.',
      'Does not place orders.',
      'Does not change broker routes.',
      'Does not bypass risk gates.',
      'Does not change ranking weights automatically.',
    ],
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'research_metadata_only',
  },
  shadow_validation_plan: {
    status: 'blocked_by_evidence',
    summary: {
      item_count: 8,
      open_item_count: 7,
      critical_open_items: 5,
      ready_item_count: 1,
      top_validation_item: 'Same-opportunity sample',
      proof_first_rule: 'Ambition is allowed. Proof decides priority.',
      claim_permissions: {
        cautious_internal_shadow_review: false,
        system_beats_human_claim: false,
        human_override_quality_claim: false,
        public_alpha_claim: false,
        automatic_ranking_mutation: false,
        paper_to_live_readiness: false,
        live_trading_readiness: false,
      },
      blocked_claims: ['system_beats_human_claim', 'human_override_quality_claim', 'public_alpha_claim', 'repeatability_claim', 'paper_to_live_readiness', 'live_trading_readiness'],
      safe_boundary: 'Human vs System validation records proof gaps and claim boundaries only. It does not authorize orders, broker-route changes, risk-gate changes, kill-switch changes, ranking-weight mutation, AI order authority, or live trading.',
    },
    items: [
      { key: 'same_opportunity_sample', title: 'Same-opportunity sample', priority: 'critical', status: 'no_records', missing_fields: ['linked_candidate_id', 'system_prediction_id', 'same_horizon'], blocked_claims: ['human_vs_system_comparison_claim', 'system_beats_human_claim'], safe_next_action: 'Capture enough human and system decisions on the same candidate opportunity before judging either side.', manual_review_only: true, research_only: true, changes_execution: false, changes_order_submission: false, changes_broker_routes: false, changes_risk_gates: false, changes_ranking_weights: false },
      { key: 'decision_linkage', title: 'Decision linkage', priority: 'critical', status: 'no_records', missing_fields: ['linked_candidate_id', 'system_prediction_id', 'human_horizon_minutes', 'system_horizon_minutes'], blocked_claims: ['fair_comparison_claim', 'repeatability_claim'], safe_next_action: 'Link every human thesis to the exact candidate, system prediction, and matching horizon.', manual_review_only: true, research_only: true, changes_execution: false, changes_order_submission: false, changes_broker_routes: false, changes_risk_gates: false, changes_ranking_weights: false },
      { key: 'human_thesis_contract', title: 'Human thesis contract', priority: 'critical', status: 'no_records', missing_fields: ['symbol', 'human_direction', 'human_confidence', 'human_target_pct', 'human_invalidation_level', 'human_horizon_minutes', 'human_reason', 'created_at'], blocked_claims: ['human_skill_claim', 'override_quality_claim'], safe_next_action: 'Require a complete, timestamped human thesis before the outcome window closes.', manual_review_only: true, research_only: true, changes_execution: false, changes_order_submission: false, changes_broker_routes: false, changes_risk_gates: false, changes_ranking_weights: false },
      { key: 'system_forecast_contract', title: 'System forecast contract', priority: 'critical', status: 'no_records', missing_fields: ['system_direction', 'system_confidence', 'system_target_pct', 'system_invalidation_level', 'system_horizon_minutes'], blocked_claims: ['system_quality_claim', 'system_beats_human_claim'], safe_next_action: 'Attach the system forecast contract used at decision time to the same comparison row.', manual_review_only: true, research_only: true, changes_execution: false, changes_order_submission: false, changes_broker_routes: false, changes_risk_gates: false, changes_ranking_weights: false },
      { key: 'outcome_contract', title: 'Outcome contract', priority: 'critical', status: 'no_records', missing_fields: ['actual_forward_return', 'baseline_forward_return', 'outcome_window_closed_at', 'target_hit', 'invalidation_hit'], blocked_claims: ['decision_quality_claim', 'benchmark_relative_claim'], safe_next_action: 'Attach closed-window actual returns, baseline returns, target hits, invalidation hits, and outcome close evidence.', manual_review_only: true, research_only: true, changes_execution: false, changes_order_submission: false, changes_broker_routes: false, changes_risk_gates: false, changes_ranking_weights: false },
      { key: 'cost_risk_context', title: 'Cost and risk context', priority: 'high', status: 'no_records', missing_fields: ['cost_model', 'spread', 'slippage', 'fill_assumption', 'risk_adjustment', 'risk_gate_state', 'kill_switch_state', 'portfolio_exposure'], blocked_claims: ['after_cost_quality_claim', 'paper_to_live_readiness'], safe_next_action: 'Attach spread, slippage, fill assumptions, risk adjustment, gate state, kill-switch state, and portfolio exposure.', manual_review_only: true, research_only: true, changes_execution: false, changes_order_submission: false, changes_broker_routes: false, changes_risk_gates: false, changes_ranking_weights: false },
      { key: 'decision_quality_metrics', title: 'Decision quality metrics', priority: 'high', status: 'no_records', missing_fields: ['direction_accuracy', 'target_hit_rate', 'false_positive_rate', 'false_negative_rate', 'override_quality'], blocked_claims: ['system_beats_human_claim', 'human_override_quality_claim'], safe_next_action: 'Measure direction accuracy, targets, false positives, false negatives, overrides, and missed winners for both sides.', manual_review_only: true, research_only: true, changes_execution: false, changes_order_submission: false, changes_broker_routes: false, changes_risk_gates: false, changes_ranking_weights: false },
      { key: 'shadow_safety_governance', title: 'Shadow safety governance', priority: 'critical', status: 'ready', missing_fields: [], blocked_claims: ['automatic_ranking_mutation', 'paper_to_live_readiness', 'live_trading_readiness'], safe_next_action: 'Keep Shadow Mode as research metadata only; do not place, route, approve, or configure trades.', manual_review_only: true, research_only: true, changes_execution: false, changes_order_submission: false, changes_broker_routes: false, changes_risk_gates: false, changes_ranking_weights: false },
    ],
    safe_next_actions: [],
    research_only: true,
    can_submit_orders: false,
    can_submit_live_orders: false,
    mutation: 'research_metadata_only',
  },
  aggregations: {
    human_direction_accuracy: null,
    system_direction_accuracy: null,
    human_target_hit_rate: null,
    system_target_hit_rate: null,
    human_invalidation_hit_rate: null,
    system_invalidation_hit_rate: null,
    human_avg_reward: null,
    system_avg_reward: null,
    human_vs_system_edge: null,
    human_false_positive_rate: null,
    system_false_positive_rate: null,
    human_false_negative_rate: null,
    system_false_negative_rate: null,
    override_quality: {},
    missed_winner_comparison: {},
    bias_diagnostics: { items: [], counts: {} },
    shadow_proof: { status: 'needs_evidence', proof_ready: false, requirements: [] },
    shadow_validation_plan: { status: 'blocked_by_evidence', summary: { open_item_count: 7 }, items: [] },
  },
  warnings: [],
  missing_fields: {},
  finish_tracker: FALLBACK_FINISH_TRACKER,
  safety_notes: [
    'Research only. Does not affect trading.',
    'Does not place orders.',
    'Does not change broker routes.',
    'Does not bypass risk gates.',
    'Does not change ranking weights automatically.',
  ],
  can_submit_orders: false,
  can_submit_live_orders: false,
  mutation: 'research_metadata_only',
}

export async function getShadowModeSummary(params = {}) { return safeRequest(() => api.get('/shadow-mode/summary', { params }), FALLBACK_SHADOW_MODE, { key: 'shadow-mode:summary', retries: 1 }) }
export async function getShadowModeRecords(params = {}) { return safeRequest(() => api.get('/shadow-mode/records', { params }), { ...FALLBACK_SHADOW_MODE, records: [] }, { key: 'shadow-mode:records', retries: 1 }) }
export async function getShadowModeComparisons(params = {}) { return safeRequest(() => api.get('/shadow-mode/comparisons', { params }), { ...FALLBACK_SHADOW_MODE, records: [] }, { key: 'shadow-mode:comparisons', retries: 1 }) }
export async function getShadowModeBias(params = {}) { return safeRequest(() => api.get('/shadow-mode/bias', { params }), { ...FALLBACK_SHADOW_MODE, records: [] }, { key: 'shadow-mode:bias', retries: 1 }) }
export async function createHumanShadowThesis(payload = {}) { return unwrap(await api.post('/shadow-mode/human-thesis', payload || {})) }

export const FALLBACK_AI_AGENTS = {
  status: 'empty',
  generated_at: null,
  research_only: true,
  authority_level: 'research_only',
  summary: {
    memo_count: 0,
    committee_report_count: 0,
    role_count: 14,
    desk_agent_count: 5,
    permission_model: 'read-only decision support with append-only sanitized research memo storage',
  },
  record: null,
  records: [],
  warnings: [],
  missing_fields: [],
  finish_tracker: FALLBACK_FINISH_TRACKER,
  memos_created: [],
  agents_run: [],
  agents_skipped: [],
  llm_available: false,
  fallback_used: true,
  safety_checks_passed: true,
  execution_mutation: false,
  broker_route_mutation: false,
  risk_gate_mutation: false,
  ranking_mutation: false,
  safety_notes: [
    'Research only. Does not affect trading.',
    'Does not place orders.',
    'Does not change broker routes.',
    'Does not bypass risk gates.',
    'Does not clear kill switches.',
    'Does not change ranking weights automatically.',
    'Does not grant AI order authority.',
  ],
}

export async function getAiAgentsSummary(params = {}) {
  return safeRequest(() => api.get('/ai-agents/summary', { params }), FALLBACK_AI_AGENTS, { key: 'ai-agents:summary', retries: 1 })
}

export async function getAiAgentRoles(params = {}) {
  return safeRequest(() => api.get('/ai-agents/roles', { params }), { ...FALLBACK_AI_AGENTS, records: [] }, { key: 'ai-agents:roles', retries: 1 })
}

export async function getAiAgentMemos(params = {}) {
  return safeRequest(() => api.get('/ai-agents/memos', { params }), { ...FALLBACK_AI_AGENTS, records: [] }, { key: 'ai-agents:memos', retries: 1 })
}

export async function getAiAgentMemo(memoId) {
  return safeRequest(() => api.get(`/ai-agents/memos/${encodeURIComponent(memoId)}`), { ...FALLBACK_AI_AGENTS, record: null }, { key: 'ai-agents:memo', retries: 1 })
}

export async function getAiAgentsCommitteeLatest(params = {}) {
  return safeRequest(() => api.get('/ai-agents/committee/latest', { params }), { ...FALLBACK_AI_AGENTS, record: null }, { key: 'ai-agents:committee', retries: 1 })
}

export async function getAiAgentsSafety(params = {}) {
  return safeRequest(() => api.get('/ai-agents/safety', { params }), FALLBACK_AI_AGENTS, { key: 'ai-agents:safety', retries: 1 })
}

export async function getAiAgentsLlmStatus(params = {}) {
  return safeRequest(() => api.get('/ai-agents/llm-status', { params }), FALLBACK_AI_AGENTS, { key: 'ai-agents:llm-status', retries: 1 })
}

export async function getAiAgentsReadinessBacklog(params = {}) {
  return safeRequest(() => api.get('/ai-agents/readiness-backlog', { params }), { ...FALLBACK_AI_AGENTS, records: [] }, { key: 'ai-agents:readiness-backlog', retries: 1 })
}

export async function getAiAgentsExternalReview(params = {}) {
  return safeRequest(() => api.get('/ai-agents/external-review', { params }), { ...FALLBACK_AI_AGENTS, records: [] }, { key: 'ai-agents:external-review', retries: 1 })
}

export async function getAiAgentProposals(params = {}) {
  return safeRequest(() => api.get('/ai-agents/proposals', { params }), { ...FALLBACK_AI_AGENTS, records: [] }, { key: 'ai-agents:proposals', retries: 1 })
}

export async function createAiAgentProposal(payload = {}) {
  return strictRequest(() => api.post('/ai-agents/proposals', payload || {}), { retries: 0 })
}

export async function decideAiAgentProposal(proposalId, payload = {}) {
  return strictRequest(() => api.post(`/ai-agents/proposals/${encodeURIComponent(proposalId)}/decision`, payload || {}), { retries: 0 })
}

export async function runAiAgentRole(roleName) {
  return strictRequest(() => api.post(`/ai-agents/run-role/${encodeURIComponent(roleName)}`, {}), { retries: 0 })
}

export async function runAiAgentsCommittee() {
  return strictRequest(() => api.post('/ai-agents/run-committee', {}), { retries: 0 })
}

export async function runAiDeskAgent(deskName) {
  return strictRequest(() => api.post(`/ai-agents/run-desk/${encodeURIComponent(deskName)}`, {}), { retries: 0 })
}

export async function getExecutionQualitySummary(params = {}) { return strictRequest(() => api.get('/execution-analytics/summary', { params }), { retries: 1 }) }
export async function getExecutionSlippage(params = {}) { return safeRequest(() => api.get('/execution-analytics/slippage', { params }), { summary: {}, rows: [] }, { key: 'execution:slippage', retries: 1 }) }
export async function getStrategyExecutionQuality(strategyId, params = {}) { return strictRequest(() => api.get(`/execution-analytics/strategies/${encodeURIComponent(strategyId)}`, { params }), { retries: 1 }) }

export async function getExecutionDiagnostics(params = {}) { return strictRequest(() => api.get('/execution/diagnostics', { params }), { retries: 1 }) }
