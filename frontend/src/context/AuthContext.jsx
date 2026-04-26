import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { activateOrganization, getAuthConfig, getAuthSession, logout } from '../api/client'
import { AuthContext } from './authContextObject'
import { appConfig } from '../config/appConfig'
const DEFAULT_THEME = {
  accentPrimary: '#565656',
  accentSecondary: '#2F2F2F',
  backgroundColor: '#000000',
  surfaceColor: '#111111',
  textColor: '#F5F5F5',
}
const DEMO_PERMISSIONS = [
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

function buildDemoAuthConfig(currentSession = null, currentConfig = null) {
  const environment = currentConfig?.environment || currentSession?.environment || 'development'
  const cookieName = currentConfig?.local_session?.cookie_name || 'stocksignals_session'
  const maxAgeSeconds = currentConfig?.local_session?.max_age_seconds || 60 * 60 * 24 * 14
  const defaultPlan = currentConfig?.local_session?.default_plan || 'starter'

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
      role: currentSession?.user?.role || 'owner',
      platform_role: currentSession?.user?.platform_role || 'admin',
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
        role: currentSession?.active_tenant?.role || 'owner',
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

  useEffect(() => {
    authConfigRef.current = authConfig
  }, [authConfig])

  useEffect(() => {
    sessionRef.current = session
  }, [session])

  const refreshSession = useCallback(() => {
    return getAuthSession()
      .then((data) => {
        const nextSession = data?.authenticated ? data : buildDemoSession(data, authConfigRef.current)
        setSession(nextSession)
        setError('')
        return nextSession
      })
      .catch((err) => {
        const nextSession = buildDemoSession(sessionRef.current, authConfigRef.current)
        setSession(nextSession)
        setError('')
        return nextSession
      })
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
          nextSession = buildDemoSession(nextSession, nextConfig)
          setSession(nextSession)
          setError('')
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

  const signIn = useCallback(async () => {
    const nextSession = buildDemoSession(sessionRef.current, authConfigRef.current)
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
      const nextSession = buildDemoSession(sessionRef.current, authConfigRef.current)
      setSession(nextSession)
      setError('')
      return nextSession
    } finally {
      setBusy(false)
    }
  }, [])

  const beginProviderLogin = useCallback(async () => {
    const nextSession = buildDemoSession(sessionRef.current, authConfigRef.current)
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
    refreshAuthSurface().catch(() => {})
  }, [refreshAuthSurface])

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
