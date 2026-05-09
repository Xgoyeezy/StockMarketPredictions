import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import Button from '../components/Button'
import Chip from '../components/Chip'
import CustomMarketChart from '../components/CustomMarketChart'
import EducationCallout from '../components/EducationCallout'
import EmptyState from '../components/EmptyState'
import ErrorState from '../components/ErrorState'
import FeedbackState from '../components/FeedbackState'
import { SelectField, TextField, ToggleField } from '../components/FormFields'
import InlineMeta, { formatInlineMeta } from '../components/InlineMeta'
import Kicker from '../components/Kicker'
import LoadingBlock from '../components/LoadingBlock'
import SegmentedControl from '../components/SegmentedControl'
import SignalDot from '../components/SignalDot'
import StrategyDeskStatusPanel from '../components/StrategyDeskStatusPanel'
import StatusBadge from '../components/StatusBadge'
import TickerInput from '../components/TickerInput'
import ToolGlyph from '../components/ToolGlyph'
import WorkflowGuide, { buildWorkflowSteps } from '../components/WorkflowGuide'
import {
  analyzeTicker,
  cancelPendingOrder,
  createFallbackDashboard,
  createNote,
  fillPendingOrder,
  getChart,
  getDashboard,
  getInternalBrokerRouter,
  getLinkedBrokerageAccounts,
  getLiveBatch,
  getNotes,
  getOrganizationTradeAutomation,
  getPortfolio,
  getSavedWorkspaces,
  openTrade,
  previewTrade,
  replacePendingOrder,
  recordRecentTicker,
  saveWorkspace,
  updateWorkspace,
} from '../api/client'
import { useToast } from '../context/ToastContext'
import { usePreferences } from '../context/PreferencesContext'
import useMarketStream from '../hooks/useMarketStream'
import useKeyboardListNavigation from '../hooks/useKeyboardListNavigation'
import usePolling from '../hooks/usePolling'
import {
  buildCapitalPreservationPolicy,
  buildPromotionGateSummary as buildSharedPromotionGateSummary,
  buildCapitalPreservationSummary,
} from '../utils/capitalPreservation'
import {
  buildEventWindowModel,
  buildIntervalModel,
  buildTradingSessionModel,
  formatMinuteWindow,
  getStyleIntervalOptions,
  getStyleQuickIntervals,
} from '../utils/intradayModel'
import { buildIntradayExecutionPlan } from '../utils/intradayExecutionModel'
import { buildIntradayPresetGuide, getIntradayPresetProfile } from '../utils/intradayPresetModel'
import { buildSessionAwareFreshness, buildSessionAwareFreshnessAlert } from '../utils/marketFreshnessModel'
import {
  normalizeAccountProfile,
  resolveAccountProfileTradingContext,
  resolveAccountProfileExecutionIntent,
} from '../utils/accountProfileModel'
import { isTickerValid } from '../utils/validators'

const defaultForm = { ticker: 'SPY', interval: '5m', horizon: 5 }
const INITIAL_CHART_HYDRATION_WAIT_MS = 1200
const TRADE_TICKET_MAX_HORIZON = 50
const TRADE_PREVIEW_MIN_REFRESH_MS = 1500
const defaultTradeTicket = {
  accountSize: 100000,
  riskPercent: 0.5,
  instrumentType: 'equity',
  optionStrategy: 'long_option',
  orderType: 'limit',
  timeInForce: 'day',
  limitPrice: '',
  stopPrice: '',
  trailingPercent: '',
}
const intervalPresets = ['1m', '5m', '15m', '30m', '1h', '4h', '1d']
const orderTypeOptions = [
  { key: 'market', label: 'Market' },
  { key: 'limit', label: 'Limit' },
  { key: 'stop_market', label: 'Stop market' },
  { key: 'stop_limit', label: 'Stop limit' },
  { key: 'trailing_stop', label: 'Trailing stop' },
]
const instrumentTypeOptions = [
  { key: 'listed_option', label: 'Listed option' },
  { key: 'equity', label: 'Equity' },
]
const optionStrategyOptions = [
  { key: 'long_option', label: 'Long option' },
  { key: 'short_premium', label: 'Short premium' },
  { key: 'vertical_spread', label: 'Vertical spread' },
]
const timeInForceOptions = [
  { key: 'day', label: 'Day' },
  { key: 'day_ext', label: 'Day + AH' },
  { key: 'gtc_90d', label: 'GTC 90D' },
]

function normalizeTradeTicketHorizon(value) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return defaultForm.horizon
  return Math.max(1, Math.min(TRADE_TICKET_MAX_HORIZON, Math.round(parsed)))
}

function initialChartPointsForInterval(interval) {
  const normalized = String(interval || '').trim().toLowerCase()
  const pointMap = {
    '1m': 1800,
    '5m': 600,
    '15m': 320,
    '30m': 240,
    '1h': 180,
    '4h': 180,
    '1d': 365,
  }
  return pointMap[normalized] || 600
}

const currencyFormatter = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 2,
})

const compactFormatter = new Intl.NumberFormat('en-US', {
  notation: 'compact',
  maximumFractionDigits: 1,
})
const marketClockFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  weekday: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
  hourCycle: 'h23',
})
const knownEtfTickers = new Set([
  'SPY', 'QQQ', 'IWM', 'DIA', 'TLT', 'GLD', 'SLV', 'XLE', 'XLF', 'XLK', 'XLI', 'XLY', 'XLP',
  'XLV', 'XLU', 'XLB', 'XLRE', 'SMH', 'ARKK', 'VTI', 'VOO', 'VEA', 'EEM', 'UVXY', 'SOXL',
  'TQQQ', 'SQQQ', 'UPRO', 'SPXL', 'SPXS', 'LABU', 'LABD',
])

const tickerAccentPalette = [
  '#16a34a',
  '#f4b942',
  '#ff8a65',
  '#d16dff',
  '#ff6b6b',
  '#c8d85a',
  '#f06595',
  '#ffd166',
]

const overlayAccentPalette = [
  '#22c55e',
  '#f4b942',
  '#ff8a65',
  '#b388ff',
  '#ff6b6b',
  '#c9d94d',
  '#ff8fab',
  '#ffd43b',
]

const namedOverlayPalette = {
  ema_9: '#22c55e',
  ema_21: '#f4b942',
  ema_50: '#ff8a65',
  ema_200: '#b388ff',
  sma_20: '#c9d94d',
  sma_50: '#f06595',
  sma_200: '#ff6b6b',
  vwap: '#ffd43b',
  rsi_14: '#ff8fab',
  macd: '#d16dff',
  macd_signal: '#ffd166',
  macd_hist: '#16a34a',
  idm_upper_band: '#16a34a',
  idm_lower_band: '#ff6b6b',
  idm_vwap: '#ffd43b',
  idm_trailing_stop: '#b388ff',
}

const namedOverlayLabels = {
  ema_9: 'EMA 9',
  ema_21: 'EMA 21',
  ema_50: 'EMA 50',
  ema_200: 'EMA 200',
  sma_20: 'SMA 20',
  sma_50: 'SMA 50',
  sma_200: 'SMA 200',
  rsi_14: 'RSI 14',
  macd: 'MACD',
  macd_signal: 'MACD signal',
  macd_hist: 'MACD histogram',
  idm_upper_band: 'Breakout upper',
  idm_lower_band: 'Breakout lower',
  idm_vwap: 'Session VWAP',
  idm_trailing_stop: 'Trail stop',
}

const CHART_LAYOUT_STORAGE_KEY = 'tradeview-chart-layouts-v2'
const EXECUTION_CHECKLIST_STORAGE_KEY = 'tradeview-execution-checklist-v2'
const DESK_SNAPSHOT_STORAGE_KEY = 'tradeview-desk-snapshot-v1'
const WATCHLIST_HISTORY_LIMIT = 24
const DESK_BOOTSTRAP_TIMEOUT_MS = 12000

function toNumber(value) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

function toMeaningfulNumber(value, { allowZero = false } = {}) {
  const numeric = toNumber(value)
  if (numeric === null) return null
  if (!allowZero && Math.abs(numeric) < 0.000001) return null
  return numeric
}

function formatPrice(value) {
  const numeric = toNumber(value)
  return numeric === null ? '--' : currencyFormatter.format(numeric)
}

function formatMeaningfulPrice(value, options = {}) {
  const numeric = toMeaningfulNumber(value, options)
  return numeric === null ? '--' : formatPrice(numeric)
}

function formatMeaningfulPriceRange(low, high, options = {}) {
  const lowNumber = toMeaningfulNumber(low, options)
  const highNumber = toMeaningfulNumber(high, options)
  return lowNumber !== null && highNumber !== null
    ? `${formatPrice(lowNumber)} - ${formatPrice(highNumber)}`
    : '--'
}

function formatCompactMeaningfulPriceRange(low, high, options = {}) {
  const lowNumber = toMeaningfulNumber(low, options)
  const highNumber = toMeaningfulNumber(high, options)
  return lowNumber !== null && highNumber !== null
    ? `${formatPrice(lowNumber)}-${formatPrice(highNumber)}`
    : '--'
}

function resolveDisplaySpread(spread, bidPrice = null, askPrice = null) {
  const bid = toMeaningfulNumber(bidPrice, { allowZero: true })
  const ask = toMeaningfulNumber(askPrice, { allowZero: true })
  if (bid !== null && ask !== null) {
    return Math.max(Math.abs(ask - bid), 0)
  }
  return toMeaningfulNumber(spread)
}

function formatSignedCurrency(value) {
  const numeric = toNumber(value)
  if (numeric === null) return '--'
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
    signDisplay: 'always',
  }).format(numeric)
}

function formatOptionalPrice(value) {
  if (value === null || value === undefined || value === '') return '--'
  return formatPrice(value)
}

function formatNumber(value, digits = 1) {
  const numeric = toNumber(value)
  return numeric === null ? '--' : numeric.toFixed(digits)
}

function formatCompact(value) {
  const numeric = toNumber(value)
  return numeric === null ? '--' : compactFormatter.format(numeric)
}

function formatPercent(value, digits = 1) {
  const numeric = toNumber(value)
  return numeric === null ? '--' : `${numeric.toFixed(digits)}%`
}

function formatRatioPercent(value, digits = 1) {
  const numeric = toNumber(value)
  return numeric === null ? '--' : `${(numeric * 100).toFixed(digits)}%`
}

function formatOptionalPercent(value, digits = 1) {
  if (value === null || value === undefined || value === '') return '--'
  return formatPercent(Number(value) * (Number(value) <= 1 ? 100 : 1), digits)
}

function isQuietStreamError(value) {
  const normalized = String(value || '').trim().toLowerCase()
  if (!normalized) return false
  return (
    normalized.includes('auth failed') ||
    normalized.includes('not authorized') ||
    normalized.includes('forbidden') ||
    normalized.includes('snapshot polling is active') ||
    normalized.includes('reconnects in the background')
  )
}

function formatSignedNumber(value, digits = 2) {
  const numeric = toNumber(value)
  if (numeric === null) return '--'
  const sign = numeric > 0 ? '+' : ''
  return `${sign}${numeric.toFixed(digits)}`
}

function formatSignedPercent(value, digits = 2) {
  const numeric = toNumber(value)
  if (numeric === null) return '--'
  const sign = numeric > 0 ? '+' : ''
  return `${sign}${numeric.toFixed(digits)}%`
}

function describeCalibrationShift(shiftPercent, resolvedCount = 0) {
  const numeric = toNumber(shiftPercent)
  if (numeric === null || Math.abs(numeric) < 0.15 || resolvedCount < 8) {
    return 'Stable'
  }
  if (numeric > 0) return `Boosting ${formatSignedPercent(numeric, 1)}`
  return `Dampening ${formatSignedPercent(numeric, 1)}`
}

function formatLabel(value, fallback = '--') {
  const normalized = String(value || '').trim()
  if (!normalized) return fallback
  return normalized.replaceAll('_', ' ').replace(/\b\w/g, (character) => character.toUpperCase())
}

function buildLiveBrokerDeskStatus(snapshot) {
  const paperRoute = snapshot?.broker_routes?.broker_paper || null
  const liveRoute = snapshot?.broker_routes?.broker_live || null

  if (!paperRoute && !liveRoute) {
    return {
      tone: 'info',
      label: 'Alpaca live',
      value: 'Route sync pending',
      detail: 'Automation route inventory is still loading for this desk session.',
    }
  }

  if (liveRoute?.active) {
    return {
      tone: 'warning',
      label: 'Alpaca live',
      value: liveRoute?.value || 'Live active',
      detail: 'Alpaca live routing is currently active. Tighten oversight before leaving the desk unattended.',
    }
  }

  if (liveRoute?.connected || liveRoute?.enabled) {
    return {
      tone: 'info',
      label: 'Alpaca live',
      value: liveRoute?.value || 'Standby',
      detail: paperRoute?.active
        ? 'Alpaca paper is active. Alpaca live is configured and visible, but it is not routing yet.'
        : String(liveRoute?.detail || '').trim() || 'Alpaca live is configured and waiting behind the rollout gate.',
    }
  }

  return {
    tone: liveRoute?.status === 'unavailable' ? 'negative' : 'warning',
    label: 'Alpaca live',
    value: liveRoute?.value || 'Not configured',
    detail:
      String(liveRoute?.detail || '').trim() ||
      'Alpaca live credentials are not loaded yet, so only desk and Alpaca paper routes are available.',
  }
}

function summarizeInlineCopy(value, maxLength = 120) {
  const normalized = String(value || '')
    .replace(/\s+/g, ' ')
    .trim()
  if (!normalized) return ''
  if (normalized.length <= maxLength) return normalized
  return `${normalized.slice(0, Math.max(0, maxLength - 1)).trimEnd()}...`
}

function omitKeys(record, fields) {
  const next = { ...record }
  fields.forEach((field) => {
    delete next[field]
  })
  return next
}

function buildDeskFormErrors(form) {
  const errors = {}
  if (!isTickerValid(form?.ticker)) {
    errors.ticker = 'Enter a valid ticker with up to 8 letters, dots, or dashes.'
  }
  const horizon = Number(form?.horizon)
  if (!Number.isInteger(horizon) || horizon < 1 || horizon > 50) {
    errors.horizon = 'Bars must be a whole number between 1 and 50.'
  }
  return errors
}

function resolveReviewLoopNoteTone(note) {
  if (String(note?.blocked_state || '').trim().toLowerCase() === 'blocked') return 'negative'
  if (String(note?.priority || '').trim().toLowerCase() === 'high') return 'warning'
  if (String(note?.progress_state || '').trim().toLowerCase() === 'in_progress') return 'positive'
  return 'neutral'
}

function resolveReviewLoopNoteStatus(note) {
  const blockedState = String(note?.blocked_state || '').trim().toLowerCase()
  if (blockedState === 'blocked') return 'Blocked'
  const progressState = String(note?.progress_state || '').trim().toLowerCase()
  if (progressState === 'in_progress') return 'In progress'
  if (progressState === 'planned') return 'Planned'
  if (progressState === 'done' || Boolean(note?.completed)) return 'Done'
  return 'Ready'
}

function isOperatorMemoryNote(note) {
  const owner = String(note?.owner || '').trim().toLowerCase()
  const tags = Array.isArray(note?.tags)
    ? note.tags.map((tag) => String(tag || '').trim().toLowerCase()).filter(Boolean)
    : []
  return owner === 'operator-memory' || tags.includes('memory') || tags.includes('operator-memory')
}

function resolveOperatorMemoryNoteTone(note) {
  const progressState = String(note?.progress_state || '').trim().toLowerCase()
  if (progressState === 'in_progress') return 'positive'
  if (String(note?.priority || '').trim().toLowerCase() === 'high') return 'warning'
  return 'neutral'
}

function resolveOperatorMemoryNoteStatus(note) {
  const progressState = String(note?.progress_state || '').trim().toLowerCase()
  if (progressState === 'in_progress') return 'In progress'
  if (progressState === 'planned') return 'Planned'
  if (progressState === 'done' || Boolean(note?.completed)) return 'Done'
  return 'Remember'
}

function buildNotesFocusUrl(search, note) {
  const params = new URLSearchParams(search || '')
  params.set('noteFocus', 'review-loop')
  params.set('noteTag', 'review-loop')
  params.set('noteCompletion', isReviewLoopNoteResolved(note) ? 'completed' : 'open')
  params.set('noteRestored', '1')
  if (note?.id) {
    params.set('noteId', String(note.id))
  } else {
    params.delete('noteId')
  }
  if (note?.ticker) {
    params.set('noteTicker', String(note.ticker).trim().toUpperCase())
  } else {
    params.delete('noteTicker')
  }
  if (note?.title) {
    params.set('noteTitle', String(note.title).trim())
  } else {
    params.delete('noteTitle')
  }
  const nextQuery = params.toString()
  return `/notes${nextQuery ? `?${nextQuery}` : ''}`
}

function buildReviewLoopNotesUrl(search, completion = 'open', note = null) {
  const params = new URLSearchParams(search || '')
  params.set('noteFocus', 'review-loop')
  params.set('noteTag', 'review-loop')
  params.set('noteCompletion', completion === 'completed' ? 'completed' : 'open')
  params.set('noteRestored', '1')
  if (note?.id) {
    params.set('noteId', String(note.id))
  } else {
    params.delete('noteId')
  }
  if (note?.ticker) {
    params.set('noteTicker', String(note.ticker).trim().toUpperCase())
  } else {
    params.delete('noteTicker')
  }
  if (note?.title) {
    params.set('noteTitle', String(note.title).trim())
  } else {
    params.delete('noteTitle')
  }
  const nextQuery = params.toString()
  return `/notes${nextQuery ? `?${nextQuery}` : ''}`
}

function buildOperatorMemoryNotesUrl(search, note = null) {
  const params = new URLSearchParams(search || '')
  params.set('noteTag', 'memory')
  params.set('noteRestored', '1')
  if (note?.id) {
    params.set('noteId', String(note.id))
  } else {
    params.delete('noteId')
  }
  if (note?.ticker) {
    params.set('noteTicker', String(note.ticker).trim().toUpperCase())
  } else {
    params.delete('noteTicker')
  }
  if (note?.title) {
    params.set('noteTitle', String(note.title).trim())
  } else {
    params.delete('noteTitle')
  }
  const nextQuery = params.toString()
  return `/notes${nextQuery ? `?${nextQuery}` : ''}`
}

function buildCompareWorkflowReturnUrl({
  tickers = [],
  interval = '5m',
  horizon = 5,
  focusTicker = '',
  source = 'dashboard',
}) {
  const params = new URLSearchParams()
  if (tickers.length) {
    params.set('tickers', tickers.join(','))
  }
  params.set('interval', String(interval || '5m'))
  params.set('horizon', String(Math.max(1, Math.round(Number(horizon) || 5))))
  if (focusTicker) {
    params.set('focusTicker', String(focusTicker).trim().toUpperCase())
  }
  params.set('workflowAutoload', '1')
  params.set('workflowFrom', source)
  return `/compare?${params.toString()}`
}

function buildNotesWorkflowReturnUrl({
  ticker = '',
  title = '',
  completion = 'open',
  journalReturn = false,
  replaySource = '',
  replayTitle = '',
  replayStatus = '',
} = {}) {
  const params = new URLSearchParams()
  params.set('noteFocus', 'review-loop')
  params.set('noteTag', 'review-loop')
  params.set('noteCompletion', completion === 'completed' ? 'completed' : 'open')
  params.set('noteRestored', '1')
  if (journalReturn) {
    params.set('journalReturn', '1')
  }
  if (ticker) {
    params.set('noteTicker', String(ticker).trim().toUpperCase())
  }
  if (title) {
    params.set('noteTitle', String(title).trim())
  }
  if (replaySource) {
    params.set('replaySource', String(replaySource).trim().toLowerCase())
  }
  if (replayTitle) {
    params.set('replayTitle', String(replayTitle).trim())
  }
  if (replayStatus) {
    params.set('replayStatus', String(replayStatus).trim().toLowerCase())
  }
  return `/notes?${params.toString()}`
}

function formatReplayArrivalLabel(source = '', status = '') {
  const normalizedSource = String(source || '').trim().toLowerCase()
  const normalizedStatus = String(status || '').trim().toLowerCase()
  if (normalizedSource === 'board_snapshot') return 'saved board'
  if (normalizedSource === 'board_replay') {
    return normalizedStatus === 'resolved' ? 'resolved board replay' : 'board replay'
  }
  if (normalizedSource === 'live_position') return 'live position review'
  if (normalizedSource === 'journal_review') {
    return normalizedStatus === 'resolved' ? 'cleared journal review' : 'journal review'
  }
  if (normalizedSource === 'journal_repair_loop') {
    return normalizedStatus === 'resolved' ? 'cleared repair flow' : 'open repair flow'
  }
  if (normalizedSource === 'repair_note') return 'repair note'
  return 'replay context'
}

function isReviewLoopNoteResolved(note) {
  const progressState = String(note?.progress_state || '').trim().toLowerCase()
  const status = String(note?.status || '').trim().toLowerCase()
  return progressState === 'done' || status === 'done' || status === 'completed' || Boolean(note?.completed)
}

function buildReviewLoopTicketGuardrail({ currentTicker, reviewLoopNotes }) {
  const symbol = String(currentTicker || '').trim().toUpperCase()
  const noteRows = Array.isArray(reviewLoopNotes) ? reviewLoopNotes : []

  if (!symbol || !noteRows.length) {
    return {
      blocker: null,
      warning: null,
      primaryNote: null,
      noteCount: 0,
      tone: 'neutral',
    }
  }

  const matchingNotes = noteRows
    .filter((note) => String(note?.ticker || '').trim().toUpperCase() === symbol)
    .filter((note) => !isReviewLoopNoteResolved(note))
    .sort((left, right) => {
      const leftBlocked = String(left?.blocked_state || '').trim().toLowerCase() === 'blocked'
      const rightBlocked = String(right?.blocked_state || '').trim().toLowerCase() === 'blocked'
      if (leftBlocked !== rightBlocked) return leftBlocked ? -1 : 1

      const leftHighPriority = String(left?.priority || '').trim().toLowerCase() === 'high'
      const rightHighPriority = String(right?.priority || '').trim().toLowerCase() === 'high'
      if (leftHighPriority !== rightHighPriority) return leftHighPriority ? -1 : 1

      return String(right?.updated_at || '').localeCompare(String(left?.updated_at || ''))
    })

  if (!matchingNotes.length) {
    return {
      blocker: null,
      warning: null,
      primaryNote: null,
      noteCount: 0,
      tone: 'neutral',
    }
  }

  const primaryNote = matchingNotes[0]
  const noteTitle = String(primaryNote?.title || primaryNote?.body || 'Active repair note').trim()
  const isBlocked = String(primaryNote?.blocked_state || '').trim().toLowerCase() === 'blocked'
  const isHighPriority = String(primaryNote?.priority || '').trim().toLowerCase() === 'high'

  return {
    blocker:
      isBlocked || isHighPriority
        ? `High-priority repair note is still open for ${symbol}: ${noteTitle}. Resolve it before promoting new capital.`
        : null,
    warning:
      isBlocked || isHighPriority
        ? null
        : `Active repair note is still open for ${symbol}: ${noteTitle}. Review it before treating this route as clean.`,
    primaryNote,
    noteCount: matchingNotes.length,
    tone: isBlocked || isHighPriority ? 'negative' : 'warning',
  }
}

function formatShares(value) {
  const numeric = toNumber(value)
  if (numeric === null) return '--'
  const normalized = Math.max(0, numeric)
  if (Math.abs(normalized - Math.round(normalized)) < 0.0001) {
    return Math.round(normalized).toLocaleString()
  }
  return normalized.toLocaleString('en-US', {
    minimumFractionDigits: normalized < 1 ? 3 : 0,
    maximumFractionDigits: 3,
  })
}

function intervalToMinutes(interval) {
  const intervalMap = {
    '1m': 1,
    '5m': 5,
    '15m': 15,
    '30m': 30,
    '1h': 60,
    '4h': 240,
    '1d': 1440,
  }
  return intervalMap[String(interval || '').trim().toLowerCase()] || 5
}

function formatForecastHorizon(interval, horizon) {
  const steps = Math.max(1, Math.round(toNumber(horizon) || 1))
  const totalMinutes = intervalToMinutes(interval) * steps
  let durationLabel = `${totalMinutes}m`
  if (totalMinutes >= 1440 && totalMinutes % 1440 === 0) {
    durationLabel = `${totalMinutes / 1440}d`
  } else if (totalMinutes >= 60 && totalMinutes % 60 === 0) {
    durationLabel = `${totalMinutes / 60}h`
  } else if (totalMinutes > 60) {
    durationLabel = `${(totalMinutes / 60).toFixed(1)}h`
  }
  return `${steps} bar${steps === 1 ? '' : 's'} (~${durationLabel})`
}

function normalizeInstrumentType(value) {
  return String(value || '').trim().toLowerCase() === 'equity' ? 'equity' : 'listed_option'
}

function normalizeOptionStrategy(value) {
  const normalized = String(value || '').trim().toLowerCase()
  if (['short_premium', 'short-premium', 'short premium'].includes(normalized)) {
    return 'short_premium'
  }
  if (['vertical_spread', 'vertical-spread', 'vertical spread'].includes(normalized)) {
    return 'vertical_spread'
  }
  return 'long_option'
}

function formatInstrumentTypeLabel(value) {
  return normalizeInstrumentType(value) === 'equity' ? 'Equity' : 'Listed option'
}

function formatOptionStrategyLabel(value) {
  return (
    optionStrategyOptions.find((option) => option.key === normalizeOptionStrategy(value))?.label ||
    'Long option'
  )
}

function describeInstrumentType(value) {
  return normalizeInstrumentType(value) === 'equity'
    ? 'Size the ticket in shares using the live price versus the invalidation level.'
    : 'Use the recommended listed option contract with explicit structure and 100-share multiplier awareness.'
}

function describeOptionStrategy(value, optionRight = 'call') {
  const strategy = normalizeOptionStrategy(value)
  const rightLabel = String(optionRight || 'call').trim().toUpperCase()
  if (strategy === 'short_premium') {
    return 'Review a premium-selling idea. Submit is blocked until margin, assignment, and buy-to-close controls are enabled.'
  }
  if (strategy === 'vertical_spread') {
    return 'Review a defined-risk multi-leg spread. Submit is blocked until multi-leg validation and routing are enabled.'
  }
  return `Route a buy-to-open ${rightLabel} contract. Each listed option contract controls 100 underlying shares.`
}

function optionStrategyBrokerSide(value) {
  return normalizeOptionStrategy(value) === 'short_premium' ? 'sell' : 'buy'
}

function formatUnitLabel(instrumentType, quantity = null) {
  const singular = normalizeInstrumentType(instrumentType) === 'equity' ? 'share' : 'contract'
  if (quantity === 1) return singular
  return `${singular}s`
}

function buildFallbackForecastFraming(interval, horizon) {
  return {
    target_family: 'directional_move',
    label: 'Directional move',
    short_label: 'Direction',
    use_label: 'Best for conditional direction over the selected bar window.',
    trust_label: 'This is a horizon-bound directional read, not a broad market forecast.',
    interval: String(interval || '5m').trim().toLowerCase(),
    horizon_bars: Math.max(1, Math.round(toNumber(horizon) || 1)),
    horizon_label: formatForecastHorizon(interval, horizon),
    benchmark_label: 'Neutral 50/50',
    benchmark_detail: 'Fallback mode should at least clear a neutral up/down baseline before it is treated as actionable.',
    benchmark_reference_probability: 0.5,
  }
}

function inferLegacyEventWindowLabel(source = null) {
  const explicitLabel = String(source?.event_window_label || '').trim().toLowerCase()
  if (explicitLabel) return explicitLabel
  const nextEvent = String(source?.next_event_name || '').trim().toLowerCase()
  const eventLabel = String(source?.event_label || '').trim().toLowerCase()
  const eventReason = String(source?.event_reason || '').trim().toLowerCase()
  if (!Boolean(source?.event_risk)) return 'quiet_window'
  if (
    nextEvent.includes('earnings') ||
    eventLabel.includes('earnings') ||
    eventReason.includes('earnings')
  ) {
    return 'earnings_window'
  }
  if (
    nextEvent.includes('macro') ||
    eventLabel.includes('macro') ||
    eventReason.includes('macro')
  ) {
    return 'macro_window'
  }
  return nextEvent || eventLabel || eventReason ? 'corporate_window' : 'event_window'
}

function buildFallbackEventContext(source = null) {
  const eventRisk = Boolean(source?.event_risk)
  const nextEventName = String(source?.next_event_name || '').trim()
  const nextEventDate = String(source?.next_event_date || '').trim()
  const nextEventDays = toNumber(source?.next_event_days)
  const eventWindowLabel = inferLegacyEventWindowLabel(source)
  const eventSeverity =
    String(source?.event_severity || '').trim().toLowerCase() ||
    (eventRisk ? 'high' : eventWindowLabel === 'quiet_window' ? 'low' : 'medium')
  const tradePosture =
    String(source?.trade_posture || '').trim().toLowerCase() ||
    (eventRisk ? 'defer' : eventWindowLabel === 'quiet_window' ? 'clear' : 'caution')
  const sessionLabel =
    String(source?.event_session_label || source?.session_label || '').trim().toLowerCase() ||
    (eventRisk ? 'event_heavy_session' : eventWindowLabel === 'quiet_window' ? 'quiet_session' : 'event_watch_session')
  const primaryEventLabel =
    String(source?.primary_event_label || source?.event_label || '').trim() ||
    (eventWindowLabel === 'earnings_window'
      ? 'Earnings window'
      : eventWindowLabel === 'macro_window'
        ? 'Macro window'
        : eventWindowLabel === 'corporate_window'
          ? 'Corporate window'
          : eventRisk
            ? 'Event risk'
            : 'Quiet window')
  const summary =
    String(source?.summary || source?.event_reason || '').trim() ||
    (eventRisk
      ? `${nextEventName || 'A known catalyst'} is close enough to distort normal stop logic and widen spreads.`
      : nextEventName
        ? `${nextEventName} is on deck, so treat this setup as more conditional until the catalyst window clears.`
        : 'No near-term catalyst window is active.')
  const rawUpcomingEvents = Array.isArray(source?.upcoming_events) ? source.upcoming_events : []
  const upcomingEvents = rawUpcomingEvents.length
    ? rawUpcomingEvents
    : nextEventName
      ? [
          {
            event_name: nextEventName,
            event_date: nextEventDate,
            event_class: eventWindowLabel.replace('_window', ''),
            source: 'legacy',
            days_until: nextEventDays,
            severity: eventSeverity,
          },
        ]
      : []

  return {
    event_risk: eventRisk,
    event_label: String(source?.event_label || '').trim(),
    event_reason: String(source?.event_reason || '').trim(),
    next_event_name: nextEventName,
    next_event_date: nextEventDate,
    next_event_days: nextEventDays,
    next_earnings_name: String(source?.next_earnings_name || '').trim(),
    next_earnings_date: String(source?.next_earnings_date || '').trim(),
    next_earnings_days: toNumber(source?.next_earnings_days),
    next_macro_name: String(source?.next_macro_name || '').trim(),
    next_macro_date: String(source?.next_macro_date || '').trim(),
    next_macro_days: toNumber(source?.next_macro_days),
    next_corporate_name: String(source?.next_corporate_name || '').trim(),
    next_corporate_date: String(source?.next_corporate_date || '').trim(),
    next_corporate_days: toNumber(source?.next_corporate_days),
    event_class: String(source?.event_class || '').trim().toLowerCase() || eventWindowLabel.replace('_window', ''),
    event_severity: eventSeverity,
    event_window_label: eventWindowLabel,
    session_label: sessionLabel,
    trade_posture: tradePosture,
    primary_event_label: primaryEventLabel,
    summary,
    upcoming_events: upcomingEvents,
  }
}

function resolveEventContext(rawContext, source = null) {
  const fallback = buildFallbackEventContext(source)
  if (!rawContext || typeof rawContext !== 'object') return fallback
  return {
    ...fallback,
    ...rawContext,
    event_risk: Boolean(rawContext.event_risk ?? fallback.event_risk),
    next_event_days: toNumber(rawContext.next_event_days ?? fallback.next_event_days),
    next_earnings_days: toNumber(rawContext.next_earnings_days ?? fallback.next_earnings_days),
    next_macro_days: toNumber(rawContext.next_macro_days ?? fallback.next_macro_days),
    next_corporate_days: toNumber(rawContext.next_corporate_days ?? fallback.next_corporate_days),
    upcoming_events:
      Array.isArray(rawContext.upcoming_events) && rawContext.upcoming_events.length
        ? rawContext.upcoming_events
        : fallback.upcoming_events,
  }
}

function eventContextTone(context) {
  const normalizedPosture = String(context?.trade_posture || '').trim().toLowerCase()
  const normalizedSeverity = String(context?.event_severity || '').trim().toLowerCase()
  if (Boolean(context?.event_risk) || normalizedPosture === 'defer') return 'negative'
  if (
    normalizedPosture === 'caution' ||
    normalizedSeverity === 'critical' ||
    normalizedSeverity === 'high' ||
    normalizedSeverity === 'medium'
  ) {
    return 'warning'
  }
  return 'positive'
}

function eventContextStatus(context) {
  const primaryLabel = String(context?.primary_event_label || context?.event_label || '').trim()
  if (primaryLabel) return primaryLabel
  return Boolean(context?.event_risk) ? 'Event risk' : 'Quiet window'
}

function eventContextDetail(context) {
  const summary = String(context?.summary || context?.event_reason || '').trim()
  if (summary) return summary
  const nextEventName = String(context?.next_event_name || '').trim()
  if (nextEventName) {
    return `${nextEventName} is the next known catalyst, so keep the setup under review until the window clears.`
  }
  return 'No near-term catalyst window is active.'
}

function eventContextNextLabel(context) {
  const nextEventName = String(context?.next_event_name || '').trim()
  if (!nextEventName) return ''
  const nextEventTime = formatEventTime(context?.next_event_date)
  if (nextEventTime === '--') return `Next: ${nextEventName}.`
  return `Next: ${nextEventName} at ${nextEventTime}.`
}

function buildEventCalendarPriorityLookup(items = [], currentTicker = '') {
  const normalizedCurrentTicker = String(currentTicker || '').trim().toUpperCase()

  return (Array.isArray(items) ? items : []).reduce((lookup, item, index) => {
    const ticker = String(item?.ticker || '').trim().toUpperCase()
    if (!ticker) return lookup

    const daysUntil = toNumber(item?.days_until)
    const impact = String(item?.impact || '').trim().toLowerCase()
    const tone = String(item?.tone || '').trim().toLowerCase()
    const source = String(item?.source || '').trim().toLowerCase()
    let priority = 0

    if (daysUntil !== null) {
      if (daysUntil <= 0) priority += 70
      else if (daysUntil <= 1) priority += 56
      else if (daysUntil <= 3) priority += 42
      else if (daysUntil <= 7) priority += 24
      else if (daysUntil <= 14) priority += 10
    }

    if (impact === 'high') priority += 18
    else if (impact === 'medium') priority += 9

    if (tone === 'negative') priority += 16
    else if (tone === 'warning') priority += 8

    if (source === 'ticker_event') priority += 6
    if (index < 4) priority += (4 - index) * 2
    if (ticker === normalizedCurrentTicker) priority += 4

    lookup[ticker] = Math.max(lookup[ticker] || 0, priority)
    return lookup
  }, {})
}

function prioritizeRowsByEventCalendar(rows = [], items = [], currentTicker = '') {
  const priorityLookup = buildEventCalendarPriorityLookup(items, currentTicker)

  return (Array.isArray(rows) ? rows : [])
    .map((row, index) => ({ row, index }))
    .sort((left, right) => {
      const leftTicker = String(left.row?.ticker || '').trim().toUpperCase()
      const rightTicker = String(right.row?.ticker || '').trim().toUpperCase()
      const leftPriority = priorityLookup[leftTicker] || 0
      const rightPriority = priorityLookup[rightTicker] || 0

      if (leftPriority !== rightPriority) return rightPriority - leftPriority

      const leftBoardRank = toNumber(left.row?.board_rank ?? left.row?.ranking_context?.board_rank)
      const rightBoardRank = toNumber(right.row?.board_rank ?? right.row?.ranking_context?.board_rank)
      if (leftBoardRank !== null || rightBoardRank !== null) {
        if (leftBoardRank === null) return 1
        if (rightBoardRank === null) return -1
        if (leftBoardRank !== rightBoardRank) return leftBoardRank - rightBoardRank
      }

      const leftScore = toNumber(
        left.row?.ranking_score ?? left.row?.ranking_context?.score ?? left.row?.setup_score,
      )
      const rightScore = toNumber(
        right.row?.ranking_score ?? right.row?.ranking_context?.score ?? right.row?.setup_score,
      )
      if (leftScore !== null || rightScore !== null) {
        if (leftScore === null) return 1
        if (rightScore === null) return -1
        if (leftScore !== rightScore) return rightScore - leftScore
      }

      return left.index - right.index
    })
    .map(({ row }) => row)
}

function resolveMorningBriefCalendarLead(eventCalendar, currentTicker = '') {
  const items = Array.isArray(eventCalendar?.items) ? eventCalendar.items : []
  const normalizedCurrentTicker = String(currentTicker || '').trim().toUpperCase()
  const nextItem =
    eventCalendar?.summary?.next_item && typeof eventCalendar.summary.next_item === 'object'
      ? eventCalendar.summary.next_item
      : items[0] || null

  const currentTickerEvent = normalizedCurrentTicker
    ? items.find((item) => String(item?.ticker || '').trim().toUpperCase() === normalizedCurrentTicker) || null
    : null
  const urgentMacro =
    items.find((item) => {
      const source = String(item?.source || '').trim().toLowerCase()
      const impact = String(item?.impact || '').trim().toLowerCase()
      const daysUntil = toNumber(item?.days_until)
      return source === 'macro_calendar' && impact === 'high' && daysUntil !== null && daysUntil <= 1
    }) || null

  return {
    nextItem,
    urgentMacro,
    currentTickerEvent:
      currentTickerEvent && toNumber(currentTickerEvent?.days_until) !== null && toNumber(currentTickerEvent?.days_until) <= 3
        ? currentTickerEvent
        : null,
  }
}

function parseIsoDate(value) {
  if (!value) return null
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

function daysUntilExpiration(value) {
  const expiration = parseIsoDate(value)
  if (!expiration) return null
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const startOfExpiry = new Date(
    expiration.getFullYear(),
    expiration.getMonth(),
    expiration.getDate(),
  ).getTime()
  return Math.round((startOfExpiry - startOfToday) / (1000 * 60 * 60 * 24))
}

function describeOptionStrategyShape(optionRight, optionStrategy = 'long_option') {
  const strategy = normalizeOptionStrategy(optionStrategy)
  if (strategy === 'short_premium') {
    return String(optionRight || '').trim().toLowerCase() === 'put'
      ? 'Short put premium'
      : 'Short call premium'
  }
  if (strategy === 'vertical_spread') {
    return String(optionRight || '').trim().toLowerCase() === 'put'
      ? 'Put vertical spread'
      : 'Call vertical spread'
  }
  return String(optionRight || '').trim().toLowerCase() === 'put'
    ? 'Long put debit'
    : 'Long call debit'
}

function formatOrderTypeLabel(value) {
  return (
    orderTypeOptions.find((option) => option.key === value)?.label ||
    String(value || 'market').replaceAll('_', ' ')
  )
}

function formatTimeInForceLabel(value) {
  return (
    timeInForceOptions.find((option) => option.key === value)?.label ||
    String(value || 'day').toUpperCase()
  )
}

function formatOrderLifecycleValue(value, fallback = '--') {
  const normalized = String(value || '').trim()
  if (!normalized) return fallback
  return normalized.replaceAll('_', ' ').replace(/\b\w/g, (character) => character.toUpperCase())
}

function formatOrderLifecycleLabel(event) {
  if (!event) return 'Recorded'
  if (event.label) return event.label
  const normalizedKey = String(event.event_key || '').trim().toLowerCase()
  const normalizedStatus = String(event.status || '').trim().toLowerCase()
  if (normalizedKey === 'order.submitted') return 'Submitted'
  if (normalizedKey === 'order.accepted' || normalizedStatus === 'working') return 'Working'
  if (normalizedKey === 'order.replaced') return 'Replaced'
  if (normalizedKey === 'order.canceled' || normalizedStatus === 'canceled') return 'Canceled'
  if (normalizedKey === 'order.filled') return 'Filled'
  if (normalizedKey === 'order.rejected' || normalizedStatus === 'rejected') return 'Rejected'
  if (normalizedKey === 'order.closed' || normalizedStatus === 'closed') return 'Closed'
  if (normalizedStatus === 'filled') return 'Filled'
  if (normalizedStatus === 'open') return 'Accepted'
  return formatOrderLifecycleValue(normalizedStatus, 'Recorded')
}

function describeOrderType(value) {
  switch (value) {
    case 'limit':
      return 'Work a specific price and wait for price improvement before filling.'
    case 'stop_market':
      return 'Trigger into a market order once the stop level is breached.'
    case 'stop_limit':
      return 'Trigger into a limit order once the stop level is breached.'
    case 'trailing_stop':
      return 'Trail the market by a fixed percentage while protecting the move.'
    case 'market':
    default:
      return 'Take the inside market immediately using the current live execution price.'
  }
}

function buildTicketEducationCards({
  report,
  instrumentType,
  optionStrategy,
  optionRight,
  contract,
  orderType,
  timeInForce,
  riskReward,
  positionPreview,
}) {
  const normalizedInstrumentType = normalizeInstrumentType(instrumentType)
  const normalizedOptionStrategy = normalizeOptionStrategy(optionStrategy)
  const normalizedOrderType = String(orderType || 'market').trim().toLowerCase()
  const normalizedTimeInForce = String(timeInForce || 'day').trim().toLowerCase()
  const dte = daysUntilExpiration(contract?.expiration)
  const cards = []

  if (normalizedInstrumentType === 'listed_option') {
    const strategyDetail =
      normalizedOptionStrategy === 'short_premium'
        ? 'Short premium is review-only here because assignment, margin expansion, and buy-to-close controls must be explicit before routing.'
        : normalizedOptionStrategy === 'vertical_spread'
          ? 'Vertical spreads are review-only here until the ticket has a real multi-leg contract builder, net debit/credit validation, and multi-leg routing payload.'
          : `This desk opens long ${String(optionRight || 'call').toUpperCase()} option tickets as buy-to-open contracts, so max loss is capped at the premium paid.`
    cards.push({
      key: 'risk-structure',
      title: 'Risk structure',
      value: formatOptionStrategyLabel(normalizedOptionStrategy),
      tone: normalizedOptionStrategy === 'long_option' ? 'positive' : 'warning',
      detail: strategyDetail,
    })
  } else {
    cards.push({
      key: 'risk-structure',
      title: 'Risk structure',
      value: 'Linear spot',
      tone: 'warning',
      detail: 'Equity trades move one-for-one with the underlying. Risk is controlled by share size versus invalidation, not by a capped premium outlay.',
    })
  }

  cards.push({
    key: 'order-behavior',
    title: 'Order behavior',
    value: formatOrderTypeLabel(orderType),
    tone:
      normalizedOrderType === 'market'
        ? 'warning'
        : normalizedOrderType === 'trailing_stop'
          ? 'warning'
          : 'positive',
    detail:
      normalizedOrderType === 'market'
        ? 'Market orders prioritize getting filled now, so they need tight spreads and clean liquidity.'
        : normalizedOrderType === 'trailing_stop'
          ? 'Trailing stops are reactive protection tools and can move on quote noise, especially in thinner books.'
          : 'Price-controlled orders are slower, but they usually protect execution quality better than a market order.',
  })

  cards.push({
    key: 'time-window',
    title: 'Time window',
    value: formatTimeInForceLabel(timeInForce),
    tone:
      normalizedInstrumentType === 'listed_option' && normalizedTimeInForce === 'day_ext'
        ? 'negative'
        : dte !== null && dte <= 2
          ? 'warning'
          : 'neutral',
    detail:
      normalizedInstrumentType === 'listed_option' && normalizedTimeInForce === 'day_ext'
        ? 'Listed options are kept on regular-hours routing here because after-hours option liquidity is too inconsistent.'
        : dte !== null && dte === 0
          ? 'Same-day expiry options decay and reprice too fast for this desk flow, so they are intentionally blocked.'
          : dte !== null && dte <= 2
            ? `This contract is only ${dte} DTE, so gamma, assignment pressure, and spread sensitivity are all elevated.`
            : normalizedTimeInForce === 'gtc_90d'
              ? 'Longer resting orders can make sense for swing ideas, but they need periodic review as liquidity and events change.'
              : 'Day orders force a same-session decision and reduce the chance of a stale order drifting into a different market condition.',
  })

  cards.push({
    key: 'event-and-risk',
    title: 'Event and invalidation',
    value: report?.event_risk ? 'Event risk' : riskReward === null ? 'Needs review' : `${riskReward.toFixed(2)}R`,
    tone: report?.event_risk ? 'negative' : riskReward !== null && riskReward >= 2 ? 'positive' : 'warning',
    detail: report?.event_risk
      ? `${report?.event_label || 'Event risk is active'}${report?.event_reason ? `: ${report.event_reason}` : '.'} Treat gap risk and liquidity shock as part of the trade, not as a surprise.`
      : positionPreview?.effectiveMaxRiskDollars
        ? `The desk sizes this ticket against a live invalidation rule and effective risk budget of ${formatPrice(positionPreview.effectiveMaxRiskDollars)}.`
        : 'A trade is only actionable when invalidation and effective risk are explicit.',
  })

  return cards
}

function buildEntryDragProfile({ instrumentType, positionPreview, quote, contract, livePrice }) {
  const normalizedInstrumentType = normalizeInstrumentType(instrumentType)
  const units = toNumber(positionPreview?.suggestedContracts)

  let estimatedEntryDrag = null
  let spreadContext = '--'
  let spreadTone = 'warning'
  let spreadDetail = 'Waiting for enough quote detail to estimate entry drag.'

  if (normalizedInstrumentType === 'listed_option') {
    const mid = toNumber(positionPreview?.entryUnitPrice ?? contract?.mid)
    const spreadPct = toNumber(contract?.spread_pct)
    const spreadPerContract =
      mid !== null && spreadPct !== null ? mid * (spreadPct / 100) * 100 : null
    estimatedEntryDrag =
      spreadPerContract !== null && units !== null ? (spreadPerContract / 2) * units : null
    spreadContext =
      spreadPct !== null ? `${formatPercent(spreadPct, 1)} contract spread` : 'Contract quote pending'
    spreadTone =
      estimatedEntryDrag === null
        ? 'warning'
        : estimatedEntryDrag <= 20
          ? 'positive'
          : estimatedEntryDrag <= 75
            ? 'warning'
            : 'negative'
    spreadDetail =
      estimatedEntryDrag === null
        ? 'Option quote depth is not fully mapped yet, so treat execution quality as unconfirmed.'
        : `Crossing half the current option spread would cost about ${formatPrice(estimatedEntryDrag)} on entry for ${formatShares(units)} contracts.`
  } else {
    const bid = toNumber(quote?.bid_price)
    const ask = toNumber(quote?.ask_price)
    const rawSpread = resolveDisplaySpread(quote?.spread, bid, ask)
    const referencePrice = toNumber(livePrice)
    const spreadPct =
      rawSpread !== null && referencePrice !== null && referencePrice > 0
        ? (rawSpread / referencePrice) * 100
        : null
    estimatedEntryDrag = rawSpread !== null && units !== null ? (rawSpread / 2) * units : null
    spreadContext = rawSpread !== null ? `${formatPrice(rawSpread)} spread` : 'Live quote pending'
    spreadTone =
      estimatedEntryDrag === null
        ? 'warning'
        : estimatedEntryDrag <= 10
          ? 'positive'
          : estimatedEntryDrag <= 40
            ? 'warning'
            : 'negative'
    spreadDetail =
      rawSpread === null
        ? 'Waiting for a live bid/ask to estimate the current entry drag.'
        : `Crossing half the live book would cost about ${formatPrice(estimatedEntryDrag)} on entry, with the spread running ${formatPercent(spreadPct, 2)} of price.`
  }

  return {
    estimatedEntryDrag,
    spreadContext,
    spreadTone,
    spreadDetail,
  }
}

function estimateRouteDrag(baseDrag, { instrumentType, orderType, timeInForce }) {
  const normalizedBaseDrag = toNumber(baseDrag)
  if (normalizedBaseDrag === null) {
    return null
  }

  const normalizedInstrumentType = normalizeInstrumentType(instrumentType)
  const normalizedOrderType = String(orderType || 'market').trim().toLowerCase()
  const normalizedTimeInForce = String(timeInForce || 'day').trim().toLowerCase()

  let multiplier = 0.7
  if (normalizedOrderType === 'market') {
    multiplier = 1
  } else if (normalizedOrderType === 'limit') {
    multiplier = 0.35
  } else if (normalizedOrderType === 'stop_market') {
    multiplier = 0.9
  } else if (normalizedOrderType === 'stop_limit') {
    multiplier = 0.55
  } else if (normalizedOrderType === 'trailing_stop') {
    multiplier = 0.85
  }

  if (normalizedTimeInForce === 'day_ext') {
    multiplier *= normalizedOrderType === 'market' ? 1.35 : 1.1
  }

  if (normalizedInstrumentType === 'listed_option' && normalizedOrderType === 'trailing_stop') {
    multiplier *= 1.08
  }

  return normalizedBaseDrag * multiplier
}

function buildExecutionReviewSnapshot({
  ticker,
  instrumentType,
  orderType,
  timeInForce,
  positionPreview,
  quote,
  contract,
  livePrice,
}) {
  const normalizedInstrumentType = normalizeInstrumentType(instrumentType)
  const bid = toNumber(quote?.bid_price)
  const ask = toNumber(quote?.ask_price)
  const stockSpread = resolveDisplaySpread(quote?.spread, bid, ask)
  const referencePrice =
    toNumber(livePrice) ??
    (bid !== null && ask !== null ? (bid + ask) / 2 : null) ??
    toNumber(positionPreview?.entryUnitPrice) ??
    toNumber(contract?.mid)
  const optionSpreadPct = toNumber(contract?.spread_pct)
  const optionVolume = toNumber(contract?.volume)
  const optionOpenInterest = toNumber(contract?.open_interest)
  const optionMid = toNumber(contract?.mid)
  const optionBid = toNumber(contract?.bid)
  const optionAsk = toNumber(contract?.ask)
  const optionQuoteTimestamp = contract?.quote_timestamp || null
  let optionQuoteAgeSeconds = null
  if (optionQuoteTimestamp) {
    const quoteTimestampMs = new Date(optionQuoteTimestamp).getTime()
    if (Number.isFinite(quoteTimestampMs)) {
      optionQuoteAgeSeconds = Math.max(0, Math.round((Date.now() - quoteTimestampMs) / 1000))
    }
  }
  const { estimatedEntryDrag } = buildEntryDragProfile({
    instrumentType,
    positionPreview,
    quote,
    contract,
    livePrice,
  })

  return {
    ticker: String(ticker || '').trim().toUpperCase(),
    instrumentType: normalizedInstrumentType,
    referencePrice,
    contractSymbol: String(contract?.contract_symbol || '').trim() || null,
    optionBid,
    optionAsk,
    optionMid,
    stockSpread,
    optionSpreadPct,
    optionVolume,
    optionOpenInterest,
    optionQuoteTimestamp,
    optionQuoteAgeSeconds,
    routeDrag: estimateRouteDrag(estimatedEntryDrag, {
      instrumentType,
      orderType,
      timeInForce,
    }),
  }
}

function buildOptionExecutionReviewPanel({
  executionReviewSnapshot,
  latestBackendOrderEvent,
  activePendingOrder,
}) {
  if (!executionReviewSnapshot || executionReviewSnapshot.instrumentType !== 'listed_option') {
    return null
  }

  const eventPayload =
    latestBackendOrderEvent && typeof latestBackendOrderEvent.payload === 'object'
      ? latestBackendOrderEvent.payload
      : {}
  const backendReview =
    eventPayload && typeof eventPayload.option_execution_review === 'object'
      ? eventPayload.option_execution_review
      : {}
  const reviewChecks = Array.isArray(backendReview.checks) ? backendReview.checks : []
  const latestRecord =
    eventPayload && typeof eventPayload.record === 'object' ? eventPayload.record : activePendingOrder || {}

  const spreadPct =
    toNumber(backendReview.spread_pct) ?? toNumber(executionReviewSnapshot.optionSpreadPct)
  const quoteAgeSeconds =
    toNumber(backendReview.quote_age_seconds) ?? toNumber(executionReviewSnapshot.optionQuoteAgeSeconds)
  const volume = toNumber(backendReview.volume) ?? toNumber(executionReviewSnapshot.optionVolume)
  const openInterest =
    toNumber(backendReview.open_interest) ?? toNumber(executionReviewSnapshot.optionOpenInterest)
  const contractSymbol =
    String(backendReview.contract_symbol || executionReviewSnapshot.contractSymbol || '').trim() || '--'
  const expectedFillPrice =
    toNumber(latestRecord.expected_fill_price) ??
    toNumber(backendReview.expected_fill_price) ??
    toNumber(executionReviewSnapshot.optionMid)
  const actualFillPrice =
    toNumber(latestRecord.actual_fill_price) ?? toNumber(backendReview.actual_fill_price)
  const fillSlippageBps =
    toNumber(latestRecord.fill_slippage_bps) ?? toNumber(backendReview.fill_slippage_bps)
  const fillSlippageDollars =
    toNumber(latestRecord.fill_slippage_dollars) ?? toNumber(backendReview.fill_slippage_dollars)

  const spreadClear = spreadPct !== null && spreadPct <= 0.15
  const freshnessClear = quoteAgeSeconds !== null && quoteAgeSeconds <= 180
  const volumeClear = volume !== null && volume >= 25
  const openInterestClear = openInterest !== null && openInterest >= 100
  const allChecksClear = spreadClear && freshnessClear && volumeClear && openInterestClear
  const hasFillAudit = actualFillPrice !== null || fillSlippageBps !== null
  const tone = hasFillAudit
    ? fillSlippageBps !== null && Math.abs(fillSlippageBps) >= 15
      ? 'warning'
      : 'positive'
    : allChecksClear
      ? 'positive'
      : 'negative'
  const label = hasFillAudit ? 'Fill audit' : allChecksClear ? 'Route clear' : 'Review route'
  const detail = hasFillAudit
    ? actualFillPrice !== null
      ? `Expected ${formatPrice(expectedFillPrice)} and filled ${formatPrice(actualFillPrice)}.`
      : 'A live order exists, but fill quality is still waiting on the broker response.'
    : 'Spread, freshness, and participation are being checked before the option route is trusted.'

  const checks = reviewChecks.length
    ? reviewChecks.map((check) => ({
        key: check.key,
        label: check.label,
        status: check.status,
        value:
          check.key === 'spread'
            ? formatPercent(toNumber(check.value), 1)
            : check.key === 'quote_age_seconds'
              ? `${Math.round(toNumber(check.value) ?? 0)}s`
              : formatCompact(toNumber(check.value) ?? 0),
      }))
    : [
        { key: 'spread', label: 'Spread', status: spreadClear ? 'pass' : 'fail', value: formatPercent(spreadPct, 1) },
        { key: 'quote_age_seconds', label: 'Quote age', status: freshnessClear ? 'pass' : 'fail', value: quoteAgeSeconds === null ? '--' : `${Math.round(quoteAgeSeconds)}s` },
        { key: 'volume', label: 'Volume', status: volumeClear ? 'pass' : 'fail', value: formatCompact(volume) },
        { key: 'open_interest', label: 'Open interest', status: openInterestClear ? 'pass' : 'fail', value: formatCompact(openInterest) },
      ]

  return {
    tone,
    label,
    detail,
    contractSymbol,
    spreadPct,
    quoteAgeSeconds,
    volume,
    openInterest,
    expectedFillPrice,
    actualFillPrice,
    fillSlippageBps,
    fillSlippageDollars,
    brokerLabel: latestBackendOrderEvent?.label || (activePendingOrder ? 'Working' : 'Watching'),
    checks,
  }
}

function buildExecutionReviewDrift({ baseline, current }) {
  if (!baseline || !current) return null
  if (baseline.ticker !== current.ticker || baseline.instrumentType !== current.instrumentType) {
    return null
  }

  const items = []
  let hasImprovement = false
  let hasDeterioration = false
  let hasMovement = false

  const baselinePrice = toNumber(baseline.referencePrice)
  const currentPrice = toNumber(current.referencePrice)
  if (baselinePrice !== null && currentPrice !== null && baselinePrice > 0) {
    const delta = currentPrice - baselinePrice
    const pctDelta = (delta / baselinePrice) * 100
    const priceThreshold =
      current.instrumentType === 'listed_option'
        ? Math.max(0.05, baselinePrice * 0.002)
        : Math.max(0.1, baselinePrice * 0.0015)
    if (Math.abs(delta) >= priceThreshold || Math.abs(pctDelta) >= 0.2) {
      hasMovement = true
      items.push({
        key: 'price',
        label: 'Quote moved',
        tone: 'warning',
        message: `Live price shifted from ${formatPrice(baselinePrice)} to ${formatPrice(currentPrice)} (${pctDelta > 0 ? '+' : ''}${formatPercent(pctDelta, 2)}).`,
      })
    }
  }

  if (current.instrumentType === 'listed_option') {
    const baselineSpreadPct = toNumber(baseline.optionSpreadPct)
    const currentSpreadPct = toNumber(current.optionSpreadPct)
    if (baselineSpreadPct !== null && currentSpreadPct !== null && Math.abs(currentSpreadPct - baselineSpreadPct) >= 1) {
      if (currentSpreadPct < baselineSpreadPct) {
        hasImprovement = true
        items.push({
          key: 'option-spread',
          label: 'Spread improved',
          tone: 'positive',
          message: `Contract spread tightened from ${formatPercent(baselineSpreadPct, 1)} to ${formatPercent(currentSpreadPct, 1)}.`,
        })
      } else {
        hasDeterioration = true
        items.push({
          key: 'option-spread',
          label: 'Spread widened',
          tone: 'negative',
          message: `Contract spread widened from ${formatPercent(baselineSpreadPct, 1)} to ${formatPercent(currentSpreadPct, 1)}.`,
        })
      }
    }

    const volumeTier = (value) => (value === null ? null : value < 50 ? 0 : value < 250 ? 1 : 2)
    const oiTier = (value) => (value === null ? null : value < 100 ? 0 : value < 500 ? 1 : 2)
    const baselineVolumeTier = volumeTier(toNumber(baseline.optionVolume))
    const currentVolumeTier = volumeTier(toNumber(current.optionVolume))
    const baselineOiTier = oiTier(toNumber(baseline.optionOpenInterest))
    const currentOiTier = oiTier(toNumber(current.optionOpenInterest))

    if (baselineVolumeTier !== null && currentVolumeTier !== null && baselineVolumeTier !== currentVolumeTier) {
      if (currentVolumeTier > baselineVolumeTier) {
        hasImprovement = true
        items.push({
          key: 'volume',
          label: 'Volume improved',
          tone: 'positive',
          message: `Contract volume improved to ${formatCompact(current.optionVolume)} from ${formatCompact(baseline.optionVolume)}.`,
        })
      } else {
        hasDeterioration = true
        items.push({
          key: 'volume',
          label: 'Volume softened',
          tone: 'negative',
          message: `Contract volume slipped to ${formatCompact(current.optionVolume)} from ${formatCompact(baseline.optionVolume)}.`,
        })
      }
    }

    if (baselineOiTier !== null && currentOiTier !== null && baselineOiTier !== currentOiTier) {
      if (currentOiTier > baselineOiTier) {
        hasImprovement = true
        items.push({
          key: 'oi',
          label: 'Open interest improved',
          tone: 'positive',
          message: `Open interest improved to ${formatCompact(current.optionOpenInterest)} from ${formatCompact(baseline.optionOpenInterest)}.`,
        })
      } else {
        hasDeterioration = true
        items.push({
          key: 'oi',
          label: 'Open interest softened',
          tone: 'negative',
          message: `Open interest dropped to ${formatCompact(current.optionOpenInterest)} from ${formatCompact(baseline.optionOpenInterest)}.`,
        })
      }
    }
  } else {
    const baselineSpread = toNumber(baseline.stockSpread)
    const currentSpread = toNumber(current.stockSpread)
    if (baselineSpread !== null && currentSpread !== null) {
      const spreadThreshold = Math.max(0.02, baselineSpread * 0.2)
      if (Math.abs(currentSpread - baselineSpread) >= spreadThreshold) {
        if (currentSpread < baselineSpread) {
          hasImprovement = true
          items.push({
            key: 'spread',
            label: 'Spread tightened',
            tone: 'positive',
            message: `Live spread tightened from ${formatPrice(baselineSpread)} to ${formatPrice(currentSpread)}.`,
          })
        } else {
          hasDeterioration = true
          items.push({
            key: 'spread',
            label: 'Spread widened',
            tone: 'negative',
            message: `Live spread widened from ${formatPrice(baselineSpread)} to ${formatPrice(currentSpread)}.`,
          })
        }
      }
    }
  }

  const baselineRouteDrag = toNumber(baseline.routeDrag)
  const currentRouteDrag = toNumber(current.routeDrag)
  if (baselineRouteDrag !== null && currentRouteDrag !== null) {
    const dragThreshold = Math.max(2, baselineRouteDrag * 0.2)
    if (Math.abs(currentRouteDrag - baselineRouteDrag) >= dragThreshold) {
      if (currentRouteDrag < baselineRouteDrag) {
        hasImprovement = true
        items.push({
          key: 'route-drag',
          label: 'Route drag improved',
          tone: 'positive',
          message: `Estimated route drag improved from ${formatPrice(baselineRouteDrag)} to ${formatPrice(currentRouteDrag)}.`,
        })
      } else {
        hasDeterioration = true
        items.push({
          key: 'route-drag',
          label: 'Route drag worsened',
          tone: 'negative',
          message: `Estimated route drag worsened from ${formatPrice(baselineRouteDrag)} to ${formatPrice(currentRouteDrag)}.`,
        })
      }
    }
  }

  if (!items.length) {
    return null
  }

  const tone =
    hasDeterioration && !hasImprovement
      ? 'negative'
      : hasDeterioration || hasMovement
        ? 'warning'
        : 'positive'

  return {
    tone,
    summary:
      hasDeterioration && !hasImprovement
        ? 'Execution conditions changed'
        : hasImprovement && !hasDeterioration && !hasMovement
          ? 'Execution conditions improved'
          : 'Review market changes',
    items,
  }
}

function buildSendConfidence({
  canOpenTrade,
  checklistIsComplete,
  routeComparison,
  executionReviewDrift,
  warningReasons,
  positionPreview,
  activePendingOrder,
  selectedChartPoint,
  capitalPreservationSummary,
  executionRouteSummary,
}) {
  const reviewOnlyMode = Boolean(capitalPreservationSummary?.reviewOnlyMode)
  if (reviewOnlyMode) {
    const resetLabel = capitalPreservationSummary?.reviewOnlyResetLabel || 'next regular session'
    return {
      tone: 'negative',
      locked: true,
      title: activePendingOrder ? 'Working order changes are locked' : 'New entries are locked',
      detail: activePendingOrder
        ? `${
            capitalPreservationSummary?.detail ||
            'The desk is in review-only mode until the next regular session.'
          } Cancel the working order or reduce risk instead of replacing or filling it.`
        : capitalPreservationSummary?.detail ||
          'The desk is in review-only mode until the next regular session.',
      facts: [
        {
          key: 'route',
          label: 'Route',
          value: activePendingOrder ? 'Cancel only' : 'Review only',
        },
        {
          key: 'review',
          label: 'Desk state',
          value: activePendingOrder ? 'No replacements' : 'No new entries',
        },
        {
          key: 'reset',
          label: 'Reset',
          value: resetLabel,
        },
      ],
    }
  }

  if (!canOpenTrade) return null

  const effectiveRisk = toNumber(positionPreview?.effectiveMaxRiskDollars)
  const routeNeedsCaution =
    routeComparison?.current?.tone === 'warning' || routeComparison?.current?.tone === 'negative'
  const hasWarnings = Array.isArray(warningReasons) && warningReasons.length > 0
  const reviewNeedsRefresh = Boolean(executionReviewDrift)
  const tone = reviewNeedsRefresh || hasWarnings || routeNeedsCaution || !checklistIsComplete ? 'warning' : 'positive'
  const title = activePendingOrder
    ? tone === 'positive'
      ? 'Replacement ticket is ready to route'
      : 'Replacement ticket can route, but review it once more'
    : selectedChartPoint
      ? tone === 'positive'
        ? 'Staged ticket is ready to send'
        : 'Staged ticket can send, but review it once more'
      : tone === 'positive'
        ? 'Live ticket is ready to send'
        : 'Live ticket can send, but review it once more'

  const detailParts = []
  if (checklistIsComplete) {
    detailParts.push('the execution checklist is clear')
  } else {
    detailParts.push('the execution checklist is still carrying review items')
  }
  if (executionRouteSummary?.label) {
    detailParts.push(`${executionRouteSummary.label.toLowerCase()} is selected`)
  }
  if (routeComparison?.current?.label) {
    detailParts.push(`${routeComparison.current.label} is the active route`)
  }
  if (reviewNeedsRefresh) {
    detailParts.push('market conditions changed since the last review')
  } else {
    detailParts.push('the review snapshot is current')
  }

  return {
    tone,
    locked: false,
    title,
    detail: `${detailParts[0].charAt(0).toUpperCase()}${detailParts[0].slice(1)}, ${detailParts.slice(1).join(', ')}.`,
    facts: [
      {
        key: 'execution',
        label: 'Execution',
        value: executionRouteSummary?.label || '--',
      },
      {
        key: 'route',
        label: 'Order route',
        value: routeComparison?.current?.label || '--',
      },
      {
        key: 'risk',
        label: 'Risk',
        value: effectiveRisk !== null ? formatPrice(effectiveRisk) : '--',
      },
      {
        key: 'review',
        label: 'Review',
        value: reviewNeedsRefresh ? 'Refresh needed' : tone === 'positive' ? 'Current' : 'Caution',
      },
    ],
  }
}

function buildActionConfirmation({
  activePendingOrder,
  selectedChartPoint,
  sendConfidence,
  actionConfirmArmed,
  executionRouteSummary,
}) {
  if (!sendConfidence) return null
  if (sendConfidence.locked) return null

  const tone = sendConfidence.tone === 'positive' ? 'positive' : 'warning'
  const mode = activePendingOrder ? 'replace' : selectedChartPoint ? 'staged' : 'live'
  const sendLabel = executionRouteSummary?.sendLabel || 'live order'

  const labelBase =
    mode === 'replace'
      ? 'working order replacement'
      : mode === 'staged'
        ? `staged ${sendLabel}`
        : sendLabel

  const title =
    mode === 'replace'
      ? tone === 'positive'
        ? 'Replace working order'
        : 'Replace working order with caution'
      : mode === 'staged'
        ? tone === 'positive'
          ? `Send staged ${sendLabel}`
          : `Send staged ${sendLabel} with caution`
        : tone === 'positive'
          ? `Send ${sendLabel}`
          : `Send ${sendLabel} with caution`

  return {
    tone,
    title,
    detail: actionConfirmArmed
      ? `Second click confirms this ${labelBase}. The rail will send the current route and risk settings exactly as shown above.`
      : `First click arms this ${labelBase}. Use the confirmation state to make sure the route, risk, and review signals still look right before the final send.`,
    buttonLabel: actionConfirmArmed
      ? mode === 'replace'
        ? 'Confirm replacement'
        : mode === 'staged'
          ? `Confirm staged ${sendLabel}`
          : `Confirm ${sendLabel}`
      : mode === 'replace'
        ? 'Arm replacement'
        : mode === 'staged'
          ? `Arm staged ${sendLabel}`
          : `Arm ${sendLabel}`,
    cancelLabel: actionConfirmArmed ? 'Keep editing' : null,
  }
}

function buildExecutionRouteSummary({
  executionIntent,
  promotionGateSummary,
  intradayExecutionPlan,
  profileTradingContext = null,
}) {
  if (profileTradingContext?.executionRouteOverride) {
    return profileTradingContext.executionRouteOverride
  }
  const normalizedIntent = String(executionIntent || 'desk').trim().toLowerCase() || 'desk'
  const gateLabel = String(promotionGateSummary?.label || 'paper gate review')
    .trim()
    .toLowerCase()
  const gateDetail =
    promotionGateSummary?.detail ||
    promotionGateSummary?.action ||
    'Paper stability still needs review before Alpaca live routing.'

  if (intradayExecutionPlan && intradayExecutionPlan.allowsNewEntries === false) {
    return {
      tone: 'negative',
      label: intradayExecutionPlan.cleanupOnly ? 'Session cleanup only' : 'Session locked',
      detail: intradayExecutionPlan.description,
      locked: true,
      sendLabel: 'intraday order',
      badgeLabel: 'Locked',
      pathLabel: intradayExecutionPlan.cleanupOnly ? 'Cleanup only' : 'Wait for session',
    }
  }

  if (normalizedIntent === 'broker_live') {
    if (promotionGateSummary?.allowsPromotion === false) {
      return {
        tone: 'negative',
        label: 'Alpaca live locked',
        detail: `${gateDetail} Switch to Alpaca paper or desk routing until ${gateLabel} clears.`,
        locked: true,
        sendLabel: 'Alpaca live order',
        badgeLabel: 'Locked',
        pathLabel: 'Alpaca live',
      }
    }

    return {
      tone: 'positive',
      label: 'Alpaca live',
      detail: intradayExecutionPlan
        ? `The ticket will route through Alpaca live because the paper gate is clear and approval gates remain visible. ${intradayExecutionPlan.routeDetail}`
        : 'The ticket will route through Alpaca live because the paper gate is clear and approval gates remain visible.',
      locked: false,
      sendLabel: 'Alpaca live order',
      badgeLabel: 'Live',
      pathLabel: 'Alpaca live',
    }
  }

  if (normalizedIntent === 'broker_paper') {
    return {
      tone: 'positive',
      label: 'Alpaca paper',
      detail: intradayExecutionPlan
        ? `The ticket will route through Alpaca paper execution so fills and lifecycle can be reconciled before live capital. ${intradayExecutionPlan.routeDetail}`
        : 'The ticket will route through Alpaca paper execution so fills and lifecycle can be reconciled before live capital.',
      locked: false,
      sendLabel: 'Alpaca paper order',
      badgeLabel: 'Paper',
      pathLabel: 'Alpaca paper',
    }
  }

  return {
    tone: 'info',
    label: 'Desk route',
    detail: intradayExecutionPlan
      ? `The ticket will stay on the local desk ledger without connected-account routing. ${intradayExecutionPlan.routeDetail}`
      : 'The ticket will stay on the local desk ledger without connected-account routing. Use this when you want the setup tracked locally before route handling.',
    locked: false,
    sendLabel: 'desk order',
    badgeLabel: 'Desk',
    pathLabel: 'Local ledger',
  }
}

function buildActionHistoryEntry({
  status,
  ticker,
  activePendingOrder,
  selectedChartPoint,
  sendConfidence,
  tradeTicket,
}) {
  const normalizedTicker = String(ticker || '').trim().toUpperCase()
  const mode = activePendingOrder ? 'replace' : selectedChartPoint ? 'staged' : 'live'
  const actionLabel =
    mode === 'replace'
      ? 'Replace'
      : mode === 'staged'
        ? 'Staged send'
        : 'Live send'

  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    ticker: normalizedTicker,
    status,
    tone: sendConfidence?.tone || 'info',
    label: `${actionLabel} ${status === 'armed' ? 'armed' : 'confirmed'}`,
    detail: `${formatInstrumentTypeLabel(tradeTicket.instrumentType)} ${formatOrderTypeLabel(tradeTicket.orderType)} | ${formatTimeInForceLabel(tradeTicket.timeInForce)}`,
    createdAt: new Date().toISOString(),
  }
}

function buildExecutionCostCards({
  instrumentType,
  optionStrategy,
  orderType,
  timeInForce,
  positionPreview,
  quote,
  contract,
  accountSize,
  livePrice,
}) {
  const normalizedInstrumentType = normalizeInstrumentType(instrumentType)
  const normalizedOptionStrategy = normalizeOptionStrategy(optionStrategy)
  const normalizedOrderType = String(orderType || 'market').trim().toLowerCase()
  const normalizedTimeInForce = String(timeInForce || 'day').trim().toLowerCase()
  const positionCost = toNumber(positionPreview?.totalPositionCost)
  const effectiveRisk = toNumber(positionPreview?.effectiveMaxRiskDollars)
  const accountSizeNumber = toNumber(accountSize)
  const accountUsagePct =
    positionCost !== null && accountSizeNumber !== null && accountSizeNumber > 0
      ? (positionCost / accountSizeNumber) * 100
      : null

  const {
    estimatedEntryDrag,
    spreadContext,
    spreadTone,
    spreadDetail,
  } = buildEntryDragProfile({
    instrumentType,
    positionPreview,
    quote,
    contract,
    livePrice,
  })
  const routeAdjustedEntryDrag = estimateRouteDrag(estimatedEntryDrag, {
    instrumentType,
    orderType,
    timeInForce,
  })

  let orderImpactValue = 'Balanced'
  let orderImpactTone = 'positive'
  let orderImpactDetail = 'The current routing instructions are balanced between speed and price control.'

  if (normalizedOrderType === 'market') {
    orderImpactValue = 'Fastest fill'
    orderImpactTone = 'negative'
    orderImpactDetail =
      estimatedEntryDrag !== null
        ? `Market routing prioritizes immediacy, so you should expect the current spread drag to matter. Switching to a limit order would trade speed for better price control.`
        : 'Market routing prioritizes immediacy, but you still need a clean book because there is no price cap.'
  } else if (normalizedOrderType === 'limit') {
    orderImpactValue = 'Price capped'
    orderImpactTone = 'positive'
    orderImpactDetail =
      normalizedTimeInForce === 'gtc_90d'
        ? 'A resting limit order can preserve price discipline, but it needs periodic review as liquidity and catalysts change.'
        : 'A limit order caps the entry price and is the cleanest way to reduce spread drag when the book is wide.'
  } else if (normalizedOrderType === 'stop_market') {
    orderImpactValue = 'Trigger then market'
    orderImpactTone = 'warning'
    orderImpactDetail =
      'Once the stop triggers, this behaves like a market order. It protects the trigger logic, not the final fill price.'
  } else if (normalizedOrderType === 'stop_limit') {
    orderImpactValue = 'Trigger + cap'
    orderImpactTone = 'positive'
    orderImpactDetail =
      'A stop-limit preserves both the trigger and a price cap, but it can miss the fill if price moves through the limit too quickly.'
  } else if (normalizedOrderType === 'trailing_stop') {
    orderImpactValue = 'Reactive trigger'
    orderImpactTone = 'warning'
    orderImpactDetail =
      'Trailing logic adapts as price moves, but once it triggers the fill quality still depends on the live book and can react badly to quote noise.'
  }

  const capitalTone =
    accountUsagePct === null
      ? 'warning'
      : accountUsagePct <= 20
        ? 'positive'
        : accountUsagePct <= 50
          ? 'warning'
          : 'negative'

  return [
    {
      key: 'entry-drag',
      title: 'Entry drag',
      value: routeAdjustedEntryDrag !== null ? formatPrice(routeAdjustedEntryDrag) : spreadContext,
      tone: spreadTone,
      detail: `${
        routeAdjustedEntryDrag !== null
          ? `Current route likely exposes about ${formatPrice(routeAdjustedEntryDrag)} of entry drag.`
          : spreadDetail
      } ${
        estimatedEntryDrag !== null && routeAdjustedEntryDrag !== null && Math.abs(routeAdjustedEntryDrag - estimatedEntryDrag) > 0.01
          ? `Full book-crossing baseline is ${formatPrice(estimatedEntryDrag)}.`
          : ''
      } ${spreadContext !== '--' ? `Context: ${spreadContext}.` : ''}`.trim(),
    },
    {
      key: 'capital-usage',
      title: 'Capital usage',
      value:
        accountUsagePct !== null
          ? `${formatPercent(accountUsagePct, 1)} used`
          : positionCost !== null
            ? formatPrice(positionCost)
            : '--',
      tone: capitalTone,
      detail:
        positionCost !== null
          ? `${formatPrice(positionCost)} of capital is committed to this ticket, with effective risk near ${formatPrice(effectiveRisk)} if the invalidation is hit.`
          : 'Sizing needs a valid position preview before capital usage can be estimated.',
    },
    {
      key: 'cost-structure',
      title: 'Cost structure',
      value:
        normalizedInstrumentType === 'listed_option'
          ? normalizedOptionStrategy === 'short_premium'
            ? 'Premium credit'
            : normalizedOptionStrategy === 'vertical_spread'
              ? 'Net spread'
              : 'Premium debit'
          : 'Cash notional',
      tone:
        normalizedInstrumentType === 'listed_option' && normalizedOptionStrategy === 'long_option'
          ? 'positive'
          : 'warning',
      detail:
        normalizedInstrumentType === 'listed_option'
          ? normalizedOptionStrategy === 'short_premium'
            ? 'Short premium needs margin, assignment, and buy-to-close controls before it can be routed from this ticket.'
            : normalizedOptionStrategy === 'vertical_spread'
              ? 'A vertical spread needs both legs, width, and net debit or credit validation before submit.'
              : 'This is a defined-risk premium outlay. The main execution risk is poor contract liquidity rather than full-stock notional exposure.'
          : 'This is linear cash exposure. Notional size and gap risk matter immediately, even if the setup uses a tight invalidation.'
    },
    {
      key: 'order-impact',
      title: 'Order impact',
      value: orderImpactValue,
      tone: orderImpactTone,
      detail: orderImpactDetail,
    },
  ]
}

function clampExecutionScore(value) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return 0
  return Math.max(0, Math.min(100, Math.round(numeric)))
}

function executionScoreTone(score) {
  if (score >= 78) return 'positive'
  if (score >= 55) return 'warning'
  return 'negative'
}

function buildLiquidityExecutionWarnings({
  instrumentType,
  optionStrategy,
  orderType,
  timeInForce,
  positionPreview,
  quote,
  contract,
  livePrice,
}) {
  const normalizedInstrumentType = normalizeInstrumentType(instrumentType)
  const normalizedOptionStrategy = normalizeOptionStrategy(optionStrategy)
  const normalizedOrderType = String(orderType || 'market').trim().toLowerCase()
  const normalizedTimeInForce = String(timeInForce || 'day').trim().toLowerCase()
  const units = toNumber(positionPreview?.suggestedContracts)
  const effectiveRisk = toNumber(positionPreview?.effectiveMaxRiskDollars)
  const { estimatedEntryDrag, spreadContext } = buildEntryDragProfile({
    instrumentType,
    positionPreview,
    quote,
    contract,
    livePrice,
  })
  const routeAdjustedEntryDrag = estimateRouteDrag(estimatedEntryDrag, {
    instrumentType,
    orderType,
    timeInForce,
  })
  const dragPctOfRisk =
    routeAdjustedEntryDrag !== null && effectiveRisk !== null && effectiveRisk > 0
      ? (routeAdjustedEntryDrag / effectiveRisk) * 100
      : null

  let score = 100
  let spreadValue = spreadContext
  let spreadTone = 'warning'
  let spreadDetail = 'Waiting for enough quote detail to confirm spread quality.'
  let liquidityValue = 'Liquidity pending'
  let liquidityTone = 'warning'
  let liquidityDetail = 'Waiting for displayed participation before trusting fill quality.'

  if (normalizedInstrumentType === 'listed_option') {
    const spreadPct = toNumber(contract?.spread_pct)
    const volume = toNumber(contract?.volume)
    const openInterest = toNumber(contract?.open_interest)

    if (spreadPct === null) score -= 28
    else if (spreadPct > 20) score -= 55
    else if (spreadPct > 12) score -= 35
    else if (spreadPct > 6) score -= 15

    if (volume === null) score -= 10
    else if (volume < 25) score -= 28
    else if (volume < 100) score -= 14

    if (openInterest === null) score -= 10
    else if (openInterest < 100) score -= 28
    else if (openInterest < 500) score -= 14

    spreadValue = spreadPct !== null ? `${formatPercent(spreadPct, 1)} contract spread` : 'Contract quote pending'
    spreadTone =
      spreadPct === null ? 'warning' : spreadPct <= 6 ? 'positive' : spreadPct <= 12 ? 'warning' : 'negative'
    spreadDetail =
      spreadPct === null
        ? 'The option route needs a live bid/ask before spread drag can be trusted.'
        : spreadPct <= 6
          ? 'The contract spread is tight enough that a priced route can preserve most of the setup edge.'
          : spreadPct <= 12
            ? 'The contract spread is usable, but marketable routing can still consume a visible share of the edge.'
            : 'The contract spread is wide enough that fill quality can dominate the trade outcome.'

    liquidityValue =
      volume !== null || openInterest !== null
        ? `Vol ${formatCompact(volume)} | OI ${formatCompact(openInterest)}`
        : 'Vol / OI pending'
    liquidityTone =
      volume === null && openInterest === null
        ? 'warning'
        : volume !== null && volume >= 100 && openInterest !== null && openInterest >= 500
          ? 'positive'
          : volume !== null && volume >= 25 && openInterest !== null && openInterest >= 100
            ? 'warning'
            : 'negative'
    liquidityDetail =
      normalizedOptionStrategy === 'long_option'
        ? 'Long-option fills still need both current volume and open interest so entry and exit are not dominated by the spread.'
        : 'Complex or short-premium option structures need stronger participation because exit liquidity, assignment pressure, and margin can change quickly.'
  } else {
    const bid = toNumber(quote?.bid_price)
    const ask = toNumber(quote?.ask_price)
    const bidSize = toNumber(quote?.bid_size)
    const askSize = toNumber(quote?.ask_size)
    const rawSpread = resolveDisplaySpread(quote?.spread, bid, ask)
    const referencePrice = toNumber(livePrice) ?? (bid !== null && ask !== null ? (bid + ask) / 2 : null)
    const spreadPct =
      rawSpread !== null && referencePrice !== null && referencePrice > 0
        ? (rawSpread / referencePrice) * 100
        : null
    const displayedDepth = (bidSize || 0) + (askSize || 0)

    if (spreadPct === null) score -= 25
    else if (spreadPct > 0.5) score -= 45
    else if (spreadPct > 0.15) score -= 25
    else if (spreadPct > 0.05) score -= 10

    if (bidSize === null && askSize === null) score -= 12
    else if (displayedDepth < 500) score -= 24
    else if (displayedDepth < 2000) score -= 10

    spreadValue = rawSpread !== null ? `${formatPrice(rawSpread)} spread` : 'Live quote pending'
    spreadTone =
      spreadPct === null ? 'warning' : spreadPct <= 0.05 ? 'positive' : spreadPct <= 0.15 ? 'warning' : 'negative'
    spreadDetail =
      rawSpread === null
        ? 'The equity route needs a firm bid/ask before spread drag can be trusted.'
        : `The spread is ${formatPercent(spreadPct, 2)} of price. Wider spreads make marketable stock orders more sensitive to timing.`

    liquidityValue =
      bidSize !== null || askSize !== null
        ? `${formatCompact(bidSize)} x ${formatCompact(askSize)}`
        : 'Sizes pending'
    liquidityTone =
      bidSize === null && askSize === null
        ? 'warning'
        : displayedDepth >= 2000
          ? 'positive'
          : displayedDepth >= 500
            ? 'warning'
            : 'negative'
    liquidityDetail =
      units !== null
        ? `Suggested size is ${formatShares(units)} share${units === 1 ? '' : 's'} against the displayed top-of-book depth.`
        : 'Displayed top-of-book depth is the first check before trusting a marketable equity route.'
  }

  if (normalizedOrderType === 'market') score -= 14
  if (normalizedOrderType === 'stop_market') score -= 12
  if (normalizedOrderType === 'trailing_stop') score -= 8
  if (normalizedTimeInForce === 'day_ext') score -= normalizedInstrumentType === 'listed_option' ? 35 : 12
  if (normalizedInstrumentType === 'listed_option' && normalizedOrderType === 'trailing_stop') score -= 15
  if (dragPctOfRisk !== null) {
    if (dragPctOfRisk > 10) score -= 25
    else if (dragPctOfRisk > 5) score -= 15
    else if (dragPctOfRisk > 2) score -= 6
  }

  const finalScore = clampExecutionScore(score)
  const summaryTone = executionScoreTone(finalScore)
  const routeTone =
    normalizedInstrumentType === 'listed_option' && normalizedTimeInForce === 'day_ext'
      ? 'negative'
      : normalizedOrderType === 'market' || normalizedOrderType === 'stop_market' || normalizedOrderType === 'trailing_stop'
        ? 'warning'
        : 'positive'
  const routeValue = `${formatOrderTypeLabel(normalizedOrderType)} | ${formatTimeInForceLabel(normalizedTimeInForce)}`
  const routeDetail =
    normalizedInstrumentType === 'listed_option' && normalizedTimeInForce === 'day_ext'
      ? 'Listed options should stay on regular-hours routing here because after-hours option liquidity is not reliable enough for this desk.'
      : normalizedOrderType === 'market'
        ? 'Market routing is fastest, but it gives up price protection and should only be used when spread and depth are clean.'
        : normalizedOrderType === 'stop_market'
          ? 'Stop-market protects the trigger, not the final fill. A stop-limit is safer when liquidity is thin.'
          : normalizedOrderType === 'trailing_stop'
            ? 'Trailing stops can react to quote noise, so they deserve extra caution on thin books.'
            : 'Price-controlled routing is the preferred default when liquidity or spread drag is uncertain.'
  const slippageTone =
    routeAdjustedEntryDrag === null
      ? 'warning'
      : dragPctOfRisk !== null && dragPctOfRisk > 5
        ? 'negative'
        : dragPctOfRisk !== null && dragPctOfRisk > 2
          ? 'warning'
          : 'positive'
  const slippageValue =
    routeAdjustedEntryDrag !== null
      ? formatPrice(routeAdjustedEntryDrag)
      : estimatedEntryDrag !== null
        ? formatPrice(estimatedEntryDrag)
        : 'Pending'
  const slippageDetail =
    routeAdjustedEntryDrag !== null
      ? `Estimated route-adjusted entry drag is ${
          dragPctOfRisk !== null ? `${formatPercent(dragPctOfRisk, 1)} of effective risk` : 'not yet mapped to effective risk'
        }.`
      : 'Spread and size need to resolve before route-adjusted slippage can be estimated.'

  return {
    score: finalScore,
    label:
      finalScore >= 78
        ? 'Execution clean'
        : finalScore >= 55
          ? 'Use price control'
          : 'Fragile fills',
    tone: summaryTone,
    cards: [
      {
        key: 'fill-quality',
        title: 'Fill quality',
        value: `${finalScore}/100`,
        tone: summaryTone,
        detail:
          finalScore >= 78
            ? 'Spread, liquidity, and route choice look supportive enough for a normal ticket review.'
            : finalScore >= 55
              ? 'The setup is workable, but a priced route and smaller participation are the safer defaults.'
              : 'Execution risk is high enough that the forecast edge may not survive a poor fill.',
      },
      {
        key: 'spread',
        title: 'Spread warning',
        value: spreadValue,
        tone: spreadTone,
        detail: spreadDetail,
      },
      {
        key: 'liquidity',
        title: 'Displayed liquidity',
        value: liquidityValue,
        tone: liquidityTone,
        detail: liquidityDetail,
      },
      {
        key: 'slippage',
        title: 'Slippage estimate',
        value: slippageValue,
        tone: slippageTone,
        detail: slippageDetail,
      },
      {
        key: 'route',
        title: 'Route warning',
        value: routeValue,
        tone: routeTone,
        detail: routeDetail,
      },
    ],
  }
}

function getContractGreek(contract, key) {
  const direct = toNumber(contract?.[key])
  if (direct !== null) return direct
  return toNumber(contract?.greeks?.[key])
}

function formatGreekValue(value, digits = 2) {
  const numeric = toNumber(value)
  return numeric === null ? '--' : formatNumber(numeric, digits)
}

function buildPreTradeRiskPanel({
  report,
  instrumentType,
  optionStrategy,
  optionRight,
  positionPreview,
  contract,
  livePrice,
  riskReward,
}) {
  const normalizedInstrumentType = normalizeInstrumentType(instrumentType)
  const normalizedOptionStrategy = normalizeOptionStrategy(optionStrategy)
  const normalizedOptionRight = String(optionRight || '').trim().toLowerCase() === 'put' ? 'put' : 'call'
  const optionPlan = report?.option_plan || {}
  const units = toNumber(positionPreview?.suggestedContracts)
  const entryUnitPrice = toNumber(positionPreview?.entryUnitPrice ?? livePrice)
  const positionCost = toNumber(positionPreview?.totalPositionCost)
  const plannedMaxLoss = toNumber(positionPreview?.totalMaxLoss)
  const effectiveRisk = toNumber(positionPreview?.effectiveMaxRiskDollars)
  const accountRisk = plannedMaxLoss ?? effectiveRisk
  const plannedStopLoss = plannedMaxLoss ?? effectiveRisk
  const targetPrice = toNumber(optionPlan.expected_underlying_target)
  const invalidationPrice = toNumber(optionPlan.invalidation_price)
  const strike = toNumber(contract?.strike)
  const premium = toNumber(contract?.mid ?? positionPreview?.entryUnitPrice)
  const dte = daysUntilExpiration(contract?.expiration)
  const multiplier = normalizedInstrumentType === 'listed_option' ? 100 : 1
  const theoreticalPremiumLoss =
    normalizedInstrumentType === 'listed_option' && positionCost !== null ? positionCost : null
  const estimatedMaxProfit =
    accountRisk !== null && riskReward !== null && Number.isFinite(riskReward)
      ? accountRisk * riskReward
      : null

  let maxLossValue = accountRisk !== null ? formatPrice(accountRisk) : '--'
  let maxLossTone = accountRisk !== null ? 'positive' : 'warning'
  let maxLossDetail =
    accountRisk !== null
      ? `Planned loss is mapped from the ticket size and stop rule. Account risk budget is ${formatPrice(effectiveRisk)}.`
      : 'The ticket needs a valid size and invalidation before max loss can be mapped.'

  let maxProfitValue = estimatedMaxProfit !== null ? formatPrice(estimatedMaxProfit) : '--'
  let maxProfitTone = estimatedMaxProfit !== null && riskReward !== null && riskReward >= 2 ? 'positive' : 'warning'
  let maxProfitDetail =
    estimatedMaxProfit !== null
      ? `Mapped from the current ${formatNumber(riskReward, 2)}R target. This is a planned exit estimate, not a guaranteed fill.`
      : 'Profit potential needs a target and a valid risk/reward map.'

  let breakevenValue = entryUnitPrice !== null ? formatPrice(entryUnitPrice) : '--'
  let breakevenTone = 'neutral'
  let breakevenDetail = 'Equity breakeven is the entry price before commissions and slippage.'
  let marginValue = positionCost !== null ? formatPrice(positionCost) : '--'
  let marginTone = 'warning'
  let marginDetail =
    normalizedInstrumentType === 'equity'
      ? 'Cash equity exposure consumes notional directly and remains linear until exit.'
      : 'Margin impact depends on option structure and broker treatment.'
  let assignmentValue = 'Not applicable'
  let assignmentTone = 'positive'
  let assignmentDetail = 'Stock tickets do not carry option exercise or assignment mechanics.'

  if (normalizedInstrumentType === 'listed_option') {
    const longBreakEven =
      strike !== null && premium !== null
        ? normalizedOptionRight === 'put'
          ? strike - premium
          : strike + premium
        : null

    if (normalizedOptionStrategy === 'long_option') {
      maxLossValue =
        theoreticalPremiumLoss !== null
          ? `${formatPrice(plannedStopLoss)} planned | ${formatPrice(theoreticalPremiumLoss)} max`
          : formatPrice(plannedStopLoss)
      maxLossTone = theoreticalPremiumLoss !== null ? 'positive' : 'warning'
      maxLossDetail =
        theoreticalPremiumLoss !== null
          ? `The stop-risk plan is ${formatPrice(plannedStopLoss)}, while the theoretical worst case is the full premium debit of ${formatPrice(theoreticalPremiumLoss)}.`
          : 'Long options are capped at premium paid, but this contract needs a valid premium before full max loss is mapped.'
      maxProfitValue = estimatedMaxProfit !== null ? formatPrice(estimatedMaxProfit) : 'Exit-defined'
      maxProfitDetail =
        estimatedMaxProfit !== null
          ? `Mapped from the underlying target and current risk/reward. The option payoff remains path, IV, and time sensitive.`
          : 'Theoretical option profit is path-dependent; use the planned target and exit rules rather than a static payoff.'
      breakevenValue = longBreakEven !== null ? formatPrice(longBreakEven) : '--'
      breakevenTone = longBreakEven !== null ? 'positive' : 'warning'
      breakevenDetail =
        longBreakEven !== null
          ? `${normalizedOptionRight.toUpperCase()} breakeven is strike ${formatPrice(strike)} ${normalizedOptionRight === 'put' ? '-' : '+'} premium ${formatPrice(premium)}.`
          : 'Breakeven needs strike and premium.'
      marginValue = theoreticalPremiumLoss !== null ? formatPrice(theoreticalPremiumLoss) : '--'
      marginTone = theoreticalPremiumLoss !== null ? 'positive' : 'warning'
      marginDetail = `Long option buying power is the premium debit. Each contract controls ${multiplier} shares.`
      assignmentValue = dte === null ? 'Expiry pending' : dte <= 2 ? `${dte} DTE` : 'Exercise only'
      assignmentTone = dte !== null && dte <= 2 ? 'warning' : 'positive'
      assignmentDetail =
        dte !== null && dte <= 2
          ? 'Near-expiry long options can require exercise decisions and have unstable gamma and spreads.'
          : 'Long options do not create short-assignment risk, but expiry and exercise still need review.'
    } else if (normalizedOptionStrategy === 'short_premium') {
      const credit = positionCost
      const putWorstCase =
        normalizedOptionRight === 'put' && strike !== null && premium !== null && units !== null
          ? Math.max(0, (strike - premium) * multiplier * units)
          : null
      maxLossValue =
        normalizedOptionRight === 'call'
          ? 'Undefined'
          : putWorstCase !== null
            ? formatPrice(putWorstCase)
            : 'Margin-defined'
      maxLossTone = 'negative'
      maxLossDetail =
        normalizedOptionRight === 'call'
          ? 'A naked short call has undefined upside loss. This ticket remains review-only.'
          : 'A short put worst case is strike minus premium, multiplied by contracts and contract multiplier. Broker margin may be lower but risk is larger.'
      maxProfitValue = credit !== null ? formatPrice(credit) : 'Premium credit'
      maxProfitTone = 'warning'
      maxProfitDetail = 'Short premium max profit is capped at the credit received before fees and closing costs.'
      breakevenValue = longBreakEven !== null ? formatPrice(longBreakEven) : '--'
      breakevenTone = longBreakEven !== null ? 'warning' : 'negative'
      breakevenDetail =
        longBreakEven !== null
          ? `Short ${normalizedOptionRight.toUpperCase()} breakeven is strike ${formatPrice(strike)} ${normalizedOptionRight === 'put' ? '-' : '+'} credit ${formatPrice(premium)}.`
          : 'Breakeven needs strike and premium.'
      marginValue = 'Broker margin'
      marginTone = 'negative'
      marginDetail = 'Short premium is blocked until margin expansion, assignment, and buy-to-close rules are modeled.'
      assignmentValue = 'Assignment risk'
      assignmentTone = 'negative'
      assignmentDetail = 'Short options can be assigned before expiry and need explicit broker-margin controls.'
    } else if (normalizedOptionStrategy === 'vertical_spread') {
      maxLossValue = 'Needs legs'
      maxLossTone = 'warning'
      maxLossDetail = 'Vertical spread max loss needs both strikes, net debit or credit, and width.'
      maxProfitValue = 'Needs legs'
      maxProfitTone = 'warning'
      maxProfitDetail = 'Vertical spread max profit cannot be calculated from a single recommended contract.'
      breakevenValue = 'Needs legs'
      breakevenTone = 'warning'
      breakevenDetail = 'Spread breakeven needs the second leg and net premium.'
      marginValue = 'Net width'
      marginTone = 'warning'
      marginDetail = 'Defined-risk spread margin is based on width and net premium once both legs are known.'
      assignmentValue = dte === null ? 'Review expiry' : `${dte} DTE`
      assignmentTone = 'warning'
      assignmentDetail = 'Multi-leg options still carry early assignment and pin-risk behavior near expiry.'
    }
  } else if (targetPrice !== null && invalidationPrice !== null && units !== null) {
    maxProfitValue = estimatedMaxProfit !== null ? formatPrice(estimatedMaxProfit) : '--'
    maxProfitDetail = `Target ${formatPrice(targetPrice)} and invalidation ${formatPrice(invalidationPrice)} define the planned risk/reward map.`
    marginValue = positionCost !== null ? formatPrice(positionCost) : '--'
    marginTone = positionCost !== null ? 'warning' : 'negative'
  }

  const delta = getContractGreek(contract, 'delta')
  const gamma = getContractGreek(contract, 'gamma')
  const theta = getContractGreek(contract, 'theta')
  const vega = getContractGreek(contract, 'vega')
  const iv = getContractGreek(contract, 'implied_volatility') ?? getContractGreek(contract, 'iv')
  const netDelta =
    normalizedInstrumentType === 'listed_option' && delta !== null && units !== null
      ? delta * units * multiplier * (normalizedOptionStrategy === 'short_premium' ? -1 : 1)
      : normalizedInstrumentType === 'equity' && units !== null
        ? units
        : null
  const greekAvailable =
    normalizedInstrumentType === 'equity' || [delta, gamma, theta, vega, iv].some((value) => value !== null)

  return [
    {
      key: 'max-loss',
      title: 'Max loss',
      value: maxLossValue,
      tone: maxLossTone,
      detail: maxLossDetail,
    },
    {
      key: 'max-profit',
      title: 'Max profit',
      value: maxProfitValue,
      tone: maxProfitTone,
      detail: maxProfitDetail,
    },
    {
      key: 'breakeven',
      title: 'Breakeven',
      value: breakevenValue,
      tone: breakevenTone,
      detail: breakevenDetail,
    },
    {
      key: 'margin-impact',
      title: 'Margin impact',
      value: marginValue,
      tone: marginTone,
      detail: marginDetail,
    },
    {
      key: 'greeks',
      title: normalizedInstrumentType === 'equity' ? 'Directional exposure' : 'Greeks',
      value:
        netDelta !== null
          ? `${formatNumber(netDelta, normalizedInstrumentType === 'equity' ? 2 : 1)} delta`
          : greekAvailable
            ? 'Partial'
            : 'Pending',
      tone: greekAvailable ? 'positive' : 'warning',
      detail:
        normalizedInstrumentType === 'equity'
          ? `Equity delta is linear at roughly ${formatShares(units)} share${units === 1 ? '' : 's'} before borrow or leverage effects.`
          : `Delta ${formatGreekValue(delta)} | gamma ${formatGreekValue(gamma, 4)} | theta ${formatGreekValue(theta)} | vega ${formatGreekValue(vega)}${iv !== null ? ` | IV ${formatPercent(iv * 100, 1)}` : ''}.`,
    },
    {
      key: 'assignment',
      title: 'Exercise / assignment',
      value: assignmentValue,
      tone: assignmentTone,
      detail: assignmentDetail,
    },
  ]
}

function buildRouteComparison({
  instrumentType,
  orderType,
  timeInForce,
  positionPreview,
  quote,
  contract,
  livePrice,
}) {
  const normalizedInstrumentType = normalizeInstrumentType(instrumentType)
  const normalizedOrderType = String(orderType || 'market').trim().toLowerCase()
  const normalizedTimeInForce = String(timeInForce || 'day').trim().toLowerCase()
  const { estimatedEntryDrag, spreadContext } = buildEntryDragProfile({
    instrumentType,
    positionPreview,
    quote,
    contract,
    livePrice,
  })
  const currentRouteDrag = estimateRouteDrag(estimatedEntryDrag, {
    instrumentType,
    orderType: normalizedOrderType,
    timeInForce: normalizedTimeInForce,
  })

  let currentTone = 'positive'
  let currentDetail = 'The current route is already using a price-controlled path for this setup.'

  if (normalizedInstrumentType === 'listed_option' && normalizedTimeInForce === 'day_ext') {
    currentTone = 'negative'
    currentDetail =
      'Listed options should stay in regular hours here because after-hours option liquidity is too inconsistent.'
  } else if (normalizedTimeInForce === 'day_ext' && normalizedOrderType === 'market') {
    currentTone = 'negative'
    currentDetail =
      'Extended-hours market routing is the most execution-sensitive combination because it gives up both time and price control.'
  } else if (normalizedOrderType === 'market') {
    currentTone = 'warning'
    currentDetail =
      estimatedEntryDrag !== null
        ? `Market routing prioritizes immediacy, so the current spread drag estimate of ${formatPrice(estimatedEntryDrag)} matters.`
        : 'Market routing prioritizes immediacy, so a clean book matters because there is no price cap.'
  } else if (normalizedOrderType === 'stop_market') {
    currentTone = 'warning'
    currentDetail =
      'Stop-market protects the trigger condition, but once it fires the final fill price still depends on the live book.'
  } else if (normalizedInstrumentType === 'listed_option' && normalizedOrderType === 'trailing_stop') {
    currentTone = 'warning'
    currentDetail =
      'Trailing-stop logic on listed options can react to quote noise, so it is less stable than a fixed priced route.'
  }

  let alternativeOrderType = normalizedOrderType
  let alternativeTimeInForce = normalizedTimeInForce
  const improvements = []
  const tradeoffs = []

  if (normalizedInstrumentType === 'listed_option' && normalizedTimeInForce === 'day_ext') {
    alternativeTimeInForce = 'day'
    improvements.push('keeps the order in regular-hours option liquidity')
    tradeoffs.push('gives up after-hours participation')
  }

  if (normalizedOrderType === 'market') {
    alternativeOrderType = 'limit'
    improvements.push('caps the entry price instead of crossing the book unchecked')
    tradeoffs.push('can miss the fill if price moves away')
  } else if (normalizedOrderType === 'stop_market') {
    alternativeOrderType = 'stop_limit'
    improvements.push('keeps the trigger while adding a price ceiling')
    tradeoffs.push('can miss the fill if price gaps through the limit')
  } else if (normalizedInstrumentType === 'listed_option' && normalizedOrderType === 'trailing_stop') {
    alternativeOrderType = 'limit'
    improvements.push('removes quote-noise-sensitive trailing behavior')
    tradeoffs.push('loses the auto-following stop logic')
  }

  const hasAlternative =
    alternativeOrderType !== normalizedOrderType || alternativeTimeInForce !== normalizedTimeInForce
  const alternativeRouteDrag = estimateRouteDrag(estimatedEntryDrag, {
    instrumentType,
    orderType: alternativeOrderType,
    timeInForce: alternativeTimeInForce,
  })

  const alternativeDetail = hasAlternative
    ? `${improvements.length ? `${improvements.join(', ')}.` : 'This route uses more price control.'} ${
        tradeoffs.length ? `Tradeoff: ${tradeoffs.join(', ')}.` : ''
      } ${
        currentRouteDrag !== null && alternativeRouteDrag !== null
          ? `Estimated drag shifts from ${formatPrice(currentRouteDrag)} to ${formatPrice(alternativeRouteDrag)} on the current book.`
          : spreadContext !== '--'
            ? `Current spread context: ${spreadContext}.`
            : ''
      }`.trim()
    : 'The current route already uses the more conservative combination for this setup, so there is no safer default switch to apply automatically.'

  return {
    summaryLabel: hasAlternative ? 'Safer route available' : 'Route already disciplined',
    current: {
      label: `${formatOrderTypeLabel(normalizedOrderType)} | ${formatTimeInForceLabel(normalizedTimeInForce)}`,
      tone: currentTone,
      detail: `${
        currentRouteDrag !== null ? `${currentDetail} Estimated route drag is ${formatPrice(currentRouteDrag)}.` : currentDetail
      }`,
    },
    alternative: {
      orderType: alternativeOrderType,
      timeInForce: alternativeTimeInForce,
      label: hasAlternative
        ? `${formatOrderTypeLabel(alternativeOrderType)} | ${formatTimeInForceLabel(alternativeTimeInForce)}`
        : 'Keep current route',
      tone: hasAlternative ? 'positive' : 'info',
      detail: alternativeDetail,
      actionLabel: hasAlternative ? 'Use safer route' : 'Already using safer route',
    },
    hasAlternative,
  }
}

function buildMarketStructureCards({
  instrumentType,
  quote,
  contract,
  report,
  eventContext,
  regimeStrengthScore,
  chartFreshness,
  sessionLabel,
  extendedHours,
  venueLabel,
  routeComparison,
}) {
  const normalizedInstrumentType = normalizeInstrumentType(instrumentType)
  const sessionValue = extendedHours ? `${sessionLabel} + EXT` : sessionLabel
  const sessionTone =
    sessionLabel === 'Regular'
      ? 'positive'
      : sessionLabel === 'Premarket' || sessionLabel === 'After-hours'
        ? 'warning'
        : 'info'
  const sessionDetail =
    sessionLabel === 'Regular'
      ? 'Primary liquidity window. Price discovery and fill quality are usually strongest here.'
      : sessionLabel === 'Premarket'
        ? 'Premarket trading can be thinner, so spreads and route sensitivity usually increase before the opening auction.'
        : sessionLabel === 'After-hours'
          ? 'After-hours trading often has less depth and wider spreads, so use more price control than you would during regular hours.'
          : 'Outside the active session, the desk should assume thinner liquidity and more fragile fills.'

  let quoteValue = 'Quote waiting'
  let quoteTone = 'warning'
  let quoteDetail = 'Waiting for enough live quote detail to score spread quality.'

  let participationValue = 'Book waiting'
  let participationTone = 'warning'
  let participationDetail = 'Waiting for enough live size detail to judge the resting book.'
  const activeEventContext = resolveEventContext(eventContext, report)
  const eventValue = eventContextStatus(activeEventContext)
  const eventTone = eventContextTone(activeEventContext)
  const eventDetail = [
    eventContextDetail(activeEventContext),
    String(activeEventContext?.trade_posture || '').trim().toLowerCase() !== 'clear'
      ? `Trade posture: ${formatLabel(activeEventContext?.trade_posture || 'caution')}.`
      : '',
    eventContextNextLabel(activeEventContext),
  ]
    .filter(Boolean)
    .join(' ')
  const regimeValue = formatLabel(report?.market_regime || 'Unknown')
  const regimeTone =
    regimeStrengthScore === null
      ? 'info'
      : regimeStrengthScore >= 0.65
        ? 'positive'
        : regimeStrengthScore >= 0.45
          ? 'warning'
          : 'negative'
  const regimeDetail =
    regimeStrengthScore === null
      ? 'Regime strength is waiting on enough live context before it can guide sizing and trust.'
      : `Current regime strength is ${formatRatioPercent(regimeStrengthScore, 1)}. Lower scores mean the setup should be treated as more fragile live.`
  const freshnessStatus = String(chartFreshness?.status || '').trim().toLowerCase()
  const freshnessValue = chartFreshness?.status ? formatLabel(chartFreshness.status) : 'Unknown'
  const freshnessTone =
    freshnessStatus === 'fresh'
      ? 'positive'
      : freshnessStatus === 'stale'
        ? 'warning'
        : 'info'
  const freshnessDetail =
    chartFreshness?.message ||
    'Freshness context is unavailable, so the desk should assume the live tape may lag the intended decision window.'

  if (normalizedInstrumentType === 'listed_option') {
    const spreadPct = toNumber(contract?.spread_pct)
    const volume = toNumber(contract?.volume)
    const openInterest = toNumber(contract?.open_interest)

    quoteValue = spreadPct !== null ? `${formatPercent(spreadPct, 1)} spread` : 'Contract quote pending'
    quoteTone =
      spreadPct === null ? 'warning' : spreadPct <= 6 ? 'positive' : spreadPct <= 12 ? 'warning' : 'negative'
    quoteDetail =
      spreadPct === null
        ? 'The recommended contract still needs a stable quoted spread before route quality can be judged confidently.'
        : spreadPct <= 6
          ? 'Contract spread is inside a tradable range for a listed option ticket.'
          : spreadPct <= 12
            ? 'Contract spread is usable but wide enough that price control should matter.'
            : 'Contract spread is structurally wide, so execution quality is fragile even if the directional setup looks good.'

    participationValue =
      volume !== null || openInterest !== null
        ? `Vol ${formatCompact(volume)} | OI ${formatCompact(openInterest)}`
        : 'Vol / OI pending'
    participationTone =
      volume === null && openInterest === null
        ? 'warning'
        : volume !== null && volume >= 100 && openInterest !== null && openInterest >= 500
          ? 'positive'
          : volume !== null && volume >= 25 && openInterest !== null && openInterest >= 100
            ? 'warning'
            : 'negative'
    participationDetail =
      volume === null && openInterest === null
        ? 'Waiting for contract participation data to confirm whether the spread is backed by real liquidity.'
        : 'Listed option tickets should favor contracts with both live volume and enough open interest to support cleaner fills and exits.'
  } else {
    const bid = toNumber(quote?.bid_price)
    const ask = toNumber(quote?.ask_price)
    const bidSize = toNumber(quote?.bid_size)
    const askSize = toNumber(quote?.ask_size)
    const rawSpread = resolveDisplaySpread(quote?.spread, bid, ask)
    const midPrice = bid !== null && ask !== null ? (bid + ask) / 2 : null
    const spreadPct = rawSpread !== null && midPrice !== null && midPrice > 0 ? (rawSpread / midPrice) * 100 : null

    quoteValue = rawSpread !== null ? `${formatPrice(rawSpread)} spread` : 'Quote waiting'
    quoteTone =
      spreadPct === null ? 'warning' : spreadPct <= 0.05 ? 'positive' : spreadPct <= 0.15 ? 'warning' : 'negative'
    quoteDetail =
      rawSpread === null
        ? 'The desk is waiting for a firm bid and ask before spread quality can be scored.'
        : `Bid ${formatPrice(bid)} x Ask ${formatPrice(ask)}. Wider stock spreads increase market-order drag and make price control more important.`

    const totalDisplayed = (bidSize || 0) + (askSize || 0)
    participationValue =
      bidSize !== null || askSize !== null
        ? `${formatCompact(bidSize)} x ${formatCompact(askSize)}`
        : 'Sizes pending'
    participationTone =
      bidSize === null && askSize === null
        ? 'warning'
        : totalDisplayed >= 2000
          ? 'positive'
          : totalDisplayed >= 500
            ? 'warning'
            : 'negative'
    participationDetail =
      bidSize === null && askSize === null
        ? 'Waiting for top-of-book sizes before judging how much displayed liquidity is supporting the spread.'
        : `Displayed bid and ask size provide a quick depth check. Thin size means a marketable order is more likely to move through the book.`
  }

  return [
    {
      key: 'session',
      title: 'Session state',
      value: sessionValue,
      tone: sessionTone,
      detail: sessionDetail,
    },
    {
      key: 'venue',
      title: 'Venue context',
      value: venueLabel || 'US market',
      tone: 'info',
      detail:
        normalizedInstrumentType === 'listed_option'
          ? 'Underlying quote venue helps frame the tape, while the contract still needs its own spread and participation check.'
          : 'Primary quote venue helps explain tape behavior, but fill quality still depends on route choice and resting liquidity.',
    },
    {
      key: 'quote',
      title: normalizedInstrumentType === 'listed_option' ? 'Contract quote' : 'Quote quality',
      value: quoteValue,
      tone: quoteTone,
      detail: quoteDetail,
    },
    {
      key: 'participation',
      title: normalizedInstrumentType === 'listed_option' ? 'Contract flow' : 'Top of book',
      value: participationValue,
      tone: participationTone,
      detail: participationDetail,
    },
    {
      key: 'route-fit',
      title: 'Route fit',
      value: routeComparison.current.label,
      tone: routeComparison.current.tone,
      detail: routeComparison.current.detail,
    },
    {
      key: 'event-window',
      title: 'Event window',
      value: eventValue,
      tone: eventTone,
      detail: eventDetail,
    },
    {
      key: 'regime',
      title: 'Regime context',
      value: regimeValue,
      tone: regimeTone,
      detail: regimeDetail,
    },
    {
      key: 'freshness',
      title: 'Data freshness',
      value: freshnessValue,
      tone: freshnessTone,
      detail: freshnessDetail,
    },
  ]
}

function buildRouteChangeFeedback({
  previousRoute,
  currentRoute,
  instrumentType,
  positionPreview,
  quote,
  contract,
  livePrice,
}) {
  if (
    !previousRoute ||
    !currentRoute ||
    (previousRoute.orderType === currentRoute.orderType &&
      previousRoute.timeInForce === currentRoute.timeInForce)
  ) {
    return null
  }

  const { estimatedEntryDrag } = buildEntryDragProfile({
    instrumentType,
    positionPreview,
    quote,
    contract,
    livePrice,
  })

  const previousDrag = estimateRouteDrag(estimatedEntryDrag, {
    instrumentType,
    orderType: previousRoute.orderType,
    timeInForce: previousRoute.timeInForce,
  })
  const currentDrag = estimateRouteDrag(estimatedEntryDrag, {
    instrumentType,
    orderType: currentRoute.orderType,
    timeInForce: currentRoute.timeInForce,
  })

  const improvements = []
  const worsened = []

  if (previousDrag !== null && currentDrag !== null) {
    if (currentDrag + 0.01 < previousDrag) {
      improvements.push(`Estimated entry drag improved from ${formatPrice(previousDrag)} to ${formatPrice(currentDrag)}.`)
    } else if (currentDrag > previousDrag + 0.01) {
      worsened.push(`Estimated entry drag worsened from ${formatPrice(previousDrag)} to ${formatPrice(currentDrag)}.`)
    }
  }

  if (previousRoute.orderType === 'market' && currentRoute.orderType !== 'market') {
    improvements.push('The route now uses price control instead of an uncapped market fill.')
  } else if (previousRoute.orderType !== 'market' && currentRoute.orderType === 'market') {
    worsened.push('The route now prioritizes immediacy over price control.')
  }

  if (previousRoute.orderType === 'stop_market' && currentRoute.orderType === 'stop_limit') {
    improvements.push('The trigger now keeps a price cap after activation.')
  } else if (previousRoute.orderType === 'stop_limit' && currentRoute.orderType === 'stop_market') {
    worsened.push('The trigger now converts into an uncapped market fill once hit.')
  }

  if (normalizeInstrumentType(instrumentType) === 'listed_option') {
    if (previousRoute.timeInForce === 'day_ext' && currentRoute.timeInForce !== 'day_ext') {
      improvements.push('The order now stays in regular-hours option liquidity.')
    } else if (previousRoute.timeInForce !== 'day_ext' && currentRoute.timeInForce === 'day_ext') {
      worsened.push('The order is now trying to work outside regular-hours option liquidity.')
    }
  }

  if (previousRoute.timeInForce === 'gtc_90d' && currentRoute.timeInForce !== 'gtc_90d') {
    improvements.push('The order now has a shorter review window and less stale-order risk.')
  } else if (previousRoute.timeInForce !== 'gtc_90d' && currentRoute.timeInForce === 'gtc_90d') {
    worsened.push('The order can now rest much longer, so stale-order review matters more.')
  }

  const tone =
    improvements.length && !worsened.length
      ? 'positive'
      : worsened.length && !improvements.length
        ? 'negative'
        : improvements.length || worsened.length
          ? 'warning'
          : 'info'

  return {
    tone,
    summary:
      improvements.length && !worsened.length
        ? 'Route improved'
        : worsened.length && !improvements.length
          ? 'Route worsened'
          : 'Route updated',
    currentLabel: `${formatOrderTypeLabel(currentRoute.orderType)} | ${formatTimeInForceLabel(currentRoute.timeInForce)}`,
    previousLabel: `${formatOrderTypeLabel(previousRoute.orderType)} | ${formatTimeInForceLabel(previousRoute.timeInForce)}`,
    improvements,
    worsened,
  }
}

function buildForecastTrustSummary({
  confidenceScore,
  freshness,
  regimeStrengthScore,
  resolvedCount,
  eventConfidencePenalty,
}) {
  const freshnessStatus = String(freshness?.status || '').trim().toLowerCase()
  let score = 0

  if (freshnessStatus === 'fresh') score += 1
  else if (freshnessStatus === 'stale') score -= 1

  if (confidenceScore !== null) {
    if (confidenceScore >= 0.62) score += 1
    else if (confidenceScore < 0.48) score -= 1
  }

  if (regimeStrengthScore !== null) {
    if (regimeStrengthScore >= 0.6) score += 1
    else if (regimeStrengthScore < 0.45) score -= 1
  }

  if (resolvedCount >= 8) score += 1
  else if (resolvedCount < 3) score -= 1

  if (eventConfidencePenalty !== null && eventConfidencePenalty > 0.08) score -= 1

  if (score >= 2) {
    return {
      label: 'High trust',
      tone: 'positive',
      detail: 'Fresh inputs, stronger regime support, and enough resolved history are reinforcing the current forecast.',
    }
  }

  if (score >= 0) {
    return {
      label: 'Conditional',
      tone: 'warning',
      detail: 'The forecast is usable, but at least one support layer is thin enough that execution discipline should dominate.',
    }
  }

  return {
    label: 'Fragile',
    tone: 'negative',
    detail: 'Thin support, stale inputs, or weak regime context mean the desk should treat this read more as a prompt than a conviction call.',
  }
}

function buildTargetQualitySummary({
  resolvedCount,
  averageError,
  empiricalHitRate,
  averageProbabilityUp,
  calibrationScope,
}) {
  const scopeLabel = formatLabel(calibrationScope || 'unknown')
  const edge =
    empiricalHitRate !== null && averageProbabilityUp !== null
      ? empiricalHitRate - averageProbabilityUp
      : null

  if (
    resolvedCount >= 20 &&
    averageError !== null &&
    averageError <= 0.18 &&
    edge !== null &&
    edge >= 0
  ) {
    return {
      label: 'Established',
      tone: 'positive',
      detail: `${scopeLabel} calibration has enough resolved history to act as a recurring edge check, not just a fresh pattern read.`,
    }
  }

  if (
    resolvedCount >= 8 &&
    averageError !== null &&
    averageError <= 0.24
  ) {
    return {
      label: 'Developing',
      tone: 'warning',
      detail: `${scopeLabel} calibration is usable, but the sample is still maturing and should stay secondary to execution discipline.`,
    }
  }

  return {
    label: 'Thin sample',
    tone: 'negative',
    detail: 'Resolved history is still too thin to treat this forecast as a durable recurring edge on its own.',
  }
}

function buildModelDriftSummary({
  confidenceScore,
  freshness,
  regimeStrengthScore,
  resolvedCount,
  averageError,
  empiricalHitRate,
  averageProbabilityUp,
  eventConfidencePenalty,
}) {
  const freshnessStatus = String(freshness?.status || '').trim().toLowerCase()
  const edge =
    empiricalHitRate !== null && averageProbabilityUp !== null
      ? empiricalHitRate - averageProbabilityUp
      : null

  let riskFlags = 0
  if (freshnessStatus === 'stale') riskFlags += 1
  if (confidenceScore !== null && confidenceScore < 0.48) riskFlags += 1
  if (regimeStrengthScore !== null && regimeStrengthScore < 0.45) riskFlags += 1
  if (averageError !== null && averageError > 0.24) riskFlags += 1
  if (edge !== null && edge < -0.03) riskFlags += 1
  if (eventConfidencePenalty !== null && eventConfidencePenalty > 0.08) riskFlags += 1

  if (
    riskFlags >= 3 ||
    (
      resolvedCount >= 20 &&
      averageError !== null &&
      averageError > 0.24 &&
      edge !== null &&
      edge < -0.03
    )
  ) {
    return {
      label: 'Kill switch',
      tone: 'negative',
      action: 'Pause or heavily down-weight until the support recovers.',
      detail: 'The signal is degrading enough that the desk should not treat it as a live edge right now.',
    }
  }

  if (
    riskFlags >= 1 ||
    (averageError !== null && averageError > 0.18) ||
    resolvedCount < 8
  ) {
    return {
      label: 'Watch drift',
      tone: 'warning',
      action: 'Keep it live, but reduce trust and require tighter review.',
      detail: 'At least one support layer is slipping, so the setup should be treated as conditionally degrading instead of stable.',
    }
  }

  return {
    label: 'Stable',
    tone: 'positive',
    action: 'No drift warning is active.',
    detail: 'Fresh inputs, acceptable calibration error, and stable support mean the model is not showing obvious decay.',
  }
}

function buildBenchmarkSummary({
  adjustedProbabilityUp,
  technicalProbabilityUp,
  averageProbabilityUp,
  calibrationScope,
  resolvedCount,
}) {
  if (
    adjustedProbabilityUp !== null &&
    averageProbabilityUp !== null &&
    resolvedCount >= 8
  ) {
    const edge = adjustedProbabilityUp - averageProbabilityUp
    return {
      label: `${formatLabel(calibrationScope || 'global')} baseline`,
      tone: edge >= 0.03 ? 'positive' : edge <= -0.03 ? 'negative' : 'warning',
      comparison: `${formatRatioPercent(adjustedProbabilityUp, 1)} vs ${formatRatioPercent(averageProbabilityUp, 1)}`,
      detail: 'The live forecast is trying to beat the resolved calibration baseline, not just call direction in isolation.',
    }
  }

  if (adjustedProbabilityUp !== null && technicalProbabilityUp !== null) {
    const edge = adjustedProbabilityUp - technicalProbabilityUp
    return {
      label: 'Technical base',
      tone: edge >= 0.02 ? 'positive' : edge <= -0.02 ? 'negative' : 'warning',
      comparison: `${formatRatioPercent(adjustedProbabilityUp, 1)} vs ${formatRatioPercent(technicalProbabilityUp, 1)}`,
      detail: 'The adjusted forecast is being measured against the raw technical model before journal and event effects.',
    }
  }

  if (adjustedProbabilityUp !== null) {
    const edge = adjustedProbabilityUp - 0.5
    return {
      label: 'Neutral 50/50',
      tone: edge >= 0.03 ? 'positive' : edge <= -0.03 ? 'negative' : 'warning',
      comparison: `${formatRatioPercent(adjustedProbabilityUp, 1)} vs 50.0%`,
      detail: 'Without enough calibration history, the forecast should at least beat a neutral up/down baseline.',
    }
  }

  return {
    label: 'No benchmark',
    tone: 'warning',
    comparison: 'Pending',
    detail: 'The desk does not yet have enough live forecast context to define a meaningful benchmark for this setup.',
  }
}

function buildMemorySummary({
  marketRegime,
  bestRegime,
  weakestRegime,
  bestDriver,
  weakestDriver,
}) {
  const activeRegime = String(marketRegime || '').trim().toLowerCase()
  const bestRegimeName = String(bestRegime?.market_regime || '').trim().toLowerCase()
  const weakestRegimeName = String(weakestRegime?.market_regime || '').trim().toLowerCase()
  const bestDriverLabel = formatLabel(bestDriver?.driver || 'unknown')
  const weakestDriverLabel = formatLabel(weakestDriver?.driver || 'unknown')

  if (activeRegime && weakestRegimeName && activeRegime === weakestRegimeName) {
    return {
      label: 'Weak regime memory',
      tone: 'negative',
      detail: `The active ${formatLabel(marketRegime)} regime has been one of the weakest resolved states. ${weakestDriverLabel} has also been the least supportive driver.`,
    }
  }

  if (activeRegime && bestRegimeName && activeRegime === bestRegimeName) {
    return {
      label: 'Known strong regime',
      tone: 'positive',
      detail: `The active ${formatLabel(marketRegime)} regime has resolved well historically. ${bestDriverLabel} has been the most supportive driver in that memory stack.`,
    }
  }

  if (bestRegimeName || weakestRegimeName || bestDriver?.driver || weakestDriver?.driver) {
    return {
      label: 'Mixed memory',
      tone: 'warning',
      detail: `Best memory sits in ${formatLabel(bestRegime?.market_regime || 'another regime')}, weakest in ${formatLabel(weakestRegime?.market_regime || 'another regime')}. Drivers are mixed between ${bestDriverLabel} and ${weakestDriverLabel}.`,
    }
  }

  return {
    label: 'No memory',
    tone: 'warning',
    detail: 'There is not enough resolved regime or driver history here to say where the edge usually holds up or breaks down.',
  }
}

function buildSessionMemorySummary({
  sessionLabel,
  bestSession,
  weakestSession,
}) {
  const activeSession = String(sessionLabel || '').trim().toLowerCase().replaceAll('-', '_')
  const bestSessionName = String(bestSession?.session_label || '').trim().toLowerCase()
  const weakestSessionName = String(weakestSession?.session_label || '').trim().toLowerCase()

  if (activeSession && weakestSessionName && activeSession === weakestSessionName) {
    return {
      label: 'Weak session memory',
      tone: 'negative',
      detail: `The active ${formatLabel(sessionLabel)} session has been one of the weakest resolved trading windows for this setup.`,
    }
  }

  if (activeSession && bestSessionName && activeSession === bestSessionName) {
    return {
      label: 'Known strong session',
      tone: 'positive',
      detail: `The active ${formatLabel(sessionLabel)} session has historically been one of the most supportive windows for this setup.`,
    }
  }

  if (bestSessionName || weakestSessionName) {
    return {
      label: 'Mixed session memory',
      tone: 'warning',
      detail: `Best session memory sits in ${formatLabel(bestSession?.session_label || 'another session')}, while ${formatLabel(weakestSession?.session_label || 'another session')} has been weaker.`,
    }
  }

  return {
    label: 'No session memory',
    tone: 'warning',
    detail: 'There is not enough resolved session history yet to say whether this edge behaves better or worse outside regular hours.',
  }
}

function buildEventMemorySummary({
  eventContext,
  eventRisk,
  nextEventName,
  bestEventWindow,
  weakestEventWindow,
}) {
  const activeEventContext = resolveEventContext(eventContext, {
    event_risk: eventRisk,
    next_event_name: nextEventName,
  })
  const activeEventWindow = String(activeEventContext?.event_window_label || '').trim().toLowerCase() || 'quiet_window'
  const bestEventName = String(bestEventWindow?.event_window_label || '').trim().toLowerCase()
  const weakestEventName = String(weakestEventWindow?.event_window_label || '').trim().toLowerCase()

  if (activeEventWindow && weakestEventName && activeEventWindow === weakestEventName) {
    return {
      label: 'Weak event memory',
      tone: 'negative',
      detail: `The current ${formatLabel(activeEventWindow)} state has been one of the weakest resolved event windows for this setup.`,
    }
  }

  if (activeEventWindow && bestEventName && activeEventWindow === bestEventName) {
    return {
      label: 'Known strong event window',
      tone: 'positive',
      detail: `The current ${formatLabel(activeEventWindow)} state has historically been one of the most supportive event windows for this setup.`,
    }
  }

  if (bestEventName || weakestEventName) {
    return {
      label: 'Mixed event memory',
      tone: 'warning',
      detail: `Best event memory sits in ${formatLabel(bestEventWindow?.event_window_label || 'another window')}, while ${formatLabel(weakestEventWindow?.event_window_label || 'another window')} has been weaker.`,
    }
  }

  return {
    label: 'No event memory',
    tone: 'warning',
    detail: 'There is not enough resolved event-window history yet to say whether this edge behaves better in quiet, macro, or earnings conditions.',
  }
}

function buildDecisionGateSummary({
  tradeDecision,
  forecastTrustSummary,
  executionQualitySummary,
  targetQualitySummary,
  modelDriftSummary,
  benchmarkSummary,
  eventMemorySummary,
  sessionMemorySummary,
  memorySummary,
  promotionGateSummary,
}) {
  const normalizedDecision = String(tradeDecision || '').trim().toUpperCase()
  const blockingReasons = []
  const cautionReasons = []
  const supportFrames = [eventMemorySummary, sessionMemorySummary, memorySummary].filter(Boolean)
  const supportNegativeCount = supportFrames.filter((frame) => frame?.tone === 'negative').length
  const supportWarningCount = supportFrames.filter((frame) => frame?.tone === 'warning').length

  if (normalizedDecision && normalizedDecision !== 'VALID TRADE') {
    blockingReasons.push(normalizedDecision === 'REJECT' ? 'model rejected the setup' : 'model has not green-lit the setup')
  }

  const coreChecks = [
    { frame: forecastTrustSummary, negative: 'forecast trust is fragile', warning: 'forecast trust is conditional' },
    { frame: executionQualitySummary, negative: 'execution is fragile', warning: 'execution still needs price control' },
    { frame: targetQualitySummary, negative: 'calibration sample is thin', warning: 'sample quality is still developing' },
    { frame: modelDriftSummary, negative: 'drift kill switch is active', warning: 'model drift is under watch' },
    { frame: benchmarkSummary, negative: 'forecast is below baseline', warning: 'benchmark edge is only marginal' },
  ]

  coreChecks.forEach(({ frame, negative, warning }) => {
    if (frame?.tone === 'negative') blockingReasons.push(negative)
    else if (frame?.tone === 'warning') cautionReasons.push(warning)
  })

  if (promotionGateSummary?.tone === 'negative') {
    blockingReasons.push('paper promotion gate is locked')
  } else if (promotionGateSummary?.tone === 'warning') {
    cautionReasons.push('paper promotion gate is still in review')
  }

  if (!blockingReasons.length && supportNegativeCount >= 2) {
    blockingReasons.push('multiple historical memory layers are weak together')
  }
  if (!blockingReasons.length && supportNegativeCount === 1) {
    cautionReasons.push('one historical memory layer is weak')
  }
  if (supportWarningCount) {
    cautionReasons.push(
      supportWarningCount === 1
        ? 'historical memory is mixed'
        : 'multiple historical memory layers are mixed',
    )
  }

  if (blockingReasons.length) {
    return {
      label: 'Stand down',
      tone: 'negative',
      action: 'Do not promote this setup to a live candidate yet.',
      basis: `Blocked by ${blockingReasons.slice(0, 2).join(' and ')}${blockingReasons.length > 2 ? ', plus more.' : '.'}`,
      detail: 'At least one core layer is failing hard enough that the stack should not be promoted, even if a few supporting signals still look good.',
    }
  }

  const coreAllPositive =
    normalizedDecision === 'VALID TRADE' &&
    forecastTrustSummary?.tone === 'positive' &&
    executionQualitySummary?.tone === 'positive' &&
    targetQualitySummary?.tone === 'positive' &&
    modelDriftSummary?.tone === 'positive' &&
    benchmarkSummary?.tone === 'positive' &&
    promotionGateSummary?.allowsPromotion !== false

  if (coreAllPositive && supportNegativeCount === 0 && supportWarningCount === 0) {
    return {
      label: 'Promote',
      tone: 'positive',
      action: 'Promote this setup as a live candidate and keep execution discipline tight.',
      basis:
        'Trust, execution, sample quality, benchmark edge, drift, historical memory, and the paper gate all clear together.',
      detail: 'This is the kind of aligned stack the desk should prioritize first when several names are competing for attention.',
    }
  }

  return {
    label: 'Review gate',
    tone: 'warning',
    action: 'Keep it on the board, but require deliberate review before sizing or routing.',
    basis: cautionReasons.length
      ? `${cautionReasons.length} review flag${cautionReasons.length === 1 ? '' : 's'}: ${cautionReasons.slice(0, 2).join(' and ')}${cautionReasons.length > 2 ? ', plus more.' : '.'}`
      : 'The stack is usable, but it is not clearing together strongly enough to auto-promote.',
    detail: 'The setup has enough support to monitor, but not enough full-stack agreement yet to treat it like a top live candidate.',
  }
}

function buildPromotionGateSummary({ validationSnapshot, policy }) {
  return buildSharedPromotionGateSummary({ validationSnapshot, policy })
}

function buildDeskCandidateQueue(rows = [], promotionGateSummary = null) {
  function resolveDeskRanking(row) {
    const rankingContext = row?.ranking_context || {}
    const rankingScore = toNumber(row?.ranking_score ?? rankingContext?.score ?? row?.setup_score)
    const rankingTier = String(row?.ranking_tier || rankingContext?.tier || '').trim().toLowerCase() || 'review'
    const rankingLabel =
      String(row?.ranking_label || rankingContext?.label || '').trim() ||
      (rankingTier === 'promote' ? 'Promote first' : rankingTier === 'stand_down' ? 'Stand down' : 'Reviewable')
    const rankingSummary =
      String(row?.ranking_summary || rankingContext?.summary || '').trim() ||
      (rankingScore === null ? 'Ranking context is still forming.' : `Board score ${rankingScore.toFixed(1)} is setting the desk priority.`)
    const boardRank = toNumber(row?.board_rank ?? rankingContext?.board_rank)
    return {
      score: rankingScore,
      tier: rankingTier,
      label: rankingLabel,
      summary: rankingSummary,
      boardRank,
    }
  }

  const seen = new Set()
  const mapped = (Array.isArray(rows) ? rows : [])
    .filter((row) => row && row.ticker)
    .filter((row) => {
      const symbol = String(row.ticker || '').trim().toUpperCase()
      if (!symbol || seen.has(symbol)) return false
      seen.add(symbol)
      return true
    })
    .map((row) => {
      const decision = String(row.trade_decision || '').trim().toUpperCase()
      const ranking = resolveDeskRanking(row)
      const score = ranking.score
      const probabilityUp = toNumber(row.probability_up)
      const rejectReason = String(row.reject_reason || '').trim()
      let gateLabel = 'Stand down'
      let gateTone = 'negative'
      let gateDetail = rejectReason || ranking.summary || 'The setup is not clearing enough even for the review queue.'
      const paperGateReady = promotionGateSummary?.allowsPromotion !== false

      if (
        ranking.tier === 'promote' &&
        decision === 'VALID TRADE' &&
        score !== null &&
        score >= 70 &&
        probabilityUp !== null &&
        Math.abs(probabilityUp - 0.5) >= 0.08 &&
        !rejectReason
      ) {
        if (paperGateReady) {
          gateLabel = 'Promote'
          gateTone = 'positive'
          gateDetail =
            ranking.summary || 'This liquid-board leader is clearing the desk promotion check and should be reviewed first.'
        } else {
          gateLabel = promotionGateSummary?.tone === 'negative' ? 'Paper gate' : 'Review gate'
          gateTone = 'warning'
          gateDetail = [ranking.summary, promotionGateSummary?.detail]
            .filter(Boolean)
            .join(' ')
        }
      } else if (
        ranking.tier !== 'stand_down' &&
        (
          decision === 'VALID TRADE' ||
          decision === 'PASS' ||
          (score !== null && score >= 55)
        )
      ) {
        gateLabel = 'Review gate'
        gateTone = 'warning'
        gateDetail = rejectReason || ranking.summary || 'The setup is still usable, but it needs more review before it belongs in the live queue.'
      }

      return {
        ticker: String(row.ticker || '').trim().toUpperCase(),
        verdict: row.verdict || row.trade_decision || 'Watch',
        gateLabel,
        gateTone,
        gateDetail,
        rankingLabel: ranking.label,
        rankingTier: ranking.tier,
        rankingSummary: ranking.summary,
        boardRank: ranking.boardRank,
        score,
        probabilityUp,
        livePrice: toNumber(row.live_price ?? row.current_underlying_price ?? row.close),
      }
    })
    .sort((left, right) => {
      const tierRank = { promote: 0, review: 1, stand_down: 2 }
      const leftTierRank = tierRank[left.rankingTier] ?? 3
      const rightTierRank = tierRank[right.rankingTier] ?? 3
      if (leftTierRank !== rightTierRank) return leftTierRank - rightTierRank
      const toneRank = { positive: 0, warning: 1, negative: 2 }
      const leftRank = toneRank[left.gateTone] ?? 3
      const rightRank = toneRank[right.gateTone] ?? 3
      if (leftRank !== rightRank) return leftRank - rightRank
      const leftBoardRank = left.boardRank ?? Number.POSITIVE_INFINITY
      const rightBoardRank = right.boardRank ?? Number.POSITIVE_INFINITY
      if (leftBoardRank !== rightBoardRank) return leftBoardRank - rightBoardRank
      const leftScore = left.score ?? Number.NEGATIVE_INFINITY
      const rightScore = right.score ?? Number.NEGATIVE_INFINITY
      if (leftScore !== rightScore) return rightScore - leftScore
      return left.ticker.localeCompare(right.ticker)
    })

  const promoted = mapped.filter((row) => row.rankingTier === 'promote' && row.gateTone === 'positive').slice(0, 3)
  if (promoted.length) {
    return {
      mode: 'promote',
      rows: promoted,
    }
  }

  return {
    mode: 'review',
    rows: mapped.filter((row) => row.rankingTier !== 'stand_down' && row.gateTone !== 'negative').slice(0, 3),
  }
}

function buildMondayPlaybook({
  sidebarCount,
  candidateQueue,
  decisionGateSummary,
  executionRailState,
  canOpenTrade,
  currentTicker,
  reportTicker,
  hasPendingOrder,
  reviewLoopTicketGuardrail,
}) {
  const normalizedCurrentTicker = String(currentTicker || reportTicker || '').trim().toUpperCase()
  const queueRows = Array.isArray(candidateQueue?.rows) ? candidateQueue.rows : []
  const reviewCandidate = queueRows[0] || null
  const promoteCandidate = queueRows.find((row) => row?.gateTone === 'positive') || reviewCandidate
  const hasCurrentPromote = decisionGateSummary?.tone === 'positive'
  const hasLiveQueue = queueRows.length > 0
  const reviewLockMessage = reviewLoopTicketGuardrail?.blocker || ''
  const reviewLockNote = reviewLoopTicketGuardrail?.primaryNote || null

  const openStep = {
    key: 'open',
    title: 'Open',
    tone: sidebarCount > 0 ? 'positive' : 'warning',
    status: sidebarCount > 0 ? 'Desk ready' : 'Waiting',
    detail:
      sidebarCount > 0
        ? `${formatCompact(sidebarCount)} names are already on the board. Start with the live liquid board before touching the ticket.`
        : 'Open the liquid-board pulse first so the desk has a live board to work from.',
    actionLabel: 'Open watchlist',
  }

  const reviewStep = {
    key: 'review',
    title: 'Review',
    tone: reviewCandidate?.gateTone || 'warning',
    status: reviewCandidate?.gateLabel || 'Queue first',
    detail: reviewCandidate
      ? `${reviewCandidate.ticker} is the first liquid-board setup to review. ${reviewCandidate.gateDetail}`
      : 'No queue leader is active yet, so start with the liquid-board pulse and compare leaders.',
    actionLabel: reviewCandidate ? `Review ${reviewCandidate.ticker}` : 'Open board',
    ticker: reviewCandidate?.ticker || null,
  }

  const promoteStep = {
    key: 'promote',
    title: reviewLockMessage ? 'Repair' : 'Promote',
    tone: reviewLockMessage ? 'negative' : hasCurrentPromote ? 'positive' : promoteCandidate ? 'warning' : 'negative',
    status: reviewLockMessage ? 'Repair lock' : hasCurrentPromote ? 'Current setup' : promoteCandidate?.gateLabel || 'Stand down',
    detail: reviewLockMessage
      ? reviewLockMessage
      : hasCurrentPromote
        ? `${normalizedCurrentTicker || 'The current setup'} already clears the full decision gate and can stay on the live board.`
        : promoteCandidate
          ? promoteCandidate?.gateTone === 'positive'
            ? `${promoteCandidate.ticker} is the best next liquid-board leader to promote before routing.`
            : `${promoteCandidate.ticker} is leading the board, but ${promoteCandidate.gateDetail}`
          : 'No setup is clearing strongly enough to promote right now.',
    actionLabel: reviewLockMessage
      ? 'Open repair note'
      : hasCurrentPromote
        ? `Use ${normalizedCurrentTicker || 'current'}`
        : promoteCandidate
          ? promoteCandidate?.gateTone === 'positive'
            ? `Promote ${promoteCandidate.ticker}`
            : `Review ${promoteCandidate.ticker}`
          : 'Recheck queue',
    actionMode: reviewLockMessage ? 'repair' : hasCurrentPromote ? 'review' : promoteCandidate ? 'review' : 'open',
    note: reviewLockNote,
    ticker: hasCurrentPromote ? normalizedCurrentTicker || null : promoteCandidate?.ticker || null,
  }

  const routeStep = {
    key: 'route',
    title: 'Route',
    tone: reviewLockMessage ? 'negative' : hasPendingOrder || canOpenTrade ? 'positive' : executionRailState?.tone || 'warning',
    status: reviewLockMessage
      ? 'Repair lock'
      : hasPendingOrder
        ? 'Working order'
        : canOpenTrade
          ? 'Ready to route'
          : executionRailState?.label || 'Review route',
    detail: reviewLockMessage
      ? `${reviewLockMessage} Clear the repair note before reopening first-capital routing.`
      : hasPendingOrder
        ? executionRailState?.detail || 'A working order is already live. Review it before sending anything else.'
        : canOpenTrade
          ? 'The ticket has cleared the desk checks. Open the execution rail and route with price control.'
          : executionRailState?.detail || 'The route is not ready yet. Clear the ticket blockers before sending.',
    actionLabel: reviewLockMessage ? 'Open repair note' : 'Open ticket',
    actionMode: reviewLockMessage ? 'repair' : 'route',
    note: reviewLockNote,
  }

  const journalStep = {
    key: 'journal',
    title: 'Journal',
    tone: reportTicker ? 'info' : 'warning',
    status: reportTicker ? 'Capture review' : 'Setup first',
    detail: reportTicker
      ? `Save a desk note for ${String(reportTicker).toUpperCase()} after the review or route decision is clear.`
      : 'Load a live setup first, then capture the desk note from the same rail.',
    actionLabel: reportTicker ? 'Save note' : 'Open ticket',
  }

  return {
    steps: [openStep, reviewStep, promoteStep, routeStep, journalStep],
    footnote: reviewLockMessage
      ? 'The board can still be reviewed, but promotion and routing should stay repair-first until the active repair note is resolved.'
      : hasLiveQueue
        ? 'Use the queue from left to right: open the board, review the leader, promote only cleared setups, then route and journal.'
        : 'No live queue is active yet, so open the board first and run the same sequence once a candidate appears.',
  }
}

function buildPreOpenSnapshot({
  chartFreshness,
  eventContext,
  eventRisk,
  nextEventName,
  candidateQueue,
  canOpenTrade,
  hasPendingOrder,
  executionQualitySummary,
  modelDriftSummary,
  decisionGateSummary,
}) {
  const freshnessStatus = String(chartFreshness?.status || '').trim().toLowerCase()
  const feedTone =
    freshnessStatus === 'fresh'
      ? 'positive'
      : freshnessStatus === 'stale'
        ? 'negative'
        : freshnessStatus
          ? 'warning'
          : 'info'
  const queueRows = Array.isArray(candidateQueue?.rows) ? candidateQueue.rows : []
  const topQueueRow = queueRows[0] || null
  const queueHasPromote = queueRows.some((row) => row?.gateTone === 'positive')
  const activeEventContext = resolveEventContext(eventContext, {
    event_risk: eventRisk,
    next_event_name: nextEventName,
  })
  const routeTone = hasPendingOrder
    ? 'positive'
    : canOpenTrade
      ? 'positive'
      : executionQualitySummary?.tone || 'warning'

  return {
    cards: [
      {
        key: 'feed',
        title: 'Feed',
        tone: feedTone,
        status: chartFreshness?.status ? formatLabel(chartFreshness.status) : 'Unknown',
        detail:
          chartFreshness?.message ||
          'Freshness context is unavailable, so treat the tape as review-first before the open.',
      },
      {
        key: 'event',
        title: 'Event window',
        tone: eventContextTone(activeEventContext),
        status: eventContextStatus(activeEventContext),
        detail: [eventContextDetail(activeEventContext), eventContextNextLabel(activeEventContext)]
          .filter(Boolean)
          .join(' '),
      },
      {
        key: 'queue',
        title: 'Queue',
        tone: queueHasPromote ? 'positive' : topQueueRow ? 'warning' : 'negative',
        status: queueHasPromote ? 'Promote ready' : topQueueRow ? 'Review only' : 'Empty',
        detail: topQueueRow
          ? `${topQueueRow.ticker} is leading the board with a ${topQueueRow.gateLabel.toLowerCase()} state.`
          : 'No live candidate is strong enough yet, so treat the board as review-only.',
      },
      {
        key: 'route',
        title: 'Route',
        tone: routeTone,
        status: hasPendingOrder ? 'Working order' : canOpenTrade ? 'Ready' : executionQualitySummary?.label || 'Review',
        detail: hasPendingOrder
          ? 'A working order is already live, so check that before sending anything new.'
          : canOpenTrade
            ? 'The current ticket is clear enough to route once the playbook review is done.'
            : executionQualitySummary?.detail || 'Execution still needs review before the open.',
      },
      {
        key: 'drift',
        title: 'Drift',
        tone: modelDriftSummary?.tone || 'info',
        status: modelDriftSummary?.label || 'Unknown',
        detail:
          modelDriftSummary?.action ||
          modelDriftSummary?.detail ||
          'Model drift context is unavailable, so keep the setup in manual review.',
      },
    ],
    footnote:
      decisionGateSummary?.tone === 'positive'
        ? 'The current setup is clearing the decision gate, but run this snapshot first so the pre-open tape does not change underneath you.'
        : 'Use this snapshot before the Monday playbook so you know whether you are starting from a real candidate, a review setup, or a stand-down board.',
  }
}

function resolveSessionHandoffPhase(date = new Date()) {
  const { weekday, hour, minute } = getMarketClockParts(date)
  if (weekday === 'Sat' || weekday === 'Sun') return 'close_review'
  const minutes = hour * 60 + minute
  if (minutes < 9 * 60 + 30) return 'pre_open'
  if (minutes < 10 * 60 + 30) return 'opening_drive'
  if (minutes < 14 * 60 + 30) return 'midday'
  return 'close_review'
}

function buildSessionHandoff({
  sessionLabel,
  decisionGateSummary,
  executionQualitySummary,
  modelDriftSummary,
  candidateQueue,
  hasPendingOrder,
  canOpenTrade,
  eventContext,
  eventRisk,
  nextEventName,
  reportTicker,
  currentTicker,
}) {
  const now = new Date()
  const activePhase = resolveSessionHandoffPhase(now)
  const marketClockLabel = marketClockFormatter.format(now)
  const queueRows = Array.isArray(candidateQueue?.rows) ? candidateQueue.rows : []
  const queueLead = queueRows[0] || null
  const currentSymbol = String(reportTicker || currentTicker || '').trim().toUpperCase() || 'the current setup'
  const queueLeadLabel = queueLead?.ticker || 'the board leader'
  const activeEventContext = resolveEventContext(eventContext, {
    event_risk: eventRisk,
    next_event_name: nextEventName,
  })
  const activeEventTone = eventContextTone(activeEventContext)
  const activeEventSummary = [eventContextStatus(activeEventContext), eventContextDetail(activeEventContext)]
    .filter(Boolean)
    .join(': ')
  const phaseOrder = ['pre_open', 'opening_drive', 'midday', 'close_review']
  const activeIndex = Math.max(0, phaseOrder.indexOf(activePhase))

  const phaseCards = [
    {
      key: 'pre_open',
      title: 'Pre-open',
      tone: queueRows.some((row) => row?.gateTone === 'positive') ? 'positive' : queueLead ? 'warning' : 'negative',
      detail: queueLead
        ? `${queueLeadLabel} is leading the board. Confirm the event window and only carry cleared names into the opening sequence.`
        : 'The board is not producing a clean leader yet, so stay in review mode before the bell.',
      focus:
        activeEventTone !== 'positive'
          ? `${activeEventSummary} ${eventContextNextLabel(activeEventContext)}`.trim()
          : 'Use the board to narrow the list before the market opens instead of solving the ticket live at the bell.',
    },
    {
      key: 'opening_drive',
      title: 'Opening drive',
      tone:
        canOpenTrade && decisionGateSummary?.tone === 'positive' && executionQualitySummary?.tone === 'positive'
          ? 'positive'
          : executionQualitySummary?.tone === 'negative'
            ? 'negative'
            : 'warning',
      detail:
        canOpenTrade && decisionGateSummary?.tone === 'positive'
          ? `${currentSymbol} is clear enough to route if the opening tape still holds after the first push.`
          : 'Stay review-first during the opening drive until the gate and route both clear together.',
      focus:
        executionQualitySummary?.detail ||
        'Use price control and avoid forcing fills while spreads and participation are still settling.',
    },
    {
      key: 'midday',
      title: 'Midday',
      tone: modelDriftSummary?.tone || 'info',
      detail:
        modelDriftSummary?.action ||
        modelDriftSummary?.detail ||
        'Use the quieter tape to recheck drift, benchmark edge, and whether the board still deserves attention.',
      focus: queueLead
        ? `If no fresh promote candidate replaces ${queueLeadLabel}, shift toward review, note capture, and selective routing only.`
        : 'If the board is thin by midday, protect attention and focus on review instead of forcing new risk.',
    },
    {
      key: 'close_review',
      title: 'Close review',
      tone: hasPendingOrder ? 'warning' : reportTicker ? 'info' : 'positive',
      detail: hasPendingOrder
        ? 'A working order is still live, so tighten the book and decide whether it belongs into the close.'
        : reportTicker
          ? `Use ${String(reportTicker).toUpperCase()} to wrap the session, save the note, and prep tomorrow's board.`
          : 'Use the close to flatten the routine, capture notes, and reset the board for the next session.',
      focus: 'Review open risk, cancel stale working orders, and save the desk note before the handoff to the next session.',
    },
  ]

  return {
    activePhase,
    marketClockLabel,
    cards: phaseCards.map((card, index) => ({
      ...card,
      status: card.key === activePhase ? 'Now' : index === activeIndex + 1 ? 'Next' : 'Later',
    })),
    footnote: `${marketClockLabel} ET. Let the routine change with the session instead of trading the whole day like one long window.`,
  }
}

function buildPostCloseReview({
  portfolioSummary,
  tradeSummary,
  attributionSummary,
  reviewLoopProgress,
  monitoredTrades,
  pendingOrders,
  decisionGateSummary,
  modelDriftSummary,
  candidateQueue,
  reportTicker,
  currentTicker,
}) {
  const summary = portfolioSummary || {}
  const trades = tradeSummary || {}
  const realizedPnl = toNumber(summary.realized_pnl)
  const unrealizedPnl = toNumber(summary.unrealized_pnl)
  const activeTradeCount = toNumber(summary.active_trade_count) ?? 0
  const closedTrades = toNumber(trades.closed_trades) ?? 0
  const winRate = toNumber(trades.win_rate)
  const executionReviewCount = toNumber(attributionSummary?.execution_review_count) ?? 0
  const thesisReviewCount = toNumber(attributionSummary?.thesis_review_count) ?? 0
  const riskReviewCount = toNumber(attributionSummary?.risk_review_count) ?? 0
  const latestReview = attributionSummary?.latest_review || null
  const openRepairCount = toNumber(reviewLoopProgress?.open_count) ?? 0
  const resolvedRepairCount = toNumber(reviewLoopProgress?.resolved_count) ?? 0
  const latestResolvedRepair = reviewLoopProgress?.latest_resolved || null
  const monitoredRows = Array.isArray(monitoredTrades) ? monitoredTrades : []
  const pendingRows = Array.isArray(pendingOrders) ? pendingOrders : []
  const urgentActions = monitoredRows.filter(
    (row) => String(row?.monitor_action || '').trim().toUpperCase() !== 'HOLD',
  ).length
  const queueRows = Array.isArray(candidateQueue?.rows) ? candidateQueue.rows : []
  const keepRows = queueRows.filter((row) => row?.gateTone === 'positive')
  const fallbackRows = keepRows.length ? keepRows : queueRows.filter((row) => row?.gateTone === 'warning')
  const currentSymbol = String(reportTicker || currentTicker || '').trim().toUpperCase()
  const tomorrowBoard = []

  if (decisionGateSummary?.tone === 'positive' && currentSymbol) {
    tomorrowBoard.push(currentSymbol)
  }
  fallbackRows.forEach((row) => {
    if (row?.ticker && !tomorrowBoard.includes(row.ticker)) {
      tomorrowBoard.push(row.ticker)
    }
  })

  const degradeFlags = []
  if (urgentActions > 0) degradeFlags.push(`${formatCompact(urgentActions)} urgent action${urgentActions === 1 ? '' : 's'}`)
  if (pendingRows.length > 0) degradeFlags.push(`${formatCompact(pendingRows.length)} working order${pendingRows.length === 1 ? '' : 's'}`)
  if (modelDriftSummary?.tone === 'negative') degradeFlags.push('kill-switch drift')
  else if (modelDriftSummary?.tone === 'warning') degradeFlags.push('watch drift')
  if (executionReviewCount > 0) degradeFlags.push(`${formatCompact(executionReviewCount)} execution drift${executionReviewCount === 1 ? '' : 's'}`)
  if (riskReviewCount > 0) degradeFlags.push(`${formatCompact(riskReviewCount)} risk review${riskReviewCount === 1 ? '' : 's'}`)

  return {
    cards: [
      {
        key: 'today',
        title: 'Today',
        tone: realizedPnl === null ? 'info' : realizedPnl > 0 ? 'positive' : realizedPnl < 0 ? 'negative' : 'warning',
        status: realizedPnl === null ? 'No closeout yet' : formatSignedCurrency(realizedPnl),
        detail: `${formatCompact(closedTrades)} closed trades | ${winRate === null ? '--' : formatRatioPercent(winRate, 1)} win rate | ${activeTradeCount} active ${
          activeTradeCount === 1 ? 'position' : 'positions'
        } | ${formatCompact(thesisReviewCount)} thesis review${thesisReviewCount === 1 ? '' : 's'}`,
        focus: `Open P&L is ${unrealizedPnl === null ? '--' : formatSignedCurrency(unrealizedPnl)} heading into the handoff.${latestReview?.label ? ` Latest review: ${latestReview.label.toLowerCase()} on ${latestReview.ticker}.` : ''}`,
      },
      {
        key: 'degraded',
        title: 'What degraded',
        tone: degradeFlags.length === 0 ? 'positive' : modelDriftSummary?.tone === 'negative' || urgentActions > 0 ? 'negative' : 'warning',
        status: degradeFlags.length === 0 ? 'Stable close' : `${degradeFlags.length} flag${degradeFlags.length === 1 ? '' : 's'}`,
        detail: degradeFlags.length
          ? `${degradeFlags.slice(0, 2).join(' | ')}${degradeFlags.length > 2 ? ' | more' : ''}`
          : 'No urgent action, pending-order, or drift warning is standing out at the close.',
        focus:
          modelDriftSummary?.action ||
          'If this card goes negative, down-weight the setup before it earns another place on tomorrow’s board.',
      },
      {
        key: 'repairs',
        title: 'Repair loop',
        tone: openRepairCount > 0 ? 'warning' : resolvedRepairCount > 0 ? 'positive' : 'info',
        status: openRepairCount > 0 ? `${openRepairCount} open` : resolvedRepairCount > 0 ? `${resolvedRepairCount} cleared` : 'No repairs yet',
        detail: latestResolvedRepair
          ? `Latest clear: ${String(latestResolvedRepair?.ticker || 'Desk').trim().toUpperCase() || 'Desk'} - ${String(latestResolvedRepair?.title || 'Resolved repair').trim()}.`
          : openRepairCount > 0
            ? 'Active repair notes are still on the desk, so measure progress by clearing them instead of carrying them.'
            : 'No repair notes have been resolved yet.',
        focus: openRepairCount > 0
          ? 'A cleaner desk means fewer unresolved repairs carried into the next session.'
          : resolvedRepairCount > 0
            ? 'Resolved repairs are a good sign only if the same issues stop recurring in attribution and notes.'
            : 'Use repair notes to capture issues now so the desk can prove they are getting fixed over time.',
      },
      {
        key: 'tomorrow',
        title: 'Tomorrow board',
        tone: tomorrowBoard.length ? (keepRows.length || decisionGateSummary?.tone === 'positive' ? 'positive' : 'warning') : 'negative',
        status: tomorrowBoard.length ? `${tomorrowBoard.length} name${tomorrowBoard.length === 1 ? '' : 's'}` : 'Reset board',
        detail: tomorrowBoard.length
          ? `${tomorrowBoard.slice(0, 3).join(', ')}${tomorrowBoard.length > 3 ? ', plus more' : ''} should be the first names back on the board tomorrow.`
          : 'Nothing is clearing strongly enough to carry forward without a fresh morning review.',
        focus: tomorrowBoard.length
          ? 'Carry only the names that still clear the gate, route, and drift stack after the close.'
          : 'Come in with a clean board and let the pre-open snapshot rebuild the queue from fresh conditions.',
      },
    ],
    footnote:
      'Use this after the close to decide what actually earned another day of attention versus what should be reset before the next session.',
  }
}

function buildTomorrowPrep({
  candidateQueue,
  watchlistRows,
  scannerRows,
  monitoredTrades,
  pendingOrders,
  reviewLoopNotes,
  decisionGateSummary,
  modelDriftSummary,
  reportTicker,
  currentTicker,
}) {
  const queueRows = Array.isArray(candidateQueue?.rows) ? candidateQueue.rows : []
  const watchRows = Array.isArray(watchlistRows) ? watchlistRows : []
  const scanRows = Array.isArray(scannerRows) ? scannerRows : []
  const monitorRows = Array.isArray(monitoredTrades) ? monitoredTrades : []
  const pendingRows = Array.isArray(pendingOrders) ? pendingOrders : []
  const noteRows = Array.isArray(reviewLoopNotes) ? reviewLoopNotes : []
  const currentSymbol = String(reportTicker || currentTicker || '').trim().toUpperCase()
  const carryForward = []
  const resetList = []
  const repairList = []
  const seenCarry = new Set()
  const seenReset = new Set()
  const seenRepair = new Set()

  const addCarry = (ticker, reason, tone = 'positive') => {
    const symbol = String(ticker || '').trim().toUpperCase()
    if (!symbol || seenCarry.has(symbol)) return
    seenCarry.add(symbol)
    carryForward.push({ ticker: symbol, reason, tone })
  }

  const addReset = (ticker, reason, tone = 'warning') => {
    const symbol = String(ticker || '').trim().toUpperCase()
    if (!symbol || seenReset.has(symbol)) return
    seenReset.add(symbol)
    resetList.push({ ticker: symbol, reason, tone })
  }

  const addRepair = (ticker, reason, tone = 'warning') => {
    const symbol = String(ticker || '').trim().toUpperCase()
    if (!symbol || seenRepair.has(symbol)) return
    seenRepair.add(symbol)
    repairList.push({ ticker: symbol, reason, tone })
  }

  if (decisionGateSummary?.tone === 'positive' && currentSymbol) {
    addCarry(currentSymbol, 'Current gate is still clear.')
  }

  queueRows
    .filter((row) => row?.gateTone === 'positive')
    .forEach((row) => addCarry(row.ticker, row.gateDetail || 'Queue leader still clears.'))

  monitorRows
    .filter((row) => String(row?.monitor_action || '').trim().toUpperCase() === 'HOLD')
    .slice(0, 2)
    .forEach((row) => addCarry(row.ticker, 'Live position is still a hold into tomorrow.', 'info'))

  pendingRows.forEach((row) =>
    addReset(
      row?.ticker,
      `${formatOrderTypeLabel(row?.order_type)} is still working and should be reviewed at the open.`,
      'warning',
    ),
  )

  monitorRows
    .filter((row) => String(row?.monitor_action || '').trim().toUpperCase() !== 'HOLD')
    .forEach((row) =>
      addReset(
        row?.ticker,
        `${String(row?.monitor_action || 'Review').trim()} is still active on this name.`,
        'negative',
      ),
    )

  if (modelDriftSummary?.tone === 'negative' && currentSymbol) {
    addReset(currentSymbol, 'Current model drift is on kill switch and should be reset before reuse.', 'negative')
  }

  noteRows
    .filter((note) => String(note?.ticker || '').trim())
    .slice(0, 3)
    .forEach((note) =>
      addRepair(
        note?.ticker,
        String(note?.title || note?.body || 'Active repair note').trim() ||
          'Active repair note',
        resolveReviewLoopNoteTone(note),
      ),
    )

  const excluded = new Set([...seenCarry, ...seenReset])
  const firstLook = []
  const seenFirstLook = new Set()
  ;[...watchRows, ...scanRows].forEach((row) => {
    const symbol = String(row?.ticker || '').trim().toUpperCase()
    if (!symbol || excluded.has(symbol) || seenFirstLook.has(symbol) || firstLook.length >= 4) return
    seenFirstLook.add(symbol)
    firstLook.push({
      ticker: symbol,
      reason: row?.trade_decision || row?.verdict || 'Review first',
      tone: String(row?.trade_decision || '').trim().toUpperCase() === 'VALID TRADE' ? 'warning' : 'info',
    })
  })

  return {
    cards: [
      {
        key: 'carry',
        title: 'Carry forward',
        tone: carryForward.length ? 'positive' : 'warning',
        status: carryForward.length ? `${carryForward.length} kept` : 'Rebuild',
        detail: carryForward.length
          ? 'These names earned another look from the current gate, queue, or live position state.'
          : 'Nothing is strong enough to auto-carry, so rebuild the board from fresh conditions tomorrow.',
        items: carryForward,
      },
      {
        key: 'reset',
        title: 'Reset at open',
        tone: resetList.length ? 'warning' : 'positive',
        status: resetList.length ? `${resetList.length} resets` : 'Clean',
        detail: resetList.length
          ? 'These names need cleanup, cancellation, or a fresh review before they belong back on the live board.'
          : 'No stale working orders or urgent monitored actions are standing out right now.',
        items: resetList,
      },
      {
        key: 'repair',
        title: 'Desk repair',
        tone: repairList.length
          ? repairList.some((item) => item.tone === 'negative')
            ? 'negative'
            : 'warning'
          : 'positive',
        status: repairList.length ? `${repairList.length} repair${repairList.length === 1 ? '' : 's'}` : 'Clear',
        detail: repairList.length
          ? 'These active repair notes should shape tomorrow\'s first pass before new capital gets promoted.'
          : 'No active repair notes with ticker context are waiting for tomorrow\'s board.',
        items: repairList,
      },
      {
        key: 'firstlook',
        title: 'First look',
        tone: firstLook.length ? 'info' : 'warning',
        status: firstLook.length ? `${firstLook.length} queued` : 'Watch board',
        detail: firstLook.length
          ? 'Use these as the first fresh names to scan once the pre-open snapshot is live again.'
          : 'No extra names are waiting in the wings, so let the morning board rebuild naturally.',
        items: firstLook,
      },
    ],
    footnote:
      repairList.length
        ? 'Tomorrow prep should be short: carry only what still deserves attention, reset what drifted, clear the active repair notes, and give yourself a clean first-look list for the next open.'
        : 'Tomorrow prep should be short: carry only what still deserves attention, reset what drifted or stayed stale, and give yourself a clean first-look list for the next open.',
  }
}

function buildMorningBrief({
  chartFreshness,
  candidateQueue,
  tomorrowPrep,
  reviewLoopNotes,
  decisionGateSummary,
  executionQualitySummary,
  modelDriftSummary,
  eventContext,
  eventRisk,
  nextEventName,
  reportTicker,
  currentTicker,
  eventCalendar,
  canOpenTrade,
  capitalPreservationSummary,
  reviewLoopTicketGuardrail,
}) {
  const queueRows = Array.isArray(candidateQueue?.rows) ? candidateQueue.rows : []
  const noteRows = Array.isArray(reviewLoopNotes) ? reviewLoopNotes : []
  const prepCards = Array.isArray(tomorrowPrep?.cards) ? tomorrowPrep.cards : []
  const carryLead = prepCards.find((card) => card?.key === 'carry')?.items?.[0] || null
  const resetLead = prepCards.find((card) => card?.key === 'reset')?.items?.[0] || null
  const repairLead = prepCards.find((card) => card?.key === 'repair')?.items?.[0] || null
  const firstLookLead = prepCards.find((card) => card?.key === 'firstlook')?.items?.[0] || null
  const promoteLead = queueRows.find((row) => row?.gateTone === 'positive') || null
  const queueLead = promoteLead || queueRows[0] || carryLead || firstLookLead || null
  const reviewLead = noteRows[0] || null
  const reviewLeadTicker = String(reviewLead?.ticker || '').trim().toUpperCase()
  const reviewLeadTitle = String(reviewLead?.title || '').trim() || 'Active repair note'
  const reviewLeadTone = reviewLead ? resolveReviewLoopNoteTone(reviewLead) : 'neutral'
  const currentSymbol = String(reportTicker || currentTicker || '').trim().toUpperCase()
  const freshnessLabel = chartFreshness?.status ? formatLabel(chartFreshness.status) : 'Unknown'
  const activeEventContext = resolveEventContext(eventContext, {
    event_risk: eventRisk,
    next_event_name: nextEventName,
  })
  const eventLabel = eventContextStatus(activeEventContext)
  const eventDetail = [eventContextDetail(activeEventContext), eventContextNextLabel(activeEventContext)]
    .filter(Boolean)
    .join(' ')
  const reviewOnlyMode = Boolean(capitalPreservationSummary?.reviewOnlyMode)
  const reviewLoopLock = reviewLoopTicketGuardrail?.blocker || ''
  const reviewLoopLockNote = reviewLoopTicketGuardrail?.primaryNote || null
  const calendarLead = resolveMorningBriefCalendarLead(eventCalendar, currentSymbol)
  const reviewDetail = reviewLead
    ? ` Resolve ${reviewLeadTitle}${reviewLeadTicker ? ` on ${reviewLeadTicker}` : ''} before promoting more size.`
    : ''

  if (reviewOnlyMode) {
    const reviewDetail =
      capitalPreservationSummary?.detail ||
      'The desk is in review-only mode until the next regular session.'

    return {
      tone: 'negative',
      headline: 'Session locked to review-only mode',
      summary: `${freshnessLabel} feed | ${eventLabel}${reviewLead ? ' | Repair note active' : ''}`,
      detail: `${reviewDetail} ${eventDetail} Use the board to review names and the trades view to reduce or close risk.`,
      actionLabel: 'Open trades',
      actionMode: 'trades',
      actionTicker: null,
      items: [
        {
          key: 'watch',
          label: 'Watch first',
          value: queueLead?.ticker || '--',
          detail: queueLead?.reason || queueLead?.gateDetail || 'No clear leader is on the board yet.',
          tone: queueLead?.tone || 'info',
        },
        {
          key: 'ignore',
          label: reviewLead ? 'Repair first' : 'Ignore first',
          value: resetLead?.ticker || repairLead?.ticker || reviewLeadTicker || (reviewLead ? 'Desk' : '--'),
          detail:
            resetLead?.reason ||
            repairLead?.reason ||
            (reviewLead ? `${reviewLeadTitle} should shape the next session before anything gets re-promoted.` : 'No urgent reset is standing out right now.'),
          tone: resetLead?.tone || repairLead?.tone || reviewLeadTone || 'neutral',
        },
        {
          key: 'capital',
          label: 'First capital',
          value: 'Locked',
          detail: reviewDetail,
          tone: 'negative',
        },
      ],
    }
  }

  if (reviewLoopLock) {
    const lockTicker =
      String(reviewLoopLockNote?.ticker || currentSymbol || '').trim().toUpperCase() || 'Desk'
    const lockTitle = String(reviewLoopLockNote?.title || 'Active repair note').trim()

    return {
      tone: 'negative',
      headline: `${lockTicker} is blocked by repair work`,
      summary: `${freshnessLabel} feed | ${eventLabel} | Repair lock`,
      detail: `${reviewLoopLock} ${eventDetail}`.trim(),
      actionLabel: 'Open repair note',
      actionMode: 'repair',
      actionTicker: lockTicker || null,
      actionNote: reviewLoopLockNote,
      items: [
        {
          key: 'watch',
          label: 'Watch first',
          value: queueLead?.ticker || '--',
          detail: queueLead?.reason || queueLead?.gateDetail || 'No clear leader is on the board yet.',
          tone: queueLead?.tone || 'info',
        },
        {
          key: 'ignore',
          label: 'Repair first',
          value: lockTicker,
          detail: lockTitle,
          tone: 'negative',
        },
        {
          key: 'capital',
          label: 'First capital',
          value: 'Locked',
          detail: 'The ticket is blocked until the active repair note is resolved.',
          tone: 'negative',
        },
      ],
    }
  }

  if (calendarLead.currentTickerEvent) {
    const catalyst = calendarLead.currentTickerEvent
    const catalystTone =
      String(catalyst?.tone || '').trim().toLowerCase() === 'negative' ||
      toNumber(catalyst?.days_until) === 0
        ? 'negative'
        : 'warning'
    const catalystTitle = String(catalyst?.title || `${currentSymbol} catalyst`).trim()
    const catalystDetail =
      String(catalyst?.detail || '').trim() ||
      `${currentSymbol} has ${catalystTitle} approaching, so the setup should stay conditional until the window clears.`

    return {
      tone: catalystTone,
      headline: `${currentSymbol} is trading into a catalyst window`,
      summary: `${freshnessLabel} feed | ${eventLabel} | Catalyst first`,
      detail: `${catalystDetail} ${eventContextNextLabel(activeEventContext)}${reviewDetail}`.trim(),
      actionLabel: `Review ${currentSymbol}`,
      actionMode: 'review',
      actionTicker: currentSymbol || null,
      items: [
        {
          key: 'watch',
          label: 'Watch first',
          value: currentSymbol || queueLead?.ticker || '--',
          detail: catalystTitle,
          tone: catalystTone,
        },
        {
          key: 'ignore',
          label: reviewLead ? 'Repair first' : 'Ignore first',
          value: resetLead?.ticker || repairLead?.ticker || reviewLeadTicker || (reviewLead ? 'Desk' : '--'),
          detail:
            resetLead?.reason ||
            repairLead?.reason ||
            (reviewLead ? `${reviewLeadTitle} should stay in the opening caution stack.` : 'No urgent reset is standing out right now.'),
          tone: resetLead?.tone || repairLead?.tone || reviewLeadTone || 'neutral',
        },
        {
          key: 'capital',
          label: 'First capital',
          value: 'Catalyst first',
          detail: `${catalystTitle} is close enough that first capital should stay conditional until the event window clears.`,
          tone: catalystTone,
        },
      ],
    }
  }

  if (calendarLead.urgentMacro) {
    const macroLead = calendarLead.urgentMacro
    const macroTone =
      String(macroLead?.tone || '').trim().toLowerCase() === 'negative' ||
      toNumber(macroLead?.days_until) === 0
        ? 'negative'
        : 'warning'
    const macroTitle = String(macroLead?.title || 'Macro event').trim() || 'Macro event'
    const macroDetail =
      String(macroLead?.detail || '').trim() ||
      `${macroTitle} is the next macro release on deck and should shape the opening posture.`

    return {
      tone: macroTone,
      headline: `${macroTitle} should set the opening tone`,
      summary: `${freshnessLabel} feed | ${eventLabel} | Macro calendar first`,
      detail: `${macroDetail} Let the release land before promoting first capital from the board.${reviewDetail}`.trim(),
      actionLabel: 'Open alerts',
      actionMode: 'calendar',
      actionTicker: null,
      items: [
        {
          key: 'watch',
          label: 'Watch first',
          value: queueLead?.ticker || '--',
          detail: queueLead?.reason || queueLead?.gateDetail || 'No clear leader is on the board yet.',
          tone: queueLead?.tone || 'info',
        },
        {
          key: 'ignore',
          label: reviewLead ? 'Repair first' : 'Ignore first',
          value: resetLead?.ticker || repairLead?.ticker || reviewLeadTicker || (reviewLead ? 'Desk' : '--'),
          detail:
            resetLead?.reason ||
            repairLead?.reason ||
            (reviewLead ? `${reviewLeadTitle} should still shape the opening caution stack.` : 'No urgent reset is standing out right now.'),
          tone: resetLead?.tone || repairLead?.tone || reviewLeadTone || 'neutral',
        },
        {
          key: 'capital',
          label: 'First capital',
          value: 'Wait for macro',
          detail: `${macroTitle} is close enough that capital promotion should wait for the macro window to clear.`,
          tone: macroTone,
        },
      ],
    }
  }

  let firstCapitalTicker = null
  let firstCapitalReason = 'No setup is cleared enough yet to deserve first capital.'
  let tone = 'warning'

  if (
    decisionGateSummary?.tone === 'positive' &&
    executionQualitySummary?.tone === 'positive' &&
    modelDriftSummary?.tone !== 'negative' &&
    currentSymbol
  ) {
    firstCapitalTicker = currentSymbol
    firstCapitalReason = `${currentSymbol} is already clearing gate, route, and drift well enough to be first capital if the opening tape confirms it.`
    tone = 'positive'
  } else if (promoteLead && modelDriftSummary?.tone !== 'negative') {
    firstCapitalTicker = promoteLead.ticker
    firstCapitalReason = `${promoteLead.ticker} is the clearest promote-ready name on the board, so start capital review there first.`
    tone = 'warning'
  } else if (queueLead) {
    firstCapitalReason = `${queueLead.ticker} is the next name to review, but it still needs more confirmation before it deserves first capital.`
    tone = 'warning'
  } else {
    tone = 'negative'
  }

  const actionTicker = firstCapitalTicker || queueLead?.ticker || null
  const actionMode =
    firstCapitalTicker && currentSymbol && firstCapitalTicker === currentSymbol && canOpenTrade
      ? 'route'
      : actionTicker
        ? 'review'
        : 'open'
  const actionLabel =
    actionMode === 'route'
      ? 'Open ticket'
      : actionMode === 'review'
        ? `Review ${actionTicker}`
        : 'Open board'

  return {
    tone,
    headline:
    tone === 'positive'
        ? `${firstCapitalTicker} deserves first capital`
        : queueLead
          ? `Review ${queueLead.ticker} before committing capital`
          : 'No first-capital candidate yet',
    summary: `${freshnessLabel} feed | ${eventLabel}${reviewLead ? ' | Repair note active' : ''}`,
    detail: `${firstCapitalReason} ${eventDetail}${reviewDetail}`.trim(),
    actionLabel,
    actionMode,
    actionTicker,
    items: [
      {
        key: 'watch',
        label: 'Watch first',
        value: queueLead?.ticker || '--',
        detail: queueLead?.reason || queueLead?.gateDetail || 'No clear leader is on the board yet.',
        tone: queueLead?.tone || 'info',
      },
      {
        key: 'ignore',
          label: reviewLead ? 'Repair first' : 'Ignore first',
          value: resetLead?.ticker || repairLead?.ticker || reviewLeadTicker || (reviewLead ? 'Desk' : '--'),
          detail:
            resetLead?.reason ||
            repairLead?.reason ||
            (reviewLead ? `${reviewLeadTitle} should shape tomorrow's opening caution before new size is promoted.` : 'No urgent reset is standing out right now.'),
        tone: resetLead?.tone || repairLead?.tone || reviewLeadTone || 'neutral',
      },
      {
        key: 'capital',
        label: 'First capital',
        value: firstCapitalTicker || 'Stand down',
        detail: firstCapitalReason,
        tone,
      },
    ],
  }
}

function buildLiveFocusSummary({
  currentTicker,
  reportTicker,
  focusLockTicker,
  livePrice,
  priceDelta,
  priceDeltaPct,
  decisionGateSummary,
  executionQualitySummary,
  modelDriftSummary,
  routeComparison,
  positionPreview,
  riskReward,
  sendConfidence,
  activePendingOrder,
  selectedChartPoint,
  candidateQueue,
  canOpenTrade,
  orderType,
  timeInForce,
  eventRisk,
  capitalPreservationSummary,
}) {
  const currentSymbol = String(reportTicker || currentTicker || '').trim().toUpperCase() || 'Desk'
  const lockedTicker = String(focusLockTicker || '').trim().toUpperCase()
  const isLocked = Boolean(currentSymbol) && currentSymbol === lockedTicker
  const queueRows = Array.isArray(candidateQueue?.rows) ? candidateQueue.rows : []
  const queueLead = queueRows[0] || null
  const reviewLead =
    !isLocked && queueLead && String(queueLead.ticker || '').trim().toUpperCase() !== currentSymbol
      ? queueLead
      : null
  const effectiveRisk = toNumber(positionPreview?.effectiveMaxRiskDollars)
  const suggestedUnits = toNumber(positionPreview?.suggestedContracts)
  const routeLabel =
    routeComparison?.current?.label ||
    `${describeOrderType(orderType)} | ${formatTimeInForceLabel(timeInForce)}`
  const routeDetail =
    routeComparison?.current?.detail ||
    sendConfidence?.detail ||
    executionQualitySummary?.detail ||
    'Keep price control and routing discipline tight while this setup is live.'
  const activeNameTone = reviewLead ? 'warning' : decisionGateSummary?.tone || 'info'
  const routeTone =
    sendConfidence?.tone || executionQualitySummary?.tone || decisionGateSummary?.tone || 'info'
  const riskTone = eventRisk
    ? 'negative'
    : riskReward === null
      ? 'warning'
      : riskReward >= 2
        ? 'positive'
        : riskReward >= 1
          ? 'warning'
          : 'negative'
  const reviewOnlyMode = Boolean(capitalPreservationSummary?.reviewOnlyMode)

  if (reviewOnlyMode) {
    const reviewDetail =
      capitalPreservationSummary?.detail ||
      'The desk is in review-only mode until the next regular session.'

    return {
      tone: 'negative',
      headline: activePendingOrder
        ? `Manage ${currentSymbol} without adding risk`
        : `Review-only mode is active for ${currentSymbol}`,
      summary: activePendingOrder
        ? `${reviewDetail} Keep the ticket in cancel-or-reduce mode only.`
        : reviewDetail,
      isLocked,
      lockLabel: isLocked ? 'Unlock setup' : 'Lock setup',
      lockDetail: isLocked
        ? `Trade lock is active for ${currentSymbol}. Keep the board pinned here while you clean up risk without routing anything new.`
        : `Lock ${currentSymbol} if you want the board pinned here while the session stays in review-only mode.`,
      actionLabel: activePendingOrder ? 'Open ticket' : 'Open trades',
      actionMode: activePendingOrder ? 'route' : 'trades',
      actionTicker: null,
      cards: [
        {
          key: 'name',
          title: 'Active name',
          value:
            toNumber(livePrice) !== null
              ? formatInlineMeta([currentSymbol, formatPrice(livePrice)])
              : currentSymbol,
          tone: decisionGateSummary?.tone || 'warning',
          detail: `${formatSignedNumber(priceDelta)} (${formatSignedPercent(
            priceDeltaPct,
          )}) today. The board stays in observation mode until the next reset window opens.`,
        },
        {
          key: 'route',
          title: 'Route now',
          value: activePendingOrder ? 'Cancel only' : 'Review only',
          tone: 'negative',
          detail: activePendingOrder
            ? 'Keep price control tight and favor canceling the working order over editing or filling it.'
            : 'New routing is paused. Keep queue ranking, event posture, and cleanup priorities in view.',
        },
        {
          key: 'risk',
          title: 'Risk view',
          value: eventRisk ? 'Event risk live' : 'Stand down',
          tone: eventRisk ? 'negative' : riskTone,
          detail: reviewDetail,
        },
      ],
      footnote: activePendingOrder
        ? 'A working order can still be canceled, but fresh entries and replacements stay paused until the next regular session.'
        : 'Use live focus mode to review the stack, not to route new entries, until the session resets.',
    }
  }

  let headline = `Keep ${currentSymbol} in focus`
  let summary = decisionGateSummary?.detail || 'Stay selective until the stack improves.'
  let tone = decisionGateSummary?.tone || 'warning'
  let actionLabel = 'Open ticket'
  let actionMode = 'plan'
  let actionTicker = null

  if (canOpenTrade && sendConfidence) {
    headline = sendConfidence.title
    summary = sendConfidence.detail
    tone = sendConfidence.tone === 'positive' ? 'positive' : 'warning'
    actionLabel = activePendingOrder ? 'Open replacement' : 'Open ticket'
    actionMode = 'route'
    if (isLocked) {
      summary = `${sendConfidence.detail} Queue nudges are paused while ${currentSymbol} is locked.`
    }
  } else if (reviewLead) {
    headline = `Review ${reviewLead.ticker} before routing`
    summary = `${reviewLead.gateDetail} ${currentSymbol} can wait while the stronger candidate is checked first.`
    tone = reviewLead.gateTone || 'warning'
    actionLabel = `Review ${reviewLead.ticker}`
    actionMode = 'review'
    actionTicker = reviewLead.ticker
  } else if (decisionGateSummary?.tone === 'negative') {
    headline = `Stand down on ${currentSymbol}`
    summary = decisionGateSummary.detail
    tone = 'negative'
    actionLabel = 'Review checks'
    actionMode = 'plan'
  } else if (selectedChartPoint) {
    headline = `Stage ${currentSymbol} with care`
    summary =
      sendConfidence?.detail ||
      'A chart point is staged, but the rest of the stack still needs deliberate review before send.'
    tone = routeTone
    actionLabel = 'Open ticket'
    actionMode = 'route'
  } else if (isLocked) {
    headline = `Locked on ${currentSymbol}`
    summary = `Queue nudges are paused while you manage ${currentSymbol}. Keep route, risk, and drift in view until the setup is resolved.`
    tone = decisionGateSummary?.tone || routeTone
  }

  const activeNameValue =
    toNumber(livePrice) !== null ? formatInlineMeta([currentSymbol, formatPrice(livePrice)]) : currentSymbol
  const activeNameDetail = isLocked
    ? `${formatSignedNumber(priceDelta)} (${formatSignedPercent(
        priceDeltaPct,
      )}) today. Trade lock is active, so the broader queue will not interrupt this setup.`
    : reviewLead
    ? `${formatSignedNumber(priceDelta)} (${formatSignedPercent(
        priceDeltaPct,
      )}) today. ${reviewLead.ticker} is the stronger alternate if this setup slips.`
    : `${formatSignedNumber(priceDelta)} (${formatSignedPercent(
        priceDeltaPct,
      )}) today. ${decisionGateSummary?.label || 'Review'} is the live board state.`

  const riskValue = eventRisk
    ? 'Event risk live'
    : riskReward === null
      ? effectiveRisk !== null
        ? formatPrice(effectiveRisk)
        : 'Needs review'
      : `${formatNumber(riskReward, 2)}R`
  const riskDetail = eventRisk
    ? 'Catalyst risk is active, so treat mapped reward and size as conditional rather than stable.'
    : positionPreview?.statusText
      ? `${positionPreview.statusText}${
          effectiveRisk !== null ? ` Effective risk is ${formatPrice(effectiveRisk)}.` : ''
        }`
      : modelDriftSummary?.detail || 'Keep invalidation, size, and drift discipline visible while routing.'

  return {
    tone,
    headline,
    summary,
    isLocked,
    lockLabel: isLocked ? 'Unlock setup' : 'Lock setup',
    lockDetail: isLocked
      ? `Trade lock is active for ${currentSymbol}. Alternate queue nudges stay muted until you release it.`
      : reviewLead
        ? `Lock ${currentSymbol} if you want to stop ${reviewLead.ticker} from interrupting active management.`
        : `Lock ${currentSymbol} to keep the board pinned to this route and risk view while you manage it.`,
    actionLabel,
    actionMode,
    actionTicker,
    cards: [
      {
        key: 'name',
        title: 'Active name',
        value: activeNameValue,
        tone: activeNameTone,
        detail: activeNameDetail,
      },
      {
        key: 'route',
        title: 'Route now',
        value: routeLabel,
        tone: routeTone,
        detail: routeDetail,
      },
      {
        key: 'risk',
        title: 'Risk view',
        value: riskValue,
        tone: riskTone,
        detail:
          suggestedUnits !== null && suggestedUnits > 0
            ? `${riskDetail} Sized for ${formatShares(suggestedUnits)} ${positionPreview?.unitLabel || 'units'}.`
            : riskDetail,
      },
    ],
    footnote: isLocked
      ? `Trade lock keeps ${currentSymbol} pinned in focus mode until you release it or move on intentionally.`
      : reviewLead
      ? `Focus mode keeps the board minimal on purpose. If ${reviewLead.ticker} stays stronger than ${currentSymbol}, switch before you route risk.`
      : 'Focus mode hides the broader board until you need it again. Use Full view to reopen the wider watchlist.',
  }
}

function buildExecutionQualitySummary({
  instrumentType,
  quote,
  contract,
  routeComparison,
  freshness,
  sessionLabel,
  executionContext,
  sessionModel,
}) {
  const normalizedFreshnessStatus = String(freshness?.status || '').trim().toLowerCase()
  const awaitingRegularSession =
    normalizedFreshnessStatus === 'awaiting_regular_session' && Boolean(sessionModel?.regularHoursOnly)

  if (executionContext && typeof executionContext === 'object' && executionContext.fill_label) {
    let tone =
      executionContext.fill_tone === 'negative'
        ? 'negative'
        : executionContext.fill_tone === 'positive'
          ? 'positive'
          : 'warning'
    let label = executionContext.fill_label
    let routeLabel = executionContext.route_label || 'Prefer a priced limit route.'
    let detail =
      executionContext.summary ||
      'Execution posture is being framed from the current session, data freshness, and available liquidity.'

    if (awaitingRegularSession) {
      tone = 'warning'
      label = 'Await regular session'
      routeLabel = 'Desk is intentionally waiting for core-session liquidity.'
      detail =
        freshness?.message ||
        'Regular-hours mode is active, so off-session bars should be treated as prep context instead of a stale-feed failure.'
    }

    if (!awaitingRegularSession && routeComparison?.current?.tone === 'negative') {
      tone = 'negative'
      label = 'Fragile fills'
      routeLabel = 'Current route needs tighter price control.'
      detail = `${detail} The active route is still leaning too aggressive for the current book.`
    } else if (!awaitingRegularSession && routeComparison?.current?.tone === 'warning' && tone === 'positive') {
      tone = 'warning'
      label = 'Use price control'
      routeLabel = 'Current route needs a priced entry.'
      detail = `${detail} The setup is tradable, but the chosen route still deserves tighter price control.`
    }

    const participationLabel = [executionContext.liquidity_label, executionContext.size_cap_label]
      .filter(Boolean)
      .join(' | ')

    return {
      label,
      tone,
      routeLabel,
      detail,
      spreadLabel: executionContext.spread_label || 'Spread pending',
      participationLabel: participationLabel || 'Liquidity pending',
    }
  }

  const normalizedInstrumentType = normalizeInstrumentType(instrumentType)
  const normalizedFreshness = normalizedFreshnessStatus
  let score = 0
  let spreadLabel = 'Spread pending'
  let participationLabel = 'Liquidity pending'

  if (normalizedInstrumentType === 'listed_option') {
    const spreadPct = toNumber(contract?.spread_pct)
    const volume = toNumber(contract?.volume)
    const openInterest = toNumber(contract?.open_interest)

    spreadLabel = spreadPct === null ? 'Spread pending' : `${formatPercent(spreadPct, 1)} spread`
    participationLabel =
      volume === null && openInterest === null
        ? 'Vol / OI pending'
        : `Vol ${formatCompact(volume)} | OI ${formatCompact(openInterest)}`

    if (spreadPct !== null) {
      if (spreadPct <= 6) score += 1
      else if (spreadPct > 12) score -= 1
    }
    if (volume !== null && openInterest !== null) {
      if (volume >= 100 && openInterest >= 500) score += 1
      else if (volume < 25 || openInterest < 100) score -= 1
    }
  } else {
    const bid = toNumber(quote?.bid_price)
    const ask = toNumber(quote?.ask_price)
    const bidSize = toNumber(quote?.bid_size)
    const askSize = toNumber(quote?.ask_size)
    const rawSpread = resolveDisplaySpread(quote?.spread, bid, ask)
    const midPrice = bid !== null && ask !== null ? (bid + ask) / 2 : null
    const spreadPct = rawSpread !== null && midPrice !== null && midPrice > 0 ? (rawSpread / midPrice) * 100 : null
    const displayedDepth = (bidSize || 0) + (askSize || 0)

    spreadLabel = rawSpread === null ? 'Spread pending' : `${formatPrice(rawSpread)} spread`
    participationLabel =
      bidSize === null && askSize === null
        ? 'Sizes pending'
        : `${formatCompact(bidSize)} x ${formatCompact(askSize)}`

    if (spreadPct !== null) {
      if (spreadPct <= 0.05) score += 1
      else if (spreadPct > 0.15) score -= 1
    }
    if (bidSize !== null && askSize !== null) {
      if (displayedDepth >= 2000) score += 1
      else if (displayedDepth < 500) score -= 1
    }
  }

  if (awaitingRegularSession) {
    return {
      label: 'Await regular session',
      tone: 'warning',
      routeLabel: 'Desk is intentionally waiting for core-session liquidity.',
      detail:
        freshness?.message ||
        'Regular-hours mode is active, so the desk is waiting for the next core session rather than forcing an off-session read.',
      spreadLabel,
      participationLabel,
    }
  }

  if (normalizedFreshness === 'stale') score -= 1
  if (sessionLabel !== 'Regular') score -= 1
  if (routeComparison?.current?.tone === 'warning' || routeComparison?.current?.tone === 'negative') score -= 1

  if (score >= 2) {
    return {
      label: 'Execution clean',
      tone: 'positive',
      routeLabel: 'Marketable routing can work if urgency is real.',
      detail: 'Spread drag and available liquidity look supportive enough that the fill should not dominate the idea.',
      spreadLabel,
      participationLabel,
    }
  }

  if (score >= 0) {
    return {
      label: 'Use price control',
      tone: 'warning',
      routeLabel: 'Prefer a priced route over immediacy.',
      detail: 'Execution is workable, but fill drag is meaningful enough that you should protect the entry price.',
      spreadLabel,
      participationLabel,
    }
  }

  return {
    label: 'Fragile fills',
    tone: 'negative',
    routeLabel: 'Do not assume the forecast edge survives a sloppy fill.',
    detail: 'Wide spreads, thin liquidity, or off-session routing can overwhelm a modest forecast edge.',
    spreadLabel,
    participationLabel,
  }
}

function buildTicketChecklist({
  instrumentType,
  blockingReasons,
  warningReasons,
  routeComparison,
  positionPreview,
  riskReward,
  contract,
  orderNeedsLimitPrice,
  orderNeedsStopPrice,
  orderNeedsTrailingPercent,
}) {
  const normalizedInstrumentType = normalizeInstrumentType(instrumentType)
  const allWarnings = [...warningReasons]
  const findReason = (targetKeys) =>
    blockingReasons.find((reason) => targetKeys.includes(reason.targetKey)) ||
    allWarnings.find((reason) => targetKeys.includes(reason.targetKey)) ||
    null
  const buildStep = ({ key, title, targetKey, reason, doneDetail, warningDetail, blockedDetail }) => {
    if (reason && blockingReasons.includes(reason)) {
      return {
        key,
        title,
        targetKey: reason.targetKey || targetKey,
        tone: 'negative',
        stateLabel: 'Blocked',
        detail: blockedDetail || reason.message,
        actionLabel: reason.actionLabel || 'Fix this step',
      }
    }
    if (reason) {
      return {
        key,
        title,
        targetKey: reason.targetKey || targetKey,
        tone: 'warning',
        stateLabel: 'Review',
        detail: warningDetail || reason.message,
        actionLabel: reason.actionLabel || 'Review this step',
      }
    }
    return {
      key,
      title,
      targetKey,
      tone: 'positive',
      stateLabel: 'Clear',
      detail: doneDetail,
      actionLabel: 'Jump to section',
    }
  }

  const setupReason = findReason(['checks'])
  const sizingReason = findReason(['account-size', 'risk-percent'])
  const routeReason = findReason(['order-type', 'time-in-force'])
  const detailsReason = findReason([
    'contract-summary',
    'limit-price',
    'stop-price',
    'trail-percent',
  ])

  const units = toNumber(positionPreview?.suggestedContracts)
  const effectiveRisk = toNumber(positionPreview?.effectiveMaxRiskDollars)
  const detailsTargetKey =
    detailsReason?.targetKey ||
    (normalizedInstrumentType === 'listed_option'
      ? 'contract-summary'
      : orderNeedsLimitPrice
        ? 'limit-price'
        : orderNeedsStopPrice
          ? 'stop-price'
          : orderNeedsTrailingPercent
            ? 'trail-percent'
            : 'execution-guide')

  const steps = [
    buildStep({
      key: 'setup',
      title: 'Validate setup',
      targetKey: 'checks',
      reason: setupReason,
      doneDetail:
        riskReward !== null
          ? `Setup, event risk, and reward map are live with about ${riskReward.toFixed(2)}R mapped.`
          : 'Setup checks are clean and ready for final review.',
    }),
    buildStep({
      key: 'sizing',
      title: 'Size the risk',
      targetKey: 'risk-percent',
      reason: sizingReason,
      doneDetail:
        units !== null && effectiveRisk !== null
          ? `${units} unit${units === 1 ? '' : 's'} fit the current risk budget with about ${formatPrice(effectiveRisk)} at risk.`
          : 'Sizing is mapped cleanly against the current account and risk settings.',
    }),
    buildStep({
      key: 'route',
      title: 'Choose route',
      targetKey: 'order-type',
      reason:
        routeReason ||
        (routeComparison.hasAlternative
          ? {
              targetKey: 'order-type',
              message: routeComparison.alternative.detail,
              actionLabel: 'Review safer route',
            }
          : null),
      doneDetail: `${routeComparison.current.label} is already the disciplined route for this setup.`,
      warningDetail: routeComparison.hasAlternative
        ? `${routeComparison.summaryLabel}. ${routeComparison.alternative.detail}`
        : null,
    }),
    buildStep({
      key: 'details',
      title: normalizedInstrumentType === 'listed_option' ? 'Review contract' : 'Complete order details',
      targetKey: detailsTargetKey,
      reason: detailsReason,
      doneDetail:
        normalizedInstrumentType === 'listed_option'
          ? `${contract?.contract_symbol || 'Recommended contract'} and the current order details are ready for review.`
          : orderNeedsLimitPrice || orderNeedsStopPrice || orderNeedsTrailingPercent
            ? 'All required priced-order fields are filled and ready.'
            : 'No extra priced-order fields are required for this route.',
    }),
  ]

  const clearedCount = steps.filter((step) => step.tone === 'positive').length
  const blockedCount = steps.filter((step) => step.tone === 'negative').length
  const reviewCount = steps.filter((step) => step.tone === 'warning').length
  const summary =
    blockedCount > 0
      ? `${blockedCount} step${blockedCount === 1 ? '' : 's'} still block routing. Start at the first blocked item.`
      : reviewCount > 0
        ? `${reviewCount} step${reviewCount === 1 ? '' : 's'} still need review before you treat this ticket as clean execution.`
        : 'Checklist is clear. Review the cost preview and route when you are ready.'

  return {
    steps,
    clearedCount,
    totalCount: steps.length,
    summary,
  }
}

function describeTimeInForce(value) {
  switch (value) {
    case 'gtc_90d':
      return 'Resting long ideas can stay working for up to 90 days.'
    case 'day_ext':
      return 'Short-hour orders can work through the after-hours close.'
    case 'day':
    default:
      return 'Regular-session order that expires at today close.'
  }
}

function createGuardrailReason(message, targetKey, actionLabel) {
  return {
    message,
    targetKey,
    actionLabel,
  }
}

function TicketFieldLabel({ label, tooltip }) {
  if (!tooltip) {
    return <span>{label}</span>
  }

  return (
    <span className="ticket-field__label-row">
      <span>{label}</span>
      <span className="ticket-field__help-wrap">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="ticket-field__help"
          aria-label={`${label} help`}
          title={tooltip}
          onClick={(event) => event.preventDefault()}
        >
          ?
        </Button>
        <span className="ticket-field__tooltip" role="tooltip">
          {tooltip}
        </span>
      </span>
    </span>
  )
}

function executionTone(value) {
  switch (String(value || '').toLowerCase()) {
    case 'filled':
    case 'open':
    case 'ready':
      return 'positive'
    case 'working':
    case 'pending':
    case 'accepted':
    case 'staged':
    case 'submitting':
      return 'info'
    case 'canceled':
    case 'locked':
    case 'exit_watch':
      return 'warning'
    case 'rejected':
    case 'risk_watch':
      return 'negative'
    case 'closed':
    case 'waiting':
    default:
      return 'neutral'
  }
}

function describeExecutionRailState({
  activeExecutionPrice,
  backendOrderEvent,
  canOpenTrade,
  capitalPreservationSummary,
  currentTicker,
  lastOrderEvent,
  orderType,
  pendingOrder,
  positionStatusText,
  reportTicker,
  selectedChartPoint,
  timeInForce,
}) {
  const normalizedTicker = String(currentTicker || '').trim().toUpperCase()
  const scopedLastOrderEvent =
    lastOrderEvent && String(lastOrderEvent.ticker || '').trim().toUpperCase() === normalizedTicker
      ? lastOrderEvent
      : null
  const scopedBackendOrderEvent =
    backendOrderEvent &&
    String(backendOrderEvent.ticker || '').trim().toUpperCase() === normalizedTicker
      ? backendOrderEvent
      : null

  const sourceValue = selectedChartPoint
    ? 'Chart staged'
    : activeExecutionPrice !== null
      ? 'Live price'
      : 'No feed'
  const reviewOnlyMode = Boolean(capitalPreservationSummary?.reviewOnlyMode)

  if (reviewOnlyMode) {
    return {
      key: 'review_only',
      label: 'Review only',
      tone: capitalPreservationSummary?.tone || 'negative',
      detail:
        capitalPreservationSummary?.detail ||
        'The session is locked into review-only mode until the next regular session.',
      chips: [
        { label: 'Source', value: sourceValue },
        { label: 'Ticket', value: 'Review only' },
        { label: 'Route', value: 'Stand down' },
        { label: 'Book', value: 'No new orders' },
      ],
    }
  }

  if (scopedLastOrderEvent?.state === 'submitting') {
    return {
      key: 'submitting',
      label: 'Submitting',
      tone: executionTone('submitting'),
      detail: scopedLastOrderEvent.detail || 'Sending the ticket through the desk model now.',
      chips: [
        { label: 'Source', value: sourceValue },
        { label: 'Ticket', value: 'Ready' },
        { label: 'Route', value: 'Submitting' },
        { label: 'Book', value: 'Awaiting open' },
      ],
    }
  }

  if (scopedLastOrderEvent?.state === 'rejected') {
    return {
      key: 'rejected',
      label: 'Rejected',
      tone: executionTone('rejected'),
      detail: scopedLastOrderEvent.detail || 'The last order was not accepted by the desk.',
      chips: [
        { label: 'Source', value: sourceValue },
        { label: 'Ticket', value: canOpenTrade ? 'Ready' : 'Locked' },
        { label: 'Route', value: 'Rejected' },
        { label: 'Book', value: 'No live order' },
      ],
    }
  }

  if (scopedLastOrderEvent?.state === 'working') {
    return {
      key: scopedLastOrderEvent.bookState || 'pending',
      label: scopedLastOrderEvent.label || 'Working',
      tone: executionTone(scopedLastOrderEvent.bookState || 'working'),
      detail:
        scopedLastOrderEvent.detail ||
        'The most recent order is working on the desk.',
      chips: [
        { label: 'Source', value: sourceValue },
        { label: 'Ticket', value: formatOrderTypeLabel(orderType) },
        { label: 'Route', value: scopedLastOrderEvent.routeLabel || 'Accepted' },
        { label: 'Book', value: scopedLastOrderEvent.bookLabel || 'Pending' },
      ],
    }
  }

  if (scopedLastOrderEvent?.state === 'open') {
    return {
      key: scopedLastOrderEvent.bookState || 'open',
      label: scopedLastOrderEvent.label || 'Open',
      tone: executionTone(scopedLastOrderEvent.bookState || 'open'),
      detail:
        scopedLastOrderEvent.detail ||
        'The most recent order opened a live position on the desk.',
      chips: [
        { label: 'Source', value: sourceValue },
        { label: 'Ticket', value: formatOrderTypeLabel(orderType) },
        { label: 'Route', value: scopedLastOrderEvent.routeLabel || 'Accepted' },
        { label: 'Book', value: scopedLastOrderEvent.bookLabel || 'Live position' },
      ],
    }
  }

  if (scopedLastOrderEvent?.state === 'canceled') {
    return {
      key: 'canceled',
      label: scopedLastOrderEvent.label || 'Canceled',
      tone: executionTone('canceled'),
      detail: scopedLastOrderEvent.detail || 'The working order was canceled.',
      chips: [
        { label: 'Source', value: sourceValue },
        { label: 'Ticket', value: 'Ready' },
        { label: 'Route', value: scopedLastOrderEvent.routeLabel || 'Canceled' },
        { label: 'Book', value: 'Flat' },
      ],
    }
  }

  if (
    pendingOrder &&
    String(pendingOrder?.ticker || '').trim().toUpperCase() === normalizedTicker
  ) {
    const remainingContracts = formatShares(
      pendingOrder.remaining_contracts ?? pendingOrder.suggested_contracts,
    )
    const workingUnitLabel = formatUnitLabel(
      pendingOrder.instrument_type,
      toNumber(pendingOrder.remaining_contracts ?? pendingOrder.suggested_contracts),
    )
    const workingPrice =
      pendingOrder.order_type === 'limit'
        ? formatOptionalPrice(pendingOrder.limit_price)
        : pendingOrder.order_type === 'stop_market' || pendingOrder.order_type === 'stop_limit'
          ? formatOptionalPrice(pendingOrder.stop_price)
          : pendingOrder.order_type === 'trailing_stop'
            ? formatPercent(pendingOrder.trailing_percent, 1)
            : formatOptionalPrice(pendingOrder.live_price_at_submit)
    return {
      key: pendingOrder.order_status || pendingOrder.book_state || 'working',
      label: formatOrderLifecycleValue(
        pendingOrder.order_status || pendingOrder.book_state || 'working',
        'Working',
      ),
      tone: executionTone(pendingOrder.order_status || pendingOrder.book_state || 'working'),
      detail:
        `Working ${formatOrderTypeLabel(pendingOrder.order_type || orderType)} order for ${remainingContracts} ${workingUnitLabel}.` +
        (workingPrice !== '--' ? ` Guide ${workingPrice}.` : ''),
      chips: [
        { label: 'Source', value: sourceValue },
        {
          label: 'Ticket',
          value: formatOrderTypeLabel(pendingOrder.order_type || orderType),
        },
        {
          label: 'Route',
          value: formatOrderLifecycleValue(pendingOrder.route_state, 'Accepted'),
        },
        {
          label: 'Book',
          value: formatOrderLifecycleValue(
            pendingOrder.book_state || pendingOrder.order_status,
            'Pending',
          ),
        },
      ],
    }
  }

  if (scopedBackendOrderEvent) {
    const eventStatus =
      scopedBackendOrderEvent.book_state ||
      scopedBackendOrderEvent.route_state ||
      scopedBackendOrderEvent.status
    return {
      key: eventStatus || 'recorded',
      label: formatOrderLifecycleLabel(scopedBackendOrderEvent),
      tone: executionTone(eventStatus || 'waiting'),
      detail:
        scopedBackendOrderEvent.detail ||
        'The most recent backend-tracked order lifecycle event is available below.',
      chips: [
        { label: 'Source', value: sourceValue },
        {
          label: 'Ticket',
          value: formatOrderTypeLabel(scopedBackendOrderEvent.order_type || orderType),
        },
        {
          label: 'Route',
          value: formatOrderLifecycleValue(scopedBackendOrderEvent.route_state, 'Recorded'),
        },
        {
          label: 'Book',
          value:
            scopedBackendOrderEvent.status === 'closed'
              ? 'Flat'
              : formatOrderLifecycleValue(
                  scopedBackendOrderEvent.book_state || scopedBackendOrderEvent.status,
                  'Live position',
                ),
        },
      ],
    }
  }

  if (!normalizedTicker || !String(reportTicker || '').trim().toUpperCase() || activeExecutionPrice === null) {
    return {
      key: 'waiting',
      label: 'Waiting',
      tone: executionTone('waiting'),
      detail: 'Waiting for a live price and a ready ticket before routing the order.',
      chips: [
        { label: 'Source', value: sourceValue },
        { label: 'Ticket', value: 'Waiting' },
        { label: 'Route', value: 'Awaiting send' },
        { label: 'Book', value: 'No live order' },
      ],
    }
  }

  if (!canOpenTrade) {
    return {
      key: 'locked',
      label: 'Locked',
      tone: executionTone('locked'),
      detail: positionStatusText || 'The current setup has not cleared the desk checks yet.',
      chips: [
        { label: 'Source', value: sourceValue },
        { label: 'Ticket', value: 'Locked' },
        { label: 'Route', value: 'Awaiting send' },
        { label: 'Book', value: 'No live order' },
      ],
    }
  }

  if (selectedChartPoint) {
    return {
      key: 'staged',
      label: 'Staged',
      tone: executionTone('staged'),
      detail: `Chart-picked ${formatPrice(activeExecutionPrice)} is loaded into the ${formatOrderTypeLabel(orderType)} ticket.`,
      chips: [
        { label: 'Source', value: 'Chart staged' },
        { label: 'Ticket', value: 'Ready' },
        { label: 'Route', value: 'Awaiting send' },
        { label: 'Book', value: formatTimeInForceLabel(timeInForce) },
      ],
    }
  }

  return {
    key: 'ready',
    label: 'Ready',
    tone: executionTone('ready'),
    detail: `Live ${formatOrderTypeLabel(orderType)} ticket is aligned and ready to send.`,
    chips: [
      { label: 'Source', value: 'Live price' },
      { label: 'Ticket', value: 'Ready' },
      { label: 'Route', value: 'Awaiting send' },
      { label: 'Book', value: formatTimeInForceLabel(timeInForce) },
    ],
  }
}

function describeMonitorOrderState(row) {
  const status = String(row?.status || '').trim().toLowerCase()
  const action = String(row?.monitor_action || row?.trade_decision || '').trim().toLowerCase()
  const unrealizedPnl = toNumber(row?.unrealized_pnl)

  if (status.includes('reject') || status.includes('error')) {
    return {
      label: 'Rejected',
      tone: executionTone('rejected'),
      detail: 'Ticket was not accepted.',
    }
  }

  if (status.includes('close') || status.includes('exit')) {
    return {
      label: 'Closed',
      tone: executionTone('closed'),
      detail: 'Position is no longer live.',
    }
  }

  if (action.includes('take profit') || action.includes('trim') || action.includes('scale')) {
    return {
      label: 'Exit watch',
      tone: executionTone('exit_watch'),
      detail: 'A profit-taking ladder is active.',
    }
  }

  if (action.includes('cut loss') || action.includes('stop')) {
    return {
      label: 'Risk watch',
      tone: executionTone('risk_watch'),
      detail: 'Risk protection is active on the open position.',
    }
  }

  if (unrealizedPnl !== null && unrealizedPnl > 0) {
    return {
      label: 'Open',
      tone: executionTone('open'),
      detail: 'Position is live and trading above entry.',
    }
  }

  if (unrealizedPnl !== null && unrealizedPnl < 0) {
    return {
      label: 'Open',
      tone: executionTone('waiting'),
      detail: 'Position is live and trading below entry.',
    }
  }

  return {
    label: 'Open',
    tone: executionTone('open'),
    detail: 'Position is live on the desk.',
  }
}

function percentageDelta(current, baseline) {
  const currentNumber = toNumber(current)
  const baselineNumber = toNumber(baseline)
  if (currentNumber === null || baselineNumber === null || baselineNumber === 0) return null
  return ((currentNumber - baselineNumber) / baselineNumber) * 100
}

function hashLabel(value) {
  let hash = 0
  for (const character of String(value || '')) {
    hash = (hash * 31 + character.charCodeAt(0)) >>> 0
  }
  return hash
}

function tickerAccent(ticker) {
  if (!ticker) return '#565656'
  return tickerAccentPalette[hashLabel(String(ticker).toUpperCase()) % tickerAccentPalette.length]
}

function overlayAccent(name) {
  if (namedOverlayPalette[name]) return namedOverlayPalette[name]
  return overlayAccentPalette[hashLabel(name) % overlayAccentPalette.length]
}

function overlayLabel(name) {
  return namedOverlayLabels[name] || String(name || '').replaceAll('_', ' ')
}

function hexToRgba(hex, alpha) {
  const normalized = String(hex || '').replace('#', '')
  if (normalized.length !== 6) return `rgba(86, 86, 86, ${alpha})`
  const red = Number.parseInt(normalized.slice(0, 2), 16)
  const green = Number.parseInt(normalized.slice(2, 4), 16)
  const blue = Number.parseInt(normalized.slice(4, 6), 16)
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`
}

function formatClock(value) {
  if (!value) return 'waiting for first sync'
  const parsed = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(parsed.getTime())) return 'time unavailable'
  return parsed.toLocaleTimeString([], {
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
  })
}

function formatEventTime(value) {
  if (!value) return '--'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value)
  return parsed.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function getMarketClockParts(date = new Date()) {
  const parts = Object.fromEntries(
    marketClockFormatter
      .formatToParts(date)
      .filter((part) => part.type !== 'literal')
      .map((part) => [part.type, part.value]),
  )
  return {
    weekday: parts.weekday || '',
    hour: Number(parts.hour),
    minute: Number(parts.minute),
  }
}

function getSessionLabel(date = new Date()) {
  const { weekday, hour, minute } = getMarketClockParts(date)
  if (weekday === 'Sat' || weekday === 'Sun') return 'Market closed'
  const minutes = hour * 60 + minute
  if (minutes >= 4 * 60 && minutes < 9 * 60 + 30) return 'Premarket'
  if (minutes >= 9 * 60 + 30 && minutes < 16 * 60) return 'Regular'
  if (minutes >= 16 * 60 && minutes < 20 * 60) return 'After-hours'
  return 'Market closed'
}

function inferInstrumentLabel(ticker) {
  const symbol = String(ticker || '').trim().toUpperCase()
  if (!symbol) return 'US listed'
  return knownEtfTickers.has(symbol) ? 'ETF' : 'Equity'
}

function inferVenueLabel(quote, trade, provider = '') {
  const venue =
    quote?.exchange ||
    quote?.bid_exchange ||
    quote?.ask_exchange ||
    trade?.exchange ||
    trade?.tape
  if (venue) return String(venue).toUpperCase()
  if (provider) return String(provider).toUpperCase()
  return 'US market'
}

function midpoint(low, high) {
  const lowNumber = toNumber(low)
  const highNumber = toNumber(high)
  if (lowNumber === null || highNumber === null) return null
  return (lowNumber + highNumber) / 2
}

function midFromQuote(quote) {
  const bidPrice = toNumber(quote?.bid_price)
  const askPrice = toNumber(quote?.ask_price)
  if (bidPrice === null || askPrice === null) return null
  return (bidPrice + askPrice) / 2
}

function toEpochMilliseconds(value) {
  if (!value) return null
  if (value instanceof Date) {
    const timestamp = value.getTime()
    return Number.isFinite(timestamp) ? timestamp : null
  }
  const timestamp = new Date(value).getTime()
  return Number.isFinite(timestamp) ? timestamp : null
}

function resolveFreshestDeskPrice({ trade, quote, liveBatchEntry, analysis }) {
  const candidates = []

  const tradePrice = toNumber(trade?.price)
  if (tradePrice !== null && tradePrice > 0) {
    candidates.push({
      source: 'trade',
      price: tradePrice,
      timestamp: toEpochMilliseconds(trade?.timestamp),
      priority: 4,
    })
  }

  const quoteMid = midFromQuote(quote)
  if (quoteMid !== null && quoteMid > 0) {
    candidates.push({
      source: 'quote',
      price: quoteMid,
      timestamp: toEpochMilliseconds(quote?.timestamp),
      priority: 3,
    })
  }

  const liveBatchPrice = toNumber(liveBatchEntry?.price)
  if (liveBatchPrice !== null && liveBatchPrice > 0) {
    candidates.push({
      source: 'live-batch',
      price: liveBatchPrice,
      timestamp: toEpochMilliseconds(liveBatchEntry?.timestamp),
      priority: 2,
    })
  }

  const analysisPrice = toNumber(analysis?.live_price ?? analysis?.report?.live_price ?? analysis?.report?.close)
  if (analysisPrice !== null && analysisPrice > 0) {
    candidates.push({
      source: 'analysis',
      price: analysisPrice,
      timestamp: null,
      priority: 1,
    })
  }

  if (!candidates.length) {
    return { source: 'none', price: null, timestamp: null }
  }

  candidates.sort((left, right) => {
    const leftTimestamp = left.timestamp ?? -1
    const rightTimestamp = right.timestamp ?? -1
    if (leftTimestamp !== rightTimestamp) return rightTimestamp - leftTimestamp
    return right.priority - left.priority
  })

  return candidates[0]
}

function intervalToMilliseconds(interval) {
  const intervalMap = {
    '1m': 60 * 1000,
    '5m': 5 * 60 * 1000,
    '15m': 15 * 60 * 1000,
    '30m': 30 * 60 * 1000,
    '1h': 60 * 60 * 1000,
    '4h': 4 * 60 * 60 * 1000,
    '1d': 24 * 60 * 60 * 1000,
  }
  return intervalMap[interval] || intervalMap['5m']
}

function appendTickerHistory(history, price, timestamp) {
  const numericPrice = toNumber(price)
  if (numericPrice === null) return Array.isArray(history) ? history : []

  const nextHistory = Array.isArray(history) ? [...history] : []
  const lastPoint = nextHistory[nextHistory.length - 1]
  if (lastPoint && toNumber(lastPoint.price) === numericPrice) {
    nextHistory[nextHistory.length - 1] = {
      price: numericPrice,
      timestamp: timestamp || lastPoint.timestamp || null,
    }
    return nextHistory.slice(-WATCHLIST_HISTORY_LIMIT)
  }

  nextHistory.push({
    price: numericPrice,
    timestamp: timestamp || null,
  })
  return nextHistory.slice(-WATCHLIST_HISTORY_LIMIT)
}

function buildWatchlistPreviewSeries(row, history = []) {
  const liveHistory = (Array.isArray(history) ? history : [])
    .map((point) => toNumber(point?.price ?? point))
    .filter((value) => value !== null && value > 0)

  if (liveHistory.length >= 2) {
    return liveHistory.slice(-WATCHLIST_HISTORY_LIMIT)
  }

  const fallbackSeries = [
    toNumber(row?.close),
    toNumber(row?.live_price ?? row?.current_underlying_price ?? row?.close),
  ].filter((value) => value !== null && value > 0)

  return fallbackSeries.length >= 2 ? fallbackSeries : liveHistory
}

function buildChartSeedSeries(series = [], anchorPrice = null, minimumPoints = 32) {
  const normalizedSeries = (Array.isArray(series) ? series : [])
    .map((value) => toNumber(value))
    .filter((value) => value !== null && value > 0)

  if (normalizedSeries.length >= minimumPoints) {
    return normalizedSeries.slice(-minimumPoints)
  }

  const seedPrice = toNumber(anchorPrice) ?? normalizedSeries.at(-1) ?? normalizedSeries[0] ?? null
  if (seedPrice === null || seedPrice <= 0) {
    return normalizedSeries
  }

  const nextSeries = [...normalizedSeries]
  while (nextSeries.length < minimumPoints) {
    nextSeries.unshift(Number(seedPrice.toFixed(4)))
  }
  return nextSeries
}

function buildDeskFallbackChartPayload({ ticker, interval = '5m', row = null }) {
  const normalizedTicker = String(ticker || '').trim().toUpperCase()
  const normalizedInterval = String(interval || '5m').trim().toLowerCase()
  const anchorPrice = toNumber(row?.live_price ?? row?.current_underlying_price ?? row?.close)
  const previewSeries = buildChartSeedSeries(
    buildWatchlistPreviewSeries(row, row?.history),
    anchorPrice,
  )
  if (previewSeries.length < 2) return null

  const intervalMs = intervalToMilliseconds(normalizedInterval)
  const now = Date.now()
  const candles = previewSeries
    .map((value, index) => {
      const close = toNumber(value)
      const open = toNumber(previewSeries[index - 1]) ?? close
      if (close === null || open === null) return null
      return {
        datetime: new Date(now - intervalMs * (previewSeries.length - 1 - index)).toISOString(),
        open,
        high: Math.max(open, close),
        low: Math.min(open, close),
        close,
        volume: 0,
      }
    })
    .filter(Boolean)

  if (candles.length < 2) return null

  return {
    ticker: normalizedTicker,
    interval: normalizedInterval,
    period: 'fallback',
    extended_hours: true,
    point_count: candles.length,
    candles,
    overlays: {},
    available_indicators: [],
    forecast_framing: buildFallbackForecastFraming(normalizedInterval, 5),
    event_context: resolveEventContext(row?.event_context, row),
    freshness: {
      ticker: normalizedTicker,
      interval: normalizedInterval,
      status: 'warning',
      warning: true,
      stale: false,
      feed_expected: false,
      session: 'unknown',
      session_label: 'Fallback',
      latest_bar_at: candles.at(-1)?.datetime || null,
      latest_bar_age_seconds: null,
      latest_bar_age_minutes: null,
      warning_threshold_seconds: 0,
      stale_threshold_seconds: 0,
      point_count: candles.length,
      source: 'desk-fallback',
      checked_at: new Date().toISOString(),
      checked_at_et: null,
      message: 'Using watchlist fallback data while the chart endpoint is unavailable.',
    },
  }
}

function buildDeskFallbackAnalysis({ ticker, interval = '5m', horizon = 5, row = null }) {
  const normalizedTicker = String(ticker || '').trim().toUpperCase()
  const normalizedInterval = String(interval || '5m').trim().toLowerCase()
  const livePrice = toNumber(row?.live_price ?? row?.current_underlying_price ?? row?.close)
  if (!normalizedTicker || livePrice === null) return null
  const forecastFraming = buildFallbackForecastFraming(normalizedInterval, horizon)

  return {
    settings: {
      ticker: normalizedTicker,
      interval: normalizedInterval,
      horizon: Number(horizon) || 5,
    },
    forecast_framing: forecastFraming,
    report: {
      ticker: normalizedTicker,
      interval: normalizedInterval,
      close: toNumber(row?.close) ?? livePrice,
      live_price: livePrice,
      verdict: row?.verdict || row?.trade_decision || 'Watching',
      trade_decision: row?.trade_decision || row?.verdict || 'Monitor',
      probability_up: toNumber(row?.probability_up),
      setup_score: toNumber(row?.setup_score),
      event_risk: Boolean(row?.event_risk),
      event_label: String(row?.event_label || '').trim(),
      event_reason: String(row?.event_reason || '').trim(),
      next_event_name: String(row?.next_event_name || '').trim(),
      next_event_date: String(row?.next_event_date || '').trim(),
      event_context: resolveEventContext(row?.event_context, row),
      forecast_framing: forecastFraming,
      option_plan: {
        entry_low_price: toNumber(row?.entry_low_price),
        entry_high_price: toNumber(row?.entry_high_price),
        expected_underlying_target: toNumber(row?.target_price),
        invalidation_price: toNumber(row?.stop_loss ?? row?.stop_price),
        recommended_contract: null,
      },
    },
  }
}

function hasUsableDeskRow(row) {
  if (!row || typeof row !== 'object') return false
  const livePrice = toNumber(row?.live_price ?? row?.current_underlying_price ?? row?.close)
  if (livePrice !== null && livePrice > 0) return true
  if (Array.isArray(row?.history)) {
    return row.history.some((point) => {
      const value = toNumber(point?.price ?? point)
      return value !== null && value > 0
    })
  }
  return false
}

function mergeDeskRow(baseRow = null, liveEntry = null) {
  if (!baseRow && !liveEntry) return null
  const merged = {
    ...(baseRow || {}),
    live_price: liveEntry?.price ?? baseRow?.live_price ?? baseRow?.current_underlying_price ?? baseRow?.close ?? null,
    bid_price: liveEntry?.bid_price ?? baseRow?.bid_price ?? null,
    ask_price: liveEntry?.ask_price ?? baseRow?.ask_price ?? null,
    spread: liveEntry?.spread ?? baseRow?.spread ?? null,
    last_trade_at: liveEntry?.timestamp ?? baseRow?.last_trade_at ?? null,
    history: liveEntry?.history ?? baseRow?.history ?? [],
  }
  if (!merged.ticker && liveEntry?.ticker) {
    merged.ticker = liveEntry.ticker
  }
  return merged
}

function sanitizeChartPayloadCandles(payload, fallbackPrice = null) {
  if (!payload || !Array.isArray(payload.candles) || !payload.candles.length) return payload

  let previousClose = null
  const normalizedFallback = toNumber(fallbackPrice)
  const candles = payload.candles.map((candle) => {
    const values = [
      toNumber(candle?.open),
      toNumber(candle?.high),
      toNumber(candle?.low),
      toNumber(candle?.close),
      previousClose,
      normalizedFallback,
    ].filter((value) => value !== null && value > 0)

    const anchor = values[0] ?? null
    if (anchor === null) {
      return candle
    }

    const open = toNumber(candle?.open)
    const high = toNumber(candle?.high)
    const low = toNumber(candle?.low)
    const close = toNumber(candle?.close)

    const safeOpen = open !== null && open > 0 ? open : anchor
    const safeClose = close !== null && close > 0 ? close : anchor
    const safeHighCandidate = high !== null && high > 0 ? high : Math.max(safeOpen, safeClose)
    const safeLowCandidate = low !== null && low > 0 ? low : Math.min(safeOpen, safeClose)
    const safeHigh = Math.max(safeOpen, safeClose, safeHighCandidate)
    const safeLow = Math.min(safeOpen, safeClose, safeLowCandidate)

    previousClose = safeClose

    return {
      ...candle,
      open: safeOpen,
      high: safeHigh,
      low: safeLow,
      close: safeClose,
    }
  })

  return {
    ...payload,
    candles,
    point_count: candles.length,
  }
}

function hasUsableChartPrices(payload) {
  const candles = Array.isArray(payload?.candles) ? payload.candles : []
  return candles.some((candle) => {
    const values = [
      toNumber(candle?.open),
      toNumber(candle?.high),
      toNumber(candle?.low),
      toNumber(candle?.close),
    ].filter((value) => value !== null)
    return values.some((value) => value > 0)
  })
}

function isPlaceholderDeskChartPayload(payload) {
  const candles = Array.isArray(payload?.candles) ? payload.candles : []
  if (!candles.length) return true
  const numericCloses = candles
    .map((candle) => toNumber(candle?.close))
    .filter((value) => value !== null)
  if (!numericCloses.length) return true
  return numericCloses.every((value) => Math.abs(value) < 0.000001)
}

function isPlaceholderDeskAnalysis(payload) {
  const report = payload?.report
  if (!report) return true
  const livePrice = toNumber(payload?.live_price ?? report?.live_price ?? report?.close)
  const verdict = String(report?.verdict || '').trim().toLowerCase()
  const tradeDecision = String(report?.trade_decision || '').trim().toLowerCase()
  if (livePrice === null || Math.abs(livePrice) < 0.000001) return true
  return verdict === 'watching' && tradeDecision === 'monitor'
}

function isUsableAnalysisPayload(payload) {
  return Boolean(payload?.report) && !isPlaceholderDeskAnalysis(payload)
}

function areFallbackAnalysesEquivalent(left, right) {
  if (left === right) return true
  if (!left || !right) return false

  const leftReport = left.report || {}
  const rightReport = right.report || {}
  const fields = [
    String(leftReport.ticker || '').trim().toUpperCase() ===
      String(rightReport.ticker || '').trim().toUpperCase(),
    String(leftReport.interval || '').trim().toLowerCase() ===
      String(rightReport.interval || '').trim().toLowerCase(),
    String(leftReport.verdict || '').trim().toLowerCase() ===
      String(rightReport.verdict || '').trim().toLowerCase(),
    String(leftReport.trade_decision || '').trim().toLowerCase() ===
      String(rightReport.trade_decision || '').trim().toLowerCase(),
    toNumber(leftReport.live_price) === toNumber(rightReport.live_price),
    toNumber(leftReport.close) === toNumber(rightReport.close),
    toNumber(leftReport.probability_up) === toNumber(rightReport.probability_up),
    toNumber(leftReport.setup_score) === toNumber(rightReport.setup_score),
    toNumber(leftReport.option_plan?.entry_low_price) ===
      toNumber(rightReport.option_plan?.entry_low_price),
    toNumber(leftReport.option_plan?.entry_high_price) ===
      toNumber(rightReport.option_plan?.entry_high_price),
    toNumber(leftReport.option_plan?.expected_underlying_target) ===
      toNumber(rightReport.option_plan?.expected_underlying_target),
    toNumber(leftReport.option_plan?.invalidation_price) ===
      toNumber(rightReport.option_plan?.invalidation_price),
  ]

  return fields.every(Boolean)
}

function buildFallbackChartSignature(payload) {
  const candles = Array.isArray(payload?.candles) ? payload.candles : []
  const closes = candles
    .map((candle) => {
      const close = toNumber(candle?.close)
      return close === null ? 'x' : close.toFixed(4)
    })
    .join('|')

  return [
    String(payload?.ticker || '').trim().toUpperCase(),
    String(payload?.interval || '').trim().toLowerCase(),
    String(payload?.freshness?.source || '').trim().toLowerCase(),
    candles.length,
    closes,
  ].join('::')
}

function areFallbackChartPayloadsEquivalent(left, right) {
  if (left === right) return true
  if (!left || !right) return false
  return buildFallbackChartSignature(left) === buildFallbackChartSignature(right)
}

function buildSparklinePath(values, width = 62, height = 18, padding = 2) {
  if (!Array.isArray(values) || values.length < 2) return ''
  const numericValues = values.map((value) => toNumber(value)).filter((value) => value !== null)
  if (numericValues.length < 2) return ''

  const minValue = Math.min(...numericValues)
  const maxValue = Math.max(...numericValues)
  const span = Math.max(maxValue - minValue, Math.abs(maxValue) * 0.001, 0.01)
  const innerWidth = Math.max(width - padding * 2, 1)
  const innerHeight = Math.max(height - padding * 2, 1)

  return numericValues
    .map((value, index) => {
      const x = padding + (innerWidth * index) / Math.max(numericValues.length - 1, 1)
      const y = padding + innerHeight - ((value - minValue) / span) * innerHeight
      return `${index === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`
    })
    .join(' ')
}

function WatchlistSparkline({ values, accent, active = false }) {
  const numericValues = (Array.isArray(values) ? values : [])
    .map((value) => toNumber(value))
    .filter((value) => value !== null)
  const width = 62
  const height = 18
  const path = buildSparklinePath(numericValues, width, height)
  const firstValue = numericValues[0]
  const lastValue = numericValues[numericValues.length - 1]
  const positive = toNumber(lastValue) !== null && toNumber(firstValue) !== null
    ? lastValue >= firstValue
    : true
  const stroke = active ? accent : positive ? '#22c55e' : '#ff6b6b'

  if (!path) {
    return <span className="tv-watchlist-table__sparkline tv-watchlist-table__sparkline--empty" />
  }

  const endpointY = (() => {
    const minValue = Math.min(...numericValues)
    const maxValue = Math.max(...numericValues)
    const span = Math.max(maxValue - minValue, Math.abs(maxValue) * 0.001, 0.01)
    const innerHeight = Math.max(height - 4, 1)
    return 2 + innerHeight - ((lastValue - minValue) / span) * innerHeight
  })()

  return (
    <span className={`tv-watchlist-table__sparkline ${active ? 'tv-watchlist-table__sparkline--active' : ''}`}>
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true">
        <path className="tv-watchlist-table__sparkline-track" d={path} />
        <path className="tv-watchlist-table__sparkline-line" d={path} style={{ stroke }} />
        <circle
          className="tv-watchlist-table__sparkline-end"
          cx={width - 2}
          cy={endpointY}
          r="1.7"
          style={{ fill: stroke }}
        />
      </svg>
    </span>
  )
}

function bucketStartIso(timestamp, interval) {
  const parsed = new Date(timestamp)
  if (Number.isNaN(parsed.getTime())) return null

  if (interval === '1d') {
    return new Date(
      Date.UTC(parsed.getUTCFullYear(), parsed.getUTCMonth(), parsed.getUTCDate()),
    ).toISOString()
  }

  const duration = intervalToMilliseconds(interval)
  const bucketStart = Math.floor(parsed.getTime() / duration) * duration
  return new Date(bucketStart).toISOString()
}

function applyTradeTickToChart(payload, tradeEvent, interval) {
  if (!payload?.candles?.length) return payload

  const tradePrice = toNumber(tradeEvent?.price)
  if (tradePrice === null) return payload

  const candleTimestamp = bucketStartIso(tradeEvent.timestamp, interval)
  if (!candleTimestamp) return payload

  const candles = [...payload.candles]
  const lastCandle = candles[candles.length - 1]
  const lastCandleBucket = bucketStartIso(lastCandle?.datetime, interval)
  const tradeSize = Math.max(toNumber(tradeEvent?.size) || 0, 0)

  if (lastCandleBucket === candleTimestamp) {
    const currentOpen = toNumber(lastCandle.open) ?? tradePrice
    const currentHigh = toNumber(lastCandle.high) ?? tradePrice
    const currentLow = toNumber(lastCandle.low) ?? tradePrice
    const currentVolume = toNumber(lastCandle.volume) ?? 0

    candles[candles.length - 1] = {
      ...lastCandle,
      open: currentOpen,
      high: Math.max(currentHigh, tradePrice),
      low: Math.min(currentLow, tradePrice),
      close: tradePrice,
      volume: currentVolume + tradeSize,
      datetime: candleTimestamp,
    }
  } else if (lastCandleBucket !== null && candleTimestamp > lastCandleBucket) {
    const priorClose = toNumber(lastCandle.close) ?? tradePrice
    candles.push({
      datetime: candleTimestamp,
      open: priorClose,
      high: Math.max(priorClose, tradePrice),
      low: Math.min(priorClose, tradePrice),
      close: tradePrice,
      volume: tradeSize,
    })
  } else {
    return payload
  }

  const trimmedCandles =
    candles.length > payload.candles.length ? candles.slice(-payload.candles.length) : candles

  return sanitizeChartPayloadCandles({
    ...payload,
    candles: trimmedCandles,
    point_count: trimmedCandles.length,
  }, tradePrice)
}

function applyQuoteTickToChart(payload, quoteEvent, interval) {
  if (!payload?.candles?.length) return payload

  const bidPrice = toNumber(quoteEvent?.bid_price)
  const askPrice = toNumber(quoteEvent?.ask_price)
  const quotePrice =
    bidPrice !== null && askPrice !== null
      ? (bidPrice + askPrice) / 2
      : askPrice ?? bidPrice

  if (quotePrice === null) return payload

  const quoteTimestamp = bucketStartIso(quoteEvent?.timestamp || new Date().toISOString(), interval)
  if (!quoteTimestamp) return payload

  const candles = [...payload.candles]
  const lastCandle = candles[candles.length - 1]
  const lastCandleBucket = bucketStartIso(lastCandle?.datetime, interval)

  if (lastCandleBucket === quoteTimestamp) {
    const currentOpen = toNumber(lastCandle.open) ?? quotePrice
    const currentHigh = toNumber(lastCandle.high) ?? quotePrice
    const currentLow = toNumber(lastCandle.low) ?? quotePrice

    candles[candles.length - 1] = {
      ...lastCandle,
      open: currentOpen,
      high: Math.max(currentHigh, quotePrice),
      low: Math.min(currentLow, quotePrice),
      close: quotePrice,
      datetime: quoteTimestamp,
    }
  } else if (lastCandleBucket !== null && quoteTimestamp > lastCandleBucket) {
    const priorClose = toNumber(lastCandle.close) ?? quotePrice
    candles.push({
      ...lastCandle,
      datetime: quoteTimestamp,
      open: priorClose,
      high: Math.max(priorClose, quotePrice),
      low: Math.min(priorClose, quotePrice),
      close: quotePrice,
      volume: 0,
    })
  } else {
    return payload
  }

  const trimmedCandles =
    candles.length > payload.candles.length ? candles.slice(-payload.candles.length) : candles

  return sanitizeChartPayloadCandles({
    ...payload,
    candles: trimmedCandles,
    point_count: trimmedCandles.length,
  }, quotePrice)
}

function deriveTradeStatus(report, livePrice) {
  const price = toNumber(livePrice)
  if (!report || price === null) return 'STREAM WAITING'
  if (report.event_risk) return 'WAIT UNTIL AFTER EVENT'

  const optionPlan = report.option_plan || {}
  const entryLow = toNumber(optionPlan.entry_low_price)
  const entryHigh = toNumber(optionPlan.entry_high_price)
  const targetPrice = toNumber(optionPlan.expected_underlying_target)
  const invalidationPrice = toNumber(optionPlan.invalidation_price)
  const verdict = String(report.verdict || '').toUpperCase()

  if (String(optionPlan.action || '').toUpperCase() === 'WAIT') {
    return 'NO TRADE'
  }

  if (verdict === 'BULLISH') {
    if (targetPrice !== null && price >= targetPrice) return 'TAKE PROFIT'
    if (invalidationPrice !== null && price <= invalidationPrice) return 'CUT LOSS'
  }

  if (verdict === 'BEARISH') {
    if (targetPrice !== null && price <= targetPrice) return 'TAKE PROFIT'
    if (invalidationPrice !== null && price >= invalidationPrice) return 'CUT LOSS'
  }

  if (entryLow !== null && entryHigh !== null && price >= entryLow && price <= entryHigh) {
    return 'ENTER NOW'
  }

  return 'WAIT FOR ENTRY'
}

function deriveExecutionDecision(report, livePrice) {
  const status = deriveTradeStatus(report, livePrice)
  const tradeDecision = String(report?.trade_decision || '').toUpperCase()

  if (report?.event_risk) return 'WAIT UNTIL AFTER EVENT'
  if (tradeDecision === 'REJECT') return 'REJECT'
  if (tradeDecision === 'PASS') return 'PASS'
  if (status === 'ENTER NOW') return 'BUY NOW'
  if (status === 'WAIT FOR ENTRY') return 'WAIT FOR ENTRY'
  if (status === 'TAKE PROFIT') return 'TAKE PROFIT'
  if (status === 'CUT LOSS') return 'CUT LOSS'
  return status
}

function deriveLiveAlerts(report, livePrice) {
  const alerts = []
  const price = toNumber(livePrice)
  if (!report || price === null) return alerts

  const optionPlan = report.option_plan || {}
  const entryLow = toNumber(optionPlan.entry_low_price)
  const entryHigh = toNumber(optionPlan.entry_high_price)
  const targetPrice = toNumber(optionPlan.expected_underlying_target)
  const invalidationPrice = toNumber(optionPlan.invalidation_price)

  if (report.event_risk) alerts.push('EVENT RISK')
  if (entryLow !== null && entryHigh !== null && price >= entryLow && price <= entryHigh) alerts.push('ENTRY ZONE')
  if (toNumber(report.setup_score) !== null && Number(report.setup_score) >= 70) alerts.push('HIGH SCORE SETUP')
  if (['A+ SETUP', 'A SETUP'].includes(String(report.setup_grade || '').toUpperCase())) alerts.push('A-GRADE SETUP')
  if (targetPrice !== null) {
    const bullish = String(report.verdict || '').toUpperCase() === 'BULLISH'
    const bearish = String(report.verdict || '').toUpperCase() === 'BEARISH'
    if ((bullish && price >= targetPrice) || (bearish && price <= targetPrice)) alerts.push('TARGET TOUCHED')
  }
  if (invalidationPrice !== null) {
    const bullish = String(report.verdict || '').toUpperCase() === 'BULLISH'
    const bearish = String(report.verdict || '').toUpperCase() === 'BEARISH'
    if ((bullish && price <= invalidationPrice) || (bearish && price >= invalidationPrice)) alerts.push('INVALIDATION HIT')
  }

  return alerts
}

function describeFeedPill(label) {
  switch (String(label || '').trim().toLowerCase()) {
    case 'loading desk':
      return 'The dashboard is loading the latest saved desk state.'
    case 'realtime feed':
      return 'Live market updates are connected for this chart.'
    case 'internal api feed':
      return 'The owned API is polling free delayed data for paper-only desk updates.'
    case 'feed delayed':
      return 'The live stream is unavailable, so the chart is using the latest stable data.'
    case 'feed standby':
      return 'The feed connection is idle or still connecting.'
    case 'background sync':
      return 'The chart is refreshing in the background instead of streaming live ticks.'
    case 'manual mode':
      return 'The chart updates only when you manually refresh or change inputs.'
    default:
      return ''
  }
}

function describeLiveAlert(alert) {
  switch (String(alert || '').trim().toUpperCase()) {
    case 'EVENT RISK':
      return 'A nearby event may distort the setup, so entries need extra caution.'
    case 'ENTRY ZONE':
      return 'Current price is inside the model preferred entry range.'
    case 'HIGH SCORE SETUP':
      return 'The setup score is high enough to flag stronger model alignment.'
    case 'A-GRADE SETUP':
      return 'The model grade is in the top setup bucket.'
    case 'TARGET TOUCHED':
      return 'Current price has reached the model target level.'
    case 'INVALIDATION HIT':
      return 'Current price has reached the model invalidation level.'
    default:
      return ''
  }
}

function describeEntryAlignment(report, referencePrice) {
  const price = toNumber(referencePrice)
  if (!report) return 'Run analysis to map the model levels onto the chart.'
  if (price === null) return 'Waiting for a live price or chart pick.'

  const optionPlan = report.option_plan || {}
  const entryLow = toNumber(optionPlan.entry_low_price)
  const entryHigh = toNumber(optionPlan.entry_high_price)
  const targetPrice = toNumber(optionPlan.expected_underlying_target)
  const invalidationPrice = toNumber(optionPlan.invalidation_price)
  const verdict = String(report.verdict || '').toUpperCase()

  if (verdict === 'BULLISH') {
    if (targetPrice !== null && price >= targetPrice) return 'Price is already at or above the algorithmic target.'
    if (invalidationPrice !== null && price <= invalidationPrice) return 'Price is under the cut-loss line.'
    if (entryLow !== null && entryHigh !== null && price >= entryLow && price <= entryHigh) return 'Price is inside the preferred long entry zone.'
    if (entryHigh !== null && price > entryHigh) return 'Price is extended above the preferred long entry zone.'
    if (entryLow !== null && price < entryLow) return 'Price is below the preferred long entry zone.'
  }

  if (verdict === 'BEARISH') {
    if (targetPrice !== null && price <= targetPrice) return 'Price is already at or below the algorithmic target.'
    if (invalidationPrice !== null && price >= invalidationPrice) return 'Price is above the cut-loss line.'
    if (entryLow !== null && entryHigh !== null && price >= entryLow && price <= entryHigh) return 'Price is inside the preferred short entry zone.'
    if (entryHigh !== null && price > entryHigh) return 'Price is above the preferred short entry zone.'
    if (entryLow !== null && price < entryLow) return 'Price is below the preferred short entry zone.'
  }

  return 'The model is waiting for a cleaner setup.'
}

function calculateRiskReward(report, referencePrice) {
  const price = toNumber(referencePrice)
  const targetPrice = toNumber(report?.option_plan?.expected_underlying_target)
  const invalidationPrice = toNumber(report?.option_plan?.invalidation_price)
  const verdict = String(report?.verdict || '').toUpperCase()

  if (price === null || targetPrice === null || invalidationPrice === null) return null

  let reward = null
  let risk = null

  if (verdict === 'BULLISH') {
    reward = targetPrice - price
    risk = price - invalidationPrice
  } else if (verdict === 'BEARISH') {
    reward = price - targetPrice
    risk = invalidationPrice - price
  }

  if (reward === null || risk === null || reward <= 0 || risk <= 0) return null
  return reward / risk
}

function describeStrategyAlignment(strategy, referencePrice) {
  const price = toNumber(referencePrice)
  if (!strategy?.available || price === null) return null

  const upperBand = toNumber(strategy.upper_band)
  const lowerBand = toNumber(strategy.lower_band)
  const activeStop = toMeaningfulNumber(strategy.active_stop)

  if (upperBand !== null && price > upperBand) {
    return `Price is above the breakout band. ${strategy.next_checkpoint ? `The next confirmation is ${strategy.next_checkpoint} ET.` : 'The next checkpoint will confirm the move.'}`
  }

  if (lowerBand !== null && price < lowerBand) {
    return `Price is below the lower noise band. ${strategy.next_checkpoint ? `The next confirmation is ${strategy.next_checkpoint} ET.` : 'The next checkpoint will confirm the move.'}`
  }

  if (activeStop !== null) {
    return `The trade is active with a trailing stop at ${formatPrice(activeStop)}.`
  }

  return strategy.decision || 'The intraday strategy is waiting for a clean checkpoint.'
}

function riskTonePriority(tone) {
  return {
    positive: 0,
    info: 1,
    warning: 2,
    negative: 3,
  }[tone] ?? 1
}

function buildPreTradeRiskChecks({
  report,
  instrumentType,
  optionStrategy,
  positionPreview,
  riskReward,
  quote,
  contract,
  livePrice,
  orderType,
  timeInForce,
  optionRight,
}) {
  const checks = []
  const normalizedInstrumentType = normalizeInstrumentType(instrumentType)
  const tradeDecision = String(report?.trade_decision || '').toUpperCase()
  const eventRisk = Boolean(report?.event_risk)
  const normalizedLivePrice = toNumber(livePrice)
  const normalizedOrderType = String(orderType || 'market').trim().toLowerCase()
  const normalizedTimeInForce = String(timeInForce || 'day').trim().toLowerCase()
  const normalizedOptionRight = String(optionRight || '').trim().toLowerCase()
  const normalizedOptionStrategy = normalizeOptionStrategy(optionStrategy)

  checks.push({
    key: 'decision',
    title: 'Setup',
    value:
      tradeDecision === 'VALID TRADE'
        ? 'Model green light'
        : tradeDecision === 'PASS'
          ? 'Model pass'
          : tradeDecision === 'REJECT'
            ? 'Model reject'
            : formatLabel(tradeDecision || 'review'),
    tone:
      tradeDecision === 'VALID TRADE'
        ? 'positive'
        : tradeDecision === 'PASS'
          ? 'warning'
          : tradeDecision === 'REJECT'
            ? 'negative'
            : 'info',
    detail:
      report?.reject_reason ||
      (tradeDecision === 'VALID TRADE'
        ? 'The model currently supports opening the trade if execution quality is acceptable.'
        : 'Review the setup before routing size.'),
  })

  checks.push({
    key: 'event-risk',
    title: 'Event risk',
    value: eventRisk ? (report?.event_label || 'Event risk live') : 'Clear window',
    tone: eventRisk ? 'negative' : 'positive',
    detail: eventRisk
      ? report?.event_reason || 'A known event can disrupt the setup or widen spreads.'
      : 'No active event blocker is attached to the current setup.',
  })

  checks.push({
    key: 'reward',
    title: 'Risk / reward',
    value: riskReward === null ? 'Not mapped' : `${formatNumber(riskReward, 2)}R`,
    tone:
      riskReward === null ? 'warning' : riskReward >= 2 ? 'positive' : riskReward >= 1 ? 'warning' : 'negative',
    detail:
      riskReward === null
        ? 'The chart needs a valid target and invalidation to compute a clean payoff ratio.'
        : riskReward >= 2
          ? 'The projected reward is at least two times the mapped risk.'
          : riskReward >= 1
            ? 'The setup pays more than it risks, but not by a large margin.'
            : 'The mapped reward is smaller than the mapped risk.',
  })

  checks.push({
    key: 'sizing',
    title: 'Sizing',
    value: positionPreview?.suggestedContracts > 0 ? `${formatShares(positionPreview.suggestedContracts)} ${positionPreview.unitLabel}` : 'Blocked',
    tone:
      !positionPreview || !positionPreview.affordable || positionPreview.suggestedContracts <= 0
        ? 'negative'
        : positionPreview.riskBudgetMultiplier < 0.95
          ? 'warning'
          : 'positive',
    detail:
      positionPreview?.statusText ||
      'Sizing will turn on once the ticket has a valid price, risk budget, and stop structure.',
  })

  if (normalizedInstrumentType === 'listed_option') {
    const expiration = contract?.expiration
    const dte = daysUntilExpiration(expiration)
    checks.push({
      key: 'shape',
      title: 'Risk shape',
      value: describeOptionStrategyShape(normalizedOptionRight, normalizedOptionStrategy),
      tone: normalizedOptionStrategy === 'long_option' ? 'positive' : 'warning',
      detail:
        normalizedOptionStrategy === 'short_premium'
          ? 'Premium-selling tickets are review-only until margin expansion, assignment, and buy-to-close controls are wired into the route.'
          : normalizedOptionStrategy === 'vertical_spread'
            ? 'Vertical spreads need a complete two-leg payload and net debit/credit validation before this desk can submit them.'
            : 'This is a defined-risk debit option structure. The maximum loss is the premium outlay instead of uncapped short-option exposure.',
    })
    checks.push({
      key: 'assignment',
      title: 'Exercise / assignment',
      value:
        normalizedOptionStrategy === 'short_premium'
          ? 'Assignment risk'
          : dte === null
            ? 'Review expiry'
            : dte <= 2
              ? `${dte} DTE`
              : 'No short assignment',
      tone: normalizedOptionStrategy === 'short_premium' || (dte !== null && dte <= 2) ? 'warning' : 'positive',
      detail:
        normalizedOptionStrategy === 'short_premium'
          ? 'Short premium can create early assignment and margin pressure, so submit is blocked until those controls exist.'
          : dte !== null && dte <= 2
          ? `This contract expires in ${dte} day${dte === 1 ? '' : 's'}. Long options avoid short-assignment risk, but near-expiry exercise and liquidity behavior need closer review.`
          : 'This desk flow only opens long listed options, so there is no short-option assignment risk. Watch expiry and exercise decisions as the contract matures.',
    })
    checks.push({
      key: 'capital-style',
      title: 'Capital style',
      value:
        normalizedOptionStrategy === 'short_premium'
          ? 'Margin credit'
          : normalizedOptionStrategy === 'vertical_spread'
            ? 'Net spread'
            : 'Premium debit',
      tone: normalizedOptionStrategy === 'long_option' ? 'positive' : 'warning',
      detail:
        normalizedOptionStrategy === 'short_premium'
          ? 'Credit strategies need margin and assignment models, so this structure is kept in review mode.'
          : normalizedOptionStrategy === 'vertical_spread'
            ? 'Spread risk depends on both legs, net premium, and width. This ticket does not route multi-leg orders yet.'
            : 'Capital at risk is driven by premium paid and option liquidity, not by full notional stock value or short-option margin expansion.',
    })
  } else {
    checks.push({
      key: 'shape',
      title: 'Risk shape',
      value: 'Linear spot',
      tone: 'warning',
      detail: 'Cash equity exposure is linear. There is no option premium cap, so downside runs with the stock until the invalidation or a gap through it.',
    })
    checks.push({
      key: 'assignment',
      title: 'Assignment',
      value: 'Not applicable',
      tone: 'positive',
      detail: 'Straight equity tickets do not carry option exercise or assignment mechanics, but they still carry overnight gap and borrow constraints if shorting is added later.',
    })
    checks.push({
      key: 'capital-style',
      title: 'Capital style',
      value: 'Cash notional',
      tone: 'warning',
      detail: 'Equity tickets consume cash notional directly, so the position cost and overnight gap exposure matter more than premium-style debit math.',
    })
  }

  if (normalizedInstrumentType === 'listed_option') {
    const spreadPct = toNumber(contract?.spread_pct)
    const volume = toNumber(contract?.volume)
    const openInterest = toNumber(contract?.open_interest)
    const contractAvailable = Boolean(contract?.contract_symbol)

    let liquidityTone = 'info'
    if (!contractAvailable) {
      liquidityTone = 'negative'
    } else {
      const spreadTone =
        spreadPct === null ? 'warning' : spreadPct <= 6 ? 'positive' : spreadPct <= 12 ? 'warning' : 'negative'
      const volumeTone =
        volume === null ? 'warning' : volume >= 250 ? 'positive' : volume >= 50 ? 'warning' : 'negative'
      const oiTone =
        openInterest === null
          ? 'warning'
          : openInterest >= 1000
            ? 'positive'
            : openInterest >= 250
              ? 'warning'
              : 'negative'
      liquidityTone = [spreadTone, volumeTone, oiTone].sort(
        (left, right) => riskTonePriority(right) - riskTonePriority(left),
      )[0]
    }

    checks.push({
      key: 'liquidity',
      title: 'Option liquidity',
      value: contractAvailable
        ? `${formatPercent(spreadPct, 1)} spread`
        : 'No contract',
      tone: liquidityTone,
      detail: contractAvailable
        ? `Vol ${formatCompact(volume)} | OI ${formatCompact(openInterest)}. Wide spreads or thin open interest can distort fills.`
        : 'The model has not surfaced a contract yet, so the option ticket cannot be reviewed for execution quality.',
    })
  } else {
    const bid = toNumber(quote?.bid_price)
    const ask = toNumber(quote?.ask_price)
    const rawSpread = resolveDisplaySpread(quote?.spread, bid, ask)
    const spreadPct =
      normalizedLivePrice !== null && normalizedLivePrice > 0 && rawSpread !== null
        ? (rawSpread / normalizedLivePrice) * 100
        : null
    const bidSize = toNumber(quote?.bid_size)
    const askSize = toNumber(quote?.ask_size)
    checks.push({
      key: 'liquidity',
      title: 'Equity liquidity',
      value: rawSpread === null ? 'Quote waiting' : `${formatPrice(rawSpread)} spread`,
      tone:
        rawSpread === null
          ? 'warning'
          : spreadPct !== null && spreadPct <= 0.05
            ? 'positive'
            : spreadPct !== null && spreadPct <= 0.15
              ? 'warning'
              : 'negative',
      detail:
        rawSpread === null
          ? 'Waiting for a live bid/ask quote to judge how tight the equity book is.'
          : `Bid ${formatPrice(bid)} x ${formatCompact(bidSize)} | Ask ${formatPrice(ask)} x ${formatCompact(askSize)}. Wider spreads increase entry slippage.`,
    })
  }

  let routingTone = 'positive'
  let routingValue = `${formatOrderTypeLabel(normalizedOrderType)} | ${formatTimeInForceLabel(normalizedTimeInForce)}`
  let routingDetail = 'The current routing instructions are aligned with the desk defaults.'

  if (normalizedTimeInForce === 'day_ext' && normalizedInstrumentType === 'listed_option') {
    routingTone = 'negative'
    routingValue = 'Regular hours only'
    routingDetail = 'Listed option tickets should stay on regular-hours routing. After-hours option liquidity is not supported in this desk flow.'
  } else if (normalizedTimeInForce === 'day_ext' && normalizedOrderType === 'market') {
    routingTone = 'negative'
    routingValue = 'Use a limit'
    routingDetail = 'Extended-hours market orders are blocked because spreads can widen sharply outside the regular session.'
  } else if (normalizedInstrumentType === 'listed_option' && normalizedOrderType === 'trailing_stop') {
    routingTone = 'warning'
    routingValue = 'Quote-sensitive'
    routingDetail = 'Trailing stops on listed options can move on quote noise instead of true trade prints.'
  } else if (normalizedOrderType === 'market') {
    routingTone = 'warning'
    routingValue = 'Fast route'
    routingDetail = 'Market orders trade execution certainty for price control. Use them only when the book is tight.'
  } else if (normalizedTimeInForce === 'day_ext') {
    routingTone = 'warning'
    routingValue = 'Thin session'
    routingDetail = 'Extended-hours routing can thin out the book and increase slippage even with a limit price.'
  }

  checks.push({
    key: 'routing',
    title: 'Routing',
    value: routingValue,
    tone: routingTone,
    detail: routingDetail,
  })

  return checks
}

function buildTradeGuardrails({
  report,
  instrumentType,
  positionPreview,
  riskReward,
  quote,
  contract,
  livePrice,
  orderType,
  timeInForce,
  optionRight,
  optionStrategy,
  capitalPreservationPolicy,
  capitalPreservationSummary,
  reviewLoopTicketGuardrail,
  intradayExecutionPlan,
  strictRouteGuards = true,
}) {
  const blockingReasons = []
  const warningReasons = []
  const addBlockingReason = (message, targetKey, actionLabel) => {
    blockingReasons.push(createGuardrailReason(message, targetKey, actionLabel))
  }
  const addWarningReason = (message, targetKey, actionLabel) => {
    warningReasons.push(createGuardrailReason(message, targetKey, actionLabel))
  }
  const normalizedInstrumentType = normalizeInstrumentType(instrumentType)
  const normalizedOrderType = String(orderType || 'market').trim().toLowerCase()
  const normalizedTimeInForce = String(timeInForce || 'day').trim().toLowerCase()
  const normalizedLivePrice = toNumber(livePrice)
  const normalizedOptionRight = String(optionRight || '').trim().toLowerCase()
  const normalizedOptionStrategy = normalizeOptionStrategy(optionStrategy)
  const preservationPolicy = capitalPreservationPolicy || { enabled: false }
  const preservationSummary = capitalPreservationSummary || { enabled: false }
  const reviewGuardrail = reviewLoopTicketGuardrail || { blocker: null, warning: null }
  const executionPlan = intradayExecutionPlan || null

  if (executionPlan) {
    if (!executionPlan.allowsNewEntries) {
      if (strictRouteGuards) {
        addBlockingReason(
          executionPlan.description,
          'time-in-force',
          executionPlan.cleanupOnly ? 'Manage active risk' : 'Wait for session',
        )
      } else {
        addWarningReason(
          executionPlan.description,
          'time-in-force',
          executionPlan.cleanupOnly ? 'Manage active risk' : 'Review session',
        )
      }
    }

    if (executionPlan.orderTone === 'negative') {
      if (strictRouteGuards) {
        addBlockingReason(
          executionPlan.orderDetail,
          normalizedTimeInForce === 'gtc_90d' ? 'time-in-force' : 'order-type',
          normalizedTimeInForce === 'gtc_90d' ? 'Use a day order' : 'Choose a priced order',
        )
      } else {
        addWarningReason(
          executionPlan.orderDetail,
          normalizedTimeInForce === 'gtc_90d' ? 'time-in-force' : 'order-type',
          normalizedTimeInForce === 'gtc_90d' ? 'Review time in force' : 'Review order posture',
        )
      }
    } else if (executionPlan.orderTone === 'warning') {
      addWarningReason(
        executionPlan.orderDetail,
        'order-type',
        'Review order posture',
      )
    }

    if (executionPlan.riskTone === 'negative') {
      if (strictRouteGuards) {
        addBlockingReason(
          executionPlan.riskDetail,
          'risk-percent',
          'Reduce risk budget',
        )
      } else {
        addWarningReason(
          executionPlan.riskDetail,
          'risk-percent',
          'Review risk budget',
        )
      }
    } else if (executionPlan.riskTone === 'warning') {
      addWarningReason(
        executionPlan.riskDetail,
        'risk-percent',
        'Tighten risk budget',
      )
    }
  }

  if (preservationPolicy.enabled) {
    const positionCost = toNumber(positionPreview?.totalPositionCost)

    if (preservationPolicy.equitiesOnly && normalizedInstrumentType !== 'equity') {
      addBlockingReason(
        'Capital preservation mode only allows equity tickets.',
        'contract-summary',
        'Switch instrument',
      )
    }

    if (preservationPolicy.limitOrdersOnly && normalizedOrderType !== 'limit') {
      addBlockingReason(
        'Capital preservation mode only allows limit orders.',
        'order-type',
        'Change order type',
      )
    }

    if (preservationPolicy.regularHoursOnly && normalizedTimeInForce === 'day_ext') {
      addBlockingReason(
        'Capital preservation mode blocks extended-hours routing.',
        'time-in-force',
        'Switch time in force',
      )
    }

    if (preservationPolicy.longOnly && normalizedInstrumentType === 'listed_option') {
      if (normalizedOptionStrategy !== 'long_option') {
        addBlockingReason(
          'Long-only mode only allows buy-to-open long option tickets.',
          'option-structure',
          'Choose long option',
        )
      } else if (normalizedOptionRight === 'put') {
        addBlockingReason(
          'Long-only mode blocks bearish PUT option tickets.',
          'contract-summary',
          'Review direction',
        )
      }
    }

    if (
      preservationPolicy.maxNotionalPerTrade !== null &&
      positionCost !== null &&
      positionCost > preservationPolicy.maxNotionalPerTrade
    ) {
      addBlockingReason(
        `Capital preservation mode caps each ticket at ${formatPrice(preservationPolicy.maxNotionalPerTrade)} notional. This ticket maps to ${formatPrice(positionCost)}.`,
        'account-size',
        'Reduce ticket size',
      )
    }

    if (preservationSummary.dailyLossLocked) {
      addBlockingReason(
        preservationSummary.detail,
        'checks',
        'Stand down for the day',
      )
    } else if (preservationSummary.lossStreakLocked) {
      addBlockingReason(
        preservationSummary.detail,
        'checks',
        'Stand down for the day',
      )
    } else if (preservationSummary.positionCapLocked) {
      addBlockingReason(
        preservationSummary.detail,
        'checks',
        'Manage active tickets',
      )
    } else if (preservationSummary.activeTicketCount > 0) {
      addWarningReason(
        preservationSummary.detail,
        'checks',
        'Review active tickets',
      )
    }

    if (preservationPolicy.tinyAccountMode) {
      addWarningReason(
        'Tiny-account mode is active. The desk will stay equities-only, limit-only, one-position, and fractional-share aware.',
        'account-size',
        'Review capital preservation',
      )
    }
  }

  if (reviewGuardrail.blocker) {
    if (strictRouteGuards) {
      addBlockingReason(reviewGuardrail.blocker, 'review-loop', 'Resolve repair note')
    } else {
      addWarningReason(reviewGuardrail.blocker, 'review-loop', 'Review repair note')
    }
  } else if (reviewGuardrail.warning) {
    addWarningReason(reviewGuardrail.warning, 'review-loop', 'Open repair note')
  }

  if (String(report?.trade_decision || '').toUpperCase() !== 'VALID TRADE') {
    addBlockingReason(
      report?.reject_reason || 'The model has not green-lit this setup yet.',
      'checks',
      'Review setup checks',
    )
  }

  if (report?.event_risk) {
    addBlockingReason(
      report?.event_reason || 'A live event-risk blocker is attached to this setup.',
      'checks',
      'Review event risk',
    )
  }

  if (!positionPreview || !positionPreview.affordable || positionPreview.suggestedContracts <= 0) {
    addBlockingReason(
      positionPreview?.statusText || 'The ticket does not have a valid position size yet.',
      'risk-percent',
      'Adjust risk sizing',
    )
  }

  if (riskReward === null) {
    addBlockingReason(
      'The ticket needs a valid target and invalidation so the risk is defined before entry.',
      'checks',
      'Review target and invalidation',
    )
  } else if (riskReward < 1) {
    addWarningReason('The mapped reward is smaller than the mapped risk.', 'checks', 'Review risk/reward')
  }

  if (normalizedTimeInForce === 'day_ext' && normalizedInstrumentType === 'listed_option') {
    addBlockingReason(
      'Listed option tickets are restricted to regular-hours routing in this desk flow.',
      'time-in-force',
      'Switch time in force',
    )
  }

  if (normalizedTimeInForce === 'day_ext' && normalizedOrderType === 'market') {
    addBlockingReason(
      'Extended-hours market orders are blocked. Use a priced limit or stop-limit ticket instead.',
      'order-type',
      'Choose a priced order',
    )
  }

  if (normalizedInstrumentType === 'listed_option') {
    const spreadPct = toNumber(contract?.spread_pct)
    const volume = toNumber(contract?.volume)
    const openInterest = toNumber(contract?.open_interest)
    const contractAvailable = Boolean(contract?.contract_symbol)
    const dte = daysUntilExpiration(contract?.expiration)

    if (normalizedOptionStrategy === 'short_premium') {
      addBlockingReason(
        'Short premium structures are review-only until margin, assignment, and buy-to-close controls are enabled.',
        'option-structure',
        'Choose long option',
      )
    } else if (normalizedOptionStrategy === 'vertical_spread') {
      addBlockingReason(
        'Vertical spreads require multi-leg validation and routing before submit is allowed.',
        'option-structure',
        'Choose long option',
      )
    }

    if (!contractAvailable) {
      addBlockingReason(
        'The model has not surfaced a tradeable option contract yet.',
        'contract-summary',
        'Review contract',
      )
    }

    if (!normalizedOptionRight) {
      addWarningReason(
        'Option side is not fully mapped, so review the contract before routing.',
        'contract-summary',
        'Review contract side',
      )
    }

    if (dte !== null && dte < 0) {
      addBlockingReason(
        'The recommended option contract is already expired.',
        'contract-summary',
        'Choose a live contract',
      )
    } else if (dte !== null && dte === 0) {
      addBlockingReason(
        'Same-day expiry option tickets are blocked in this desk flow.',
        'contract-summary',
        'Review expiry',
      )
    } else if (dte !== null && dte <= 2) {
      addWarningReason(
        `This option expires in ${dte} day${dte === 1 ? '' : 's'}, so exercise and liquidity behavior become more path-sensitive.`,
        'contract-summary',
        'Review expiry',
      )
    }

    if (spreadPct !== null && spreadPct > 18) {
      addBlockingReason(
        `Option spread is too wide at ${formatPercent(spreadPct, 1)}.`,
        'contract-summary',
        'Review contract liquidity',
      )
    } else if (spreadPct !== null && spreadPct > 10) {
      addWarningReason(
        `Option spread is elevated at ${formatPercent(spreadPct, 1)}.`,
        'contract-summary',
        'Review contract liquidity',
      )
    }

    if (volume !== null && volume < 5) {
      addBlockingReason(
        `Option volume is too thin at ${formatCompact(volume)} contracts.`,
        'contract-summary',
        'Review option volume',
      )
    } else if (volume !== null && volume < 50) {
      addWarningReason(
        `Option volume is thin at ${formatCompact(volume)} contracts.`,
        'contract-summary',
        'Review option volume',
      )
    }

    if (openInterest !== null && openInterest < 25) {
      addBlockingReason(
        `Open interest is too thin at ${formatCompact(openInterest)} contracts.`,
        'contract-summary',
        'Review open interest',
      )
    } else if (openInterest !== null && openInterest < 250) {
      addWarningReason(
        `Open interest is light at ${formatCompact(openInterest)} contracts.`,
        'contract-summary',
        'Review open interest',
      )
    }

    if (
      normalizedOrderType === 'market' &&
      ((spreadPct !== null && spreadPct > 8) || (volume !== null && volume < 25) || (openInterest !== null && openInterest < 100))
    ) {
      addBlockingReason(
        'Use a priced order instead of a market order when option liquidity is weak.',
        'order-type',
        'Change order type',
      )
    } else if (normalizedOrderType === 'trailing_stop') {
      addWarningReason(
        'Trailing-stop logic on listed options can move on quote noise.',
        'order-type',
        'Review order type',
      )
    }

    if (normalizedTimeInForce === 'gtc_90d' && dte !== null && dte < 30) {
      addWarningReason(
        'The contract expires well before the GTC window, so review whether a shorter-lived order makes more sense.',
        'time-in-force',
        'Review time in force',
      )
    }
  } else {
    const bid = toNumber(quote?.bid_price)
    const ask = toNumber(quote?.ask_price)
    const rawSpread = resolveDisplaySpread(quote?.spread, bid, ask)
    const spreadPct =
      normalizedLivePrice !== null && normalizedLivePrice > 0 && rawSpread !== null
        ? (rawSpread / normalizedLivePrice) * 100
        : null
    const bidSize = toNumber(quote?.bid_size)
    const askSize = toNumber(quote?.ask_size)

    if (rawSpread === null) {
      addWarningReason(
        'Waiting for a live bid/ask quote to verify equity liquidity.',
        'checks',
        'Review liquidity checks',
      )
    }

    if (spreadPct !== null && spreadPct > 0.4) {
      addBlockingReason(
        `Equity spread is too wide at ${formatPercent(spreadPct, 2)}.`,
        'checks',
        'Review liquidity checks',
      )
    } else if (spreadPct !== null && spreadPct > 0.15) {
      addWarningReason(
        `Equity spread is elevated at ${formatPercent(spreadPct, 2)}.`,
        'checks',
        'Review liquidity checks',
      )
    }

    if (
      normalizedOrderType === 'market' &&
      (rawSpread === null || (spreadPct !== null && spreadPct > 0.15))
    ) {
      addBlockingReason(
        'Use a priced order instead of a market order when the equity book is wide or quote quality is missing.',
        'order-type',
        'Change order type',
      )
    }

    if ((bidSize !== null && bidSize <= 0) || (askSize !== null && askSize <= 0)) {
      addWarningReason(
        'Top-of-book size is thin, so even small orders can slip.',
        'checks',
        'Review liquidity checks',
      )
    }

    if (normalizedTimeInForce === 'gtc_90d' && normalizedOrderType === 'market') {
      addBlockingReason(
        'Long-lived GTC equity tickets need a priced order instead of an unbounded market instruction.',
        'order-type',
        'Change order type',
      )
    }
  }

  return {
    blockingReasons,
    warningReasons,
    primaryMessage:
      blockingReasons[0]?.message ||
      warningReasons[0]?.message ||
      positionPreview?.statusText ||
      'The ticket is ready for review.',
  }
}

function buildPositionPreview(report, accountSize, riskPercent, instrumentType, livePrice, options = {}) {
  const normalizedInstrumentType = normalizeInstrumentType(instrumentType)
  const allowFractionalShares =
    normalizedInstrumentType === 'equity' && Boolean(options.fractionalSharesOnly)
  const maxNotionalPerTrade = toNumber(options.maxNotionalPerTrade)
  const normalizedAccountSize = toNumber(accountSize)
  const normalizedRiskPercent = toNumber(riskPercent)
  const regimeStrengthScore = toNumber(report?.forecast?.regime_strength_score) ?? 0.5
  const maxRiskDollars =
    normalizedAccountSize !== null && normalizedRiskPercent !== null
      ? normalizedAccountSize * (normalizedRiskPercent / 100)
      : null
  const riskBudgetMultiplier = Math.max(0.7, Math.min(1, 0.65 + regimeStrengthScore * 0.5))
  const effectiveMaxRiskDollars =
    maxRiskDollars === null ? null : maxRiskDollars * riskBudgetMultiplier

  if (
    normalizedAccountSize === null ||
    normalizedRiskPercent === null ||
    normalizedAccountSize <= 0 ||
    normalizedRiskPercent <= 0
  ) {
    return null
  }

  if (effectiveMaxRiskDollars === null) {
    return null
  }

  let entryUnitPrice = null
  let estimatedCostPerUnit = null
  let maxLossPerUnit = null
  let unitLabel = formatUnitLabel(normalizedInstrumentType)
  let sizingTooLargeText = 'The position is too large for the current risk rule.'

  if (normalizedInstrumentType === 'equity') {
    const normalizedLivePrice = toNumber(livePrice)
    const invalidationPrice = toNumber(report?.option_plan?.invalidation_price)
    if (
      normalizedLivePrice === null ||
      invalidationPrice === null ||
      normalizedLivePrice <= 0 ||
      invalidationPrice <= 0
    ) {
      return null
    }
    entryUnitPrice = normalizedLivePrice
    estimatedCostPerUnit = normalizedLivePrice
    maxLossPerUnit = Math.abs(normalizedLivePrice - invalidationPrice)
    sizingTooLargeText = 'The share risk is too large for the current risk rule.'
  } else {
    const contractMid = toNumber(report?.option_plan?.recommended_contract?.mid)
    const stopLossFraction = toNumber(report?.option_plan?.stop_loss)
    if (contractMid === null || stopLossFraction === null) {
      return null
    }
    entryUnitPrice = contractMid
    estimatedCostPerUnit = contractMid * 100
    maxLossPerUnit = estimatedCostPerUnit * stopLossFraction
  }

  if (
    entryUnitPrice === null ||
    estimatedCostPerUnit === null ||
    maxLossPerUnit === null ||
    maxLossPerUnit <= 0
  ) {
    return null
  }

  let suggestedUnits = effectiveMaxRiskDollars / maxLossPerUnit
  if (allowFractionalShares) {
    suggestedUnits = Math.min(suggestedUnits, normalizedAccountSize / estimatedCostPerUnit)
    if (maxNotionalPerTrade !== null && maxNotionalPerTrade > 0) {
      suggestedUnits = Math.min(suggestedUnits, maxNotionalPerTrade / estimatedCostPerUnit)
    }
    suggestedUnits = Math.floor(suggestedUnits * 1000) / 1000
  } else {
    suggestedUnits = Math.floor(suggestedUnits)
  }
  const totalPositionCost = suggestedUnits * estimatedCostPerUnit
  const totalMaxLoss = suggestedUnits * maxLossPerUnit
  const minimumUnits = allowFractionalShares ? 0.001 : 1
  const affordable = suggestedUnits >= minimumUnits && totalPositionCost <= normalizedAccountSize

  let statusText = 'Sizing is ready for review.'
  if (String(report?.trade_decision || '').toUpperCase() !== 'VALID TRADE') {
    statusText = 'The model has not green-lit this setup yet.'
  } else if (suggestedUnits < minimumUnits) {
    statusText = sizingTooLargeText
  } else if (totalPositionCost > normalizedAccountSize) {
    statusText = 'The projected position cost is larger than the account size.'
  } else if (riskBudgetMultiplier < 0.95) {
    statusText = `Sizing is being trimmed to ${formatPercent(riskBudgetMultiplier * 100, 0)} of the normal risk budget because this regime has been weaker live.`
  }

  return {
    instrumentType: normalizedInstrumentType,
    instrumentLabel: formatInstrumentTypeLabel(normalizedInstrumentType),
    unitLabel,
    contractMid: entryUnitPrice,
    entryUnitPrice,
    maxRiskDollars,
    effectiveMaxRiskDollars,
    riskBudgetMultiplier,
    regimeStrengthScore,
    estimatedCostPerContract: estimatedCostPerUnit,
    maxLossPerContract: maxLossPerUnit,
    suggestedContracts: suggestedUnits,
    totalPositionCost,
    totalMaxLoss,
    affordable,
    fractionalSharesOnly: allowFractionalShares,
    statusText,
  }
}

function layoutStorageKeyFor(ticker, interval) {
  const normalizedTicker = String(ticker || '').trim().toUpperCase()
  const normalizedInterval = String(interval || '').trim().toLowerCase()
  return normalizedTicker ? `${normalizedTicker}:${normalizedInterval || '5m'}` : ''
}

function executionChecklistKeyFor(ticker, instrumentType) {
  const normalizedTicker = String(ticker || '').trim().toUpperCase()
  const normalizedInstrumentType = normalizeInstrumentType(instrumentType)
  return normalizedTicker ? `${normalizedTicker}:${normalizedInstrumentType}` : ''
}

function workspaceSignature(ticker, interval, horizon, instrumentType = 'equity') {
  const normalizedTicker = String(ticker || '').trim().toUpperCase()
  const normalizedInterval = String(interval || '').trim().toLowerCase() || '5m'
  const normalizedHorizon = Number(horizon) || 5
  const normalizedInstrumentType = normalizeInstrumentType(instrumentType)
  return normalizedTicker
    ? `${normalizedTicker}:${normalizedInterval}:${normalizedHorizon}:${normalizedInstrumentType}`
    : ''
}

function mergeAnalysisPayload(previous, incoming) {
  if (!incoming?.report) return incoming
  if (!previous?.report) return incoming

  const previousSignature = workspaceSignature(
    previous.report?.ticker,
    previous.report?.interval,
    previous.settings?.horizon,
    previous.settings?.instrument_type,
  )
  const nextSignature = workspaceSignature(
    incoming.report?.ticker,
    incoming.report?.interval,
    incoming.settings?.horizon,
    incoming.settings?.instrument_type,
  )

  if (!previousSignature || previousSignature !== nextSignature) {
    return incoming
  }

  const previousOptionPlan = previous.report?.option_plan || {}
  const nextOptionPlan = incoming.report?.option_plan || {}
  const preserveDeferredEvent =
    String(incoming.report?.event_label || '').toUpperCase() === 'EVENT CHECK DEFERRED'
  const preserveDeferredAlignment =
    String(incoming.report?.alignment_label || '').toUpperCase() === 'ALIGNMENT DEFERRED'

  return {
    ...previous,
    ...incoming,
    settings: {
      ...(previous.settings || {}),
      ...(incoming.settings || {}),
    },
    report: {
      ...previous.report,
      ...incoming.report,
      option_plan: {
        ...previousOptionPlan,
        ...nextOptionPlan,
        recommended_contract:
          nextOptionPlan.recommended_contract || previousOptionPlan.recommended_contract || null,
      },
      alignment_label: preserveDeferredAlignment
        ? previous.report?.alignment_label
        : incoming.report?.alignment_label,
      alignment_score: preserveDeferredAlignment
        ? previous.report?.alignment_score
        : incoming.report?.alignment_score,
      event_risk: preserveDeferredEvent
        ? previous.report?.event_risk
        : incoming.report?.event_risk,
      event_label: preserveDeferredEvent
        ? previous.report?.event_label
        : incoming.report?.event_label,
      event_reason: preserveDeferredEvent
        ? previous.report?.event_reason
        : incoming.report?.event_reason,
      next_event_name: preserveDeferredEvent
        ? previous.report?.next_event_name
        : incoming.report?.next_event_name,
      next_event_date: preserveDeferredEvent
        ? previous.report?.next_event_date
        : incoming.report?.next_event_date,
      event_context: preserveDeferredEvent
        ? previous.report?.event_context
        : incoming.report?.event_context || previous.report?.event_context,
    },
  }
}

function mergeDashboardPayload(previous, incoming) {
  if (!previous) return incoming
  if (!incoming) return previous

  const previousWatchlistRows = previous?.watchlist?.rows || previous?.watchlist?.results || []
  const incomingWatchlistRows = incoming?.watchlist?.rows || incoming?.watchlist?.results || []
  const previousScannerRows = previous?.scan?.results || []
  const incomingScannerRows = incoming?.scan?.results || []

  const shouldPreserveWatchlist = !incomingWatchlistRows.length && previousWatchlistRows.length
  const shouldPreserveScanner = !incomingScannerRows.length && previousScannerRows.length

  if (!shouldPreserveWatchlist && !shouldPreserveScanner) {
    return incoming
  }

  return {
    ...previous,
    ...incoming,
    watchlist: shouldPreserveWatchlist
      ? {
          ...(previous.watchlist || {}),
          ...(incoming.watchlist || {}),
          rows: previous.watchlist?.rows || previous.watchlist?.results || [],
          results: previous.watchlist?.results || previous.watchlist?.rows || [],
        }
      : incoming.watchlist,
    scan: shouldPreserveScanner
      ? {
          ...(previous.scan || {}),
          ...(incoming.scan || {}),
          results: previous.scan?.results || [],
        }
      : incoming.scan,
  }
}

function loadChartLayouts() {
  if (typeof window === 'undefined') return {}

  try {
    const raw = window.localStorage.getItem(CHART_LAYOUT_STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

function loadExecutionChecklistMemory() {
  if (typeof window === 'undefined') return {}

  try {
    const raw = window.localStorage.getItem(EXECUTION_CHECKLIST_STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

function loadDeskSnapshot() {
  if (typeof window === 'undefined') return null

  try {
    const raw = window.localStorage.getItem(DESK_SNAPSHOT_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return null

    const savedAt = Date.parse(parsed.savedAt || '')
    if (Number.isFinite(savedAt)) {
      const ageMs = Date.now() - savedAt
      if (ageMs > 1000 * 60 * 60 * 12) {
        return null
      }
    }

    const form = parsed.form && typeof parsed.form === 'object'
      ? {
          ticker: String(parsed.form.ticker || '').trim().toUpperCase() || defaultForm.ticker,
          interval: String(parsed.form.interval || '').trim().toLowerCase() || defaultForm.interval,
          horizon: Number(parsed.form.horizon) || defaultForm.horizon,
        }
      : null

    return {
      form,
      chartPayload: parsed.chartPayload && typeof parsed.chartPayload === 'object' ? parsed.chartPayload : null,
      analysis: parsed.analysis && typeof parsed.analysis === 'object' ? parsed.analysis : null,
      optionAnalysis:
        parsed.optionAnalysis && typeof parsed.optionAnalysis === 'object'
          ? parsed.optionAnalysis
          : null,
      dashboard: parsed.dashboard && typeof parsed.dashboard === 'object' ? parsed.dashboard : null,
      tradeTicket:
        parsed.tradeTicket && typeof parsed.tradeTicket === 'object'
          ? parsed.tradeTicket
          : null,
    }
  } catch {
    return null
  }
}

function persistDeskSnapshot(payload) {
  if (typeof window === 'undefined') return

  try {
    window.localStorage.setItem(
      DESK_SNAPSHOT_STORAGE_KEY,
      JSON.stringify({
        ...payload,
        savedAt: new Date().toISOString(),
      }),
    )
  } catch {
    // ignore storage failures so the desk keeps working
  }
}

function persistExecutionChecklistMemory(memoryKey, stepKey) {
  if (typeof window === 'undefined' || !memoryKey || !stepKey) return

  const existing = loadExecutionChecklistMemory()
  existing[memoryKey] = {
    stepKey,
    updatedAt: new Date().toISOString(),
  }

  try {
    window.localStorage.setItem(EXECUTION_CHECKLIST_STORAGE_KEY, JSON.stringify(existing))
  } catch {
    // ignore storage failures so the desk keeps working
  }
}

function persistChartLayout(layoutKey, payload) {
  if (typeof window === 'undefined' || !layoutKey) return

  const existing = loadChartLayouts()
  existing[layoutKey] = {
    ...(existing[layoutKey] || {}),
    ...payload,
    updatedAt: new Date().toISOString(),
  }

  try {
    window.localStorage.setItem(CHART_LAYOUT_STORAGE_KEY, JSON.stringify(existing))
  } catch {
    // ignore storage failures so the desk keeps working
  }
}

function clearChartLayout(layoutKey) {
  if (typeof window === 'undefined' || !layoutKey) return

  const existing = loadChartLayouts()
  if (!(layoutKey in existing)) return

  delete existing[layoutKey]

  try {
    window.localStorage.setItem(CHART_LAYOUT_STORAGE_KEY, JSON.stringify(existing))
  } catch {
    // ignore storage failures so the desk keeps working
  }
}

function sanitizeBooleanMap(value) {
  if (!value || typeof value !== 'object') return {}

  return Object.fromEntries(
    Object.entries(value).filter(([key, entryValue]) => key && typeof entryValue === 'boolean'),
  )
}

const DEFAULT_DRAWING_VISIBILITY = {
  levels: true,
  trends: true,
  zones: true,
  notes: true,
  measures: true,
}

function sanitizeDrawingVisibility(value) {
  if (!value || typeof value !== 'object') return { ...DEFAULT_DRAWING_VISIBILITY }

  return {
    levels: typeof value.levels === 'boolean' ? value.levels : DEFAULT_DRAWING_VISIBILITY.levels,
    trends: typeof value.trends === 'boolean' ? value.trends : DEFAULT_DRAWING_VISIBILITY.trends,
    zones: typeof value.zones === 'boolean' ? value.zones : DEFAULT_DRAWING_VISIBILITY.zones,
    notes: typeof value.notes === 'boolean' ? value.notes : DEFAULT_DRAWING_VISIBILITY.notes,
    measures:
      typeof value.measures === 'boolean' ? value.measures : DEFAULT_DRAWING_VISIBILITY.measures,
  }
}

function drawingGroupForGuideType(type) {
  switch (String(type || '').trim().toLowerCase()) {
    case 'hline':
      return 'levels'
    case 'trend':
    case 'ray':
      return 'trends'
    case 'rectangle':
      return 'zones'
    case 'note':
      return 'notes'
    case 'measure':
      return 'measures'
    default:
      return 'levels'
  }
}

function buildGuideAnchorId(id) {
  const normalizedId = typeof id === 'string' && id.trim() ? id.trim() : 'guide'
  return `${normalizedId}-anchor`
}

function sanitizeNumericMap(value) {
  if (!value || typeof value !== 'object') return {}

  return Object.fromEntries(
    Object.entries(value)
      .map(([key, entryValue]) => [key, toNumber(entryValue)])
      .filter(([key, entryValue]) => key && entryValue !== null),
  )
}

function sanitizeChartViewportState(value) {
  if (!value || typeof value !== 'object') return null

  const nextViewport = {}

  if (Array.isArray(value.xaxisRange) && value.xaxisRange.length === 2) {
    const nextRange = value.xaxisRange.map((entry) => {
      if (entry === null) return null
      if (typeof entry !== 'string' || !entry.trim()) return null
      return Number.isNaN(new Date(entry).getTime()) ? null : entry
    })
    if (nextRange.some((entry) => entry !== null)) {
      nextViewport.xaxisRange = nextRange
    }
  }

  if (Array.isArray(value.xaxisLogicalRange) && value.xaxisLogicalRange.length === 2) {
    const from = toNumber(value.xaxisLogicalRange[0])
    const to = toNumber(value.xaxisLogicalRange[1])
    if (from !== null && to !== null && to > from) {
      nextViewport.xaxisLogicalRange = [from, to]
    }
  }

  if (Array.isArray(value.yaxisRange) && value.yaxisRange.length === 2) {
    const min = toNumber(value.yaxisRange[0])
    const max = toNumber(value.yaxisRange[1])
    if (min !== null && max !== null && max > min) {
      nextViewport.yaxisRange = [min, max]
    }
  }

  for (const key of ['showVolumePane', 'showRsiPane', 'showMacdPane']) {
    if (typeof value[key] === 'boolean') {
      nextViewport[key] = value[key]
    }
  }

  if (value.paneRatios && typeof value.paneRatios === 'object') {
    const nextRatios = Object.fromEntries(
      ['price', 'volume', 'rsi', 'macd']
        .map((key) => [key, toNumber(value.paneRatios[key])])
        .filter(([, ratio]) => ratio !== null && ratio > 0),
    )

    if (Object.keys(nextRatios).length) {
      nextViewport.paneRatios = nextRatios
    }
  }

  return Object.keys(nextViewport).length ? nextViewport : null
}

function sanitizeCustomGuides(value) {
  if (!Array.isArray(value)) return []

  return value.flatMap((guide, index) => {
    if (!guide || typeof guide !== 'object') return []

    const id =
      typeof guide.id === 'string' && guide.id.trim()
        ? guide.id.trim()
        : `restored-guide-${index + 1}`
    const type = String(guide.type || '').trim().toLowerCase()
    const label =
      typeof guide.label === 'string' && guide.label.trim()
        ? guide.label.trim()
        : type === 'measure'
          ? 'Measure'
          : type === 'ray'
            ? 'Ray'
          : type === 'trend'
            ? 'Trend'
            : `Guide ${index + 1}`
    const color =
      typeof guide.color === 'string' && guide.color.trim() ? guide.color.trim() : '#9b6bff'
    const group =
      typeof guide.group === 'string' && guide.group.trim()
        ? guide.group.trim()
        : drawingGroupForGuideType(type)
    const anchorId =
      typeof guide.anchorId === 'string' && guide.anchorId.trim()
        ? guide.anchorId.trim()
        : buildGuideAnchorId(id)

    if (type === 'hline') {
      const price = toNumber(guide.price ?? guide.y0 ?? guide.y1)
      if (price === null) return []

      return [
        {
          id,
          type: 'hline',
          price,
          label,
          color,
          locked: Boolean(guide.locked),
          group,
          anchorId,
          dash:
            guide.dash === 'solid' || guide.dash === 'dash' || guide.dash === 'dot'
              ? guide.dash
              : 'dot',
        },
      ]
    }

    if (type === 'trend' || type === 'ray' || type === 'measure' || type === 'rectangle') {
      const x0 = typeof guide.x0 === 'string' ? guide.x0 : null
      const x1 = typeof guide.x1 === 'string' ? guide.x1 : null
      const y0 = toNumber(guide.y0)
      const y1 = toNumber(guide.y1)
      if (!x0 || !x1 || y0 === null || y1 === null) return []

      return [
        {
          id,
          type,
          x0,
          y0,
          x1,
          y1,
          label,
          color,
          locked: Boolean(guide.locked),
          group,
          anchorId,
        },
      ]
    }

    if (type === 'note') {
      const x0 = typeof guide.x0 === 'string' ? guide.x0 : null
      const y0 = toNumber(guide.y0)
      if (!x0 || y0 === null) return []

      return [
        {
          id,
          type: 'note',
          x0,
          y0,
          label,
          color,
          locked: Boolean(guide.locked),
          group,
          anchorId,
        },
      ]
    }

    return []
  })
}

function cloneCustomGuides(value) {
  return Array.isArray(value) ? value.map((guide) => ({ ...guide })) : []
}

function areRangeValuesEqual(left, right) {
  if (left === right) return true
  if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) return false

  return left.every((value, index) => value === right[index])
}

function areViewportsEqual(left, right) {
  if (left === right) return true
  if (!left && !right) return true
  if (!left || !right) return false

  const keys = new Set([...Object.keys(left), ...Object.keys(right)])
  for (const key of keys) {
    const leftValue = left[key]
    const rightValue = right[key]
    if (Array.isArray(leftValue) || Array.isArray(rightValue)) {
      if (!areRangeValuesEqual(leftValue, rightValue)) return false
      continue
    }

    if (key === 'paneRatios') {
      const leftRatios = leftValue && typeof leftValue === 'object' ? leftValue : {}
      const rightRatios = rightValue && typeof rightValue === 'object' ? rightValue : {}
      const ratioKeys = new Set([...Object.keys(leftRatios), ...Object.keys(rightRatios)])
      for (const ratioKey of ratioKeys) {
        if (leftRatios[ratioKey] !== rightRatios[ratioKey]) return false
      }
      continue
    }

    if (leftValue !== rightValue) return false
  }

  return true
}

function mergeViewportState(current, update) {
  if (!update || typeof update !== 'object') return current || null

  const nextViewport = { ...(current || {}) }
  for (const [key, value] of Object.entries(update)) {
    if (value === null) {
      delete nextViewport[key]
      continue
    }

    nextViewport[key] = value
  }

  return sanitizeChartViewportState(nextViewport)
}

function inferTradeSide(event, quoteSnapshot) {
  const price = toNumber(event?.price)
  const bidPrice = toNumber(quoteSnapshot?.bid_price)
  const askPrice = toNumber(quoteSnapshot?.ask_price)

  if (price === null) return 'neutral'
  if (askPrice !== null && price >= askPrice) return 'buy'
  if (bidPrice !== null && price <= bidPrice) return 'sell'

  const midPrice =
    bidPrice !== null && askPrice !== null ? (bidPrice + askPrice) / 2 : null
  if (midPrice !== null) {
    if (price > midPrice) return 'buy'
    if (price < midPrice) return 'sell'
  }

  return 'neutral'
}

function buildDomLevels({ quote, trade, fallbackPrice, levels = 11 }) {
  const bidPrice = toNumber(quote?.bid_price)
  const askPrice = toNumber(quote?.ask_price)
  const livePrice = toNumber(trade?.price) ?? toNumber(fallbackPrice)
  const centerPrice =
    bidPrice !== null && askPrice !== null ? (bidPrice + askPrice) / 2 : livePrice

  if (centerPrice === null) return []

  const spread = Math.abs((askPrice ?? centerPrice) - (bidPrice ?? centerPrice))
  const decimalStep =
    spread > 0
      ? Math.max(Number((spread / 2).toFixed(4)), centerPrice < 1 ? 0.0001 : 0.01)
      : centerPrice < 1
        ? 0.0001
        : 0.01
  const halfLevels = Math.floor(levels / 2)
  const rows = []

  for (let offset = halfLevels; offset >= -halfLevels; offset -= 1) {
    const price = Number((centerPrice + offset * decimalStep).toFixed(centerPrice < 1 ? 4 : 2))
    const isBid = bidPrice !== null && Math.abs(price - bidPrice) < decimalStep / 2
    const isAsk = askPrice !== null && Math.abs(price - askPrice) < decimalStep / 2
    const isLast = livePrice !== null && Math.abs(price - livePrice) < decimalStep / 2

    rows.push({
      price,
      bidSize: isBid ? toNumber(quote?.bid_size) : null,
      askSize: isAsk ? toNumber(quote?.ask_size) : null,
      isBid,
      isAsk,
      isLast,
    })
  }

  return rows
}

function clampVisualizationPercent(value) {
  const numeric = toNumber(value)
  if (numeric === null) return 0
  return Math.max(0, Math.min(100, numeric))
}

function normalizeVisualizationScore(value, { ratio = false } = {}) {
  const numeric = toNumber(value)
  if (numeric === null) return 0
  return clampVisualizationPercent(ratio ? numeric * 100 : numeric)
}

function toneToVisualizationScore(tone, mapping = {}) {
  const normalized = String(tone || '').trim().toLowerCase()
  if (normalized === 'positive') return mapping.positive ?? 86
  if (normalized === 'warning') return mapping.warning ?? 56
  if (normalized === 'negative') return mapping.negative ?? 24
  return mapping.default ?? 48
}

function buildInstitutionalFlowTone(score) {
  const numeric = toNumber(score)
  if (numeric === null) return 'warning'
  if (numeric >= 0.72) return 'positive'
  if (numeric < 0.48) return 'negative'
  return 'warning'
}

function buildInstitutionalFlowSummary(flow) {
  if (!flow || typeof flow !== 'object') {
    return { label: 'Flow pending', tone: 'warning', summary: 'Institutional flow is still being scored.' }
  }

  const score = toNumber(flow.score)
  const tone = buildInstitutionalFlowTone(score)
  const label =
    String(flow.label || '').trim() ||
    (score === null ? 'Flow pending' : tone === 'positive' ? 'Flow strong' : tone === 'negative' ? 'Flow weak' : 'Flow mixed')
  const avgDollarVolume = toNumber(flow.avg_dollar_volume)
  const parts = []
  if (flow.controlled_universe) parts.push('Controlled universe')
  if (avgDollarVolume !== null) parts.push(`Avg $${formatCompact(avgDollarVolume)} / bar`)
  const optionLiquidityScore = toNumber(flow.option_liquidity_score)
  if (optionLiquidityScore !== null) parts.push(`Opt liq ${Math.round(optionLiquidityScore * 100)}`)
  const note = Array.isArray(flow.notes) ? flow.notes.find(Boolean) : ''

  return {
    label,
    tone,
    score,
    summary: parts.join(' | ') || note || label,
    note: note || '',
  }
}

function buildNewsSummary(newsSentiment) {
  if (!newsSentiment || typeof newsSentiment !== 'object') {
    return { label: 'No recent news', tone: 'neutral', score: null, summary: 'No recent articles', detail: '' }
  }

  const score = toNumber(newsSentiment.sentiment_score)
  const confidence = toNumber(newsSentiment.confidence)
  const articleCount = toNumber(newsSentiment.article_count)
  const label =
    String(newsSentiment.label || '').trim() ||
    ((articleCount || 0) > 0 ? 'News watch' : 'No recent news')
  let tone = 'neutral'
  if ((articleCount || 0) > 0) {
    if (score !== null && score >= 0.18) tone = 'positive'
    else if (score !== null && score <= -0.18) tone = 'negative'
    else tone = 'warning'
  }

  const parts = []
  if ((articleCount || 0) > 0) {
    parts.push(`${Math.round(articleCount)} article${Math.round(articleCount) === 1 ? '' : 's'}`)
    if (confidence !== null) parts.push(`${formatRatioPercent(confidence, 0)} confidence`)
    parts.push(String(newsSentiment.source || '').trim() || 'News feed')
  } else {
    parts.push('No recent articles')
  }
  const topHeadline = Array.isArray(newsSentiment.headlines) ? newsSentiment.headlines.find(Boolean) : null
  const detail = topHeadline?.title
    ? summarizeInlineCopy(
        `${topHeadline.title}${topHeadline.publisher ? ` — ${topHeadline.publisher}` : ''}`,
        140,
      )
    : ''

  return {
    label,
    tone,
    score,
    summary: parts.join(' | '),
    detail,
  }
}

function buildOptionExecutionSummary(optionExecutionProfile) {
  if (!optionExecutionProfile || typeof optionExecutionProfile !== 'object') {
    return {
      score: null,
      scoreLabel: 'Pending',
      qualityTier: 'pending',
      qualityLabel: 'Quality pending',
      qualityTone: 'warning',
      detail: 'Option execution checks are still loading.',
      rejectSummary: '',
      metaSummary: 'Execution profile pending',
    }
  }

  const score = toNumber(optionExecutionProfile.execution_score)
  const qualityTier = String(optionExecutionProfile.contract_quality_tier || '').trim().toLowerCase() || 'pending'
  const qualityTone =
    qualityTier === 'strong'
      ? 'positive'
      : qualityTier === 'acceptable'
        ? 'warning'
        : qualityTier === 'weak'
          ? 'negative'
          : 'warning'
  const quoteAgeSeconds = toNumber(optionExecutionProfile.quote_age_seconds)
  const rejectReasons = Array.isArray(optionExecutionProfile.reject_reasons)
    ? optionExecutionProfile.reject_reasons
        .map((reason) => summarizeInlineCopy(reason, 120))
        .filter(Boolean)
    : []
  const metaParts = [
    optionExecutionProfile.liquidity_tier ? formatLabel(optionExecutionProfile.liquidity_tier) : null,
    optionExecutionProfile.dte_bucket ? formatLabel(optionExecutionProfile.dte_bucket) : null,
    optionExecutionProfile.moneyness_bucket ? formatLabel(optionExecutionProfile.moneyness_bucket) : null,
    quoteAgeSeconds === null ? null : `Quote ${Math.round(quoteAgeSeconds)}s`,
  ].filter(Boolean)

  return {
    score,
    scoreLabel: score === null ? 'Pending' : `${Math.round(score)}/100`,
    qualityTier,
    qualityLabel: qualityTier === 'pending' ? 'Quality pending' : formatLabel(qualityTier),
    qualityTone,
    detail: rejectReasons[0] || 'Execution checks are supportive enough to evaluate the option path.',
    rejectSummary: rejectReasons.slice(0, 2).join(' | '),
    metaSummary: metaParts.join(' | ') || 'Execution profile pending',
  }
}

function buildVehicleSelectionSummary({
  vehicleRecommendation,
  vehicleReason,
  optionExecutionProfile,
  fallbackInstrumentType = 'equity',
} = {}) {
  const recommendation = String(vehicleRecommendation || '').trim().toLowerCase()
  const fallbackRecommendation =
    normalizeInstrumentType(fallbackInstrumentType) === 'listed_option' ? 'listed_option' : 'equity'
  const effectiveRecommendation = ['equity', 'listed_option', 'stand_down'].includes(recommendation)
    ? recommendation
    : fallbackRecommendation
  const executionSummary = buildOptionExecutionSummary(optionExecutionProfile)
  const tone =
    effectiveRecommendation === 'listed_option'
      ? 'positive'
      : effectiveRecommendation === 'equity'
        ? 'warning'
        : 'negative'
  const label =
    effectiveRecommendation === 'listed_option'
      ? 'Option preferred'
      : effectiveRecommendation === 'equity'
        ? 'Stock preferred'
        : 'Stand down'

  return {
    recommendation: effectiveRecommendation,
    label,
    tone,
    reason:
      summarizeInlineCopy(vehicleReason, 180) ||
      (effectiveRecommendation === 'listed_option'
        ? 'The option chain is liquid enough to express the setup better than stock.'
        : effectiveRecommendation === 'equity'
          ? 'The signal may be valid, but the option chain does not beat the stock route.'
          : 'Neither stock nor option execution is clean enough right now.'),
    executionSummary,
  }
}

function buildDeskResearchPillars(snapshot) {
  return [
    {
      key: 'setup',
      label: 'Setup',
      value: normalizeVisualizationScore(snapshot.setupScore),
      tone: snapshot.decisionTone || 'default',
    },
    {
      key: 'confidence',
      label: 'Confidence',
      value: normalizeVisualizationScore(snapshot.confidenceScore, { ratio: true }),
      tone: snapshot.trustTone || 'default',
    },
    {
      key: 'execution',
      label: 'Execution',
      value: toneToVisualizationScore(snapshot.executionTone, { positive: 84, warning: 58, negative: 24 }),
      tone: snapshot.executionTone || 'default',
    },
    {
      key: 'event',
      label: 'Event',
      value: toneToVisualizationScore(snapshot.eventTone, { positive: 82, warning: 52, negative: 24 }),
      tone: snapshot.eventTone || 'default',
    },
    {
      key: 'news',
      label: 'News',
      value:
        snapshot.newsScore === null || snapshot.newsScore === undefined
          ? toneToVisualizationScore(snapshot.newsTone, { positive: 84, warning: 56, negative: 24 })
          : normalizeVisualizationScore(Math.abs(snapshot.newsScore), { ratio: true }),
      tone: snapshot.newsTone || 'default',
    },
    {
      key: 'flow',
      label: 'Flow',
      value: normalizeVisualizationScore(snapshot.flowScore, { ratio: true }),
      tone: snapshot.flowTone || 'default',
    },
    {
      key: 'benchmark',
      label: 'Benchmark',
      value: toneToVisualizationScore(snapshot.benchmarkTone, { positive: 84, warning: 54, negative: 24 }),
      tone: snapshot.benchmarkTone || 'default',
    },
  ]
}

function buildDeskResearchPathModel(snapshot) {
  const live = toNumber(snapshot.livePriceValue)
  const target = toNumber(snapshot.targetPriceValue)
  const stop = toNumber(snapshot.stopPriceValue)
  const entryLow = toNumber(snapshot.entryLowPrice)
  const entryHigh = toNumber(snapshot.entryHighPrice)
  const points = [live, target, stop, entryLow, entryHigh].filter((value) => value !== null)
  if (points.length < 2) return null
  const lower = Math.min(...points)
  const upper = Math.max(...points)
  const span = Math.max(upper - lower, 0.01)
  const project = (value) => {
    const numeric = toNumber(value)
    if (numeric === null) return null
    return clampVisualizationPercent(((numeric - lower) / span) * 100)
  }
  return {
    lower,
    upper,
    livePct: project(live),
    targetPct: project(target),
    stopPct: project(stop),
    entryLowPct: project(entryLow),
    entryHighPct: project(entryHigh),
  }
}

function DeskResearchCard({ snapshot, compact = false, showPath = true }) {
  if (!snapshot) return null
  const pillars = buildDeskResearchPillars(snapshot)
  const pathModel = buildDeskResearchPathModel(snapshot)
  const notes = snapshot.notes.filter(Boolean).slice(0, compact ? 2 : 3)
  const pathRangeLabel = pathModel
    ? compact
      ? formatCompactMeaningfulPriceRange(pathModel.lower, pathModel.upper)
      : `${formatPrice(pathModel.lower)} to ${formatPrice(pathModel.upper)}`
    : 'Path pending'
  const entryZoneDisplayLabel = compact
    ? formatCompactMeaningfulPriceRange(snapshot.entryLowPrice, snapshot.entryHighPrice)
    : snapshot.entryZoneLabel

  return (
    <article
      className={`compare-snapshot-card compare-snapshot-card--${snapshot.tone || 'default'}${compact ? ' compare-snapshot-card--compact' : ''}`}
    >
      <div className="compare-snapshot-card__header">
        <div>
          <div className="compare-snapshot-card__ticker-row">
            <strong className="compare-snapshot-card__ticker">{snapshot.ticker}</strong>
            <span className="compare-snapshot-card__rank">{snapshot.interval}</span>
          </div>
          <div className="compare-snapshot-card__price">{snapshot.livePriceLabel}</div>
          <div className="compare-snapshot-card__forecast">
            {snapshot.contextLabel} | {snapshot.instrumentLabel}
          </div>
        </div>
        <div className="compare-snapshot-card__badges">
          <StatusBadge tone={snapshot.tone}>{snapshot.decisionLabel}</StatusBadge>
          <StatusBadge tone={snapshot.executionTone}>{snapshot.executionLabel}</StatusBadge>
          <StatusBadge tone={snapshot.trustTone}>{snapshot.trustLabel}</StatusBadge>
          <StatusBadge tone={snapshot.vehicleTone}>{snapshot.vehicleLabel}</StatusBadge>
          <StatusBadge tone={snapshot.optionExecutionQualityTone}>{snapshot.optionExecutionQualityLabel}</StatusBadge>
        </div>
      </div>

      <div className="compare-snapshot-card__subhead">
        <span>{snapshot.regimeLabel}</span>
        <span>{snapshot.newsLabel}</span>
        <span>{snapshot.flowLabel}</span>
        <span>{snapshot.optionExecutionScoreLabel}</span>
        <span>{snapshot.routeLabel}</span>
        <span>{snapshot.horizonLabel}</span>
      </div>

      <div className="compare-snapshot-pillars" aria-label={`${snapshot.ticker} desk research pillars`}>
        {pillars.map((pillar) => (
          <div key={pillar.key} className="compare-snapshot-pillars__item">
            <div className="compare-snapshot-pillars__label-row">
              <span>{pillar.label}</span>
              <strong>{Math.round(pillar.value)}</strong>
            </div>
            <div className="compare-snapshot-pillars__track">
              <div
                className={`compare-snapshot-pillars__fill compare-snapshot-pillars__fill--${pillar.tone}`}
                style={{ width: `${pillar.value}%` }}
              />
            </div>
          </div>
        ))}
      </div>

      {showPath ? (
        <div className="compare-snapshot-card__path">
          <div className="compare-snapshot-card__path-head">
            <span>Price path</span>
            <strong>{pathRangeLabel}</strong>
          </div>
          {pathModel ? (
            <>
              <div className="compare-snapshot-path">
                <div className="compare-snapshot-path__rail" />
                {pathModel.entryLowPct !== null ? (
                  <span
                    className="compare-snapshot-path__band compare-snapshot-path__band--entry"
                    style={{
                      left: `${pathModel.entryLowPct}%`,
                      width: `${Math.max(2, (pathModel.entryHighPct ?? pathModel.entryLowPct) - pathModel.entryLowPct)}%`,
                    }}
                  />
                ) : null}
                {pathModel.stopPct !== null ? (
                  <span className="watchlist-drift-range__marker watchlist-drift-range__marker--stop" style={{ left: `${pathModel.stopPct}%` }} />
                ) : null}
                {pathModel.targetPct !== null ? (
                  <span className="compare-snapshot-path__marker compare-snapshot-path__marker--target" style={{ left: `${pathModel.targetPct}%` }} />
                ) : null}
                {pathModel.livePct !== null ? (
                  <span className="compare-snapshot-path__marker compare-snapshot-path__marker--live" style={{ left: `${pathModel.livePct}%` }} />
                ) : null}
              </div>
              <div className={`compare-snapshot-path__legend${compact ? ' compare-snapshot-path__legend--compact' : ''}`}>
                <div className="compare-snapshot-path__legend-item compare-snapshot-path__legend-item--entry">
                  <span>Entry</span>
                  <strong>{entryZoneDisplayLabel}</strong>
                </div>
                <div className="compare-snapshot-path__legend-item compare-snapshot-path__legend-item--live">
                  <span>Live</span>
                  <strong>{snapshot.livePriceLabel}</strong>
                </div>
                <div className="compare-snapshot-path__legend-item compare-snapshot-path__legend-item--target">
                  <span>Target</span>
                  <strong>{snapshot.targetPriceLabel}</strong>
                </div>
              </div>
            </>
          ) : (
            <p className="compare-snapshot-card__path-empty">Entry, live, and target levels are not all available yet.</p>
          )}
        </div>
      ) : null}

      <div className="compare-snapshot-card__notes">
        {notes.length ? notes.map((note) => <p key={note}>{note}</p>) : <p>No desk notes are attached yet.</p>}
      </div>
    </article>
  )
}

function ChartStageCockpit({ snapshot }) {
  if (!snapshot) return null
  const flowStrength = normalizeVisualizationScore(snapshot.flowScore, { ratio: true })

  return (
    <div className="chart-stage-cockpit" aria-label={`${snapshot.ticker} chart cockpit`}>
      <div className="chart-stage-cockpit__head">
        <div>
          <Kicker as="div">{snapshot.kicker}</Kicker>
          <strong>{snapshot.title}</strong>
        </div>
        <div className="chart-stage-cockpit__badges">
          <StatusBadge tone={snapshot.tone}>{snapshot.decisionLabel}</StatusBadge>
          <StatusBadge tone={snapshot.executionTone}>{snapshot.executionLabel}</StatusBadge>
          <StatusBadge tone={snapshot.trustTone}>{snapshot.trustLabel}</StatusBadge>
        </div>
      </div>

      <div className="chart-stage-cockpit__flow">
        <div className="chart-stage-cockpit__flow-head">
          <span>Institutional flow</span>
          <div className="chart-stage-cockpit__flow-meta">
            <StatusBadge tone={snapshot.flowTone}>{snapshot.flowLabel}</StatusBadge>
            <strong>{Math.round(flowStrength)}</strong>
          </div>
        </div>
        <div className="chart-stage-cockpit__flow-track">
          <div
            className={`chart-stage-cockpit__flow-fill chart-stage-cockpit__flow-fill--${snapshot.flowTone || 'default'}`}
            style={{ width: `${flowStrength}%` }}
          />
        </div>
        <div className="chart-stage-cockpit__flow-note">
          <span>{snapshot.flowSummary}</span>
          {snapshot.flowDetail && snapshot.flowDetail !== snapshot.flowSummary ? <span>{snapshot.flowDetail}</span> : null}
        </div>
      </div>

      <div className="chart-stage-cockpit__grid">
        {snapshot.blocks.map((block) => (
          <div key={block.label} className="chart-stage-cockpit__block">
            <span>{block.label}</span>
            <strong>{block.value}</strong>
          </div>
        ))}
      </div>

      <div className="chart-stage-cockpit__note">{snapshot.note}</div>

      <div className="chart-stage-cockpit__momentum">
        <div className="chart-mini-section__title">{snapshot.momentumTitle}</div>
        <div className="tv-sidebar-meter-card__labels">
          <span>Bear</span>
          <span>Neutral</span>
          <span>Bull</span>
        </div>
        <div className="tv-sidebar-meter-card__track">
          <span style={{ width: `${snapshot.momentumStrength}%` }} />
        </div>
        <div className="tv-sidebar-meter-card__value">{snapshot.momentumValue}</div>
      </div>
    </div>
  )
}

export default function DashboardPage({ bootstrap }) {
  const location = useLocation()
  const navigate = useNavigate()
  const initialDeskSnapshot = useMemo(() => loadDeskSnapshot(), [])
  const initialDeskForm = initialDeskSnapshot?.form
    ? {
        ...defaultForm,
        ...initialDeskSnapshot.form,
      }
    : defaultForm
  const initialDeskTradeTicket =
    initialDeskSnapshot?.tradeTicket && typeof initialDeskSnapshot.tradeTicket === 'object'
      ? {
          ...defaultTradeTicket,
          ...initialDeskSnapshot.tradeTicket,
        }
      : defaultTradeTicket
  const initialDeskDashboard = initialDeskSnapshot?.dashboard
    ? mergeDashboardPayload(createFallbackDashboard(), initialDeskSnapshot.dashboard)
    : createFallbackDashboard()
  const initialDeskHasHydratedData = Boolean(
    hasUsableChartPrices(initialDeskSnapshot?.chartPayload) ||
      isUsableAnalysisPayload(initialDeskSnapshot?.analysis),
  )
  const [dashboard, setDashboard] = useState(() => initialDeskDashboard)
  const [portfolioFallback, setPortfolioFallback] = useState(null)
  const [automationSnapshot, setAutomationSnapshot] = useState(null)
  const [internalBrokerRouter, setInternalBrokerRouter] = useState(null)
  const [chartPayload, setChartPayload] = useState(() => initialDeskSnapshot?.chartPayload || null)
  const [analysis, setAnalysis] = useState(() => initialDeskSnapshot?.analysis || null)
  const [optionAnalysis, setOptionAnalysis] = useState(() => initialDeskSnapshot?.optionAnalysis || null)
  const [form, setForm] = useState(() => initialDeskForm)
  const [tradeTicket, setTradeTicket] = useState(() => initialDeskTradeTicket)
  const [linkedClientAccounts, setLinkedClientAccounts] = useState([])
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [loading, setLoading] = useState(() => !initialDeskHasHydratedData)
  const [analysisLoading, setAnalysisLoading] = useState(false)
  const [workspaceSyncMode, setWorkspaceSyncMode] = useState('idle')
  const [boardSyncing, setBoardSyncing] = useState(false)
  const [error, setError] = useState('')
  const [formErrors, setFormErrors] = useState({})
  const [deskActionIssue, setDeskActionIssue] = useState(null)
  const [selectedChartPoint, setSelectedChartPoint] = useState(null)
  const [watchlistLiveMap, setWatchlistLiveMap] = useState({})
  const [selectedTrade, setSelectedTrade] = useState(null)
  const [selectedQuote, setSelectedQuote] = useState(null)
  const [tradeTape, setTradeTape] = useState([])
  const [operatorMemoryNotes, setOperatorMemoryNotes] = useState([])
  const [savingOperatorMemory, setSavingOperatorMemory] = useState(false)
  const [activeDrawer, setActiveDrawer] = useState(null)
  const [tapeOpen, setTapeOpen] = useState(false)
  const [marketPanelOpen, setMarketPanelOpen] = useState(false)
  const [marketPanelTab, setMarketPanelTab] = useState('watchlist')
  const [liveFocusMode, setLiveFocusMode] = useState(false)
  const [focusLockTicker, setFocusLockTicker] = useState('')
  const [viewportHeight, setViewportHeight] = useState(900)
  const [chartStyle, setChartStyle] = useState('candles')
  const [hiddenOverlays, setHiddenOverlays] = useState({})
  const [toolMode, setToolMode] = useState('pan')
  const [magnetMode, setMagnetMode] = useState(true)
  const [drawingVisibility, setDrawingVisibility] = useState(DEFAULT_DRAWING_VISIBILITY)
  const [customGuides, setCustomGuides] = useState([])
  const [selectedGuideId, setSelectedGuideId] = useState(null)
  const [drawingHistoryState, setDrawingHistoryState] = useState({ canUndo: false, canRedo: false })
  const [pendingGuidePoint, setPendingGuidePoint] = useState(null)
  const [levelOverrides, setLevelOverrides] = useState({})
  const [chartViewport, setChartViewport] = useState(null)
  const [layoutReadyKey, setLayoutReadyKey] = useState('')
  const [lastOrderEvent, setLastOrderEvent] = useState(null)
  const [tradePreview, setTradePreview] = useState(null)
  const [tradePreviewLoading, setTradePreviewLoading] = useState(false)
  const [tradePreviewError, setTradePreviewError] = useState('')
  const [pendingOrderActionKey, setPendingOrderActionKey] = useState('')
  const [lastRouteChange, setLastRouteChange] = useState(null)
  const [preferredChecklistStepKey, setPreferredChecklistStepKey] = useState('')
  const [checklistExpanded, setChecklistExpanded] = useState(false)
  const [executionReviewBaseline, setExecutionReviewBaseline] = useState(null)
  const [actionConfirmArmed, setActionConfirmArmed] = useState(false)
  const [actionHistory, setActionHistory] = useState([])
  const [helperContextExpanded, setHelperContextExpanded] = useState(false)
  const [resolvedRepairNotice, setResolvedRepairNotice] = useState(null)
  const [workflowArrivalNotice, setWorkflowArrivalNotice] = useState(null)
  const [showExtendedSidebarDetails, setShowExtendedSidebarDetails] = useState(false)
  const tradeTapeStoreRef = useRef({})
  const watchlistLiveMapRef = useRef({})
  const selectedQuoteRef = useRef(null)
  const formRef = useRef(initialDeskForm)
  const streamFlushFrameRef = useRef(null)
  const lastStreamMessageAtRef = useRef(0)
  const streamEventBufferRef = useRef({
    watchlist: {},
    activeTicker: '',
    activeTrade: null,
    activeQuote: null,
    activeTape: null,
    activeTradeEvent: null,
  })
  const ticketTargetRefs = useRef({})
  const previousRouteRef = useRef({
    orderType: initialDeskTradeTicket.orderType,
    timeInForce: initialDeskTradeTicket.timeInForce,
  })
  const workspaceRefreshInFlight = useRef(false)
  const analysisRefreshInFlight = useRef(false)
  const boardRefreshInFlight = useRef(false)
  const liveBatchRefreshInFlight = useRef(false)
  const tradePreviewInFlightRef = useRef(false)
  const lastTradePreviewAtRef = useRef(0)
  const workspaceLoadRequestRef = useRef({ key: '', mode: '', startedAt: 0, promise: null })
  const lastSilentWorkspaceLoadRef = useRef({ key: '', at: 0 })
  const hasBootstrapped = useRef(false)
  const customGuideId = useRef(1)
  const activeWorkspaceKey = useRef('')
  const detailHydrationSequence = useRef(0)
  const optionHydrationSequence = useRef(0)
  const chartViewportRef = useRef(null)
  const viewportCommitTimeoutRef = useRef(null)
  const customGuidesRef = useRef([])
  const drawingHistoryRef = useRef({ past: [], future: [] })
  const shellTickerRequestRef = useRef('')
  const repairNoticeRef = useRef('')
  const workflowArrivalRef = useRef('')
  const appliedTradePlanDefaultsRef = useRef('')

  useEffect(() => {
    let active = true
    getLinkedBrokerageAccounts()
      .then((payload) => {
        if (!active) return
        const connectedAccounts = Array.isArray(payload?.items)
          ? payload.items.filter((item) => String(item?.connection_status || '').trim().toLowerCase() === 'connected')
          : []
        setLinkedClientAccounts(connectedAccounts)
      })
      .catch(() => {
        if (!active) return
        setLinkedClientAccounts([])
      })
    return () => {
      active = false
    }
  }, [])

  function normalizeTapeTicker(ticker) {
    return String(ticker || '').trim().toUpperCase()
  }

  function getStoredTradeTape(ticker) {
    const normalizedTicker = normalizeTapeTicker(ticker)
    if (!normalizedTicker) return []
    const stored = tradeTapeStoreRef.current[normalizedTicker]
    return Array.isArray(stored) ? stored : []
  }

  function updateStoredTradeTape(ticker, updater) {
    const normalizedTicker = normalizeTapeTicker(ticker)
    if (!normalizedTicker) return []
    const currentTape = getStoredTradeTape(normalizedTicker)
    const nextTape =
      typeof updater === 'function' ? updater(currentTape) : Array.isArray(updater) ? updater : []
    tradeTapeStoreRef.current[normalizedTicker] = nextTape
    return nextTape
  }

  function resetStreamEventBuffer() {
    streamEventBufferRef.current = {
      watchlist: {},
      activeTicker: '',
      activeTrade: null,
      activeQuote: null,
      activeTape: null,
      activeTradeEvent: null,
    }
  }

  function queueStreamFlush() {
    if (streamFlushFrameRef.current !== null) return
    streamFlushFrameRef.current = window.requestAnimationFrame(() => {
      streamFlushFrameRef.current = null
      const pending = streamEventBufferRef.current
      resetStreamEventBuffer()

      if (Object.keys(pending.watchlist).length) {
        setWatchlistLiveMap((current) => {
          const next = { ...current }
          for (const [symbol, snapshot] of Object.entries(pending.watchlist)) {
            next[symbol] = {
              ...(next[symbol] || {}),
              ...snapshot,
            }
          }
          watchlistLiveMapRef.current = next
          return next
        })
      }

      const currentTicker = String(formRef.current?.ticker || '').toUpperCase()

      if (pending.activeTrade && pending.activeTicker === currentTicker) {
        selectedQuoteRef.current = pending.activeQuote || selectedQuoteRef.current
        setSelectedTrade(pending.activeTrade)
        setTradeTape(pending.activeTape || [])
        if (pending.activeTradeEvent) {
          setChartPayload((current) =>
            applyTradeTickToChart(current, pending.activeTradeEvent, formRef.current.interval),
          )
        }
      }

      if (pending.activeQuote && pending.activeTicker === currentTicker) {
        selectedQuoteRef.current = pending.activeQuote
        setSelectedQuote(pending.activeQuote)
        setChartPayload((current) =>
          applyQuoteTickToChart(current, pending.activeQuote, formRef.current.interval),
        )
      }
    })
  }

  const { pushToast } = useToast()
  const { preferences } = usePreferences()
  const activeAccountProfile = normalizeAccountProfile(preferences?.activeAccountProfile)
  const defaultExecutionIntent = String(preferences?.defaultExecutionIntent || 'desk').trim().toLowerCase() || 'desk'
  const profileTradingContext = useMemo(
    () =>
      resolveAccountProfileTradingContext({
        activeAccountProfile,
        defaultExecutionIntent,
        primaryBrokerageLinkedAccountId: preferences?.primaryBrokerageLinkedAccountId,
        linkedAccounts: linkedClientAccounts,
      }),
    [
      activeAccountProfile,
      defaultExecutionIntent,
      linkedClientAccounts,
      preferences?.primaryBrokerageLinkedAccountId,
    ],
  )
  const dashboardQueryOptions = useMemo(
    () => ({
      account_profile: activeAccountProfile === 'brokerage' ? 'brokerage' : activeAccountProfile,
      linked_account_id: activeAccountProfile === 'brokerage'
        ? profileTradingContext.effectiveLinkedAccountId || ''
        : '',
    }),
    [
      activeAccountProfile,
      profileTradingContext.effectiveLinkedAccountId,
    ],
  )
  const automationScopeOptions = useMemo(
    () =>
      activeAccountProfile === 'brokerage'
        ? {
            scope: 'linked',
            scope_key: profileTradingContext.effectiveLinkedAccountId
              ? `linked:${profileTradingContext.effectiveLinkedAccountId}`
              : '',
            linked_account_id: profileTradingContext.effectiveLinkedAccountId || '',
          }
        : {
            scope: activeAccountProfile,
            scope_key: activeAccountProfile,
          },
    [activeAccountProfile, profileTradingContext.effectiveLinkedAccountId],
  )
  const eventCalendarNavigation = useKeyboardListNavigation({ selector: '.candidate-queue__item', layout: 'grid' })
  const repairNotesNavigation = useKeyboardListNavigation({ selector: '.candidate-queue__item', layout: 'grid' })
  const deskCandidateNavigation = useKeyboardListNavigation({ selector: '.candidate-queue__item', layout: 'grid' })
  const applyDashboardPayload = (payload) => {
    setDashboard((current) => mergeDashboardPayload(current, payload))
  }

  const supportedIntervals = useMemo(
    () => (
      bootstrap?.defaults?.supported_intervals?.length
        ? bootstrap.defaults.supported_intervals
        : intervalPresets
    ),
    [bootstrap?.defaults?.supported_intervals],
  )
  const tradingStyle = String(preferences?.tradingStyle || 'intraday').trim().toLowerCase() === 'intraday' ? 'intraday' : 'swing'
  const intradayPresetProfile = getIntradayPresetProfile(preferences?.intradayPreset)
  const intradayPresetGuide = buildIntradayPresetGuide({ preset: preferences?.intradayPreset, page: 'dashboard' })
  const orderedIntervals = useMemo(
    () => getStyleIntervalOptions(tradingStyle, supportedIntervals),
    [supportedIntervals, tradingStyle],
  )
  const quickIntervals = useMemo(
    () => getStyleQuickIntervals(tradingStyle, supportedIntervals),
    [supportedIntervals, tradingStyle],
  )
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

  const pollMs = Math.max(Number(preferences?.pollingMs) || 15000, 5000)
  useEffect(() => {
    let cancelled = false

    getOrganizationTradeAutomation(automationScopeOptions)
      .then((payload) => {
        if (!cancelled) {
          setAutomationSnapshot(payload)
        }
      })
      .catch(() => {
        // keep the desk usable when automation inventory is unavailable
      })

    return () => {
      cancelled = true
    }
  }, [automationScopeOptions])

  useEffect(() => {
    let cancelled = false

    getInternalBrokerRouter()
      .then((payload) => {
        if (!cancelled) {
          setInternalBrokerRouter(payload)
        }
      })
      .catch(() => {
        // keep the desk usable when Alpaca paper status is unavailable
      })

    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    const nextSignature = [
      preferences?.tradingStyle || 'intraday',
      preferences?.defaultAccountSize ?? defaultTradeTicket.accountSize,
      preferences?.defaultRiskPercent ?? defaultTradeTicket.riskPercent,
      preferences?.defaultOrderType ?? defaultTradeTicket.orderType,
      preferences?.capitalPreservationMode !== false ? 'preserve' : 'flex',
      preferences?.equitiesOnlyMode !== false ? 'equity-only' : 'instrument-flex',
      preferences?.limitOrdersOnlyMode !== false ? 'limit-only' : 'order-flex',
      preferences?.regularHoursOnly === true ? 'day' : 'session-flex',
    ].join('|')
    if (appliedTradePlanDefaultsRef.current === nextSignature) return
    appliedTradePlanDefaultsRef.current = nextSignature
    setTradeTicket((state) => ({
      ...state,
      accountSize: Number(preferences?.defaultAccountSize) || defaultTradeTicket.accountSize,
      riskPercent: Number(preferences?.defaultRiskPercent) || defaultTradeTicket.riskPercent,
      instrumentType:
        preferences?.capitalPreservationMode !== false && preferences?.equitiesOnlyMode !== false
          ? 'equity'
          : state.instrumentType,
      orderType:
        preferences?.capitalPreservationMode !== false && preferences?.limitOrdersOnlyMode !== false
          ? 'limit'
          : preferences?.defaultOrderType || defaultTradeTicket.orderType,
      timeInForce:
        preferences?.regularHoursOnly === false
          ? (state.timeInForce === 'day' || state.timeInForce === 'day_ext' ? 'day_ext' : state.timeInForce)
          : 'day',
    }))
  }, [
    preferences?.tradingStyle,
    preferences?.defaultAccountSize,
    preferences?.defaultOrderType,
    preferences?.defaultRiskPercent,
    preferences?.capitalPreservationMode,
    preferences?.equitiesOnlyMode,
    preferences?.limitOrdersOnlyMode,
    preferences?.regularHoursOnly,
  ])
  const rawWatchlistRows = dashboard?.watchlist?.rows || dashboard?.watchlist?.results || []
  const rawScannerRows = dashboard?.scan?.results || []
  const watchlistRows = useMemo(
    () =>
      rawWatchlistRows.map((row) => {
        const liveEntry = watchlistLiveMap[String(row.ticker || '').toUpperCase()]
        if (!liveEntry) return row
        return {
          ...row,
          live_price: liveEntry.price ?? row.live_price,
          bid_price: liveEntry.bid_price ?? row.bid_price,
          ask_price: liveEntry.ask_price ?? row.ask_price,
          spread: liveEntry.spread ?? row.spread,
          last_trade_at: liveEntry.timestamp ?? row.last_trade_at,
          history: liveEntry.history ?? row.history ?? [],
        }
      }),
    [rawWatchlistRows, watchlistLiveMap],
  )
  const hasHydratedWorkspaceData = Boolean(
    hasUsableChartPrices(chartPayload) || isUsableAnalysisPayload(analysis),
  )
  const hasDeskData = Boolean(chartPayload || analysis || dashboard)
  const scannerRows = useMemo(
    () =>
      rawScannerRows.map((row) => {
        const liveEntry = watchlistLiveMap[String(row.ticker || '').toUpperCase()]
        if (!liveEntry) return row
        return {
          ...row,
          live_price: liveEntry.price ?? row.live_price,
          bid_price: liveEntry.bid_price ?? row.bid_price,
          ask_price: liveEntry.ask_price ?? row.ask_price,
          spread: liveEntry.spread ?? row.spread,
          last_trade_at: liveEntry.timestamp ?? row.last_trade_at,
          history: liveEntry.history ?? row.history ?? [],
        }
      }),
    [rawScannerRows, watchlistLiveMap],
  )
  const baseReport = analysis?.report || null
  const baseOptionPlan = baseReport?.option_plan || {}
  const optionPlan = useMemo(
    () => ({
      ...baseOptionPlan,
      ...levelOverrides,
    }),
    [baseOptionPlan, levelOverrides],
  )
  const report = useMemo(
    () => (baseReport ? { ...baseReport, option_plan: optionPlan } : null),
    [baseReport, optionPlan],
  )
  const canSaveDeskLayout =
    isTickerValid(form.ticker) && Boolean(chartPayload || analysis || report?.ticker)
  const canSaveDeskNote = isTickerValid(form.ticker) && Boolean(report?.ticker)
  const effectiveAnalysis = useMemo(
    () => (analysis ? { ...analysis, report } : analysis),
    [analysis, report],
  )
  const forecastSummary = report?.forecast || chartPayload?.forecast || null
  const forecastFraming =
    report?.forecast_framing ||
    analysis?.forecast_framing ||
    chartPayload?.forecast_framing ||
    null
  const eventContext = useMemo(
    () =>
      resolveEventContext(
        report?.event_context || chartPayload?.event_context || null,
        {
          event_risk: report?.event_risk,
          event_label: report?.event_label,
          event_reason: report?.event_reason,
          next_event_name: report?.next_event_name,
          next_event_date: report?.next_event_date,
          next_event_days: report?.next_event_days,
        },
      ),
    [
      chartPayload?.event_context,
      report?.event_context,
      report?.event_label,
      report?.event_reason,
      report?.event_risk,
      report?.next_event_date,
      report?.next_event_days,
      report?.next_event_name,
    ],
  )
  const intervalModel = useMemo(
    () =>
      buildIntervalModel({
        tradingStyle,
        interval: form.interval,
        horizon: form.horizon,
      }),
    [form.horizon, form.interval, tradingStyle],
  )
  const eventWindowModel = useMemo(
    () =>
      buildEventWindowModel({
        tradingStyle,
        eventContext,
        intradayEventGuardMinutes: preferences?.intradayEventGuardMinutes,
        sessionModel,
      }),
    [eventContext, preferences?.intradayEventGuardMinutes, sessionModel, tradingStyle],
  )

  useEffect(() => {
    watchlistLiveMapRef.current = watchlistLiveMap
  }, [watchlistLiveMap])

  useEffect(() => {
    selectedQuoteRef.current = selectedQuote
  }, [selectedQuote])

  useEffect(() => {
    formRef.current = form
  }, [form])

  useEffect(() => {
    if (workspaceSyncMode !== 'idle' || analysisLoading) return
    if (!hasUsableChartPrices(chartPayload) && !isUsableAnalysisPayload(analysis)) return

    const persistTimer = window.setTimeout(() => {
      persistDeskSnapshot({
        form,
        chartPayload,
        analysis,
        optionAnalysis,
        dashboard,
        tradeTicket,
      })
    }, 250)

    return () => {
      window.clearTimeout(persistTimer)
    }
  }, [analysis, analysisLoading, chartPayload, dashboard, form, optionAnalysis, tradeTicket, workspaceSyncMode])

  useEffect(
    () => () => {
      if (streamFlushFrameRef.current !== null) {
        window.cancelAnimationFrame(streamFlushFrameRef.current)
        streamFlushFrameRef.current = null
      }
    },
    [],
  )
  const executionContext = useMemo(() => {
    const nextContext = report?.execution_context || analysis?.execution_context || chartPayload?.execution_context
    return nextContext && typeof nextContext === 'object' && Object.keys(nextContext).length ? nextContext : null
  }, [analysis?.execution_context, chartPayload?.execution_context, report?.execution_context])
  const journalCalibration = forecastSummary?.journal_calibration || analysis?.journal_calibration || null
  const technicalProbabilityUp = toNumber(
    forecastSummary?.technical_probability_up ?? report?.technical_probability_up,
  )
  const journalAdjustedProbabilityUp = toNumber(forecastSummary?.journal_adjusted_probability_up)
  const adjustedProbabilityUp = toNumber(
    forecastSummary?.adjusted_probability_up ?? report?.probability_up,
  )
  const journalResolvedCount = Number(journalCalibration?.resolved_count || 0)
  const journalHitRate = toNumber(journalCalibration?.empirical_hit_rate)
  const journalAverageError = toNumber(journalCalibration?.average_error)
  const journalAverageProbabilityUp = toNumber(journalCalibration?.average_probability_up)
  const journalMarketRegime = String(
    forecastSummary?.market_regime || report?.market_regime || journalCalibration?.market_regime || '',
  ).trim()
  const calibrationScope = String(journalCalibration?.calibration_scope || '').trim().toLowerCase()
  const regimeBreakdown = Array.isArray(journalCalibration?.regime_breakdown)
    ? journalCalibration.regime_breakdown
    : []
  const bestRegime = journalCalibration?.best_regime || regimeBreakdown[0] || null
  const weakestRegime =
    journalCalibration?.weakest_regime || regimeBreakdown[regimeBreakdown.length - 1] || null
  const sessionBreakdown = Array.isArray(journalCalibration?.session_breakdown)
    ? journalCalibration.session_breakdown
    : []
  const bestSession = journalCalibration?.best_session || sessionBreakdown[0] || null
  const weakestSession =
    journalCalibration?.weakest_session || sessionBreakdown[sessionBreakdown.length - 1] || null
  const eventBreakdown = Array.isArray(journalCalibration?.event_breakdown)
    ? journalCalibration.event_breakdown
    : []
  const bestEventWindow = journalCalibration?.best_event_window || eventBreakdown[0] || null
  const weakestEventWindow =
    journalCalibration?.weakest_event_window || eventBreakdown[eventBreakdown.length - 1] || null
  const driverAttribution = Array.isArray(journalCalibration?.driver_attribution)
    ? journalCalibration.driver_attribution
    : []
  const bestDriver = journalCalibration?.best_driver || driverAttribution[0] || null
  const weakestDriver =
    journalCalibration?.weakest_driver || driverAttribution[driverAttribution.length - 1] || null
  const journalProbabilityShift =
    journalAdjustedProbabilityUp !== null && technicalProbabilityUp !== null
      ? (journalAdjustedProbabilityUp - technicalProbabilityUp) * 100
      : null
  const journalEdgeDelta =
    journalHitRate !== null && journalAverageProbabilityUp !== null
      ? (journalHitRate - journalAverageProbabilityUp) * 100
      : null
  const calibrationShiftLabel = describeCalibrationShift(
    journalProbabilityShift,
    journalResolvedCount,
  )
  const calibrationSupportLine =
    journalResolvedCount >= 8
      ? `${journalResolvedCount} resolved forecasts from the ${calibrationScope === 'regime' ? `${formatLabel(journalMarketRegime, 'active')} regime` : 'broader sample'} are nudging the live bias ${journalProbabilityShift > 0 ? 'higher' : journalProbabilityShift < 0 ? 'lower' : 'sideways'}.`
      : 'Calibration will turn on once enough live forecasts have resolved for this symbol and interval.'
  const regimeStrengthScore = toNumber(forecastSummary?.regime_strength_score)
  const contributionBreakdown = forecastSummary?.contribution_breakdown || null
  const technicalConfidenceComponent = toNumber(contributionBreakdown?.technical_confidence_component)
  const newsConfidenceComponent = toNumber(contributionBreakdown?.news_confidence_component)
  const regimeConfidenceComponent = toNumber(contributionBreakdown?.regime_confidence_component)
  const eventConfidencePenalty = toNumber(contributionBreakdown?.event_confidence_penalty)
  const journalProbabilityContribution = toNumber(contributionBreakdown?.journal_probability_shift)
  const newsProbabilityContribution = toNumber(contributionBreakdown?.news_probability_shift)
  const strategySnapshot = chartPayload?.strategy || analysis?.strategy || null
  const normalizedInstrumentType = normalizeInstrumentType(tradeTicket.instrumentType)
  const normalizedOptionStrategy = normalizeOptionStrategy(tradeTicket.optionStrategy)
  const optionReport = optionAnalysis?.report || null
  const optionDeskPlan = optionReport?.option_plan || {}
  const optionContract = optionDeskPlan.recommended_contract || {}
  const contract =
    normalizedInstrumentType === 'listed_option'
      ? optionContract
      : optionPlan.recommended_contract || {}
  const optionRight =
    String(
      (normalizedInstrumentType === 'listed_option'
        ? optionDeskPlan.option_side
        : optionPlan.option_side) || '',
    )
      .trim()
      .toLowerCase() === 'put'
      ? 'put'
      : 'call'
  const entryMidpoint = midpoint(optionPlan.entry_low_price, optionPlan.entry_high_price)
  const streamTickers = useMemo(() => {
    const activeTicker = String(form.ticker || '').trim().toUpperCase()
    return activeTicker ? [activeTicker] : []
  }, [form.ticker])
  const activeTickerKey = String(form.ticker || '').trim().toUpperCase()
  const activeLiveBatchEntry = activeTickerKey ? watchlistLiveMap[activeTickerKey] || null : null
  const freshestDeskPrice = useMemo(
    () =>
      resolveFreshestDeskPrice({
        trade: selectedTrade,
        quote: selectedQuote,
        liveBatchEntry: activeLiveBatchEntry,
        analysis,
      }),
    [activeLiveBatchEntry, analysis, selectedQuote, selectedTrade],
  )
  const streamedMidPrice = midFromQuote(selectedQuote)
  const streamedLivePrice = freshestDeskPrice.price
  const stagedExecutionPrice = toNumber(selectedChartPoint?.price)
  const activeExecutionPrice =
    streamedLivePrice ??
    toNumber(chartPayload?.candles?.at(-1)?.close) ??
    stagedExecutionPrice
  const liveTradeStatus = deriveTradeStatus(report, activeExecutionPrice)
  const liveExecutionDecision = deriveExecutionDecision(report, activeExecutionPrice)
  const liveAlerts = deriveLiveAlerts(report, activeExecutionPrice)
  const entryAlignmentMessage = describeEntryAlignment(report, activeExecutionPrice)
  const strategyAlignmentMessage = describeStrategyAlignment(
    strategySnapshot,
    activeExecutionPrice,
  )
  const riskReward = calculateRiskReward(report, activeExecutionPrice)
  const capitalPreservationPolicy = useMemo(
    () =>
      buildCapitalPreservationPolicy({
        preferences,
        tradeTicket,
        defaults: defaultTradeTicket,
      }),
    [preferences, tradeTicket],
  )
  const selectedExecutionIntent = resolveAccountProfileExecutionIntent({
    activeAccountProfile,
    defaultExecutionIntent,
  })
  const liveRouteSelected = selectedExecutionIntent === 'broker_live'
  const paperRouteSelected = selectedExecutionIntent === 'broker_paper'
  const unlockedPaperLikeRoute = selectedExecutionIntent === 'broker_paper' || selectedExecutionIntent === 'desk'
  const effectiveCapitalPreservationPolicy = useMemo(
    () =>
      unlockedPaperLikeRoute
        ? {
            ...capitalPreservationPolicy,
            enabled: false,
            tinyAccountMode: false,
            fractionalSharesOnly: false,
            regularHoursOnly: false,
            maxDailyLossR: null,
            maxConsecutiveLosses: null,
            maxOpenPositions: null,
            maxNotionalPerTrade: null,
            equitiesOnly: false,
            limitOrdersOnly: false,
            longOnly: false,
          }
        : capitalPreservationPolicy,
    [capitalPreservationPolicy, unlockedPaperLikeRoute],
  )
  const positionPreview = buildPositionPreview(
    report,
    tradeTicket.accountSize,
    tradeTicket.riskPercent,
    tradeTicket.instrumentType,
    activeExecutionPrice,
    {
      fractionalSharesOnly: effectiveCapitalPreservationPolicy.fractionalSharesOnly,
      maxNotionalPerTrade: effectiveCapitalPreservationPolicy.enabled ? effectiveCapitalPreservationPolicy.maxNotionalPerTrade : null,
    },
  )
  const riskBudgetMultiplier = toNumber(positionPreview?.riskBudgetMultiplier)
  const capitalPreservationMetrics = useMemo(() => {
    const raw = dashboard?.portfolio?.capital_preservation || {}
    const openPositionCount =
      toNumber(raw.open_position_count) ??
      (Array.isArray(dashboard?.portfolio?.open_trades) ? dashboard.portfolio.open_trades.length : 0)
    const pendingOrderCount =
      toNumber(raw.pending_order_count) ??
      (Array.isArray(dashboard?.portfolio?.pending_orders) ? dashboard.portfolio.pending_orders.length : 0)
    return {
      ...raw,
      open_position_count: openPositionCount,
      pending_order_count: pendingOrderCount,
      active_ticket_count:
        toNumber(raw.active_ticket_count) ?? (openPositionCount + pendingOrderCount),
      consecutive_losses: toNumber(raw.consecutive_losses) ?? 0,
      today_realized_pnl: toNumber(raw.today_realized_pnl) ?? 0,
    }
  }, [dashboard?.portfolio?.capital_preservation, dashboard?.portfolio?.open_trades, dashboard?.portfolio?.pending_orders])
  const capitalPreservationSummary = useMemo(
    () =>
      buildCapitalPreservationSummary({
        policy: capitalPreservationPolicy,
        metrics: capitalPreservationMetrics,
      }),
    [capitalPreservationMetrics, capitalPreservationPolicy],
  )
  const effectiveCapitalPreservationSummary = useMemo(
    () =>
      unlockedPaperLikeRoute
        ? {
            ...capitalPreservationSummary,
            enabled: false,
            tone: 'info',
            label: paperRouteSelected ? 'Paper unlocked' : 'Desk unlocked',
            detail: paperRouteSelected
              ? 'Paper routing ignores live-only preservation locks so you can test fills, sessions, and route behavior freely.'
              : 'Desk routing ignores live-only preservation locks and stays available for manual testing.',
            dailyLossLocked: false,
            lossStreakLocked: false,
            positionCapLocked: false,
            reviewOnlyMode: false,
            reviewOnlyReason: null,
            reviewOnlyResetAt: null,
            reviewOnlyResetLabel: '',
            dailyLossLimitDollars: null,
          }
        : capitalPreservationSummary,
    [capitalPreservationSummary, paperRouteSelected, unlockedPaperLikeRoute],
  )
  const reviewOnlyMode = Boolean(effectiveCapitalPreservationSummary.reviewOnlyMode)
  const portfolioSummary = dashboard?.portfolio?.summary || {}
  const portfolioTradeSummary = dashboard?.portfolio?.trade_summary || {}
  const portfolioAttributionSummary = dashboard?.portfolio?.attribution_summary || {}
  const dashboardBrokerAccount =
    dashboard?.portfolio?.broker_account && typeof dashboard.portfolio.broker_account === 'object'
      ? dashboard.portfolio.broker_account
      : null
  const fallbackBrokerAccount =
    activeAccountProfile === 'personal_paper' &&
    portfolioFallback?.broker_account &&
    typeof portfolioFallback.broker_account === 'object'
      ? portfolioFallback.broker_account
      : null
  const brokerAccount = dashboardBrokerAccount || fallbackBrokerAccount || {}
  const brokerAccountConnected = brokerAccount?.connected === true
  const brokerAccountValueDelta =
    brokerAccountConnected && toNumber(brokerAccount?.equity) !== null && toNumber(brokerAccount?.cash) !== null
      ? Number(brokerAccount.equity) - Number(brokerAccount.cash)
      : null
  const brokerAccountLabel = activeAccountProfile === 'brokerage'
    ? 'Linked-account equity'
    : activeAccountProfile === 'personal_live'
      ? 'Live equity'
      : 'Paper equity'
  const brokerAccountCards = brokerAccountConnected
    ? [
        { label: brokerAccountLabel, value: formatPrice(brokerAccount?.equity) },
        { label: 'Cash', value: formatPrice(brokerAccount?.cash) },
        { label: 'Buying power', value: formatPrice(brokerAccount?.buying_power) },
        {
          label: brokerAccountValueDelta === null ? 'Open value' : 'Equity minus cash',
          value:
            brokerAccountValueDelta === null
              ? formatPrice(brokerAccount?.position_market_value)
              : formatSignedCurrency(brokerAccountValueDelta),
        },
      ]
    : []
  const internalRouterBalances =
    internalBrokerRouter?.balances && typeof internalBrokerRouter.balances === 'object'
      ? internalBrokerRouter.balances
      : {}
  const internalRouterHealth =
    internalBrokerRouter?.health && typeof internalBrokerRouter.health === 'object'
      ? internalBrokerRouter.health
      : {}
  const internalRouterRouting =
    internalBrokerRouter?.routing && typeof internalBrokerRouter.routing === 'object'
      ? internalBrokerRouter.routing
      : {}
  const internalRouterOrders =
    internalBrokerRouter?.orders && typeof internalBrokerRouter.orders === 'object'
      ? internalBrokerRouter.orders
      : {}
  const internalRouterOpenOrders = Array.isArray(internalRouterOrders.open) ? internalRouterOrders.open : []
  const internalRouterRecentFills = Array.isArray(internalRouterOrders.recent_fills)
    ? internalRouterOrders.recent_fills
    : []
  const internalRouterRejectedOrders = Array.isArray(internalRouterOrders.rejected)
    ? internalRouterOrders.rejected
    : []
  const internalRouterPositions = Array.isArray(internalBrokerRouter?.positions)
    ? internalBrokerRouter.positions
    : []
  const internalRouterAudit =
    internalBrokerRouter?.audit && typeof internalBrokerRouter.audit === 'object'
      ? internalBrokerRouter.audit
      : {}
  const internalRouterStatus = String(
    internalRouterHealth.status || internalBrokerRouter?.status || 'degraded',
  ).trim().toLowerCase()
  const internalRouterTone =
    internalRouterStatus === 'failed'
      ? 'negative'
      : internalRouterStatus === 'healthy' || internalRouterStatus === 'ready'
        ? 'positive'
        : 'warning'
  const internalRouterBalanceCards = [
    {
      key: 'internal',
      label: 'Alpaca paper equity',
      value: formatPrice(internalRouterBalances.internal_simulated?.equity),
      detail: `Cash ${formatPrice(internalRouterBalances.internal_simulated?.cash)} | BP ${formatPrice(internalRouterBalances.internal_simulated?.buying_power)}`,
    },
    {
      key: 'buying-power',
      label: 'Buying power',
      value: formatPrice(internalRouterBalances.internal_simulated?.buying_power),
      detail: `Options BP ${formatPrice(internalRouterBalances.internal_simulated?.option_buying_power)}`,
    },
    {
      key: 'combined',
      label: 'Alpaca paper total',
      value: formatPrice(internalRouterBalances.combined_paper?.equity),
      detail: 'Paper route total | Not withdrawable cash',
    },
  ]
  const internalRouterActivityCards = [
    {
      key: 'route',
      label: 'Alpaca paper mode',
      value: 'Alpaca paper',
      detail: 'Equities and listed options stay on Alpaca paper until live gates clear.',
    },
    {
      key: 'orders',
      label: 'Open paper orders',
      value: formatNumber(internalRouterOpenOrders.length, 0),
      detail: internalRouterOpenOrders[0]
        ? `${internalRouterOpenOrders[0].symbol} | ${formatLabel(internalRouterOpenOrders[0].state)}`
        : 'No Alpaca paper orders are working.',
    },
    {
      key: 'fills',
      label: 'Recent fills',
      value: formatNumber(internalRouterRecentFills.length, 0),
      detail: internalRouterRecentFills[0]
        ? `${internalRouterRecentFills[0].symbol} | ${formatPrice(internalRouterRecentFills[0].price)}`
        : 'Simulated fills will appear here.',
    },
    {
      key: 'safety',
      label: 'Safety state',
      value: internalRouterRouting.live_routing_enabled ? 'Live enabled' : 'Paper only',
      detail:
        internalRouterRejectedOrders.length > 0
          ? `${internalRouterRejectedOrders.length} rejected paper intent${internalRouterRejectedOrders.length === 1 ? '' : 's'} recorded.`
          : `${internalRouterPositions.length} simulated position${internalRouterPositions.length === 1 ? '' : 's'} tracked.`,
    },
    {
      key: 'audit',
      label: 'Audit chain',
      value: internalRouterAudit.hash_chain_valid === false ? 'Needs review' : 'Valid',
      detail: `${Array.isArray(internalRouterAudit.latest_events) ? internalRouterAudit.latest_events.length : 0} recent audit events loaded.`,
    },
  ]
  const portfolioValidationSnapshot =
    dashboard?.portfolio?.validation_snapshot && typeof dashboard.portfolio.validation_snapshot === 'object'
      ? dashboard.portfolio.validation_snapshot
      : {}
  const loadPortfolioFallback = useCallback(async () => {
    try {
      const payload = await getPortfolio()
      if (payload && typeof payload === 'object') {
        setPortfolioFallback(payload)
      }
    } catch {
      // keep the desk usable if the dedicated portfolio endpoint is temporarily unavailable
    }
  }, [])
  const eventCalendarPayload =
    dashboard?.event_calendar && typeof dashboard.event_calendar === 'object'
      ? dashboard.event_calendar
      : {}
  const eventCalendarItems = Array.isArray(eventCalendarPayload?.items)
    ? eventCalendarPayload.items
    : []
  const reviewLoopProgress = dashboard?.review_loop_progress || {}
  const reviewLoopNotesPayload =
    dashboard?.review_loop_notes && typeof dashboard.review_loop_notes === 'object'
      ? dashboard.review_loop_notes
      : {}
  const reviewLoopNotes = Array.isArray(reviewLoopNotesPayload?.items)
    ? reviewLoopNotesPayload.items
    : []
  const loadOperatorMemoryNotes = useCallback(async () => {
    const payload = await getNotes({ status: 'active', limit: 50, sortBy: 'updated_desc' })
    const items = Array.isArray(payload?.items) ? payload.items.filter(isOperatorMemoryNote) : []
    setOperatorMemoryNotes(items)
    return items
  }, [])
  const reviewLoopTicketGuardrail = useMemo(
    () =>
      buildReviewLoopTicketGuardrail({
        currentTicker: report?.ticker || form.ticker,
        reviewLoopNotes,
      }),
    [form.ticker, report?.ticker, reviewLoopNotes],
  )
  const promotionGateSummary = useMemo(
    () =>
      buildPromotionGateSummary({
        validationSnapshot: portfolioValidationSnapshot,
        policy: capitalPreservationPolicy.promotionGate,
      }),
    [capitalPreservationPolicy.promotionGate, portfolioValidationSnapshot],
  )
  const deskMarketModelState = useMemo(() => {
    const tones = [sessionModel.tone, eventWindowModel.tone]
    const tone = tones.includes('negative')
      ? 'negative'
      : tones.includes('warning')
        ? 'warning'
        : 'positive'
    return {
      tone,
      title: `${sessionModel.label} | ${eventWindowModel.label}`,
      description: `${sessionModel.detail} ${eventWindowModel.detail} ${intervalModel.recommendedDetail} ${preferences?.regularHoursOnly === true ? 'Desk routing is explicitly regular-hours only.' : 'Extended-hours routing is available if execution quality still clears.'}`,
    }
  }, [eventWindowModel.detail, eventWindowModel.label, eventWindowModel.tone, intervalModel.recommendedDetail, preferences?.regularHoursOnly, sessionModel.detail, sessionModel.label, sessionModel.tone])
  const intradayExecutionPlan = useMemo(
    () =>
      buildIntradayExecutionPlan({
        tradingStyle,
        sessionModel,
        regularHoursOnly: liveRouteSelected ? preferences?.regularHoursOnly === true : false,
        reviewOnlyMode: liveRouteSelected ? capitalPreservationSummary.reviewOnlyMode : false,
        executionIntent: selectedExecutionIntent,
        orderType: tradeTicket.orderType,
        timeInForce: tradeTicket.timeInForce,
        riskPercent: tradeTicket.riskPercent,
        rolloutAllowsLive: promotionGateSummary?.allowsPromotion !== false,
      }),
    [
      capitalPreservationSummary.reviewOnlyMode,
      liveRouteSelected,
      preferences?.regularHoursOnly,
      promotionGateSummary?.allowsPromotion,
      sessionModel,
      selectedExecutionIntent,
      tradeTicket.orderType,
      tradeTicket.riskPercent,
      tradeTicket.timeInForce,
      tradingStyle,
    ],
  )
  const preTradeRiskPanelCards = useMemo(
    () =>
      buildPreTradeRiskPanel({
        report,
        instrumentType: tradeTicket.instrumentType,
        optionStrategy: tradeTicket.optionStrategy,
        optionRight,
        positionPreview,
        contract,
        livePrice: activeExecutionPrice,
        riskReward,
      }),
    [
      activeExecutionPrice,
      contract,
      optionRight,
      positionPreview,
      report,
      riskReward,
      tradeTicket.instrumentType,
      tradeTicket.optionStrategy,
    ],
  )
  const preTradeRiskChecks = useMemo(
  () =>
    buildPreTradeRiskChecks({
      report,
      instrumentType: tradeTicket.instrumentType,
      optionStrategy: tradeTicket.optionStrategy,
      positionPreview,
      riskReward,
      quote: selectedQuote,
      contract,
      livePrice: activeExecutionPrice,
      orderType: tradeTicket.orderType,
      timeInForce: tradeTicket.timeInForce,
      optionRight,
    }),
  [
    activeExecutionPrice,
    contract,
    optionRight,
    tradeTicket.orderType,
    tradeTicket.timeInForce,
    positionPreview,
    report,
      riskReward,
    selectedQuote,
    tradeTicket.instrumentType,
      tradeTicket.optionStrategy,
    ],
  )
  const tradeGuardrails = useMemo(
  () =>
      buildTradeGuardrails({
        report,
        instrumentType: tradeTicket.instrumentType,
        positionPreview,
      riskReward,
      quote: selectedQuote,
      contract,
      livePrice: activeExecutionPrice,
        orderType: tradeTicket.orderType,
        timeInForce: tradeTicket.timeInForce,
        optionRight,
        optionStrategy: tradeTicket.optionStrategy,
        capitalPreservationPolicy: effectiveCapitalPreservationPolicy,
      capitalPreservationSummary: effectiveCapitalPreservationSummary,
      reviewLoopTicketGuardrail,
      intradayExecutionPlan,
      strictRouteGuards: liveRouteSelected,
    }),
  [
    activeExecutionPrice,
    effectiveCapitalPreservationPolicy,
    effectiveCapitalPreservationSummary,
    contract,
    optionRight,
    positionPreview,
    report,
    reviewLoopTicketGuardrail,
    riskReward,
    selectedQuote,
    liveRouteSelected,
    intradayExecutionPlan,
    tradeTicket.instrumentType,
      tradeTicket.optionStrategy,
      tradeTicket.orderType,
      tradeTicket.timeInForce,
    ],
  )
  const ticketEducationCards = useMemo(
    () =>
      buildTicketEducationCards({
        report,
        instrumentType: tradeTicket.instrumentType,
        optionStrategy: tradeTicket.optionStrategy,
        optionRight,
        contract,
        orderType: tradeTicket.orderType,
        timeInForce: tradeTicket.timeInForce,
        riskReward,
        positionPreview,
      }),
    [
      contract,
      optionRight,
      positionPreview,
      report,
      riskReward,
      tradeTicket.instrumentType,
      tradeTicket.optionStrategy,
      tradeTicket.orderType,
      tradeTicket.timeInForce,
    ],
  )
  const executionCostCards = useMemo(
    () =>
      buildExecutionCostCards({
        instrumentType: tradeTicket.instrumentType,
        optionStrategy: tradeTicket.optionStrategy,
        orderType: tradeTicket.orderType,
        timeInForce: tradeTicket.timeInForce,
        positionPreview,
        quote: selectedQuote,
        contract,
        accountSize: tradeTicket.accountSize,
        livePrice: activeExecutionPrice,
      }),
    [
      activeExecutionPrice,
      contract,
      positionPreview,
      selectedQuote,
      tradeTicket.accountSize,
      tradeTicket.instrumentType,
      tradeTicket.optionStrategy,
      tradeTicket.orderType,
      tradeTicket.timeInForce,
    ],
  )
  const liquidityExecutionWarnings = useMemo(
    () =>
      buildLiquidityExecutionWarnings({
        instrumentType: tradeTicket.instrumentType,
        optionStrategy: tradeTicket.optionStrategy,
        orderType: tradeTicket.orderType,
        timeInForce: tradeTicket.timeInForce,
        positionPreview,
        quote: selectedQuote,
        contract,
        livePrice: activeExecutionPrice,
      }),
    [
      activeExecutionPrice,
      contract,
      positionPreview,
      selectedQuote,
      tradeTicket.instrumentType,
      tradeTicket.optionStrategy,
      tradeTicket.orderType,
      tradeTicket.timeInForce,
    ],
  )
  const routeComparison = useMemo(
    () =>
      buildRouteComparison({
        instrumentType: tradeTicket.instrumentType,
        orderType: tradeTicket.orderType,
        timeInForce: tradeTicket.timeInForce,
        positionPreview,
        quote: selectedQuote,
        contract,
        livePrice: activeExecutionPrice,
      }),
    [
      activeExecutionPrice,
      contract,
      positionPreview,
      selectedQuote,
      tradeTicket.instrumentType,
      tradeTicket.orderType,
      tradeTicket.timeInForce,
    ],
  )
  const routeChangeFeedback = useMemo(
    () =>
      buildRouteChangeFeedback({
        previousRoute: lastRouteChange?.previous || null,
        currentRoute: lastRouteChange?.current || null,
        instrumentType: tradeTicket.instrumentType,
        positionPreview,
        quote: selectedQuote,
        contract,
        livePrice: activeExecutionPrice,
      }),
    [
      activeExecutionPrice,
      contract,
      lastRouteChange,
      positionPreview,
      selectedQuote,
      tradeTicket.instrumentType,
    ],
  )
  const executionReviewSnapshot = useMemo(
    () =>
      buildExecutionReviewSnapshot({
        ticker: form.ticker,
        instrumentType: tradeTicket.instrumentType,
        orderType: tradeTicket.orderType,
        timeInForce: tradeTicket.timeInForce,
        positionPreview,
        quote: selectedQuote,
        contract,
        livePrice: activeExecutionPrice,
      }),
    [
      activeExecutionPrice,
      contract,
      form.ticker,
      positionPreview,
      selectedQuote,
      tradeTicket.instrumentType,
      tradeTicket.orderType,
      tradeTicket.timeInForce,
    ],
  )
  const ticketContractDte = useMemo(
    () => daysUntilExpiration(contract?.expiration),
    [contract?.expiration],
  )
  const instrumentTooltip =
    normalizedInstrumentType === 'listed_option'
      ? `${formatOptionStrategyLabel(normalizedOptionStrategy)} structure. ${describeOptionStrategy(normalizedOptionStrategy, optionRight)}`
      : 'Linear spot exposure. Share size and invalidation distance drive the real dollars at risk, so the stop and the position size matter more than the label.'
  const optionStructureTooltip = describeOptionStrategy(normalizedOptionStrategy, optionRight)
  const orderTypeTooltip =
    tradeTicket.orderType === 'market'
      ? 'Market orders prioritize getting filled immediately and sacrifice price control. Only use them when the spread and depth are clean.'
      : tradeTicket.orderType === 'trailing_stop'
        ? 'Trailing stops move with price and are best treated as reactive protection, not as precision entry tools.'
        : `${describeOrderType(tradeTicket.orderType)} Price control usually matters more when spreads widen.`
  const timeInForceTooltip =
    normalizedInstrumentType === 'listed_option' && tradeTicket.timeInForce === 'day_ext'
      ? 'Listed options in this desk flow stay on regular-hours routing only. Same-day expiry contracts are also blocked because decay and liquidity shift too fast.'
      : ticketContractDte === 0
        ? 'Same-day expiry option contracts are blocked in this ticket because gamma, decay, and assignment pressure accelerate too quickly.'
        : ticketContractDte !== null && ticketContractDte <= 2
          ? `This contract is ${ticketContractDte} DTE. Near-dated options become more sensitive to spread changes, gamma, and assignment behavior.`
          : tradeTicket.timeInForce === 'gtc_90d'
            ? 'Time in force controls how long an order can rest. Longer orders need active review because liquidity, catalysts, and invalidation can all change before the order fills.'
            : 'Time in force controls how long an order can rest. Day orders force the idea to resolve in the current session instead of drifting into a new market context.'
  const riskPercentTooltip =
    normalizedInstrumentType === 'listed_option'
      ? 'This is the portfolio risk budget for the premium trade, not a confidence slider. The desk sizes contracts so the defined max-risk amount stays near this fraction of account size.'
      : 'This is the portfolio risk budget for the share trade, not a confidence slider. The desk sizes shares so the invalidation distance maps back to this fraction of account size.'
  const limitPriceTooltip =
    'Limit price is your price-control boundary. It helps protect execution quality when spreads are wide, especially outside the most liquid moments.'
  const stopPriceTooltip =
    'Stop price is a trigger, not a guaranteed execution level. Once triggered, the order still depends on the book and can fill worse in fast conditions.'
  const trailPercentTooltip =
    'Trail percent controls how far the protective trigger sits behind price as the move extends. It can help lock gains, but it also reacts to quote noise in thinner books.'
  const registerTicketTarget = (key) => (node) => {
    if (node) {
      ticketTargetRefs.current[key] = node
    } else {
      delete ticketTargetRefs.current[key]
    }
  }
  const jumpToTicketTarget = (key) => {
    const node = ticketTargetRefs.current[key]
    if (!node) {
      return
    }
    node.scrollIntoView({ behavior: 'smooth', block: 'center' })
    const focusTarget =
      node.matches?.('input, button, select, textarea, [href], [tabindex]:not([tabindex="-1"])')
        ? node
        : node.querySelector?.('input, button, select, textarea, [href], [tabindex]:not([tabindex="-1"])')
    if (focusTarget && typeof focusTarget.focus === 'function') {
      requestAnimationFrame(() => {
        focusTarget.focus({ preventScroll: true })
      })
    }
  }
  const handleChecklistStepSelect = (step) => {
    if (!step) return
    setPreferredChecklistStepKey(step.key)
    jumpToTicketTarget(step.targetKey)
  }
  const applyExecutionRoute = ({ orderType: nextOrderType, timeInForce: nextTimeInForce }) => {
    setTradeTicket((state) => {
      const resolvedOrderType = nextOrderType ?? state.orderType
      const resolvedTimeInForce = nextTimeInForce ?? state.timeInForce
      const anchorPrice = activeExecutionPrice
      return {
        ...state,
        orderType: resolvedOrderType,
        timeInForce: resolvedTimeInForce,
        limitPrice:
          ['limit', 'stop_limit'].includes(resolvedOrderType) &&
          toNumber(state.limitPrice) === null &&
          anchorPrice !== null
            ? Number(anchorPrice).toFixed(2)
            : state.limitPrice,
        stopPrice:
          ['stop_market', 'stop_limit'].includes(resolvedOrderType) &&
          toNumber(state.stopPrice) === null &&
          anchorPrice !== null
            ? Number(anchorPrice).toFixed(2)
            : state.stopPrice,
        trailingPercent:
          resolvedOrderType === 'trailing_stop' && toNumber(state.trailingPercent) === null
            ? '1.0'
            : state.trailingPercent,
      }
    })
  }
  const markExecutionReviewed = () => {
    if (!executionReviewSnapshot) return
    setExecutionReviewBaseline(executionReviewSnapshot)
  }
  const currentTickerOrderEvents = useMemo(() => {
    const activeTicker = String(form.ticker || '').trim().toUpperCase()
    if (!activeTicker) return []

    const items = Array.isArray(dashboard?.portfolio?.order_events?.items)
      ? dashboard.portfolio.order_events.items
      : []
    return items
      .filter((row) => String(row?.ticker || '').trim().toUpperCase() === activeTicker)
      .slice(0, 6)
  }, [dashboard?.portfolio?.order_events?.items, form.ticker])
  const currentTickerPendingOrders = useMemo(() => {
    const activeTicker = String(form.ticker || '').trim().toUpperCase()
    if (!activeTicker) return []

    const items = Array.isArray(dashboard?.portfolio?.pending_orders)
      ? dashboard.portfolio.pending_orders
      : []
    return items
      .filter((row) => String(row?.ticker || '').trim().toUpperCase() === activeTicker)
      .sort((left, right) => {
        const leftTime = new Date(left?.updated_at || left?.submitted_at || 0).getTime()
        const rightTime = new Date(right?.updated_at || right?.submitted_at || 0).getTime()
        return (Number.isFinite(rightTime) ? rightTime : 0) - (Number.isFinite(leftTime) ? leftTime : 0)
      })
  }, [dashboard?.portfolio?.pending_orders, form.ticker])
  const currentTickerOpenOptionTrades = useMemo(() => {
    const activeTicker = String(form.ticker || '').trim().toUpperCase()
    if (!activeTicker) return []

    const items = Array.isArray(dashboard?.portfolio?.open_trades)
      ? dashboard.portfolio.open_trades
      : []
    return items
      .filter((row) => {
        const ticker = String(row?.ticker || '').trim().toUpperCase()
        const instrumentType = normalizeInstrumentType(row?.instrument_type)
        return ticker === activeTicker && instrumentType === 'listed_option'
      })
      .sort((left, right) => {
        const leftTime = new Date(left?.opened_at || 0).getTime()
        const rightTime = new Date(right?.opened_at || 0).getTime()
        return (Number.isFinite(rightTime) ? rightTime : 0) - (Number.isFinite(leftTime) ? leftTime : 0)
      })
  }, [dashboard?.portfolio?.open_trades, form.ticker])
  const activePendingOrder = currentTickerPendingOrders[0] || null
  const activeOptionPendingOrder =
    currentTickerPendingOrders.find(
      (row) => normalizeInstrumentType(row?.instrument_type) === 'listed_option',
    ) || null
  const activeOptionOpenTrade = currentTickerOpenOptionTrades[0] || null
  const latestBackendOrderEvent = currentTickerOrderEvents[0] || null
  const optionExecutionReviewPanel = useMemo(
    () =>
      buildOptionExecutionReviewPanel({
        executionReviewSnapshot,
        latestBackendOrderEvent,
        activePendingOrder,
      }),
    [activePendingOrder, executionReviewSnapshot, latestBackendOrderEvent],
  )
  const deskOptionCards = useMemo(() => {
    const cards = []
    const contractSymbol = String(optionContract?.contract_symbol || '').trim()
    const expiration = String(optionContract?.expiration || '').trim()
    const strike = toNumber(optionContract?.strike)
    const optionMid = toNumber(optionContract?.mid)
    const optionSpreadPct = toNumber(optionContract?.spread_pct)
    const optionVehicleSummary = buildVehicleSelectionSummary({
      vehicleRecommendation: optionReport?.vehicle_recommendation,
      vehicleReason: optionReport?.vehicle_reason,
      optionExecutionProfile: optionReport?.option_execution_profile,
      fallbackInstrumentType: 'listed_option',
    })
    const optionShape = [
      formatLabel(optionRight || 'call', 'Call'),
      expiration || '--',
      strike === null ? '--' : formatPrice(strike),
    ].join(' | ')

    if (contractSymbol) {
      cards.push({
        key: 'recommended',
        label: 'Recommended option',
        value: contractSymbol,
        detail: `${optionShape} | ${optionVehicleSummary.label}`,
        meta: [
          optionMid === null ? null : `Mid ${formatPrice(optionMid)}`,
          optionSpreadPct === null ? null : `${formatPercent(optionSpreadPct, 1)} spread`,
          optionVehicleSummary.executionSummary.qualityLabel,
        ]
          .filter(Boolean)
          .join(' | '),
      })
    } else {
      cards.push({
        key: 'recommended',
        label: 'Recommended option',
        value: 'No clean contract',
        detail: String(form.ticker || '').trim().toUpperCase() || '--',
        meta: optionVehicleSummary.reason,
      })
    }

    cards.push({
      key: 'execution',
      label: 'Option execution',
      value: `${optionVehicleSummary.executionSummary.qualityLabel} | ${optionVehicleSummary.executionSummary.scoreLabel}`,
      detail: optionVehicleSummary.label,
      meta:
        optionVehicleSummary.executionSummary.rejectSummary ||
        optionVehicleSummary.executionSummary.metaSummary ||
        optionVehicleSummary.reason,
    })

    if (activeOptionPendingOrder) {
      const units = toNumber(
        activeOptionPendingOrder.remaining_contracts ?? activeOptionPendingOrder.suggested_contracts,
      )
      cards.push({
        key: 'working',
        label: 'Working option order',
        value:
          String(activeOptionPendingOrder.contract_symbol || '').trim() ||
          String(activeOptionPendingOrder.ticker || '').trim().toUpperCase() ||
          '--',
        detail: [
          formatOrderTypeLabel(activeOptionPendingOrder.order_type),
          units === null ? '--' : `${formatShares(units)} contracts`,
          latestBackendOrderEvent?.label || 'Working',
        ].join(' | '),
        meta: [
          toNumber(activeOptionPendingOrder.limit_price) === null
            ? null
            : `Limit ${formatPrice(activeOptionPendingOrder.limit_price)}`,
          activeOptionPendingOrder.updated_at || activeOptionPendingOrder.submitted_at
            ? `Updated ${formatEventTime(
                activeOptionPendingOrder.updated_at || activeOptionPendingOrder.submitted_at,
              )}`
            : null,
        ]
          .filter(Boolean)
          .join(' | '),
      })
    } else {
      cards.push({
        key: 'working',
        label: 'Working option order',
        value: 'None',
        detail: 'No option order is currently working for this ticker.',
        meta: 'No option order is currently working for this ticker.',
      })
    }

    if (activeOptionOpenTrade) {
      const units = toNumber(activeOptionOpenTrade.suggested_contracts)
      const entryMid = toNumber(activeOptionOpenTrade.contract_mid_at_open)
      const positionCost = toNumber(activeOptionOpenTrade.position_cost)
      cards.push({
        key: 'open',
        label: 'Open option position',
        value:
          String(activeOptionOpenTrade.contract_symbol || '').trim() ||
          String(activeOptionOpenTrade.ticker || '').trim().toUpperCase() ||
          '--',
        detail: [
          units === null ? '--' : `${formatShares(units)} contracts`,
          activeOptionOpenTrade.status || 'Open',
        ].join(' | '),
        meta: [
          entryMid === null ? null : `Entry ${formatPrice(entryMid)}`,
          positionCost === null ? null : `Cost ${formatPrice(positionCost)}`,
        ]
          .filter(Boolean)
          .join(' | '),
      })
    } else {
      cards.push({
        key: 'open',
        label: 'Open option position',
        value: 'None',
        detail: 'No option position is open for this ticker.',
        meta: 'Open option positions will appear here once routed and filled.',
      })
    }

    return cards
  }, [
    activeOptionOpenTrade,
    activeOptionPendingOrder,
    form.ticker,
    latestBackendOrderEvent?.label,
    optionContract?.contract_symbol,
    optionContract?.expiration,
    optionContract?.mid,
    optionContract?.spread_pct,
    optionContract?.strike,
    optionReport?.option_execution_profile,
    optionReport?.reject_reason,
    optionReport?.vehicle_reason,
    optionReport?.vehicle_recommendation,
    optionRight,
  ])
  const deskOptionActivityCards = useMemo(() => {
    const pendingOptionOrders = (
      Array.isArray(dashboard?.portfolio?.pending_orders) ? dashboard.portfolio.pending_orders : []
    )
      .filter((row) => normalizeInstrumentType(row?.instrument_type) === 'listed_option')
      .sort((left, right) => {
        const leftTime = new Date(left?.updated_at || left?.submitted_at || 0).getTime()
        const rightTime = new Date(right?.updated_at || right?.submitted_at || 0).getTime()
        return (Number.isFinite(rightTime) ? rightTime : 0) - (Number.isFinite(leftTime) ? leftTime : 0)
      })

    const openOptionTrades = (
      Array.isArray(dashboard?.portfolio?.open_trades) ? dashboard.portfolio.open_trades : []
    )
      .filter((row) => normalizeInstrumentType(row?.instrument_type) === 'listed_option')
      .sort((left, right) => {
        const leftTime = new Date(left?.opened_at || 0).getTime()
        const rightTime = new Date(right?.opened_at || 0).getTime()
        return (Number.isFinite(rightTime) ? rightTime : 0) - (Number.isFinite(leftTime) ? leftTime : 0)
      })

    const cards = [
      ...pendingOptionOrders.slice(0, 2).map((row, index) => {
        const units = toNumber(row?.remaining_contracts ?? row?.suggested_contracts)
        return {
          key: `pending-${row?.order_id || row?.contract_symbol || row?.ticker || index}`,
          label: 'Pending option order',
          value:
            String(row?.contract_symbol || '').trim() ||
            String(row?.ticker || '').trim().toUpperCase() ||
            '--',
          detail: [
            String(row?.ticker || '').trim().toUpperCase() || '--',
            formatOrderTypeLabel(row?.order_type),
            units === null ? '--' : `${formatShares(units)} contracts`,
          ].join(' | '),
          meta: [
            toNumber(row?.limit_price) === null ? null : `Limit ${formatPrice(row.limit_price)}`,
            row?.updated_at || row?.submitted_at
              ? `Updated ${formatEventTime(row.updated_at || row.submitted_at)}`
              : null,
          ]
            .filter(Boolean)
            .join(' | '),
        }
      }),
      ...openOptionTrades.slice(0, 2).map((row, index) => {
        const units = toNumber(row?.suggested_contracts)
        const entryMid = toNumber(row?.contract_mid_at_open)
        return {
          key: `open-${row?.trade_id || row?.contract_symbol || row?.ticker || index}`,
          label: 'Open option position',
          value:
            String(row?.contract_symbol || '').trim() ||
            String(row?.ticker || '').trim().toUpperCase() ||
            '--',
          detail: [
            String(row?.ticker || '').trim().toUpperCase() || '--',
            units === null ? '--' : `${formatShares(units)} contracts`,
            row?.status || 'Open',
          ].join(' | '),
          meta: [
            entryMid === null ? null : `Entry ${formatPrice(entryMid)}`,
            toNumber(row?.position_cost) === null ? null : `Cost ${formatPrice(row.position_cost)}`,
          ]
            .filter(Boolean)
            .join(' | '),
        }
      }),
    ]

    if (cards.length) return cards

    return [
      {
        key: 'empty',
        label: 'Option activity',
        value: 'No live option trades',
        detail: 'No option orders or positions are currently active in the account.',
        meta: 'Pending and open option activity will appear here across the whole desk.',
      },
    ]
  }, [dashboard?.portfolio?.open_trades, dashboard?.portfolio?.pending_orders])

  const appendActionHistory = (status) => {
    const nextEntry = buildActionHistoryEntry({
      status,
      ticker: form.ticker,
      activePendingOrder,
      selectedChartPoint,
      sendConfidence,
      tradeTicket,
    })
    setActionHistory((current) => [nextEntry, ...current].slice(0, 8))
  }

  const handlePrimaryAction = async () => {
    if (reviewOnlyMode) {
      pushToast(
        capitalPreservationSummary.detail ||
          'The desk is in review-only mode until the next regular session.',
        'error',
      )
      return
    }

    if (activePendingOrder) {
      if (!canOpenTrade || pendingOrderActionKey !== '') return
      if (!actionConfirmArmed) {
        appendActionHistory('armed')
        setActionConfirmArmed(true)
        return
      }
      appendActionHistory('confirmed')
      setActionConfirmArmed(false)
      await handleReplaceWorkingOrder()
      return
    }

    if (!canOpenTrade) return
    if (!actionConfirmArmed) {
      appendActionHistory('armed')
      setActionConfirmArmed(true)
      return
    }
    appendActionHistory('confirmed')
    setActionConfirmArmed(false)
    await handleOpenTrade()
  }

  const handleChoiceRowKeyDown = (event) => {
    const { key, currentTarget } = event
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(key)) {
      return
    }

    const buttons = Array.from(currentTarget.querySelectorAll('button:not(:disabled)'))
    if (!buttons.length) {
      return
    }

    const currentIndex = buttons.indexOf(document.activeElement)
    if (currentIndex === -1) {
      return
    }

    event.preventDefault()

    let nextIndex = currentIndex
    if (key === 'Home') {
      nextIndex = 0
    } else if (key === 'End') {
      nextIndex = buttons.length - 1
    } else if (key === 'ArrowLeft' || key === 'ArrowUp') {
      nextIndex = currentIndex === 0 ? buttons.length - 1 : currentIndex - 1
    } else if (key === 'ArrowRight' || key === 'ArrowDown') {
      nextIndex = currentIndex === buttons.length - 1 ? 0 : currentIndex + 1
    }

    buttons[nextIndex]?.focus()
  }
  useEffect(() => {
    const currentRoute = {
      orderType: tradeTicket.orderType,
      timeInForce: tradeTicket.timeInForce,
    }
    const previousRoute = previousRouteRef.current
    if (
      previousRoute.orderType !== currentRoute.orderType ||
      previousRoute.timeInForce !== currentRoute.timeInForce
    ) {
      setLastRouteChange({
        previous: previousRoute,
        current: currentRoute,
      })
      previousRouteRef.current = currentRoute
    }
  }, [tradeTicket.orderType, tradeTicket.timeInForce])
  const orderNeedsLimitPrice = ['limit', 'stop_limit'].includes(tradeTicket.orderType)
  const orderNeedsStopPrice = ['stop_market', 'stop_limit'].includes(tradeTicket.orderType)
  const orderNeedsTrailingPercent = tradeTicket.orderType === 'trailing_stop'
  const tradePreviewPayload = useMemo(() => {
    const normalizedLivePrice = toNumber(activeExecutionPrice)
    const normalizedAccountSize = toNumber(tradeTicket.accountSize)
    const normalizedRiskPercent = toNumber(tradeTicket.riskPercent)
    const normalizedLimitPrice = orderNeedsLimitPrice ? toNumber(tradeTicket.limitPrice) : null
    const normalizedStopPrice = orderNeedsStopPrice ? toNumber(tradeTicket.stopPrice) : null
    const normalizedTrailingPercent = orderNeedsTrailingPercent ? toNumber(tradeTicket.trailingPercent) : null
    if (
      !report?.ticker ||
      normalizedLivePrice === null ||
      normalizedAccountSize === null ||
      normalizedRiskPercent === null
    ) {
      return null
    }
    const optionContractStrike = toNumber(contract.strike)
    const optionContractMid = toNumber(contract.mid)
    if (normalizedInstrumentType === 'listed_option') {
      if (
        !normalizedOptionStrategy ||
        !optionRight ||
        !contract.contract_symbol ||
        !contract.expiration ||
        optionContractStrike === null ||
        optionContractMid === null
      ) {
        return null
      }
    }
    return {
      ticker: report.ticker,
      interval: form.interval,
      horizon: normalizeTradeTicketHorizon(form.horizon),
      account_target_type: profileTradingContext.effectiveAccountTargetType,
      linked_account_id:
        profileTradingContext.effectiveAccountTargetType === 'linked_client'
          ? profileTradingContext.effectiveLinkedAccountId || null
          : null,
      live_price: normalizedLivePrice,
      account_size: normalizedAccountSize,
      risk_percent: normalizedRiskPercent,
      instrument_type: normalizedInstrumentType,
      broker_side:
        normalizedInstrumentType === 'listed_option'
          ? optionStrategyBrokerSide(normalizedOptionStrategy)
          : 'buy',
      option_strategy: normalizedInstrumentType === 'listed_option' ? normalizedOptionStrategy : null,
      option_right: normalizedInstrumentType === 'listed_option' ? optionRight : null,
      contract_symbol:
        normalizedInstrumentType === 'listed_option'
          ? contract.contract_symbol || null
          : `EQUITY:${report.ticker}`,
      contract_expiration:
        normalizedInstrumentType === 'listed_option' ? contract.expiration || null : null,
      contract_strike: normalizedInstrumentType === 'listed_option' ? optionContractStrike : null,
      contract_bid: normalizedInstrumentType === 'listed_option' ? toNumber(contract.bid) : null,
      contract_ask: normalizedInstrumentType === 'listed_option' ? toNumber(contract.ask) : null,
      contract_mid: normalizedInstrumentType === 'listed_option' ? optionContractMid : null,
      contract_spread_pct: normalizedInstrumentType === 'listed_option' ? toNumber(contract.spread_pct) : null,
      contract_volume:
        normalizedInstrumentType === 'listed_option'
          ? Math.trunc(toNumber(contract.volume) ?? 0)
          : null,
      contract_open_interest:
        normalizedInstrumentType === 'listed_option'
          ? Math.trunc(toNumber(contract.open_interest) ?? 0)
          : null,
      contract_quote_timestamp:
        normalizedInstrumentType === 'listed_option'
          ? contract.quote_timestamp || contract.timestamp || null
          : null,
      order_type: tradeTicket.orderType,
      time_in_force: tradeTicket.timeInForce,
      limit_price: normalizedLimitPrice,
      stop_price: normalizedStopPrice,
      trailing_percent: normalizedTrailingPercent,
      extended_hours: tradeTicket.timeInForce === 'day_ext',
      capital_preservation_mode: effectiveCapitalPreservationPolicy.enabled,
      tiny_account_mode: effectiveCapitalPreservationPolicy.tinyAccountMode,
      regular_hours_only: effectiveCapitalPreservationPolicy.regularHoursOnly,
      max_daily_loss_r: effectiveCapitalPreservationPolicy.maxDailyLossR,
      max_consecutive_losses: effectiveCapitalPreservationPolicy.maxConsecutiveLosses,
      max_open_positions: effectiveCapitalPreservationPolicy.maxOpenPositions,
      max_notional_per_trade: effectiveCapitalPreservationPolicy.maxNotionalPerTrade,
      equities_only: effectiveCapitalPreservationPolicy.equitiesOnly,
      limit_orders_only: effectiveCapitalPreservationPolicy.limitOrdersOnly,
      long_only: effectiveCapitalPreservationPolicy.longOnly,
      fractional_shares_only: effectiveCapitalPreservationPolicy.fractionalSharesOnly,
      execution_intent:
        profileTradingContext.effectiveAccountTargetType === 'personal'
          ? selectedExecutionIntent
          : defaultExecutionIntent,
    }
  }, [
    activeExecutionPrice,
    contract,
    defaultExecutionIntent,
    effectiveCapitalPreservationPolicy,
    form.horizon,
    form.interval,
    normalizedInstrumentType,
    normalizedOptionStrategy,
    optionRight,
    orderNeedsLimitPrice,
    orderNeedsStopPrice,
    orderNeedsTrailingPercent,
    profileTradingContext.effectiveAccountTargetType,
    profileTradingContext.effectiveLinkedAccountId,
    report?.ticker,
    selectedExecutionIntent,
    tradeTicket.accountSize,
    tradeTicket.limitPrice,
    tradeTicket.orderType,
    tradeTicket.riskPercent,
    tradeTicket.stopPrice,
    tradeTicket.timeInForce,
    tradeTicket.trailingPercent,
  ])
  useEffect(() => {
    if (!tradePreviewPayload) {
      setTradePreview(null)
      setTradePreviewError('')
      setTradePreviewLoading(false)
      return undefined
    }
    const now = Date.now()
    if (
      tradePreviewInFlightRef.current ||
      now - lastTradePreviewAtRef.current < TRADE_PREVIEW_MIN_REFRESH_MS
    ) {
      return undefined
    }
    let active = true
    const timeout = window.setTimeout(() => {
      tradePreviewInFlightRef.current = true
      lastTradePreviewAtRef.current = Date.now()
      setTradePreviewLoading(true)
      previewTrade(tradePreviewPayload)
        .then((payload) => {
          if (!active) return
          setTradePreview(payload)
          setTradePreviewError('')
        })
        .catch((previewError) => {
          if (!active) return
          setTradePreview(null)
          setTradePreviewError(previewError?.message || 'Pre-trade preview is unavailable.')
        })
        .finally(() => {
          tradePreviewInFlightRef.current = false
          if (active) setTradePreviewLoading(false)
        })
    }, 350)
    return () => {
      active = false
      window.clearTimeout(timeout)
    }
  }, [tradePreviewPayload])
  const parameterBlockingReasons = useMemo(() => {
    const reasons = []
    if (orderNeedsLimitPrice && toNumber(tradeTicket.limitPrice) === null) {
      reasons.push(
        createGuardrailReason(
          'A priced order needs a limit price before it can route.',
          'limit-price',
          'Set limit price',
        ),
      )
    }
    if (orderNeedsStopPrice && toNumber(tradeTicket.stopPrice) === null) {
      reasons.push(
        createGuardrailReason(
          'Stop-based orders need a stop trigger before they can route.',
          'stop-price',
          'Set stop price',
        ),
      )
    }
    if (orderNeedsTrailingPercent && toNumber(tradeTicket.trailingPercent) === null) {
      reasons.push(
        createGuardrailReason(
          'Trailing-stop tickets need a trail percentage before they can route.',
          'trail-percent',
          'Set trail percent',
        ),
      )
    }
    return reasons
  }, [
    orderNeedsLimitPrice,
    orderNeedsStopPrice,
    orderNeedsTrailingPercent,
    tradeTicket.limitPrice,
    tradeTicket.stopPrice,
    tradeTicket.trailingPercent,
  ])
  const blockingTicketReasons = useMemo(
    () => [...tradeGuardrails.blockingReasons, ...parameterBlockingReasons],
    [parameterBlockingReasons, tradeGuardrails.blockingReasons],
  )
  const ticketChecklist = useMemo(
    () =>
      buildTicketChecklist({
        instrumentType: tradeTicket.instrumentType,
        blockingReasons: blockingTicketReasons,
        warningReasons: tradeGuardrails.warningReasons,
        routeComparison,
        positionPreview,
        riskReward,
        contract,
        orderNeedsLimitPrice,
        orderNeedsStopPrice,
        orderNeedsTrailingPercent,
      }),
    [
      blockingTicketReasons,
      contract,
      orderNeedsLimitPrice,
      orderNeedsStopPrice,
      orderNeedsTrailingPercent,
      positionPreview,
      riskReward,
      routeComparison,
      tradeGuardrails.warningReasons,
      tradeTicket.instrumentType,
    ],
  )
  const checklistMemoryKey = useMemo(
    () => executionChecklistKeyFor(form.ticker, tradeTicket.instrumentType),
    [form.ticker, tradeTicket.instrumentType],
  )
  const activeChecklistStepKey = useMemo(() => {
    const steps = ticketChecklist.steps || []
    if (!steps.length) return ''

    const unresolvedStepKey =
      steps.find((step) => step.tone === 'negative')?.key ||
      steps.find((step) => step.tone === 'warning')?.key ||
      steps[0]?.key ||
      ''

    if (!preferredChecklistStepKey) {
      return unresolvedStepKey
    }

    const preferredStep = steps.find((step) => step.key === preferredChecklistStepKey)
    if (!preferredStep) {
      return unresolvedStepKey
    }

    if (preferredStep.tone === 'positive' && unresolvedStepKey) {
      return unresolvedStepKey
    }

    return preferredStep.key
  }, [preferredChecklistStepKey, ticketChecklist.steps])
  const checklistIsComplete =
    ticketChecklist.totalCount > 0 && ticketChecklist.clearedCount === ticketChecklist.totalCount
  const executionReviewDrift = useMemo(
    () =>
      buildExecutionReviewDrift({
        baseline: executionReviewBaseline,
        current: executionReviewSnapshot,
      }),
    [executionReviewBaseline, executionReviewSnapshot],
  )
  const activePortfolioTradeCount = toNumber(portfolioSummary.active_trade_count) ?? 0
  const portfolioPendingOrderCount = toNumber(capitalPreservationMetrics.pending_order_count) ?? 0
  const isFirstCapitalState = activePortfolioTradeCount === 0 && portfolioPendingOrderCount === 0
  const promotionGateBlocksFirstCapital =
    liveRouteSelected && isFirstCapitalState && promotionGateSummary?.allowsPromotion === false
  const executionRouteSummary = useMemo(
    () =>
      buildExecutionRouteSummary({
        executionIntent: selectedExecutionIntent,
        promotionGateSummary,
        intradayExecutionPlan,
        profileTradingContext,
      }),
    [intradayExecutionPlan, profileTradingContext, promotionGateSummary, selectedExecutionIntent],
  )
  const liveBrokerDeskStatus = useMemo(
    () => buildLiveBrokerDeskStatus(automationSnapshot),
    [automationSnapshot],
  )
  const deskReadiness = useMemo(() => {
    const tones = [
      promotionGateSummary?.tone,
      capitalPreservationSummary?.tone,
      executionRouteSummary?.tone,
      liveBrokerDeskStatus?.tone,
    ]
    const tone = tones.includes('negative')
      ? 'negative'
      : tones.includes('warning')
        ? 'warning'
        : tones.includes('positive')
          ? 'positive'
          : 'info'
    const title = reviewOnlyMode
      ? 'Desk posture'
      : liveRouteSelected
        ? 'Go-live readiness'
        : paperRouteSelected
          ? 'Paper route state'
          : 'Desk route state'
    const headline = reviewOnlyMode
      ? 'Review-only mode is active'
      : liveRouteSelected
        ? promotionGateSummary?.allowsPromotion
          ? 'Desk is clear for tightly controlled first capital'
          : 'Live route remains locked'
        : paperRouteSelected
          ? 'Paper route is unlocked'
          : 'Desk route is unlocked'
    const summary = reviewOnlyMode
      ? capitalPreservationSummary?.detail || 'The desk is locked to review-only mode until the next regular session.'
      : liveRouteSelected
        ? promotionGateSummary?.allowsPromotion
          ? 'Replay depth, slippage, and control posture are clearing. Keep first capital intentionally small and keep one setup in focus.'
          : promotionGateSummary?.action || promotionGateSummary?.detail || 'Replay evidence and fill drift still need work before first capital.'
        : paperRouteSelected
          ? 'Paper routing is available now. Use paper fills, drift, and resolved outcomes to build the live rollout sample.'
          : 'Desk routing is available without broker execution. Use it to review setups and keep the route logic stable.'

    return {
      tone,
      title,
      headline,
      summary,
      actionLabel: reviewOnlyMode
        ? 'Open trades'
        : liveRouteSelected
          ? promotionGateSummary?.allowsPromotion
            ? 'Open route controls'
            : 'Open review loop'
          : paperRouteSelected
          ? 'Open route controls'
          : 'Open watchlist',
      actionRoute: reviewOnlyMode
        ? '/trades'
        : liveRouteSelected
          ? promotionGateSummary?.allowsPromotion
            ? '/trades'
            : '/journal'
          : paperRouteSelected
          ? '/trades'
          : '/watchlist',
      items: [
        {
          key: 'gate',
          tone: promotionGateSummary?.tone || 'warning',
          label: liveRouteSelected ? 'Live gate' : 'Paper sample',
          value: liveRouteSelected
            ? promotionGateSummary?.label || 'Paper gate review'
            : `${promotionGateSummary?.resolvedCount ?? 0} resolved`,
          detail: liveRouteSelected
            ? `${promotionGateSummary?.resolvedCount ?? 0} resolved | ${promotionGateSummary?.winRateLabel || '--'} win rate | avg ${promotionGateSummary?.averageAbsSlippageLabel || '--'}`
            : `${promotionGateSummary?.winRateLabel || '--'} win rate | avg ${promotionGateSummary?.averageAbsSlippageLabel || '--'} drift`,
        },
        {
          key: 'controls',
          tone: capitalPreservationSummary?.tone || 'warning',
          label: 'Control posture',
          value: capitalPreservationSummary?.label || 'Risk review',
          detail: reviewOnlyMode
            ? `Reset ${capitalPreservationSummary?.reviewOnlyResetLabel || 'next session'}`
            : `${capitalPreservationSummary?.openPositionCount ?? 0} open | ${capitalPreservationSummary?.pendingOrderCount ?? 0} working | ${capitalPreservationSummary?.consecutiveLosses ?? 0} losses`,
        },
        {
          key: 'route',
          tone: executionRouteSummary?.tone || 'info',
          label: 'Route mode',
          value: executionRouteSummary?.label || 'Desk route',
          detail: executionRouteSummary?.locked
            ? `${executionRouteSummary?.pathLabel || 'Route'} locked`
            : `${executionRouteSummary?.pathLabel || 'Route'} active`,
        },
        {
          key: 'connected-live',
          tone: liveBrokerDeskStatus?.tone || 'info',
          label: liveBrokerDeskStatus?.label || 'Alpaca live',
          value: liveBrokerDeskStatus?.value || 'Standby',
          detail: liveBrokerDeskStatus?.detail || 'Alpaca live state is not available yet.',
        },
      ],
    }
  }, [
    liveBrokerDeskStatus,
    capitalPreservationSummary,
    executionRouteSummary,
    liveRouteSelected,
    paperRouteSelected,
    promotionGateSummary,
    reviewOnlyMode,
  ])
  const brokerLiveRouteBlocked =
    !activePendingOrder && Boolean(executionRouteSummary?.locked)
  const ticketPrimaryMessage =
    blockingTicketReasons[0]?.message ||
    (promotionGateBlocksFirstCapital ? promotionGateSummary?.action || promotionGateSummary?.detail : null) ||
    (brokerLiveRouteBlocked ? executionRouteSummary?.detail : null) ||
    tradeGuardrails.warningReasons[0]?.message ||
    positionPreview?.statusText ||
    'The ticket is ready for review.'
  const signalEligibleForRoute = liveRouteSelected
    ? String(report?.trade_decision || '').toUpperCase() === 'VALID TRADE'
    : Boolean(report?.ticker)
  const canOpenTrade =
    signalEligibleForRoute &&
    activeExecutionPrice !== null &&
    positionPreview?.affordable &&
    positionPreview?.suggestedContracts > 0 &&
    blockingTicketReasons.length === 0 &&
    !profileTradingContext.profileTradingLockedReason &&
    !promotionGateBlocksFirstCapital &&
    !brokerLiveRouteBlocked
  const sendConfidence = useMemo(
    () =>
      buildSendConfidence({
        canOpenTrade,
        checklistIsComplete,
        routeComparison,
        executionReviewDrift,
        warningReasons: tradeGuardrails.warningReasons,
        positionPreview,
        activePendingOrder,
        selectedChartPoint,
        capitalPreservationSummary,
        executionRouteSummary,
      }),
    [
      activePendingOrder,
      capitalPreservationSummary,
      canOpenTrade,
      checklistIsComplete,
      executionReviewDrift,
      positionPreview,
      routeComparison,
      selectedChartPoint,
      tradeGuardrails.warningReasons,
      executionRouteSummary,
    ],
  )
  const actionConfirmation = useMemo(
    () =>
      buildActionConfirmation({
        activePendingOrder,
        selectedChartPoint,
        sendConfidence,
        actionConfirmArmed,
        executionRouteSummary,
      }),
    [
      actionConfirmArmed,
      activePendingOrder,
      executionRouteSummary,
      selectedChartPoint,
      sendConfidence,
    ],
  )
  const visibleActionHistory = useMemo(
    () =>
      actionHistory
        .filter((entry) => entry.ticker === String(form.ticker || '').trim().toUpperCase())
        .slice(0, 3),
    [actionHistory, form.ticker],
  )
  const helperContextCount =
    (routeChangeFeedback ? 1 : 0) +
    (executionReviewDrift ? 1 : 0) +
    (activePendingOrder ? 1 : 0) +
    (visibleActionHistory.length ? 1 : 0)
  const helperContextTone = useMemo(() => {
    if (executionReviewDrift?.tone === 'negative') return 'negative'
    if (executionReviewDrift?.tone === 'warning') return 'warning'
    if (routeChangeFeedback?.tone === 'negative') return 'negative'
    if (routeChangeFeedback?.tone === 'warning') return 'warning'
    if (routeChangeFeedback?.tone === 'positive' || executionReviewDrift?.tone === 'positive') return 'positive'
    return 'info'
  }, [executionReviewDrift?.tone, routeChangeFeedback?.tone])
  useEffect(() => {
    const memory = loadExecutionChecklistMemory()
    const savedStepKey = memory[checklistMemoryKey]?.stepKey
    setPreferredChecklistStepKey(typeof savedStepKey === 'string' ? savedStepKey : '')
  }, [checklistMemoryKey])

  useEffect(() => {
    if (!checklistMemoryKey || !activeChecklistStepKey) return
    persistExecutionChecklistMemory(checklistMemoryKey, activeChecklistStepKey)
  }, [activeChecklistStepKey, checklistMemoryKey])

  useEffect(() => {
    if (!executionReviewSnapshot) return
    if (
      !executionReviewBaseline ||
      executionReviewBaseline.ticker !== executionReviewSnapshot.ticker ||
      executionReviewBaseline.instrumentType !== executionReviewSnapshot.instrumentType
    ) {
      setExecutionReviewBaseline(executionReviewSnapshot)
    }
  }, [executionReviewBaseline, executionReviewSnapshot])

  useEffect(() => {
    if (!checklistIsComplete) {
      setChecklistExpanded(false)
    }
  }, [checklistIsComplete, checklistMemoryKey])

  useEffect(() => {
    setActionConfirmArmed(false)
  }, [
    activePendingOrder?.order_id,
    canOpenTrade,
    form.ticker,
    selectedChartPoint?.price,
    selectedChartPoint?.timestamp,
    sendConfidence?.tone,
    tradeTicket.instrumentType,
    tradeTicket.limitPrice,
    tradeTicket.orderType,
    tradeTicket.riskPercent,
    tradeTicket.stopPrice,
    tradeTicket.timeInForce,
    tradeTicket.trailingPercent,
  ])
  useEffect(() => {
    setHelperContextExpanded(false)
  }, [form.ticker])
  const {
    status: streamStatus,
    error: streamError,
    meta: streamMeta,
    lastMessageAt,
    isLive: streamIsLive,
  } = useMarketStream({
    tickers: streamTickers,
    channels: ['trades', 'quotes'],
    enabled: autoRefresh && streamTickers.length > 0,
    onEvent: (event) => {
      const symbol = String(event?.symbol || '').toUpperCase()
      if (!symbol) return
      const activeForm = formRef.current
      const isActiveTicker = symbol === String(activeForm?.ticker || '').toUpperCase()
      const currentWatchlistSnapshot =
        streamEventBufferRef.current.watchlist[symbol] ||
        watchlistLiveMapRef.current[symbol] ||
        {}

      if (event.type === 'trade') {
        const quoteSnapshot =
          currentWatchlistSnapshot || (isActiveTicker ? selectedQuoteRef.current : null)
        const enrichedTrade = {
          ...event,
          price: toNumber(event.price),
          size: toNumber(event.size),
          notional:
            toNumber(event.price) !== null && toNumber(event.size) !== null
              ? Number(event.price) * Number(event.size)
              : null,
          side: inferTradeSide(event, quoteSnapshot),
          bid_price: toNumber(quoteSnapshot?.bid_price),
          ask_price: toNumber(quoteSnapshot?.ask_price),
        }
        const nextTape = updateStoredTradeTape(symbol, (current) =>
          [enrichedTrade, ...current].slice(0, 40),
        )
        streamEventBufferRef.current.watchlist[symbol] = {
          ...currentWatchlistSnapshot,
          price: enrichedTrade.price,
          timestamp: event.timestamp,
          size: enrichedTrade.size,
          history: appendTickerHistory(currentWatchlistSnapshot?.history, enrichedTrade.price, event.timestamp),
        }

        if (isActiveTicker) {
          streamEventBufferRef.current.activeTicker = symbol
          streamEventBufferRef.current.activeTrade = enrichedTrade
          streamEventBufferRef.current.activeTape = nextTape
          streamEventBufferRef.current.activeTradeEvent = event
        }
        queueStreamFlush()
      }

      if (event.type === 'quote') {
        const normalizedQuote = {
          ...event,
          bid_price: toNumber(event.bid_price),
          ask_price: toNumber(event.ask_price),
          bid_size: toNumber(event.bid_size),
          ask_size: toNumber(event.ask_size),
          spread: toNumber(event.spread),
        }
        streamEventBufferRef.current.watchlist[symbol] = {
          ...currentWatchlistSnapshot,
          bid_price: normalizedQuote.bid_price,
          ask_price: normalizedQuote.ask_price,
          bid_size: normalizedQuote.bid_size,
          ask_size: normalizedQuote.ask_size,
          spread: normalizedQuote.spread,
          timestamp: event.timestamp,
        }

        if (isActiveTicker) {
          streamEventBufferRef.current.activeTicker = symbol
          streamEventBufferRef.current.activeQuote = normalizedQuote
        }
        queueStreamFlush()
      }
    },
  })

  async function loadWorkspace({
    ticker,
    interval,
    horizon,
    instrumentType = 'equity',
    includeDashboard = false,
    silent = false,
    clearSelection = false,
  }) {
    const normalizedTicker = String(ticker || '').trim().toUpperCase()
    const normalizedInterval = supportedIntervals.includes(interval) ? interval : '5m'
    const normalizedHorizon = Number(horizon) || 5
    const normalizedInstrumentType = normalizeInstrumentType(instrumentType)
    const nextWorkspaceKey = workspaceSignature(
      normalizedTicker,
      normalizedInterval,
      normalizedHorizon,
      normalizedInstrumentType,
    )
    const requestMode = silent ? 'background' : includeDashboard ? 'manual+board' : 'manual'
    const requestStartedAt = Date.now()
    const activeLoad = workspaceLoadRequestRef.current

    if (
      activeLoad?.promise &&
      activeLoad.key === nextWorkspaceKey &&
      (silent || activeLoad.mode === 'background')
    ) {
      return activeLoad.promise
    }

    if (
      silent &&
      lastSilentWorkspaceLoadRef.current.key === nextWorkspaceKey &&
      requestStartedAt - lastSilentWorkspaceLoadRef.current.at < Math.max(pollMs - 1000, 5000)
    ) {
      return undefined
    }

    activeWorkspaceKey.current = nextWorkspaceKey
    const syncMode = silent ? 'background' : hasHydratedWorkspaceData ? 'manual' : 'initial'
    const allowDeferredChartHydration = !silent && syncMode === 'initial'

    const requestPromise = (async () => {
      if (!silent) {
        setAnalysisLoading(true)
        setForm((current) => {
          const currentTicker = String(current?.ticker || '').trim().toUpperCase()
          const currentInterval = String(current?.interval || '').trim().toLowerCase()
          const currentHorizon = Number(current?.horizon)
          if (
            currentTicker === normalizedTicker &&
            currentInterval === normalizedInterval &&
            currentHorizon === normalizedHorizon
          ) {
            return current
          }
          return {
            ticker: normalizedTicker,
            interval: normalizedInterval,
            horizon: normalizedHorizon,
          }
        })
      }
      setWorkspaceSyncMode(syncMode)

      if (clearSelection && !silent) {
        setSelectedChartPoint(null)
        setSelectedTrade(null)
        setSelectedQuote(null)
        setTradeTape(getStoredTradeTape(normalizedTicker))
        setPendingGuidePoint(null)
        applyCustomGuides([], { record: false, selectedId: null })
        resetDrawingHistory([])
        setSelectedGuideId(null)
        setLevelOverrides({})
        setToolMode('pan')
        chartViewportRef.current = null
        if (viewportCommitTimeoutRef.current) {
          window.clearTimeout(viewportCommitTimeoutRef.current)
          viewportCommitTimeoutRef.current = null
        }
        setChartViewport(null)
      }

      if (includeDashboard && !silent) {
        setBoardSyncing(true)
        void getDashboard('desk', dashboardQueryOptions)
          .then((payload) => {
            applyDashboardPayload(payload)
          })
          .catch(() => {
            // keep the current board state on transient failures
          })
          .finally(() => {
            setBoardSyncing(false)
          })
      }

      const shouldHydrateAnalysis =
        !silent ||
        !isUsableAnalysisPayload(analysis) ||
        String(formRef.current?.ticker || '').trim().toUpperCase() !== normalizedTicker

      const requestSequence = shouldHydrateAnalysis ? ++detailHydrationSequence.current : detailHydrationSequence.current
      if (shouldHydrateAnalysis) {
        analysisRefreshInFlight.current = true
        void analyzeTicker({
          ticker: normalizedTicker,
          interval: normalizedInterval,
          horizon: normalizedHorizon,
          regular_hours_only: preferences?.regularHoursOnly === true,
          instrument_type: normalizedInstrumentType,
          option_strategy: normalizedInstrumentType === 'listed_option' ? normalizedOptionStrategy : null,
          option_right: normalizedInstrumentType === 'listed_option' ? optionRight : null,
          contract_symbol:
            normalizedInstrumentType === 'listed_option'
              ? String(contract?.contract_symbol || '').trim() || null
              : null,
          include_live_price: true,
          include_history: false,
          include_contract_lookup: !silent || normalizedInstrumentType === 'listed_option',
          include_event_lookup: !silent,
          include_alignment: !silent,
          use_fast_model: true,
        })
          .then((payload) => {
            if (
              detailHydrationSequence.current !== requestSequence ||
              activeWorkspaceKey.current !== nextWorkspaceKey
            ) {
              return
            }
            if (!isUsableAnalysisPayload(payload)) {
              return
            }
            const nextPayload = {
              ...payload,
              settings: {
                ...(payload?.settings || {}),
                instrument_type: normalizedInstrumentType,
              },
            }
            setAnalysis((current) => mergeAnalysisPayload(current, nextPayload))
          })
          .catch(() => {
            // keep the chart-first load moving if analysis hydration lags or fails
          })
          .finally(() => {
            if (
              detailHydrationSequence.current === requestSequence &&
              activeWorkspaceKey.current === nextWorkspaceKey
            ) {
              analysisRefreshInFlight.current = false
              if (!silent) {
                setAnalysisLoading(false)
              }
            }
          })
      }

      const shouldHydrateOptionAnalysis =
        normalizedInstrumentType === 'listed_option' &&
        (
          !optionAnalysis ||
          !isUsableAnalysisPayload(optionAnalysis) ||
          String(formRef.current?.ticker || '').trim().toUpperCase() !== normalizedTicker
        )

      if (shouldHydrateOptionAnalysis) {
        const optionRequestSequence = ++optionHydrationSequence.current
        void analyzeTicker({
          ticker: normalizedTicker,
          interval: normalizedInterval,
          horizon: normalizedHorizon,
          regular_hours_only: preferences?.regularHoursOnly === true,
          instrument_type: 'listed_option',
          option_strategy: 'long_option',
          option_right: null,
          contract_symbol: null,
          include_live_price: true,
          include_history: false,
          include_contract_lookup: true,
          include_event_lookup: !silent,
          include_alignment: !silent,
          use_fast_model: true,
        })
          .then((payload) => {
            if (
              optionHydrationSequence.current !== optionRequestSequence ||
              activeWorkspaceKey.current !== nextWorkspaceKey ||
              !isUsableAnalysisPayload(payload)
            ) {
              return
            }
            const nextPayload = {
              ...payload,
              settings: {
                ...(payload?.settings || {}),
                instrument_type: 'listed_option',
              },
            }
            setOptionAnalysis(nextPayload)
          })
          .catch(() => {
            // keep the current option board state if parallel option analysis fails
          })
      }

      const chartRequest = getChart(
        normalizedTicker,
        normalizedInterval,
        initialChartPointsForInterval(normalizedInterval),
        preferences?.regularHoursOnly === true,
      ).then((payload) => sanitizeChartPayloadCandles(payload))

      const applyChartPayload = (nextChartPayload) => {
        if (!hasUsableChartPrices(nextChartPayload)) {
          if (activeWorkspaceKey.current === nextWorkspaceKey) {
            setChartPayload(null)
          }
          return false
        }
        if (activeWorkspaceKey.current === nextWorkspaceKey) {
          setChartPayload(nextChartPayload)
        }
        return true
      }

      try {
        if (allowDeferredChartHydration) {
          const quickChartPayload = await Promise.race([
            chartRequest,
            new Promise((resolve) => {
              window.setTimeout(() => resolve(null), INITIAL_CHART_HYDRATION_WAIT_MS)
            }),
          ])
          if (quickChartPayload) {
            applyChartPayload(quickChartPayload)
          } else {
            void chartRequest
              .then((nextChartPayload) => {
                applyChartPayload(nextChartPayload)
              })
              .catch(() => {
                // keep the first desk load usable even if the initial chart request lags or fails
              })
          }
          setWorkspaceSyncMode('idle')
          return
        }

        const nextChartPayload = await chartRequest
        applyChartPayload(nextChartPayload)
        setWorkspaceSyncMode('idle')
      } catch (err) {
        if (
          shouldHydrateAnalysis &&
          detailHydrationSequence.current === requestSequence &&
          activeWorkspaceKey.current === nextWorkspaceKey
        ) {
          analysisRefreshInFlight.current = false
          if (!silent) {
            setAnalysisLoading(false)
          }
        }
        setWorkspaceSyncMode('idle')
        throw err
      } finally {
        if (silent) {
          lastSilentWorkspaceLoadRef.current = {
            key: nextWorkspaceKey,
            at: Date.now(),
          }
        }
      }
    })()

    workspaceLoadRequestRef.current = {
      key: nextWorkspaceKey,
      mode: requestMode,
      startedAt: requestStartedAt,
      promise: requestPromise,
    }

    try {
      return await requestPromise
    } finally {
      if (workspaceLoadRequestRef.current.promise === requestPromise) {
        workspaceLoadRequestRef.current = { key: '', mode: '', startedAt: 0, promise: null }
      }
    }
  }

  async function recordTickerActivity(ticker) {
    try {
      await recordRecentTicker(ticker)
    } catch {
      // keep the workspace responsive even if the hub cannot update
    }
  }

  async function focusTickerInPlace(ticker, interval = form.interval, horizon = form.horizon) {
    const normalizedTicker = String(ticker || '').trim().toUpperCase()
    const normalizedInterval = supportedIntervals.includes(interval) ? interval : '5m'
    const normalizedHorizon = Number(horizon) || 5
    if (!isTickerValid(normalizedTicker)) {
      return
    }

    setError('')
    setFormErrors({})
    setDeskActionIssue(null)
    setForm({
      ticker: normalizedTicker,
      interval: normalizedInterval,
      horizon: normalizedHorizon,
    })
    setSelectedChartPoint(null)
    setSelectedTrade(null)
    setSelectedQuote(null)
    setTradeTape(getStoredTradeTape(normalizedTicker))
    setPendingGuidePoint(null)
    applyCustomGuides([], { record: false, selectedId: null })
    resetDrawingHistory([])
    setSelectedGuideId(null)
    setLevelOverrides({})
    setToolMode('pan')
    chartViewportRef.current = null
    if (viewportCommitTimeoutRef.current) {
      window.clearTimeout(viewportCommitTimeoutRef.current)
      viewportCommitTimeoutRef.current = null
    }
    setChartViewport(null)

    const immediateRow = liveTickerLookup[normalizedTicker] || null
    if (hasUsableDeskRow(immediateRow)) {
      const seededChartPayload = buildDeskFallbackChartPayload({
        ticker: normalizedTicker,
        interval: normalizedInterval,
        row: immediateRow,
      })
      if (seededChartPayload) {
        setChartPayload(
          sanitizeChartPayloadCandles(
            seededChartPayload,
            toNumber(immediateRow?.live_price ?? immediateRow?.current_underlying_price ?? immediateRow?.close),
          ),
        )
      }

      const seededAnalysis = buildDeskFallbackAnalysis({
        ticker: normalizedTicker,
        interval: normalizedInterval,
        horizon: normalizedHorizon,
        row: immediateRow,
      })
      if (seededAnalysis) {
        setAnalysis(seededAnalysis)
      }
    }

    void loadWorkspace({
      ticker: normalizedTicker,
      interval: normalizedInterval,
      horizon: normalizedHorizon,
      includeDashboard: false,
      silent: true,
      clearSelection: false,
    }).catch(() => {
      // keep the current desk state if the in-place refresh fails
    })
    void recordTickerActivity(normalizedTicker)
  }

  async function focusTicker(ticker, interval = form.interval, horizon = form.horizon) {
    const normalizedTicker = String(ticker || '').trim().toUpperCase()
    if (!isTickerValid(normalizedTicker)) {
      setFormErrors((current) => ({ ...current, ticker: 'Enter a valid ticker with up to 8 letters, dots, or dashes.' }))
      setDeskActionIssue({
        tone: 'warning',
        title: 'Desk load needs a valid ticker',
        description: 'Fix the ticker in the desk controls before loading the chart and ticket rail.',
      })
      setError('Enter a valid ticker with up to 8 characters.')
      pushToast('Enter a valid ticker before loading the desk.', 'error')
      return
    }

    try {
      setError('')
      setFormErrors({})
      setDeskActionIssue(null)
      const immediateRow = liveTickerLookup[normalizedTicker] || null
      if (hasUsableDeskRow(immediateRow)) {
        const seededChartPayload = buildDeskFallbackChartPayload({
          ticker: normalizedTicker,
          interval,
          row: immediateRow,
        })
        if (seededChartPayload) {
          setChartPayload(
            sanitizeChartPayloadCandles(
              seededChartPayload,
              toNumber(immediateRow?.live_price ?? immediateRow?.current_underlying_price ?? immediateRow?.close),
            ),
          )
        }

        const seededAnalysis = buildDeskFallbackAnalysis({
          ticker: normalizedTicker,
          interval,
          horizon,
          row: immediateRow,
        })
        if (seededAnalysis) {
          setAnalysis(seededAnalysis)
        }
      } else {
        setChartPayload(null)
        setAnalysis(null)
      }
      await loadWorkspace({
        ticker: normalizedTicker,
        interval,
        horizon,
        includeDashboard: false,
        silent: false,
        clearSelection: true,
      })
      await recordTickerActivity(normalizedTicker)
      pushToast(`Loaded ${normalizedTicker} into the trading desk.`, 'success')
    } catch (err) {
      setAnalysisLoading(false)
      setError(err?.response?.data?.detail || err.message || 'Failed to load the ticker desk.')
      pushToast(err?.response?.data?.detail || err.message || 'Failed to load the ticker desk.', 'error')
    }
  }

  useEffect(() => {
    if (location.pathname !== '/') return
    const params = new URLSearchParams(location.search)
    const resolvedRepair = String(params.get('repairResolved') || '').trim().toLowerCase()
    const repairTicker = String(params.get('repairTicker') || '').trim().toUpperCase()
    const repairTitle = String(params.get('repairTitle') || '').trim()
    const requestedTicker = String(params.get('ticker') || '').trim().toUpperCase()
    const workflowFrom = String(params.get('workflowFrom') || '').trim().toLowerCase()
    const replaySource = String(params.get('replaySource') || '').trim().toLowerCase()
    const replayTitle = String(params.get('replayTitle') || '').trim()
    const replayStatus = String(params.get('replayStatus') || '').trim().toLowerCase()
    const notesReturn = String(params.get('notesReturn') || '').trim()
    const notesReturnTicker = String(params.get('notesReturnTicker') || '').trim().toUpperCase()
    const notesReturnTitle = String(params.get('notesReturnTitle') || '').trim()
    const notesReturnCompletion = String(params.get('notesReturnCompletion') || '').trim().toLowerCase()
    const notesReturnJournal = String(params.get('notesReturnJournal') || '').trim()
    const compareTickers = String(params.get('compareTickers') || '')
      .split(',')
      .map((value) => value.trim().toUpperCase())
      .filter(Boolean)
    const compareFocusTicker = String(params.get('compareFocusTicker') || '').trim().toUpperCase()
    const hasRepairNotice = Boolean(resolvedRepair || repairTicker || repairTitle)

    if (
      hasRepairNotice &&
      (resolvedRepair === '1' || resolvedRepair === 'true')
    ) {
      const repairKey = `${repairTicker}|${repairTitle}`
      if (repairNoticeRef.current !== repairKey) {
        repairNoticeRef.current = repairKey
        setResolvedRepairNotice({
          ticker: repairTicker || requestedTicker || form.ticker,
          title: repairTitle || 'Repair note resolved',
          detail: repairTicker
            ? `${repairTicker} repair note was resolved. The desk can reopen first-capital review if the setup still clears.`
            : 'The repair note was resolved. The desk can reopen first-capital review if the setup still clears.',
        })
        pushToast(
          repairTicker
            ? `${repairTicker} repair cleared. The desk is ready to reassess first capital.`
            : 'Repair cleared. The desk is ready to reassess first capital.',
          'success',
        )
      }
    }

    if (workflowFrom) {
      const workflowKey = [
        workflowFrom,
        requestedTicker,
        compareTickers.join(','),
        compareFocusTicker,
        replaySource,
        replayTitle,
        replayStatus,
        notesReturn,
        notesReturnTicker,
        notesReturnTitle,
        notesReturnCompletion,
      ].join('|')
      if (workflowArrivalRef.current !== workflowKey) {
        workflowArrivalRef.current = workflowKey
        const replayLabel = formatReplayArrivalLabel(replaySource, replayStatus)
        const arrivalTicker = requestedTicker || compareFocusTicker || notesReturnTicker || form.ticker
        const sourceLabel =
          workflowFrom === 'compare'
            ? 'compare board'
            : workflowFrom === 'watchlist'
              ? 'liquid board'
              : workflowFrom === 'portfolio'
                ? 'portfolio replay'
                : workflowFrom === 'journal'
                  ? 'journal review'
                  : workflowFrom === 'notes'
                    ? 'notes review'
                    : 'workflow handoff'
        const workflowNotice =
          workflowFrom === 'compare'
            ? {
                source: workflowFrom,
                title: `${arrivalTicker} loaded from compare`,
                detail:
                  'This setup was promoted from the compare board with its shared interval and horizon intact. Use the desk to confirm route, gate, and event posture before acting.',
                returnUrl:
                  compareTickers.length
                    ? buildCompareWorkflowReturnUrl({
                        tickers: compareTickers,
                        interval: supportedIntervals.includes(String(params.get('interval') || '').trim())
                          ? String(params.get('interval')).trim()
                          : defaultForm.interval,
                        horizon: Math.max(1, Math.round(toNumber(params.get('horizon')) || defaultForm.horizon)),
                        focusTicker: compareFocusTicker || requestedTicker,
                      })
                    : '',
                returnLabel: compareTickers.length ? 'Return to compare board' : '',
              }
            : workflowFrom === 'watchlist'
              ? {
                  source: workflowFrom,
                  title: `${arrivalTicker} loaded from the board`,
                  detail:
                    'This setup was handed off from a ranking surface. Reconfirm the decision gate before treating the setup as promotable.',
                  returnUrl: '',
                  returnLabel: '',
                }
              : workflowFrom === 'portfolio'
                ? {
                    source: workflowFrom,
                    title: `${arrivalTicker} loaded from portfolio replay`,
                    detail: replayTitle
                      ? `Portfolio handed this ticker back from ${replayLabel} "${replayTitle}". Recheck whether the saved replay evidence still matches the current gate, route, and event posture before acting.`
                      : `Portfolio handed this ticker back from ${replayLabel}. Recheck whether the saved replay evidence still matches the current gate, route, and event posture before acting.`,
                    returnUrl: '/portfolio',
                    returnLabel: 'Return to portfolio',
                  }
                : workflowFrom === 'journal'
                  ? {
                      source: workflowFrom,
                      title: `${arrivalTicker} loaded from journal review`,
                      detail: replayTitle
                        ? `Journal sent this ticker back from "${replayTitle}". Use the desk to test whether that repair or cleared-review lesson still holds in the live setup.`
                        : `Journal sent this ticker back from ${replayLabel}. Use the desk to test whether that review lesson still holds in the live setup.`,
                      returnUrl: '/journal?journalRestored=1',
                      returnLabel: 'Return to journal review',
                    }
                  : workflowFrom === 'notes'
                    ? {
                        source: workflowFrom,
                        title: `${arrivalTicker} loaded from notes`,
                        detail: replayTitle
                          ? `Notes reopened this ticker with ${replayLabel} "${replayTitle}" attached. Confirm that the saved repair thread still matches the live desk before clearing or promoting anything.`
                          : 'Notes reopened this ticker with a saved repair thread attached. Confirm that it still matches the live desk before clearing or promoting anything.',
                        returnUrl:
                          notesReturn === '1'
                            ? buildNotesWorkflowReturnUrl({
                                ticker: notesReturnTicker || arrivalTicker,
                                title: notesReturnTitle,
                                completion: notesReturnCompletion === 'completed' ? 'completed' : 'open',
                                journalReturn: notesReturnJournal === '1',
                                replaySource,
                                replayTitle,
                                replayStatus,
                              })
                            : '',
                        returnLabel: notesReturn === '1' ? 'Return to notes' : '',
                      }
                    : {
                        source: workflowFrom,
                        title: `${arrivalTicker} loaded from workflow handoff`,
                        detail: 'This setup was restored from another workstation surface. Reconfirm the gate, route, and event posture before acting.',
                        returnUrl: '',
                        returnLabel: '',
                      }
        setWorkflowArrivalNotice(workflowNotice)
        pushToast(
          arrivalTicker
            ? `${arrivalTicker} loaded from ${sourceLabel}.`
            : `Workflow handoff loaded from ${sourceLabel}.`,
          'success',
        )
      }
    }

    if (!requestedTicker && !hasRepairNotice && !workflowFrom) return

    const requestedInterval = supportedIntervals.includes(String(params.get('interval') || '').trim())
      ? String(params.get('interval')).trim()
      : defaultForm.interval
    const requestedHorizon = Math.max(1, Math.round(toNumber(params.get('horizon')) || defaultForm.horizon))
    const requestKey = `${requestedTicker}|${requestedInterval}|${requestedHorizon}`
    if (requestedTicker) {
      if (shellTickerRequestRef.current === requestKey) return
      shellTickerRequestRef.current = requestKey
    }

    const nextParams = new URLSearchParams(location.search)
    nextParams.delete('ticker')
    nextParams.delete('interval')
    nextParams.delete('horizon')
    nextParams.delete('repairResolved')
    nextParams.delete('repairTicker')
    nextParams.delete('repairTitle')
    nextParams.delete('workflowFrom')
    nextParams.delete('compareTickers')
    nextParams.delete('compareFocusTicker')
    nextParams.delete('replaySource')
    nextParams.delete('replayTitle')
    nextParams.delete('replayStatus')
    nextParams.delete('notesReturn')
    nextParams.delete('notesReturnTicker')
    nextParams.delete('notesReturnTitle')
    nextParams.delete('notesReturnCompletion')
    nextParams.delete('notesReturnJournal')
    navigate(
      {
        pathname: location.pathname,
        search: nextParams.toString() ? `?${nextParams.toString()}` : '',
      },
      { replace: true },
    )

    if (!requestedTicker) return

    void focusTicker(requestedTicker, requestedInterval, requestedHorizon).finally(() => {
      if (shellTickerRequestRef.current === requestKey) {
        shellTickerRequestRef.current = ''
      }
    })
  }, [form.ticker, location.pathname, location.search, navigate, pushToast, supportedIntervals])

  useEffect(() => {
    setLastOrderEvent(null)
  }, [form.ticker])

  useEffect(() => {
    lastStreamMessageAtRef.current =
      lastMessageAt instanceof Date
        ? lastMessageAt.getTime()
        : lastMessageAt
          ? new Date(lastMessageAt).getTime()
          : 0
  }, [lastMessageAt])

  useEffect(() => {
    if (hasBootstrapped.current && hasHydratedWorkspaceData) return
    let cancelled = false
    const bootstrapTimer = window.setTimeout(() => {
      if (cancelled) return
      setLoading(false)
      setAnalysisLoading(false)
      setWorkspaceSyncMode('idle')
      setError('Trading desk bootstrap timed out. You can still use the page and try Load or Refresh.')
    }, DESK_BOOTSTRAP_TIMEOUT_MS)

    async function bootstrapWorkspace() {
      try {
        if (!hasHydratedWorkspaceData) {
          setLoading(true)
        }
        setError('')

        const defaultTicker = preferences?.defaultTicker || 'SPY'
        const preferredInterval = preferences?.defaultInterval || '5m'
        const safeInterval = orderedIntervals.includes(preferredInterval)
          ? preferredInterval
          : orderedIntervals[0] || '5m'
        const nextForm = {
          ticker: defaultTicker,
          interval: safeInterval,
          horizon: preferences?.defaultHorizon || 5,
        }

        if (cancelled) return

        await loadWorkspace({
          ...nextForm,
          includeDashboard: false,
          silent: false,
          clearSelection: true,
        })
        hasBootstrapped.current = true
        if (!cancelled) {
          setBoardSyncing(true)
          void getDashboard('desk', dashboardQueryOptions)
            .then((payload) => {
              if (!cancelled) {
                applyDashboardPayload(payload)
              }
            })
            .catch(() => {
              // keep the chart-first load moving if the board is slow
            })
            .finally(() => {
              if (!cancelled) {
                setBoardSyncing(false)
              }
            })
        }
      } catch (err) {
        hasBootstrapped.current = false
        if (!cancelled) {
          setAnalysisLoading(false)
          setWorkspaceSyncMode('idle')
          setError(err?.response?.data?.detail || err.message || 'Failed to load the trading desk.')
        }
      } finally {
        if (!cancelled) {
          window.clearTimeout(bootstrapTimer)
          setLoading(false)
        }
      }
    }

    bootstrapWorkspace()

    return () => {
      cancelled = true
      window.clearTimeout(bootstrapTimer)
    }
  }, [bootstrap, hasHydratedWorkspaceData, orderedIntervals, preferences?.defaultHorizon, preferences?.defaultInterval, preferences?.defaultTicker])

  const activeWatchlistRow = useMemo(() => {
    const activeTicker = String(form.ticker || '').trim().toUpperCase()
    if (!activeTicker) return null
    return watchlistRows.find((row) => String(row?.ticker || '').trim().toUpperCase() === activeTicker) || null
  }, [form.ticker, watchlistRows])

  const activeScannerRow = useMemo(() => {
    const activeTicker = String(form.ticker || '').trim().toUpperCase()
    if (!activeTicker) return null
    return scannerRows.find((row) => String(row?.ticker || '').trim().toUpperCase() === activeTicker) || null
  }, [form.ticker, scannerRows])

  const activeDeskRow = useMemo(() => {
    const activeTicker = String(form.ticker || '').trim().toUpperCase()
    if (!activeTicker) return null
    const liveEntry = watchlistLiveMap[activeTicker] || null
    const mergedWatchlistRow = mergeDeskRow(activeWatchlistRow, liveEntry)
    if (hasUsableDeskRow(mergedWatchlistRow)) return mergedWatchlistRow
    const mergedScannerRow = mergeDeskRow(activeScannerRow, liveEntry)
    if (hasUsableDeskRow(mergedScannerRow)) return mergedScannerRow
    return mergedWatchlistRow || mergedScannerRow || null
  }, [activeScannerRow, activeWatchlistRow, form.ticker, watchlistLiveMap])

  useEffect(() => {
    const fallbackRow = hasUsableDeskRow(activeDeskRow) ? activeDeskRow : null
    if (!fallbackRow) return
    const fallbackLivePrice = toNumber(
      fallbackRow?.live_price ?? fallbackRow?.current_underlying_price ?? fallbackRow?.close,
    )
    const currentAnalysisLivePrice = toNumber(
      analysis?.live_price ?? analysis?.report?.live_price ?? analysis?.report?.close,
    )

    const shouldRefreshAnalysis =
      !analysis?.report ||
      isPlaceholderDeskAnalysis(analysis) ||
      (
        fallbackLivePrice !== null &&
        fallbackLivePrice > 0 &&
        (
          currentAnalysisLivePrice === null ||
          Math.abs(currentAnalysisLivePrice) < 0.000001
        )
      )

    if (shouldRefreshAnalysis) {
      const nextAnalysis = buildDeskFallbackAnalysis({
        ticker: form.ticker,
        interval: form.interval,
        horizon: form.horizon,
        row: fallbackRow,
      })
      if (nextAnalysis) {
        setAnalysis((current) =>
          areFallbackAnalysesEquivalent(current, nextAnalysis) ? current : nextAnalysis,
        )
      }
    }

    const currentChartLastClose = toNumber(chartPayload?.candles?.at(-1)?.close)
    const shouldRefreshChart =
      !Array.isArray(chartPayload?.candles) ||
      !chartPayload.candles.length ||
      isPlaceholderDeskChartPayload(chartPayload) ||
      (
        fallbackLivePrice !== null &&
        fallbackLivePrice > 0 &&
        (
          currentChartLastClose === null ||
          Math.abs(currentChartLastClose) < 0.000001 ||
          (
            String(chartPayload?.freshness?.source || '').trim().toLowerCase() === 'desk-fallback' &&
            Math.abs(currentChartLastClose - fallbackLivePrice) > Math.max(fallbackLivePrice * 0.001, 0.5)
          )
        )
      )

    if (shouldRefreshChart) {
      const nextChartPayload = buildDeskFallbackChartPayload({
        ticker: form.ticker,
        interval: form.interval,
        row: fallbackRow,
      })
      if (nextChartPayload) {
        const sanitizedFallbackChart = sanitizeChartPayloadCandles(nextChartPayload, fallbackLivePrice)
        setChartPayload((current) =>
          areFallbackChartPayloadsEquivalent(current, sanitizedFallbackChart)
            ? current
            : sanitizedFallbackChart,
        )
      }
    }

    if (
      (error === 'Network Error' || /failed to load/i.test(String(error || ''))) &&
      (fallbackRow || chartPayload?.candles?.length || analysis?.report)
    ) {
      setError('')
    }
  }, [
    activeDeskRow,
    analysis,
    chartPayload,
    error,
    form.horizon,
    form.interval,
    form.ticker,
  ])

  useEffect(() => {
    if (!error) return

    const hasRecoveredDeskData =
      hasUsableDeskRow(activeDeskRow) ||
      isUsableAnalysisPayload(analysis) ||
      hasUsableChartPrices(chartPayload)

    if (!hasRecoveredDeskData) return

    const normalizedError = String(error || '').trim().toLowerCase()
    if (
      normalizedError.includes('status code 5') ||
      normalizedError.includes('network error') ||
      normalizedError.includes('failed to load') ||
      normalizedError.includes('timed out')
    ) {
      setError('')
    }
  }, [activeDeskRow, analysis, chartPayload, error])

  usePolling(
    () => {
      if (
        workspaceRefreshInFlight.current ||
        analysisRefreshInFlight.current ||
        loading ||
        !autoRefresh ||
        !isTickerValid(form.ticker)
      ) {
        return
      }

      const streamMessageAgeMs = lastStreamMessageAtRef.current
        ? Date.now() - lastStreamMessageAtRef.current
        : Number.POSITIVE_INFINITY
      const streamIsFresh =
        streamIsLive && streamMessageAgeMs < Math.max(pollMs * 2, 20000)

      if (streamIsFresh && chartPayload?.candles?.length) {
        return
      }

      workspaceRefreshInFlight.current = true
      loadWorkspace({
        ticker: form.ticker,
        interval: form.interval,
        horizon: form.horizon,
        includeDashboard: false,
        silent: true,
      })
        .catch(() => {
          // ignore transient polling failures and keep the last good state
        })
        .finally(() => {
          workspaceRefreshInFlight.current = false
        })
    },
    pollMs,
    autoRefresh,
  )

  usePolling(
    () => {
      if (boardRefreshInFlight.current || loading || !autoRefresh) {
        return
      }

      boardRefreshInFlight.current = true
      setBoardSyncing(true)
        getDashboard('desk', dashboardQueryOptions)
        .then((payload) => {
          applyDashboardPayload(payload)
        })
        .catch(() => {
          // keep the current board state on transient failures
        })
        .finally(() => {
          boardRefreshInFlight.current = false
          setBoardSyncing(false)
        })
    },
    Math.max(pollMs * 2, 30000),
    autoRefresh,
  )

  useEffect(() => {
    if (dashboardBrokerAccount?.connected === true) {
      return
    }
    void loadPortfolioFallback()
  }, [dashboardBrokerAccount?.connected, loadPortfolioFallback])

  usePolling(
    () => {
      if (dashboardBrokerAccount?.connected === true) {
        return
      }
      void loadPortfolioFallback()
    },
    Math.max(pollMs * 2, 30000),
    true,
  )

  usePolling(
    () => {
      getOrganizationTradeAutomation(automationScopeOptions)
        .then((payload) => {
          setAutomationSnapshot(payload)
        })
        .catch(() => {
          // keep the current route inventory on transient failures
        })
    },
    Math.max(pollMs * 2, 30000),
    autoRefresh,
  )

  usePolling(
    () => {
      getInternalBrokerRouter()
        .then((payload) => {
          setInternalBrokerRouter(payload)
        })
        .catch(() => {
          // keep the current broker/router panel on transient failures
        })
    },
    Math.max(pollMs * 2, 30000),
    autoRefresh,
  )

  useEffect(() => {
    loadOperatorMemoryNotes().catch(() => {
      // keep the current operator memory lane on transient failures
    })
  }, [loadOperatorMemoryNotes])

  usePolling(
    () => {
      loadOperatorMemoryNotes().catch(() => {
        // keep the current operator memory lane on transient failures
      })
    },
    Math.max(pollMs * 2, 30000),
    autoRefresh,
  )

  const quickStats = useMemo(() => {
    const entryLow = toNumber(optionPlan.entry_low_price)
    const entryHigh = toNumber(optionPlan.entry_high_price)
    const livePrice = streamedLivePrice ?? toNumber(chartPayload?.candles?.at(-1)?.close)
    const strategyUpperBand = toNumber(strategySnapshot?.upper_band)
    const strategyLowerBand = toNumber(strategySnapshot?.lower_band)

    if (strategySnapshot?.available) {
      return [
        {
          label: 'Live Price',
          value: formatPrice(livePrice),
        },
        {
          label: 'Momentum',
          value: String(strategySnapshot.state || strategySnapshot.bias || 'Waiting').toUpperCase(),
        },
        {
          label: 'Noise Area',
          value:
            strategyUpperBand !== null && strategyLowerBand !== null
              ? `${formatPrice(strategyLowerBand)} - ${formatPrice(strategyUpperBand)}`
              : '--',
        },
        {
          label: 'Checkpoint',
          value: strategySnapshot.next_checkpoint
            ? `${strategySnapshot.next_checkpoint} ET`
            : strategySnapshot.latest_checkpoint
              ? `${strategySnapshot.latest_checkpoint} ET`
              : '--',
        },
      ]
    }

    return [
      {
        label: 'Live Price',
        value: formatPrice(livePrice),
      },
      {
        label: 'Bias',
        value: report?.verdict || 'Waiting',
      },
      {
        label: 'Setup',
        value: formatNumber(report?.setup_score),
      },
      {
        label: 'Entry Zone',
        value: formatMeaningfulPriceRange(entryLow, entryHigh),
      },
    ]
  }, [chartPayload, optionPlan, report, streamedLivePrice, strategySnapshot])
  const eventAwareWatchlistRows = useMemo(
    () =>
      prioritizeRowsByEventCalendar(
        watchlistRows,
        eventCalendarItems,
        report?.ticker || form.ticker,
      ),
    [eventCalendarItems, form.ticker, report?.ticker, watchlistRows],
  )
  const watchlistFocusRows = eventAwareWatchlistRows.slice(0, 6)
  const scannerFocusRows = scannerRows.slice(0, 5)
  const eventCalendarCards = useMemo(
    () =>
      eventCalendarItems.slice(0, 4).map((item) => ({
        key: String(item?.key || `${item?.source || 'event'}-${item?.title || 'calendar'}`),
        ticker: String(item?.ticker || '').trim().toUpperCase(),
        title: String(item?.title || '').trim() || 'Scheduled event',
        detail: summarizeInlineCopy(item?.detail || ''),
        status: String(item?.label || '').trim() || 'Event window',
        impact: formatLabel(item?.impact || 'medium'),
        tone: String(item?.tone || '').trim().toLowerCase() || 'warning',
        dateLabel: formatEventTime(item?.event_date),
        daysLabel:
          toNumber(item?.days_until) === null
            ? 'Date pending'
            : toNumber(item?.days_until) === 0
              ? 'Today'
              : `${toNumber(item?.days_until)}d`,
        source: String(item?.source || '').trim().toLowerCase() === 'macro_calendar' ? 'Macro calendar' : 'Ticker catalyst',
      })),
    [eventCalendarItems],
  )
  const reviewLoopNoteCards = useMemo(
    () =>
      reviewLoopNotes.slice(0, 4).map((note) => ({
        id: String(note?.id || ''),
        ticker: String(note?.ticker || '').trim().toUpperCase(),
        title: String(note?.title || '').trim() || 'Untitled repair note',
        detail: summarizeInlineCopy(note?.body || note?.summary || ''),
        owner: formatLabel(note?.owner || '', ''),
        priority: formatLabel(note?.priority || 'medium'),
        status: resolveReviewLoopNoteStatus(note),
        tone: resolveReviewLoopNoteTone(note),
        updatedLabel: formatEventTime(note?.updated_at),
      })),
    [reviewLoopNotes],
  )
  const operatorMemoryNoteCards = useMemo(
    () =>
      operatorMemoryNotes.slice(0, 4).map((note) => ({
        id: String(note?.id || ''),
        ticker: String(note?.ticker || '').trim().toUpperCase(),
        title: String(note?.title || '').trim() || 'Untitled memory note',
        detail: summarizeInlineCopy(note?.body || note?.summary || ''),
        owner: formatLabel(note?.owner || 'operator-memory', 'Operator memory'),
        priority: formatLabel(note?.priority || 'medium'),
        status: resolveOperatorMemoryNoteStatus(note),
        tone: resolveOperatorMemoryNoteTone(note),
        updatedLabel: formatEventTime(note?.updated_at),
      })),
    [operatorMemoryNotes],
  )
  const portfolioMonitoredTrades = Array.isArray(dashboard?.portfolio?.monitored_open_trades)
    ? dashboard.portfolio.monitored_open_trades
    : []
  const portfolioPendingOrders = Array.isArray(dashboard?.portfolio?.pending_orders)
    ? dashboard.portfolio.pending_orders
    : []
  const monitorRows = portfolioMonitoredTrades.slice(0, 4)
  const executionRailState = useMemo(
    () =>
      describeExecutionRailState({
        activeExecutionPrice,
        backendOrderEvent: latestBackendOrderEvent,
        canOpenTrade,
        capitalPreservationSummary,
        currentTicker: form.ticker,
        lastOrderEvent,
        orderType: tradeTicket.orderType,
        pendingOrder: activePendingOrder,
        positionStatusText: ticketPrimaryMessage,
        reportTicker: report?.ticker,
        selectedChartPoint,
        timeInForce: tradeTicket.timeInForce,
      }),
    [
      activeExecutionPrice,
      latestBackendOrderEvent,
      canOpenTrade,
      capitalPreservationSummary,
      form.ticker,
      lastOrderEvent,
      activePendingOrder,
      ticketPrimaryMessage,
      report?.ticker,
      selectedChartPoint,
      tradeTicket.orderType,
      tradeTicket.timeInForce,
    ],
  )
  const monitoredOrderRows = useMemo(
    () =>
      monitorRows.map((row) => ({
        ...row,
        orderState: describeMonitorOrderState(row),
      })),
    [monitorRows],
  )
  const currentTickerPositionMarkers = useMemo(() => {
    const activeTicker = String(form.ticker || '').trim().toUpperCase()
    if (!activeTicker) return []

    const openTrades = Array.isArray(dashboard?.portfolio?.open_trades)
      ? dashboard.portfolio.open_trades
      : []
    const monitoredTrades = Array.isArray(dashboard?.portfolio?.monitored_open_trades)
      ? dashboard.portfolio.monitored_open_trades
      : []

    return openTrades
      .map((row, index) => {
        const monitor = monitoredTrades[index] || {}
        return {
          ticker: row?.ticker,
          openedAt: row?.opened_at || monitor?.opened_at || null,
          entryPrice: row?.live_price_at_open ?? row?.entry_underlying_price,
          currentPrice:
            monitor?.current_underlying ??
            monitor?.current_underlying_price ??
            row?.current_underlying ??
            row?.current_underlying_price ??
            streamedLivePrice,
          targetPrice: row?.target_price ?? monitor?.target_price,
          stopPrice:
            row?.invalidation_price ??
            row?.stop_price ??
            monitor?.invalidation_price ??
            monitor?.stop_price,
          orderType: row?.order_type ?? monitor?.order_type,
          timeInForce: row?.time_in_force ?? monitor?.time_in_force,
          unrealizedPnl: monitor?.unrealized_pnl,
          verdict: row?.verdict ?? monitor?.verdict,
          tradeDecision: row?.trade_decision ?? monitor?.trade_decision,
          status: row?.status ?? monitor?.monitor_action,
        }
      })
      .filter((row) => String(row.ticker || '').toUpperCase() === activeTicker)
      .slice(-3)
  }, [
    dashboard?.portfolio?.monitored_open_trades,
    dashboard?.portfolio?.open_trades,
    form.ticker,
    streamedLivePrice,
  ])
  const workingOrderMarker = useMemo(() => {
    const activeTicker = String(form.ticker || '').trim().toUpperCase()
    if (!activeTicker) return null

    if (
      activePendingOrder &&
      String(activePendingOrder.ticker || '').trim().toUpperCase() === activeTicker
    ) {
      return {
        orderType: activePendingOrder.order_type,
        timeInForce: activePendingOrder.time_in_force,
        executionPrice:
          toNumber(activePendingOrder.live_price_at_submit) ??
          toNumber(activePendingOrder.live_price_at_open) ??
          activeExecutionPrice,
        limitPrice: toNumber(activePendingOrder.limit_price),
        stopPrice: toNumber(activePendingOrder.stop_price),
        trailingPercent: toNumber(activePendingOrder.trailing_percent),
        verdict: activePendingOrder.verdict || report?.verdict,
        tradeDecision: activePendingOrder.trade_decision || report?.trade_decision,
      }
    }

    if (!report?.ticker) return null
    const reportTicker = String(report.ticker || '').trim().toUpperCase()
    if (reportTicker !== activeTicker) return null

    return {
      orderType: tradeTicket.orderType,
      timeInForce: tradeTicket.timeInForce,
      executionPrice: activeExecutionPrice,
      limitPrice: orderNeedsLimitPrice ? toNumber(tradeTicket.limitPrice) : null,
      stopPrice: orderNeedsStopPrice ? toNumber(tradeTicket.stopPrice) : null,
      trailingPercent: orderNeedsTrailingPercent ? toNumber(tradeTicket.trailingPercent) : null,
      verdict: report?.verdict,
      tradeDecision: report?.trade_decision,
    }
  }, [
    activePendingOrder,
    activeExecutionPrice,
    form.ticker,
    orderNeedsLimitPrice,
    orderNeedsStopPrice,
    orderNeedsTrailingPercent,
    report?.ticker,
    report?.trade_decision,
    report?.verdict,
    tradeTicket.limitPrice,
    tradeTicket.orderType,
    tradeTicket.stopPrice,
    tradeTicket.timeInForce,
    tradeTicket.trailingPercent,
  ])
  const tickerStrip = useMemo(
    () =>
      Array.from(
        new Set([
          form.ticker,
          ...watchlistFocusRows.map((row) => row.ticker),
          ...scannerFocusRows.map((row) => row.ticker),
        ]),
      )
        .filter(Boolean)
        .slice(0, 10),
    [form.ticker, scannerFocusRows, watchlistFocusRows],
  )
  const liveTickerLookup = useMemo(
    () => {
      const collection = {}
      const applyRow = (row) => {
        const symbol = String(row?.ticker || '').trim().toUpperCase()
        if (!symbol) return
        const liveEntry = watchlistLiveMap[symbol] || null
        const mergedRow = mergeDeskRow(row, liveEntry)
        const existing = collection[symbol] || null
        if (!existing || (!hasUsableDeskRow(existing) && hasUsableDeskRow(mergedRow))) {
          collection[symbol] = mergedRow
        }
      }

      watchlistRows.forEach(applyRow)
      scannerRows.forEach(applyRow)

      Object.entries(watchlistLiveMap || {}).forEach(([symbol, liveEntry]) => {
        const normalizedSymbol = String(symbol || '').trim().toUpperCase()
        if (!normalizedSymbol) return
        if (!collection[normalizedSymbol]) {
          collection[normalizedSymbol] = mergeDeskRow({ ticker: normalizedSymbol }, liveEntry)
        }
      })

      return collection
    },
    [scannerRows, watchlistLiveMap, watchlistRows],
  )
  usePolling(
    () => {
      if (liveBatchRefreshInFlight.current || loading || !autoRefresh || !tickerStrip.length) {
        return
      }

      liveBatchRefreshInFlight.current = true
      getLiveBatch(tickerStrip)
        .then((payload) => {
          const incomingRows = Array.isArray(payload?.rows) ? payload.rows : []
          const incomingPrices = payload?.prices && typeof payload.prices === 'object' ? payload.prices : {}
          if (!incomingRows.length && !Object.keys(incomingPrices).length) {
            return
          }

          setWatchlistLiveMap((current) => {
            const next = { ...current }

            for (const row of incomingRows) {
              const symbol = String(row?.ticker || '').trim().toUpperCase()
              if (!symbol) continue
              const bidPrice = toNumber(row?.bid_price ?? row?.bid)
              const askPrice = toNumber(row?.ask_price ?? row?.ask)
              const livePrice = toNumber(row?.live_price ?? row?.price ?? row?.last)
              next[symbol] = {
                ...(next[symbol] || {}),
                ...(livePrice !== null && livePrice > 0 ? { price: livePrice } : {}),
                ...(bidPrice !== null && bidPrice > 0 ? { bid_price: bidPrice } : {}),
                ...(askPrice !== null && askPrice > 0 ? { ask_price: askPrice } : {}),
                spread:
                  resolveDisplaySpread(row?.spread, bidPrice, askPrice) ??
                  next[symbol]?.spread ??
                  null,
                timestamp: row?.timestamp ?? payload?.timestamp ?? next[symbol]?.timestamp ?? null,
              }
            }

            for (const [symbolKey, rawPrice] of Object.entries(incomingPrices)) {
              const symbol = String(symbolKey || '').trim().toUpperCase()
              const livePrice = toNumber(rawPrice)
              if (!symbol || livePrice === null || livePrice <= 0) continue
              next[symbol] = {
                ...(next[symbol] || {}),
                price: livePrice,
                timestamp: payload?.timestamp ?? next[symbol]?.timestamp ?? null,
              }
            }

            watchlistLiveMapRef.current = next
            return next
          })

          const activeTicker = String(formRef.current?.ticker || '').trim().toUpperCase()
          if (!activeTicker) {
            return
          }

          const activeRow =
            incomingRows.find((row) => String(row?.ticker || '').trim().toUpperCase() === activeTicker) ||
            null
          const activeLivePrice =
            toNumber(activeRow?.live_price ?? activeRow?.price ?? activeRow?.last) ??
            toNumber(incomingPrices?.[activeTicker])
          const activeTimestamp =
            activeRow?.timestamp ?? payload?.timestamp ?? new Date().toISOString()

          if (activeLivePrice !== null && activeLivePrice > 0) {
            setSelectedTrade((current) => {
              const currentPrice = toNumber(current?.price)
              const currentTimestamp = String(current?.timestamp || '')
              if (
                currentPrice !== null &&
                Math.abs(currentPrice - activeLivePrice) < 0.000001 &&
                currentTimestamp === String(activeTimestamp || '')
              ) {
                return current
              }
              return {
                ...(current || {}),
                symbol: activeTicker,
                price: activeLivePrice,
                size: toNumber(current?.size) ?? 0,
                timestamp: activeTimestamp,
                side: current?.side || 'neutral',
                notional:
                  (toNumber(current?.size) ?? 0) > 0
                    ? activeLivePrice * (toNumber(current?.size) ?? 0)
                    : null,
              }
            })

            setChartPayload((current) =>
              applyTradeTickToChart(
                current,
                {
                  symbol: activeTicker,
                  price: activeLivePrice,
                  size: 0,
                  timestamp: activeTimestamp,
                },
                formRef.current?.interval,
              ),
            )
          }

          const activeBid = toNumber(activeRow?.bid_price ?? activeRow?.bid)
          const activeAsk = toNumber(activeRow?.ask_price ?? activeRow?.ask)
          if (activeBid !== null || activeAsk !== null) {
            const nextQuote = {
              symbol: activeTicker,
              bid_price: activeBid,
              ask_price: activeAsk,
              bid_size: toNumber(activeRow?.bid_size),
              ask_size: toNumber(activeRow?.ask_size),
              spread:
                resolveDisplaySpread(activeRow?.spread, activeBid, activeAsk) ?? null,
              timestamp: activeTimestamp,
            }
            selectedQuoteRef.current = nextQuote
            setSelectedQuote(nextQuote)
            setChartPayload((current) =>
              applyQuoteTickToChart(current, nextQuote, formRef.current?.interval),
            )
          }
        })
        .catch(() => {
          // keep the last good live batch if the quote endpoint blips
        })
        .finally(() => {
          liveBatchRefreshInFlight.current = false
        })
    },
    1000,
    autoRefresh,
  )
  const activeTickerAccent = tickerAccent(form.ticker)
  const layoutStorageKey = useMemo(
    () => layoutStorageKeyFor(form.ticker, form.interval),
    [form.interval, form.ticker],
  )
  const studyLegend = useMemo(
    () =>
      Object.entries(chartPayload?.overlays || {})
        .filter(([name, series]) => {
          if (!Array.isArray(series) || !series.length) return false
          return !name.startsWith('atr') && name !== 'volume_ratio'
        })
        .map(([name]) => ({ name, label: overlayLabel(name), color: overlayAccent(name) }))
        .slice(0, 12),
    [chartPayload],
  )
  const chartHeight = hasHydratedWorkspaceData
    ? Math.min(Math.max(viewportHeight - 320, 520), 680)
    : Math.max(Math.min(viewportHeight - 520, 420), 320)
  const showInitialLoader = loading && !hasHydratedWorkspaceData
  const syncBadgeLabel =
    workspaceSyncMode === 'initial'
      ? 'Loading desk'
      : ''
  const internalApiStreamActive =
    streamStatus === 'internal_polling' ||
    streamMeta?.connection_mode === 'internal_polling' ||
    streamMeta?.provider === 'internal_owned_api'
  const streamBadgeLabel = internalApiStreamActive
    ? 'Internal API feed'
    : streamIsLive
      ? 'Realtime feed'
    : streamStatus === 'fallback' || streamError
      ? 'Feed delayed'
      : streamStatus === 'connecting' || streamStatus === 'connected'
        ? 'Feed standby'
        : autoRefresh
          ? 'Background sync'
          : 'Manual mode'
  const streamSupportLine = streamStatus === 'fallback' || streamError
    ? 'Live ticks are delayed, so the desk is holding on the latest stable saved board.'
    : internalApiStreamActive
      ? 'INTERNAL API | FREE DELAYED | PAPER ONLY'
    : streamMeta?.provider
      ? `${String(streamMeta.provider).toUpperCase()} ${streamMeta?.feed ? `| ${String(streamMeta.feed).toUpperCase()}` : ''}`
      : 'Chart-first layout with quiet background sync.'
  const hasLevelOverrides = Object.keys(levelOverrides).length > 0
  const latestCandle = chartPayload?.candles?.at(-1) || null
  const rawChartFreshness = chartPayload?.freshness || null
  const chartFreshness = useMemo(
    () => buildSessionAwareFreshness({ freshness: rawChartFreshness, sessionModel }),
    [rawChartFreshness, sessionModel],
  )
  const forecastTrustSummary = buildForecastTrustSummary({
    confidenceScore: toNumber(forecastSummary?.confidence_score),
    freshness: chartFreshness,
    regimeStrengthScore,
    resolvedCount: journalResolvedCount,
    eventConfidencePenalty,
  })
  const sessionLabel = getSessionLabel()
  const executionQualitySummary = buildExecutionQualitySummary({
    instrumentType: tradeTicket.instrumentType,
    quote: selectedQuote,
    contract,
    routeComparison,
    freshness: chartFreshness,
    sessionLabel,
    executionContext,
    sessionModel,
  })
  const chartFreshnessAlert = useMemo(
    () => buildSessionAwareFreshnessAlert({ freshness: chartFreshness }),
    [chartFreshness],
  )
  const showDeskErrorBanner = Boolean(error) && (!hasHydratedWorkspaceData || !hasUsableDeskRow(activeDeskRow))
  const targetQualitySummary = buildTargetQualitySummary({
    resolvedCount: journalResolvedCount,
    averageError: journalAverageError,
    empiricalHitRate: journalHitRate,
    averageProbabilityUp: journalAverageProbabilityUp,
    calibrationScope,
  })
  const modelDriftSummary = buildModelDriftSummary({
    confidenceScore: toNumber(forecastSummary?.confidence_score),
    freshness: chartFreshness,
    regimeStrengthScore,
    resolvedCount: journalResolvedCount,
    averageError: journalAverageError,
    empiricalHitRate: journalHitRate,
    averageProbabilityUp: journalAverageProbabilityUp,
    eventConfidencePenalty,
  })
  const benchmarkSummary = buildBenchmarkSummary({
    adjustedProbabilityUp,
    technicalProbabilityUp,
    averageProbabilityUp: journalAverageProbabilityUp,
    calibrationScope,
    resolvedCount: journalResolvedCount,
  })
  const memorySummary = buildMemorySummary({
    marketRegime: journalMarketRegime,
    bestRegime,
    weakestRegime,
    bestDriver,
    weakestDriver,
  })
  const sessionMemorySummary = buildSessionMemorySummary({
    sessionLabel,
    bestSession,
    weakestSession,
  })
  const eventMemorySummary = buildEventMemorySummary({
    eventContext,
    eventRisk: Boolean(report?.event_risk),
    nextEventName: report?.next_event_name,
    bestEventWindow,
    weakestEventWindow,
  })
  const decisionGateSummary = buildDecisionGateSummary({
    tradeDecision: report?.trade_decision,
    forecastTrustSummary,
    executionQualitySummary,
    targetQualitySummary,
    modelDriftSummary,
    benchmarkSummary,
    eventMemorySummary,
    sessionMemorySummary,
    memorySummary,
    promotionGateSummary,
  })
  const venueLabel = inferVenueLabel(selectedQuote, selectedTrade, streamMeta?.provider)
  const marketStructureCards = useMemo(
    () =>
      buildMarketStructureCards({
        instrumentType: tradeTicket.instrumentType,
        quote: selectedQuote,
        contract,
        report,
        eventContext,
        regimeStrengthScore,
        chartFreshness,
        sessionLabel,
        extendedHours: Boolean(chartPayload?.extended_hours),
        venueLabel,
        routeComparison,
      }),
    [
      chartFreshness,
      chartPayload?.extended_hours,
      contract,
      eventContext,
      regimeStrengthScore,
      report,
      routeComparison,
      selectedQuote,
      sessionLabel,
      tradeTicket.instrumentType,
      venueLabel,
    ],
  )
  const instrumentLabel = inferInstrumentLabel(form.ticker)
  const symbolMetaItems = [
    { label: 'Venue', value: venueLabel },
    { label: 'Session', value: chartPayload?.extended_hours ? `${sessionLabel} + EXT` : sessionLabel },
    { label: 'Type', value: instrumentLabel },
    { label: 'Last bar', value: formatEventTime(latestCandle?.datetime) },
    { label: 'Bars', value: formatCompact(chartPayload?.point_count) },
    {
      label: 'Spread',
      value: formatMeaningfulPrice(
        resolveDisplaySpread(selectedQuote?.spread, selectedQuote?.bid_price, selectedQuote?.ask_price),
      ),
    },
  ]
  const deskResearchSnapshot = useMemo(() => {
    const ticker = String(report?.ticker || form.ticker || '').trim().toUpperCase()
    if (!ticker) return null

    const institutionalFlowSummary = buildInstitutionalFlowSummary(report?.institutional_flow)
    const newsSummary = buildNewsSummary(report?.news_sentiment || chartPayload?.news_sentiment)
    const vehicleSelectionSummary = buildVehicleSelectionSummary({
      vehicleRecommendation: report?.vehicle_recommendation,
      vehicleReason: report?.vehicle_reason,
      optionExecutionProfile: report?.option_execution_profile,
      fallbackInstrumentType: tradeTicket.instrumentType,
    })
    const livePriceValue = toNumber(activeExecutionPrice ?? report?.live_price ?? report?.close)
    const entryLowPrice = toNumber(optionPlan?.entry_low_price ?? strategySnapshot?.lower_band)
    const entryHighPrice = toNumber(optionPlan?.entry_high_price ?? strategySnapshot?.upper_band)
    const targetPriceValue = toNumber(
      optionPlan?.expected_underlying_target ??
        strategySnapshot?.upper_band ??
        strategySnapshot?.vwap,
    )
    const stopPriceValue = toNumber(
      optionPlan?.invalidation_price ??
        strategySnapshot?.active_stop ??
        strategySnapshot?.lower_band,
    )

    return {
      ticker,
      interval: String(form.interval || '').trim() || '--',
      tone: decisionGateSummary.tone || 'warning',
      decisionLabel: decisionGateSummary.label || report?.trade_decision || 'Review',
      executionLabel: executionQualitySummary.label || 'Execution pending',
      executionTone: executionQualitySummary.tone || 'warning',
      trustLabel: forecastTrustSummary.label || 'Trust pending',
      trustTone: forecastTrustSummary.tone || 'warning',
      eventTone: eventMemorySummary.tone || 'warning',
      benchmarkTone: benchmarkSummary.tone || 'warning',
      flowScore: institutionalFlowSummary.score,
      flowTone: institutionalFlowSummary.tone,
      flowLabel: institutionalFlowSummary.label,
      flowSummary: institutionalFlowSummary.summary,
      flowDetail: institutionalFlowSummary.note || institutionalFlowSummary.summary,
      newsScore: newsSummary.score,
      newsTone: newsSummary.tone,
      newsLabel: newsSummary.label,
      newsSummary: newsSummary.summary,
      newsDetail: newsSummary.detail,
      livePriceValue,
      livePriceLabel: formatPrice(livePriceValue),
      targetPriceValue,
      targetPriceLabel: formatPrice(targetPriceValue),
      stopPriceValue,
      entryLowPrice,
      entryHighPrice,
      entryZoneLabel: formatMeaningfulPriceRange(entryLowPrice, entryHighPrice),
      contextLabel: strategySnapshot?.available
        ? (strategySnapshot.state || strategySnapshot.decision || 'Momentum map')
        : (forecastFraming?.short_label || 'Desk read'),
      instrumentLabel: formatInstrumentTypeLabel(tradeTicket.instrumentType),
      regimeLabel: strategySnapshot?.available
        ? String(strategySnapshot.bias || strategySnapshot.state || 'Neutral')
        : String(report?.verdict || forecastSummary?.label || 'Waiting'),
      routeLabel: executionQualitySummary.routeLabel || formatOrderTypeLabel(tradeTicket.orderType),
      vehicleLabel: vehicleSelectionSummary.label,
      vehicleTone: vehicleSelectionSummary.tone,
      vehicleReason: vehicleSelectionSummary.reason,
      optionExecutionScoreLabel: vehicleSelectionSummary.executionSummary.scoreLabel,
      optionExecutionQualityLabel: vehicleSelectionSummary.executionSummary.qualityLabel,
      optionExecutionQualityTone: vehicleSelectionSummary.executionSummary.qualityTone,
      optionExecutionDetail: vehicleSelectionSummary.executionSummary.detail,
      horizonLabel: forecastFraming?.horizon_label || formatForecastHorizon(form.interval, form.horizon),
      setupScore: toNumber(report?.setup_score),
      confidenceScore: toNumber(forecastSummary?.confidence_score),
      notes: [
        `${vehicleSelectionSummary.label}: ${vehicleSelectionSummary.reason}`,
        vehicleSelectionSummary.executionSummary.rejectSummary,
        newsSummary.summary,
        newsSummary.detail,
        institutionalFlowSummary.summary,
        institutionalFlowSummary.note,
        decisionGateSummary.action,
        executionQualitySummary.detail,
        report?.reject_reason || strategyAlignmentMessage || entryAlignmentMessage,
      ]
        .filter(Boolean)
        .map((value) => summarizeInlineCopy(value, 140)),
    }
  }, [
    activeExecutionPrice,
    benchmarkSummary.tone,
    decisionGateSummary.action,
    decisionGateSummary.label,
    decisionGateSummary.tone,
    entryAlignmentMessage,
    eventMemorySummary.tone,
    executionQualitySummary.detail,
    executionQualitySummary.label,
    executionQualitySummary.routeLabel,
    executionQualitySummary.tone,
    form.horizon,
    form.interval,
    form.ticker,
    chartPayload?.news_sentiment,
    forecastFraming?.horizon_label,
    forecastFraming?.short_label,
    forecastSummary?.confidence_score,
    forecastSummary?.label,
    optionPlan?.entry_high_price,
    optionPlan?.entry_low_price,
    optionPlan?.expected_underlying_target,
    optionPlan?.invalidation_price,
    report?.close,
    report?.institutional_flow,
    report?.live_price,
    report?.news_sentiment,
    report?.option_execution_profile,
    report?.reject_reason,
    report?.setup_score,
    report?.ticker,
    report?.vehicle_reason,
    report?.vehicle_recommendation,
    report?.verdict,
    strategyAlignmentMessage,
    strategySnapshot,
    tradeTicket.instrumentType,
    tradeTicket.orderType,
  ])
  const chartStagePathModel = useMemo(
    () => buildDeskResearchPathModel(deskResearchSnapshot),
    [deskResearchSnapshot],
  )
  const chartStagePathRangeLabel = chartStagePathModel
    ? formatCompactMeaningfulPriceRange(chartStagePathModel.lower, chartStagePathModel.upper)
    : 'Path pending'
  const chartCockpitSnapshot = useMemo(() => {
    if (!deskResearchSnapshot) return null
    return {
      kicker: strategySnapshot?.available ? 'Chart cockpit' : 'Desk cockpit',
      title: `${deskResearchSnapshot.ticker} ${deskResearchSnapshot.livePriceLabel}`,
      tone: deskResearchSnapshot.tone,
      decisionLabel: deskResearchSnapshot.decisionLabel,
      executionLabel: deskResearchSnapshot.executionLabel,
      executionTone: deskResearchSnapshot.executionTone,
      trustLabel: deskResearchSnapshot.trustLabel,
      trustTone: deskResearchSnapshot.trustTone,
      newsLabel: deskResearchSnapshot.newsLabel,
      newsTone: deskResearchSnapshot.newsTone,
      newsScore: deskResearchSnapshot.newsScore,
      newsSummary: deskResearchSnapshot.newsSummary,
      newsDetail: deskResearchSnapshot.newsDetail,
      flowLabel: deskResearchSnapshot.flowLabel,
      flowTone: deskResearchSnapshot.flowTone,
      flowScore: deskResearchSnapshot.flowScore,
      flowSummary: deskResearchSnapshot.flowSummary || 'Institutional flow is still being scored.',
      flowDetail: deskResearchSnapshot.flowDetail || '',
      blocks: [
        {
          label: strategySnapshot?.available ? 'Noise area' : 'Entry zone',
          value: formatCompactMeaningfulPriceRange(
            deskResearchSnapshot.entryLowPrice,
            deskResearchSnapshot.entryHighPrice,
          ),
        },
        {
          label: strategySnapshot?.available ? 'Session VWAP' : 'Target',
          value: strategySnapshot?.available
            ? formatMeaningfulPrice(strategySnapshot.vwap)
            : deskResearchSnapshot.targetPriceLabel,
        },
        {
          label: strategySnapshot?.available ? 'Trail stop' : 'Cut loss',
          value: strategySnapshot?.available
            ? formatMeaningfulPrice(strategySnapshot.active_stop)
            : formatPrice(deskResearchSnapshot.stopPriceValue),
        },
        {
          label: 'Checkpoint',
          value: strategySnapshot?.available
            ? (strategySnapshot.next_checkpoint ? `${strategySnapshot.next_checkpoint} ET` : 'Close')
            : (forecastFraming?.horizon_label || formatForecastHorizon(form.interval, form.horizon)),
        },
        {
          label: 'Route',
          value: `${formatInstrumentTypeLabel(tradeTicket.instrumentType)} / ${formatOrderTypeLabel(tradeTicket.orderType)}`,
        },
        {
          label: 'News',
          value: deskResearchSnapshot.newsLabel,
        },
      ],
      momentumTitle: strategySnapshot?.available ? 'Intraday momentum' : 'Technicals',
      momentumStrength: strategySnapshot?.available
        ? strategySnapshot.state === 'long'
          ? 82
          : strategySnapshot.state === 'short'
            ? 18
            : strategySnapshot.bias === 'bullish'
              ? 66
              : strategySnapshot.bias === 'bearish'
                ? 34
                : 50
        : Math.max(0, Math.min(100, Math.round((toNumber(report?.probability_up) ?? 0.5) * 100))),
      momentumValue: strategySnapshot?.available
        ? strategySnapshot.decision || 'Waiting for checkpoint'
        : `${Math.max(0, Math.min(100, Math.round((toNumber(report?.probability_up) ?? 0.5) * 100)))} / 100`,
      note: selectedChartPoint
        ? `Staged level ${formatPrice(selectedChartPoint.price)} at ${formatEventTime(selectedChartPoint.timestamp)}`
        : (strategyAlignmentMessage || entryAlignmentMessage),
    }
  }, [
    report?.probability_up,
    benchmarkSummary.label,
    deskResearchSnapshot,
    entryAlignmentMessage,
    forecastFraming?.horizon_label,
    form.horizon,
    form.interval,
    selectedChartPoint,
    strategyAlignmentMessage,
    strategySnapshot,
    tradeTicket.instrumentType,
    tradeTicket.orderType,
  ])
  const activeReferencePrice =
    toNumber(report?.close) ??
    toNumber(chartPayload?.candles?.at(-2)?.close) ??
    toNumber(latestCandle?.open)
  const activePriceDelta =
    activeExecutionPrice !== null && activeReferencePrice !== null
      ? activeExecutionPrice - activeReferencePrice
      : null
  const activePriceDeltaPct = percentageDelta(activeExecutionPrice, activeReferencePrice)
  const chartStageQuoteAgeSeconds = useMemo(() => {
    const timestamp = selectedQuote?.timestamp
    if (!timestamp) return null
    const parsedMs = Date.parse(timestamp)
    if (!Number.isFinite(parsedMs)) return null
    return Math.max(0, Math.round((Date.now() - parsedMs) / 1000))
  }, [selectedQuote?.timestamp])
  const chartStageContextCards = useMemo(() => {
    const spreadValue = formatMeaningfulPrice(
      resolveDisplaySpread(selectedQuote?.spread, selectedQuote?.bid_price, selectedQuote?.ask_price),
    )
    return [
      {
        label: 'Last',
        value: formatPrice(activeExecutionPrice),
      },
      {
        label: 'Change',
        value:
          activePriceDelta === null
            ? '--'
            : `${formatSignedCurrency(activePriceDelta)} (${formatSignedPercent(activePriceDeltaPct)})`,
      },
      {
        label: 'Bars',
        value: formatCompact(chartPayload?.point_count),
      },
      {
        label: 'Mode',
        value: chartStyle === 'line' ? 'Line' : 'Candles',
      },
      {
        label: 'Refresh',
        value: streamIsLive ? 'tick stream' : autoRefresh ? `${Math.round(pollMs / 1000)}s live` : 'Manual',
      },
      {
        label: 'Bid / Ask',
        value: `${formatMeaningfulPrice(selectedQuote?.bid_price)} / ${formatMeaningfulPrice(selectedQuote?.ask_price)}`,
      },
      {
        label: 'Spread',
        value: spreadValue,
      },
    ]
  }, [
    activeExecutionPrice,
    activePriceDelta,
    activePriceDeltaPct,
    autoRefresh,
    chartPayload?.point_count,
    chartStyle,
    pollMs,
    selectedQuote,
    streamIsLive,
  ])
  const chartStageExecutionChecks = useMemo(() => {
    const quoteAgeTone =
      chartStageQuoteAgeSeconds === null
        ? 'warning'
        : chartStageQuoteAgeSeconds <= 3
          ? 'positive'
          : chartStageQuoteAgeSeconds <= 10
            ? 'warning'
            : 'negative'
    const sessionValue = chartPayload?.extended_hours ? `${sessionLabel} + EXT` : sessionLabel
    const sessionTone = chartPayload?.extended_hours ? 'warning' : sessionLabel === 'Regular' ? 'positive' : 'warning'
    const routeTone =
      tradeTicket.orderType === 'limit' || tradeTicket.orderType === 'stop_limit'
        ? 'positive'
        : 'warning'
    const feedTone = streamIsLive ? 'positive' : autoRefresh ? 'warning' : 'negative'

    return [
      {
        label: 'Fill quality',
        value: executionQualitySummary.label,
        detail: executionQualitySummary.detail,
        tone: executionQualitySummary.tone,
      },
      {
        label: 'Spread and liquidity',
        value: executionQualitySummary.spreadLabel,
        detail: executionQualitySummary.participationLabel,
        tone: executionQualitySummary.tone,
      },
      {
        label: 'Quote age',
        value: chartStageQuoteAgeSeconds === null ? 'Pending' : `${chartStageQuoteAgeSeconds}s`,
        detail:
          chartStageQuoteAgeSeconds === null
            ? 'Waiting for a fresh quote timestamp.'
            : chartStageQuoteAgeSeconds <= 3
              ? 'Quote freshness is within the live window.'
              : chartStageQuoteAgeSeconds <= 10
                ? 'Quote is usable, but no longer instant.'
                : 'Quote is aging and should not drive an urgent fill.',
        tone: quoteAgeTone,
      },
      {
        label: 'Feed',
        value: streamBadgeLabel,
        detail: streamSupportLine,
        tone: feedTone,
      },
      {
        label: 'Session',
        value: sessionValue,
        detail: chartPayload?.extended_hours ? 'Extended-hours routing is active.' : 'Core session routing posture.',
        tone: sessionTone,
      },
      {
        label: 'Route',
        value: `${formatOrderTypeLabel(tradeTicket.orderType)} / ${formatTimeInForceLabel(tradeTicket.timeInForce)}`,
        detail: `${formatInstrumentTypeLabel(tradeTicket.instrumentType)} route`,
        tone: routeTone,
      },
    ]
  }, [
    autoRefresh,
    chartPayload?.extended_hours,
    chartStageQuoteAgeSeconds,
    executionQualitySummary.detail,
    executionQualitySummary.label,
    executionQualitySummary.participationLabel,
    executionQualitySummary.spreadLabel,
    executionQualitySummary.tone,
    sessionLabel,
    streamBadgeLabel,
    streamIsLive,
    streamSupportLine,
    tradeTicket.instrumentType,
    tradeTicket.orderType,
    tradeTicket.timeInForce,
  ])
  const chartStageAutomationPaths = useMemo(() => {
    const automationSettings = automationSnapshot?.settings || {}
    const automationStatus = automationSnapshot?.status || {}
    const workerEnabled = Boolean(automationSettings.enabled && automationSettings.armed)
    const workerDetail = summarizeInlineCopy(
      String(automationStatus.detail || automationSnapshot?.runtime?.last_action?.detail || '').trim() ||
        'Automation is waiting for the next cycle window.',
      140,
    )

    const equityEnabled = automationSettings.auto_trade_equities !== false
    const optionEnabled = automationSettings.auto_trade_listed_options !== false
    const equityTicker = String(report?.ticker || form.ticker || '').trim().toUpperCase() || 'Current symbol'
    const equityDecision = String(report?.trade_decision || report?.verdict || 'Review').trim()
    const equityVehicleSummary = buildVehicleSelectionSummary({
      vehicleRecommendation: report?.vehicle_recommendation,
      vehicleReason: report?.vehicle_reason,
      optionExecutionProfile: report?.option_execution_profile,
      fallbackInstrumentType: 'equity',
    })
    const equityRejectReason = summarizeInlineCopy(
      String(
        report?.reject_reason ||
          equityVehicleSummary.reason ||
          decisionGateSummary?.action ||
          workerDetail,
      ).trim(),
      140,
    )
    const equityTone = !equityEnabled
      ? 'warning'
      : String(report?.trade_decision || '').trim().toUpperCase() === 'VALID TRADE'
        ? 'positive'
        : decisionGateSummary?.tone || 'warning'

    const optionContract = optionDeskPlan?.recommended_contract || {}
    const optionContractSymbol = String(optionContract?.contract_symbol || optionReport?.contract_symbol || '').trim().toUpperCase()
    const optionDecision = String(optionReport?.trade_decision || 'Review').trim()
    const optionVehicleSummary = buildVehicleSelectionSummary({
      vehicleRecommendation: optionReport?.vehicle_recommendation,
      vehicleReason: optionReport?.vehicle_reason,
      optionExecutionProfile: optionReport?.option_execution_profile,
      fallbackInstrumentType: 'listed_option',
    })
    const optionRejectReason = summarizeInlineCopy(
      String(
        optionReport?.reject_reason ||
          optionVehicleSummary.reason ||
          optionDeskPlan?.summary ||
          optionDeskPlan?.rationale ||
          workerDetail ||
          'No clean listed-option contract is active yet.'
      ).trim(),
      140,
    )
    const optionSideLabel = String(optionDeskPlan?.option_side || optionReport?.option_right || '').trim().toUpperCase()
    const optionContractMeta = [
      optionSideLabel ? `${optionSideLabel} premium` : 'Listed option path',
      optionContract?.expiration ? String(optionContract.expiration) : '',
      optionContract?.strike ? formatPrice(optionContract.strike) : '',
    ]
      .filter(Boolean)
      .join(' | ')

    const optionTone = !optionEnabled
      ? 'warning'
      : optionContractSymbol && String(optionReport?.trade_decision || '').trim().toUpperCase() === 'VALID TRADE'
        ? 'positive'
        : optionContractSymbol
          ? 'warning'
          : optionReport?.reject_reason
            ? 'negative'
            : 'warning'

    return {
      workerLabel: workerEnabled ? String(automationStatus.label || 'Armed') : 'Not armed',
      workerTone: workerEnabled ? 'positive' : 'warning',
      items: [
        {
          label: 'Equity candidate',
          value: equityVehicleSummary.label,
          meta: `${equityTicker} | ${formatInstrumentTypeLabel('equity')} | ${equityDecision || 'Review'}`,
          detail:
            equityVehicleSummary.recommendation === 'equity'
              ? `${equityVehicleSummary.reason} ${equityRejectReason}`.trim()
              : equityRejectReason,
          tone: equityTone,
        },
        {
          label: 'Option candidate',
          value: optionContractSymbol || optionVehicleSummary.label,
          meta:
            optionContractMeta ||
            `${equityTicker} | ${formatInstrumentTypeLabel('listed_option')} | ${optionVehicleSummary.executionSummary.qualityLabel} ${optionVehicleSummary.executionSummary.scoreLabel}`,
          detail:
            optionVehicleSummary.executionSummary.rejectSummary ||
            optionRejectReason,
          tone: optionTone,
        },
      ],
    }
  }, [
    automationSnapshot,
    decisionGateSummary?.action,
    decisionGateSummary?.tone,
    form.ticker,
    optionDeskPlan,
    optionReport,
    report?.option_execution_profile,
    report?.reject_reason,
    report?.ticker,
    report?.trade_decision,
    report?.vehicle_reason,
    report?.vehicle_recommendation,
    report?.verdict,
  ])
  const activeSignalStrength = strategySnapshot?.available
    ? strategySnapshot.state === 'long'
      ? 82
      : strategySnapshot.state === 'short'
        ? 18
        : strategySnapshot.bias === 'bullish'
          ? 66
          : strategySnapshot.bias === 'bearish'
            ? 34
            : 50
    : Math.max(0, Math.min(100, Math.round((toNumber(report?.probability_up) ?? 0.5) * 100)))
  const activeDockTab = tapeOpen ? 'tape' : activeDrawer || 'plan'
  const sidebarRows = useMemo(
    () =>
      eventAwareWatchlistRows.slice(0, 3).map((row) => {
        const symbol = String(row?.ticker || '').trim().toUpperCase()
        const mergedRow = mergeDeskRow(row, liveTickerLookup[symbol] || watchlistLiveMap[symbol] || null) || row
        if (symbol && symbol === String(form.ticker || '').trim().toUpperCase()) {
          return {
            ...mergedRow,
            live_price: activeExecutionPrice ?? mergedRow?.live_price ?? mergedRow?.current_underlying_price ?? mergedRow?.close ?? null,
            bid_price: selectedQuote?.bid_price ?? mergedRow?.bid_price ?? null,
            ask_price: selectedQuote?.ask_price ?? mergedRow?.ask_price ?? null,
            spread:
              resolveDisplaySpread(
                selectedQuote?.spread,
                selectedQuote?.bid_price,
                selectedQuote?.ask_price,
              ) ?? mergedRow?.spread ?? null,
          }
        }
        return mergedRow
      }),
    [activeExecutionPrice, eventAwareWatchlistRows, form.ticker, liveTickerLookup, selectedQuote, watchlistLiveMap],
  )
  const deskCandidateQueue = useMemo(
    () => buildDeskCandidateQueue([...watchlistFocusRows, ...scannerFocusRows], promotionGateSummary),
    [promotionGateSummary, scannerFocusRows, watchlistFocusRows],
  )
  const mondayPlaybook = useMemo(
    () =>
      buildMondayPlaybook({
        sidebarCount: sidebarRows.length,
        candidateQueue: deskCandidateQueue,
        decisionGateSummary,
        executionRailState,
        canOpenTrade,
        currentTicker: form.ticker,
        reportTicker: report?.ticker,
        hasPendingOrder: Boolean(activePendingOrder),
        reviewLoopTicketGuardrail,
      }),
    [
      activePendingOrder,
      canOpenTrade,
      decisionGateSummary,
      deskCandidateQueue,
      executionRailState,
      form.ticker,
      reviewLoopTicketGuardrail,
      report?.ticker,
      sidebarRows.length,
    ],
  )
  const preOpenSnapshot = useMemo(
    () =>
      buildPreOpenSnapshot({
        chartFreshness,
        eventContext,
        eventRisk: Boolean(report?.event_risk),
        nextEventName: report?.next_event_name,
        candidateQueue: deskCandidateQueue,
        canOpenTrade,
        hasPendingOrder: Boolean(activePendingOrder),
        executionQualitySummary,
        modelDriftSummary,
        decisionGateSummary,
      }),
    [
      activePendingOrder,
      canOpenTrade,
      chartFreshness,
      decisionGateSummary,
      deskCandidateQueue,
      eventContext,
      executionQualitySummary,
      modelDriftSummary,
      report?.event_risk,
      report?.next_event_name,
    ],
  )
  const sessionHandoff = useMemo(
    () =>
      buildSessionHandoff({
        sessionLabel,
        decisionGateSummary,
        executionQualitySummary,
        modelDriftSummary,
        candidateQueue: deskCandidateQueue,
        hasPendingOrder: Boolean(activePendingOrder),
        canOpenTrade,
        eventContext,
        eventRisk: Boolean(report?.event_risk),
        nextEventName: report?.next_event_name,
        reportTicker: report?.ticker,
        currentTicker: form.ticker,
      }),
    [
      activePendingOrder,
      canOpenTrade,
      decisionGateSummary,
      deskCandidateQueue,
      eventContext,
      executionQualitySummary,
      form.ticker,
      modelDriftSummary,
      report?.event_risk,
      report?.next_event_name,
      report?.ticker,
      sessionLabel,
    ],
  )
  const postCloseReview = useMemo(
    () =>
      buildPostCloseReview({
        portfolioSummary,
        tradeSummary: portfolioTradeSummary,
        attributionSummary: portfolioAttributionSummary,
        reviewLoopProgress,
        monitoredTrades: portfolioMonitoredTrades,
        pendingOrders: portfolioPendingOrders,
        decisionGateSummary,
        modelDriftSummary,
        candidateQueue: deskCandidateQueue,
        reportTicker: report?.ticker,
        currentTicker: form.ticker,
      }),
    [
      decisionGateSummary,
      deskCandidateQueue,
      form.ticker,
      modelDriftSummary,
      portfolioAttributionSummary,
      portfolioMonitoredTrades,
      portfolioPendingOrders,
      reviewLoopProgress,
      portfolioSummary,
      portfolioTradeSummary,
      report?.ticker,
    ],
  )
  const tomorrowPrep = useMemo(
    () =>
      buildTomorrowPrep({
        candidateQueue: deskCandidateQueue,
        watchlistRows: watchlistFocusRows,
        scannerRows: scannerFocusRows,
        monitoredTrades: portfolioMonitoredTrades,
        pendingOrders: portfolioPendingOrders,
        reviewLoopNotes,
        decisionGateSummary,
        modelDriftSummary,
        reportTicker: report?.ticker,
        currentTicker: form.ticker,
      }),
    [
      decisionGateSummary,
      deskCandidateQueue,
      form.ticker,
      modelDriftSummary,
      portfolioMonitoredTrades,
      portfolioPendingOrders,
      reviewLoopNotes,
      report?.ticker,
      scannerFocusRows,
      watchlistFocusRows,
    ],
  )
  const morningBrief = useMemo(
    () =>
      buildMorningBrief({
        chartFreshness,
        candidateQueue: deskCandidateQueue,
        tomorrowPrep,
        reviewLoopNotes,
        decisionGateSummary,
        executionQualitySummary,
        modelDriftSummary,
        eventContext,
        eventRisk: Boolean(report?.event_risk),
        nextEventName: report?.next_event_name,
        reportTicker: report?.ticker,
        currentTicker: form.ticker,
        eventCalendar: eventCalendarPayload,
        canOpenTrade,
        capitalPreservationSummary,
        reviewLoopTicketGuardrail,
      }),
    [
      canOpenTrade,
      capitalPreservationSummary,
      chartFreshness,
      decisionGateSummary,
      deskCandidateQueue,
      eventContext,
      executionQualitySummary,
      eventCalendarPayload,
      form.ticker,
      modelDriftSummary,
      reviewLoopTicketGuardrail,
      reviewLoopNotes,
      report?.event_risk,
      report?.next_event_name,
      report?.ticker,
      tomorrowPrep,
    ],
  )
  const sidebarDetailModeLabel = showExtendedSidebarDetails ? 'Full side rail' : 'Core side rail'
  const sidebarDetailModeSummary = showExtendedSidebarDetails
    ? 'Watchlist, prep, route, and model detail are visible.'
    : 'Only the active symbol, short watchlist, and open-trade monitor stay visible.'
  const visibleDeskCandidateRows = showExtendedSidebarDetails ? deskCandidateQueue.rows : deskCandidateQueue.rows.slice(0, 4)
  const liveFocusSummary = useMemo(
    () =>
      buildLiveFocusSummary({
        currentTicker: form.ticker,
        reportTicker: report?.ticker,
        focusLockTicker,
        livePrice: activeExecutionPrice,
        priceDelta: activePriceDelta,
        priceDeltaPct: activePriceDeltaPct,
        decisionGateSummary,
        executionQualitySummary,
        modelDriftSummary,
        routeComparison,
        positionPreview,
        riskReward,
        sendConfidence,
        activePendingOrder,
        selectedChartPoint,
        candidateQueue: deskCandidateQueue,
        canOpenTrade,
        orderType: tradeTicket.orderType,
        timeInForce: tradeTicket.timeInForce,
        eventRisk: Boolean(report?.event_risk),
        capitalPreservationSummary,
      }),
    [
      activeExecutionPrice,
      activePendingOrder,
      activePriceDelta,
      activePriceDeltaPct,
      canOpenTrade,
      capitalPreservationSummary,
      decisionGateSummary,
      deskCandidateQueue,
      executionQualitySummary,
      focusLockTicker,
      form.ticker,
      modelDriftSummary,
      positionPreview,
      report?.event_risk,
      report?.ticker,
      riskReward,
      routeComparison,
      selectedChartPoint,
      sendConfidence,
      tradeTicket.orderType,
      tradeTicket.timeInForce,
    ],
  )
  const tapeSummary = useMemo(() => {
    if (!tradeTape.length) {
      const bidPrice = toNumber(selectedQuote?.bid_price)
      const askPrice = toNumber(selectedQuote?.ask_price)
      const bidSize = Math.max(toNumber(selectedQuote?.bid_size) || 0, 0)
      const askSize = Math.max(toNumber(selectedQuote?.ask_size) || 0, 0)
      const fallbackPrice =
        bidPrice !== null && askPrice !== null
          ? (bidPrice + askPrice) / 2
          : askPrice ?? bidPrice ?? toNumber(streamedLivePrice)
      const fallbackTotalSize = bidSize + askSize
      const fallbackNotional =
        fallbackPrice !== null && fallbackTotalSize > 0
          ? fallbackPrice * fallbackTotalSize
          : null

      if (fallbackTotalSize > 0) {
        return {
          prints: 0,
          totalSize: fallbackTotalSize,
          totalNotional: fallbackNotional,
          buyFlow: bidSize,
          sellFlow: askSize,
          largestPrint: null,
          source: 'quote',
        }
      }

      const exposureUnits = toNumber(positionPreview?.suggestedContracts)
      const exposurePrice =
        toNumber(activeExecutionPrice) ??
        askPrice ??
        bidPrice ??
        toNumber(streamedLivePrice)
      const exposureNotional =
        toNumber(positionPreview?.totalPositionCost) ??
        (exposureUnits !== null && exposureUnits > 0 && exposurePrice !== null
          ? exposureUnits * exposurePrice
          : null)

      if (exposureUnits !== null && exposureUnits > 0 && exposureNotional !== null) {
        return {
          prints: 0,
          totalSize: exposureUnits,
          totalNotional: exposureNotional,
          buyFlow: 0,
          sellFlow: 0,
          largestPrint: null,
          source: 'exposure',
          livePrice: exposurePrice,
        }
      }

      return {
        prints: 0,
        totalSize: null,
        totalNotional: null,
        buyFlow: 0,
        sellFlow: 0,
        largestPrint: null,
        source: 'idle',
        livePrice: null,
      }
    }

    let totalSize = 0
    let totalNotional = 0
    let buyFlow = 0
    let sellFlow = 0
    let largestPrint = null

    for (const print of tradeTape) {
      const size = toNumber(print.size) || 0
      const notional = toNumber(print.notional) || 0
      totalSize += size
      totalNotional += notional
      if (print.side === 'buy') buyFlow += size
      if (print.side === 'sell') sellFlow += size
      if (!largestPrint || size > (toNumber(largestPrint.size) || 0)) {
        largestPrint = print
      }
    }

    return {
      prints: tradeTape.length,
      totalSize,
      totalNotional,
      buyFlow,
      sellFlow,
      largestPrint,
      source: 'trade',
      livePrice: null,
    }
  }, [activeExecutionPrice, positionPreview, selectedQuote, streamedLivePrice, tradeTape])
  const tapePresentation = useMemo(() => {
    if (tapeSummary.source === 'trade') {
      return {
        panelLabel: tradeTape.length ? `${tradeTape.length} prints` : 'Live tape',
        headerKicker: 'Live tape',
        headerTitle: `${form.ticker} stream`,
        sizeLabel: 'Shares',
        notionalLabel: 'Notional',
        flowPrimaryLabel: 'Buy flow',
        flowSecondaryLabel: 'Sell flow',
        helperText: `Trade prints for ${form.ticker} will appear here as soon as the stream is live.`,
        flowPrimaryValue: formatCompact(tapeSummary.buyFlow),
        flowSecondaryValue: formatCompact(tapeSummary.sellFlow),
      }
    }
    if (tapeSummary.source === 'quote') {
      return {
        panelLabel: 'Quote depth',
        headerKicker: 'Quote depth',
        headerTitle: `${form.ticker} top of book`,
        sizeLabel: 'Depth',
        notionalLabel: 'Quote depth $',
        flowPrimaryLabel: 'Bid size',
        flowSecondaryLabel: 'Ask size',
        helperText: `No trade prints yet. Showing live top-of-book depth for ${form.ticker} until prints arrive.`,
        flowPrimaryValue: formatCompact(tapeSummary.buyFlow),
        flowSecondaryValue: formatCompact(tapeSummary.sellFlow),
      }
    }
    if (tapeSummary.source === 'exposure') {
      return {
        panelLabel: 'Live exposure',
        headerKicker: 'Live exposure',
        headerTitle: `${form.ticker} ticket size`,
        sizeLabel: 'Units',
        notionalLabel: 'Exposure',
        flowPrimaryLabel: 'Live price',
        flowSecondaryLabel: 'Trades',
        helperText: `No trade prints or quote depth yet. Showing live ticket exposure for ${form.ticker}. This is not executed flow.`,
        flowPrimaryValue: formatPrice(tapeSummary.livePrice),
        flowSecondaryValue: '0',
      }
    }
    return {
      panelLabel: 'Waiting',
      headerKicker: 'Live tape',
      headerTitle: `${form.ticker} stream`,
      sizeLabel: 'Shares',
      notionalLabel: 'Notional',
      flowPrimaryLabel: 'Buy flow',
      flowSecondaryLabel: 'Sell flow',
      helperText: `Trade prints for ${form.ticker} will appear here as soon as the stream is live.`,
      flowPrimaryValue: '--',
      flowSecondaryValue: '--',
    }
  }, [form.ticker, tapeSummary, tradeTape.length])
  const domLevels = useMemo(
    () =>
      buildDomLevels({
        quote: selectedQuote,
        trade: selectedTrade,
        fallbackPrice: streamedLivePrice,
      }),
    [selectedQuote, selectedTrade, streamedLivePrice],
  )
  const toolRail = [
    { key: 'pan', label: 'Pan', helper: 'V', group: 'View' },
    { key: 'crosshair', label: 'Cross', helper: 'X', group: 'View' },
    { key: 'hline', label: 'H Line', helper: 'H', group: 'Draw' },
    { key: 'trend', label: 'Trend', helper: 'L', group: 'Draw' },
    { key: 'rectangle', label: 'Zone', helper: 'R', group: 'Draw' },
    { key: 'note', label: 'Note', helper: 'N', group: 'Draw' },
    { key: 'ray', label: 'Ray', helper: 'G', group: 'Draw' },
    { key: 'measure', label: 'Measure', helper: 'M', group: 'Draw' },
    { key: 'erase', label: 'Erase', helper: 'Del', group: 'Edit' },
  ]
  const toolRailGroups = Array.from(
    toolRail.reduce((groups, tool) => {
      if (!groups.has(tool.group)) {
        groups.set(tool.group, [])
      }
      groups.get(tool.group).push(tool)
      return groups
    }, new Map()),
    ([group, tools]) => ({ group, tools }),
  )
  const selectedGuide = selectedGuideId
    ? customGuides.find((guide) => guide.id === selectedGuideId) || null
    : null
  const toolNotice =
    toolMode === 'hline'
      ? 'Horizontal line tool: click once on the chart to drop a guide.'
      : toolMode === 'trend'
        ? pendingGuidePoint
          ? 'Trendline tool: click a second point to finish the trendline.'
          : 'Trendline tool: click the first anchor point.'
        : toolMode === 'ray'
          ? pendingGuidePoint
            ? 'Ray tool: click a second point to set the direction and extend the line.'
            : 'Ray tool: click the anchor point, then click again to set the ray direction.'
        : toolMode === 'rectangle'
          ? pendingGuidePoint
            ? 'Zone tool: click a second corner to finish the price zone.'
            : 'Zone tool: click the first corner of the zone.'
          : toolMode === 'note'
            ? 'Note tool: click anywhere on the chart to drop a note marker.'
        : toolMode === 'measure'
          ? pendingGuidePoint
            ? 'Measure tool: click a second point to calculate price change.'
            : 'Measure tool: click the first anchor point.'
          : toolMode === 'erase'
          ? 'Erase tool: click near a custom guide to remove it.'
          : toolMode === 'crosshair'
            ? 'Crosshair tool: inspect candles with tighter hover focus.'
            : selectedGuide
              ? `${selectedGuide.label || 'Drawing'} selected${selectedGuide.locked ? ' | locked' : ' | drag to move, handles resize'}.`
              : selectedChartPoint
                ? `Staged entry ${formatPrice(selectedChartPoint.price)} at ${formatEventTime(selectedChartPoint.timestamp)}`
                : 'Pan tool: drag the chart, zoom, or click to stage an entry.'
  const drawerTabs = [
    { key: 'plan', label: 'Algo', helper: liveExecutionDecision || liveTradeStatus || 'Watch' },
    { key: 'position', label: 'Size', helper: formatShares(positionPreview?.suggestedContracts) },
  ]
  const marketPanelTabs = [
    { key: 'watchlist', label: 'Watchlist' },
    { key: 'dom', label: 'DOM Ladder' },
    { key: 'scanner', label: 'Scanner' },
  ]
  const chartStyleOptions = [
    { key: 'candles', label: 'Candles' },
    { key: 'line', label: 'Line' },
  ]

  useEffect(() => {
    function syncViewportHeight() {
      setViewportHeight(window.innerHeight || 900)
    }

    syncViewportHeight()
    window.addEventListener('resize', syncViewportHeight)

    return () => {
      window.removeEventListener('resize', syncViewportHeight)
    }
  }, [])

  useEffect(() => {
    return () => {
      if (viewportCommitTimeoutRef.current) {
        window.clearTimeout(viewportCommitTimeoutRef.current)
      }
    }
  }, [])

  useEffect(() => {
    const validNames = new Set(studyLegend.map((item) => item.name))
    setHiddenOverlays((current) => {
      const nextState = Object.fromEntries(
        Object.entries(current).filter(([name]) => validNames.has(name)),
      )
      return Object.keys(nextState).length === Object.keys(current).length ? current : nextState
    })
  }, [studyLegend])

  useEffect(() => {
    const savedLayouts = loadChartLayouts()
    const savedLayout = savedLayouts[layoutStorageKey] || null
    const restoredViewport = sanitizeChartViewportState(savedLayout?.chartViewport)

    if (viewportCommitTimeoutRef.current) {
      window.clearTimeout(viewportCommitTimeoutRef.current)
      viewportCommitTimeoutRef.current = null
    }

    setHiddenOverlays(sanitizeBooleanMap(savedLayout?.hiddenOverlays))
    setActiveDrawer(null)
    setTapeOpen(Boolean(savedLayout?.tapeOpen))
    setMarketPanelOpen(false)
    setMarketPanelTab(savedLayout?.marketPanelTab || 'watchlist')
    setChartStyle(savedLayout?.chartStyle === 'line' ? 'line' : 'candles')
    setToolMode(savedLayout?.toolMode || 'pan')
    setMagnetMode(savedLayout?.magnetMode ?? true)
    setDrawingVisibility(sanitizeDrawingVisibility(savedLayout?.drawingVisibility))
    const restoredGuides = sanitizeCustomGuides(savedLayout?.customGuides)
    applyCustomGuides(restoredGuides, { record: false, selectedId: null })
    resetDrawingHistory(restoredGuides)
    setSelectedGuideId(null)
    setPendingGuidePoint(null)
    setLevelOverrides(sanitizeNumericMap(savedLayout?.levelOverrides))
    chartViewportRef.current = restoredViewport
    setChartViewport(restoredViewport)
    setLayoutReadyKey(layoutStorageKey)
  }, [layoutStorageKey])

  useEffect(() => {
    customGuidesRef.current = customGuides
  }, [customGuides])

  function syncDrawingHistoryState() {
    setDrawingHistoryState({
      canUndo: drawingHistoryRef.current.past.length > 0,
      canRedo: drawingHistoryRef.current.future.length > 0,
    })
  }

  function resetDrawingHistory(nextGuides = []) {
    customGuidesRef.current = cloneCustomGuides(nextGuides)
    drawingHistoryRef.current = { past: [], future: [] }
    syncDrawingHistoryState()
  }

  function applyCustomGuides(nextGuides, options = {}) {
    const { record = true, selectedId, preserveSelection = false } = options
    const previousGuides = cloneCustomGuides(customGuidesRef.current)
    const normalizedNextGuides = cloneCustomGuides(nextGuides)

    if (record) {
      drawingHistoryRef.current = {
        past: [...drawingHistoryRef.current.past, previousGuides].slice(-60),
        future: [],
      }
      syncDrawingHistoryState()
    }

    customGuidesRef.current = normalizedNextGuides
    setCustomGuides(normalizedNextGuides)

    if (selectedId !== undefined) {
      setSelectedGuideId(selectedId)
    } else if (preserveSelection) {
      setSelectedGuideId((current) =>
        normalizedNextGuides.some((guide) => guide.id === current) ? current : null,
      )
    } else {
      setSelectedGuideId(null)
    }
  }

  function updateCustomGuides(updater, options = {}) {
    const currentGuides = cloneCustomGuides(customGuidesRef.current)
    const nextGuides =
      typeof updater === 'function' ? updater(currentGuides) : cloneCustomGuides(updater)
    applyCustomGuides(nextGuides, options)
  }

  function undoGuideChange() {
    if (!drawingHistoryRef.current.past.length) return
    const previousGuides = drawingHistoryRef.current.past[drawingHistoryRef.current.past.length - 1]
    drawingHistoryRef.current = {
      past: drawingHistoryRef.current.past.slice(0, -1),
      future: [cloneCustomGuides(customGuidesRef.current), ...drawingHistoryRef.current.future].slice(0, 60),
    }
    syncDrawingHistoryState()
    applyCustomGuides(previousGuides, { record: false, preserveSelection: true })
  }

  function redoGuideChange() {
    if (!drawingHistoryRef.current.future.length) return
    const [nextGuides, ...future] = drawingHistoryRef.current.future
    drawingHistoryRef.current = {
      past: [...drawingHistoryRef.current.past, cloneCustomGuides(customGuidesRef.current)].slice(-60),
      future,
    }
    syncDrawingHistoryState()
    applyCustomGuides(nextGuides, { record: false, preserveSelection: true })
  }

  useEffect(() => {
    if (!layoutStorageKey || layoutReadyKey !== layoutStorageKey) return

    persistChartLayout(layoutStorageKey, {
      hiddenOverlays,
      activeDrawer,
      tapeOpen,
      marketPanelOpen,
      marketPanelTab,
      chartStyle,
      toolMode,
      magnetMode,
      drawingVisibility,
      customGuides,
      levelOverrides,
      chartViewport: sanitizeChartViewportState(chartViewport),
    })
  }, [
    activeDrawer,
    chartViewport,
    customGuides,
    hiddenOverlays,
    layoutReadyKey,
    layoutStorageKey,
    levelOverrides,
    chartStyle,
    magnetMode,
    marketPanelOpen,
    marketPanelTab,
    tapeOpen,
    toolMode,
    drawingVisibility,
  ])

  useEffect(() => {
    function handleKeydown(event) {
      if (
        event.defaultPrevented ||
        event.altKey ||
        ['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target?.tagName)
      ) {
        return
      }

      const hasCommandModifier = event.metaKey || event.ctrlKey
      if (hasCommandModifier && String(event.key || '').toLowerCase() === 'z') {
        event.preventDefault()
        if (event.shiftKey) {
          redoGuideChange()
        } else {
          undoGuideChange()
        }
        return
      }
      if (hasCommandModifier && String(event.key || '').toLowerCase() === 'y') {
        event.preventDefault()
        redoGuideChange()
        return
      }

      const key = String(event.key || '').toLowerCase()
      if (key === '1') {
        event.preventDefault()
        toggleDrawer('plan')
      } else if (key === '2') {
        event.preventDefault()
        toggleDrawer('position')
      } else if (key === '3') {
        event.preventDefault()
        setMarketPanelTab('watchlist')
        setMarketPanelOpen((current) => !current)
      } else if (key === 't') {
        event.preventDefault()
        setTapeOpen((current) => !current)
      } else if (key === 'c') {
        event.preventDefault()
        setSelectedChartPoint(null)
      } else if (key === 'v') {
        event.preventDefault()
        setToolMode('pan')
      } else if (key === 'x') {
        event.preventDefault()
        setToolMode('crosshair')
      } else if (key === 'h') {
        event.preventDefault()
        setToolMode('hline')
        setPendingGuidePoint(null)
      } else if (key === 'l') {
        event.preventDefault()
        setToolMode('trend')
        setPendingGuidePoint(null)
      } else if (key === 'g') {
        event.preventDefault()
        setToolMode('ray')
        setPendingGuidePoint(null)
      } else if (key === 'r') {
        event.preventDefault()
        setToolMode('rectangle')
        setPendingGuidePoint(null)
      } else if (key === 'n') {
        event.preventDefault()
        setToolMode('note')
        setPendingGuidePoint(null)
      } else if (key === 'm') {
        event.preventDefault()
        setToolMode('measure')
        setPendingGuidePoint(null)
      } else if (key === 'backspace' || key === 'delete') {
        if (selectedGuideId) {
          event.preventDefault()
          updateCustomGuides(
            (current) => current.filter((guide) => guide.id !== selectedGuideId),
            { selectedId: null },
          )
        } else if (customGuides.length) {
          event.preventDefault()
          updateCustomGuides((current) => current.slice(0, -1), { selectedId: null })
        }
      } else if (key === 'escape') {
        event.preventDefault()
        setActiveDrawer(null)
        setTapeOpen(false)
        setMarketPanelOpen(false)
        setPendingGuidePoint(null)
        setToolMode('pan')
      }
    }

    window.addEventListener('keydown', handleKeydown)
    return () => {
      window.removeEventListener('keydown', handleKeydown)
    }
  }, [customGuides.length, selectedGuideId])

  function resetLevels() {
    setLevelOverrides({})
  }

  function changeTool(nextTool) {
    setToolMode(nextTool)
    setPendingGuidePoint(null)
    if (nextTool !== 'erase') {
      setSelectedGuideId(null)
    }
  }

  function duplicateSelectedGuide() {
    const guide = customGuidesRef.current.find((entry) => entry.id === selectedGuideId)
    if (!guide) return

    const id = `${guide.type || 'guide'}-${customGuideId.current++}`
    const baseOffset = Math.max((toNumber(activeExecutionPrice) || 1) * 0.0025, 0.05)
    const duplicated = {
      ...guide,
      id,
      anchorId: buildGuideAnchorId(id),
      label: `${guide.label || 'Drawing'} copy`,
    }

    if (guide.type === 'hline') {
      duplicated.price = (toNumber(guide.price) ?? 0) + baseOffset
    } else if (guide.type === 'note') {
      duplicated.y0 = (toNumber(guide.y0) ?? 0) + baseOffset
    } else {
      duplicated.y0 = (toNumber(guide.y0) ?? 0) + baseOffset
      duplicated.y1 = (toNumber(guide.y1) ?? 0) + baseOffset
    }

    updateCustomGuides((current) => [...current, duplicated], { selectedId: id })
    pushToast('Duplicated the selected drawing.', 'success')
  }

  function toggleSelectedGuideLock() {
    if (!selectedGuideId) return
    const currentGuide = customGuidesRef.current.find((entry) => entry.id === selectedGuideId)
    if (!currentGuide) return
    const nextLocked = !currentGuide.locked
    updateCustomGuides(
      (current) =>
        current.map((guide) =>
          guide.id === selectedGuideId ? { ...guide, locked: nextLocked } : guide,
        ),
      { selectedId: selectedGuideId },
    )
    pushToast(nextLocked ? 'Locked the selected drawing.' : 'Unlocked the selected drawing.', 'info')
  }

  function toggleDrawingGroup(groupKey) {
    if (!groupKey || !(groupKey in DEFAULT_DRAWING_VISIBILITY)) return
    setDrawingVisibility((current) => {
      const nextValue = !(current?.[groupKey] ?? true)
      return {
        ...current,
        [groupKey]: nextValue,
      }
    })
    setSelectedGuideId((currentSelectedId) => {
      if (!currentSelectedId) return currentSelectedId
      const currentGuide = customGuidesRef.current.find((entry) => entry.id === currentSelectedId)
      if (!currentGuide) return null
      return drawingGroupForGuideType(currentGuide.type) === groupKey ? null : currentSelectedId
    })
  }

  function removeNearestGuide(point) {
    if (!customGuides.length) return
    const pointTime = new Date(point.timestamp || 0).getTime()

    let bestGuideId = null
    let bestScore = Number.POSITIVE_INFINITY

    for (const guide of customGuides) {
      let score = Number.POSITIVE_INFINITY
      if (guide.type === 'hline') {
        score = Math.abs((toNumber(guide.price) ?? 0) - (toNumber(point.price) ?? 0))
      } else if (guide.type === 'note') {
        const guidePrice = toNumber(guide.y0) ?? toNumber(point.price) ?? 0
        const guideTime = new Date(guide.x0 || 0).getTime()
        score =
          Math.abs(guidePrice - (toNumber(point.price) ?? 0)) +
          Math.abs(guideTime - pointTime) / (1000 * 60 * 60 * 24)
      } else {
        const y0 = toNumber(guide.y0)
        const y1 = toNumber(guide.y1)
        const midPrice =
          y0 !== null && y1 !== null ? (y0 + y1) / 2 : y0 ?? y1 ?? toNumber(point.price) ?? 0
        const x0 = new Date(guide.x0 || 0).getTime()
        const x1 = new Date(guide.x1 || 0).getTime()
        const midTime = Number.isFinite(x0) && Number.isFinite(x1) ? (x0 + x1) / 2 : pointTime
        score =
          Math.abs(midPrice - (toNumber(point.price) ?? 0)) +
          Math.abs(midTime - pointTime) / (1000 * 60 * 60 * 24)
      }

      if (score < bestScore) {
        bestScore = score
        bestGuideId = guide.id
      }
    }

    if (bestGuideId) {
      updateCustomGuides(
        (current) => current.filter((guide) => guide.id !== bestGuideId),
        { preserveSelection: true },
      )
      setSelectedGuideId((current) => (current === bestGuideId ? null : current))
      pushToast('Removed the nearest custom chart guide.', 'info')
    }
  }

  function handleChartAction(point) {
    if (!point) return

    if (toolMode === 'hline') {
      const id = `guide-${customGuideId.current++}`
      updateCustomGuides((current) => [
        ...current,
        {
          id,
          type: 'hline',
          price: point.price,
          label: `Guide ${current.length + 1}`,
          color: '#9b6bff',
          dash: 'dot',
          group: drawingGroupForGuideType('hline'),
          anchorId: buildGuideAnchorId(id),
        },
      ], { selectedId: id })
      pushToast(`Added a horizontal guide at ${formatPrice(point.price)}.`, 'success')
      return
    }

    if (toolMode === 'trend' || toolMode === 'ray' || toolMode === 'measure' || toolMode === 'rectangle') {
      if (!pendingGuidePoint) {
        setPendingGuidePoint(point)
        return
      }

      const id = `${toolMode}-${customGuideId.current++}`
      updateCustomGuides((current) => [
        ...current,
        {
          id,
          type: toolMode,
          x0: pendingGuidePoint.timestamp,
          y0: pendingGuidePoint.price,
          x1: point.timestamp,
          y1: point.price,
          color:
            toolMode === 'measure'
              ? '#c6c6c6'
              : toolMode === 'rectangle'
                ? '#6a6a6a'
                : toolMode === 'ray'
                  ? '#8a8a8a'
                : '#565656',
          label:
            toolMode === 'measure'
              ? 'Measure'
              : toolMode === 'rectangle'
                ? 'Zone'
                : toolMode === 'ray'
                  ? 'Ray'
                : 'Trend',
          group: drawingGroupForGuideType(toolMode),
          anchorId: buildGuideAnchorId(id),
        },
      ], { selectedId: id })
      setPendingGuidePoint(null)
      pushToast(
        toolMode === 'measure'
          ? 'Added a measure guide.'
          : toolMode === 'rectangle'
            ? 'Added a price zone.'
            : toolMode === 'ray'
              ? 'Added a ray.'
            : 'Added a trendline.',
        'success',
      )
      return
    }

    if (toolMode === 'note') {
      const id = `note-${customGuideId.current++}`
      updateCustomGuides((current) => [
        ...current,
        {
          id,
          type: 'note',
          x0: point.timestamp,
          y0: point.price,
          label: `Note ${current.length + 1}`,
          color: '#f4b942',
          group: drawingGroupForGuideType('note'),
          anchorId: buildGuideAnchorId(id),
        },
      ], { selectedId: id })
      pushToast('Added a chart note marker.', 'success')
      return
    }

    if (toolMode === 'erase') {
      removeNearestGuide(point)
      return
    }
  }

  function handleGuideEdit(update) {
    const descriptor = update?.descriptor
    const fields = update?.fields || {}
    if (!descriptor) return

    if (descriptor.category === 'algo') {
      const nextValue = toNumber(fields.y0 ?? fields.y1)
      if (nextValue === null) return
      setLevelOverrides((current) => ({
        ...current,
        [descriptor.key]: nextValue,
      }))
      return
    }

    if (descriptor.category === 'custom' && descriptor.id) {
      updateCustomGuides(
        (current) =>
          current.map((guide) =>
            guide.id === descriptor.id
              ? {
                  ...guide,
                  ...fields,
                  ...(fields.y0 !== undefined && guide.type === 'hline'
                    ? { price: toNumber(fields.y0) ?? guide.price }
                    : {}),
                  ...(guide.type === 'note'
                    ? {
                        y0: toNumber(fields.y0) ?? guide.y0,
                      }
                    : {}),
                }
              : guide,
          ),
        { selectedId: descriptor.id },
      )
    }
  }

  function handleGuideDelete(id) {
    if (!id) return
    updateCustomGuides(
      (current) => current.filter((guide) => guide.id !== id),
      { selectedId: null },
    )
  }

  function handlePriceSelect(point) {
    if (!point) return
    if (toolMode === 'pan' || toolMode === 'crosshair') {
      setSelectedChartPoint(point)
    }
  }

  function handleViewportChange(update) {
    if (!update || typeof update !== 'object') return
    const nextViewport = mergeViewportState(chartViewportRef.current, update)
    if (areViewportsEqual(chartViewportRef.current, nextViewport)) return

    chartViewportRef.current = nextViewport

    if (viewportCommitTimeoutRef.current) {
      window.clearTimeout(viewportCommitTimeoutRef.current)
    }

    viewportCommitTimeoutRef.current = window.setTimeout(() => {
      viewportCommitTimeoutRef.current = null
      setChartViewport((current) =>
        areViewportsEqual(current, chartViewportRef.current) ? current : chartViewportRef.current,
      )
    }, 160)
  }

  function handleResetChartLayout() {
    if (viewportCommitTimeoutRef.current) {
      window.clearTimeout(viewportCommitTimeoutRef.current)
      viewportCommitTimeoutRef.current = null
    }

    clearChartLayout(layoutStorageKey)
    setSelectedChartPoint(null)
    setPendingGuidePoint(null)
    setHiddenOverlays({})
    setActiveDrawer(null)
    setTapeOpen(false)
    setMarketPanelOpen(true)
    setMarketPanelTab('watchlist')
    setChartStyle('candles')
    setToolMode('pan')
    setMagnetMode(true)
    setDrawingVisibility(DEFAULT_DRAWING_VISIBILITY)
    applyCustomGuides([], { record: false, selectedId: null })
    resetDrawingHistory([])
    setSelectedGuideId(null)
    setLevelOverrides({})
    chartViewportRef.current = null
    setChartViewport(null)
    setLayoutReadyKey(layoutStorageKey)
    pushToast('Chart layout reset for this ticker and interval.', 'info')
  }

  async function handleAnalyze(event) {
    event.preventDefault()
    const nextErrors = buildDeskFormErrors(form)
    if (Object.keys(nextErrors).length) {
      setFormErrors(nextErrors)
      setDeskActionIssue({
        tone: 'warning',
        title: 'Desk controls need a quick fix',
        description: 'Correct the highlighted desk inputs before loading the chart and ticket rail.',
      })
      pushToast('Fix the highlighted desk inputs and try again.', 'error')
      return
    }
    await focusTicker(form.ticker, form.interval, form.horizon)
  }

  async function handleRefreshWorkspace() {
    try {
      setError('')
      setDeskActionIssue(null)
      await loadWorkspace({
        ticker: form.ticker,
        interval: form.interval,
        horizon: form.horizon,
        includeDashboard: false,
        silent: false,
      })
      void getDashboard('desk', dashboardQueryOptions)
        .then((payload) => {
          applyDashboardPayload(payload)
        })
        .catch(() => {
          // keep the current board state if the refresh times out
        })
      pushToast('Trading desk refreshed.', 'info')
    } catch (err) {
      setAnalysisLoading(false)
      setError(err?.response?.data?.detail || err.message || 'Refresh failed.')
      pushToast(err?.response?.data?.detail || err.message || 'Refresh failed.', 'error')
    }
  }

  async function handleQuickInterval(nextInterval) {
    const normalizedInterval = supportedIntervals.includes(nextInterval) ? nextInterval : '5m'
    const currentTicker = String(formRef.current?.ticker || form.ticker || '').trim().toUpperCase()

    setForm((state) => ({ ...state, interval: normalizedInterval }))
    setDeskActionIssue(null)
    setFormErrors((current) => omitKeys(current, ['ticker', 'horizon']))

    if (!isTickerValid(currentTicker)) return

    const immediateRow = liveTickerLookup[currentTicker] || null
    if (hasUsableDeskRow(immediateRow)) {
      const seededChartPayload = buildDeskFallbackChartPayload({
        ticker: currentTicker,
        interval: normalizedInterval,
        row: immediateRow,
      })
      if (seededChartPayload) {
        setChartPayload(
          sanitizeChartPayloadCandles(
            seededChartPayload,
            toNumber(immediateRow?.live_price ?? immediateRow?.current_underlying_price ?? immediateRow?.close),
          ),
        )
      }
    }

    void getChart(
      currentTicker,
      normalizedInterval,
      initialChartPointsForInterval(normalizedInterval),
      preferences?.regularHoursOnly === true,
    )
      .then((payload) => sanitizeChartPayloadCandles(payload))
      .then((payload) => {
        const activeTicker = String(formRef.current?.ticker || '').trim().toUpperCase()
        const activeInterval = String(formRef.current?.interval || '').trim().toLowerCase()
        if (
          activeTicker === currentTicker &&
          activeInterval === normalizedInterval &&
          hasUsableChartPrices(payload)
        ) {
          setChartPayload(payload)
          setError('')
          setDeskActionIssue(null)
        }
      })
      .catch(() => {
        // preserve the current chart on interval fetch failures
      })
  }

  async function handleOpenTrade() {
    if (!report?.ticker || activeExecutionPrice === null) return
    if (profileTradingContext.profileTradingLockedReason) {
      pushToast(profileTradingContext.profileTradingLockedReason, 'error')
      setLastOrderEvent({
        state: 'rejected',
        ticker: report.ticker,
        label: 'Profile locked',
        routeLabel: 'Linked account required',
        bookState: 'blocked',
        bookLabel: 'Blocked',
        detail: profileTradingContext.profileTradingLockedReason,
      })
      return
    }
    if (normalizedInstrumentType === 'listed_option' && tradePreviewLoading) {
      pushToast('Refreshing the listed-option pre-submit route check. Wait for the current quote refresh to finish.', 'warning')
      return
    }
    if (tradePreview?.route_eligibility?.allowed === false) {
      const blockReason =
        tradePreview.route_eligibility.block_reasons?.[0] ||
        tradePreview.route_eligibility.detail ||
        'The backend pre-trade preview blocked this route.'
      pushToast(blockReason, 'error')
      setLastOrderEvent({
        state: 'rejected',
        ticker: report.ticker,
        label: 'Route preview blocked',
        routeLabel: formatInstrumentTypeLabel(tradeTicket.instrumentType),
        bookState: 'blocked',
        bookLabel: 'Blocked',
        detail: blockReason,
      })
      return
    }

    try {
      setLastOrderEvent({
        state: 'submitting',
        ticker: report.ticker,
        detail:
          profileTradingContext.effectiveAccountTargetType === 'linked_client'
            ? `Creating a linked-account approval request for ${profileTradingContext.accountTargetLabel}.`
            : `Submitting ${formatInstrumentTypeLabel(tradeTicket.instrumentType)} ${formatOrderTypeLabel(tradeTicket.orderType)} for ${formatTimeInForceLabel(tradeTicket.timeInForce)}.`,
      })
      const response = await openTrade({
        ticker: report.ticker,
        interval: form.interval,
        horizon: normalizeTradeTicketHorizon(form.horizon),
        account_target_type: profileTradingContext.effectiveAccountTargetType,
        linked_account_id:
          profileTradingContext.effectiveAccountTargetType === 'linked_client'
            ? profileTradingContext.effectiveLinkedAccountId || null
            : null,
        live_price: activeExecutionPrice,
        account_size: tradeTicket.accountSize,
        risk_percent: tradeTicket.riskPercent,
        instrument_type: normalizedInstrumentType,
        broker_side:
          normalizedInstrumentType === 'listed_option'
            ? optionStrategyBrokerSide(normalizedOptionStrategy)
            : 'buy',
        option_strategy: normalizedInstrumentType === 'listed_option' ? normalizedOptionStrategy : null,
        option_right: normalizedInstrumentType === 'listed_option' ? optionRight : null,
        contract_symbol:
          normalizedInstrumentType === 'listed_option'
            ? contract.contract_symbol || null
            : `EQUITY:${report.ticker}`,
        contract_expiration:
          normalizedInstrumentType === 'listed_option' ? contract.expiration || null : null,
        contract_strike:
          normalizedInstrumentType === 'listed_option' ? toNumber(contract.strike) : null,
        contract_bid: normalizedInstrumentType === 'listed_option' ? toNumber(contract.bid) : null,
        contract_ask: normalizedInstrumentType === 'listed_option' ? toNumber(contract.ask) : null,
        contract_mid: normalizedInstrumentType === 'listed_option' ? toNumber(contract.mid) : null,
        contract_spread_pct:
          normalizedInstrumentType === 'listed_option' ? toNumber(contract.spread_pct) : null,
        contract_volume: normalizedInstrumentType === 'listed_option' ? toNumber(contract.volume) : null,
        contract_open_interest:
          normalizedInstrumentType === 'listed_option' ? toNumber(contract.open_interest) : null,
        contract_quote_timestamp:
          normalizedInstrumentType === 'listed_option'
            ? contract.quote_timestamp || contract.timestamp || null
            : null,
        order_type: tradeTicket.orderType,
        time_in_force: tradeTicket.timeInForce,
        limit_price: orderNeedsLimitPrice ? toNumber(tradeTicket.limitPrice) : null,
        stop_price: orderNeedsStopPrice ? toNumber(tradeTicket.stopPrice) : null,
        trailing_percent: orderNeedsTrailingPercent ? toNumber(tradeTicket.trailingPercent) : null,
        extended_hours: tradeTicket.timeInForce === 'day_ext',
        capital_preservation_mode: effectiveCapitalPreservationPolicy.enabled,
        tiny_account_mode: effectiveCapitalPreservationPolicy.tinyAccountMode,
        regular_hours_only: effectiveCapitalPreservationPolicy.regularHoursOnly,
        max_daily_loss_r: effectiveCapitalPreservationPolicy.maxDailyLossR,
        max_consecutive_losses: effectiveCapitalPreservationPolicy.maxConsecutiveLosses,
        max_open_positions: effectiveCapitalPreservationPolicy.maxOpenPositions,
        max_notional_per_trade: effectiveCapitalPreservationPolicy.maxNotionalPerTrade,
        equities_only: effectiveCapitalPreservationPolicy.equitiesOnly,
        limit_orders_only: effectiveCapitalPreservationPolicy.limitOrdersOnly,
        long_only: effectiveCapitalPreservationPolicy.longOnly,
        fractional_shares_only: effectiveCapitalPreservationPolicy.fractionalSharesOnly,
        execution_intent:
          profileTradingContext.effectiveAccountTargetType === 'personal'
            ? selectedExecutionIntent
            : defaultExecutionIntent,
      })

      if (response.intent_created) {
        pushToast(
          `Created approval request for ${response?.trade_intent?.account_label || profileTradingContext.accountTargetLabel || 'the bound linked account'}.`,
          'success',
        )
        setLastOrderEvent({
          state: 'working',
          ticker: report.ticker,
          label: 'Approval required',
          routeLabel: 'Linked-account approval queue',
          bookState: 'pending_approval',
          bookLabel: 'Pending approval',
          detail: `Ticket staged for ${response?.trade_intent?.account_label || profileTradingContext.accountTargetLabel || 'the bound linked account'}. It will not submit until an operator approves it from the trades page.`,
        })
        return
      }

      pushToast(
        response.position_opened
          ? `Trade opened at ${formatPrice(activeExecutionPrice)} via ${response?.execution?.intent === 'broker_live' ? 'Alpaca live' : response?.execution?.intent === 'broker_paper' ? 'Alpaca paper' : 'desk'} routing.`
          : response.pending_order
            ? `${formatInstrumentTypeLabel(tradeTicket.instrumentType)} ${formatOrderTypeLabel(tradeTicket.orderType)} is now working via ${response?.execution?.intent === 'broker_live' ? 'Alpaca live' : response?.execution?.intent === 'broker_paper' ? 'Alpaca paper' : 'desk'} routing.`
            : 'Trade was not opened.',
        response.opened ? 'success' : 'info',
      )

      if (response.opened) {
        const latestEvent = response.latest_order_event || null
        setLastOrderEvent({
          state: response.position_opened ? 'open' : 'working',
          ticker: report.ticker,
          label:
            latestEvent?.label ||
            (response.position_opened ? 'Filled' : 'Working'),
          routeLabel:
            formatOrderLifecycleValue(latestEvent?.route_state, '') ||
            (response.position_opened ? 'Filled' : 'Accepted'),
          bookState:
            latestEvent?.book_state ||
            (response.position_opened ? 'open' : 'pending'),
          bookLabel:
            latestEvent?.status === 'closed'
              ? 'Flat'
              : formatOrderLifecycleValue(latestEvent?.book_state, '') ||
                (response.position_opened ? 'Live fill' : 'Pending'),
          detail:
            latestEvent?.detail ||
            (response.position_opened
              ? `Market order filled near ${formatPrice(activeExecutionPrice)} and is now live on the desk.`
              : `${formatInstrumentTypeLabel(tradeTicket.instrumentType)} ${formatOrderTypeLabel(tradeTicket.orderType)} is working with ${formatTimeInForceLabel(tradeTicket.timeInForce)} rules.`),
        })
      void getDashboard('desk', dashboardQueryOptions)
          .then((payload) => {
            applyDashboardPayload(payload)
          })
          .catch(() => {
            // polling will reconcile shortly if the board refresh misses
          })
      } else {
        setLastOrderEvent({
          state: 'rejected',
          ticker: report.ticker,
          detail: 'The desk reviewed the ticket but did not open a live position.',
        })
      }
    } catch (err) {
      setLastOrderEvent({
        state: 'rejected',
        ticker: report.ticker,
        detail: err?.response?.data?.detail || err.message || 'The order was rejected before opening.',
      })
      pushToast(err?.response?.data?.detail || err.message || 'Failed to open trade.', 'error')
    }
  }

  async function handleReplaceWorkingOrder() {
    if (!activePendingOrder?.order_id) return

    try {
      setPendingOrderActionKey('replace')
      const response = await replacePendingOrder(activePendingOrder.order_id, {
        instrument_type: normalizeInstrumentType(
          activePendingOrder.instrument_type || tradeTicket.instrumentType,
        ),
        option_strategy:
          normalizeInstrumentType(activePendingOrder.instrument_type || tradeTicket.instrumentType) ===
          'listed_option'
            ? normalizeOptionStrategy(activePendingOrder.option_strategy || tradeTicket.optionStrategy)
            : null,
        option_right:
          normalizeInstrumentType(activePendingOrder.instrument_type || tradeTicket.instrumentType) ===
          'listed_option'
            ? String(activePendingOrder.option_right || optionRight || '').trim().toLowerCase() || null
            : null,
        contract_symbol:
          activePendingOrder.contract_symbol ||
          (normalizeInstrumentType(activePendingOrder.instrument_type || tradeTicket.instrumentType) ===
          'listed_option'
            ? contract.contract_symbol || null
            : `EQUITY:${form.ticker}`),
        contract_expiration:
          normalizeInstrumentType(activePendingOrder.instrument_type || tradeTicket.instrumentType) ===
          'listed_option'
            ? activePendingOrder.contract_expiration || contract.expiration || null
            : null,
        contract_strike:
          normalizeInstrumentType(activePendingOrder.instrument_type || tradeTicket.instrumentType) ===
          'listed_option'
            ? toNumber(activePendingOrder.contract_strike) ?? toNumber(contract.strike)
            : null,
        order_type: tradeTicket.orderType,
        time_in_force: tradeTicket.timeInForce,
        limit_price: orderNeedsLimitPrice ? toNumber(tradeTicket.limitPrice) : null,
        stop_price: orderNeedsStopPrice ? toNumber(tradeTicket.stopPrice) : null,
        trailing_percent: orderNeedsTrailingPercent ? toNumber(tradeTicket.trailingPercent) : null,
        extended_hours: tradeTicket.timeInForce === 'day_ext',
      })
      setLastOrderEvent({
        state: 'working',
        ticker: form.ticker,
        label: response.latest_order_event?.label || 'Replaced',
        routeLabel: formatOrderLifecycleValue(response.latest_order_event?.route_state, 'Accepted'),
        bookState: response.latest_order_event?.book_state || 'pending',
        bookLabel: formatOrderLifecycleValue(response.latest_order_event?.book_state, 'Pending'),
        detail:
          response.latest_order_event?.detail ||
          `Working ${formatInstrumentTypeLabel(
            activePendingOrder.instrument_type || tradeTicket.instrumentType,
          )} order replaced with ${formatOrderTypeLabel(tradeTicket.orderType)} instructions.`,
      })
      pushToast('Working order updated.', 'success')
        const payload = await getDashboard('desk', dashboardQueryOptions)
      applyDashboardPayload(payload)
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Failed to replace the working order.', 'error')
    } finally {
      setPendingOrderActionKey('')
    }
  }

  async function handleCancelWorkingOrder() {
    if (!activePendingOrder?.order_id) return

    try {
      setPendingOrderActionKey('cancel')
      const response = await cancelPendingOrder(activePendingOrder.order_id, {})
      setLastOrderEvent({
        state: 'canceled',
        ticker: form.ticker,
        label: response.latest_order_event?.label || 'Canceled',
        routeLabel: formatOrderLifecycleValue(response.latest_order_event?.route_state, 'Canceled'),
        bookState: response.latest_order_event?.book_state || 'flat',
        bookLabel: 'Flat',
        detail: response.latest_order_event?.detail || 'Canceled the working order.',
      })
      pushToast('Working order canceled.', 'success')
        const payload = await getDashboard('desk', dashboardQueryOptions)
      applyDashboardPayload(payload)
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Failed to cancel the working order.', 'error')
    } finally {
      setPendingOrderActionKey('')
    }
  }

  async function handleFillWorkingOrder() {
    if (!activePendingOrder?.order_id || activeExecutionPrice === null) return
    if (reviewOnlyMode) {
      pushToast(
        capitalPreservationSummary.detail ||
          'The desk is in review-only mode until the next regular session.',
        'error',
      )
      return
    }

    try {
      setPendingOrderActionKey('fill')
      const response = await fillPendingOrder(activePendingOrder.order_id, {
        live_price: activeExecutionPrice,
      })
      setLastOrderEvent({
        state: 'open',
        ticker: form.ticker,
        label: response.latest_order_event?.label || 'Filled',
        routeLabel: formatOrderLifecycleValue(response.latest_order_event?.route_state, 'Filled'),
        bookState: response.latest_order_event?.book_state || 'open',
        bookLabel: 'Live position',
        detail:
          response.latest_order_event?.detail ||
          `Working order filled near ${formatPrice(activeExecutionPrice)} and is now live on the desk.`,
      })
      pushToast(`Working order filled at ${formatPrice(activeExecutionPrice)}.`, 'success')
        const payload = await getDashboard('desk', dashboardQueryOptions)
      applyDashboardPayload(payload)
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Failed to fill the working order.', 'error')
    } finally {
      setPendingOrderActionKey('')
    }
  }

  async function handleSaveWorkspace() {
    const nextErrors = buildDeskFormErrors(form)
    if (Object.keys(nextErrors).length) {
      setFormErrors(nextErrors)
      setDeskActionIssue({
        tone: 'warning',
        title: 'Desk layout is not ready to save',
        description: 'Fix the active desk inputs before saving this layout.',
      })
      pushToast('Fix the highlighted desk inputs before saving the layout.', 'error')
      return
    }
    if (!chartPayload && !analysis && !report?.ticker) {
      setDeskActionIssue({
        tone: 'info',
        title: 'Load a live setup before saving the layout',
        description: 'Save the desk layout after the chart and ticket context are loaded so the workspace preserves the current setup.',
      })
      pushToast('Load a live setup before saving the desk layout.', 'warning')
      return
    }
    try {
      setDeskActionIssue(null)
      const workspaceName = `${form.ticker}-${form.interval}-desk`
      const workspacePayload = {
        name: workspaceName,
        page: 'dashboard',
        payload: {
          ticker: form.ticker,
          interval: form.interval,
          horizon: form.horizon,
          staged_price: toNumber(selectedChartPoint?.price),
          account_size: tradeTicket.accountSize,
          risk_percent: tradeTicket.riskPercent,
        },
        notes: 'Saved from the trading workstation.',
      }
      const existing = await getSavedWorkspaces({ page: 'dashboard' })
      const existingItems = Array.isArray(existing?.items) ? existing.items : []
      const matchingWorkspace = existingItems.find((item) => {
            const itemName = String(item?.name || '').trim().toLowerCase()
            const itemPage = String(item?.page || '').trim().toLowerCase()
            return itemName === workspaceName.trim().toLowerCase() && itemPage === 'dashboard'
          })
      const fallbackWorkspace =
        existingItems.find((item) => !item?.pinned && String(item?.page || '').trim().toLowerCase() === 'dashboard') ||
        existingItems.find((item) => String(item?.page || '').trim().toLowerCase() === 'dashboard') ||
        null
      if (matchingWorkspace?.id) {
        await updateWorkspace(matchingWorkspace.id, workspacePayload)
        pushToast('Desk layout updated.', 'success')
      } else {
        try {
          await saveWorkspace(workspacePayload)
          pushToast('Desk layout saved.', 'success')
        } catch (err) {
          const detail = String(err?.response?.data?.detail || err?.message || '').toLowerCase()
          if (detail.includes('plan limit') && fallbackWorkspace?.id) {
            await updateWorkspace(fallbackWorkspace.id, workspacePayload)
            pushToast('Desk layout updated in the existing save slot.', 'success')
            return
          }
          throw err
        }
      }
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Failed to save the desk layout.', 'error')
    }
  }

  async function handleSaveNote() {
    const nextErrors = buildDeskFormErrors(form)
    if (Object.keys(nextErrors).length) {
      setFormErrors(nextErrors)
      setDeskActionIssue({
        tone: 'warning',
        title: 'Desk note needs a valid setup',
        description: 'Fix the desk inputs and load the symbol before saving a ticket note.',
      })
      pushToast('Fix the highlighted desk inputs before saving a note.', 'error')
      return
    }
    if (!report?.ticker) {
      setDeskActionIssue({
        tone: 'info',
        title: 'Analyze the symbol before saving a ticket note',
        description: 'Ticket notes are strongest after the desk has loaded the current report, execution view, and risk framing.',
      })
      pushToast('Load the current symbol before saving a ticket note.', 'warning')
      return
    }
    try {
      setDeskActionIssue(null)
      await createNote({
        title: `${form.ticker} desk note`,
        body: [
          `Verdict: ${report?.verdict || 'N/A'}`,
          `Trade decision: ${report?.trade_decision || 'N/A'}`,
          `Execution: ${liveExecutionDecision || liveTradeStatus || 'N/A'}`,
          `Staged price: ${activeExecutionPrice === null ? 'none' : formatPrice(activeExecutionPrice)}`,
          `Instrument: ${formatInstrumentTypeLabel(tradeTicket.instrumentType)}`,
          `Entry zone: ${formatPrice(optionPlan.entry_low_price)} to ${formatPrice(optionPlan.entry_high_price)}`,
          `Target: ${formatPrice(optionPlan.expected_underlying_target)}`,
          `Invalidation: ${formatPrice(optionPlan.invalidation_price)}`,
          `Momentum state: ${strategySnapshot?.available ? strategySnapshot.state || 'N/A' : 'N/A'}`,
          `Momentum decision: ${strategySnapshot?.available ? strategySnapshot.decision || 'N/A' : 'N/A'}`,
          `Noise area: ${strategySnapshot?.available ? `${formatOptionalPrice(strategySnapshot.lower_band)} to ${formatOptionalPrice(strategySnapshot.upper_band)}` : 'N/A'}`,
          `Trail stop: ${strategySnapshot?.available ? formatOptionalPrice(strategySnapshot.active_stop) : 'N/A'}`,
        ].join(' | '),
        ticker: form.ticker,
        tags: ['dashboard', String(form.interval || '').toLowerCase(), 'trade-desk'],
        pinned: false,
      })
      pushToast(`Saved note for ${form.ticker}.`, 'success')
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Failed to save note.', 'error')
    }
  }

  const handleSaveOperatorMemory = useCallback(async () => {
    const memoryTicker = String(report?.ticker || form.ticker || '').trim().toUpperCase()
    if (!memoryTicker) {
      pushToast('Load a symbol before saving operator memory.', 'warning')
      return
    }

    const suggestedUnits = toNumber(positionPreview?.suggestedContracts)
    const suggestedNotional = toNumber(positionPreview?.totalPositionCost)
    const entryLow = toNumber(optionPlan?.entry_low_price)
    const entryHigh = toNumber(optionPlan?.entry_high_price)
    const targetPrice = toNumber(optionPlan?.expected_underlying_target)
    const invalidationPrice = toNumber(optionPlan?.invalidation_price)
    const routeLabel = String(automationSnapshot?.route || '').trim() || 'desk'
    const stateLabel =
      strategySnapshot?.available && strategySnapshot?.state
        ? String(strategySnapshot.state)
        : 'N/A'
    const decisionLabel =
      strategySnapshot?.available && strategySnapshot?.decision
        ? String(strategySnapshot.decision)
        : report?.trade_decision || 'N/A'

    try {
      setSavingOperatorMemory(true)
      await createNote({
        title: `${memoryTicker} operator memory`,
        body: [
          `Rule: Keep the desk aligned with the current setup until a better memory replaces this one.`,
          `Symbol: ${memoryTicker} ${String(form.interval || '').trim() || 'N/A'}`,
          `Route: ${routeLabel}`,
          `Verdict: ${report?.verdict || 'N/A'}`,
          `Decision: ${decisionLabel}`,
          `Execution: ${liveExecutionDecision || liveTradeStatus || 'N/A'}`,
          `Live price: ${activeExecutionPrice === null ? 'N/A' : formatPrice(activeExecutionPrice)}`,
          `Entry zone: ${entryLow !== null && entryHigh !== null ? `${formatPrice(entryLow)} to ${formatPrice(entryHigh)}` : 'N/A'}`,
          `Target: ${targetPrice === null ? 'N/A' : formatPrice(targetPrice)}`,
          `Invalidation: ${invalidationPrice === null ? 'N/A' : formatPrice(invalidationPrice)}`,
          `Risk budget: ${toNumber(positionPreview?.effectiveMaxRiskDollars) === null ? 'N/A' : formatPrice(positionPreview.effectiveMaxRiskDollars)}`,
          `Suggested size: ${suggestedUnits === null ? 'N/A' : `${formatShares(suggestedUnits)} ${positionPreview?.unitLabel || 'units'}`}`,
          `Notional: ${suggestedNotional === null ? 'N/A' : formatPrice(suggestedNotional)}`,
          `Momentum state: ${stateLabel}`,
          `Noise area: ${strategySnapshot?.available ? `${formatOptionalPrice(strategySnapshot.lower_band)} to ${formatOptionalPrice(strategySnapshot.upper_band)}` : 'N/A'}`,
          `Trail stop: ${strategySnapshot?.available ? formatOptionalPrice(strategySnapshot.active_stop) : 'N/A'}`,
          `What to do: Keep sizing, route, and execution aligned with this snapshot until the desk proves it should change.`,
          `What to avoid: Do not drift into discretionary sizing or route changes without saving a new memory snapshot.`,
        ].join('\n'),
        ticker: memoryTicker,
        tags: ['memory', 'operator-memory', 'trade-desk', String(form.interval || '').toLowerCase()].filter(Boolean),
        owner: 'operator-memory',
        pinned: true,
        priority: 'high',
        note_type: 'market_note',
      })
      await loadOperatorMemoryNotes()
      pushToast(`Saved operator memory for ${memoryTicker}.`, 'success')
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Failed to save operator memory.', 'error')
    } finally {
      setSavingOperatorMemory(false)
    }
  }, [
    activeExecutionPrice,
    automationSnapshot?.route,
    form.interval,
    form.ticker,
    liveExecutionDecision,
    liveTradeStatus,
    loadOperatorMemoryNotes,
    optionPlan,
    positionPreview,
    pushToast,
    report?.ticker,
    report?.trade_decision,
    report?.verdict,
    strategySnapshot,
  ])

  function toggleDrawer(panelKey) {
    setTapeOpen(false)
    if (panelKey === 'plan') {
      setMarketPanelTab('watchlist')
      setMarketPanelOpen(true)
      setActiveDrawer(null)
      return
    }
    setActiveDrawer((current) => (current === panelKey ? null : panelKey))
  }

  function setDockTab(panelKey) {
    if (panelKey === 'tape') {
      setTapeOpen(true)
      setActiveDrawer(null)
      return
    }

    setTapeOpen(false)
    if (panelKey === 'plan') {
      setMarketPanelTab('watchlist')
      setMarketPanelOpen(true)
      setActiveDrawer(null)
      return
    }
    setActiveDrawer(panelKey)
  }

  async function handleMondayPlaybookAction(stepKey) {
    const selectedStep = mondayPlaybook.steps.find((step) => step.key === stepKey) || null
    if (selectedStep?.actionMode === 'repair' && selectedStep?.note) {
      navigate(buildNotesFocusUrl(location.search, selectedStep.note))
      return
    }

    if (stepKey === 'open') {
      setTapeOpen(false)
      setMarketPanelTab('watchlist')
      setMarketPanelOpen(true)
      return
    }

    if (stepKey === 'review') {
      setTapeOpen(false)
      setMarketPanelTab('watchlist')
      setMarketPanelOpen(true)
      const reviewTicker = mondayPlaybook.steps.find((step) => step.key === 'review')?.ticker
      if (reviewTicker) {
        await focusTicker(reviewTicker, form.interval, form.horizon)
      }
      return
    }

    if (stepKey === 'promote') {
      setTapeOpen(false)
      setMarketPanelTab('watchlist')
      setMarketPanelOpen(true)
      const promoteTicker = mondayPlaybook.steps.find((step) => step.key === 'promote')?.ticker
      if (promoteTicker) {
        await focusTicker(promoteTicker, form.interval, form.horizon)
      }
      return
    }

    if (stepKey === 'route') {
      setDockTab('plan')
      return
    }

    if (stepKey === 'journal') {
      if (report?.ticker) {
        await handleSaveNote()
      } else {
        setDockTab('plan')
      }
    }
  }

  async function handleMorningBriefAction() {
    if (morningBrief.actionMode === 'trades') {
      navigate(`/trades${location.search || ''}`)
      return
    }

    if (morningBrief.actionMode === 'calendar') {
      navigate(`/alerts${location.search || ''}`)
      return
    }

    if (morningBrief.actionMode === 'repair' && morningBrief.actionNote) {
      navigate(buildNotesFocusUrl(location.search, morningBrief.actionNote))
      return
    }

    if (morningBrief.actionMode === 'route') {
      setDockTab('plan')
      return
    }

    if (morningBrief.actionMode === 'review' && morningBrief.actionTicker) {
      setTapeOpen(false)
      setMarketPanelTab('watchlist')
      setMarketPanelOpen(true)
      await focusTicker(morningBrief.actionTicker, form.interval, form.horizon)
      return
    }

    setTapeOpen(false)
    setMarketPanelTab('watchlist')
    setMarketPanelOpen(true)
  }

  async function handleLiveFocusAction() {
    if (liveFocusSummary.actionMode === 'trades') {
      navigate(`/trades${location.search || ''}`)
      return
    }

    if (liveFocusSummary.actionMode === 'route') {
      setDockTab('plan')
      return
    }

    if (liveFocusSummary.actionMode === 'review' && liveFocusSummary.actionTicker) {
      setTapeOpen(false)
      setMarketPanelTab('watchlist')
      setMarketPanelOpen(true)
      await focusTicker(liveFocusSummary.actionTicker, form.interval, form.horizon)
      return
    }

    setDockTab('plan')
  }

  function toggleLiveFocusMode() {
    const next = !liveFocusMode
    setLiveFocusMode(next)
    if (next) {
      setMarketPanelTab('watchlist')
      setMarketPanelOpen(true)
    }
  }

  function toggleFocusLock() {
    const currentSymbol = String(report?.ticker || form.ticker || '').trim().toUpperCase()
    if (!currentSymbol) return
    setFocusLockTicker((current) => (current === currentSymbol ? '' : currentSymbol))
  }

  function toggleOverlay(name) {
    setHiddenOverlays((current) => ({
      ...current,
      [name]: !current[name],
    }))
  }

  function resetOverlayVisibility() {
    setHiddenOverlays({})
  }

  function renderDrawerBody(panelKey = activeDrawer) {
    if (panelKey === 'plan') {
      return (
        <div className="chart-side-drawer__note">
          Algo details now live in the right rail under Watchlist so the chart stays clear.
        </div>
      )
    }

    if (panelKey === 'position') {
      return (
        <>
          <div className="chart-side-drawer__note">
            {executionRailState.detail || positionPreview?.statusText || 'Waiting for a valid contract and stop rule.'}
          </div>

          <div className="trade-ticket trade-ticket--execution">
            <div className="trade-ticket__hero trade-ticket__hero--execution">
                          <Kicker className="trade-ticket__kicker">Execution rail</Kicker>
              <div className="trade-ticket__topline">
                <div>
                  <strong>{formatPrice(activeExecutionPrice)}</strong>
                  <p>
                    {form.ticker} {formatInstrumentTypeLabel(tradeTicket.instrumentType)} {formatOrderTypeLabel(tradeTicket.orderType)} |{' '}
                    {formatTimeInForceLabel(tradeTicket.timeInForce)}
                  </p>
                </div>
                <div
                  className={`trade-ticket__hero-tag trade-ticket__hero-tag--${executionRailState.tone}`}
                >
                  {executionRailState.label}
                </div>
              </div>
              <div className="trade-ticket__status-row">
                <span
                  className={`execution-state-badge execution-state-badge--${executionRailState.tone}`}
                >
                  {executionRailState.label}
                </span>
                <p className="trade-ticket__status-detail">{executionRailState.detail}</p>
              </div>
              <div className="trade-ticket__lifecycle">
                {executionRailState.chips.map((chip) => (
                  <div key={chip.label} className="trade-ticket__lifecycle-chip">
                    <span>{chip.label}</span>
                    <strong>{chip.value}</strong>
                  </div>
                ))}
              </div>
              <div className="trade-ticket__lifecycle" aria-label="Current blocker and next safe action">
                <div className="trade-ticket__lifecycle-chip">
                  <span>Current blocker</span>
                  <strong>{blockingTicketReasons[0]?.message || profileTradingContext.profileTradingLockedReason || 'No blocker on the active ticket'}</strong>
                </div>
                <div className="trade-ticket__lifecycle-chip">
                  <span>Next safe action</span>
                  <strong>{blockingTicketReasons[0]?.actionLabel || (canOpenTrade ? 'Review and send with gates visible' : 'Complete ticket validation')}</strong>
                </div>
              </div>
            </div>

            <div className="trade-ticket__form trade-ticket__form--execution">
            <div className="ticket-field ticket-field--choice" ref={registerTicketTarget('instrument')}>
              <TicketFieldLabel label="Instrument" tooltip={instrumentTooltip} />
              <div className="trade-ticket__choice-row" onKeyDown={handleChoiceRowKeyDown}>
                <SegmentedControl
                  value={tradeTicket.instrumentType}
                  options={instrumentTypeOptions}
                  onChange={(instrumentType) =>
                    setTradeTicket((state) => ({
                      ...state,
                      instrumentType,
                    }))
                  }
                  ariaLabel="Instrument selection"
                  className="trade-ticket__segmented-control"
                  size="sm"
                />
              </div>
              <small className="ticket-field__hint">
                {describeInstrumentType(tradeTicket.instrumentType)}
              </small>
            </div>

            {normalizedInstrumentType === 'listed_option' ? (
              <div className="ticket-field ticket-field--choice" ref={registerTicketTarget('option-structure')}>
                <TicketFieldLabel label="Structure" tooltip={optionStructureTooltip} />
                <div className="trade-ticket__choice-row" onKeyDown={handleChoiceRowKeyDown}>
                  <SegmentedControl
                    value={normalizedOptionStrategy}
                    options={optionStrategyOptions}
                    onChange={(optionStrategy) =>
                      setTradeTicket((state) => ({
                        ...state,
                        optionStrategy,
                      }))
                    }
                    ariaLabel="Option structure selection"
                    className="trade-ticket__segmented-control"
                    size="sm"
                  />
                </div>
                <small className="ticket-field__hint">
                  {describeOptionStrategy(normalizedOptionStrategy, optionRight)}
                </small>
              </div>
            ) : null}

            <div className="ticket-field" ref={registerTicketTarget('account-size')}>
              <TicketFieldLabel
                label="Account size"
                tooltip="Account size is the capital base the desk uses for sizing. The ticket risk budget is calculated from this number before contracts or shares are selected."
              />
              <TextField
                type="number"
                min="100"
                step="100"
                value={tradeTicket.accountSize}
                className="ticket-field__control-shell"
                inputClassName="ticket-field__control-input"
                onChange={(event) =>
                  setTradeTicket((state) => ({
                    ...state,
                    accountSize: Number(event.target.value),
                  }))
                }
              />
            </div>

            <div className="ticket-field" ref={registerTicketTarget('account-target')}>
              <TicketFieldLabel
                label="Account target"
                tooltip="The global profile controls the active money lane. Linked Accounts stays bound to one connected account, and personal profiles stay on the personal env-backed lane."
              />
              <SelectField
                value={profileTradingContext.accountTargetValue}
                className="ticket-field__control-shell"
                selectClassName="ticket-field__control-input"
                disabled={profileTradingContext.accountTargetLocked}
              >
                <option value={profileTradingContext.accountTargetValue}>
                  {profileTradingContext.accountTargetLabel}
                </option>
              </SelectField>
              <small className="ticket-field__hint">
                {profileTradingContext.accountTargetHint}
              </small>
            </div>

            <div className="ticket-field ticket-field--choice" ref={registerTicketTarget('order-type')}>
              <TicketFieldLabel label="Order type" tooltip={orderTypeTooltip} />
              <div className="trade-ticket__choice-row" onKeyDown={handleChoiceRowKeyDown}>
                <SegmentedControl
                  value={tradeTicket.orderType}
                  options={orderTypeOptions}
                  onChange={(orderType) => applyExecutionRoute({ orderType })}
                  ariaLabel="Order type selection"
                  className="trade-ticket__segmented-control"
                  size="sm"
                />
              </div>
              <small className="ticket-field__hint">{describeOrderType(tradeTicket.orderType)}</small>
            </div>

            <div className="ticket-field" ref={registerTicketTarget('risk-percent')}>
              <TicketFieldLabel label="Risk %" tooltip={riskPercentTooltip} />
              <TextField
                type="number"
                min="0.1"
                max="10"
                step="0.1"
                value={tradeTicket.riskPercent}
                className="ticket-field__control-shell"
                inputClassName="ticket-field__control-input"
                onChange={(event) =>
                  setTradeTicket((state) => ({
                    ...state,
                    riskPercent: Number(event.target.value),
                  }))
                }
              />
            </div>

            <div className="ticket-field ticket-field--choice" ref={registerTicketTarget('time-in-force')}>
              <TicketFieldLabel label="Time in force" tooltip={timeInForceTooltip} />
              <div
                className="trade-ticket__choice-row trade-ticket__choice-row--compact"
                onKeyDown={handleChoiceRowKeyDown}
              >
                <SegmentedControl
                  value={tradeTicket.timeInForce}
                  options={timeInForceOptions}
                  onChange={(timeInForce) => applyExecutionRoute({ timeInForce })}
                  ariaLabel="Time in force selection"
                  className="trade-ticket__segmented-control trade-ticket__segmented-control--compact"
                  size="sm"
                />
              </div>
              <small className="ticket-field__hint">
                {tradeTicket.timeInForce === 'gtc_90d'
                  ? 'Resting long ideas can stay working for up to 90 days.'
                  : tradeTicket.timeInForce === 'day_ext'
                    ? 'Short-hour orders can work through the after-hours close.'
                    : tradingStyle === 'intraday'
                      ? `Regular-session order that expires at today's close and preserves the ${formatMinuteWindow(preferences?.flattenBeforeCloseMinutes ?? 10)} close buffer.`
                      : "Regular-session order that expires at today's close."}
              </small>
            </div>

            <div className="trade-ticket__helper" ref={registerTicketTarget('contract-summary')}>
              <strong>{formatInstrumentTypeLabel(tradeTicket.instrumentType)} ticket</strong>
              <p>
                {normalizedInstrumentType === 'listed_option'
                  ? `${formatOptionStrategyLabel(normalizedOptionStrategy)} | ${contract.contract_symbol || 'Waiting for contract lookup'} | ${formatLabel(optionPlan.option_side, 'Call')} ${contract.expiration || '--'} ${formatOptionalPrice(contract.strike)} | x100 multiplier`
                  : `Sizing shares against invalidation ${formatPrice(optionPlan.invalidation_price)} using live price ${formatPrice(activeExecutionPrice)}.`}
              </p>
            </div>

            <div ref={registerTicketTarget('execution-guide')}>
              <EducationCallout
                topic="trade-ticket"
                        kicker="Execution guide"
                title="Instrument choice changes both sizing and risk."
                body={
                  normalizedInstrumentType === 'listed_option'
                    ? normalizedOptionStrategy === 'long_option'
                      ? 'Long listed options are buy-to-open contracts with 100-share multiplier sizing. They need a tight contract, a clear stop rule, and regular-hours liquidity.'
                      : 'This option structure is visible for planning, but submit is blocked until the desk has the required margin, assignment, and multi-leg controls.'
                    : 'Equity tickets are linear spot exposure. Shares are sized directly against invalidation, so a small stop with a big position can still create oversized portfolio risk.'
                }
                bullets={
                  normalizedInstrumentType === 'listed_option'
                    ? normalizedOptionStrategy === 'long_option'
                      ? [
                          'One listed option contract controls 100 underlying shares.',
                          'Same-day expiry and after-hours option routing are blocked on purpose.',
                        ]
                      : [
                          'Review-only means no order payload is submitted.',
                          'Switch to long option to route through the current single-leg mapper.',
                        ]
                    : [
                        'Share size is driven by live price versus invalidation.',
                        'Wide quotes and market orders can still turn a valid setup into a poor execution.',
                      ]
                }
                linkLabel="Open ticket guide"
              />
            </div>

            <div className="trade-ticket__helper trade-ticket__helper--plan">
              <strong>Local trade plan preset</strong>
              <p>
                {`${Number(preferences?.defaultRiskPercent || 0.5).toFixed(1)}% risk per trade | trim ${Math.round(Number(preferences?.firstTrimPercent || 33))}% at ${Number(preferences?.firstTargetR || 1).toFixed(1)}R | move stop to breakeven at ${Number(preferences?.breakevenAfterR || 1).toFixed(1)}R | trim ${Math.round(Number(preferences?.secondTrimPercent || 33))}% at ${Number(preferences?.secondTargetR || 2).toFixed(1)}R | stand down at ${Number(preferences?.maxDailyLossR || 1.5).toFixed(1)}R or ${Math.max(1, Math.round(Number(preferences?.maxConsecutiveLosses || 2)))} losses.${tradingStyle === 'intraday' ? ` Intraday mode is cleanest near ${intradayExecutionPlan.recommendedRiskPercent.toFixed(2)}% risk with same-session day orders.` : ''}`}
              </p>
            </div>
            <div
              className={`trade-ticket__helper trade-ticket__helper--${capitalPreservationSummary.tone}${
                capitalPreservationSummary.dailyLossLocked || capitalPreservationSummary.lossStreakLocked
                  ? ' trade-ticket__helper--critical'
                  : ''
              }`}
            >
              <strong>{capitalPreservationSummary.label}</strong>
              <p>{capitalPreservationSummary.detail}</p>
            </div>

            <div className="trade-ticket__micro-grid">
              {ticketEducationCards.map((card) => (
                <div key={card.key} className="trade-ticket__micro-card">
                  <div className="trade-ticket__micro-topline">
                    {card.key === 'risk-structure' ? (
                      <TicketFieldLabel label={card.title} tooltip={instrumentTooltip} />
                    ) : card.key === 'time-window' ? (
                      <TicketFieldLabel label={card.title} tooltip={timeInForceTooltip} />
                    ) : card.key === 'event-and-risk' ? (
                      <TicketFieldLabel
                        label={card.title}
                        tooltip="Event risk means a known catalyst can gap the trade or widen spreads beyond the normal stop logic. Invalidation is the price or condition that proves the setup wrong."
                      />
                    ) : (
                      <span>{card.title}</span>
                    )}
                    <span className={`execution-state-badge execution-state-badge--${card.tone}`}>
                      {card.value}
                    </span>
                  </div>
                  <p className="trade-ticket__micro-detail">{card.detail}</p>
                </div>
              ))}
            </div>

            {orderNeedsLimitPrice ? (
              <div className="ticket-field" ref={registerTicketTarget('limit-price')}>
                <TicketFieldLabel label="Limit price" tooltip={limitPriceTooltip} />
                <TextField
                  type="number"
                  min="0.01"
                  step="0.01"
                  value={tradeTicket.limitPrice}
                  className="ticket-field__control-shell"
                  inputClassName="ticket-field__control-input"
                  onChange={(event) =>
                    setTradeTicket((state) => ({
                      ...state,
                      limitPrice: event.target.value,
                    }))
                  }
                />
              </div>
            ) : null}

            {orderNeedsStopPrice ? (
              <div className="ticket-field" ref={registerTicketTarget('stop-price')}>
                <TicketFieldLabel label="Stop price" tooltip={stopPriceTooltip} />
                <TextField
                  type="number"
                  min="0.01"
                  step="0.01"
                  value={tradeTicket.stopPrice}
                  className="ticket-field__control-shell"
                  inputClassName="ticket-field__control-input"
                  onChange={(event) =>
                    setTradeTicket((state) => ({
                      ...state,
                      stopPrice: event.target.value,
                    }))
                  }
                />
              </div>
            ) : null}

            {orderNeedsTrailingPercent ? (
              <div className="ticket-field" ref={registerTicketTarget('trail-percent')}>
                <TicketFieldLabel label="Trail %" tooltip={trailPercentTooltip} />
                <TextField
                  type="number"
                  min="0.1"
                  step="0.1"
                  value={tradeTicket.trailingPercent}
                  className="ticket-field__control-shell"
                  inputClassName="ticket-field__control-input"
                  onChange={(event) =>
                    setTradeTicket((state) => ({
                      ...state,
                      trailingPercent: event.target.value,
                    }))
                  }
                />
              </div>
            ) : null}

            <div className="trade-ticket__timeline trade-ticket__timeline--checklist">
              <div className="trade-ticket__timeline-header">
                <span>Execution checklist</span>
                <strong>
                  {ticketChecklist.clearedCount}/{ticketChecklist.totalCount} clear
                </strong>
              </div>
              {checklistIsComplete && !checklistExpanded ? (
                <div className="trade-ticket__checklist-ready">
                  <div className="trade-ticket__checklist-ready-copy">
                    <strong>Ready to route</strong>
                    <p>
                      All checklist steps are clear for {String(form.ticker || '').trim().toUpperCase() || 'this setup'}.
                      The execution rail is in review mode now.
                    </p>
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="trade-ticket__route-action trade-ticket__route-action--neutral"
                    onClick={() => setChecklistExpanded(true)}
                  >
                    Review steps
                  </Button>
                </div>
              ) : (
                <>
                  <p className="trade-ticket__checklist-summary">{ticketChecklist.summary}</p>
                  <div className="trade-ticket__checklist">
                    {ticketChecklist.steps.map((step, index) => (
                      <Button
                        key={step.key}
                        type="button"
                        variant={activeChecklistStepKey === step.key ? 'solid' : 'ghost'}
                        size="sm"
                        className={`trade-ticket__checklist-item trade-ticket__checklist-item--${step.tone} ${
                          activeChecklistStepKey === step.key ? 'trade-ticket__checklist-item--active' : ''
                        }`}
                        aria-pressed={activeChecklistStepKey === step.key}
                        onClick={() => handleChecklistStepSelect(step)}
                      >
                        <span className={`trade-ticket__checklist-index trade-ticket__checklist-index--${step.tone}`}>
                          {index + 1}
                        </span>
                        <div className="trade-ticket__checklist-copy">
                          <div className="trade-ticket__checklist-topline">
                            <strong>{step.title}</strong>
                            <span className={`execution-state-badge execution-state-badge--${step.tone}`}>
                              {step.stateLabel}
                            </span>
                          </div>
                          <p>{step.detail}</p>
                          <small>
                            {activeChecklistStepKey === step.key ? 'Working step | ' : ''}
                            {step.actionLabel}
                          </small>
                        </div>
                      </Button>
                    ))}
                  </div>
                  {checklistIsComplete ? (
                    <div className="trade-ticket__checklist-footer">
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="trade-ticket__route-action trade-ticket__route-action--neutral"
                        onClick={() => setChecklistExpanded(false)}
                      >
                        Hide steps
                      </Button>
                    </div>
                  ) : null}
                </>
              )}
            </div>

            <div className="trade-ticket__timeline">
              <div className="trade-ticket__timeline-header">
                <span>Market structure</span>
                <strong>{venueLabel}</strong>
              </div>
              <div className="trade-ticket__structure-grid">
                {marketStructureCards.map((card) => (
                  <div key={card.key} className="analysis-message-card trade-ticket__structure-card">
                    <div className="trade-ticket__structure-topline">
                      <span>{card.title}</span>
                      <span className={`execution-state-badge execution-state-badge--${card.tone}`}>
                        {card.value}
                      </span>
                    </div>
                    <p className="trade-ticket__structure-detail">{card.detail}</p>
                  </div>
                ))}
              </div>
            </div>

            <div className="trade-ticket__timeline">
              <div className="trade-ticket__timeline-header">
                <span>Liquidity and execution</span>
                <strong>{liquidityExecutionWarnings.label}</strong>
              </div>
              <div className="trade-ticket__risk-grid">
                {liquidityExecutionWarnings.cards.map((card) => (
                  <div key={card.key} className="analysis-message-card trade-ticket__risk-card">
                    <div className="trade-ticket__risk-topline">
                      <span>{card.title}</span>
                      <span className={`execution-state-badge execution-state-badge--${card.tone}`}>
                        {card.value}
                      </span>
                    </div>
                    <p className="trade-ticket__risk-detail">{card.detail}</p>
                  </div>
                ))}
              </div>
            </div>

            <div className="trade-ticket__timeline">
              <div className="trade-ticket__timeline-header">
                <span>Cost preview</span>
                <strong>{formatInstrumentTypeLabel(tradeTicket.instrumentType)}</strong>
              </div>
              <div className="trade-ticket__cost-grid">
                {executionCostCards.map((card) => (
                  <div key={card.key} className="analysis-message-card trade-ticket__cost-card">
                    <div className="trade-ticket__cost-topline">
                      <span>{card.title}</span>
                      <span className={`execution-state-badge execution-state-badge--${card.tone}`}>
                        {card.value}
                      </span>
                    </div>
                    <p className="trade-ticket__cost-detail">{card.detail}</p>
                  </div>
                ))}
              </div>
            </div>

            <div className="trade-ticket__timeline">
              <div className="trade-ticket__timeline-header">
                <span>Compare routes</span>
                <strong>{routeComparison.summaryLabel}</strong>
              </div>
              <div className="trade-ticket__route-grid">
                <div className="analysis-message-card trade-ticket__route-card">
                  <div className="trade-ticket__route-topline">
                    <span>Current route</span>
                    <span
                      className={`execution-state-badge execution-state-badge--${routeComparison.current.tone}`}
                    >
                      {routeComparison.current.label}
                    </span>
                  </div>
                  <p className="trade-ticket__route-detail">{routeComparison.current.detail}</p>
                </div>
                <div className="analysis-message-card trade-ticket__route-card trade-ticket__route-card--alt">
                  <div className="trade-ticket__route-topline">
                    <span>{routeComparison.hasAlternative ? 'Safer route' : 'Route status'}</span>
                    <span
                      className={`execution-state-badge execution-state-badge--${routeComparison.alternative.tone}`}
                    >
                      {routeComparison.alternative.label}
                    </span>
                  </div>
                  <p className="trade-ticket__route-detail">{routeComparison.alternative.detail}</p>
                  {routeComparison.hasAlternative ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="trade-ticket__route-action"
                      onClick={() =>
                        applyExecutionRoute({
                          orderType: routeComparison.alternative.orderType,
                          timeInForce: routeComparison.alternative.timeInForce,
                        })
                      }
                    >
                      {routeComparison.alternative.actionLabel}
                    </Button>
                  ) : null}
                </div>
              </div>
            </div>

            {helperContextCount ? (
              <div className={`trade-ticket__helper trade-ticket__helper--${helperContextTone}`}>
                <div className="trade-ticket__context-head">
                  <div className="trade-ticket__context-copy">
                    <strong>Recent updates</strong>
                    <p>
                      {helperContextCount === 1
                        ? '1 recent update is available.'
                        : `${helperContextCount} recent updates are available.`}
                    </p>
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="trade-ticket__route-action trade-ticket__route-action--neutral"
                    onClick={() => setHelperContextExpanded((current) => !current)}
                    aria-expanded={helperContextExpanded}
                    aria-controls="desk-helper-updates"
                  >
                    {helperContextExpanded ? 'Hide updates' : 'Review updates'}
                  </Button>
                </div>
                {helperContextExpanded ? (
                  <div className="trade-ticket__context-stack" id="desk-helper-updates">
                    {routeChangeFeedback ? (
                      <div className="trade-ticket__context-card" aria-live="polite">
                        <div className="trade-ticket__context-topline">
                          <strong>{routeChangeFeedback.summary}</strong>
                          <span className={`execution-state-badge execution-state-badge--${routeChangeFeedback.tone}`}>
                            {routeChangeFeedback.previousLabel}{' -> '}{routeChangeFeedback.currentLabel}
                          </span>
                        </div>
                        <div className="trade-ticket__helper-list">
                          {routeChangeFeedback.improvements.map((item, index) => (
                            <div key={`improve-${index}`} className="trade-ticket__helper-action trade-ticket__helper-action--static">
                              <span>{item}</span>
                              <small>Improved</small>
                            </div>
                          ))}
                          {routeChangeFeedback.worsened.map((item, index) => (
                            <div key={`worse-${index}`} className="trade-ticket__helper-action trade-ticket__helper-action--static">
                              <span>{item}</span>
                              <small>Tradeoff</small>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    {executionReviewDrift ? (
                      <div className="trade-ticket__context-card">
                        <div className="trade-ticket__review-strip">
                          <div className="trade-ticket__review-copy">
                            <strong>{executionReviewDrift.summary}</strong>
                            <p>Conditions changed enough to justify one more review before send.</p>
                          </div>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="trade-ticket__route-action trade-ticket__route-action--neutral"
                            onClick={markExecutionReviewed}
                          >
                            Review complete
                          </Button>
                        </div>
                        <div className="trade-ticket__helper-list">
                          {executionReviewDrift.items.map((item) => (
                            <div key={item.key} className="trade-ticket__helper-action trade-ticket__helper-action--static">
                              <span>{item.message}</span>
                              <small>{item.label}</small>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    {activePendingOrder ? (
                      <div className="trade-ticket__context-card">
                        <div className="trade-ticket__context-topline">
                          <strong>Working order</strong>
                          <span className="execution-state-badge execution-state-badge--info">
                            {activePendingOrder.order_id || 'Desk order'}
                          </span>
                        </div>
                        <p>
                          {formatOrderTypeLabel(activePendingOrder.order_type)} for{' '}
                          {formatShares(
                            activePendingOrder.remaining_contracts ?? activePendingOrder.suggested_contracts,
                          )}{' '}
                          {formatUnitLabel(
                            activePendingOrder.instrument_type,
                            toNumber(
                              activePendingOrder.remaining_contracts ?? activePendingOrder.suggested_contracts,
                            ),
                          )}{' '}
                          is live on the desk.
                        </p>
                        <p>
                          Updated {formatEventTime(activePendingOrder.updated_at || activePendingOrder.submitted_at)}
                        </p>
                      </div>
                    ) : null}

                    {visibleActionHistory.length ? (
                      <div className="trade-ticket__context-card">
                        <div className="trade-ticket__context-topline">
                          <strong>Recent ticket actions</strong>
                          <span className="execution-state-badge execution-state-badge--info">
                            {String(form.ticker || '').trim().toUpperCase()}
                          </span>
                        </div>
                        <div className="trade-ticket__action-history-list">
                          {visibleActionHistory.map((entry) => (
                            <div key={entry.id} className={`trade-ticket__action-history-item trade-ticket__action-history-item--${entry.tone}`}>
                              <div className="trade-ticket__action-history-topline">
                                <strong>{entry.label}</strong>
                                <span>{formatEventTime(entry.createdAt)}</span>
                              </div>
                              <p>{entry.detail}</p>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>

          <div className="chart-dock__stats trade-ticket__snapshot-grid">
            <div className="chart-dock__stat trade-ticket__snapshot">
              <span>Units</span>
              <strong>{formatShares(positionPreview?.suggestedContracts)}</strong>
            </div>
            <div className="chart-dock__stat trade-ticket__snapshot">
              <span>Max risk</span>
              <strong>{formatPrice(positionPreview?.maxRiskDollars)}</strong>
            </div>
            <div className="chart-dock__stat trade-ticket__snapshot">
              <span>Effective risk</span>
              <strong>{formatPrice(positionPreview?.effectiveMaxRiskDollars)}</strong>
            </div>
            <div className="chart-dock__stat trade-ticket__snapshot">
              <span>Risk trim</span>
              <strong>{formatRatioPercent(positionPreview?.riskBudgetMultiplier, 0)}</strong>
            </div>
            <div className="chart-dock__stat trade-ticket__snapshot">
              <span>Position cost</span>
              <strong>{formatPrice(positionPreview?.totalPositionCost)}</strong>
            </div>
            <div className="chart-dock__stat trade-ticket__snapshot">
              <span>Entry unit</span>
              <strong>{formatPrice(positionPreview?.entryUnitPrice ?? positionPreview?.contractMid)}</strong>
            </div>
            <div className="chart-dock__stat trade-ticket__snapshot">
              <span>Regime strength</span>
              <strong>{formatRatioPercent(positionPreview?.regimeStrengthScore, 1)}</strong>
            </div>
            <div className="chart-dock__stat trade-ticket__snapshot">
              <span>Instrument</span>
              <strong>{formatInstrumentTypeLabel(tradeTicket.instrumentType)}</strong>
            </div>
            <div className={`chart-dock__stat trade-ticket__snapshot trade-ticket__snapshot--${routeComparison.current.tone}`}>
              <span>Order type</span>
              <strong>{formatOrderTypeLabel(tradeTicket.orderType)}</strong>
            </div>
              <div className={`chart-dock__stat trade-ticket__snapshot trade-ticket__snapshot--${routeComparison.current.tone}`}>
                <span>Time in force</span>
                <strong>{formatTimeInForceLabel(tradeTicket.timeInForce)}</strong>
              </div>
              <div className={`chart-dock__stat trade-ticket__snapshot trade-ticket__snapshot--${executionRailState.tone}`}>
                <span>Desk status</span>
                <strong>{executionRailState.label}</strong>
              </div>
            <div className={`chart-dock__stat trade-ticket__snapshot trade-ticket__snapshot--${routeComparison.current.tone}`}>
              <span>Route</span>
              <strong>{executionRailState.chips.find((chip) => chip.label === 'Route')?.value || '--'}</strong>
            </div>
          </div>

            <div className="trade-ticket__timeline">
              <div className="trade-ticket__timeline-header">
                <span>Pre-trade risk</span>
                <strong>
                  {normalizedInstrumentType === 'listed_option'
                    ? formatOptionStrategyLabel(normalizedOptionStrategy)
                    : 'Equity'}
                </strong>
              </div>
              {tradePreviewLoading || tradePreviewError || tradePreview?.route_eligibility ? (
                <div
                  className={`trade-ticket__helper trade-ticket__helper--${
                    tradePreviewError
                      ? 'negative'
                      : tradePreviewLoading
                        ? 'info'
                        : tradePreview?.route_eligibility?.allowed
                          ? 'positive'
                          : 'negative'
                  }`}
                >
                  <strong>
                    {tradePreviewLoading
                      ? 'Refreshing route preview'
                      : tradePreviewError
                        ? 'Route preview unavailable'
                        : tradePreview?.route_eligibility?.allowed
                          ? 'Backend route check passed'
                          : 'Backend route check blocked'}
                  </strong>
                  <p>
                    {tradePreviewLoading
                      ? 'Refreshing live option-chain, sizing, capital, and route checks before submit.'
                      : tradePreviewError ||
                        tradePreview?.route_eligibility?.detail ||
                        'The backend preview is aligned with the submit path.'}
                  </p>
                  {tradePreview?.liquidity_execution ? (
                    <div className="trade-ticket__helper-list">
                      {normalizedInstrumentType === 'listed_option' ? (
                        <>
                          <span>Quote age {formatNumber(tradePreview.liquidity_execution.quote_age_seconds, 1)}s</span>
                          <span>Spread {formatOptionalPercent(tradePreview.liquidity_execution.spread_pct, 1)}</span>
                          <span>Vol {formatCompact(tradePreview.liquidity_execution.volume)}</span>
                          <span>OI {formatCompact(tradePreview.liquidity_execution.open_interest)}</span>
                        </>
                      ) : (
                        <>
                          <span>{formatOrderTypeLabel(tradePreview.liquidity_execution.order_type)}</span>
                          <span>{formatTimeInForceLabel(tradePreview.liquidity_execution.time_in_force)}</span>
                        </>
                      )}
                    </div>
                  ) : null}
                </div>
              ) : null}
              <div className="trade-ticket__risk-grid">
                {preTradeRiskPanelCards.map((card) => (
                  <div key={card.key} className="analysis-message-card trade-ticket__risk-card">
                    <div className="trade-ticket__risk-topline">
                      <span>{card.title}</span>
                      <span className={`execution-state-badge execution-state-badge--${card.tone}`}>
                        {card.value}
                      </span>
                    </div>
                    <p className="trade-ticket__risk-detail">{card.detail}</p>
                  </div>
                ))}
              </div>
            </div>

            <div className="trade-ticket__timeline" ref={registerTicketTarget('checks')}>
              <div className="trade-ticket__timeline-header">
                <span>Review checks</span>
                <strong>{preTradeRiskChecks.length || 0}</strong>
              </div>
              <div className="trade-ticket__risk-grid">
                {preTradeRiskChecks.map((check) => (
                  <div key={check.key} className="analysis-message-card trade-ticket__risk-card">
                    <div className="trade-ticket__risk-topline">
                      <span>{check.title}</span>
                      <span
                        className={`execution-state-badge execution-state-badge--${check.tone}`}
                      >
                        {check.value}
                      </span>
                    </div>
                    <p className="trade-ticket__risk-detail">{check.detail}</p>
                  </div>
                ))}
              </div>
            </div>

            {reviewLoopTicketGuardrail.primaryNote ? (
              <div
                className={`trade-ticket__helper trade-ticket__helper--${reviewLoopTicketGuardrail.tone}`}
                ref={registerTicketTarget('review-loop')}
              >
                <strong>
                  {reviewLoopTicketGuardrail.blocker ? 'Repair lock' : 'Repair caution'}
                </strong>
                <p>{reviewLoopTicketGuardrail.blocker || reviewLoopTicketGuardrail.warning}</p>
                <div className="trade-ticket__helper-list">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="trade-ticket__helper-action"
                    onClick={() => navigate(buildNotesFocusUrl(location.search, reviewLoopTicketGuardrail.primaryNote))}
                  >
                    <span>
                      {reviewLoopTicketGuardrail.primaryNote.ticker || 'Desk'}:{' '}
                      {reviewLoopTicketGuardrail.primaryNote.title || 'Open repair note'}
                    </span>
                    <small>
                      {reviewLoopTicketGuardrail.noteCount > 1
                        ? `Open ${reviewLoopTicketGuardrail.noteCount} active notes`
                        : 'Open repair note'}
                    </small>
                  </Button>
                </div>
              </div>
            ) : null}

            {blockingTicketReasons.length ? (
              <div className="trade-ticket__helper trade-ticket__helper--negative trade-ticket__helper--critical">
                <strong>Route blocked</strong>
                <p>
                  {blockingTicketReasons.length > 1
                    ? `${blockingTicketReasons.length} blockers are stopping this route. Choose one to jump to the right field.`
                    : blockingTicketReasons[0].message}
                </p>
                <div className="trade-ticket__helper-list">
                  {blockingTicketReasons.map((reason, index) => (
                    <Button
                      key={`${reason.targetKey || 'review'}-${index}-${reason.message}`}
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="trade-ticket__helper-action"
                      onClick={() => jumpToTicketTarget(reason.targetKey)}
                    >
                      <span>{reason.message}</span>
                      <small>{reason.actionLabel || 'Go to field'}</small>
                    </Button>
                  ))}
                </div>
              </div>
            ) : tradeGuardrails.warningReasons.length ? (
              <div className="trade-ticket__helper trade-ticket__helper--warning trade-ticket__helper--critical">
                <strong>Route warning</strong>
                <p>
                  {tradeGuardrails.warningReasons.length > 1
                    ? `${tradeGuardrails.warningReasons.length} warnings should be reviewed before send.`
                    : tradeGuardrails.warningReasons[0].message}
                </p>
                <div className="trade-ticket__helper-list">
                  {tradeGuardrails.warningReasons.map((reason, index) => (
                    <Button
                      key={`${reason.targetKey || 'review'}-warning-${index}-${reason.message}`}
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="trade-ticket__helper-action"
                      onClick={() => jumpToTicketTarget(reason.targetKey)}
                    >
                      <span>{reason.message}</span>
                      <small>{reason.actionLabel || 'Go to field'}</small>
                    </Button>
                  ))}
                </div>
              </div>
            ) : null}

            <div className="trade-ticket__timeline">
              <div className="trade-ticket__timeline-header">
                <span>Order activity</span>
                <strong>{currentTickerOrderEvents.length || 0}</strong>
              </div>
              {currentTickerOrderEvents.length ? (
                <div className="trade-ticket__timeline-list">
                  {currentTickerOrderEvents.map((event) => (
                    <div key={event.id || `${event.trade_id || 'trade'}-${event.created_at || event.event_key}`} className="trade-ticket__timeline-row">
                      <div className="trade-ticket__timeline-main">
                        <div className="trade-ticket__timeline-topline">
                          <span
                            className={`execution-state-badge execution-state-badge--${executionTone(
                              event.book_state || event.route_state || event.status,
                            )}`}
                          >
                            {formatOrderLifecycleLabel(event)}
                          </span>
                          <small>{formatEventTime(event.created_at)}</small>
                        </div>
                        <p>{event.detail || 'Lifecycle event recorded for this ticket.'}</p>
                      </div>
                      <div className="trade-ticket__timeline-meta">
                        <span>{formatOrderTypeLabel(event.order_type || tradeTicket.orderType)}</span>
                        <span>{formatTimeInForceLabel(event.time_in_force || tradeTicket.timeInForce)}</span>
                        <span>{formatOrderLifecycleValue(event.route_state, 'Recorded')}</span>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="trade-ticket__helper">
                  {paperRouteSelected
                    ? `Paper order events for ${form.ticker} will appear here after the next ticket action.`
                    : liveRouteSelected
                      ? `Live order events for ${form.ticker} will appear here after the next ticket action.`
                      : `Desk route events for ${form.ticker} will appear here after the next ticket action.`}
                </div>
              )}
            </div>

          <div className="trade-ticket__final-rail">
            {!activePendingOrder && executionRouteSummary ? (
              <div className={`trade-ticket__send-confidence trade-ticket__send-confidence--${executionRouteSummary.tone}`}>
                <div className="trade-ticket__final-kicker">Route</div>
                <div className="trade-ticket__send-confidence-topline">
                  <div className="trade-ticket__send-confidence-copy">
                    <strong>{executionRouteSummary.label}</strong>
                    <p>{executionRouteSummary.detail}</p>
                  </div>
                  <span className={`execution-state-badge execution-state-badge--${executionRouteSummary.tone}`}>
                    {executionRouteSummary.badgeLabel}
                  </span>
                </div>
                <div className="trade-ticket__send-confidence-facts">
                  <div className="trade-ticket__send-confidence-fact">
                    <span>Selected</span>
                    <strong>{executionRouteSummary.label}</strong>
                  </div>
                  <div className="trade-ticket__send-confidence-fact">
                    <span>Execution window</span>
                    <strong>{intradayExecutionPlan.cards?.[0]?.value || sessionModel.label}</strong>
                  </div>
                  <div className="trade-ticket__send-confidence-fact">
                    <span>Path</span>
                    <strong>{executionRouteSummary.pathLabel}</strong>
                  </div>
                <div className="trade-ticket__send-confidence-fact">
                    <span>{liveRouteSelected ? 'Paper gate' : paperRouteSelected ? 'Paper sample' : 'Route state'}</span>
                    <strong>
                      {liveRouteSelected
                        ? promotionGateSummary?.label || '--'
                        : paperRouteSelected
                          ? `${promotionGateSummary?.resolvedCount ?? 0} resolved`
                          : executionRouteSummary?.label || '--'}
                    </strong>
                </div>
                </div>
              </div>
            ) : null}

            {sendConfidence ? (
              <div className={`trade-ticket__send-confidence trade-ticket__send-confidence--${sendConfidence.tone}`}>
                <div className="trade-ticket__final-kicker">Review</div>
                <div className="trade-ticket__send-confidence-topline">
                  <div className="trade-ticket__send-confidence-copy">
                    <strong>{sendConfidence.title}</strong>
                    <p>{sendConfidence.detail}</p>
                  </div>
                  <span className={`execution-state-badge execution-state-badge--${sendConfidence.tone}`}>
                    {sendConfidence.locked ? 'Locked' : sendConfidence.tone === 'positive' ? 'Approved' : 'Review'}
                  </span>
                </div>
                <div className="trade-ticket__send-confidence-facts">
                  {sendConfidence.facts.map((fact) => (
                    <div key={fact.key} className="trade-ticket__send-confidence-fact">
                      <span>{fact.label}</span>
                      <strong>{fact.value}</strong>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {actionConfirmation ? (
              <div className={`trade-ticket__action-confirmation trade-ticket__action-confirmation--${actionConfirmation.tone}`}>
                <div className="trade-ticket__final-kicker">Confirm</div>
                <div className="trade-ticket__action-confirmation-copy">
                  <strong>{actionConfirmation.title}</strong>
                  <p>{actionConfirmation.detail}</p>
                </div>
                <div className="trade-ticket__action-confirmation-actions">
                  {actionConfirmation.cancelLabel ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                            className="desk-action"
                      onClick={() => setActionConfirmArmed(false)}
                    >
                      {actionConfirmation.cancelLabel}
                    </Button>
                  ) : null}
                  <span className={`execution-state-badge execution-state-badge--${actionConfirmation.tone}`}>
                    {actionConfirmArmed ? 'Armed' : 'Ready'}
                  </span>
                </div>
              </div>
            ) : null}

            <div className="trade-ticket__actions trade-ticket__actions--final">
              <div className="trade-ticket__final-kicker">Send</div>
              <Button
                type="button"
                variant="solid"
                className={`trade-ticket__primary-action ${
                  actionConfirmArmed ? 'trade-ticket__primary-action--armed' : ''
                } ${actionConfirmation ? `trade-ticket__primary-action--${actionConfirmation.tone}` : ''}`}
                onClick={handlePrimaryAction}
                disabled={
                  activePendingOrder
                    ? reviewOnlyMode || !canOpenTrade || pendingOrderActionKey !== ''
                    : reviewOnlyMode || !canOpenTrade
                }
              >
                {reviewOnlyMode
                  ? activePendingOrder
                    ? 'Replacement locked'
                    : 'Review-only locked'
                  : !activePendingOrder && executionRouteSummary?.locked
                    ? executionRouteSummary?.lockedLabel || 'Route locked'
                  : activePendingOrder
                  ? pendingOrderActionKey === 'replace'
                    ? 'Updating order...'
                    : actionConfirmation?.buttonLabel || 'Replace working order'
                  : actionConfirmation?.buttonLabel ||
                    (selectedChartPoint
                      ? `Send staged ${executionRouteSummary?.sendLabel || 'order'}`
                      : `Send ${executionRouteSummary?.sendLabel || 'live order'}`)}
              </Button>

              {activePendingOrder ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                              className="desk-action"
                  onClick={handleFillWorkingOrder}
                  disabled={reviewOnlyMode || pendingOrderActionKey !== '' || activeExecutionPrice === null}
                >
                  {reviewOnlyMode ? 'Fill paused' : pendingOrderActionKey === 'fill' ? 'Filling...' : 'Fill order'}
                </Button>
              ) : null}

              {activePendingOrder ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                              className="desk-action"
                  onClick={handleCancelWorkingOrder}
                  disabled={pendingOrderActionKey !== ''}
                >
                  {pendingOrderActionKey === 'cancel' ? 'Canceling...' : 'Cancel working order'}
                </Button>
              ) : null}

              <Button
                type="button"
                variant="subtle"
                size="sm"
                                className="desk-action"
                onClick={() => setSelectedChartPoint(null)}
                disabled={!selectedChartPoint}
              >
                Clear chart point
              </Button>

                            <Button type="button" variant="subtle" size="sm" className="desk-action" onClick={handleSaveNote} disabled={!canSaveDeskNote}>
                Save ticket note
              </Button>
            </div>
          </div>
          </div>
        </>
      )
    }

    if (liveFocusMode) {
      return (
        <div className={`chart-mini-section focus-mode focus-mode--${liveFocusSummary.tone}`}>
          <div className="focus-mode__head">
            <div className="focus-mode__copy">
              <span className="chart-mini-section__title">Live focus mode</span>
              <strong>{liveFocusSummary.headline}</strong>
              <p>{liveFocusSummary.summary}</p>
            </div>
            <div className="focus-mode__actions">
              <Button
                type="button"
                variant={liveFocusSummary.isLocked ? 'solid' : 'ghost'}
                size="sm"
                              className={`desk-action focus-mode__lock ${
                  liveFocusSummary.isLocked ? 'focus-mode__lock--active' : ''
                }`}
                onClick={toggleFocusLock}
              >
                {liveFocusSummary.lockLabel}
              </Button>
              <Button
                type="button"
                variant="subtle"
                size="sm"
                              className="desk-action focus-mode__action"
                onClick={() => void handleLiveFocusAction()}
              >
                {liveFocusSummary.actionLabel}
              </Button>
            </div>
          </div>
          <div className={`focus-mode__status focus-mode__status--${liveFocusSummary.isLocked ? 'positive' : 'info'}`}>
            <span>{liveFocusSummary.isLocked ? 'Trade lock active' : 'Trade lock ready'}</span>
            <strong>{liveFocusSummary.lockDetail}</strong>
          </div>
          <div className="focus-mode__grid">
            {liveFocusSummary.cards.map((card) => (
              <div key={card.key} className={`focus-mode__card focus-mode__card--${card.tone}`}>
                <span>{card.title}</span>
                <strong>{card.value}</strong>
                <p>{card.detail}</p>
              </div>
            ))}
          </div>
          <div className="chart-market-panel__footnote">{liveFocusSummary.footnote}</div>
        </div>
      )
    }

    return (
      <>
        <div className="chart-side-drawer__note">
          Entry now {dashboard?.watchlist?.summary?.entry_now ?? 0} | Valid trades{' '}
          {dashboard?.watchlist?.summary?.valid_trades ?? 0}
        </div>

        <div className="chart-focus-list">
              {watchlistFocusRows.length ? (
            watchlistFocusRows.map((row) => (
              <Button
                key={`${row.ticker}-${row.contract_symbol || row.verdict || 'watch'}`}
                type="button"
                variant={String(row.ticker).toUpperCase() === String(form.ticker).toUpperCase() ? 'solid' : 'ghost'}
                size="sm"
                className={`chart-focus-row ${
                  String(row.ticker).toUpperCase() === String(form.ticker).toUpperCase()
                    ? 'chart-focus-row--active'
                    : ''
                }`}
                style={{
                  '--ticker-accent': tickerAccent(row.ticker),
                  '--ticker-accent-soft': hexToRgba(tickerAccent(row.ticker), 0.18),
                }}
                onClick={() => focusTickerInPlace(row.ticker, form.interval, form.horizon)}
                >
                  <div>
                    <strong>{row.ticker || '--'}</strong>
                    <span>{formatPrice(row.live_price ?? row.close)}</span>
                  </div>
                  <StatusBadge value={row.trade_decision || row.verdict || 'Monitor'} />
              </Button>
            ))
          ) : (
            <EmptyState
              title="No liquid-board pulse"
              description={tradingStyle === 'intraday'
                ? `Start here in ${intradayPresetProfile.startupSurface === '/compare' ? 'Compare' : intradayPresetProfile.startupSurface === '/trades' ? 'Trades' : 'Watchlist'} to seed the ${intradayPresetProfile.shortLabel.toLowerCase()} queue, then come back when the desk needs one clear same-session setup.`
                : 'Start here in Watchlist to rank a live basket, then come back when the desk needs one clear setup.'}
              actionLabel={tradingStyle === 'intraday'
                ? intradayPresetProfile.startupSurface === '/compare'
                  ? 'Open compare'
                  : intradayPresetProfile.startupSurface === '/trades'
                    ? 'Open trades'
                    : 'Open watchlist'
                : 'Open watchlist'}
              onAction={() => navigate(tradingStyle === 'intraday' ? intradayPresetProfile.startupSurface : '/watchlist')}
              secondaryActionLabel="Load SPY on desk"
              onSecondaryAction={() => void focusTicker('SPY', form.interval, form.horizon)}
            />
          )}
        </div>

        {scannerFocusRows.length ? (
          <div className="chart-mini-section">
            <span className="chart-mini-section__title">Compare leaders</span>
              <div className="chart-tag-grid">
                {scannerFocusRows.map((row) => (
                  <Chip
                    key={`${row.ticker}-${row.contract_symbol || row.verdict || 'scan'}`}
                    as="button"
                    type="button"
                    tone="neutral"
                    size="sm"
                    className="chart-tag"
                    onClick={() => focusTickerInPlace(row.ticker, form.interval, form.horizon)}
                  >
                    {row.ticker} {row.trade_decision || row.verdict || ''}
                  </Chip>
                ))}
              </div>
            </div>
        ) : null}

        {monitoredOrderRows.length ? (
          <div className="chart-mini-section">
            <span className="chart-mini-section__title">Open-trade monitor</span>
              <div className="chart-focus-list chart-focus-list--compact">
                {monitoredOrderRows.map((row, index) => (
                  <Button
                    key={`${row.ticker || 'row'}-${index}`}
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="chart-focus-row chart-focus-row--compact"
                    style={{
                      '--ticker-accent': tickerAccent(row.ticker),
                    '--ticker-accent-soft': hexToRgba(tickerAccent(row.ticker), 0.18),
                  }}
                  onClick={() =>
                    row.ticker && focusTicker(row.ticker, form.interval, form.horizon)
                  }
                >
                  <div className="chart-focus-row__main">
                    <strong>{row.ticker || '--'}</strong>
                    <span>
                      {row.monitor_action || row.trade_decision || 'Monitor'} | Open P&L{' '}
                      {formatSignedCurrency(row.unrealized_pnl)}
                    </span>
                  </div>
                  <div className="chart-focus-row__aside">
                    <span
                      className={`execution-state-badge execution-state-badge--${row.orderState.tone}`}
                    >
                      {row.orderState.label}
                    </span>
                    <span className="chart-focus-row__price">
                    {formatPrice(
                      row.current_underlying ??
                        row.current_underlying_price ??
                        row.live_price_at_open ??
                        row.entry_underlying_price,
                    )}
                    <InlineMeta
                      as="small"
                      className="chart-focus-row__meta"
                      items={[formatOrderTypeLabel(row.order_type), formatTimeInForceLabel(row.time_in_force)]}
                    />
                    </span>
                    </div>
                  </Button>
                ))}
              </div>
            </div>
        ) : null}
      </>
    )
  }

  function renderTapePanel() {
    return (
      <div className="tv-dock-panel tv-dock-panel--tape">
        <div className="tv-dock-panel__summary">
          <div className="chart-stage-summary__item">
            <span>Prints</span>
            <strong>{tapeSummary.prints}</strong>
          </div>
          <div className="chart-stage-summary__item">
            <span>Shares</span>
            <strong>{formatCompact(tapeSummary.totalSize)}</strong>
          </div>
          <div className="chart-stage-summary__item">
            <span>Notional</span>
            <strong>{formatPrice(tapeSummary.totalNotional)}</strong>
          </div>
          <div className="chart-stage-summary__item">
            <span>Flow</span>
            <strong>
              {formatCompact(tapeSummary.buyFlow)} / {formatCompact(tapeSummary.sellFlow)}
            </strong>
          </div>
        </div>

        <div className="tv-dock-panel__grid">
          <div className="trade-ticket__tape">
            {tradeTape.length ? (
              <div className="trade-ticket__tape-list trade-ticket__tape-list--enhanced">
                {tradeTape.map((tick, index) => (
                  <div
                    key={`${tick.timestamp}-${index}`}
                    className={`trade-ticket__tape-row trade-ticket__tape-row--${tick.side || 'neutral'}`}
                  >
                    <span>{formatClock(tick.timestamp)}</span>
                    <strong>{formatPrice(tick.price)}</strong>
                    <span>{formatCompact(tick.size)} sh</span>
                    <span>{formatPrice(tick.notional)}</span>
                    <span>{String(tick.side || 'neutral').toUpperCase()}</span>
                    <span>{tick.exchange || '--'}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="trade-ticket__helper">
                Trade prints for {form.ticker} will appear here as soon as the stream is live.
              </div>
            )}
          </div>

          <div className="tv-dock-panel__notes">
            <div className="tv-dock-panel__summary">
              <div className="chart-stage-summary__item">
                <span>Bid / ask</span>
                <strong>
                  {formatPrice(selectedQuote?.bid_price)} / {formatPrice(selectedQuote?.ask_price)}
                </strong>
              </div>
              <div className="chart-stage-summary__item">
                <span>Contract</span>
                <strong>{contract.contract_symbol || '--'}</strong>
              </div>
              <div className="chart-stage-summary__item">
                <span>Risk / reward</span>
                <strong>{riskReward === null ? '--' : `${formatNumber(riskReward, 2)}R`}</strong>
              </div>
              <div className="chart-stage-summary__item">
                <span>Largest print</span>
                <strong>
                  {tapeSummary.largestPrint
                    ? `${formatPrice(tapeSummary.largestPrint.price)} | ${formatCompact(
                        tapeSummary.largestPrint.size,
                      )} sh`
                    : '--'}
                </strong>
              </div>
            </div>

            {report?.notes?.length ? (
              <ul className="simple-list">
                {report.notes.slice(0, 4).map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            ) : (
              <div className="trade-ticket__helper">
                Model notes will show up here once the current symbol has guidance attached.
              </div>
            )}
          </div>
        </div>
      </div>
    )
  }

  function renderMarketPanelBody() {
    if (marketPanelTab === 'dom') {
      return (
        <>
          <div className="chart-market-panel__overview">
            <div className="chart-market-panel__overview-card">
              <span>Mid</span>
              <strong>{formatPrice(streamedMidPrice ?? activeExecutionPrice)}</strong>
            </div>
            <div className="chart-market-panel__overview-card">
              <span>Spread</span>
              <strong>{formatPrice(selectedQuote?.spread)}</strong>
            </div>
            <div className="chart-market-panel__overview-card">
              <span>Venue</span>
              <strong>{inferVenueLabel(selectedQuote, selectedTrade, streamMeta?.provider)}</strong>
            </div>
            <div className="chart-market-panel__overview-card">
              <span>Prints</span>
              <strong>{formatCompact(tapeSummary.prints)}</strong>
            </div>
          </div>

          <div className="chart-market-panel__snapshot">
            <div className="chart-stage-summary__item">
              <span>Bid</span>
              <strong>{formatPrice(selectedQuote?.bid_price)}</strong>
            </div>
            <div className="chart-stage-summary__item">
              <span>Ask</span>
              <strong>{formatPrice(selectedQuote?.ask_price)}</strong>
            </div>
            <div className="chart-stage-summary__item">
              <span>Bid size</span>
              <strong>{formatCompact(selectedQuote?.bid_size)}</strong>
            </div>
            <div className="chart-stage-summary__item">
              <span>Ask size</span>
              <strong>{formatCompact(selectedQuote?.ask_size)}</strong>
            </div>
          </div>

          {domLevels.length ? (
            <div className="dom-ladder">
              <div className="dom-ladder__header">
                <span>Bid</span>
                <span>Price</span>
                <span>Ask</span>
              </div>
              <div className="dom-ladder__rows">
                {domLevels.map((row) => (
                  <div
                    key={`${row.price}-${row.bidSize ?? 'b'}-${row.askSize ?? 'a'}`}
                    className={`dom-ladder__row ${row.isBid ? 'dom-ladder__row--bid' : ''} ${
                      row.isAsk ? 'dom-ladder__row--ask' : ''
                    } ${row.isLast ? 'dom-ladder__row--last' : ''}`}
                  >
                    <span>{row.bidSize === null ? '--' : formatCompact(row.bidSize)}</span>
                    <strong>{formatPrice(row.price)}</strong>
                    <span>{row.askSize === null ? '--' : formatCompact(row.askSize)}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="trade-ticket__helper">
              Waiting for a live quote to build the DOM ladder for {form.ticker}.
            </div>
          )}

          <div className="chart-market-panel__footnote">
            Top-of-book ladder uses the live bid, ask, and size feed for {form.ticker}.
          </div>
        </>
      )
    }

    if (marketPanelTab === 'scanner') {
      return scannerFocusRows.length ? (
        <>
          <div className="chart-market-panel__overview">
            <div className="chart-market-panel__overview-card">
              <span>Leaders</span>
              <strong>{formatCompact(scannerFocusRows.length)}</strong>
            </div>
            <div className="chart-market-panel__overview-card">
              <span>Top board</span>
              <strong>{formatNumber(scannerFocusRows[0]?.ranking_score ?? scannerFocusRows[0]?.setup_score, 1)}</strong>
            </div>
            <div className="chart-market-panel__overview-card">
              <span>Best prob</span>
              <strong>{formatPercent((toNumber(scannerFocusRows[0]?.probability_up) ?? 0) * 100, 1)}</strong>
            </div>
            <div className="chart-market-panel__overview-card">
              <span>Interval</span>
              <strong>{form.interval}</strong>
            </div>
          </div>

          <div className="tv-watchlist-table tv-watchlist-table--scanner">
            <div className="tv-watchlist-table__header">
              <span>Symbol</span>
              <span>Board</span>
              <span>Prob</span>
              <span>Last</span>
            </div>

            {scannerFocusRows.map((row) => (
              (() => {
                const active = String(row.ticker).toUpperCase() === String(form.ticker).toUpperCase()
                const previewSeries = buildWatchlistPreviewSeries(row, row.history)
                return (
                  <Button
                    key={`${row.ticker}-${row.contract_symbol || row.verdict || 'scan'}`}
                    type="button"
                    variant={active ? 'solid' : 'ghost'}
                    size="sm"
                    className={`tv-watchlist-table__row tv-watchlist-table__row--scanner ${
                      active ? 'tv-watchlist-table__row--active' : ''
                    }`}
                    style={{
                      '--ticker-accent': tickerAccent(row.ticker),
                      '--ticker-accent-soft': hexToRgba(tickerAccent(row.ticker), 0.18),
                    }}
                    onClick={() => focusTicker(row.ticker, form.interval, form.horizon)}
                  >
                    <div className="tv-watchlist-table__symbol">
                      <strong>
                        <SignalDot
                          className="tv-watchlist-table__dot"
                          accent={tickerAccent(row.ticker)}
                          glow={hexToRgba(tickerAccent(row.ticker), 0.35)}
                          size="sm"
                        />
                        {row.ticker || '--'}
                      </strong>
                      <div className="tv-watchlist-table__symbol-meta">
                        <span>{row.trade_decision || row.verdict || 'Scanner'}</span>
                        <WatchlistSparkline
                          values={previewSeries}
                          accent={tickerAccent(row.ticker)}
                          active={active}
                        />
                      </div>
                    </div>
                    <span>{formatNumber(row.ranking_score ?? row.setup_score, 1)}</span>
                    <span>{formatPercent((toNumber(row.probability_up) ?? 0) * 100, 1)}</span>
                    <span>{formatPrice(row.live_price ?? row.current_underlying_price ?? row.close)}</span>
                  </Button>
                )
              })()
            ))}
          </div>
        </>
      ) : (
        <EmptyState
          title="No compare leaders"
          description={tradingStyle === 'intraday'
            ? `Start here in Compare to qualify a fresh ${intradayPresetProfile.shortLabel.toLowerCase()} leader when the scanner side of the desk does not have a live queue yet.`
            : 'Start here in Compare to qualify a fresh board leader when the scanner side of the desk does not have a live queue yet.'}
          actionLabel={tradingStyle === 'intraday' && intradayPresetProfile.startupSurface === '/trades' ? 'Open trades' : 'Open compare'}
          onAction={() => navigate(tradingStyle === 'intraday' && intradayPresetProfile.startupSurface === '/trades' ? '/trades' : '/compare')}
          secondaryActionLabel="Open watchlist"
          onSecondaryAction={() => navigate('/watchlist')}
        />
      )
    }

    return (
      <>
        <div className="tv-sidebar-symbol-card">
          <div className="tv-sidebar-symbol-card__head">
            <div>
              <Kicker as="div">
                {strategySnapshot?.available
                  ? String(strategySnapshot.state || strategySnapshot.latest_action || 'monitoring').toUpperCase()
                  : report?.trade_decision || 'Monitoring'}
              </Kicker>
              <strong>{form.ticker}</strong>
            </div>
            <StatusBadge
              value={
                strategySnapshot?.available
                  ? strategySnapshot.latest_action || strategySnapshot.state || 'Monitoring'
                  : liveExecutionDecision || liveTradeStatus || 'Monitoring'
              }
            />
          </div>
          <div className="tv-sidebar-symbol-card__price">{formatPrice(activeExecutionPrice)}</div>
          <div
            className={`tv-sidebar-symbol-card__change ${
              toNumber(activePriceDelta) > 0
                ? 'tv-sidebar-symbol-card__change--up'
                : toNumber(activePriceDelta) < 0
                  ? 'tv-sidebar-symbol-card__change--down'
                  : ''
            }`}
          >
            {formatSignedNumber(activePriceDelta)} ({formatSignedPercent(activePriceDeltaPct)})
          </div>
          <div className="tv-sidebar-symbol-card__quote">
            <div>
              <span>Bid</span>
              <strong>{formatMeaningfulPrice(selectedQuote?.bid_price)}</strong>
            </div>
            <div>
              <span>Ask</span>
              <strong>{formatMeaningfulPrice(selectedQuote?.ask_price)}</strong>
            </div>
            <div>
              <span>Spread</span>
              <strong>
                {formatMeaningfulPrice(
                  resolveDisplaySpread(selectedQuote?.spread, selectedQuote?.bid_price, selectedQuote?.ask_price),
                )}
              </strong>
            </div>
          </div>
        </div>

        {deskResearchSnapshot ? (
          <div className="desk-research-board desk-research-board--rail">
            <DeskResearchCard snapshot={deskResearchSnapshot} compact showPath={false} />
          </div>
        ) : null}

        {showExtendedSidebarDetails ? (
          <div className="chart-market-panel__overview">
          <div className="chart-market-panel__overview-card">
            <span>Entry now</span>
            <strong>{formatCompact(dashboard?.watchlist?.summary?.entry_now ?? 0)}</strong>
          </div>
          <div className="chart-market-panel__overview-card">
            <span>Valid</span>
            <strong>{formatCompact(dashboard?.watchlist?.summary?.valid_trades ?? 0)}</strong>
          </div>
          <div className="chart-market-panel__overview-card">
            <span>High conv.</span>
            <strong>{formatCompact(dashboard?.watchlist?.summary?.high_conviction ?? 0)}</strong>
          </div>
          <div className="chart-market-panel__overview-card">
            <span>Tracked</span>
            <strong>{formatCompact(sidebarRows.length)}</strong>
          </div>
        </div>
        ) : null}

        {showExtendedSidebarDetails ? (
          <>
        <div className={`morning-brief desk-readiness desk-readiness--${deskReadiness.tone}`}>
          <div className="morning-brief__head">
            <div className="morning-brief__copy">
              <span className="chart-mini-section__title">{deskReadiness.title || 'Desk route state'}</span>
              <strong>{deskReadiness.headline}</strong>
              <p>{deskReadiness.summary}</p>
            </div>
            <Button
              type="button"
              variant="subtle"
              size="sm"
              className="desk-action morning-brief__action"
              onClick={() => navigate(deskReadiness.actionRoute)}
            >
              {deskReadiness.actionLabel}
            </Button>
          </div>
          <div className="morning-brief__grid">
            {deskReadiness.items.map((item) => (
              <div key={item.key} className={`morning-brief__item morning-brief__item--${item.tone}`}>
                <span>{item.label}</span>
                <strong>{item.value}</strong>
                <small>{item.detail}</small>
              </div>
            ))}
          </div>
        </div>

        <div className={`chart-mini-section morning-brief morning-brief--${morningBrief.tone}`}>
          <div className="morning-brief__head">
            <div className="morning-brief__copy">
              <span className="chart-mini-section__title">Morning brief</span>
              <strong>{morningBrief.headline}</strong>
              <p>{morningBrief.summary}</p>
            </div>
            <Button
              type="button"
              variant="subtle"
              size="sm"
                          className="desk-action morning-brief__action"
              onClick={() => void handleMorningBriefAction()}
            >
              {morningBrief.actionLabel}
            </Button>
          </div>
          {resolvedRepairNotice ? (
            <div className="trade-ticket__helper trade-ticket__helper--positive">
              <strong>Repair cleared</strong>
              <p>{resolvedRepairNotice.detail}</p>
            </div>
          ) : null}
          <p className="morning-brief__detail">{morningBrief.detail}</p>
          <div className="morning-brief__grid">
            {morningBrief.items.map((item) => (
              <div key={item.key} className={`morning-brief__item morning-brief__item--${item.tone}`}>
                <span>{item.label}</span>
                <strong>{item.value}</strong>
                <small>{item.detail}</small>
              </div>
            ))}
          </div>
        </div>
          </>
        ) : null}

        <div className="chart-side-visibility-bar">
          <div className="chart-side-visibility-bar__copy">
            <span className="chart-mini-section__title">{sidebarDetailModeLabel}</span>
            <p>{sidebarDetailModeSummary}</p>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="desk-action"
            onClick={() => setShowExtendedSidebarDetails((current) => !current)}
          >
            {showExtendedSidebarDetails ? 'Show core only' : 'Show more details'}
          </Button>
        </div>

        {showExtendedSidebarDetails ? (
          <>
        <div className="chart-mini-section">
          <span className="chart-mini-section__title">Pre-open snapshot</span>
          <div className="snapshot-grid">
            {preOpenSnapshot.cards.map((card) => (
              <div key={card.key} className={`snapshot-card snapshot-card--${card.tone}`}>
                <div className="snapshot-card__head">
                  <span>{card.title}</span>
                  <span className={`execution-state-badge execution-state-badge--${card.tone}`}>
                    {card.status}
                  </span>
                </div>
                <p className="snapshot-card__detail">{card.detail}</p>
              </div>
            ))}
          </div>
          <div className="chart-market-panel__footnote">{preOpenSnapshot.footnote}</div>
        </div>

        <div className="chart-mini-section">
          <span className="chart-mini-section__title">Monday playbook</span>
          <div className="playbook-grid">
            {mondayPlaybook.steps.map((step, index) => (
              <div
                key={step.key}
                className={`playbook-step playbook-step--${step.tone}`}
              >
                <div className="playbook-step__head">
                  <div className="playbook-step__title">
                    <span>{`Step ${index + 1}`}</span>
                    <strong>{step.title}</strong>
                  </div>
                  <span className={`execution-state-badge execution-state-badge--${step.tone}`}>
                    {step.status}
                  </span>
                </div>
                <p className="playbook-step__detail">{step.detail}</p>
                <Button
                  type="button"
                  variant="subtle"
                  size="sm"
                              className="desk-action playbook-step__action"
                  onClick={() => void handleMondayPlaybookAction(step.key)}
                >
                  {step.actionLabel}
                </Button>
              </div>
            ))}
          </div>
          <div className="chart-market-panel__footnote">{mondayPlaybook.footnote}</div>
        </div>

        <div className="chart-mini-section">
          <span className="chart-mini-section__title">Session handoff</span>
          <div className="handoff-grid">
            {sessionHandoff.cards.map((card) => (
              <div
                key={card.key}
                className={`handoff-card handoff-card--${card.tone} ${
                  card.key === sessionHandoff.activePhase ? 'handoff-card--current' : ''
                }`}
              >
                <div className="handoff-card__head">
                  <div className="handoff-card__title">
                    <strong>{card.title}</strong>
                    <span>{card.status}</span>
                  </div>
                  <span className={`execution-state-badge execution-state-badge--${card.tone}`}>
                    {card.status}
                  </span>
                </div>
                <p className="handoff-card__detail">{card.detail}</p>
                <p className="handoff-card__focus">{card.focus}</p>
              </div>
            ))}
          </div>
          <div className="chart-market-panel__footnote">{sessionHandoff.footnote}</div>
        </div>

        <div className="chart-mini-section">
          <span className="chart-mini-section__title">Post-close review</span>
          <div className="close-review-grid">
            {postCloseReview.cards.map((card) => (
              <div key={card.key} className={`close-review-card close-review-card--${card.tone}`}>
                <div className="close-review-card__head">
                  <div className="close-review-card__title">
                    <strong>{card.title}</strong>
                    <span>{card.status}</span>
                  </div>
                  <span className={`execution-state-badge execution-state-badge--${card.tone}`}>
                    {card.status}
                  </span>
                </div>
                <p className="close-review-card__detail">{card.detail}</p>
                <p className="close-review-card__focus">{card.focus}</p>
              </div>
            ))}
          </div>
          <div className="chart-market-panel__footnote">{postCloseReview.footnote}</div>
        </div>

        <div className="chart-mini-section">
          <span className="chart-mini-section__title">Event calendar</span>
          {eventCalendarCards.length ? (
            <>
              <div
                ref={eventCalendarNavigation.containerRef}
                className="candidate-queue__grid"
                onKeyDown={eventCalendarNavigation.onKeyDown}
              >
                {eventCalendarCards.map((item) => (
                  <Button
                    key={item.key}
                    type="button"
                    variant="ghost"
                    size="sm"
                    className={`candidate-queue__item candidate-queue__item--${item.tone}`}
                    onClick={() => (item.ticker ? focusTicker(item.ticker, form.interval, form.horizon) : navigate('/alerts'))}
                  >
                    <div className="candidate-queue__meta">
                      <strong>{item.ticker || item.title}</strong>
                      <span className={`execution-state-badge execution-state-badge--${item.tone}`}>
                        {item.status}
                      </span>
                    </div>
                    <div className="ui-list-cell__badges">
                      <StatusBadge tone={item.tone}>{item.impact}</StatusBadge>
                    </div>
                    <div className="candidate-queue__stack">
                      <span>{item.ticker ? item.title : `${item.title} on deck`}</span>
                      {item.detail ? <span>{item.detail}</span> : null}
                      <InlineMeta as="span" items={[item.source, item.dateLabel, item.daysLabel]} />
                    </div>
                  </Button>
                ))}
              </div>
              <div className="chart-market-panel__footnote">
                {eventCalendarPayload?.summary?.next_item
                  ? `Next on deck: ${eventCalendarPayload.summary.next_item.title} ${formatEventTime(eventCalendarPayload.summary.next_item.event_date)}.`
                  : 'Macro releases and next ticker catalysts will show up here as the board calendar fills in.'}
              </div>
            </>
          ) : (
            <p className="chart-market-panel__footnote">
              No scheduled macro or ticker catalysts are on the desk calendar yet.
            </p>
          )}
        </div>

        <div className="chart-mini-section">
          <span className="chart-mini-section__title">Operator memory</span>
          {operatorMemoryNoteCards.length ? (
            <>
              <div className="candidate-queue__grid">
                {operatorMemoryNoteCards.map((note) => (
                  <Button
                    key={note.id || `${note.ticker}-${note.title}`}
                    type="button"
                    variant="ghost"
                    size="sm"
                    className={`candidate-queue__item candidate-queue__item--${note.tone}`}
                    onClick={() => navigate(buildOperatorMemoryNotesUrl(location.search, note))}
                  >
                    <div className="candidate-queue__meta">
                      <strong>{note.ticker || 'Desk'}</strong>
                      <span className={`execution-state-badge execution-state-badge--${note.tone}`}>
                        {note.status}
                      </span>
                    </div>
                    <div className="ui-list-cell__badges">
                      <StatusBadge tone={note.tone}>{note.priority}</StatusBadge>
                    </div>
                    <div className="candidate-queue__stack">
                      <span>{note.title}</span>
                      {note.detail ? <span>{note.detail}</span> : null}
                      <InlineMeta
                        as="span"
                        items={[note.owner || 'Operator memory', `Updated ${note.updatedLabel}`]}
                      />
                    </div>
                  </Button>
                ))}
              </div>
              <div className="chart-market-panel__footnote">
                These memory notes stay pinned so the desk can reuse prior decisions instead of rebuilding context from scratch.
              </div>
            </>
          ) : (
            <p className="chart-market-panel__footnote">
              No operator memory is saved yet. Capture the current desk state when you want the next session to inherit the same rule set.
            </p>
          )}
          <div className="ui-button-row">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="desk-action"
              onClick={handleSaveOperatorMemory}
              disabled={savingOperatorMemory}
            >
              {savingOperatorMemory ? 'Saving memory…' : 'Save current context'}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="desk-action"
              onClick={() => navigate(buildOperatorMemoryNotesUrl(location.search))}
            >
              Open memory
            </Button>
          </div>
        </div>

        <div className="chart-mini-section">
          <span className="chart-mini-section__title">Repair notes</span>
          {reviewLoopNoteCards.length ? (
            <>
              <div
                ref={repairNotesNavigation.containerRef}
                className="candidate-queue__grid"
                onKeyDown={repairNotesNavigation.onKeyDown}
              >
                {reviewLoopNoteCards.map((note) => (
                  <Button
                    key={note.id || `${note.ticker}-${note.title}`}
                    type="button"
                    variant="ghost"
                    size="sm"
                    className={`candidate-queue__item candidate-queue__item--${note.tone}`}
                    onClick={() => navigate(buildNotesFocusUrl(location.search, note))}
                  >
                    <div className="candidate-queue__meta">
                      <strong>{note.ticker || 'General'}</strong>
                      <span className={`execution-state-badge execution-state-badge--${note.tone}`}>
                        {note.status}
                      </span>
                    </div>
                    <div className="ui-list-cell__badges">
                      <StatusBadge tone={note.tone}>{note.priority}</StatusBadge>
                    </div>
                    <div className="candidate-queue__stack">
                      <span>{note.title}</span>
                      {note.detail ? <span>{note.detail}</span> : null}
                      <InlineMeta
                        as="span"
                        items={[note.owner || 'Review loop', `Updated ${note.updatedLabel}`]}
                      />
                    </div>
                  </Button>
                ))}
              </div>
              <div className="chart-market-panel__footnote">
                {reviewLoopNotesPayload?.count > reviewLoopNoteCards.length
                  ? `${reviewLoopNotesPayload.count} active repair notes are shaping the next session. Open Notes to see the full queue.`
                  : 'These are the active repair notes shaping the next session.'}
              </div>
            </>
          ) : (
            <>
              <p className="chart-market-panel__footnote">
                No active repair notes are saved yet. Capture one from Notes when a drift,
                thesis miss, or risk review needs follow-up.
              </p>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="desk-action"
                onClick={() => navigate(buildReviewLoopNotesUrl(location.search, 'open'))}
              >
                Open Notes
              </Button>
              {reviewLoopProgress?.latest_resolved ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="desk-action"
                  onClick={() =>
                    navigate(buildReviewLoopNotesUrl(location.search, 'completed', reviewLoopProgress.latest_resolved))
                  }
                >
                  Open latest clear
                </Button>
              ) : null}
            </>
          )}
        </div>

        <div className="chart-mini-section">
          <span className="chart-mini-section__title">Tomorrow prep</span>
          <div className="tomorrow-prep-grid">
            {tomorrowPrep.cards.map((card) => (
              <div key={card.key} className={`tomorrow-prep-card tomorrow-prep-card--${card.tone}`}>
                <div className="tomorrow-prep-card__head">
                  <div className="tomorrow-prep-card__title">
                    <strong>{card.title}</strong>
                    <span>{card.status}</span>
                  </div>
                  <span className={`execution-state-badge execution-state-badge--${card.tone}`}>
                    {card.status}
                  </span>
                </div>
                <p className="tomorrow-prep-card__detail">{card.detail}</p>
                {card.items.length ? (
                  <div className="tomorrow-prep-list">
                    {card.items.map((item) => (
                  <Button
                    key={`${card.key}-${item.ticker}`}
                    type="button"
                    variant="ghost"
                    size="sm"
                    className={`tomorrow-prep-item tomorrow-prep-item--${item.tone}`}
                    onClick={() => void focusTicker(item.ticker, form.interval, form.horizon)}
                  >
                        <div className="tomorrow-prep-item__meta">
                          <strong>{item.ticker}</strong>
                          <StatusBadge tone={item.tone === 'info' ? 'neutral' : item.tone}>
                            {item.tone === 'positive'
                              ? 'Carry'
                              : item.tone === 'negative'
                                ? 'Reset'
                                : item.tone === 'warning'
                                  ? 'Review'
                                  : 'Watch'}
                          </StatusBadge>
                        </div>
                        <span>{item.reason}</span>
                      </Button>
                    ))}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        <div className="chart-market-panel__footnote">{tomorrowPrep.footnote}</div>
        </div>

        <StrategyDeskStatusPanel
          eyebrow="Quant desks"
          title="Shared desk allocator"
          subtitle="Macro and stat-arb now publish desk targets into a shared allocator and risk gate. Watch accepted runs, aggregated exposure, and target flow here before promoting the route."
        />

        <WorkflowGuide
          compact
          showSteps={false}
          eyebrow="Desk workflow"
          phaseLabel="Phase 3 - Act safely"
          phaseTone={promotionGateSummary?.tone === 'negative' ? 'warning' : 'positive'}
          title={tradingStyle === 'intraday' ? intradayPresetGuide.title : 'Use the desk to turn one qualified setup into a safe action.'}
          description={tradingStyle === 'intraday' ? `${intradayPresetGuide.description} Keep the desk focused on one own-account setup that can still survive route quality, event pressure, and rollout gates.` : 'Candidate queue narrows attention. Decision gate decides whether the setup is promotable, review-only, or a stand-down.'}
          steps={buildWorkflowSteps(2)}
          cards={[
            {
              label: 'Use this page for',
              value: tradingStyle === 'intraday' ? `Read one ${intradayPresetProfile.shortLabel.toLowerCase()} idea under route, gate, and event context before you send anything.` : 'Read the current setup under route, gate, and event context.',
              detail: tradingStyle === 'intraday' ? `${intradayPresetGuide.helper} The desk should reduce the session to one qualified decision, not a stack of loose ideas.` : 'The desk should make the next safe action obvious before any order is sent.',
            },
            {
              label: 'Best next move',
              value: tradingStyle === 'intraday' ? `Let the desk decide whether the ${intradayPresetProfile.shortLabel.toLowerCase()} idea is still route-ready, review-only, or cleanup-only for your own account.` : 'Let the decision gate either promote, review, or stop the setup.',
              detail: liveRouteSelected
                ? (tradingStyle === 'intraday'
                    ? `If the live gate, session window, or route lock is active, stand down or repair the ${intradayPresetProfile.shortLabel.toLowerCase()} idea. Do not force an order through just because the chart still looks attractive.`
                    : 'If the live gate or route lock is active, the correct action is to wait or repair the setup, not force it through.')
                : (tradingStyle === 'intraday'
                    ? `If the session window or route lock is active, stand down or repair the ${intradayPresetProfile.shortLabel.toLowerCase()} idea. Do not use paper mode as an excuse to force weak setups.`
                    : 'If the route or setup quality is weak, the correct action is to wait or repair the setup instead of forcing it through.'),
              tone: 'positive',
            },
            {
              label: 'Do not ignore',
              value: liveRouteSelected
                ? (tradingStyle === 'intraday' ? 'Session decay, route locks, and the live gate are part of the trade idea.' : 'Route locks and the live gate are part of the decision.')
                : (tradingStyle === 'intraday' ? 'Session decay, route quality, and fill behavior are part of the paper trade idea.' : 'Route quality and fill behavior are part of the decision.'),
              detail: liveRouteSelected
                ? (tradingStyle === 'intraday'
                    ? `A ${intradayPresetProfile.shortLabel.toLowerCase()} setup is not clear just because the chart looks good. It also has to survive session timing, execution posture, and rollout controls before it earns a live order.`
                    : 'A setup is not clear just because the chart looks good. It also has to survive execution posture and rollout controls.')
                : (tradingStyle === 'intraday'
                    ? `A ${intradayPresetProfile.shortLabel.toLowerCase()} setup is not useful in paper mode unless it also survives timing, route quality, and fill review.`
                    : 'A setup is not useful in paper mode unless it also survives route quality and fill review.'),
              tone: 'warning',
            },
          ]}
        />

        <WorkflowGuide
          compact
          showSteps={false}
          eyebrow={liveRouteSelected ? 'Go-live gate' : paperRouteSelected ? 'Paper route' : 'Desk route'}
          phaseLabel={liveRouteSelected ? 'Phase 3 - Promotion check' : paperRouteSelected ? 'Phase 3 - Paper evidence' : 'Phase 3 - Route discipline'}
          phaseTone={liveRouteSelected ? (promotionGateSummary?.tone === 'positive' ? 'positive' : 'warning') : 'info'}
          title={
            liveRouteSelected
              ? 'Keep first capital behind the paper gate until the desk can explain its fills.'
              : paperRouteSelected
                ? 'Paper routing is available. Use it to build execution evidence, not to bypass discipline.'
                : 'Desk routing is available. Keep the route stable and the setup narrow.'
          }
          description={
            liveRouteSelected
              ? 'This desk is meant to promote one small own-account setup at a time. Use the current gate and control state as a hard rollout boundary, not as advisory copy.'
              : paperRouteSelected
                ? 'Paper mode should stay fully usable. The next job is building resolved outcomes, drift samples, and clean order lifecycles before any live pilot.'
                : 'Desk mode should stay light and readable. Use it to review setups, check route quality, and avoid unnecessary visual noise.'
          }
          steps={buildWorkflowSteps(2)}
          cards={[
            {
              label: liveRouteSelected ? 'Paper gate' : paperRouteSelected ? 'Paper route' : 'Desk route',
              value: liveRouteSelected
                ? promotionGateSummary?.label || 'Paper gate review'
                : executionRouteSummary?.label || 'Desk route',
              detail: liveRouteSelected
                ? promotionGateSummary?.detail || 'Replay depth, fill drift, and resolved outcomes still decide whether first capital is allowed.'
                : executionRouteSummary?.detail || 'Routing is available and should stay predictable while the desk accumulates evidence.',
              tone: liveRouteSelected ? promotionGateSummary?.tone || 'warning' : executionRouteSummary?.tone || 'info',
            },
            {
              label: paperRouteSelected ? 'Evidence build' : 'Control posture',
              value: paperRouteSelected
                ? `${promotionGateSummary?.resolvedCount ?? 0} resolved`
                : capitalPreservationSummary?.label || 'Risk review',
              detail: paperRouteSelected
                ? `${promotionGateSummary?.winRateLabel || '--'} win rate | avg ${promotionGateSummary?.averageAbsSlippageLabel || '--'} drift | keep collecting clean fills.`
                : capitalPreservationSummary?.detail || 'Route controls, loss locks, and session posture still sit outside the strategy.',
              tone: capitalPreservationSummary?.tone || 'warning',
            },
            {
              label: liveRouteSelected ? 'Next promotion test' : 'Alpaca live',
              value: liveRouteSelected
                ? promotionGateSummary?.allowsPromotion ? 'Tiny live still applies' : 'Keep it in replay or paper'
                : liveBrokerDeskStatus?.value || 'Standby',
              detail: liveRouteSelected
                ? promotionGateSummary?.allowsPromotion
                  ? 'Even when the gate clears, first capital should stay intentionally small until live slippage and order-state behavior match the model.'
                  : 'Clear the paper sample, slippage drift, and order lifecycle mismatches before any new live routing.'
                : liveBrokerDeskStatus?.detail || 'Alpaca live should stay visible and inactive while paper mode builds the sample.',
              tone: liveRouteSelected ? (promotionGateSummary?.allowsPromotion ? 'positive' : 'warning') : liveBrokerDeskStatus?.tone || 'info',
            },
          ]}
        />
          </>
        ) : null}

        {showExtendedSidebarDetails && visibleDeskCandidateRows.length ? (
          <div className="chart-mini-section">
            <span className="chart-mini-section__title">Candidate queue</span>
              <div
                ref={deskCandidateNavigation.containerRef}
                className="candidate-queue__grid"
                onKeyDown={deskCandidateNavigation.onKeyDown}
              >
                {visibleDeskCandidateRows.map((candidate) => (
                  <Button
                    key={`${candidate.ticker}-${candidate.gateLabel}`}
                    type="button"
                    variant="ghost"
                    size="sm"
                    className={`candidate-queue__item candidate-queue__item--${candidate.gateTone}`}
                    onClick={() => focusTicker(candidate.ticker, form.interval, form.horizon)}
                  >
                  <div className="candidate-queue__meta">
                    <strong>{candidate.ticker}</strong>
                    <span className={`execution-state-badge execution-state-badge--${candidate.gateTone}`}>
                      {candidate.gateLabel}
                    </span>
                  </div>
                  <div className="ui-list-cell__badges">
                    <StatusBadge tone={candidate.rankingTier === 'promote' ? 'positive' : candidate.rankingTier === 'stand_down' ? 'negative' : 'warning'}>
                      {candidate.rankingLabel}
                    </StatusBadge>
                    <StatusBadge tone="neutral">{candidate.verdict}</StatusBadge>
                  </div>
                    <div className="candidate-queue__stack">
                      <span>{candidate.gateDetail}</span>
                      <span>{candidate.rankingSummary}</span>
                    <InlineMeta
                      as="span"
                      items={[
                        `${candidate.boardRank ? `Rank #${candidate.boardRank}` : 'Unranked'}`,
                        `Board ${formatNumber(candidate.score, 1)}`,
                        `Prob ${formatRatioPercent(candidate.probabilityUp, 1)}`,
                      ]}
                    />
                      <span>Last {formatPrice(candidate.livePrice)}</span>
                    </div>
                  </Button>
                ))}
              </div>
            <div className="chart-market-panel__footnote">
              {deskCandidateQueue.mode === 'promote'
                ? `These are the clearest controlled-liquid leaders from the desk pulse right now. ${promotionGateSummary.action}`
                : promotionGateSummary?.tone === 'positive' || promotionGateSummary?.tone === 'info'
                  ? 'No controlled-liquid promote leaders are active, so the queue falls back to the strongest review candidates.'
                  : liveRouteSelected
                    ? `Board leaders are staying in review while the ${String(promotionGateSummary?.label || 'paper gate').toLowerCase()} remains active.`
                    : 'Board leaders are staying in review until the route and fill-quality stack clean up enough to justify a paper cycle.'}
            </div>
          </div>
        ) : null}

        <div className="tv-watchlist-table">
          <div className="tv-watchlist-table__header">
            <span>Symbol</span>
            <span>Last</span>
            <span>Change</span>
            <span>Spread</span>
          </div>

          {sidebarRows.length ? (
            sidebarRows.map((row) => {
              const lastPrice = toNumber(row.live_price ?? row.close)
              const baseline = toNumber(row.close)
              const rowChangePct = percentageDelta(lastPrice, baseline)
              const active = String(row.ticker).toUpperCase() === String(form.ticker).toUpperCase()
              const rowSpread = resolveDisplaySpread(row.spread, row.bid_price, row.ask_price)
              const previewSeries = buildWatchlistPreviewSeries(row, row.history)

              return (
                <Button
                  key={`${row.ticker}-${row.contract_symbol || row.verdict || 'watch'}`}
                  type="button"
                  variant={active ? 'solid' : 'ghost'}
                  size="sm"
                  className={`tv-watchlist-table__row ${
                    active ? 'tv-watchlist-table__row--active' : ''
                  }`}
                  onClick={() => focusTickerInPlace(row.ticker, form.interval, form.horizon)}
                >
                  <div className="tv-watchlist-table__symbol">
                    <strong>
                        <SignalDot
                          className="tv-watchlist-table__dot"
                          accent={tickerAccent(row.ticker)}
                          glow={hexToRgba(tickerAccent(row.ticker), 0.35)}
                          size="sm"
                        />
                      {row.ticker || '--'}
                    </strong>
                    <div className="tv-watchlist-table__symbol-meta">
                      <span>{row.verdict || row.trade_decision || 'Watch'}</span>
                      <WatchlistSparkline
                        values={previewSeries}
                        accent={tickerAccent(row.ticker)}
                        active={active}
                      />
                    </div>
                  </div>
                  <span>{formatPrice(lastPrice)}</span>
                  <span
                    className={`tv-watchlist-table__change ${
                      toNumber(rowChangePct) > 0
                        ? 'tv-watchlist-table__change--up'
                        : toNumber(rowChangePct) < 0
                          ? 'tv-watchlist-table__change--down'
                          : ''
                    }`}
                  >
                    {formatSignedPercent(rowChangePct)}
                  </span>
                  <span className="tv-watchlist-table__spread">{formatMeaningfulPrice(rowSpread)}</span>
                </Button>
              )
            })
          ) : (
            <EmptyState
              title="No liquid-board pulse"
              description={tradingStyle === 'intraday'
                ? `Start here in ${intradayPresetProfile.startupSurface === '/compare' ? 'Compare' : intradayPresetProfile.startupSurface === '/trades' ? 'Trades' : 'Watchlist'} or Compare, then this side rail will fill in once a qualified ${intradayPresetProfile.shortLabel.toLowerCase()} basket reaches the desk.`
                : 'Start here in Watchlist or Compare, then this side rail will fill in once a qualified basket reaches the desk.'}
              actionLabel={tradingStyle === 'intraday'
                ? intradayPresetProfile.startupSurface === '/compare'
                  ? 'Open compare'
                  : intradayPresetProfile.startupSurface === '/trades'
                    ? 'Open trades'
                    : 'Open watchlist'
                : 'Open watchlist'}
              onAction={() => navigate(tradingStyle === 'intraday' ? intradayPresetProfile.startupSurface : '/watchlist')}
              secondaryActionLabel="Load SPY on desk"
              onSecondaryAction={() => void focusTicker('SPY', form.interval, form.horizon)}
            />
          )}
        </div>

        {strategySnapshot?.available ? (
          <div className="chart-mini-section">
            <span className="chart-mini-section__title">
              {strategySnapshot.strategy?.replaceAll('_', ' ') || 'Adaptive intraday momentum'}
            </span>
            <div className="chart-focus-list chart-focus-list--compact">
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>{strategySnapshot.decision || 'Waiting for checkpoint'}</strong>
                  <span>
                    {strategySnapshot.latest_checkpoint
                      ? `Latest checkpoint ${strategySnapshot.latest_checkpoint} ET`
                      : 'Waiting for the first checkpoint of the session.'}
                    {strategySnapshot.next_checkpoint
                      ? ` | Next ${strategySnapshot.next_checkpoint} ET`
                      : ' | Flatten into the close.'}
                  </span>
                </div>
              </div>
            </div>
            <div className="chart-market-panel__snapshot">
              <div className="chart-stage-summary__item">
                <span>Upper band</span>
                <strong>{formatOptionalPrice(strategySnapshot.upper_band)}</strong>
              </div>
              <div className="chart-stage-summary__item">
                <span>Lower band</span>
                <strong>{formatOptionalPrice(strategySnapshot.lower_band)}</strong>
              </div>
              <div className="chart-stage-summary__item">
                <span>Next check</span>
                <strong>
                  {strategySnapshot.next_checkpoint
                    ? `${strategySnapshot.next_checkpoint} ET`
                    : 'Close'}
                </strong>
              </div>
              <div className="chart-stage-summary__item">
                <span>Shares</span>
                <strong>{formatShares(strategySnapshot?.sizing?.suggested_shares)}</strong>
              </div>
            </div>
            <div className="chart-market-panel__snapshot">
              <div className="chart-stage-summary__item">
                <span>Session VWAP</span>
                <strong>{formatOptionalPrice(strategySnapshot.vwap)}</strong>
              </div>
              <div className="chart-stage-summary__item">
                <span>Trail stop</span>
                <strong>{formatOptionalPrice(strategySnapshot.active_stop)}</strong>
              </div>
              <div className="chart-stage-summary__item">
                <span>Bias</span>
                <strong>{String(strategySnapshot.bias || strategySnapshot.state || 'flat').toUpperCase()}</strong>
              </div>
              <div className="chart-stage-summary__item">
                <span>Data source</span>
                <strong>{strategySnapshot.data_source || 'unknown'}</strong>
              </div>
              <div className="chart-stage-summary__item">
                <span>RV 14d</span>
                <strong>{formatOptionalPercent(strategySnapshot?.sizing?.realized_vol_14d, 2)}</strong>
              </div>
              <div className="chart-stage-summary__item">
                <span>Live execution</span>
                <strong>{formatPrice(activeExecutionPrice)}</strong>
              </div>
            </div>
            <div className="chart-market-panel__snapshot">
              <div className="chart-stage-summary__item">
                <span>Entry midpoint</span>
                <strong>{formatPrice(entryMidpoint)}</strong>
              </div>
              <div className="chart-stage-summary__item">
                <span>Algo target</span>
                <strong>{formatPrice(optionPlan.expected_underlying_target)}</strong>
              </div>
              <div className="chart-stage-summary__item">
                <span>Cut loss</span>
                <strong>{formatPrice(optionPlan.invalidation_price)}</strong>
              </div>
              <div className="chart-stage-summary__item">
                <span>Risk / reward</span>
                <strong>{riskReward === null ? '--' : `${formatNumber(riskReward, 2)}R`}</strong>
              </div>
              <div className="chart-stage-summary__item">
                <span>Bid / ask</span>
                <strong>
                  {formatPrice(selectedQuote?.bid_price)} / {formatPrice(selectedQuote?.ask_price)}
                </strong>
              </div>
              <div className="chart-stage-summary__item">
                <span>Contract</span>
                <strong>{contract.contract_symbol || '--'}</strong>
              </div>
            </div>
            <div className="chart-focus-list chart-focus-list--compact">
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Entry plan</strong>
                  <span>{optionPlan.entry_signal || 'No entry guidance yet.'}</span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Exit plan</strong>
                  <span>{optionPlan.sell_signal || 'No exit guidance yet.'}</span>
                </div>
              </div>
            </div>
            {optionExecutionReviewPanel ? (
              <>
                <div className="chart-focus-list chart-focus-list--compact">
                  <div className="chart-focus-row chart-focus-row--compact">
                    <div className="chart-focus-row__main">
                      <strong>Option execution review</strong>
                      <span>{optionExecutionReviewPanel.detail}</span>
                    </div>
                    <span className={`execution-state-badge execution-state-badge--${optionExecutionReviewPanel.tone}`}>
                      {optionExecutionReviewPanel.label}
                    </span>
                  </div>
                </div>
                <div className="chart-market-panel__snapshot">
                  <div className="chart-stage-summary__item">
                    <span>Contract</span>
                    <strong>{optionExecutionReviewPanel.contractSymbol}</strong>
                  </div>
                  <div className="chart-stage-summary__item">
                    <span>Spread</span>
                    <strong>{formatPercent(optionExecutionReviewPanel.spreadPct, 1)}</strong>
                  </div>
                  <div className="chart-stage-summary__item">
                    <span>Quote age</span>
                    <strong>
                      {optionExecutionReviewPanel.quoteAgeSeconds === null
                        ? '--'
                        : `${Math.round(optionExecutionReviewPanel.quoteAgeSeconds)}s`}
                    </strong>
                  </div>
                  <div className="chart-stage-summary__item">
                    <span>Volume / OI</span>
                    <strong>
                      {formatCompact(optionExecutionReviewPanel.volume)} /{' '}
                      {formatCompact(optionExecutionReviewPanel.openInterest)}
                    </strong>
                  </div>
                  <div className="chart-stage-summary__item">
                    <span>Expected fill</span>
                    <strong>{formatPrice(optionExecutionReviewPanel.expectedFillPrice)}</strong>
                  </div>
                  <div className="chart-stage-summary__item">
                    <span>Actual fill</span>
                    <strong>{formatPrice(optionExecutionReviewPanel.actualFillPrice)}</strong>
                  </div>
                  <div className="chart-stage-summary__item">
                    <span>Slippage</span>
                    <strong>
                      {optionExecutionReviewPanel.fillSlippageBps === null
                        ? '--'
                        : `${formatNumber(optionExecutionReviewPanel.fillSlippageBps, 1)} bps`}
                    </strong>
                  </div>
                  <div className="chart-stage-summary__item">
                    <span>Broker sync</span>
                    <strong>{optionExecutionReviewPanel.brokerLabel}</strong>
                  </div>
                </div>
                <div className="chart-focus-list chart-focus-list--compact">
                  {optionExecutionReviewPanel.checks.map((check) => (
                    <div key={check.key} className="chart-focus-row chart-focus-row--compact">
                      <div className="chart-focus-row__main">
                        <strong>{check.label}</strong>
                        <span>{check.value}</span>
                      </div>
                      <span
                        className={`execution-state-badge execution-state-badge--${
                          check.status === 'pass' ? 'positive' : 'negative'
                        }`}
                      >
                        {check.status === 'pass' ? 'Clear' : 'Blocked'}
                      </span>
                    </div>
                  ))}
                </div>
              </>
            ) : null}
          </div>
        ) : null}

        {showExtendedSidebarDetails ? (
        <div className="chart-market-panel__snapshot tv-sidebar-performance-grid">
          <div className="chart-stage-summary__item">
            <span>{strategySnapshot?.available ? 'Upper band' : 'Setup'}</span>
            <strong>
              {strategySnapshot?.available
                ? formatOptionalPrice(strategySnapshot.upper_band)
                : formatNumber(report?.setup_score, 1)}
            </strong>
          </div>
          <div className="chart-stage-summary__item">
            <span>{strategySnapshot?.available ? 'Lower band' : 'Prob up'}</span>
            <strong>
              {strategySnapshot?.available
                ? formatOptionalPrice(strategySnapshot.lower_band)
                : formatPercent(toNumber(report?.probability_up) * 100, 1)}
            </strong>
          </div>
          <div className="chart-stage-summary__item">
            <span>{strategySnapshot?.available ? 'Next check' : 'Entry now'}</span>
            <strong>
              {strategySnapshot?.available
                ? strategySnapshot.next_checkpoint
                  ? `${strategySnapshot.next_checkpoint} ET`
                  : 'Close'
                : dashboard?.watchlist?.summary?.entry_now ?? 0}
            </strong>
          </div>
          <div className="chart-stage-summary__item">
            <span>{strategySnapshot?.available ? 'Shares' : 'Valid'}</span>
            <strong>
              {strategySnapshot?.available
                ? formatShares(strategySnapshot?.sizing?.suggested_shares)
                : dashboard?.watchlist?.summary?.valid_trades ?? 0}
            </strong>
          </div>
        </div>
        ) : null}

        {showExtendedSidebarDetails && !strategySnapshot?.available ? (
          <div className="chart-mini-section">
            <span className="chart-mini-section__title">Decision gate</span>
            <div className="chart-focus-list chart-focus-list--compact">
              <div className={`chart-focus-row chart-focus-row--compact ${decisionGateSummary.tone === 'positive' ? 'chart-focus-row--active' : ''}`}>
                <div className="chart-focus-row__main">
                  <strong>{decisionGateSummary.label}</strong>
                  <span>{decisionGateSummary.detail}</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className={`execution-state-badge execution-state-badge--${decisionGateSummary.tone}`}>
                    {decisionGateSummary.label}
                  </span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Current action</strong>
                  <span>How the desk should treat this setup right now.</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">{decisionGateSummary.action}</span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>{liveRouteSelected ? 'Paper gate' : paperRouteSelected ? 'Paper sample' : 'Route posture'}</strong>
                  <span>
                    {liveRouteSelected
                      ? 'Replay evidence and paper-vs-live execution drift before first capital is promoted.'
                      : paperRouteSelected
                        ? 'Paper mode uses resolved outcomes and drift to build the live sample, not to block routing.'
                        : 'Desk route posture should stay stable and readable before you move into broker execution.'}
                  </span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className={`execution-state-badge execution-state-badge--${promotionGateSummary.tone}`}>
                    {liveRouteSelected ? promotionGateSummary.label : paperRouteSelected ? `${promotionGateSummary?.resolvedCount ?? 0} resolved` : executionRouteSummary?.label || 'Desk route'}
                  </span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>{liveRouteSelected ? 'Gate replay' : paperRouteSelected ? 'Paper replay' : 'Route review'}</strong>
                  <span>
                    {liveRouteSelected
                      ? promotionGateSummary.detail
                      : paperRouteSelected
                        ? `${promotionGateSummary?.winRateLabel || '--'} win rate | avg ${promotionGateSummary?.averageAbsSlippageLabel || '--'} drift.`
                        : executionRouteSummary?.detail}
                  </span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">
                    {liveRouteSelected
                      ? promotionGateSummary.action
                      : paperRouteSelected
                        ? 'Keep collecting'
                        : 'Stay stable'}
                  </span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>{liveRouteSelected ? 'Gate policy' : paperRouteSelected ? 'Paper policy' : 'Route policy'}</strong>
                  <span>
                    {liveRouteSelected
                      ? 'The local desk thresholds that define when first capital is allowed to leave review.'
                      : paperRouteSelected
                        ? 'Paper mode should stay easy to route while still keeping enough evidence to evaluate the live path later.'
                        : 'Desk mode should stay light enough to review setups without dragging the live rollout logic into every decision.'}
                  </span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">
                    {liveRouteSelected ? promotionGateSummary.policySummary : paperRouteSelected ? 'Paper unlocked' : 'Desk unlocked'}
                  </span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Gate basis</strong>
                  <span>What is clearing together, or what is still blocking promotion.</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">{decisionGateSummary.basis}</span>
                </div>
              </div>
            </div>
          </div>
        ) : null}

        {showExtendedSidebarDetails && !strategySnapshot?.available ? (
          <div className="chart-mini-section">
            <span className="chart-mini-section__title">Execution quality</span>
            <div className="chart-focus-list chart-focus-list--compact">
              <div className={`chart-focus-row chart-focus-row--compact ${executionQualitySummary.tone === 'positive' ? 'chart-focus-row--active' : ''}`}>
                <div className="chart-focus-row__main">
                  <strong>{executionQualitySummary.label}</strong>
                  <span>{executionQualitySummary.detail}</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className={`execution-state-badge execution-state-badge--${executionQualitySummary.tone}`}>
                    {executionQualitySummary.label}
                  </span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Spread and liquidity</strong>
                  <span>Current spread drag and visible participation behind the setup.</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">
                    {executionQualitySummary.spreadLabel}
                    <small className="chart-focus-row__meta">{executionQualitySummary.participationLabel}</small>
                  </span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Route sensitivity</strong>
                  <span>How much price control should matter before you send.</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">{executionQualitySummary.routeLabel}</span>
                </div>
              </div>
            </div>
          </div>
        ) : null}

        {showExtendedSidebarDetails ? (
          <>
        {!strategySnapshot?.available ? (
          <div className="chart-mini-section">
            <span className="chart-mini-section__title">Forecast framing</span>
            <div className="chart-focus-list chart-focus-list--compact">
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>{forecastFraming?.label || 'Forecast target pending'}</strong>
                  <span>
                    {forecastFraming?.use_label ||
                      'This desk read needs a clear target, horizon, and benchmark before it should be treated as actionable.'}
                  </span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">{forecastFraming?.short_label || 'Pending'}</span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Horizon and freshness</strong>
                  <span>The prediction window and how current the supporting tape is right now.</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">
                    {forecastFraming?.horizon_label || formatForecastHorizon(form.interval, form.horizon)}
                    <small className="chart-focus-row__meta">
                      {formatLabel(chartFreshness?.status || 'unknown')}
                    </small>
                  </span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Benchmark and interpretation</strong>
                  <span>
                    {forecastFraming?.benchmark_detail ||
                      'The desk should define what this forecast must beat before it becomes a conviction call.'}
                  </span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">
                    {forecastFraming?.benchmark_label || benchmarkSummary.label}
                    <small className="chart-focus-row__meta">
                      {forecastFraming?.trust_label || 'Treat this as review-first while framing is thin.'}
                    </small>
                  </span>
                </div>
              </div>
            </div>
          </div>
        ) : null}

        {!strategySnapshot?.available ? (
          <div className="chart-mini-section">
            <span className="chart-mini-section__title">Forecast trust</span>
            <div className="chart-focus-list chart-focus-list--compact">
              <div className={`chart-focus-row chart-focus-row--compact ${forecastTrustSummary.tone === 'positive' ? 'chart-focus-row--active' : ''}`}>
                <div className="chart-focus-row__main">
                  <strong>{forecastTrustSummary.label}</strong>
                  <span>{forecastTrustSummary.detail}</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className={`execution-state-badge execution-state-badge--${forecastTrustSummary.tone}`}>
                    {forecastTrustSummary.label}
                  </span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Freshness and confidence</strong>
                  <span>Live data freshness and current forecast confidence.</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">
                    {formatLabel(chartFreshness?.status || 'unknown')} / {formatRatioPercent(toNumber(forecastSummary?.confidence_score), 1)}
                  </span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Regime and resolved sample</strong>
                  <span>Support from the active regime and the live calibration history.</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">
                    {formatRatioPercent(regimeStrengthScore, 1)} / {formatCompact(journalResolvedCount)}
                  </span>
                </div>
              </div>
            </div>
          </div>
        ) : null}

        {!strategySnapshot?.available ? (
          <div className="chart-mini-section">
            <span className="chart-mini-section__title">Target quality</span>
            <div className="chart-focus-list chart-focus-list--compact">
              <div className={`chart-focus-row chart-focus-row--compact ${targetQualitySummary.tone === 'positive' ? 'chart-focus-row--active' : ''}`}>
                <div className="chart-focus-row__main">
                  <strong>{targetQualitySummary.label}</strong>
                  <span>{targetQualitySummary.detail}</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className={`execution-state-badge execution-state-badge--${targetQualitySummary.tone}`}>
                    {targetQualitySummary.label}
                  </span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Resolved sample and scope</strong>
                  <span>How much live calibration history is actually behind this forecast.</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">
                    {formatCompact(journalResolvedCount)} / {formatLabel(calibrationScope || 'global')}
                  </span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Observed hit rate and error</strong>
                  <span>Resolved outcomes versus the model's historical probability and miss size.</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">
                    {formatRatioPercent(journalHitRate, 1)} / {formatRatioPercent(journalAverageError, 2)}
                    <small className="chart-focus-row__meta">
                      Model avg {formatRatioPercent(journalAverageProbabilityUp, 1)}
                    </small>
                  </span>
                </div>
              </div>
            </div>
          </div>
        ) : null}

        {!strategySnapshot?.available ? (
          <div className="chart-mini-section">
            <span className="chart-mini-section__title">Model drift</span>
            <div className="chart-focus-list chart-focus-list--compact">
              <div className={`chart-focus-row chart-focus-row--compact ${modelDriftSummary.tone === 'positive' ? 'chart-focus-row--active' : ''}`}>
                <div className="chart-focus-row__main">
                  <strong>{modelDriftSummary.label}</strong>
                  <span>{modelDriftSummary.detail}</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className={`execution-state-badge execution-state-badge--${modelDriftSummary.tone}`}>
                    {modelDriftSummary.label}
                  </span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Current action</strong>
                  <span>How the desk should treat the signal if support keeps degrading.</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">{modelDriftSummary.action}</span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Support vs model average</strong>
                  <span>Resolved hit rate against the model's average probability, plus live freshness.</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">
                    {formatSignedPercent(journalEdgeDelta, 1)} / {formatLabel(chartFreshness?.status || 'unknown')}
                  </span>
                </div>
              </div>
            </div>
          </div>
        ) : null}

        {!strategySnapshot?.available ? (
          <div className="chart-mini-section">
            <span className="chart-mini-section__title">Benchmark</span>
            <div className="chart-focus-list chart-focus-list--compact">
              <div className={`chart-focus-row chart-focus-row--compact ${benchmarkSummary.tone === 'positive' ? 'chart-focus-row--active' : ''}`}>
                <div className="chart-focus-row__main">
                  <strong>{benchmarkSummary.label}</strong>
                  <span>{benchmarkSummary.detail}</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className={`execution-state-badge execution-state-badge--${benchmarkSummary.tone}`}>
                    {benchmarkSummary.label}
                  </span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Current comparison</strong>
                  <span>What the live forecast is being measured against right now.</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">{benchmarkSummary.comparison}</span>
                </div>
              </div>
            </div>
          </div>
        ) : null}

        {!strategySnapshot?.available ? (
          <div className="chart-mini-section">
            <span className="chart-mini-section__title">Regime memory</span>
            <div className="chart-focus-list chart-focus-list--compact">
              <div className={`chart-focus-row chart-focus-row--compact ${memorySummary.tone === 'positive' ? 'chart-focus-row--active' : ''}`}>
                <div className="chart-focus-row__main">
                  <strong>{memorySummary.label}</strong>
                  <span>{memorySummary.detail}</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className={`execution-state-badge execution-state-badge--${memorySummary.tone}`}>
                    {memorySummary.label}
                  </span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Best regime / driver</strong>
                  <span>Where the model has historically held up best for this symbol and interval.</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">
                    {formatLabel(bestRegime?.market_regime || 'unknown')} / {formatLabel(bestDriver?.driver || 'unknown')}
                  </span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Weakest regime / driver</strong>
                  <span>Where the edge has been most likely to break down.</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">
                    {formatLabel(weakestRegime?.market_regime || 'unknown')} / {formatLabel(weakestDriver?.driver || 'unknown')}
                  </span>
                </div>
              </div>
            </div>
          </div>
        ) : null}

        {!strategySnapshot?.available ? (
          <div className="chart-mini-section">
            <span className="chart-mini-section__title">Session memory</span>
            <div className="chart-focus-list chart-focus-list--compact">
              <div className={`chart-focus-row chart-focus-row--compact ${sessionMemorySummary.tone === 'positive' ? 'chart-focus-row--active' : ''}`}>
                <div className="chart-focus-row__main">
                  <strong>{sessionMemorySummary.label}</strong>
                  <span>{sessionMemorySummary.detail}</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className={`execution-state-badge execution-state-badge--${sessionMemorySummary.tone}`}>
                    {sessionMemorySummary.label}
                  </span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Best session</strong>
                  <span>Where this setup has historically resolved best.</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">{formatLabel(bestSession?.session_label || 'unknown')}</span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Weakest session</strong>
                  <span>Where this setup has historically been least reliable.</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">{formatLabel(weakestSession?.session_label || 'unknown')}</span>
                </div>
              </div>
            </div>
          </div>
        ) : null}

        {!strategySnapshot?.available ? (
          <div className="chart-mini-section">
            <span className="chart-mini-section__title">Event memory</span>
            <div className="chart-focus-list chart-focus-list--compact">
              <div className={`chart-focus-row chart-focus-row--compact ${eventMemorySummary.tone === 'positive' ? 'chart-focus-row--active' : ''}`}>
                <div className="chart-focus-row__main">
                  <strong>{eventMemorySummary.label}</strong>
                  <span>{eventMemorySummary.detail}</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className={`execution-state-badge execution-state-badge--${eventMemorySummary.tone}`}>
                    {eventMemorySummary.label}
                  </span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Best event window</strong>
                  <span>Where this setup has historically handled event context best.</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">{formatLabel(bestEventWindow?.event_window_label || 'unknown')}</span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Weakest event window</strong>
                  <span>Where this setup has historically been least reliable.</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">{formatLabel(weakestEventWindow?.event_window_label || 'unknown')}</span>
                </div>
              </div>
            </div>
          </div>
        ) : null}

        {!strategySnapshot?.available ? (
          <div className="chart-market-panel__snapshot tv-sidebar-performance-grid">
            <div className="chart-stage-summary__item">
              <span>Calibration</span>
              <strong>{calibrationShiftLabel}</strong>
            </div>
            <div className="chart-stage-summary__item">
              <span>Regime</span>
              <strong>{formatLabel(journalMarketRegime)}</strong>
            </div>
            <div className="chart-stage-summary__item">
              <span>Scope</span>
              <strong>{formatLabel(calibrationScope || 'global')}</strong>
            </div>
            <div className="chart-stage-summary__item">
              <span>Resolved</span>
              <strong>{formatCompact(journalResolvedCount)}</strong>
            </div>
            <div className="chart-stage-summary__item">
              <span>Hit rate</span>
              <strong>{formatRatioPercent(journalHitRate, 1)}</strong>
            </div>
            <div className="chart-stage-summary__item">
              <span>Avg error</span>
              <strong>{formatRatioPercent(journalAverageError, 2)}</strong>
            </div>
            <div className="chart-stage-summary__item">
              <span>Model avg</span>
              <strong>{formatRatioPercent(journalAverageProbabilityUp, 1)}</strong>
            </div>
            <div className="chart-stage-summary__item">
              <span>Edge</span>
              <strong>{formatSignedPercent(journalEdgeDelta, 1)}</strong>
            </div>
            <div className="chart-stage-summary__item">
              <span>Tech prob</span>
              <strong>{formatRatioPercent(technicalProbabilityUp, 1)}</strong>
            </div>
            <div className="chart-stage-summary__item">
              <span>Live prob</span>
              <strong>{formatRatioPercent(adjustedProbabilityUp, 1)}</strong>
            </div>
            <div className="chart-stage-summary__item">
              <span>Regime strength</span>
              <strong>{formatRatioPercent(regimeStrengthScore, 1)}</strong>
            </div>
            <div className="chart-stage-summary__item">
              <span>Risk budget</span>
              <strong>{formatRatioPercent(riskBudgetMultiplier, 0)}</strong>
            </div>
          </div>
        ) : null}

        {!strategySnapshot?.available ? (
          <div className="chart-market-panel__footnote">{calibrationSupportLine}</div>
        ) : null}

        {!strategySnapshot?.available && contributionBreakdown ? (
          <div className="chart-mini-section">
            <span className="chart-mini-section__title">Confidence drivers</span>
            <div className="chart-focus-list chart-focus-list--compact">
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Technical base</strong>
                  <span>Core model confidence contribution</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">{formatRatioPercent(technicalConfidenceComponent, 1)}</span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>News effect</strong>
                  <span>Headline sentiment impact on confidence / bias</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">
                    {formatRatioPercent(newsConfidenceComponent, 1)} / {formatSignedPercent((newsProbabilityContribution ?? 0) * 100, 1)}
                  </span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Journal effect</strong>
                  <span>Live calibration shift from resolved forecasts</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">{formatSignedPercent((journalProbabilityContribution ?? 0) * 100, 1)}</span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Regime effect</strong>
                  <span>Active regime strength applied to confidence</span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">
                    {formatRatioPercent(regimeStrengthScore, 1)} / {formatSignedPercent((regimeConfidenceComponent ?? 0) * 100, 1)}
                    <small className="chart-focus-row__meta">
                      {eventConfidencePenalty ? `event -${formatPercent(eventConfidencePenalty * 100, 1)}` : 'no event penalty'}
                    </small>
                  </span>
                </div>
              </div>
            </div>
          </div>
        ) : null}

        {!strategySnapshot?.available && driverAttribution.length ? (
          <div className="chart-mini-section">
            <span className="chart-mini-section__title">Driver scorecard</span>
            <div className="chart-focus-list chart-focus-list--compact">
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Best driver</strong>
                  <InlineMeta
                    as="span"
                    items={[formatLabel(bestDriver?.driver), `${formatRatioPercent(bestDriver?.helpful_rate, 1)} helpful`]}
                  />
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">
                    {formatSignedPercent((toNumber(bestDriver?.average_signed_impact) ?? 0) * 100, 1)}
                    <small className="chart-focus-row__meta">
                      {formatCompact(bestDriver?.resolved_count)} resolved
                    </small>
                  </span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Weakest driver</strong>
                  <InlineMeta
                    as="span"
                    items={[formatLabel(weakestDriver?.driver), `${formatRatioPercent(weakestDriver?.helpful_rate, 1)} helpful`]}
                  />
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">
                    {formatSignedPercent((toNumber(weakestDriver?.average_signed_impact) ?? 0) * 100, 1)}
                    <small className="chart-focus-row__meta">
                      {formatCompact(weakestDriver?.resolved_count)} resolved
                    </small>
                  </span>
                </div>
              </div>
              {driverAttribution.slice(0, 5).map((item) => (
                <div key={item.driver || 'driver'} className="chart-focus-row chart-focus-row--compact">
                  <div className="chart-focus-row__main">
                    <strong>{formatLabel(item.driver)}</strong>
                    <InlineMeta
                      as="span"
                      items={[
                        `${formatRatioPercent(item.helpful_rate, 1)} helpful`,
                        `Avg ${formatSignedPercent((toNumber(item.average_contribution) ?? 0) * 100, 1)}`,
                      ]}
                    />
                  </div>
                  <div className="chart-focus-row__aside">
                    <span className="chart-focus-row__price">
                      {formatSignedPercent((toNumber(item.average_signed_impact) ?? 0) * 100, 1)}
                      <small className="chart-focus-row__meta">
                        {formatCompact(item.resolved_count)} resolved
                      </small>
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {!strategySnapshot?.available && regimeBreakdown.length ? (
          <div className="chart-mini-section">
            <span className="chart-mini-section__title">Regime scorecard</span>
            <div className="chart-focus-list chart-focus-list--compact">
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Best regime</strong>
                  <span>
                    {formatInlineMeta([
                      formatLabel(bestRegime?.market_regime),
                      `${formatRatioPercent(bestRegime?.empirical_hit_rate, 1)} hit`,
                    ])}
                  </span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">
                    {formatSignedPercent((toNumber(bestRegime?.edge) ?? 0) * 100, 1)}
                    <small className="chart-focus-row__meta">
                      {formatCompact(bestRegime?.resolved_count)} resolved
                    </small>
                  </span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Weakest regime</strong>
                  <span>
                    {formatInlineMeta([
                      formatLabel(weakestRegime?.market_regime),
                      `${formatRatioPercent(weakestRegime?.empirical_hit_rate, 1)} hit`,
                    ])}
                  </span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">
                    {formatSignedPercent((toNumber(weakestRegime?.edge) ?? 0) * 100, 1)}
                    <small className="chart-focus-row__meta">
                      {formatCompact(weakestRegime?.resolved_count)} resolved
                    </small>
                  </span>
                </div>
              </div>
              {regimeBreakdown.slice(0, 4).map((item) => (
                <div key={item.market_regime || 'regime'} className="chart-focus-row chart-focus-row--compact">
                  <div className="chart-focus-row__main">
                    <strong>{formatLabel(item.market_regime)}</strong>
                    <InlineMeta
                      as="span"
                      items={[
                        `${formatRatioPercent(item.empirical_hit_rate, 1)} hit`,
                        `Err ${formatRatioPercent(item.average_error, 2)}`,
                      ]}
                    />
                  </div>
                  <div className="chart-focus-row__aside">
                    <span className="chart-focus-row__price">
                      {formatSignedPercent((toNumber(item.edge) ?? 0) * 100, 1)}
                      <small className="chart-focus-row__meta">
                        {formatCompact(item.resolved_count)} resolved
                      </small>
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {!strategySnapshot?.available && sessionBreakdown.length ? (
          <div className="chart-mini-section">
            <span className="chart-mini-section__title">Session scorecard</span>
            <div className="chart-focus-list chart-focus-list--compact">
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Best session</strong>
                  <span>
                    {formatLabel(bestSession?.session_label)} | {formatRatioPercent(bestSession?.empirical_hit_rate, 1)} hit
                  </span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">
                    {formatSignedPercent((toNumber(bestSession?.edge) ?? 0) * 100, 1)}
                    <small className="chart-focus-row__meta">
                      {formatCompact(bestSession?.resolved_count)} resolved
                    </small>
                  </span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Weakest session</strong>
                  <span>
                    {formatLabel(weakestSession?.session_label)} | {formatRatioPercent(weakestSession?.empirical_hit_rate, 1)} hit
                  </span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">
                    {formatSignedPercent((toNumber(weakestSession?.edge) ?? 0) * 100, 1)}
                    <small className="chart-focus-row__meta">
                      {formatCompact(weakestSession?.resolved_count)} resolved
                    </small>
                  </span>
                </div>
              </div>
              {sessionBreakdown.slice(0, 4).map((item) => (
                <div key={item.session_label || 'session'} className="chart-focus-row chart-focus-row--compact">
                  <div className="chart-focus-row__main">
                    <strong>{formatLabel(item.session_label)}</strong>
                    <span>
                      {formatRatioPercent(item.empirical_hit_rate, 1)} hit | err {formatRatioPercent(item.average_error, 2)}
                    </span>
                  </div>
                  <div className="chart-focus-row__aside">
                    <span className="chart-focus-row__price">
                      {formatSignedPercent((toNumber(item.edge) ?? 0) * 100, 1)}
                      <small className="chart-focus-row__meta">
                        {formatCompact(item.resolved_count)} resolved
                      </small>
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {!strategySnapshot?.available && eventBreakdown.length ? (
          <div className="chart-mini-section">
            <span className="chart-mini-section__title">Event scorecard</span>
            <div className="chart-focus-list chart-focus-list--compact">
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Best event window</strong>
                  <span>
                    {formatLabel(bestEventWindow?.event_window_label)} | {formatRatioPercent(bestEventWindow?.empirical_hit_rate, 1)} hit
                  </span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">
                    {formatSignedPercent((toNumber(bestEventWindow?.edge) ?? 0) * 100, 1)}
                    <small className="chart-focus-row__meta">
                      {formatCompact(bestEventWindow?.resolved_count)} resolved
                    </small>
                  </span>
                </div>
              </div>
              <div className="chart-focus-row chart-focus-row--compact">
                <div className="chart-focus-row__main">
                  <strong>Weakest event window</strong>
                  <span>
                    {formatLabel(weakestEventWindow?.event_window_label)} | {formatRatioPercent(weakestEventWindow?.empirical_hit_rate, 1)} hit
                  </span>
                </div>
                <div className="chart-focus-row__aside">
                  <span className="chart-focus-row__price">
                    {formatSignedPercent((toNumber(weakestEventWindow?.edge) ?? 0) * 100, 1)}
                    <small className="chart-focus-row__meta">
                      {formatCompact(weakestEventWindow?.resolved_count)} resolved
                    </small>
                  </span>
                </div>
              </div>
              {eventBreakdown.slice(0, 4).map((item) => (
                <div key={item.event_window_label || 'event-window'} className="chart-focus-row chart-focus-row--compact">
                  <div className="chart-focus-row__main">
                    <strong>{formatLabel(item.event_window_label)}</strong>
                    <span>
                      {formatRatioPercent(item.empirical_hit_rate, 1)} hit | err {formatRatioPercent(item.average_error, 2)}
                    </span>
                  </div>
                  <div className="chart-focus-row__aside">
                    <span className="chart-focus-row__price">
                      {formatSignedPercent((toNumber(item.edge) ?? 0) * 100, 1)}
                      <small className="chart-focus-row__meta">
                        {formatCompact(item.resolved_count)} resolved
                      </small>
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}
          </>
        ) : null}

        {monitoredOrderRows.length ? (
          <div className="chart-mini-section">
            <span className="chart-mini-section__title">Open-trade monitor</span>
            <div className="chart-focus-list chart-focus-list--compact">
              {monitoredOrderRows.map((row, index) => (
                <Button
                  key={`${row.ticker || 'row'}-${index}`}
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="chart-focus-row chart-focus-row--compact"
                  style={{
                    '--ticker-accent': tickerAccent(row.ticker),
                    '--ticker-accent-soft': hexToRgba(tickerAccent(row.ticker), 0.18),
                  }}
                  onClick={() =>
                    row.ticker && focusTicker(row.ticker, form.interval, form.horizon)
                  }
                >
                  <div className="chart-focus-row__main">
                    <strong>{row.ticker || '--'}</strong>
                    <span>
                      {row.monitor_action || row.trade_decision || 'Monitor'} | Open P&L{' '}
                      {formatSignedCurrency(row.unrealized_pnl)}
                    </span>
                  </div>
                  <div className="chart-focus-row__aside">
                    <span
                      className={`execution-state-badge execution-state-badge--${row.orderState.tone}`}
                    >
                      {row.orderState.label}
                    </span>
                    <span className="chart-focus-row__price">
                    {formatPrice(
                      row.current_underlying ??
                        row.current_underlying_price ??
                        row.live_price_at_open ??
                        row.entry_underlying_price,
                    )}
                    <InlineMeta
                      as="small"
                      className="chart-focus-row__meta"
                      items={[formatOrderTypeLabel(row.order_type), formatTimeInForceLabel(row.time_in_force)]}
                    />
                    </span>
                  </div>
                </Button>
              ))}
            </div>
          </div>
        ) : null}
      </>
    )
  }

  if (showInitialLoader) {
    return (
      <LoadingBlock
        label="Preparing trading desk"
        detail="Loading live setup, route state, and chart context so the desk opens with the current ticker story."
      />
    )
  }

  return (
    <>
      <section
        className="chart-fullscreen-page"
        style={{
          '--ticker-accent': activeTickerAccent,
          '--ticker-accent-soft': hexToRgba(activeTickerAccent, 0.22),
        }}
      >
        <div className={`chart-fullscreen ${marketPanelOpen ? 'chart-fullscreen--market-open' : ''}`}>
          <div className="chart-fullscreen__overlay chart-fullscreen__overlay--top">
            <div className="chart-headbar">
              <form className="chart-headbar__controls" onSubmit={handleAnalyze}>
                <TickerInput
                  id="ticker-suggestions"
                  wrapperClassName="chart-headbar__field chart-headbar__field--bare"
                  className="chart-headbar__input"
                  error={formErrors.ticker}
                  ariaLabel="Desk ticker"
                  value={form.ticker}
                  onChange={(value) => {
                    setForm((state) => ({
                      ...state,
                      ticker: value,
                    }))
                    setFormErrors((current) => omitKeys(current, ['ticker']))
                    setDeskActionIssue(null)
                  }}
                  placeholder="Ticker"
                />
                <SelectField
                  className="chart-headbar__field chart-headbar__field--bare"
                  inputClassName="chart-headbar__input"
                  value={form.interval}
                  onChange={(event) =>
                    setForm((state) => ({ ...state, interval: event.target.value }))
                  }
                >
                  {orderedIntervals.map((interval) => (
                    <option key={interval} value={interval}>
                      {interval}
                    </option>
                  ))}
                </SelectField>

                <TextField
                  className="chart-headbar__field chart-headbar__field--bare"
                  inputClassName="chart-headbar__input"
                  ariaLabel="Desk horizon bars"
                  error={formErrors.horizon}
                  type="number"
                  min="1"
                  max="50"
                  value={form.horizon}
                  onChange={(event) => {
                    setForm((state) => ({
                      ...state,
                      horizon: Number(event.target.value),
                    }))
                    setFormErrors((current) => omitKeys(current, ['horizon']))
                    setDeskActionIssue(null)
                  }}
                  placeholder="Bars"
                />

                <Button type="submit" variant="solid" disabled={analysisLoading}>
                  {analysisLoading ? 'Loading...' : 'Load'}
                </Button>
              </form>

                <div className="chart-headbar__actions">
                <SegmentedControl
                  value={form.interval}
                  options={quickIntervals.map((interval) => ({ key: interval, label: interval }))}
                  onChange={handleQuickInterval}
                  ariaLabel="Quick interval selection"
                  className="chart-headbar__intervals"
                  size="sm"
                />

                <ToggleField
                  label="Auto-refresh"
                  className="live-toggle"
                  checked={autoRefresh}
                  onChange={(event) => setAutoRefresh(event.target.checked)}
                />

                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                          className="desk-action"
                  onClick={handleRefreshWorkspace}
                  disabled={analysisLoading}
                >
                  {analysisLoading ? 'Refreshing...' : 'Refresh'}
                </Button>

                        <Button type="button" variant="subtle" size="sm" className="desk-action" onClick={handleSaveWorkspace} disabled={!canSaveDeskLayout}>
                  Save layout
                </Button>

                {hasLevelOverrides ? (
                        <Button type="button" variant="subtle" size="sm" className="desk-action" onClick={resetLevels}>
                    Reset levels
                  </Button>
                ) : null}
              </div>
            </div>
            {brokerAccountConnected ? (
              <div className="chart-headbar chart-headbar--account">
                {brokerAccountCards.map((item) => (
                  <div key={item.label} className="chart-console__metric chart-console__metric--money">
                    <span>{item.label}</span>
                    <strong>{item.value}</strong>
                  </div>
                ))}
              </div>
            ) : null}
            <div className="chart-headbar-group chart-headbar-group--broker-router">
              <div className="chart-headbar-group__title">Alpaca paper route</div>
              <div className="chart-headbar chart-headbar--broker-router">
                <div className="chart-console__metric chart-console__metric--router chart-console__metric--router-status">
                  <span>Router health</span>
                  <strong>
                    <StatusBadge tone={internalRouterTone}>
                      {formatLabel(internalRouterStatus, 'Loading')}
                    </StatusBadge>
                  </strong>
                  <div className="chart-console__detail">
                    {String(internalRouterHealth.detail || 'Alpaca paper route is loading.')
                      .replace(new RegExp(`internal ${'paper execution'} router`, 'ig'), 'Alpaca paper route')
                      .replace(new RegExp(`${'paper execution'} router`, 'ig'), 'Alpaca paper route')}
                  </div>
                  <div className="chart-console__meta">
                    {internalBrokerRouter?.paper_only === false
                      ? 'Alpaca live route selected'
                      : 'Paper-only | Live controls off'}
                  </div>
                </div>
                {internalRouterBalanceCards.map((item) => (
                  <div key={item.key} className="chart-console__metric chart-console__metric--router">
                    <span>{item.label}</span>
                    <strong>{item.value}</strong>
                    <div className="chart-console__detail">{item.detail}</div>
                  </div>
                ))}
              </div>
              <div className="chart-headbar chart-headbar--broker-router chart-headbar--broker-router-activity">
                {internalRouterActivityCards.map((item) => (
                  <div key={item.key} className="chart-console__metric chart-console__metric--router">
                    <span>{item.label}</span>
                    <strong>{item.value}</strong>
                    <div className="chart-console__detail">{item.detail}</div>
                  </div>
                ))}
              </div>
            </div>
            <div className="chart-headbar-group chart-headbar-group--options">
              <div className="chart-headbar-group__title">Options on desk</div>
              <div className="chart-headbar chart-headbar--options">
                {deskOptionCards.map((item) => (
                  <div key={item.key} className="chart-console__metric chart-console__metric--option">
                    <span>{item.label}</span>
                    <strong>{item.value}</strong>
                    {item.detail ? <div className="chart-console__detail">{item.detail}</div> : null}
                    {item.meta ? <div className="chart-console__meta">{item.meta}</div> : null}
                  </div>
                ))}
              </div>
              <div className="chart-headbar chart-headbar--options">
                {deskOptionActivityCards.map((item) => (
                  <div key={item.key} className="chart-console__metric chart-console__metric--option">
                    <span>{item.label}</span>
                    <strong>{item.value}</strong>
                    {item.detail ? <div className="chart-console__detail">{item.detail}</div> : null}
                    {item.meta ? <div className="chart-console__meta">{item.meta}</div> : null}
                  </div>
                ))}
              </div>
            </div>
            {deskActionIssue ? (
              <FeedbackState
                compact
                tone={deskActionIssue.tone}
                title={deskActionIssue.title}
                description={deskActionIssue.description}
              />
            ) : null}

            <FeedbackState
              compact
              tone={deskMarketModelState.tone}
              title={deskMarketModelState.title}
              description={deskMarketModelState.description}
            />

            {tradingStyle === 'intraday' ? (
              <FeedbackState
                compact
                tone={intradayExecutionPlan.tone}
                title={intradayExecutionPlan.title}
                description={intradayExecutionPlan.description}
              />
            ) : null}

            {reviewOnlyMode ? (
              <div className="chart-review-banner chart-review-banner--negative">
                <div className="chart-review-banner__copy">
                  <strong>Review-only mode is active</strong>
                  <p>{capitalPreservationSummary.detail}</p>
                </div>
                <div className="chart-review-banner__actions">
                  {capitalPreservationSummary.reviewOnlyResetLabel ? (
                    <Chip tone="warning" size="sm">
                      Reset {capitalPreservationSummary.reviewOnlyResetLabel}
                    </Chip>
                  ) : null}
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="desk-action"
                    onClick={() => navigate(`/trades${location.search || ''}`)}
                  >
                    Open trades
                  </Button>
                </div>
              </div>
            ) : null}

            {workflowArrivalNotice ? (
              <div className="chart-review-banner chart-review-banner--info">
                <div className="chart-review-banner__copy">
                  <strong>{workflowArrivalNotice.title}</strong>
                  <p>{workflowArrivalNotice.detail}</p>
                </div>
                <div className="chart-review-banner__actions">
                  {workflowArrivalNotice.returnUrl ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="desk-action"
                      onClick={() => navigate(workflowArrivalNotice.returnUrl)}
                    >
                      {workflowArrivalNotice.returnLabel}
                    </Button>
                  ) : null}
                  <Button
                    type="button"
                    variant="subtle"
                    size="sm"
                    className="desk-action"
                    onClick={() => setWorkflowArrivalNotice(null)}
                  >
                    Dismiss
                  </Button>
                </div>
              </div>
            ) : null}

            <div className="chart-headbar chart-headbar--secondary">
              <div className="chart-symbol-header">
                <div
                  className="chart-symbol-chip"
                  style={{
                    '--ticker-accent': activeTickerAccent,
                    '--ticker-accent-soft': `${activeTickerAccent}26`,
                  }}
                >
                  <SignalDot className="chart-symbol-chip__dot" accent={activeTickerAccent} glow={`${activeTickerAccent}4d`} size="md" />
                  <strong>{String(form.ticker || '').toUpperCase()}</strong>
                  <small>{form.interval}</small>
                </div>

                <div className="chart-symbol-header__copy">
                  <strong>{formatPrice(activeExecutionPrice)}</strong>
                  <span>
                    {activePriceDelta === null
                      ? '--'
                      : `${formatSignedCurrency(activePriceDelta)} (${formatSignedPercent(activePriceDeltaPct)})`}
                  </span>
                </div>

                <div className="chart-symbol-header__meta">
                  {symbolMetaItems.map((item) => (
                    <div key={item.label} className="chart-symbol-meta">
                      <span>{item.label}</span>
                      <strong>{item.value}</strong>
                    </div>
                  ))}
                </div>
              </div>

              <div className="chart-headbar__pills">
                <StatusBadge value={report?.verdict || 'Watching'} />
                <StatusBadge value={report?.trade_decision || 'No trade'} />
                {strategySnapshot?.available ? (
                  <StatusBadge
                    value={
                      strategySnapshot.latest_action ||
                      String(strategySnapshot.state || strategySnapshot.bias || 'flat').toUpperCase()
                    }
                  />
                ) : null}
                {deskResearchSnapshot?.flowLabel ? (
                  <StatusBadge tone={deskResearchSnapshot.flowTone}>{deskResearchSnapshot.flowLabel}</StatusBadge>
                ) : null}
                <StatusBadge value={liveExecutionDecision || liveTradeStatus || 'Monitoring'} />
                {syncBadgeLabel ? (
                  <Chip
                    tone="neutral"
                    className="feed-pill feed-pill--syncing"
                    tooltip={describeFeedPill(syncBadgeLabel)}
                  >
                    {syncBadgeLabel}
                  </Chip>
                ) : null}
                <Chip
                  tone="neutral"
                  className={`feed-pill ${streamIsLive && !internalApiStreamActive ? 'feed-pill--live' : ''}`}
                  tooltip={describeFeedPill(streamBadgeLabel)}
                >
                  {streamBadgeLabel}
                </Chip>
                {chartPayload?.extended_hours ? (
                  <Chip
                    tone="neutral"
                    tooltip="The chart includes pre-market or after-hours bars; extended-session orders stay limit-only."
                  >
                    Extended hours
                  </Chip>
                ) : null}
                {liveAlerts.map((alert) => (
                  <Chip tone="warning" key={alert} tooltip={describeLiveAlert(alert)}>
                    {alert}
                  </Chip>
                ))}
              </div>

              <div className="chart-headbar__legend">
                <SegmentedControl
                  className="chart-view-toggle"
                  ariaLabel="Chart style"
                  value={chartStyle}
                  options={chartStyleOptions}
                  onChange={setChartStyle}
                />
                {studyLegend.length ? (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="chart-legend-action"
                    onClick={resetOverlayVisibility}
                  >
                    Reset studies
                  </Button>
                ) : null}
                {studyLegend.map((indicator) => (
                  <Chip
                    as="button"
                    type="button"
                    tone="neutral"
                    active={!hiddenOverlays[indicator.name]}
                    className={`chart-indicator-chip ${
                      hiddenOverlays[indicator.name] ? 'chart-indicator-chip--muted' : ''
                    }`}
                    key={indicator.name}
                    onClick={() => toggleOverlay(indicator.name)}
                  >
                    <SignalDot
                      className="chart-indicator-chip__swatch"
                      accent={indicator.color}
                      glow={`${indicator.color}4d`}
                      size="md"
                    />
                    {indicator.label}
                  </Chip>
                ))}
              </div>
            </div>

            <div className="chart-headbar chart-headbar--microcopy">
              <span>{streamSupportLine}</span>
              <span>
                {syncBadgeLabel
                  ? 'Background refresh is active.'
                  : autoRefresh
                    ? 'The desk is staying warm with quiet background sync.'
                    : 'Manual refresh is enabled for a calmer desk.'}
              </span>
            </div>

            {showDeskErrorBanner ? (
              <ErrorState
                compact
                className="chart-status-banner"
                title={hasDeskData ? 'Desk refresh needs attention' : 'Trading desk unavailable'}
                description={error}
                actionLabel={hasDeskData ? 'Refresh desk' : 'Reload desk'}
                onAction={handleRefreshWorkspace}
                secondaryActionLabel={!hasDeskData ? 'Open watchlist' : ''}
                onSecondaryAction={!hasDeskData ? () => navigate('/watchlist') : null}
              />
            ) : null}
            {streamError && !isQuietStreamError(streamError) ? (
              <FeedbackState
                compact
                tone="warning"
                className="chart-status-banner"
                eyebrow="Realtime stream"
                title={streamStatus === 'fallback' ? 'Realtime stream fallback active' : 'Realtime stream needs attention'}
                description={streamError}
                actions={[
                  {
                    label: 'Refresh desk',
                    onAction: handleRefreshWorkspace,
                    variant: 'ghost',
                  },
                ]}
              />
            ) : null}
            {chartFreshnessAlert ? (
              <FeedbackState
                compact
                tone={chartFreshnessAlert.tone || 'warning'}
                className="chart-status-banner"
                eyebrow="Market data"
                title={chartFreshnessAlert.title || 'Market data needs attention'}
                description={chartFreshnessAlert.message}
                actions={[
                  {
                    label: 'Refresh desk',
                    onAction: handleRefreshWorkspace,
                    variant: 'ghost',
                  },
                ]}
              />
            ) : null}
          </div>

        <div className="chart-fullscreen__ticker-rail">
          {tickerStrip.map((ticker) => {
            const symbol = String(ticker || '').toUpperCase()
            const accent = tickerAccent(symbol)
            const row = liveTickerLookup[symbol]
            const active = symbol === String(form.ticker || '').toUpperCase()

            return (
              <Chip
                key={symbol}
                as="button"
                type="button"
                tone="neutral"
                size="md"
                active={active}
                className={`chart-rail-chip ${active ? 'chart-rail-chip--active' : ''}`}
                style={{
                  '--ticker-accent': accent,
                  '--ticker-accent-soft': hexToRgba(accent, active ? 0.3 : 0.16),
                }}
                onClick={() => focusTicker(symbol, form.interval, form.horizon)}
              >
                <SignalDot className="chart-rail-chip__dot" accent={accent} glow={hexToRgba(accent, active ? 0.3 : 0.16)} size="md" />
                <strong>{symbol}</strong>
                <small>
                  {formatPrice(
                    row?.live_price ??
                      row?.close ??
                      (symbol === String(form.ticker || '').toUpperCase() ? streamedLivePrice : null),
                  )}
                </small>
              </Chip>
            )
          })}
        </div>

        <aside
          className={`chart-market-panel ${marketPanelOpen ? 'chart-market-panel--open' : ''}`}
        >
          <div className="chart-market-panel__header">
            <div>
              <Kicker as="div">Watchlist</Kicker>
              <h3>{form.ticker} details</h3>
            </div>
            <div className="chart-market-panel__actions">
              <Button
                type="button"
                variant={liveFocusMode ? 'solid' : 'ghost'}
                size="sm"
                            className={`desk-action chart-market-panel__focus-toggle ${
                  liveFocusMode ? 'chart-market-panel__focus-toggle--active' : ''
                }`}
                onClick={toggleLiveFocusMode}
              >
                {liveFocusMode ? 'Full view' : 'Focus mode'}
              </Button>
              <Button
                type="button"
                variant="subtle"
                size="sm"
                            className="desk-action chart-market-panel__hide"
                onClick={() => setMarketPanelOpen(false)}
              >
                Hide
              </Button>
            </div>
          </div>

          <div className="chart-market-panel__tabs">
            {marketPanelTabs.map((tab) => (
              <Button
                key={tab.key}
                type="button"
                variant={marketPanelTab === tab.key ? 'solid' : 'ghost'}
                size="sm"
                className={`chart-market-panel__tab ${
                  marketPanelTab === tab.key ? 'chart-market-panel__tab--active' : ''
                }`}
                onClick={() => {
                  setMarketPanelTab(tab.key)
                  setMarketPanelOpen(true)
                }}
              >
                {tab.label}
              </Button>
            ))}
          </div>

          <div className="chart-market-panel__body">{renderMarketPanelBody()}</div>
        </aside>

        <div className="chart-fullscreen__tool-rail">
          {toolRailGroups.map((group) => (
            <div key={group.group} className="chart-tool-group">
              <div className="chart-tool-group__label">{group.group}</div>
              <div className="chart-tool-group__stack">
                {group.tools.map((tool) => (
                  <Button
                    key={tool.key}
                    type="button"
                    variant={toolMode === tool.key ? 'solid' : 'ghost'}
                    size="sm"
                    className={`chart-tool-button ${
                      toolMode === tool.key ? 'chart-tool-button--active' : ''
                    }`}
                    onClick={() => changeTool(tool.key)}
                    title={`${tool.label} (${tool.helper})`}
                    aria-label={`${tool.label} tool`}
                  >
                    <ToolGlyph glyph={tool.key} className="chart-tool-button__icon" />
                    <span className="chart-tool-button__meta">
                      <strong>{tool.label}</strong>
                      <span>{tool.helper}</span>
                    </span>
                    <span className="chart-tool-button__key" aria-hidden="true">
                      {tool.helper}
                    </span>
                  </Button>
                ))}
              </div>
            </div>
          ))}
        </div>

        <section
          className={`chart-fullscreen__stage ${
            hasHydratedWorkspaceData ? '' : 'chart-fullscreen__stage--compact'
          }`.trim()}
        >
          {hasHydratedWorkspaceData ? (
            <div className="chart-stage-topband">
              {chartCockpitSnapshot ? <ChartStageCockpit snapshot={chartCockpitSnapshot} /> : null}
              <div className="chart-stage-market-summary">
                <div className="chart-stage-market-summary__grid">
                  {chartStageContextCards.map((item) => (
                    <div key={item.label} className="chart-console__metric chart-stage-market-summary__metric">
                      <span>{item.label}</span>
                      <strong>{item.value}</strong>
                    </div>
                  ))}
                </div>
                {deskResearchSnapshot ? (
                  <div className="chart-stage-market-summary__details">
                    <div className="compare-snapshot-card__path chart-stage-market-summary__path">
                      <div className="compare-snapshot-card__path-head">
                        <span>Price path</span>
                        <strong>{chartStagePathRangeLabel}</strong>
                      </div>
                      {chartStagePathModel ? (
                        <>
                          <div className="compare-snapshot-path">
                            <div className="compare-snapshot-path__rail" />
                            {chartStagePathModel.entryLowPct !== null ? (
                              <span
                                className="compare-snapshot-path__band compare-snapshot-path__band--entry"
                                style={{
                                  left: `${chartStagePathModel.entryLowPct}%`,
                                  width: `${Math.max(
                                    2,
                                    (chartStagePathModel.entryHighPct ?? chartStagePathModel.entryLowPct) -
                                      chartStagePathModel.entryLowPct,
                                  )}%`,
                                }}
                              />
                            ) : null}
                            {chartStagePathModel.stopPct !== null ? (
                              <span
                                className="watchlist-drift-range__marker watchlist-drift-range__marker--stop"
                                style={{ left: `${chartStagePathModel.stopPct}%` }}
                              />
                            ) : null}
                            {chartStagePathModel.targetPct !== null ? (
                              <span
                                className="compare-snapshot-path__marker compare-snapshot-path__marker--target"
                                style={{ left: `${chartStagePathModel.targetPct}%` }}
                              />
                            ) : null}
                            {chartStagePathModel.livePct !== null ? (
                              <span
                                className="compare-snapshot-path__marker compare-snapshot-path__marker--live"
                                style={{ left: `${chartStagePathModel.livePct}%` }}
                              />
                            ) : null}
                          </div>
                          <div className="compare-snapshot-path__legend compare-snapshot-path__legend--compact">
                            <div className="compare-snapshot-path__legend-item compare-snapshot-path__legend-item--entry">
                              <span>Entry</span>
                              <strong>{deskResearchSnapshot.entryZoneLabel}</strong>
                            </div>
                            <div className="compare-snapshot-path__legend-item compare-snapshot-path__legend-item--live">
                              <span>Live</span>
                              <strong>{deskResearchSnapshot.livePriceLabel}</strong>
                            </div>
                            <div className="compare-snapshot-path__legend-item compare-snapshot-path__legend-item--target">
                              <span>Target</span>
                              <strong>{deskResearchSnapshot.targetPriceLabel}</strong>
                            </div>
                          </div>
                        </>
                      ) : (
                        <p className="compare-snapshot-card__path-empty">Entry, live, and target levels are not all available yet.</p>
                      )}
                    </div>
                    <div className="chart-stage-market-summary__notes">
                      <div className="chart-stage-market-summary__notes-head">
                        <span>Desk read</span>
                        <div className="chart-stage-market-summary__chips">
                          <StatusBadge tone={deskResearchSnapshot.flowTone}>{deskResearchSnapshot.flowLabel}</StatusBadge>
                          <StatusBadge tone={deskResearchSnapshot.newsTone}>{deskResearchSnapshot.newsLabel}</StatusBadge>
                          <StatusBadge tone={deskResearchSnapshot.executionTone}>{deskResearchSnapshot.executionLabel}</StatusBadge>
                        </div>
                      </div>
                      <div className="chart-stage-market-summary__notes-body">
                        {deskResearchSnapshot.notes.slice(0, 3).map((note) => (
                          <p key={note}>{note}</p>
                        ))}
                      </div>
                    </div>
                  </div>
                ) : null}
                {deskResearchSnapshot ? (
                  <div className="chart-stage-market-summary__execution">
                    <div className="chart-stage-market-summary__execution-head">
                      <span>Execution checks</span>
                      <StatusBadge tone={executionQualitySummary.tone}>{executionQualitySummary.label}</StatusBadge>
                    </div>
                    <div className="chart-stage-market-summary__execution-grid">
                      {chartStageExecutionChecks.map((check) => (
                        <div
                          key={check.label}
                          className={`chart-stage-market-summary__execution-card chart-stage-market-summary__execution-card--${check.tone || 'warning'}`}
                        >
                          <span>{check.label}</span>
                          <strong>{check.value}</strong>
                          <small>{check.detail}</small>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
                {deskResearchSnapshot ? (
                  <div className="chart-stage-market-summary__automation">
                    <div className="chart-stage-market-summary__execution-head">
                      <span>Automation paths</span>
                      <StatusBadge tone={chartStageAutomationPaths.workerTone}>{chartStageAutomationPaths.workerLabel}</StatusBadge>
                    </div>
                    <div className="chart-stage-market-summary__automation-grid">
                      {chartStageAutomationPaths.items.map((item) => (
                        <div
                          key={item.label}
                          className={`chart-stage-market-summary__automation-card chart-stage-market-summary__automation-card--${item.tone || 'warning'}`}
                        >
                          <span>{item.label}</span>
                          <strong>{item.value}</strong>
                          <em>{item.meta}</em>
                          <small>{item.detail}</small>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}

          {hasHydratedWorkspaceData ? (
            <CustomMarketChart
              payload={chartPayload}
              ticker={form.ticker}
              interval={form.interval}
              livePrice={streamedLivePrice}
              selectedPrice={selectedChartPoint}
              onPriceSelect={handlePriceSelect}
              onChartAction={handleChartAction}
              onPayloadRecovered={setChartPayload}
              height={chartHeight}
              tickerAccent={activeTickerAccent}
              hiddenOverlays={hiddenOverlays}
              customGuides={customGuides}
              selectedGuideId={selectedGuideId}
              selectedGuide={selectedGuide}
              positionMarkers={currentTickerPositionMarkers}
              workingOrder={workingOrderMarker}
              pendingGuidePoint={pendingGuidePoint}
              toolNotice={toolNotice}
              toolMode={toolMode}
              onGuideEdit={handleGuideEdit}
              onGuideDelete={handleGuideDelete}
              onGuideSelect={setSelectedGuideId}
              canUndoGuides={drawingHistoryState.canUndo}
              canRedoGuides={drawingHistoryState.canRedo}
              onUndoGuides={undoGuideChange}
              onRedoGuides={redoGuideChange}
              onDuplicateGuide={duplicateSelectedGuide}
              onToggleGuideLock={toggleSelectedGuideLock}
              magnetMode={magnetMode}
              onToggleMagnetMode={() => setMagnetMode((current) => !current)}
              drawingVisibility={drawingVisibility}
              onToggleDrawingGroup={toggleDrawingGroup}
              savedViewport={chartViewport}
              onViewportChange={handleViewportChange}
              onResetLayout={handleResetChartLayout}
              chartStyle={chartStyle}
              autoRefreshLabel={
                streamIsLive ? 'tick stream' : autoRefresh ? `${Math.round(pollMs / 1000)}s live` : 'Manual'
              }
            />
          ) : (
            <div className="chart-fullscreen__stage-placeholder">
              <Kicker as="div">Desk load</Kicker>
              <strong>Waiting for chart data</strong>
              <span>Keep the board scanning or load a ticker into the desk to render the chart stage.</span>
            </div>
          )}

          <div className="chart-fullscreen__footer">
            <div className="chart-fullscreen__panel-buttons">
              {drawerTabs.map((tab) => (
                <Button
                  key={tab.key}
                  type="button"
                  variant={activeDrawer === tab.key ? 'solid' : 'ghost'}
                  size="sm"
                  className={`chart-panel-button ${
                    activeDrawer === tab.key ? 'chart-panel-button--active' : ''
                  }`}
                  onClick={() => toggleDrawer(tab.key)}
                >
                  <strong>{tab.label}</strong>
                  <span>{tab.helper}</span>
                  <span className="chart-panel-button__key" aria-hidden="true">
                    {tab.key === 'plan' ? '1' : tab.key === 'position' ? '2' : '3'}
                  </span>
                </Button>
              ))}

              <Button
                type="button"
                variant={marketPanelOpen ? 'solid' : 'ghost'}
                size="sm"
                className={`chart-panel-button ${marketPanelOpen ? 'chart-panel-button--active' : ''}`}
                onClick={() => {
                  setMarketPanelTab('watchlist')
                  setMarketPanelOpen((current) => !current)
                }}
              >
                <strong>Radar</strong>
                <span>
                  {marketPanelOpen
                    ? marketPanelTabs.find((tab) => tab.key === marketPanelTab)?.label || 'Watchlist'
                    : 'Watchlist / DOM'}
                </span>
                <span className="chart-panel-button__key" aria-hidden="true">
                  3
                </span>
              </Button>

              <Button
                type="button"
                variant={tapeOpen ? 'solid' : 'ghost'}
                size="sm"
                className={`chart-panel-button ${tapeOpen ? 'chart-panel-button--active' : ''}`}
                onClick={() => {
                  setActiveDrawer(null)
                  setTapeOpen((current) => !current)
                }}
              >
                <strong>Tape</strong>
                <span>{tapePresentation.panelLabel}</span>
                <span className="chart-panel-button__key" aria-hidden="true">
                  T
                </span>
              </Button>

              <Button
                type="button"
                variant={selectedChartPoint ? 'solid' : 'ghost'}
                size="sm"
                className={`chart-panel-button ${selectedChartPoint ? 'chart-panel-button--active' : ''}`}
                onClick={() => setSelectedChartPoint(null)}
                disabled={!selectedChartPoint}
              >
                <strong>Clear pick</strong>
                <span>{selectedChartPoint ? 'Remove staged level' : 'No staged level'}</span>
                <span className="chart-panel-button__key" aria-hidden="true">
                  C
                </span>
              </Button>
            </div>

            <section className={`chart-bottom-drawer ${tapeOpen ? 'chart-bottom-drawer--open' : ''}`}>
              <div className="chart-bottom-drawer__header">
                <div>
                  <Kicker as="div">{tapePresentation.headerKicker}</Kicker>
                  <h3>{tapePresentation.headerTitle}</h3>
                </div>
                <Button type="button" variant="subtle" size="sm" className="desk-action" onClick={() => setTapeOpen(false)}>
                  Hide tape
                </Button>
              </div>

              <div className="chart-bottom-drawer__grid">
                <div className="trade-ticket__tape">
                  <div className="chart-bottom-drawer__snapshot chart-bottom-drawer__snapshot--tape">
                    <div className="chart-stage-summary__item">
                      <span>Prints</span>
                      <strong>{formatCompact(tapeSummary.prints)}</strong>
                    </div>
                    <div className="chart-stage-summary__item">
                      <span>{tapePresentation.sizeLabel}</span>
                      <strong>{formatCompact(tapeSummary.totalSize)}</strong>
                    </div>
                    <div className="chart-stage-summary__item chart-stage-summary__item--notional">
                      <span>{tapePresentation.notionalLabel}</span>
                      <strong>{formatPrice(tapeSummary.totalNotional)}</strong>
                    </div>
                    <div className="chart-stage-summary__item">
                      <span>{tapePresentation.flowPrimaryLabel}</span>
                      <strong>{tapePresentation.flowPrimaryValue}</strong>
                    </div>
                    <div className="chart-stage-summary__item">
                      <span>{tapePresentation.flowSecondaryLabel}</span>
                      <strong>{tapePresentation.flowSecondaryValue}</strong>
                    </div>
                  </div>

                  {tradeTape.length ? (
                    <div className="trade-ticket__tape-list trade-ticket__tape-list--enhanced">
                      {tradeTape.map((tick, index) => (
                        <div
                          key={`${tick.timestamp}-${index}`}
                          className={`trade-ticket__tape-row trade-ticket__tape-row--${tick.side || 'neutral'}`}
                        >
                          <span>{formatClock(tick.timestamp)}</span>
                          <strong>{formatPrice(tick.price)}</strong>
                          <span>{formatCompact(tick.size)} sh</span>
                          <span>{formatPrice(tick.notional)}</span>
                          <span>{String(tick.side || 'neutral').toUpperCase()}</span>
                          <span>{tick.exchange || '--'}</span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="trade-ticket__helper">
                      {tapePresentation.helperText}
                    </div>
                  )}
                </div>

                <div className="chart-bottom-drawer__notes">
                  <div className="chart-bottom-drawer__snapshot">
                    <div className="chart-stage-summary__item">
                      <span>Bid / ask</span>
                      <strong>
                        {formatPrice(selectedQuote?.bid_price)} / {formatPrice(selectedQuote?.ask_price)}
                      </strong>
                    </div>
                    <div className="chart-stage-summary__item">
                      <span>Contract</span>
                      <strong>{contract.contract_symbol || '--'}</strong>
                    </div>
                    <div className="chart-stage-summary__item">
                      <span>Risk / reward</span>
                      <strong>{riskReward === null ? '--' : `${formatNumber(riskReward, 2)}R`}</strong>
                    </div>
                    <div className="chart-stage-summary__item">
                      <span>Units</span>
                      <strong>{formatShares(positionPreview?.suggestedContracts)}</strong>
                    </div>
                    <div className="chart-stage-summary__item">
                      <span>Eff. risk</span>
                      <strong>{formatPrice(positionPreview?.effectiveMaxRiskDollars)}</strong>
                    </div>
                    <div className="chart-stage-summary__item">
                      <span>Risk trim</span>
                      <strong>{formatRatioPercent(positionPreview?.riskBudgetMultiplier, 0)}</strong>
                    </div>
                    <div className="chart-stage-summary__item">
                      <span>Largest print</span>
                      <strong>
                        {tapeSummary.largestPrint
                          ? `${formatPrice(tapeSummary.largestPrint.price)} | ${formatCompact(
                              tapeSummary.largestPrint.size,
                            )} sh`
                          : '--'}
                      </strong>
                    </div>
                    <div className="chart-stage-summary__item">
                      <span>Layout</span>
                      <strong>{layoutReadyKey === layoutStorageKey ? 'Saved for ticker' : 'Syncing'}</strong>
                    </div>
                  </div>

                  {report?.notes?.length ? (
                    <ul className="simple-list">
                      {report.notes.slice(0, 4).map((note) => (
                        <li key={note}>{note}</li>
                      ))}
                    </ul>
                  ) : (
                    <div className="trade-ticket__helper">
                      Model notes will show up here once the current symbol has guidance attached.
                    </div>
                  )}
                </div>
              </div>
            </section>
          </div>
        </section>

        <div className="chart-fullscreen__summary">
          <div className="chart-stage-summary">
            <div className="chart-stage-summary__head">
              <div>
                <Kicker as="div">Execution focus</Kicker>
                <strong>
                  {form.ticker} {formatPrice(activeExecutionPrice)}
                </strong>
              </div>
              <div className="chart-stage-summary__pills">
                <StatusBadge value={liveExecutionDecision || liveTradeStatus || 'Monitoring'} />
                <StatusBadge value={report?.trade_decision || 'No trade'} />
              </div>
            </div>

            {brokerAccountConnected ? (
              <div className="chart-stage-summary__account-grid">
                <div className="chart-stage-summary__item chart-stage-summary__item--money">
                  <span>Paper equity</span>
                  <strong>{formatPrice(brokerAccount?.equity)}</strong>
                </div>
                <div className="chart-stage-summary__item chart-stage-summary__item--money">
                  <span>Cash</span>
                  <strong>{formatPrice(brokerAccount?.cash)}</strong>
                </div>
                <div className="chart-stage-summary__item chart-stage-summary__item--money">
                  <span>Buying power</span>
                  <strong>{formatPrice(brokerAccount?.buying_power)}</strong>
                </div>
                <div className="chart-stage-summary__item chart-stage-summary__item--money">
                  <span>{brokerAccountValueDelta === null ? 'Open value' : 'Equity minus cash'}</span>
                  <strong>
                    {brokerAccountValueDelta === null
                      ? formatPrice(brokerAccount?.position_market_value)
                      : formatSignedCurrency(brokerAccountValueDelta)}
                  </strong>
                </div>
              </div>
            ) : null}

            {deskResearchSnapshot ? (
              <div className="compare-snapshot-board desk-research-board">
                <DeskResearchCard snapshot={deskResearchSnapshot} compact />
              </div>
            ) : null}

            <div className="chart-stage-summary__grid">
              <div className="chart-stage-summary__item">
                <span>{strategySnapshot?.available ? 'Noise area' : 'Entry zone'}</span>
                <strong>
                  {strategySnapshot?.available
                    ? `${formatOptionalPrice(strategySnapshot.lower_band)} - ${formatOptionalPrice(strategySnapshot.upper_band)}`
                    : formatMeaningfulPriceRange(optionPlan.entry_low_price, optionPlan.entry_high_price)}
                </strong>
              </div>
              <div className="chart-stage-summary__item">
                <span>{strategySnapshot?.available ? 'Session VWAP' : 'Target'}</span>
                <strong>
                  {strategySnapshot?.available
                    ? formatOptionalPrice(strategySnapshot.vwap)
                    : formatMeaningfulPrice(optionPlan.expected_underlying_target)}
                </strong>
              </div>
              <div className="chart-stage-summary__item">
                <span>{strategySnapshot?.available ? 'Trail stop' : 'Cut loss'}</span>
                <strong>
                  {strategySnapshot?.available
                    ? formatOptionalPrice(strategySnapshot.active_stop)
                    : formatMeaningfulPrice(optionPlan.invalidation_price)}
                </strong>
              </div>
              <div className="chart-stage-summary__item">
                <span>Order</span>
                <strong>{formatOrderTypeLabel(tradeTicket.orderType)}</strong>
              </div>
              <div className="chart-stage-summary__item">
                <span>Instrument</span>
                <strong>{formatInstrumentTypeLabel(tradeTicket.instrumentType)}</strong>
              </div>
              <div className="chart-stage-summary__item">
                <span>Time in force</span>
                <strong>{formatTimeInForceLabel(tradeTicket.timeInForce)}</strong>
              </div>
              <div className="chart-stage-summary__item">
                <span>Open P&L</span>
                <strong>{formatSignedCurrency(dashboard?.portfolio?.summary?.unrealized_pnl)}</strong>
              </div>
            </div>

            <div className="chart-stage-summary__meta">
              <span>Picked {selectedChartPoint ? formatPrice(selectedChartPoint.price) : '--'}</span>
              <span>Tool {toolRail.find((item) => item.key === toolMode)?.label || 'Pan'}</span>
              <span>
                {strategySnapshot?.available ? 'Next' : 'Levels'}{' '}
                {strategySnapshot?.available
                  ? strategySnapshot.next_checkpoint
                    ? `${strategySnapshot.next_checkpoint} ET`
                    : 'Close'
                  : hasLevelOverrides
                    ? 'Adjusted'
                    : 'Default'}
              </span>
            </div>

            <div className="chart-stage-summary__note">
              {selectedChartPoint
                ? `Chart pick ${formatPrice(selectedChartPoint.price)} at ${formatEventTime(
                    selectedChartPoint.timestamp,
                  )}`
                : strategyAlignmentMessage || entryAlignmentMessage}
            </div>
          </div>
        </div>

        <aside className={`chart-side-drawer ${activeDrawer ? 'chart-side-drawer--open' : ''}`}>
          <div className="chart-side-drawer__header">
            <div>
              <Kicker as="div">Overlay panel</Kicker>
              <h3>
                {activeDrawer === 'plan'
                  ? 'Algorithmic plan'
                  : 'Position sizing'}
              </h3>
            </div>
                      <Button type="button" variant="subtle" size="sm" className="desk-action" onClick={() => setActiveDrawer(null)}>
              Close
            </Button>
          </div>
          <div className="chart-side-drawer__body">{activeDrawer ? renderDrawerBody() : null}</div>
        </aside>

      </div>
    </section>
    </>
  )
}
