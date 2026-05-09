export const DEFAULT_VISUAL_FOCUS_MODE = 'decision_focus'
export const FULL_CONSOLE_FOCUS_MODE = 'full_console'

export const FOCUS_RAIL_KEYS = [
  'watchlist',
  'risk',
  'audit',
  'execution',
  'trades',
  'automation',
  'notes',
]

const FOCUS_RAIL_KEY_SET = new Set(FOCUS_RAIL_KEYS)

export function normalizeVisualFocusMode(value, fallback = DEFAULT_VISUAL_FOCUS_MODE) {
  const normalized = String(value || '').trim().toLowerCase()
  return normalized === FULL_CONSOLE_FOCUS_MODE || normalized === DEFAULT_VISUAL_FOCUS_MODE
    ? normalized
    : fallback
}

export function normalizeFocusRailKey(value, fallback = '') {
  const normalized = String(value || '').trim().toLowerCase()
  return FOCUS_RAIL_KEY_SET.has(normalized) ? normalized : fallback
}

export function normalizePinnedFocusRails(value, fallback = ['risk', 'automation']) {
  const source = Array.isArray(value)
    ? value
    : String(value || '')
      .split(',')
      .map((item) => item.trim())
  const normalized = []
  source.forEach((item) => {
    const key = normalizeFocusRailKey(item)
    if (key && !normalized.includes(key)) normalized.push(key)
  })
  return normalized.length ? normalized : [...fallback]
}

function toNumber(value, fallback = 0) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : fallback
}

function buildTicker(location, preferences = {}) {
  const params = new URLSearchParams(location?.search || '')
  return String(params.get('ticker') || params.get('focusTicker') || preferences.defaultTicker || 'SPY')
    .trim()
    .toUpperCase()
}

function buildRouteLabel(value) {
  const normalized = String(value || 'desk').trim().toLowerCase()
  if (normalized === 'broker_live') return 'Alpaca live'
  if (normalized === 'broker_paper') return 'Alpaca paper'
  return 'Desk only'
}

function buildProfileModeLabel(profile) {
  const normalized = String(profile || '').trim().toLowerCase()
  if (normalized === 'personal_live') return 'Manual live'
  if (normalized === 'brokerage') return 'Linked account'
  return 'Paper-first'
}

function buildNextAction(pathname = '/', ticker = '') {
  if (pathname === '/' || pathname === '/app') return ticker ? `Stage ${ticker} only if gates pass` : 'Load one ticker'
  if (pathname === '/watchlist') return 'Pick one leader'
  if (pathname === '/compare') return 'Qualify best setup'
  if (pathname === '/trades') return 'Clean open risk'
  if (pathname === '/portfolio') return 'Check exposure'
  if (pathname === '/strategies') return 'Review readiness'
  if (pathname.startsWith('/strategies/') && pathname.endsWith('/live')) return 'Arm only after gates'
  if (pathname === '/live') return 'Check live sessions'
  if (pathname === '/live/approvals') return 'Approve or reject'
  if (pathname === '/risk') return 'Confirm limits'
  if (pathname === '/audit') return 'Replay evidence'
  if (pathname === '/execution-quality') return 'Inspect fill drift'
  if (pathname === '/settings') return 'Tune safety defaults'
  return ticker ? `Stage ${ticker} only if gates pass` : 'Load one ticker'
}

function buildRailActionLabel(label = '') {
  const normalized = String(label || '').trim()
  if (!normalized) return 'Open panel'
  return `Open ${normalized}`
}

function hasCriticalBlocker(blockers = [], railKey = '') {
  return Boolean(findCriticalBlocker(blockers, railKey))
}

function findCriticalBlocker(blockers = [], railKey = '') {
  return blockers.find((blocker) => {
    const severity = String(blocker?.severity || blocker?.tone || '').trim().toLowerCase()
    const key = String(blocker?.railKey || blocker?.rail_key || '').trim().toLowerCase()
    return severity === 'critical' && (!key || key === railKey)
  })
}

function buildBlocker({ railKey, message, nextAction, source = 'local_preferences' }) {
  return {
    railKey,
    severity: 'critical',
    message,
    nextAction,
    source,
  }
}

export function buildFocusBlockers({
  preferences = {},
  activeAccountProfile = '',
} = {}) {
  const blockers = []
  const executionIntent = String(preferences.defaultExecutionIntent || 'desk').trim().toLowerCase()
  const profile = String(activeAccountProfile || preferences.activeAccountProfile || '').trim().toLowerCase()
  const connectedLiveSelected = executionIntent === 'broker_live' || profile === 'personal_live'

  if (profile === 'brokerage' && !String(preferences.primaryBrokerageLinkedAccountId || '').trim()) {
    blockers.push(buildBlocker({
      railKey: 'automation',
      message: 'Primary linked account is not bound.',
      nextAction: 'Open Account Setup and bind one linked account before connected-account routing.',
    }))
  }

  if (connectedLiveSelected && preferences.capitalPreservationMode !== true) {
    blockers.push(buildBlocker({
      railKey: 'risk',
      message: 'Capital preservation is off while Alpaca live is selected.',
      nextAction: 'Turn on capital preservation before any live-control path can be treated as ready.',
    }))
  }

  if (connectedLiveSelected && preferences.promotionGateMode !== true) {
    blockers.push(buildBlocker({
      railKey: 'automation',
      message: 'Paper gate is off while Alpaca live is selected.',
      nextAction: 'Enable the paper gate so readiness evidence stays ahead of live-control actions.',
    }))
  }

  return blockers
}

export function buildDecisionRibbonModel({
  location,
  preferences = {},
  activeAccountProfile = '',
  currentPage = null,
  blockers = [],
} = {}) {
  const ticker = buildTicker(location, preferences)
  const routeLabel = buildRouteLabel(preferences.defaultExecutionIntent)
  const profileMode = buildProfileModeLabel(activeAccountProfile)
  const liveRouteSelected = String(preferences.defaultExecutionIntent || '').trim().toLowerCase() === 'broker_live'
  const criticalBlocker = blockers.find((blocker) => String(blocker?.severity || blocker?.tone || '').trim().toLowerCase() === 'critical')
  const blockerCount = blockers.filter((blocker) => String(blocker?.severity || blocker?.tone || '').trim().toLowerCase() === 'critical').length
  const readinessLabel = liveRouteSelected ? 'Live gate required' : 'Paper safe'
  const systemLabel = criticalBlocker ? 'Needs attention' : 'System healthy'
  const pathname = location?.pathname || '/'
  const focusModeLabel = 'Decision Focus'
  const focusModeDetail = 'Only the active decision path stays sharp; rails expand when pinned, opened, or blocking.'

  return {
    ticker,
    mode: profileMode,
    routeLabel,
    nextAction: buildNextAction(pathname, ticker),
    blockerCount,
    safeStateSummary: blockerCount ? `${blockerCount} critical blocker${blockerCount === 1 ? '' : 's'}` : 'No critical blockers',
    focusModeLabel,
    focusModeDetail,
    keyboardHint: 'Rails: Tab to focus, Enter to expand.',
    system: {
      label: systemLabel,
      tone: criticalBlocker ? 'negative' : 'positive',
      detail: criticalBlocker?.message || 'System healthy. Background checks stay quiet unless they block the next action.',
    },
    items: [
      { key: 'symbol', label: 'Symbol', value: ticker, tone: 'info' },
      { key: 'mode', label: 'Mode', value: profileMode, tone: profileMode === 'Manual live' ? 'warning' : 'positive' },
      { key: 'readiness', label: 'Readiness', value: readinessLabel, tone: liveRouteSelected ? 'warning' : 'positive' },
      { key: 'risk', label: 'Risk', value: `${toNumber(preferences.defaultRiskPercent, 0.5).toFixed(2)}% / trade`, tone: 'info' },
      { key: 'route', label: 'Route', value: routeLabel, tone: liveRouteSelected ? 'warning' : 'info' },
      { key: 'action', label: 'Next', value: criticalBlocker?.nextAction || buildNextAction(pathname, ticker), tone: criticalBlocker ? 'negative' : 'accent' },
    ],
    pageLabel: currentPage?.label || 'Desk',
  }
}

export function buildFocusRailItems({
  location,
  preferences = {},
  expandedFocusRail = '',
  pinnedFocusRails = [],
  visualFocusMode = DEFAULT_VISUAL_FOCUS_MODE,
  blockers = [],
} = {}) {
  const mode = normalizeVisualFocusMode(visualFocusMode)
  const expandedKey = normalizeFocusRailKey(expandedFocusRail)
  const pinnedKeys = normalizePinnedFocusRails(pinnedFocusRails)
  const watchlistCount = String(preferences.watchlistTickers || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean).length
  const routeLabel = buildRouteLabel(preferences.defaultExecutionIntent)
  const fullConsole = mode === FULL_CONSOLE_FOCUS_MODE
  const pathname = location?.pathname || '/'

  const baseItems = [
    {
      key: 'watchlist',
      label: 'Research',
      to: '/watchlist',
      tone: 'info',
      count: watchlistCount,
      next: `${watchlistCount || 0} symbols ready`,
      metaLabel: watchlistCount ? `${watchlistCount} symbols` : 'No symbols',
      stateLabel: watchlistCount ? 'Ready' : 'Empty',
      detail: 'Scan stays compressed until a candidate needs comparison.',
    },
    {
      key: 'risk',
      label: 'Risk gates',
      to: '/risk',
      tone: String(preferences.defaultExecutionIntent || '').trim().toLowerCase() === 'broker_live' ? 'warning' : 'positive',
      count: toNumber(preferences.maxOpenPositions, 1),
      next: `${toNumber(preferences.maxDailyLossR, 1.5).toFixed(1)}R daily stop`,
      metaLabel: `${toNumber(preferences.maxOpenPositions, 1)} max open`,
      stateLabel: 'Guarded',
      detail: 'Risk remains visible because it can block the next trade.',
    },
    {
      key: 'audit',
      label: 'Audit trail',
      to: '/audit',
      tone: 'info',
      count: 0,
      next: 'Replay evidence on demand',
      metaLabel: 'Quiet',
      stateLabel: 'Quiet',
      detail: 'Decision evidence expands when a trade needs explanation.',
    },
    {
      key: 'execution',
      label: 'Execution evidence',
      to: '/execution-quality',
      tone: 'accent',
      count: 0,
      next: 'Fill drift compressed',
      metaLabel: 'Quiet',
      stateLabel: 'Quiet',
      detail: 'Slippage and route quality stay quiet until review matters.',
    },
    {
      key: 'trades',
      label: 'Trades',
      to: '/trades',
      tone: 'info',
      count: 0,
      next: 'Open risk first',
      metaLabel: 'Book quiet',
      stateLabel: 'Quiet',
      detail: 'The trade book expands when a position needs cleanup.',
    },
    {
      key: 'automation',
      label: 'Automation',
      to: '/live',
      tone: routeLabel === 'Alpaca live' ? 'warning' : 'positive',
      count: 1,
      next: routeLabel,
      metaLabel: '1 route',
      stateLabel: routeLabel === 'Alpaca live' ? 'Review' : 'Ready',
      detail: 'Automation state is a rail unless it blocks or requires approval.',
    },
    {
      key: 'notes',
      label: 'Notes',
      to: '/settings',
      tone: 'neutral',
      count: 0,
      next: 'Settings and defaults',
      metaLabel: 'Defaults',
      stateLabel: 'Available',
      detail: 'Notes and settings are available without competing with the decision path.',
    },
  ]

  return baseItems.map((item) => {
    const criticalBlocker = findCriticalBlocker(blockers, item.key)
    const critical = hasCriticalBlocker(blockers, item.key)
    const current = pathname === item.to || pathname.startsWith(`${item.to}/`)
    return {
      ...item,
      tone: critical ? 'negative' : item.tone,
      next: criticalBlocker?.message || item.next,
      detail: criticalBlocker?.nextAction || item.detail,
      metaLabel: critical ? 'Blocking' : item.metaLabel,
      stateLabel: critical ? 'Blocking' : item.stateLabel,
      blocker: criticalBlocker || null,
      actionLabel: buildRailActionLabel(item.label),
      shortcutLabel: item.key === 'risk' ? 'Alt+R' : item.key === 'audit' ? 'Alt+A' : item.key === 'execution' ? 'Alt+E' : '',
      emptyState: item.count ? '' : `${item.label} is quiet; nothing needs the decision path right now.`,
      current,
      pinned: pinnedKeys.includes(item.key),
      critical,
      expanded: fullConsole || critical || expandedKey === item.key,
      forceVisible: critical,
    }
  })
}
