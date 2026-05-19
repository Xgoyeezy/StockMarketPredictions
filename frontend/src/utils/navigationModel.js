export const ADMIN_PERMISSION_KEYS = [
  'tenant.admin',
  'tenant.owner',
  'tenant.manage_billing',
  'tenant.manage_members',
  'tenant.manage_branding',
  'tenant.manage_delivery',
  'tenant.manage_onboarding',
  'tenant.manage_flags',
  'tenant.manage_api_tokens',
  'tenant.manage_webhooks',
  'tenant.manage_support',
  'tenant.manage_security',
  'tenant.create',
  'tenant.change_status',
  'platform.admin',
  'support.manage',
  'support.view',
]

export const CUSTOMER_NAV_ITEMS = [
  { to: '/app', label: 'Desk', kicker: 'Live' },
  { to: '/watchlist', label: 'Research', kicker: 'Scan' },
  { to: '/compare', label: 'Compare', kicker: 'Rank' },
  { to: '/trades', label: 'Trades', kicker: 'Order' },
  { to: '/portfolio', label: 'Portfolio', kicker: 'Risk' },
  { to: '/strategies', label: 'Strategies', kicker: 'Ctl' },
  { to: '/live', label: 'Live Console', kicker: 'Live' },
  { to: '/live/approvals', label: 'Live Approvals', kicker: 'Ok' },
  { to: '/risk', label: 'Risk', kicker: 'Gate' },
  { to: '/portfolio-risk', label: 'Portfolio Risk', kicker: 'Port' },
  { to: '/audit', label: 'Audit Replay', kicker: 'Trace' },
  { to: '/execution-quality', label: 'Execution Quality', kicker: 'Fill' },
  { to: '/evidence-edge', label: 'Evidence Edge', kicker: 'Edge' },
  { to: '/evidence-outcomes', label: 'Evidence Outcomes', kicker: 'Out' },
  { to: '/forecast-validation', label: 'Forecast Validation', kicker: 'Test' },
  { to: '/evidence-reward', label: 'Evidence Reward', kicker: 'Rwd' },
  { to: '/professional-benchmark', label: 'Professional Benchmark', kicker: 'Proof' },
  { to: '/data-completeness', label: 'Data Completeness', kicker: 'Data' },
  { to: '/walk-forward', label: 'Walk-Forward', kicker: 'WF' },
  { to: '/research-promotion', label: 'Research Promotion', kicker: 'Promo' },
  { to: '/score-calibration', label: 'Score Calibration', kicker: 'Cal' },
  { to: '/shadow-mode', label: 'Human vs System', kicker: 'Shdw' },
  { to: '/ai-committee', label: 'AI Committee', kicker: 'AI' },
  { to: '/category-readiness', label: '10/10 Readiness', kicker: 'Gate' },
  { to: '/proof-metrics', label: 'Proof Metrics', kicker: 'Proof' },
  { to: '/settings', label: 'Settings', kicker: 'Acct' },
]

export const ADMIN_NAV_ITEM = { to: '/admin', label: 'Admin / Advanced', kicker: 'Adv' }

export const ADMIN_ONLY_ROUTE_PREFIXES = [
  '/admin',
  '/activity',
  '/workspaces',
  '/release',
  '/education',
  '/strategy-desks',
]

export function hasAdminSurfaceAccess(permissionMap = {}) {
  if (!permissionMap || typeof permissionMap !== 'object') return false
  return ADMIN_PERMISSION_KEYS.some((key) => permissionMap[key] === true)
}

export function getShellNavItems({ permissionMap = {} } = {}) {
  const items = [...CUSTOMER_NAV_ITEMS]
  if (hasAdminSurfaceAccess(permissionMap)) {
    items.push(ADMIN_NAV_ITEM)
  }
  return items
}

export function getShellNavShortcuts(items = CUSTOMER_NAV_ITEMS) {
  const keyMap = {
    '/app': 'D',
    '/watchlist': 'W',
    '/compare': 'C',
    '/trades': 'T',
    '/portfolio': 'P',
    '/strategies': 'L',
    '/live': 'V',
    '/live/approvals': 'K',
    '/risk': 'R',
    '/portfolio-risk': 'X',
    '/audit': 'U',
    '/execution-quality': 'E',
    '/evidence-edge': 'G',
    '/evidence-outcomes': 'Q',
    '/forecast-validation': 'F',
    '/evidence-reward': 'Y',
    '/professional-benchmark': 'B',
    '/data-completeness': 'M',
    '/walk-forward': 'O',
    '/research-promotion': 'N',
    '/score-calibration': 'I',
    '/shadow-mode': 'H',
    '/ai-committee': 'Z',
    '/category-readiness': 'J',
    '/proof-metrics': '0',
    '/settings': 'S',
    '/admin': 'A',
  }
  return items.map((item) => ({
    to: item.to,
    label: item.label,
    keys: ['Alt', 'Shift', keyMap[item.to] || 'D'],
  }))
}

export function isAdminOnlyPath(pathname = '') {
  const normalized = String(pathname || '').trim() || '/'
  return ADMIN_ONLY_ROUTE_PREFIXES.some((prefix) => normalized === prefix || normalized.startsWith(`${prefix}/`))
}
