const ACCOUNT_PROFILE_DEFINITIONS = {
  brokerage: {
    key: 'brokerage',
    label: 'Brokerage',
    badgeLabel: 'Brokerage',
    settingsTitle: 'Brokerage account',
    settingsDescription:
      'Manage brokerage-linked account surfaces and account-management controls separately from the personal trading systems.',
    executionIntentOverride: null,
  },
  personal_paper: {
    key: 'personal_paper',
    label: 'Personal / Paper',
    badgeLabel: 'Personal / Paper',
    settingsTitle: 'Personal paper trading system',
    settingsDescription:
      'Tune the personal paper trading system, watchlist behavior, and session defaults while keeping live capital separate.',
    executionIntentOverride: 'broker_paper',
  },
  personal_live: {
    key: 'personal_live',
    label: 'Personal / Live',
    badgeLabel: 'Personal / Live',
    settingsTitle: 'Personal live trading system',
    settingsDescription:
      'Tune the personal live trading system, rollout-sensitive route posture, and live-capital defaults without mixing it with brokerage account management.',
    executionIntentOverride: 'broker_live',
  },
}

export const ACCOUNT_PROFILE_FALLBACK = 'personal_paper'
const SUPPORTED_ACCOUNT_PROFILES = new Set(Object.keys(ACCOUNT_PROFILE_DEFINITIONS))
export const PRIMARY_BROKERAGE_LINKED_ACCOUNT_FALLBACK = ''

export function normalizeAccountProfile(value, fallback = ACCOUNT_PROFILE_FALLBACK) {
  const normalized = String(value || '').trim().toLowerCase()
  return SUPPORTED_ACCOUNT_PROFILES.has(normalized) ? normalized : fallback
}

export function normalizePrimaryBrokerageLinkedAccountId(
  value,
  fallback = PRIMARY_BROKERAGE_LINKED_ACCOUNT_FALLBACK,
) {
  const normalized = String(value || '').trim()
  return normalized || fallback
}

export function getAccountProfileDefinition(profile) {
  return ACCOUNT_PROFILE_DEFINITIONS[normalizeAccountProfile(profile)]
}

export function getAccountProfileOptions() {
  return Object.values(ACCOUNT_PROFILE_DEFINITIONS)
}

export function isPersonalAccountProfile(profile) {
  const normalized = normalizeAccountProfile(profile)
  return normalized === 'personal_paper' || normalized === 'personal_live'
}

export function resolveAccountProfileExecutionIntent({
  activeAccountProfile,
  defaultExecutionIntent = 'desk',
}) {
  const profile = getAccountProfileDefinition(activeAccountProfile)
  return profile.executionIntentOverride || String(defaultExecutionIntent || 'desk').trim().toLowerCase() || 'desk'
}

function hasHealthyLinkedAccountConnection(account) {
  if (!account || typeof account !== 'object') return false
  const connectionStatus = String(account.connection_status || '').trim().toLowerCase()
  const tokenHealth = String(account.token_health || '').trim().toLowerCase()
  const relinkRequired = Boolean(account.relink_required)
  return connectionStatus === 'connected' && !relinkRequired && ['healthy', 'unknown'].includes(tokenHealth)
}

export function resolveAccountProfileTradingContext({
  activeAccountProfile,
  defaultExecutionIntent = 'desk',
  primaryBrokerageLinkedAccountId = '',
  linkedAccounts = [],
}) {
  const normalizedProfile = normalizeAccountProfile(activeAccountProfile)
  const effectiveExecutionIntent = resolveAccountProfileExecutionIntent({
    activeAccountProfile: normalizedProfile,
    defaultExecutionIntent,
  })
  const normalizedPrimaryLinkedAccountId = normalizePrimaryBrokerageLinkedAccountId(
    primaryBrokerageLinkedAccountId,
  )
  const items = Array.isArray(linkedAccounts) ? linkedAccounts : []
  const boundBrokerageAccount =
    items.find((account) => String(account?.id || '').trim() === normalizedPrimaryLinkedAccountId) || null

  if (normalizedProfile === 'brokerage') {
    if (!normalizedPrimaryLinkedAccountId) {
      return {
        activeProfile: normalizedProfile,
        effectiveExecutionIntent,
        effectiveAccountTargetType: 'linked_client',
        effectiveLinkedAccountId: '',
        accountTargetLocked: true,
        accountTargetValue: 'unbound',
        accountTargetLabel: 'No primary brokerage account bound',
        accountTargetHint:
          'Orders route only to the bound brokerage account while Brokerage is active. Bind a primary brokerage account in Brokerage settings first.',
        profileTradingLockedReason:
          'Bind a primary brokerage account in Brokerage settings before submitting trades from the Brokerage profile.',
        executionRouteOverride: {
          tone: 'negative',
          label: 'Brokerage account required',
          detail:
            'Brokerage trading is locked because no primary broker account is bound to the Brokerage profile yet.',
          locked: true,
          lockedLabel: 'Brokerage account required',
          sendLabel: 'brokerage approval request',
          badgeLabel: 'Locked',
          pathLabel: 'Bind broker account',
        },
      }
    }

    if (!boundBrokerageAccount || !hasHealthyLinkedAccountConnection(boundBrokerageAccount)) {
      return {
        activeProfile: normalizedProfile,
        effectiveExecutionIntent,
        effectiveAccountTargetType: 'linked_client',
        effectiveLinkedAccountId: normalizedPrimaryLinkedAccountId,
        accountTargetLocked: true,
        accountTargetValue: normalizedPrimaryLinkedAccountId || 'unavailable',
        accountTargetLabel:
          boundBrokerageAccount?.label || 'Primary broker account unavailable',
        accountTargetHint:
          'Orders route only to the bound brokerage account while Brokerage is active. Reconnect or rebind the primary brokerage account before trading.',
        profileTradingLockedReason:
          'The primary brokerage account is unavailable. Reconnect it or choose another linked brokerage account in Brokerage settings before trading.',
        executionRouteOverride: {
          tone: 'negative',
          label: 'Brokerage account unavailable',
          detail:
            'Brokerage trading is locked because the bound broker account is disconnected or needs to be relinked.',
          locked: true,
          lockedLabel: 'Brokerage account unavailable',
          sendLabel: 'brokerage approval request',
          badgeLabel: 'Locked',
          pathLabel: 'Reconnect broker account',
        },
      }
    }

    const boundLabel = String(
      boundBrokerageAccount.label ||
        boundBrokerageAccount.linked_identity_label ||
        'Bound broker account',
    ).trim()

    return {
      activeProfile: normalizedProfile,
      effectiveExecutionIntent,
      effectiveAccountTargetType: 'linked_client',
      effectiveLinkedAccountId: boundBrokerageAccount.id,
      accountTargetLocked: true,
      accountTargetValue: boundBrokerageAccount.id,
      accountTargetLabel: boundLabel,
      accountTargetHint: `Orders route only to ${boundLabel} while Brokerage is active. Switch the global profile to Personal / Paper or Personal / Live to use your own-account funds.`,
      profileTradingLockedReason: '',
      executionRouteOverride: {
        tone: 'positive',
        label: 'Brokerage account',
        detail: `Orders route only to ${boundLabel} while the Brokerage profile is active.`,
        locked: false,
        lockedLabel: '',
        sendLabel: 'brokerage approval request',
        badgeLabel: 'Brokerage',
        pathLabel: boundLabel,
      },
      boundBrokerageAccount,
    }
  }

  const profile = getAccountProfileDefinition(normalizedProfile)
  return {
    activeProfile: normalizedProfile,
    effectiveExecutionIntent,
    effectiveAccountTargetType: 'personal',
    effectiveLinkedAccountId: '',
    accountTargetLocked: true,
    accountTargetValue: 'personal',
    accountTargetLabel: 'Personal env-backed lane',
    accountTargetHint: `Orders route only to the personal lane while ${profile.badgeLabel} is active.`,
    profileTradingLockedReason: '',
    executionRouteOverride: null,
    boundBrokerageAccount: null,
  }
}
