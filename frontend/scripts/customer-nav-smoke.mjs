import assert from 'node:assert/strict'
import {
  CUSTOMER_NAV_ITEMS,
  getShellNavItems,
  hasAdminSurfaceAccess,
  isAdminOnlyPath,
} from '../src/utils/navigationModel.js'

const customerItems = getShellNavItems({ permissionMap: {} })
const customerLabels = customerItems.map((item) => item.label)
const expectedLabels = [
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
]

assert.deepEqual(customerLabels, expectedLabels, 'standard customer nav should be trader-operator only')
assert.equal(customerItems.some((item) => item.label === 'Admin / Advanced'), false)

const hiddenLabels = [
  'Activity',
  'Workspaces',
  'Organizations',
  'Release',
  'Operator guide',
  'Strategy desks',
  'Systematic',
  'Support console',
]
for (const label of hiddenLabels) {
  assert.equal(customerLabels.includes(label), false, `${label} should not appear in customer nav`)
}

const adminItems = getShellNavItems({ permissionMap: { 'tenant.manage_billing': true } })
assert.equal(hasAdminSurfaceAccess({ 'tenant.manage_billing': true }), true)
assert.equal(adminItems.some((item) => item.label === 'Admin / Advanced'), true)

for (const path of ['/admin', '/activity', '/workspaces', '/release', '/education', '/strategy-desks/systematic-equities']) {
  assert.equal(isAdminOnlyPath(path), true, `${path} should be admin-only`)
}

assert.equal(CUSTOMER_NAV_ITEMS.length, expectedLabels.length)
console.log('customer-nav-smoke passed')
