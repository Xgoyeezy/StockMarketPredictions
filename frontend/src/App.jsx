import { Suspense, lazy, useEffect, useRef, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { FALLBACK_BOOTSTRAP, getBootstrap } from './api/client'
import { appConfig } from './config/appConfig'
import AppShell from './components/AppShell'
import LoadingBlock from './components/LoadingBlock'
import WorkflowStatusStrip from './components/WorkflowStatusStrip'
import { ToastProvider } from './context/ToastContext'
import { AuthProvider } from './context/AuthContext'
import { useAuth } from './context/useAuth'
import { PreferencesProvider, usePreferences } from './context/PreferencesContext'
import ErrorBoundary from './components/ErrorBoundary'
import {
  getSurfaceLabel,
  isWorkflowSurfacePath,
  normalizeTradingStyle,
  resolveReviewSurface,
  resolveStartupSurface,
} from './utils/operatorCustomization'
import { normalizeAccountProfile } from './utils/accountProfileModel'
import { getPublicSitePage } from './utils/publicSiteModel'
const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const WatchlistPage = lazy(() => import('./pages/WatchlistPage'))
const ComparePage = lazy(() => import('./pages/ComparePage'))
const TradesPage = lazy(() => import('./pages/TradesPage'))
const PortfolioPage = lazy(() => import('./pages/PortfolioPage'))
const JournalPage = lazy(() => import('./pages/JournalPage'))
const AlertsPage = lazy(() => import('./pages/AlertsPage'))
const ActivityPage = lazy(() => import('./pages/ActivityPage'))
const WorkspacesPage = lazy(() => import('./pages/WorkspacesPage'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))
const OwnAccountSettingsPage = lazy(() => import('./pages/OwnAccountSettingsPage'))
const NotesPage = lazy(() => import('./pages/NotesPage'))
const ReleasePage = lazy(() => import('./pages/ReleasePage'))
const EducationPage = lazy(() => import('./pages/EducationPage'))
const StrategyDesksPage = lazy(() => import('./pages/StrategyDesksPage'))
const SystematicDeskPage = lazy(() => import('./pages/SystematicDeskPage'))
const PublicInfoPage = lazy(() => import('./pages/PublicInfoPage'))

function buildDocumentTitle(pathname, activeAccountProfile = 'personal_paper') {
  const titleMap = appConfig.personalMode
    ? {
        '/': 'Desk',
        '/watchlist': 'Watchlist',
        '/compare': 'Compare',
        '/trades': 'Trades',
        '/portfolio': 'Portfolio',
        '/journal': 'Journal',
        '/alerts': 'Alerts',
        '/notes': 'Notes',
        '/education': 'Playbook',
        '/strategy-desks': 'Strategy desks',
        '/strategy-desks/systematic-equities': 'Systematic Equities',
        '/settings': 'Desk setup',
        '/activity': 'Activity',
        '/workspaces': 'Workspaces',
        '/release': 'Release',
        '/login': 'Sign in',
      }
    : {
        '/': 'Operations',
        '/watchlist': 'Market watch',
        '/compare': 'Analysis',
        '/trades': 'Execution ops',
        '/portfolio': 'Exposure',
        '/journal': 'Audit log',
        '/alerts': 'Alerts',
        '/notes': 'Runbook',
        '/education': 'Operator guide',
        '/strategy-desks': 'Strategy desks',
        '/strategy-desks/systematic-equities': 'Systematic Equities',
        '/settings': 'Platform ops',
        '/activity': 'Activity',
        '/workspaces': 'Organizations',
        '/release': 'Release',
        '/login': 'Organization sign in',
      }

  const normalizedAccountProfile = normalizeAccountProfile(activeAccountProfile)
  const pageName =
    pathname === '/settings'
      ? normalizedAccountProfile === 'brokerage'
        ? 'Brokerage account'
        : normalizedAccountProfile === 'personal_live'
          ? 'Personal live'
          : 'Personal paper'
      : titleMap[pathname] || (appConfig.personalMode ? 'Desk' : 'Operations')
  return `${pageName} | ${appConfig.appName}`
}

function buildReadinessNotice({ authConfig, session, authError, bootstrapError, personalMode }) {
  if (authConfig?.mode === 'demo' || authConfig?.supports_login === false) {
    return null
  }

  if (authError) {
    return {
      tone: 'error',
      message: `Authentication readiness issue: ${authError}`,
    }
  }

  if (!authConfig) {
    return {
      tone: 'error',
      message: personalMode
        ? 'Authentication configuration could not be loaded. Desk access state is currently unverified.'
        : 'Authentication configuration could not be loaded. Organization access state is currently unverified.',
    }
  }

  if (bootstrapError && session?.authenticated) {
    return {
      tone: 'error',
      message: `Application bootstrap failed: ${bootstrapError}`,
    }
  }

  if (personalMode) return null

  const environment = authConfig?.environment || 'unknown'
  const mode = authConfig?.mode || 'unknown'

  if (mode === 'demo') {
    return {
      tone: 'info',
      message: `Environment: ${environment}. Auth mode is demo, so launch readiness is not yet production-grade.`,
    }
  }

  if (environment !== 'production') {
    return {
      tone: 'info',
      message: personalMode
        ? `Environment: ${environment}. This desk is running in a non-production environment.`
        : `Environment: ${environment}. Platform operations are running in a non-production environment.`,
    }
  }

  return null
}

function AppFrame() {
  const { authConfig, session, loading: authLoading, error: authError } = useAuth()
  const { preferences } = usePreferences()
  const location = useLocation()
  const navigate = useNavigate()
  const [bootstrap, setBootstrap] = useState(FALLBACK_BOOTSTRAP)
  const [error, setError] = useState('')
  const personalMode = appConfig.personalMode
  const shellAppName = personalMode ? appConfig.appName : bootstrap?.app?.name || appConfig.appName
  const shellTagline = personalMode ? appConfig.appTagline : bootstrap?.app?.tagline || appConfig.appTagline
  const tradingStyle = normalizeTradingStyle(preferences?.tradingStyle, 'intraday')
  const activeAccountProfile = normalizeAccountProfile(preferences?.activeAccountProfile)
  const publicInfoPage = getPublicSitePage(location.pathname)
  const isPublicInfoRoute = Boolean(publicInfoPage)
  const startupSurface = resolveStartupSurface(tradingStyle, preferences?.startupSurface)
  const reviewSurface = resolveReviewSurface(tradingStyle, preferences?.defaultReviewSurface)
  const rememberLastWorkflowSurface = Boolean(preferences?.rememberLastWorkflowSurface)
  const readinessNotice = buildReadinessNotice({
    authConfig,
    session,
    authError,
    bootstrapError: error,
    personalMode,
  })
  const showBootstrapError = Boolean(error) && !(readinessNotice?.tone === 'error' && readinessNotice?.message === `Application bootstrap failed: ${error}`)
  const startupRouteHandledRef = useRef(false)

  function getStoredWorkflowSurface() {
    if (typeof window === 'undefined') return ''
    const stored = window.localStorage.getItem('sos-last-workflow-surface')
    return isWorkflowSurfacePath(stored) ? stored : ''
  }

  function resolvePreferredStartupSurface() {
    if (rememberLastWorkflowSurface) {
      const stored = getStoredWorkflowSurface()
      if (stored) return stored
    }
    return startupSurface
  }

  useEffect(() => {
    if (isPublicInfoRoute) return
    if (authLoading || !session?.authenticated) return
    const params = new URLSearchParams(location.search)
    let nextPathname = location.pathname
    let changed = false

    const preferredStartupSurface = resolvePreferredStartupSurface()

    if (location.pathname.startsWith('/login')) {
      nextPathname = preferredStartupSurface
      changed = true
    }

    ;['invite', 'invite_token', 'auth_error', 'auth_error_description'].forEach((key) => {
      if (params.has(key)) {
        params.delete(key)
        changed = true
      }
    })

    if (personalMode) {
      ;['tenant', 'tenant_slug'].forEach((key) => {
        if (params.has(key)) {
          params.delete(key)
          changed = true
        }
      })
    } else if (nextPathname === '/' && session?.active_tenant?.slug && !params.get('tenant')) {
      params.set('tenant', session.active_tenant.slug)
      changed = true
    }

    if (!changed) return
    navigate(
      {
        pathname: nextPathname,
        search: params.toString() ? `?${params.toString()}` : '',
      },
      { replace: true },
    )
  }, [
    isPublicInfoRoute,
    authLoading,
    location.pathname,
    location.search,
    navigate,
    personalMode,
    rememberLastWorkflowSurface,
    session?.active_tenant?.slug,
    session?.authenticated,
    startupSurface,
  ])

  useEffect(() => {
    if (isPublicInfoRoute) return
    if (authLoading || !session?.authenticated) return
    if (!isWorkflowSurfacePath(location.pathname)) return
    if (typeof window === 'undefined') return
    window.localStorage.setItem('sos-last-workflow-surface', location.pathname)
  }, [authLoading, isPublicInfoRoute, location.pathname, session?.authenticated])

  useEffect(() => {
    if (isPublicInfoRoute) return
    if (authLoading || !session?.authenticated) return
    if (startupRouteHandledRef.current) return
    startupRouteHandledRef.current = true
    if (location.pathname !== '/' || location.search) return
    const preferredStartupSurface = resolvePreferredStartupSurface()
    if (preferredStartupSurface === '/') return
    navigate({ pathname: preferredStartupSurface, search: '' }, { replace: true })
  }, [
    isPublicInfoRoute,
    authLoading,
    location.pathname,
    location.search,
    navigate,
    rememberLastWorkflowSurface,
    session?.authenticated,
    startupSurface,
  ])

  useEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: 'auto' })
    if (typeof document !== 'undefined') {
      if (isPublicInfoRoute) return
      document.title = buildDocumentTitle(location.pathname, activeAccountProfile)
      window.requestAnimationFrame(() => {
        const main = document.querySelector('.ui-shell__body')
        if (main && typeof main.focus === 'function') {
          main.focus({ preventScroll: true })
        }
      })
    }
  }, [activeAccountProfile, isPublicInfoRoute, location.pathname])

  useEffect(() => {
    if (isPublicInfoRoute) return
    if (authLoading) return
    if (!session?.authenticated) {
      startupRouteHandledRef.current = false
      setBootstrap(FALLBACK_BOOTSTRAP)
      setError('')
      return
    }
    getBootstrap('shell')
      .then((data) => {
        setBootstrap(data)
        setError('')
      })
      .catch((err) => {
        setError(err?.response?.data?.detail || err.message || 'Failed to load application bootstrap.')
      })
  }, [authLoading, isPublicInfoRoute, session?.authenticated, session?.active_tenant?.slug])

  if (isPublicInfoRoute) {
    return (
      <Suspense fallback={<div className="ui-shell__page"><LoadingBlock label="Loading public page..." /></div>}>
        <PublicInfoPage pathname={location.pathname} />
      </Suspense>
    )
  }

  if (authLoading) {
    return <div className="ui-shell__page"><LoadingBlock label="Loading application..." /></div>
  }

  if (session?.authenticated && location.pathname.startsWith('/login')) {
    return <div className="ui-shell__page"><LoadingBlock label={personalMode ? 'Opening desk...' : 'Opening platform operations...'} /></div>
  }

  return (
    <AppShell appName={shellAppName} appTagline={shellTagline}>
      <div className="ui-visually-hidden" aria-live="polite">
        Home surface {getSurfaceLabel(resolvePreferredStartupSurface())}. Review surface {getSurfaceLabel(reviewSurface)}.
      </div>
      {readinessNotice ? <div className={readinessNotice.tone === 'error' ? 'error-banner' : 'info-banner'}>{readinessNotice.message}</div> : null}
      {showBootstrapError ? <div className="error-banner">{error}</div> : null}
      <WorkflowStatusStrip />
      <Suspense fallback={<LoadingBlock label="Loading page..." />}>
        <Routes>
          <Route path="/" element={<DashboardPage bootstrap={bootstrap} />} />
          <Route path="/compare" element={<ComparePage />} />
          <Route path="/watchlist" element={<WatchlistPage />} />
          <Route path="/trades" element={<TradesPage />} />
          <Route path="/journal" element={<JournalPage />} />
          <Route path="/alerts" element={<AlertsPage />} />
          <Route path="/portfolio" element={<PortfolioPage />} />
          <Route path="/notes" element={<NotesPage />} />
          <Route path="/education" element={<EducationPage />} />
          <Route path="/strategy-desks/systematic-equities" element={<SystematicDeskPage />} />
          <Route path="/strategy-desks" element={<StrategyDesksPage />} />
          <Route
            path="/settings"
            element={activeAccountProfile === 'brokerage' ? <SettingsPage /> : <OwnAccountSettingsPage />}
          />
          {!personalMode ? <Route path="/activity" element={<ActivityPage />} /> : null}
          {!personalMode ? <Route path="/workspaces" element={<WorkspacesPage />} /> : null}
          <Route path="/release" element={<ReleasePage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </AppShell>
  )
}

export default function App() {
  return (
    <ErrorBoundary>
      <ToastProvider>
        <AuthProvider>
          <PreferencesProvider>
            <BrowserRouter>
              <AppFrame />
            </BrowserRouter>
          </PreferencesProvider>
        </AuthProvider>
      </ToastProvider>
    </ErrorBoundary>
  )
}
