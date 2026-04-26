export const DEFAULT_INTRADAY_PRESET = 'opening_range'

export const INTRADAY_PRESET_OPTIONS = [
  { value: 'opening_range', label: 'Opening range breakout' },
  { value: 'intraday_momentum', label: 'Intraday momentum' },
  { value: 'vwap_reversion', label: 'VWAP reversion' },
  { value: 'close_cleanup', label: 'Close cleanup' },
]

export const INTRADAY_PRESET_PROFILES = {
  opening_range: {
    label: 'Opening range breakout',
    shortLabel: 'ORB',
    shellLabel: 'ORB preset',
    description: 'Front-load prep, mark the opening range, and only promote names that survive the first burst with clean liquidity.',
    startupSurface: '/',
    reviewSurface: '/journal',
    defaultTicker: 'SPY',
    watchlistTickers: 'SPY,QQQ,NVDA,TSLA,AMD,META',
    defaultInterval: '5m',
    defaultHorizon: 4,
    defaultRiskPercent: 0.25,
    openingRangeMinutes: 15,
    intradayEventGuardMinutes: 30,
    flattenBeforeCloseMinutes: 12,
    defaultOrderType: 'limit',
    defaultExecutionIntent: 'desk',
    regularHoursOnly: false,
    autoRefreshWatchlist: true,
    compactTables: true,
  },
  intraday_momentum: {
    label: 'Intraday momentum',
    shortLabel: 'Momentum',
    shellLabel: 'Momentum preset',
    description: 'Favor clean morning continuation and selective power-hour follow-through in liquid leaders.',
    startupSurface: '/',
    reviewSurface: '/trades',
    defaultTicker: 'QQQ',
    watchlistTickers: 'QQQ,NVDA,TSLA,AMD,META,SPY',
    defaultInterval: '5m',
    defaultHorizon: 5,
    defaultRiskPercent: 0.25,
    openingRangeMinutes: 10,
    intradayEventGuardMinutes: 25,
    flattenBeforeCloseMinutes: 10,
    defaultOrderType: 'limit',
    defaultExecutionIntent: 'desk',
    regularHoursOnly: false,
    autoRefreshWatchlist: true,
    compactTables: true,
  },
  vwap_reversion: {
    label: 'VWAP reversion',
    shortLabel: 'VWAP',
    shellLabel: 'VWAP preset',
    description: 'Use Compare and the desk to confirm patient mean-reversion attempts instead of chasing the first move.',
    startupSurface: '/compare',
    reviewSurface: '/journal',
    defaultTicker: 'SPY',
    watchlistTickers: 'SPY,QQQ,AAPL,MSFT,META,NVDA',
    defaultInterval: '15m',
    defaultHorizon: 4,
    defaultRiskPercent: 0.2,
    openingRangeMinutes: 20,
    intradayEventGuardMinutes: 45,
    flattenBeforeCloseMinutes: 20,
    defaultOrderType: 'limit',
    defaultExecutionIntent: 'desk',
    regularHoursOnly: false,
    autoRefreshWatchlist: true,
    compactTables: true,
  },
  close_cleanup: {
    label: 'Close cleanup',
    shortLabel: 'Cleanup',
    shellLabel: 'Cleanup preset',
    description: 'Bias the workstation toward trimming, flattening, and late-session discipline instead of fresh conviction.',
    startupSurface: '/trades',
    reviewSurface: '/notes',
    defaultTicker: 'SPY',
    watchlistTickers: 'SPY,QQQ,AAPL,MSFT,NVDA,META',
    defaultInterval: '5m',
    defaultHorizon: 2,
    defaultRiskPercent: 0.15,
    openingRangeMinutes: 15,
    intradayEventGuardMinutes: 20,
    flattenBeforeCloseMinutes: 25,
    defaultOrderType: 'limit',
    defaultExecutionIntent: 'desk',
    regularHoursOnly: false,
    autoRefreshWatchlist: true,
    compactTables: true,
  },
}

export function normalizeIntradayPreset(value, fallback = DEFAULT_INTRADAY_PRESET) {
  const normalized = String(value || '').trim().toLowerCase()
  return Object.prototype.hasOwnProperty.call(INTRADAY_PRESET_PROFILES, normalized) ? normalized : fallback
}

export function getIntradayPresetProfile(value = DEFAULT_INTRADAY_PRESET) {
  const normalized = normalizeIntradayPreset(value)
  return INTRADAY_PRESET_PROFILES[normalized] || INTRADAY_PRESET_PROFILES[DEFAULT_INTRADAY_PRESET]
}

export function buildIntradayPresetDefaults(value = DEFAULT_INTRADAY_PRESET) {
  const profile = getIntradayPresetProfile(value)
  return {
    tradingStyle: 'intraday',
    intradayPreset: normalizeIntradayPreset(value),
    startupSurface: profile.startupSurface,
    defaultReviewSurface: profile.reviewSurface,
    defaultTicker: profile.defaultTicker,
    watchlistTickers: profile.watchlistTickers,
    defaultInterval: profile.defaultInterval,
    defaultHorizon: profile.defaultHorizon,
    autoRefreshWatchlist: profile.autoRefreshWatchlist,
    compactTables: profile.compactTables,
    defaultRiskPercent: profile.defaultRiskPercent,
    regularHoursOnly: profile.regularHoursOnly,
    openingRangeMinutes: profile.openingRangeMinutes,
    intradayEventGuardMinutes: profile.intradayEventGuardMinutes,
    flattenBeforeCloseMinutes: profile.flattenBeforeCloseMinutes,
    defaultOrderType: profile.defaultOrderType,
    defaultExecutionIntent: profile.defaultExecutionIntent,
    showWorkflowStatusStrip: true,
    showWorkflowGuides: true,
    showArrivalBanners: true,
  }
}

const PAGE_GUIDES = {
  watchlist: {
    opening_range: {
      title: 'Rank the liquid board for the opening range',
      description: 'Use Watchlist to prep the open, line up the first break, and keep thin premarket noise out of the live queue.',
      helper: 'Start with liquid names, then promote only the names that still look clean after the opening burst forms.',
      actionLabel: 'Load ORB basket',
    },
    intraday_momentum: {
      title: 'Rank the liquid board for continuation',
      description: 'Use Watchlist to keep liquid momentum leaders in one queue while the morning drive and late-session follow-through stay visible.',
      helper: 'Start with relative volume and clean fills, then push only the best continuation names into Compare or the desk.',
      actionLabel: 'Load momentum basket',
    },
    vwap_reversion: {
      title: 'Rank the liquid board for reversion candidates',
      description: 'Use Watchlist to flag stretched liquid names, then hand only the cleanest reversion candidates into Compare.',
      helper: 'Start with patient names and cleaner session posture, not the loudest tape.',
      actionLabel: 'Load VWAP basket',
    },
    close_cleanup: {
      title: 'Rank the liquid board for late-session cleanup',
      description: 'Use Watchlist to decide what still deserves attention before the close and what belongs in trim-or-flatten mode.',
      helper: 'Start with live pressure and shrinking exit room, then use the board to reduce same-session complexity.',
      actionLabel: 'Load cleanup basket',
    },
  },
  compare: {
    opening_range: {
      title: 'Qualify opening-range leaders under one shared frame',
      description: 'Use Compare to decide which ORB names still deserve desk attention once the break, event pressure, and fills are read together.',
      helper: 'The best ORB candidate is the one that still looks orderly after the first burst, not the loudest print.',
    },
    intraday_momentum: {
      title: 'Qualify continuation leaders under one shared frame',
      description: 'Use Compare to stress-test momentum leaders so only the cleanest same-session continuation survives to the desk.',
      helper: 'Hold the frame constant and let fill quality, catalyst pressure, and session fit overrule raw score.',
    },
    vwap_reversion: {
      title: 'Qualify reversion setups without forcing them',
      description: 'Use Compare to decide which stretched names still have enough liquidity and session calm to support a patient VWAP reversion.',
      helper: 'The best reversion setup is the calmest clean setup, not the one furthest from VWAP.',
    },
    close_cleanup: {
      title: 'Qualify only the late-session names worth touching',
      description: 'Use Compare to filter the late-session queue down to the few names that still deserve attention before cleanup takes over.',
      helper: 'A shrinking exit window should overrule almost any pretty late-session score.',
    },
  },
  dashboard: {
    opening_range: {
      title: 'Use the desk to confirm one clean opening-range setup',
      description: 'The desk should slow the open down: confirm the break, tighten price control, and avoid turning the first burst into a chase.',
      helper: 'Keep the ticket simple, keep the order priced, and let the opening range decide whether the setup still deserves risk.',
    },
    intraday_momentum: {
      title: 'Use the desk to route one clean continuation setup',
      description: 'The desk should confirm that momentum still fits the active tape, not just the board score.',
      helper: 'Read route posture, session fit, and cleanup risk before you translate continuation into a live ticket.',
    },
    vwap_reversion: {
      title: 'Use the desk to slow down the reversion idea',
      description: 'The desk should make VWAP reversion more patient by tightening price control, cleanup bias, and stop logic.',
      helper: 'If the mean-reversion case is still too fast, the desk should push it back to review instead of route.',
    },
    close_cleanup: {
      title: 'Use the desk to clean up same-session risk',
      description: 'The desk should reduce complexity late in the session, confirm trims and exits, and block fresh conviction when the close buffer is active.',
      helper: 'Late-session work is about better exits and fewer surprises, not about inventing one more trade.',
    },
  },
  trades: {
    opening_range: {
      title: 'Manage opening-range risk before it compounds',
      description: 'Trades should show which open-driven positions still deserve room and which ones already need cleanup.',
      helper: 'Same-session review starts here when the open moved faster than the route or stop logic allowed.',
    },
    intraday_momentum: {
      title: 'Manage continuation risk before the tape fades',
      description: 'Trades should tell you whether the continuation still fits the session or whether route drift and cleanup pressure are now bigger than the signal.',
      helper: 'A live momentum position should get simpler as the session matures, not harder to explain.',
    },
    vwap_reversion: {
      title: 'Manage reversion risk with patience',
      description: 'Trades should keep VWAP reversion honest by exposing premature entries, fill drift, and exits that got too late.',
      helper: 'The best reversion management is calm, incremental, and very aware of session decay.',
    },
    close_cleanup: {
      title: 'Manage the late-session cleanup book',
      description: 'Trades should act like a cleanup surface first, with the route, risk budget, and flatten bias visible at all times.',
      helper: 'When this preset is active, same-session cleanup is the product, not a side effect.',
    },
  },
}

export function buildIntradayPresetGuide({ preset = DEFAULT_INTRADAY_PRESET, page = 'watchlist' } = {}) {
  const normalizedPreset = normalizeIntradayPreset(preset)
  const pageGuide = PAGE_GUIDES[page]?.[normalizedPreset]
  const profile = getIntradayPresetProfile(normalizedPreset)
  return {
    ...pageGuide,
    profile,
  }
}
