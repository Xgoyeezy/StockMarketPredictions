import { Suspense, lazy, useEffect, useRef, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { FALLBACK_BOOTSTRAP, getBootstrap } from './api/client'
import { appConfig } from './config/appConfig'
import AppShell from './components/AppShell'
import AdminAccessGate from './components/AdminAccessGate'
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
const StrategiesPage = lazy(() => import('./pages/StrategiesPage'))
const StrategyDetailPage = lazy(() => import('./pages/StrategyDetailPage'))
const RiskCenterPage = lazy(() => import('./pages/RiskCenterPage'))
const PortfolioRiskPage = lazy(() => import('./pages/PortfolioRiskPage'))
const AuditReplayPage = lazy(() => import('./pages/AuditReplayPage'))
const ExecutionQualityPage = lazy(() => import('./pages/ExecutionQualityPage'))
const EvidenceEdgePage = lazy(() => import('./pages/EvidenceEdgePage'))
const EvidenceOutcomesPage = lazy(() => import('./pages/EvidenceOutcomesPage'))
const ForecastValidationPage = lazy(() => import('./pages/ForecastValidationPage'))
const EvidenceRewardPage = lazy(() => import('./pages/EvidenceRewardPage'))
const ProfessionalBenchmarkPage = lazy(() => import('./pages/ProfessionalBenchmarkPage'))
const DataCompletenessPage = lazy(() => import('./pages/DataCompletenessPage'))
const WalkForwardExperimentsPage = lazy(() => import('./pages/WalkForwardExperimentsPage'))
const ResearchPromotionPage = lazy(() => import('./pages/ResearchPromotionPage'))
const ScoreCalibrationPage = lazy(() => import('./pages/ScoreCalibrationPage'))
const ShadowModePage = lazy(() => import('./pages/ShadowModePage'))
const AICommitteePage = lazy(() => import('./pages/AICommitteePage'))
const CategoryReadinessPage = lazy(() => import('./pages/CategoryReadinessPage'))
const ProofMetricsPage = lazy(() => import('./pages/ProofMetricsPage'))
const LiveTradingConsolePage = lazy(() => import('./pages/LiveTradingConsolePage'))
const LiveStrategyControlPage = lazy(() => import('./pages/LiveStrategyControlPage'))
const LiveOrderApprovalPage = lazy(() => import('./pages/LiveOrderApprovalPage'))
const PricingPage = lazy(() => import('./pages/PricingPage'))
const PublicInfoPage = lazy(() => import('./pages/PublicInfoPage'))
const MarketingHomePage = lazy(() => import('./pages/MarketingHomePage'))

function buildDocumentTitle(pathname, activeAccountProfile = 'personal_paper') {
  const titleMap = appConfig.personalMode
    ? {
        '/': 'Desk',
        '/app': 'Desk',
        '/watchlist': 'Watchlist',
        '/compare': 'Compare',
        '/trades': 'Trades',
        '/portfolio': 'Portfolio',
        '/journal': 'Journal',
        '/alerts': 'Alerts',
        '/notes': 'Notes',
        '/education': 'Playbook',
        '/strategies': 'Strategies',
        '/risk': 'Risk Center',
        '/portfolio-risk': 'Portfolio Risk',
        '/audit': 'Audit Replay',
        '/execution-quality': 'Execution Quality',
        '/evidence-edge': 'Evidence Edge',
        '/evidence-outcomes': 'Evidence Outcomes',
        '/forecast-validation': 'Forecast Validation',
        '/evidence-reward': 'Evidence Reward',
        '/professional-benchmark': 'Professional Benchmark',
        '/data-completeness': 'Data Completeness',
        '/walk-forward': 'Walk-Forward Experiments',
        '/research-promotion': 'Research Promotion',
        '/score-calibration': 'Score Calibration',
        '/shadow-mode': 'Human vs System Shadow',
        '/ai-committee': 'AI Committee',
        '/category-readiness': '10/10 Readiness',
        '/proof-metrics': 'Proof Metrics',
        '/live': 'Live Console',
        '/live/approvals': 'Live Approvals',
        '/pricing': 'Pricing',
        '/admin': 'Admin / Advanced',
        '/strategy-desks': 'Strategy desks',
        '/strategy-desks/systematic-equities': 'Systematic Equities',
        '/settings': 'Settings',
        '/activity': 'Activity',
        '/workspaces': 'Workspaces',
        '/release': 'Release',
        '/login': 'Sign in',
      }
    : {
        '/': 'Desk',
        '/app': 'Desk',
        '/watchlist': 'Research',
        '/compare': 'Compare',
        '/trades': 'Trades',
        '/portfolio': 'Portfolio',
        '/journal': 'Audit log',
        '/alerts': 'Alerts',
        '/notes': 'Runbook',
        '/education': 'Operator guide',
        '/strategies': 'Strategies',
        '/risk': 'Risk Center',
        '/portfolio-risk': 'Portfolio Risk',
        '/audit': 'Audit Replay',
        '/execution-quality': 'Execution Quality',
        '/evidence-edge': 'Evidence Edge',
        '/evidence-outcomes': 'Evidence Outcomes',
        '/forecast-validation': 'Forecast Validation',
        '/evidence-reward': 'Evidence Reward',
        '/professional-benchmark': 'Professional Benchmark',
        '/data-completeness': 'Data Completeness',
        '/walk-forward': 'Walk-Forward Experiments',
        '/research-promotion': 'Research Promotion',
        '/score-calibration': 'Score Calibration',
        '/shadow-mode': 'Human vs System Shadow',
        '/ai-committee': 'AI Committee',
        '/category-readiness': '10/10 Readiness',
        '/proof-metrics': 'Proof Metrics',
        '/live': 'Live Console',
        '/live/approvals': 'Live Approvals',
        '/pricing': 'Pricing',
        '/admin': 'Admin / Advanced',
        '/strategy-desks': 'Strategy desks',
        '/strategy-desks/systematic-equities': 'Systematic Equities',
        '/settings': 'Settings',
        '/activity': 'Activity',
        '/workspaces': 'Organizations',
        '/release': 'Release',
        '/login': 'Organization sign in',
      }

  const pageName =
    pathname === '/settings'
      ? 'Settings'
      : titleMap[pathname] ||
        (pathname.startsWith('/strategies/') && pathname.endsWith('/live')
          ? 'Live Strategy Control'
          : pathname.startsWith('/strategies/')
            ? 'Strategy Detail'
            : 'Desk')
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

  if (appConfig.customerReadyMode) return null

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
        : `Environment: ${environment}. The trading desk is running in a non-production environment.`,
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
  const isPricingRoute = location.pathname === '/pricing'
  const isMarketingHomeRoute = location.pathname === '/'
  const isPublicSurfaceRoute = isPublicInfoRoute || isPricingRoute || isMarketingHomeRoute
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
    if (isPublicSurfaceRoute) return
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
    } else if (nextPathname === '/app' && session?.active_tenant?.slug && !params.get('tenant')) {
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
    isPublicSurfaceRoute,
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
    if (isPublicSurfaceRoute) return
    if (authLoading || !session?.authenticated) return
    if (!isWorkflowSurfacePath(location.pathname)) return
    if (typeof window === 'undefined') return
    window.localStorage.setItem('sos-last-workflow-surface', location.pathname)
  }, [authLoading, isPublicSurfaceRoute, location.pathname, session?.authenticated])

  useEffect(() => {
    if (isPublicSurfaceRoute) return
    if (authLoading || !session?.authenticated) return
    if (startupRouteHandledRef.current) return
    startupRouteHandledRef.current = true
    if (location.pathname !== '/app' || location.search) return
    const preferredStartupSurface = resolvePreferredStartupSurface()
    if (preferredStartupSurface === '/') return
    navigate({ pathname: preferredStartupSurface, search: '' }, { replace: true })
  }, [
    isPublicSurfaceRoute,
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
      if (isPublicSurfaceRoute) return
      document.title = buildDocumentTitle(location.pathname, activeAccountProfile)
      window.requestAnimationFrame(() => {
        const main = document.querySelector('.ui-shell__body')
        if (main && typeof main.focus === 'function') {
          main.focus({ preventScroll: true })
        }
      })
    }
  }, [activeAccountProfile, isPublicSurfaceRoute, location.pathname])

  useEffect(() => {
    if (isPublicSurfaceRoute) return
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
  }, [authLoading, isPublicSurfaceRoute, session?.authenticated, session?.active_tenant?.slug])

  if (isPublicInfoRoute) {
    return (
      <Suspense fallback={<div className="ui-shell__page"><LoadingBlock label="Loading public page..." /></div>}>
        <PublicInfoPage pathname={location.pathname} />
      </Suspense>
    )
  }

  if (isPricingRoute) {
    return (
      <Suspense fallback={<div className="ui-shell__page"><LoadingBlock label="Loading pricing..." /></div>}>
        <PricingPage />
      </Suspense>
    )
  }

  if (isMarketingHomeRoute) {
    return (
      <Suspense fallback={<div className="ui-shell__page"><LoadingBlock label="Loading..." /></div>}>
        <MarketingHomePage />
      </Suspense>
    )
  }

  if (authLoading) {
    return <div className="ui-shell__page"><LoadingBlock label="Loading application..." /></div>
  }

  if (session?.authenticated && location.pathname.startsWith('/login')) {
    return <div className="ui-shell__page"><LoadingBlock label={personalMode ? 'Opening desk...' : 'Opening trading desk...'} /></div>
  }

  return (
    <AppShell appName={shellAppName} appTagline={shellTagline}>
      <div className="ui-visually-hidden" aria-live="polite">
        Home surface {getSurfaceLabel(resolvePreferredStartupSurface())}. Review surface {getSurfaceLabel(reviewSurface)}.
      </div>
      {readinessNotice ? <div className={readinessNotice.tone === 'error' ? 'error-banner' : 'info-banner'}>{readinessNotice.message}</div> : null}
      {showBootstrapError ? <div className="error-banner">{error}</div> : null}
      {preferences?.showWorkflowStatusStrip === false ? null : <WorkflowStatusStrip />}
      <Suspense fallback={<LoadingBlock label="Loading page..." />}>
        <Routes>
          <Route path="/" element={<Navigate to="/app" replace />} />
          <Route path="/app" element={<DashboardPage bootstrap={bootstrap} />} />
          <Route path="/compare" element={<ComparePage />} />
          <Route path="/watchlist" element={<WatchlistPage />} />
          <Route path="/trades" element={<TradesPage />} />
          <Route path="/journal" element={<JournalPage />} />
          <Route path="/alerts" element={<AlertsPage />} />
          <Route path="/portfolio" element={<PortfolioPage />} />
          <Route path="/notes" element={<NotesPage />} />
          <Route path="/education" element={<AdminAccessGate><EducationPage /></AdminAccessGate>} />
          <Route path="/strategies/:strategyId" element={<StrategyDetailPage />} />
          <Route path="/strategies/:strategyId/live" element={<LiveStrategyControlPage />} />
          <Route path="/strategies" element={<StrategiesPage />} />
          <Route path="/risk" element={<RiskCenterPage />} />
          <Route path="/portfolio-risk" element={<PortfolioRiskPage />} />
          <Route path="/audit" element={<AuditReplayPage />} />
          <Route path="/execution-quality" element={<ExecutionQualityPage />} />
          <Route path="/evidence-edge" element={<EvidenceEdgePage />} />
          <Route path="/evidence-outcomes" element={<EvidenceOutcomesPage />} />
          <Route path="/forecast-validation" element={<ForecastValidationPage />} />
          <Route path="/evidence-reward" element={<EvidenceRewardPage />} />
          <Route path="/professional-benchmark" element={<ProfessionalBenchmarkPage />} />
          <Route path="/data-completeness" element={<DataCompletenessPage />} />
          <Route path="/walk-forward" element={<WalkForwardExperimentsPage />} />
          <Route path="/research-promotion" element={<ResearchPromotionPage />} />
          <Route path="/score-calibration" element={<ScoreCalibrationPage />} />
          <Route path="/shadow-mode" element={<ShadowModePage />} />
          <Route path="/ai-committee" element={<AICommitteePage />} />
          <Route path="/category-readiness" element={<CategoryReadinessPage />} />
          <Route path="/proof-metrics" element={<ProofMetricsPage />} />
          <Route path="/live" element={<LiveTradingConsolePage />} />
          <Route path="/live/approvals" element={<LiveOrderApprovalPage />} />
          <Route path="/strategy-desks/systematic-equities" element={<AdminAccessGate><SystematicDeskPage /></AdminAccessGate>} />
          <Route path="/strategy-desks" element={<AdminAccessGate><StrategyDesksPage /></AdminAccessGate>} />
          <Route path="/settings" element={<OwnAccountSettingsPage />} />
          <Route path="/admin" element={<AdminAccessGate><SettingsPage /></AdminAccessGate>} />
          {!personalMode ? <Route path="/activity" element={<AdminAccessGate><ActivityPage /></AdminAccessGate>} /> : null}
          {!personalMode ? <Route path="/workspaces" element={<AdminAccessGate><WorkspacesPage /></AdminAccessGate>} /> : null}
          <Route path="/release" element={<AdminAccessGate><ReleasePage /></AdminAccessGate>} />
          <Route path="*" element={<Navigate to="/app" replace />} />
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
