import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { activateOrganization, getAuthConfig, getAuthSession, login, logout, probeBackendHealthz } from '../api/client'
import { AuthContext } from './authContextObject'
import { appConfig } from '../config/appConfig'
const DEFAULT_THEME = {
  accentPrimary: '#565656',
  accentSecondary: '#2F2F2F',
  backgroundColor: '#000000',
  surfaceColor: '#111111',
  textColor: '#F5F5F5',
}
const CUSTOMER_DEMO_PERMISSIONS = [
  'audit.export',
  'automation.manage',
  'execution_analytics.read',
  'live.approve',
  'live.manage',
  'live.read',
  'market.read',
  'readiness.evaluate',
  'risk.manage',
  'strategy.manage',
  'tenant.read',
  'trade.execute',
  'workspace.write',
]
const ADMIN_DEMO_PERMISSIONS = [
  'market.read',
  'platform.admin',
  'tenant.change_status',
  'tenant.create',
  'tenant.manage_api_tokens',
  'tenant.manage_billing',
  'tenant.manage_branding',
  'tenant.manage_delivery',
  'tenant.manage_flags',
  'tenant.manage_members',
  'tenant.manage_onboarding',
  'tenant.manage_support',
  'tenant.manage_webhooks',
  'tenant.read',
  'trade.execute',
  'workspace.write',
]
const DEMO_PERMISSIONS = appConfig.showAdminSurfaces ? ADMIN_DEMO_PERMISSIONS : CUSTOMER_DEMO_PERMISSIONS

function buildDemoAuthConfig(currentSession = null, currentConfig = null) {
  const environment = currentConfig?.environment || currentSession?.environment || 'development'
  const cookieName = currentConfig?.local_session?.cookie_name || 'stocksignals_session'
  const maxAgeSeconds = currentConfig?.local_session?.max_age_seconds || 60 * 60 * 24 * 14
  const defaultPlan = currentConfig?.local_session?.default_plan || 'personal'

  return {
    enabled: false,
    demo_allowed: true,
    provider: 'local-demo',
    provider_label: 'Local Demo',
    environment,
    mode: 'demo',
    supports_login: false,
    supports_logout: false,
    supports_signup: true,
    supports_org_switch: true,
    supports_invite_claim: true,
    available_providers: [],
    local_session: {
      enabled: false,
      cookie_name: cookieName,
      max_age_seconds: maxAgeSeconds,
      allow_signup: true,
      default_plan: defaultPlan,
    },
    auth0: {
      enabled: false,
      ready: false,
    },
    oidc: {
      enabled: false,
      ready: false,
    },
  }
}

function buildDemoSession(currentSession = null, authConfig = null) {
  const permissionMap = Object.fromEntries(DEMO_PERMISSIONS.map((permission) => [permission, true]))
  const personalMode = appConfig.personalMode
  const defaultDeskName = personalMode ? appConfig.appName : 'Systematic Equities Desk'
  const defaultDeskTagline = personalMode
    ? appConfig.appTagline
    : 'Organization trading workspace'
  const defaultDeskSlug = personalMode ? 'own-account-desk' : 'systematic-equities'
  const defaultPlan = personalMode ? 'personal' : 'pro'
  const customerPreview = appConfig.customerReadyMode && !appConfig.showAdminSurfaces
  const fallbackOrganization =
    currentSession?.active_tenant ||
    currentSession?.memberships?.find((membership) => membership?.tenant)?.tenant ||
    null

  return {
    authenticated: true,
    mode: 'demo',
    provider: authConfig?.provider || currentSession?.provider || 'local-demo',
    environment: authConfig?.environment || currentSession?.environment || 'development',
    user: {
      id: currentSession?.user?.id || 'demo-trader',
      auth_subject: currentSession?.user?.auth_subject || 'demo-trader',
      email: currentSession?.user?.email || (personalMode ? 'trader@personal-desk.local' : 'demo@stocksignals.local'),
      name: currentSession?.user?.name || (personalMode ? 'Personal Trader' : 'Demo Trader'),
      role: currentSession?.user?.role || (customerPreview ? 'operator' : 'owner'),
      platform_role: currentSession?.user?.platform_role || (customerPreview ? 'trader' : 'admin'),
      permissions: currentSession?.user?.permissions || DEMO_PERMISSIONS,
      permission_map: currentSession?.user?.permission_map || permissionMap,
    },
    active_tenant:
      currentSession?.active_tenant || {
        id: fallbackOrganization?.id || 'demo-tenant',
        slug: fallbackOrganization?.slug || defaultDeskSlug,
        name: fallbackOrganization?.name || defaultDeskName,
        status: fallbackOrganization?.status || 'active',
        plan_key: fallbackOrganization?.plan_key || defaultPlan,
        role: currentSession?.active_tenant?.role || (customerPreview ? 'operator' : 'owner'),
        permissions: currentSession?.active_tenant?.permissions || DEMO_PERMISSIONS,
        permission_map: currentSession?.active_tenant?.permission_map || permissionMap,
        brand_settings:
          currentSession?.active_tenant?.brand_settings ||
          fallbackOrganization?.brand_settings || {
            app_name: defaultDeskName,
            app_tagline: defaultDeskTagline,
          },
      },
    api_token: currentSession?.api_token || null,
    memberships: currentSession?.memberships || [],
  }
}

function buildOfflineSession(currentSession = null) {
  const permissionMap = {}
  const personalMode = appConfig.personalMode
  const defaultDeskName = personalMode ? appConfig.appName : 'Systematic Equities Desk'
  const defaultDeskTagline = personalMode ? appConfig.appTagline : 'Organization trading workspace'
  const defaultDeskSlug = personalMode ? 'own-account-desk' : 'systematic-equities'
  const defaultPlan = personalMode ? 'personal' : 'pro'

  return {
    authenticated: false,
    mode: 'offline',
    provider: currentSession?.provider || 'backend-unavailable',
    environment: currentSession?.environment || 'development',
    user: {
      id: currentSession?.user?.id || null,
      auth_subject: currentSession?.user?.auth_subject || null,
      email: currentSession?.user?.email || null,
      name: currentSession?.user?.name || null,
      role: currentSession?.user?.role || null,
      platform_role: currentSession?.user?.platform_role || null,
      permissions: [],
      permission_map: permissionMap,
    },
    active_tenant: currentSession?.active_tenant || {
      id: null,
      slug: defaultDeskSlug,
      name: defaultDeskName,
      status: 'active',
      plan_key: defaultPlan,
      role: 'viewer',
      permissions: [],
      permission_map: permissionMap,
      brand_settings: {
        app_name: defaultDeskName,
        app_tagline: defaultDeskTagline,
      },
    },
    api_token: null,
    memberships: [],
  }
}

function applyCustomerReadySession(session = null) {
  if (!session || !appConfig.customerReadyMode || appConfig.showAdminSurfaces) return session
  const permissionMap = Object.fromEntries(CUSTOMER_DEMO_PERMISSIONS.map((permission) => [permission, true]))
  return {
    ...session,
    mode: session.mode === 'demo' ? 'customer_preview' : session.mode,
    provider: session.provider === 'local-demo' ? 'customer-access' : session.provider,
    user: {
      ...(session.user || {}),
      role: (session.user || {}).role === 'owner' ? 'operator' : (session.user || {}).role,
      platform_role: ['admin', 'platform_admin'].includes(String((session.user || {}).platform_role || '').toLowerCase()) ? 'trader' : (session.user || {}).platform_role,
      permissions: CUSTOMER_DEMO_PERMISSIONS,
      permission_map: permissionMap,
    },
    active_tenant: session.active_tenant
      ? {
          ...session.active_tenant,
          role: session.active_tenant.role === 'owner' ? 'operator' : session.active_tenant.role,
          permissions: CUSTOMER_DEMO_PERMISSIONS,
          permission_map: permissionMap,
        }
      : session.active_tenant,
  }
}

function normalizeHexColor(value, fallback) {
  const cleaned = String(value || '').trim().toUpperCase()
  return /^#([0-9A-F]{6}|[0-9A-F]{8})$/.test(cleaned) ? cleaned : fallback
}

function hexToRgb(color) {
  const normalized = normalizeHexColor(color, '#000000').slice(1, 7)
  return {
    r: parseInt(normalized.slice(0, 2), 16),
    g: parseInt(normalized.slice(2, 4), 16),
    b: parseInt(normalized.slice(4, 6), 16),
  }
}

function rgbToHex({ r, g, b }) {
  const clamp = (value) => Math.max(0, Math.min(255, Math.round(value)))
  return `#${[clamp(r), clamp(g), clamp(b)].map((value) => value.toString(16).padStart(2, '0')).join('').toUpperCase()}`
}

function mixHex(baseColor, mixColor, weight) {
  const base = hexToRgb(baseColor)
  const mix = hexToRgb(mixColor)
  return rgbToHex({
    r: base.r + (mix.r - base.r) * weight,
    g: base.g + (mix.g - base.g) * weight,
    b: base.b + (mix.b - base.b) * weight,
  })
}

function rgba(color, alpha) {
  const { r, g, b } = hexToRgb(color)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

function rgbTriplet(color) {
  const { r, g, b } = hexToRgb(color)
  return `${r} ${g} ${b}`
}

function rgbToHue({ r, g, b }) {
  const red = r / 255
  const green = g / 255
  const blue = b / 255
  const max = Math.max(red, green, blue)
  const min = Math.min(red, green, blue)
  const delta = max - min
  if (delta === 0) return null

  let hue
  if (max === red) {
    hue = ((green - blue) / delta) % 6
  } else if (max === green) {
    hue = (blue - red) / delta + 2
  } else {
    hue = (red - green) / delta + 4
  }

  const degrees = hue * 60
  return degrees < 0 ? degrees + 360 : degrees
}

function isBlueFamily(color) {
  const hue = rgbToHue(hexToRgb(color))
  return hue !== null && hue >= 165 && hue <= 230
}

function sanitizeThemeColor(color, fallback) {
  return isBlueFamily(color) ? fallback : color
}

function buildOrganizationTheme(activeOrganization) {
  const brandSettings = activeOrganization?.brand_settings || {}
  const accentPrimary = sanitizeThemeColor(
    normalizeHexColor(brandSettings.accent_primary, DEFAULT_THEME.accentPrimary),
    DEFAULT_THEME.accentPrimary,
  )
  const accentSecondary = sanitizeThemeColor(
    normalizeHexColor(brandSettings.accent_secondary, DEFAULT_THEME.accentSecondary),
    DEFAULT_THEME.accentSecondary,
  )
  const backgroundColor = sanitizeThemeColor(
    normalizeHexColor(brandSettings.background_color, DEFAULT_THEME.backgroundColor),
    DEFAULT_THEME.backgroundColor,
  )
  const surfaceColor = sanitizeThemeColor(
    normalizeHexColor(brandSettings.surface_color, DEFAULT_THEME.surfaceColor),
    DEFAULT_THEME.surfaceColor,
  )
  const textColor = normalizeHexColor(brandSettings.text_color, DEFAULT_THEME.textColor)
  const bg1 = mixHex(backgroundColor, '#FFFFFF', 0.02)
  const bg2 = mixHex(backgroundColor, '#FFFFFF', 0.05)
  const bg3 = mixHex(surfaceColor, '#FFFFFF', 0.05)

  return {
    '--bg-0': backgroundColor,
    '--bg-1': bg1,
    '--bg-2': bg2,
    '--bg-3': bg3,
    '--bg-0-rgb': rgbTriplet(backgroundColor),
    '--surface-0': rgba(surfaceColor, 0.88),
    '--surface-1': rgba(surfaceColor, 0.94),
    '--surface-2': rgba(surfaceColor, 0.98),
    '--accent-1': accentPrimary,
    '--accent-2': accentSecondary,
    '--accent-3': mixHex(accentSecondary, '#FFFFFF', 0.55),
    '--accent-1-rgb': rgbTriplet(accentPrimary),
    '--accent-2-rgb': rgbTriplet(accentSecondary),
    '--text-1': textColor,
    '--text-2': rgba(textColor, 0.72),
    '--text-3': rgba(textColor, 0.46),
  }
}

function applyOrganizationTheme(activeOrganization) {
  if (typeof document === 'undefined') return

  const root = document.documentElement
  const theme = buildOrganizationTheme(activeOrganization)
  Object.entries(theme).forEach(([key, value]) => root.style.setProperty(key, value))
  root.dataset.organizationSlug = activeOrganization?.slug || ''
  root.dataset.brandAppName = activeOrganization?.brand_settings?.app_name || ''
}

export function AuthProvider({ children }) {
  const [authConfig, setAuthConfig] = useState(null)
  const [session, setSession] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const authConfigRef = useRef(authConfig)
  const sessionRef = useRef(session)
  const offlineLoopRef = useRef(null)

  useEffect(() => {
    authConfigRef.current = authConfig
  }, [authConfig])

  useEffect(() => {
    sessionRef.current = session
  }, [session])

  const refreshSession = useCallback(() => {
    return getAuthSession()
      .then((data) => {
        const nextSession = applyCustomerReadySession(data?.authenticated ? data : buildDemoSession(data, authConfigRef.current))
        setSession(nextSession)
        setError('')
        return nextSession
      })
      .catch((err) => {
        const allowDemoFallback = authConfigRef.current?.mode === 'demo' || sessionRef.current?.mode === 'demo'
        const nextSession = allowDemoFallback
          ? buildDemoSession(sessionRef.current, authConfigRef.current)
          : buildOfflineSession(sessionRef.current)
        const presentedSession = applyCustomerReadySession(nextSession)
        setSession(presentedSession)
        if (!allowDemoFallback) {
          setError((current) => current || (err?.response?.data?.detail || err?.message || 'Backend is starting or unavailable.'))
        } else {
          setError('')
        }
        return presentedSession
      })
  }, [])

  const waitForBackend = useCallback(async ({ maxWaitMs = 10000 } = {}) => {
    const startedAt = Date.now()
    const delays = [200, 400, 800, 1600, 2000]
    let attempt = 0
    while (Date.now() - startedAt < maxWaitMs) {
      const probe = await probeBackendHealthz({ timeoutMs: 8000 })
      if (probe?.ok) return true
      const delay = delays[Math.min(attempt, delays.length - 1)]
      attempt += 1
      await new Promise((resolve) => setTimeout(resolve, delay))
    }
    return false
  }, [])

  const refreshAuthSurface = useCallback(() => {
    return Promise.allSettled([getAuthConfig(), refreshSession()])
      .then(([configResult, sessionResult]) => {
        let nextConfig = configResult.status === 'fulfilled' ? configResult.value : null
        let nextSession = sessionResult.status === 'fulfilled' ? sessionResult.value : null
        const useDemoFallbackConfig = configResult.status === 'rejected'
          && (
            nextSession?.mode === 'demo'
            || nextSession?.provider === 'local-demo'
            || authConfigRef.current?.mode === 'demo'
          )

        if (configResult.status === 'fulfilled' || sessionResult.status === 'fulfilled') {
          setError('')
        }

        if (!nextSession?.authenticated) {
          const allowDemoFallback =
            useDemoFallbackConfig
            || nextSession?.mode === 'demo'
            || authConfigRef.current?.mode === 'demo'
            || sessionRef.current?.mode === 'demo'
          nextSession = applyCustomerReadySession(allowDemoFallback ? buildDemoSession(nextSession, nextConfig) : buildOfflineSession(nextSession))
          setSession(nextSession)
          if (!allowDemoFallback) {
            setError((current) => current || 'Backend is starting or unavailable.')
          } else {
            setError('')
          }
        }

        if (useDemoFallbackConfig) {
          nextConfig = buildDemoAuthConfig(nextSession, authConfigRef.current)
        }

        setAuthConfig(nextConfig)

        if (configResult.status === 'rejected' && !useDemoFallbackConfig) {
          const message = configResult.reason?.response?.data?.detail || configResult.reason?.message || 'Failed to load authentication configuration.'
          setError((current) => current || message)
        }

        if (configResult.status === 'rejected' && sessionResult.status === 'rejected') {
          throw configResult.reason || sessionResult.reason
        }

        return { config: nextConfig, session: nextSession }
      })
      .finally(() => setLoading(false))
  }, [refreshSession])

  const signIn = useCallback(async (payload = {}) => {
    const config = authConfigRef.current
    const supportsLogin = Boolean(config?.supports_login)
    const mode = String(config?.mode || '').toLowerCase()
    if (supportsLogin && mode !== 'demo') {
      const sessionPayload = await login(payload)
      const nextSession = applyCustomerReadySession(sessionPayload)
      setSession(nextSession)
      setError('')
      return nextSession
    }
    const nextSession = applyCustomerReadySession(buildDemoSession(sessionRef.current, authConfigRef.current))
    setSession(nextSession)
    setError('')
    return nextSession
  }, [])

  const signOut = useCallback(async () => {
    setBusy(true)
    try {
      try {
        await logout()
      } catch {
        // auth is intentionally bypassed; ignore logout transport failures
      }
      const nextSession = applyCustomerReadySession(buildDemoSession(sessionRef.current, authConfigRef.current))
      setSession(nextSession)
      setError('')
      return nextSession
    } finally {
      setBusy(false)
    }
  }, [])

  const beginProviderLogin = useCallback(async () => {
    const nextSession = applyCustomerReadySession(buildDemoSession(sessionRef.current, authConfigRef.current))
    setSession(nextSession)
    setError('')
    return nextSession
  }, [])

  const switchOrganization = useCallback(async (organizationSlug) => {
    setBusy(true)
    try {
      await activateOrganization(organizationSlug)
      return await refreshSession()
    } finally {
      setBusy(false)
    }
  }, [refreshSession])

  useEffect(() => {
    let cancelled = false

    const run = async () => {
      const reachable = await waitForBackend({ maxWaitMs: 30000 })
      if (cancelled) return

      if (!reachable) {
        setSession((current) => current || applyCustomerReadySession(buildOfflineSession(sessionRef.current)))
        setError((current) => current || 'Backend is starting or unavailable.')
        setLoading(false)

        if (offlineLoopRef.current) {
          clearTimeout(offlineLoopRef.current)
        }
        offlineLoopRef.current = setTimeout(() => {
          refreshAuthSurface().catch(() => {})
        }, 2000)
        return
      }

      refreshAuthSurface().catch(() => {})
    }

    run()
    return () => {
      cancelled = true
      if (offlineLoopRef.current) {
        clearTimeout(offlineLoopRef.current)
        offlineLoopRef.current = null
      }
    }
  }, [refreshAuthSurface, waitForBackend])

  useEffect(() => {
    applyOrganizationTheme(session?.active_tenant || null)
  }, [session])

  const value = useMemo(
    () => ({
      authConfig,
      session,
      loading,
      busy,
      error,
      refreshSession,
      refreshAuthSurface,
      signIn,
      signOut,
      beginProviderLogin,
      switchOrganization,
    }),
    [authConfig, session, loading, busy, error, refreshSession, refreshAuthSurface, signIn, signOut, beginProviderLogin, switchOrganization],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
