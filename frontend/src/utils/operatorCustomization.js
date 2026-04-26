import {
  buildIntradayPresetDefaults,
  DEFAULT_INTRADAY_PRESET,
} from './intradayPresetModel'

export const STYLE_DEFAULT_SURFACE_VALUE = 'style_default'

export const TRADING_STYLE_OPTIONS = [
  { value: 'swing', label: 'Swing' },
  { value: 'intraday', label: 'Intraday' },
]

export const TRADING_STYLE_PROFILES = {
  swing: {
    label: 'Swing',
    shellLabel: 'Swing mode',
    startupSurface: '/',
    reviewSurface: '/journal',
    tone: 'neutral',
    description: 'Broader setup review, slower follow-up cadence, and multi-session context.',
  },
  intraday: {
    label: 'Intraday',
    shellLabel: 'Intraday mode',
    startupSurface: '/',
    reviewSurface: '/trades',
    tone: 'warning',
    description: 'Session-first scanning, tighter execution review, and same-day risk management.',
  },
}

export const STARTUP_SURFACE_OPTIONS = [
  { value: STYLE_DEFAULT_SURFACE_VALUE, label: 'Use trading-style default' },
  { value: '/', label: 'Desk' },
  { value: '/watchlist', label: 'Watchlist' },
  { value: '/compare', label: 'Compare' },
  { value: '/trades', label: 'Trades' },
  { value: '/portfolio', label: 'Portfolio' },
  { value: '/journal', label: 'Journal' },
  { value: '/alerts', label: 'Alerts' },
  { value: '/notes', label: 'Notes' },
]

export const REVIEW_SURFACE_OPTIONS = [
  { value: STYLE_DEFAULT_SURFACE_VALUE, label: 'Use trading-style default' },
  { value: '/alerts', label: 'Alerts' },
  { value: '/trades', label: 'Trades' },
  { value: '/journal', label: 'Journal' },
  { value: '/notes', label: 'Notes' },
]

const TRADING_STYLE_SET = new Set(TRADING_STYLE_OPTIONS.map((item) => item.value))
const STARTUP_SURFACE_SET = new Set(STARTUP_SURFACE_OPTIONS.map((item) => item.value))
const REVIEW_SURFACE_SET = new Set(REVIEW_SURFACE_OPTIONS.map((item) => item.value))
const WORKFLOW_SURFACE_SET = new Set([
  '/',
  '/watchlist',
  '/compare',
  '/trades',
  '/portfolio',
  '/journal',
  '/alerts',
  '/notes',
])

export function normalizeTradingStyle(value, fallback = 'swing') {
  const normalized = String(value || '').trim().toLowerCase()
  return TRADING_STYLE_SET.has(normalized) ? normalized : fallback
}

export function getTradingStyleProfile(value = 'swing') {
  const normalized = normalizeTradingStyle(value)
  return TRADING_STYLE_PROFILES[normalized] || TRADING_STYLE_PROFILES.swing
}

export function getTradingStyleLabel(value = 'swing') {
  return getTradingStyleProfile(value).label
}

export function normalizeStartupSurface(value, fallback = STYLE_DEFAULT_SURFACE_VALUE) {
  const normalized = String(value || '').trim()
  return STARTUP_SURFACE_SET.has(normalized) ? normalized : fallback
}

export function normalizeReviewSurface(value, fallback = STYLE_DEFAULT_SURFACE_VALUE) {
  const normalized = String(value || '').trim()
  return REVIEW_SURFACE_SET.has(normalized) ? normalized : fallback
}

export function resolveStartupSurface(tradingStyle = 'swing', startupSurface = STYLE_DEFAULT_SURFACE_VALUE) {
  const normalizedSurface = normalizeStartupSurface(startupSurface)
  if (normalizedSurface === STYLE_DEFAULT_SURFACE_VALUE) {
    return getTradingStyleProfile(tradingStyle).startupSurface
  }
  return normalizedSurface
}

export function resolveReviewSurface(tradingStyle = 'swing', reviewSurface = STYLE_DEFAULT_SURFACE_VALUE) {
  const normalizedSurface = normalizeReviewSurface(reviewSurface)
  if (normalizedSurface === STYLE_DEFAULT_SURFACE_VALUE) {
    return getTradingStyleProfile(tradingStyle).reviewSurface
  }
  return normalizedSurface
}

export function isWorkflowSurfacePath(pathname = '') {
  return WORKFLOW_SURFACE_SET.has(String(pathname || '').trim())
}

export function getSurfaceLabel(pathname = '') {
  const normalized = String(pathname || '').trim()
  const known = [...STARTUP_SURFACE_OPTIONS, ...REVIEW_SURFACE_OPTIONS].find((item) => item.value === normalized)
  return known?.label || 'Desk'
}

export function buildSurfaceSummary({
  tradingStyle = 'swing',
  startupSurface = STYLE_DEFAULT_SURFACE_VALUE,
  rememberLastWorkflowSurface = false,
  reviewSurface = STYLE_DEFAULT_SURFACE_VALUE,
  showWorkflowStatusStrip = true,
  showWorkflowGuides = true,
  showArrivalBanners = true,
} = {}) {
  const resolvedStartupSurface = resolveStartupSurface(tradingStyle, startupSurface)
  const resolvedReviewSurface = resolveReviewSurface(tradingStyle, reviewSurface)
  const guidanceParts = [
    showWorkflowStatusStrip ? 'status strip' : null,
    showWorkflowGuides ? 'page guides' : null,
    showArrivalBanners ? 'arrival banners' : null,
  ].filter(Boolean)

  return {
    styleLabel: getTradingStyleLabel(tradingStyle),
    startupLabel: rememberLastWorkflowSurface
      ? `Resume last surface, fallback ${getSurfaceLabel(resolvedStartupSurface)}`
      : getSurfaceLabel(resolvedStartupSurface),
    reviewLabel: getSurfaceLabel(resolvedReviewSurface),
    guidanceLabel: guidanceParts.length ? guidanceParts.join(' + ') : 'minimal guidance',
    guidanceCount: guidanceParts.length,
  }
}

export function buildTradingStylePreset(tradingStyle = 'swing', intradayPreset = DEFAULT_INTRADAY_PRESET) {
  const normalizedStyle = normalizeTradingStyle(tradingStyle)
  if (normalizedStyle === 'intraday') {
    return buildIntradayPresetDefaults(intradayPreset)
  }

  return {
    tradingStyle: 'swing',
    intradayPreset: DEFAULT_INTRADAY_PRESET,
    startupSurface: STYLE_DEFAULT_SURFACE_VALUE,
    defaultReviewSurface: STYLE_DEFAULT_SURFACE_VALUE,
    defaultInterval: '1h',
    defaultHorizon: 20,
    autoRefreshWatchlist: false,
    compactTables: false,
    regularHoursOnly: false,
    openingRangeMinutes: 30,
    intradayEventGuardMinutes: 60,
    flattenBeforeCloseMinutes: 15,
    defaultOrderType: 'limit',
    defaultExecutionIntent: 'desk',
    showWorkflowStatusStrip: true,
    showWorkflowGuides: true,
    showArrivalBanners: true,
  }
}
