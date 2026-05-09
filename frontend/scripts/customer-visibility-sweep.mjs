import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  CUSTOMER_NAV_ITEMS,
  getShellNavItems,
  isAdminOnlyPath,
} from '../src/utils/navigationModel.js'
import {
  PUBLIC_PLAN_KEYS,
  STATIC_PRICING_PLANS,
  formatPlanPrice,
  normalizePricingPlans,
} from '../src/utils/pricingModel.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const frontendRoot = resolve(__dirname, '..')

function readProjectFile(path) {
  return readFileSync(resolve(frontendRoot, path), 'utf8')
}

function walk(dir, matcher, results = []) {
  for (const entry of readdirSync(dir)) {
    const fullPath = join(dir, entry)
    const stat = statSync(fullPath)
    if (stat.isDirectory()) {
      walk(fullPath, matcher, results)
    } else if (matcher(fullPath)) {
      results.push(fullPath)
    }
  }
  return results
}

const customerNavLabels = getShellNavItems({ permissionMap: {} }).map((item) => item.label)
assert.deepEqual(customerNavLabels, [
  'Desk',
  'Research',
  'Compare',
  'Trades',
  'Portfolio',
  'Strategies',
  'Live Console',
  'Live Approvals',
  'Risk',
  'Audit Replay',
  'Execution Quality',
  'Settings',
])
assert.equal(CUSTOMER_NAV_ITEMS.some((item) => item.label === 'Admin / Advanced'), false)

for (const path of ['/activity', '/workspaces', '/release', '/education', '/strategy-desks']) {
  assert.equal(isAdminOnlyPath(path), true, `${path} should stay out of customer navigation`)
}

const appSource = readProjectFile('src/App.jsx')
for (const route of ['/education', '/strategy-desks', '/admin', '/release']) {
  assert.match(appSource, new RegExp(`path="${route.replace('/', '\\/')}"[\\s\\S]{0,120}AdminAccessGate`))
}

const forbiddenCustomerCopy = [
  /native broker/i,
  /proprietary broker/i,
  /broker-dealer/i,
  /brokerage platform/i,
  /premium brokerage/i,
  /internal broker/i,
  /broker-live pilot/i,
  /platform operations/i,
  /launch ops/i,
  /feature rollout controls/i,
  /billing recovery/i,
  /not a broker-dealer/i,
  /\bTradier\b/i,
  /Connected paper/i,
  /Connected live/i,
  /Paper execution router/i,
  /Live broker/i,
  /Brokerage-linked/i,
  /Brokerage profile/i,
]

const customerPaths = [
  'src/App.jsx',
  'src/components/AppShell.jsx',
  'src/components/AdminAccessGate.jsx',
  'src/pages/OwnAccountSettingsPage.jsx',
  'src/pages/DashboardPage.jsx',
  'src/pages/PricingPage.jsx',
  'src/pages/LiveTradingConsolePage.jsx',
  'src/pages/LiveStrategyControlPage.jsx',
  'src/pages/LiveOrderApprovalPage.jsx',
  'src/pages/StrategiesPage.jsx',
  'src/pages/StrategyDetailPage.jsx',
  'src/pages/RiskCenterPage.jsx',
  'src/pages/AuditReplayPage.jsx',
  'src/pages/ExecutionQualityPage.jsx',
  'src/utils/accountProfileModel.js',
  'src/utils/navigationModel.js',
  'src/utils/pricingModel.js',
]

const customerDirectories = [
  'src/components/live',
  'src/components/pricing',
  'src/components/risk',
  'src/components/audit',
  'src/components/execution',
  'src/components/strategy',
]

const customerFiles = [
  ...customerPaths.map((path) => resolve(frontendRoot, path)),
  ...customerDirectories.flatMap((path) =>
    walk(resolve(frontendRoot, path), (filePath) => /\.(jsx|js)$/.test(filePath)),
  ),
]

const copyFailures = []
for (const filePath of customerFiles) {
  const source = readFileSync(filePath, 'utf8')
  for (const pattern of forbiddenCustomerCopy) {
    if (pattern.test(source)) {
      copyFailures.push(`${relative(frontendRoot, filePath)} :: ${pattern}`)
    }
  }
}
assert.deepEqual(copyFailures, [], `customer-facing copy drift:\n${copyFailures.join('\n')}`)

const debuggerFailures = []
for (const filePath of customerFiles) {
  const source = readFileSync(filePath, 'utf8')
  if (/\bdebugger\b/.test(source) || /console\.(log|debug|trace)\(/.test(source)) {
    debuggerFailures.push(relative(frontendRoot, filePath))
  }
}
assert.deepEqual(debuggerFailures, [], `customer-facing debug statements:\n${debuggerFailures.join('\n')}`)

const appRoutes = readProjectFile('src/App.jsx')
assert.match(appRoutes, /isPricingRoute/)
assert.match(appRoutes, /<PricingPage \/>/)
for (const route of ['/live', '/live/approvals', '/strategies/:strategyId/live']) {
  assert.match(appRoutes, new RegExp(`path="${route.replace('/', '\\/')}"`), `${route} should stay routed`)
}
assert.doesNotMatch(appRoutes, /visualFocusMode !== 'full_console'/, 'normal shell should not hide workflow status behind focus mode')

const appShell = readProjectFile('src/components/AppShell.jsx')
assert.doesNotMatch(appShell, /FocusApertureFrame/)
assert.doesNotMatch(appShell, /focus-aperture/)
assert.match(appShell, /appConfig\.showAdminSurfaces/)
assert.doesNotMatch(appShell, /Local demo/)

const appConfigSource = readProjectFile('src/config/appConfig.js')
assert.match(appConfigSource, /VITE_CUSTOMER_READY_MODE/)
assert.match(appConfigSource, /'true'\)\.toLowerCase\(\) !== 'false'/)
assert.match(appConfigSource, /VITE_SHOW_ADMIN_SURFACES/)
assert.match(appConfigSource, /'false'\)\.toLowerCase\(\) === 'true'/)

const authContext = readProjectFile('src/context/AuthContext.jsx')
assert.match(authContext, /CUSTOMER_DEMO_PERMISSIONS/)
assert.match(authContext, /applyCustomerReadySession/)
assert.match(authContext, /customer_preview/)

const settingsPage = readProjectFile('src/pages/SettingsPage.jsx')
assert.match(settingsPage, /appConfig\.customerReadyMode/)
assert.match(settingsPage, /Linked Alpaca accounts/)
assert.match(settingsPage, /Account setup/)
assert.doesNotMatch(settingsPage, /billing recovery/i)

const liveConsole = readProjectFile('src/pages/LiveTradingConsolePage.jsx')
const liveStrategy = readProjectFile('src/pages/LiveStrategyControlPage.jsx')
const liveApprovals = readProjectFile('src/pages/LiveOrderApprovalPage.jsx')
assert.match(liveConsole, /LiveModeStrip/)
assert.match(liveStrategy, /LiveModeStrip/)
assert.match(liveApprovals, /LiveModeStrip/)

const publicPlans = normalizePricingPlans(STATIC_PRICING_PLANS)
assert.deepEqual(publicPlans.map((plan) => plan.key), ['starter', 'pro', 'professional', 'team', 'enterprise'])
assert.equal(PUBLIC_PLAN_KEYS.includes('white-label'), false)
const professional = publicPlans.find((plan) => plan.key === 'professional')
assert.equal(professional?.recommended, true)
assert.equal(formatPlanPrice(professional, 'monthly'), '$499/mo')
assert.equal(formatPlanPrice(professional, 'annual'), '$4,990/yr')
assert.equal(formatPlanPrice(publicPlans.find((plan) => plan.key === 'starter'), 'annual'), '$990/yr')
assert.equal(formatPlanPrice(publicPlans.find((plan) => plan.key === 'team'), 'annual'), '$8,990/yr')
const pricingSource = readProjectFile('src/pages/PricingPage.jsx')
assert.doesNotMatch(pricingSource, /white-label/i)
assert.match(pricingSource, /Paper-validated live automation control/)
assert.match(readProjectFile('src/utils/pricingModel.js'), /Managed automation/)

const foundationStyles = readProjectFile('src/styles/foundation.css')
for (const token of ['--accent-1', '--accent-2', '--accent-3', '--negative', '--bg-0']) {
  assert.match(foundationStyles, new RegExp(token.replace('-', '\\-')), `${token} should remain in the palette`)
}

console.log('customer-visibility-sweep passed')
