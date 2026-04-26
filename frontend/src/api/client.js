import axios from "axios"

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 30000,
  withCredentials: true,
})

const API_WARNING_WINDOW_MS = 15000
const apiWarningTimestamps = new Map()

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
  console.warn('API fallback used:', error?.message || error)
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
  plan: { key: 'pro', name: 'Pro', monthly_price: 299, annual_price: 2988, seats_label: 'Up to 5 members', tagline: 'Realtime charting and market stream fallback.' },
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
export async function getBootstrap(consumer = 'full') {
  return safeRequest(() => api.get('/frontend/bootstrap', { params: { consumer } }), FALLBACK_BOOTSTRAP, { key: `bootstrap:${consumer}`, retries: 1 })
}
export async function getAuthConfig() {
  return strictRequest(() => api.get('/auth/config'), { retries: 1 })
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
  return strictRequest(() => api.get('/auth/session'), { retries: 1 })
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
export async function analyzeTicker(payload) { return unwrap(await api.post('/market/analyze', payload)) }
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
      detail: 'Paper stability evidence is still thin. Keep broker-live routing on paper until replay depth and fill drift improve.',
      basis: 'Need resolved board outcomes, saved fill replay, and a clean order lifecycle before broker-live pilot routing.',
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
        label: 'No broker-live history',
        tone: 'info',
        detail: 'Saved boards and fill replay will populate broker-live history once the desk records a few validation checkpoints.',
        items: [],
      },
      order_lifecycle: {
        summary: {
          status: 'healthy',
          message: 'Order lifecycle is healthy for the current broker-live snapshot.',
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
      label: 'No broker-live pilot yet',
      tone: 'info',
      detail: 'Broker-live attempts will be recorded here once the desk clears the paper gate and routes a pilot order.',
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
