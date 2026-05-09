import { createContext, useContext, useEffect, useMemo } from "react"
import useLocalStorage from "../hooks/useLocalStorage"
import {
  DEFAULT_INTRADAY_PRESET,
  normalizeIntradayPreset,
} from '../utils/intradayPresetModel'
import {
  normalizeReviewSurface,
  normalizeStartupSurface,
  normalizeTradingStyle,
  STYLE_DEFAULT_SURFACE_VALUE,
} from '../utils/operatorCustomization'
import {
  ACCOUNT_PROFILE_FALLBACK,
  PRIMARY_BROKERAGE_LINKED_ACCOUNT_FALLBACK,
  normalizeAccountProfile,
  normalizePrimaryBrokerageLinkedAccountId,
} from '../utils/accountProfileModel'
import {
  DEFAULT_VISUAL_FOCUS_MODE,
  normalizeFocusRailKey,
  normalizePinnedFocusRails,
  normalizeVisualFocusMode,
} from '../utils/focusApertureModel'

const PreferencesContext = createContext(null)

const LEGACY_PREFERENCES_STORAGE_KEY = 'sos-preferences'
const PREFERENCES_STORAGE_KEY = 'sos-preferences-v2'
const LOCAL_DESK_PROFILE_VERSION = 17
const supportedIntervals = new Set(['1m', '5m', '15m', '30m', '1h', '4h', '1d'])
const supportedOrderTypes = new Set(['market', 'limit', 'stop_market', 'stop_limit', 'trailing_stop'])
const supportedExecutionIntents = new Set(['desk', 'broker_paper', 'broker_live'])

const defaultPreferences = {
  preferencesVersion: LOCAL_DESK_PROFILE_VERSION,
  defaultTicker: 'SPY',
  defaultInterval: '5m',
  defaultHorizon: 5,
  watchlistTickers: 'SPY,QQQ,NVDA,AAPL,MSFT',
  pollingMs: 15000,
  autoRefreshWatchlist: true,
  compactTables: true,
  tradingStyle: 'intraday',
  intradayPreset: DEFAULT_INTRADAY_PRESET,
  startupSurface: STYLE_DEFAULT_SURFACE_VALUE,
  rememberLastWorkflowSurface: false,
  defaultReviewSurface: STYLE_DEFAULT_SURFACE_VALUE,
  showWorkflowStatusStrip: true,
  showWorkflowGuides: true,
  showArrivalBanners: true,
  visualFocusMode: DEFAULT_VISUAL_FOCUS_MODE,
  pinnedFocusRails: ['risk', 'automation'],
  expandedFocusRail: '',
  activeAccountProfile: ACCOUNT_PROFILE_FALLBACK,
  primaryBrokerageLinkedAccountId: PRIMARY_BROKERAGE_LINKED_ACCOUNT_FALLBACK,
  defaultAccountSize: 100000,
  defaultRiskPercent: 0.5,
  defaultOrderType: 'limit',
  defaultExecutionIntent: 'desk',
  regularHoursOnly: false,
  openingRangeMinutes: 15,
  intradayEventGuardMinutes: 30,
  flattenBeforeCloseMinutes: 10,
  capitalPreservationMode: true,
  tinyAccountMode: false,
  fractionalSharesOnlyMode: false,
  promotionGateMode: true,
  promotionGateMinResolved: 3,
  promotionGateMinWinRatePercent: 55,
  promotionGateMaxAverageAbsSlippageBps: 10,
  promotionGateMaxWorstAbsSlippageBps: 20,
  maxOpenPositions: 1,
  maxNotionalPerTrade: 500,
  equitiesOnlyMode: true,
  limitOrdersOnlyMode: true,
  longOnlyMode: true,
  breakevenAfterR: 1,
  firstTargetR: 1,
  firstTrimPercent: 33,
  secondTargetR: 2,
  secondTrimPercent: 33,
  maxDailyLossR: 1.5,
  maxConsecutiveLosses: 2,
}

function clampNumber(value, fallback, min, max) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return fallback
  return Math.min(Math.max(Math.round(numeric), min), max)
}

function normalizeTicker(value, fallback) {
  const normalized = String(value || '').trim().toUpperCase()
  return normalized || fallback
}

function normalizeInterval(value, fallback) {
  const normalized = String(value || '').trim().toLowerCase()
  return supportedIntervals.has(normalized) ? normalized : fallback
}

function normalizeOrderType(value, fallback) {
  const normalized = String(value || '').trim().toLowerCase()
  return supportedOrderTypes.has(normalized) ? normalized : fallback
}

function normalizeExecutionIntent(value, fallback) {
  const normalized = String(value || '').trim().toLowerCase()
  return supportedExecutionIntents.has(normalized) ? normalized : fallback
}

function normalizeWatchlist(value, fallback) {
  const raw = String(value || fallback)
  const uniqueTickers = []
  raw
    .split(',')
    .map((item) => normalizeTicker(item, ''))
    .filter(Boolean)
    .forEach((ticker) => {
      if (!uniqueTickers.includes(ticker)) {
        uniqueTickers.push(ticker)
      }
    })
  return uniqueTickers.length ? uniqueTickers.join(',') : fallback
}

function normalizePreferences(rawPreferences) {
  const current = rawPreferences && typeof rawPreferences === 'object' ? rawPreferences : {}
  const legacyProfile = Number(current.preferencesVersion || 0) < LOCAL_DESK_PROFILE_VERSION
  const tradingStyleFallback =
    legacyProfile && String(current.tradingStyle || '').trim().toLowerCase() === 'swing'
      ? 'intraday'
      : defaultPreferences.tradingStyle
  const normalizedTradingStyle = normalizeTradingStyle(current.tradingStyle, tradingStyleFallback)
  const normalizedIntradayPreset = normalizeIntradayPreset(current.intradayPreset, defaultPreferences.intradayPreset)
  const shouldMigrateDeskStartup =
    legacyProfile &&
    normalizedTradingStyle === 'intraday' &&
    (String(current.startupSurface || '').trim() === '/watchlist' ||
      String(current.startupSurface || '').trim() === STYLE_DEFAULT_SURFACE_VALUE) &&
    ['opening_range', 'intraday_momentum'].includes(normalizedIntradayPreset)
  return {
    preferencesVersion: LOCAL_DESK_PROFILE_VERSION,
    defaultTicker: normalizeTicker(current.defaultTicker, defaultPreferences.defaultTicker),
    defaultInterval: normalizeInterval(current.defaultInterval, defaultPreferences.defaultInterval),
    defaultHorizon: clampNumber(current.defaultHorizon, defaultPreferences.defaultHorizon, 1, 50),
    watchlistTickers: normalizeWatchlist(current.watchlistTickers, defaultPreferences.watchlistTickers),
    pollingMs: clampNumber(current.pollingMs, defaultPreferences.pollingMs, 5000, 60000),
    autoRefreshWatchlist: current.autoRefreshWatchlist !== false,
    compactTables: current.compactTables !== undefined ? Boolean(current.compactTables) : defaultPreferences.compactTables,
    tradingStyle: normalizedTradingStyle,
    intradayPreset: normalizedIntradayPreset,
    startupSurface: shouldMigrateDeskStartup
      ? '/'
      : normalizeStartupSurface(current.startupSurface, defaultPreferences.startupSurface),
    rememberLastWorkflowSurface:
      current.rememberLastWorkflowSurface !== undefined
        ? Boolean(current.rememberLastWorkflowSurface)
        : defaultPreferences.rememberLastWorkflowSurface,
    defaultReviewSurface: normalizeReviewSurface(
      current.defaultReviewSurface,
      defaultPreferences.defaultReviewSurface,
    ),
    showWorkflowStatusStrip:
      current.showWorkflowStatusStrip !== undefined
        ? Boolean(current.showWorkflowStatusStrip)
        : defaultPreferences.showWorkflowStatusStrip,
    showWorkflowGuides:
      current.showWorkflowGuides !== undefined
        ? Boolean(current.showWorkflowGuides)
        : defaultPreferences.showWorkflowGuides,
    showArrivalBanners:
      current.showArrivalBanners !== undefined
        ? Boolean(current.showArrivalBanners)
        : defaultPreferences.showArrivalBanners,
    visualFocusMode: normalizeVisualFocusMode(
      current.visualFocusMode,
      defaultPreferences.visualFocusMode,
    ),
    pinnedFocusRails: normalizePinnedFocusRails(
      current.pinnedFocusRails,
      defaultPreferences.pinnedFocusRails,
    ),
    expandedFocusRail: normalizeFocusRailKey(
      current.expandedFocusRail,
      defaultPreferences.expandedFocusRail,
    ),
    activeAccountProfile: normalizeAccountProfile(
      current.activeAccountProfile,
      defaultPreferences.activeAccountProfile,
    ),
    primaryBrokerageLinkedAccountId: normalizePrimaryBrokerageLinkedAccountId(
      current.primaryBrokerageLinkedAccountId,
      defaultPreferences.primaryBrokerageLinkedAccountId,
    ),
    defaultAccountSize: Math.min(
      Math.max(Number(current.defaultAccountSize ?? defaultPreferences.defaultAccountSize) || defaultPreferences.defaultAccountSize, 100000),
      1000000,
    ),
    defaultRiskPercent: Math.min(
      Math.max(Number(current.defaultRiskPercent ?? defaultPreferences.defaultRiskPercent) || defaultPreferences.defaultRiskPercent, 0.1),
      10,
    ),
    defaultOrderType: normalizeOrderType(current.defaultOrderType, defaultPreferences.defaultOrderType),
    defaultExecutionIntent: normalizeExecutionIntent(
      current.defaultExecutionIntent,
      defaultPreferences.defaultExecutionIntent,
    ),
    regularHoursOnly: legacyProfile ? false : current.regularHoursOnly === true,
    openingRangeMinutes: clampNumber(
      current.openingRangeMinutes,
      defaultPreferences.openingRangeMinutes,
      5,
      60,
    ),
    intradayEventGuardMinutes: clampNumber(
      current.intradayEventGuardMinutes,
      defaultPreferences.intradayEventGuardMinutes,
      0,
      180,
    ),
    flattenBeforeCloseMinutes: clampNumber(
      current.flattenBeforeCloseMinutes,
      defaultPreferences.flattenBeforeCloseMinutes,
      1,
      60,
    ),
    capitalPreservationMode:
      current.capitalPreservationMode !== undefined
        ? Boolean(current.capitalPreservationMode)
        : defaultPreferences.capitalPreservationMode,
    tinyAccountMode: Boolean(current.tinyAccountMode),
    fractionalSharesOnlyMode:
      current.fractionalSharesOnlyMode !== undefined
        ? Boolean(current.fractionalSharesOnlyMode)
        : Boolean(current.tinyAccountMode),
    promotionGateMode:
      current.promotionGateMode !== undefined
        ? Boolean(current.promotionGateMode)
        : defaultPreferences.promotionGateMode,
    promotionGateMinResolved: clampNumber(
      current.promotionGateMinResolved,
      defaultPreferences.promotionGateMinResolved,
      1,
      50,
    ),
    promotionGateMinWinRatePercent: clampNumber(
      current.promotionGateMinWinRatePercent,
      defaultPreferences.promotionGateMinWinRatePercent,
      1,
      100,
    ),
    promotionGateMaxAverageAbsSlippageBps: Math.min(
      Math.max(
        Number(
          current.promotionGateMaxAverageAbsSlippageBps ??
            defaultPreferences.promotionGateMaxAverageAbsSlippageBps,
        ) || defaultPreferences.promotionGateMaxAverageAbsSlippageBps,
        1,
      ),
      1000,
    ),
    promotionGateMaxWorstAbsSlippageBps: Math.min(
      Math.max(
        Number(
          current.promotionGateMaxWorstAbsSlippageBps ??
            defaultPreferences.promotionGateMaxWorstAbsSlippageBps,
        ) || defaultPreferences.promotionGateMaxWorstAbsSlippageBps,
        1,
      ),
      1000,
    ),
    maxOpenPositions: clampNumber(
      current.maxOpenPositions,
      defaultPreferences.maxOpenPositions,
      1,
      10,
    ),
    maxNotionalPerTrade: Math.min(
      Math.max(Number(current.maxNotionalPerTrade ?? defaultPreferences.maxNotionalPerTrade) || defaultPreferences.maxNotionalPerTrade, 1),
      1000000,
    ),
    equitiesOnlyMode:
      current.equitiesOnlyMode !== undefined
        ? Boolean(current.equitiesOnlyMode)
        : defaultPreferences.equitiesOnlyMode,
    limitOrdersOnlyMode:
      current.limitOrdersOnlyMode !== undefined
        ? Boolean(current.limitOrdersOnlyMode)
        : defaultPreferences.limitOrdersOnlyMode,
    longOnlyMode:
      current.longOnlyMode !== undefined
        ? Boolean(current.longOnlyMode)
        : defaultPreferences.longOnlyMode,
    breakevenAfterR: Math.min(
      Math.max(Number(current.breakevenAfterR ?? defaultPreferences.breakevenAfterR) || defaultPreferences.breakevenAfterR, 0.5),
      10,
    ),
    firstTargetR: Math.min(
      Math.max(Number(current.firstTargetR ?? defaultPreferences.firstTargetR) || defaultPreferences.firstTargetR, 0.5),
      10,
    ),
    firstTrimPercent: clampNumber(current.firstTrimPercent, defaultPreferences.firstTrimPercent, 1, 100),
    secondTargetR: Math.min(
      Math.max(Number(current.secondTargetR ?? defaultPreferences.secondTargetR) || defaultPreferences.secondTargetR, 0.5),
      20,
    ),
    secondTrimPercent: clampNumber(current.secondTrimPercent, defaultPreferences.secondTrimPercent, 1, 100),
    maxDailyLossR: Math.min(
      Math.max(Number(current.maxDailyLossR ?? defaultPreferences.maxDailyLossR) || defaultPreferences.maxDailyLossR, 0.5),
      10,
    ),
    maxConsecutiveLosses: clampNumber(
      current.maxConsecutiveLosses,
      defaultPreferences.maxConsecutiveLosses,
      1,
      10,
    ),
  }
}

function preferencesNeedSync(current, normalized) {
  if (!current || typeof current !== 'object') return true
  const currentKeys = Object.keys(current)
  const normalizedKeys = Object.keys(normalized)
  if (currentKeys.length !== normalizedKeys.length) return true
  return normalizedKeys.some((key) => {
    const currentValue = current[key]
    const normalizedValue = normalized[key]
    if (Array.isArray(currentValue) || Array.isArray(normalizedValue)) {
      return JSON.stringify(currentValue || []) !== JSON.stringify(normalizedValue || [])
    }
    return currentValue !== normalizedValue
  })
}

export function PreferencesProvider({ children }) {
  const [preferences, setPreferences] = useLocalStorage(PREFERENCES_STORAGE_KEY, defaultPreferences)

  useEffect(() => {
    try {
      window.localStorage.removeItem(LEGACY_PREFERENCES_STORAGE_KEY)
      window.localStorage.removeItem('tradeview-chart-layouts-v1')
      window.localStorage.removeItem('tradeview-execution-checklist-v1')
    } catch {
      // ignore storage failures
    }
  }, [])

  useEffect(() => {
    const normalized = normalizePreferences(preferences)
    if (preferencesNeedSync(preferences, normalized)) {
      setPreferences(normalized)
    }
  }, [preferences, setPreferences])

  const value = useMemo(() => ({
    preferences: normalizePreferences(preferences),
    setPreference: (key, nextValue) => setPreferences((state) => ({ ...state, [key]: nextValue })),
    applyPreferences: (partial) => setPreferences((state) => ({ ...state, ...(partial || {}) })),
    resetPreferences: () => setPreferences(defaultPreferences),
  }), [preferences, setPreferences])

  return <PreferencesContext.Provider value={value}>{children}</PreferencesContext.Provider>
}

export function usePreferences() {
  const context = useContext(PreferencesContext)
  if (!context) {
    throw new Error('usePreferences must be used inside PreferencesProvider.')
  }
  return context
}
