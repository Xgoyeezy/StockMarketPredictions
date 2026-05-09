import { useEffect, useMemo, useState } from 'react'
import {
  getOptionsAutomationSnapshot,
  getOrganizationTradeAutomation,
  getOrganizationTradeAutomationCandidateDiagnostics,
  getOrganizationTradeAutomationDeskCandidateDiagnostics,
  getOrganizationTradeAutomationDesks,
  getOrganizationTradeAutomationMarketSession,
  getOrganizationTradeAutomationNoTradeReport,
  getOrganizationTradeAutomationProductionTrust,
  getOrganizationTradeAutomationWatchdog,
  getLinkedBrokerageAccounts,
  exportOrganizationTradeAutomationSupportBundle,
  runOrganizationTradeAutomationAction,
  scanOrganizationTradeAutomationDesk,
  testOrganizationTradeAutomationAlertDelivery,
  updateOrganizationTradeAutomation,
} from '../api/client'
import { usePreferences } from '../context/PreferencesContext'
import { useToast } from '../context/ToastContext'
import ActionBar from './ActionBar'
import Button from './Button'
import ErrorState from './ErrorState'
import FeedbackState from './FeedbackState'
import { SelectField, TextField, ToggleField } from './FormFields'
import MetricCard from './MetricCard'
import SectionCard from './SectionCard'
import TradeAutomationAccountSummary from './trade-automation/TradeAutomationAccountSummary'
import ExecutionProviderDiagnostics from './execution/ExecutionProviderDiagnostics'
import {
  buildAutomationTelemetrySnapshot,
  buildAiReviewModel,
  buildCollectionPhaseModel,
  buildControlPlaneModel,
  buildLivePilotCanaryModel,
  buildLivePilotExpansionCanaryModel,
  buildLivePilotExpansionModel,
  buildLivePilotPromotionReportModel,
  buildLivePilotReadinessModel,
  buildLivePilotSoakModel,
  buildLivePilotWindowCanaryModel,
  buildLivePilotWindowModel,
  buildLimitedLiveCapExpansionGateModel,
  buildLimitedLiveCapExpansionCanaryModel,
  buildLimitedLiveCapExpansionReportModel,
  buildLimitedLiveNextTierCapGateModel,
  buildLimitedLiveNextTierCapReportModel,
  buildLimitedLiveRolloutCanaryModel,
  buildLimitedLiveRolloutGateModel,
  buildOptionAutomationDiagnostics,
  buildPaperBrokerReconciliationModel,
  buildPaperCanaryModel,
  buildPaperOrderLifecycleCanaryModel,
  buildPaperOrderLifecycleSoakModel,
  buildRankedEntryGateModel,
  buildReplayLabModel,
  buildTransactionCostCalibrationModel,
  buildTradeAutomationForm as buildForm,
  buildTradeAutomationPayload as buildPayload,
  buildTradeAutomationPresetPayload as buildPresetPayload,
  buildValidationSampleModel,
} from '../utils/tradeAutomationModel'
import {
  normalizeAccountProfile,
  resolveAccountProfileTradingContext,
} from '../utils/accountProfileModel'

const INTERVAL_OPTIONS = ['1m', '5m', '15m', '30m', '1h', '4h', '1d']
const EXECUTION_INTENT_OPTIONS = [
  { value: 'desk', label: 'Desk only' },
  { value: 'broker_paper', label: 'Alpaca paper' },
  { value: 'broker_live', label: 'Alpaca live' },
]
const ORDER_TYPE_OPTIONS = [
  { value: 'market', label: 'Market' },
  { value: 'limit', label: 'Limit' },
]
const TIME_IN_FORCE_OPTIONS = [
  { value: 'day', label: 'DAY' },
  { value: 'day_ext', label: 'DAY_EXT' },
  { value: 'gtc_90d', label: 'GTC 90d' },
]
const TRADE_AUTOMATION_LOAD_TIMEOUT_MS = 25000

function getToneForStatus(statusKey) {
  if (statusKey === 'active' || statusKey === 'scheduled') return 'positive'
  if (statusKey === 'killed') return 'negative'
  if (statusKey === 'configured') return 'warning'
  return 'neutral'
}

function formatMoney(value) {
  const amount = Number(value || 0)
  if (!Number.isFinite(amount)) return '--'
  return `${amount >= 0 ? '$' : '-$'}${Math.abs(amount).toFixed(2)}`
}

function formatPercent(value, digits = 0) {
  const amount = Number(value)
  if (!Number.isFinite(amount)) return '--'
  return `${(amount * 100).toFixed(digits)}%`
}

function humanizeValue(value, fallback = '--') {
  const normalized = String(value || '').trim()
  if (!normalized) return fallback
  return normalized
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function formatCompactNumber(value) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return '--'
  const absolute = Math.abs(parsed)
  if (absolute >= 1000000) {
    const digits = absolute >= 10000000 ? 0 : 1
    return `${(parsed / 1000000).toFixed(digits)}M`
  }
  if (absolute >= 1000) {
    const digits = absolute >= 100000 ? 0 : 1
    return `${(parsed / 1000).toFixed(digits)}k`
  }
  return parsed.toLocaleString()
}

function formatEngineStateLabel(value) {
  const normalized = String(value || '').trim().toLowerCase()
  if (normalized === 'active') return 'Active'
  if (normalized === 'proxy_only') return 'Proxy'
  if (normalized === 'research_only') return 'Research'
  if (normalized === 'unsupported') return 'Unsupported'
  return 'Off'
}

function formatSupportMaturity(value) {
  const normalized = String(value || '').trim().toLowerCase()
  if (normalized === 'paper_routeable') return 'Paper routeable'
  if (normalized === 'proxy_scannable') return 'Proxy scannable'
  if (normalized === 'data_connected') return 'Data connected'
  if (normalized === 'live_ready_disabled') return 'Live disabled'
  return humanizeValue(normalized, 'Modeled')
}


export default function TradeAutomationSection({
  mode = 'organization',
  title,
  subtitle,
  eyebrow,
}) {
  const { pushToast } = useToast()
  const { preferences } = usePreferences()
  const [snapshot, setSnapshot] = useState(null)
  const [form, setForm] = useState(() => buildForm(null))
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [actionBusyKey, setActionBusyKey] = useState('')
  const [linkedAccounts, setLinkedAccounts] = useState([])
  const [optionsSnapshot, setOptionsSnapshot] = useState(null)
  const [candidateDiagnostics, setCandidateDiagnostics] = useState(null)
  const [deskSnapshot, setDeskSnapshot] = useState(null)
  const [marketOpsSnapshot, setMarketOpsSnapshot] = useState(null)
  const [watchdogSnapshot, setWatchdogSnapshot] = useState(null)
  const [noTradeReport, setNoTradeReport] = useState(null)
  const [productionTrust, setProductionTrust] = useState(null)
  const [productionTrustBusyKey, setProductionTrustBusyKey] = useState('')
  const [deskBusyKey, setDeskBusyKey] = useState('')
  const [selectedProxyWorkflowDeskKey, setSelectedProxyWorkflowDeskKey] = useState('equity_long_short')
  const [proxyWorkflowDiagnostics, setProxyWorkflowDiagnostics] = useState(null)
  const [proxyWorkflowBusy, setProxyWorkflowBusy] = useState(false)
  const [proxyWorkflowError, setProxyWorkflowError] = useState('')
  const isPersonalMode = mode === 'personal'
  const sectionTitle = title || (isPersonalMode ? 'Autonomous desk' : 'Trade automation')
  const sectionSubtitle =
    subtitle ||
    (isPersonalMode
    ? 'Let the workstation prep, scan, route Alpaca paper orders, and manage exits while you are away. Keep Alpaca live on a separate readiness gate.'
      : 'Arm the unattended worker, keep it paper-first, and let the server cycle the board while you are away.')
  const sectionEyebrow = eyebrow || (isPersonalMode ? 'Autonomous mode' : 'Autonomous control')
  const activeAccountProfile = normalizeAccountProfile(preferences?.activeAccountProfile)
  const defaultExecutionIntent = String(preferences?.defaultExecutionIntent || 'desk').trim().toLowerCase() || 'desk'
  const profileTradingContext = useMemo(
    () =>
      resolveAccountProfileTradingContext({
        activeAccountProfile,
        defaultExecutionIntent,
        primaryBrokerageLinkedAccountId: preferences?.primaryBrokerageLinkedAccountId,
        linkedAccounts,
      }),
    [
      activeAccountProfile,
      defaultExecutionIntent,
      linkedAccounts,
      preferences?.primaryBrokerageLinkedAccountId,
    ],
  )
  const automationScope = useMemo(() => {
    if (activeAccountProfile === 'brokerage') {
      return {
        scope: 'linked',
        scope_key: profileTradingContext.effectiveLinkedAccountId ? `linked:${profileTradingContext.effectiveLinkedAccountId}` : '',
        linked_account_id: profileTradingContext.effectiveLinkedAccountId || '',
        locked: Boolean(profileTradingContext.profileTradingLockedReason),
        lockedReason: profileTradingContext.profileTradingLockedReason,
      }
    }
    return {
      scope: activeAccountProfile,
      scope_key: activeAccountProfile,
      linked_account_id: '',
      locked: false,
      lockedReason: '',
    }
  }, [activeAccountProfile, profileTradingContext.effectiveLinkedAccountId, profileTradingContext.profileTradingLockedReason])

  useEffect(() => {
    let cancelled = false
    getLinkedBrokerageAccounts()
      .then((payload) => {
        if (!cancelled) {
          setLinkedAccounts(Array.isArray(payload?.items) ? payload.items : [])
        }
      })
      .catch(() => {
        if (!cancelled) {
          setLinkedAccounts([])
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function loadSnapshot() {
    setLoading(true)
    setError('')
    let timeoutId = null
    try {
      if (automationScope.locked) {
        setSnapshot(null)
        setOptionsSnapshot(null)
        setCandidateDiagnostics(null)
        setDeskSnapshot(null)
        setMarketOpsSnapshot(null)
        setWatchdogSnapshot(null)
        setNoTradeReport(null)
        setProductionTrust(null)
        setProxyWorkflowDiagnostics(null)
        setProxyWorkflowError('')
        setForm(buildForm(null))
        setError(automationScope.lockedReason || 'Bind a linked account before configuring automation.')
        return
      }
      const shouldLoadOptions = automationScope.scope === 'personal_paper'
      const loadPromise = Promise.all([
        getOrganizationTradeAutomation(automationScope),
        shouldLoadOptions ? getOptionsAutomationSnapshot() : Promise.resolve(null),
        getOrganizationTradeAutomationCandidateDiagnostics(automationScope).catch((err) => ({
          diagnostic_error: err?.response?.data?.detail || err?.message || 'Candidate diagnostics could not be loaded.',
        })),
        getOrganizationTradeAutomationDesks().catch(() => ({ items: [], count: 0 })),
        getOrganizationTradeAutomationMarketSession().catch(() => null),
        getOrganizationTradeAutomationWatchdog({ force: true }).catch(() => null),
        getOrganizationTradeAutomationNoTradeReport().catch(() => null),
        getOrganizationTradeAutomationProductionTrust().catch(() => null),
      ])
      const timeoutPromise = new Promise((_, reject) => {
        timeoutId = window.setTimeout(() => {
          reject(new Error(`Trade Automation data endpoint timed out after ${Math.round(TRADE_AUTOMATION_LOAD_TIMEOUT_MS / 1000)}s. Check backend /api/healthz, /api/readyz, and scripts/smoke-trade-automation-readiness.ps1.`))
        }, TRADE_AUTOMATION_LOAD_TIMEOUT_MS)
      })
      const [
        payload,
        nextOptionsSnapshot,
        nextCandidateDiagnostics,
        nextDeskSnapshot,
        nextMarketOpsSnapshot,
        nextWatchdogSnapshot,
        nextNoTradeReport,
        nextProductionTrust,
      ] = await Promise.race([loadPromise, timeoutPromise])
      setSnapshot(payload)
      setOptionsSnapshot(nextOptionsSnapshot)
      setCandidateDiagnostics(nextCandidateDiagnostics)
      setDeskSnapshot(nextDeskSnapshot)
      setMarketOpsSnapshot(nextMarketOpsSnapshot)
      setWatchdogSnapshot(nextWatchdogSnapshot)
      setNoTradeReport(nextNoTradeReport)
      setProductionTrust(nextProductionTrust)
      setForm(buildForm(payload))
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || 'Automation settings could not be loaded.')
    } finally {
      if (timeoutId) {
        window.clearTimeout(timeoutId)
      }
      setLoading(false)
    }
  }

  useEffect(() => {
    loadSnapshot()
  }, [automationScope.locked, automationScope.linked_account_id, automationScope.scope, automationScope.scope_key])

  const metrics = useMemo(() => {
    if (!snapshot) return []
    return [
      { label: 'Open positions', value: String(snapshot.counts?.open_positions ?? 0) },
      { label: 'Pending orders', value: String(snapshot.counts?.pending_orders ?? 0) },
      { label: 'Actual funds', value: formatMoney(snapshot.actual_funds) },
      { label: 'Effective funds', value: formatMoney(snapshot.effective_funds) },
      { label: 'Funds source', value: humanizeValue(snapshot.funds_source, '--') },
      { label: 'Session phase', value: snapshot.session?.phase || 'Unknown' },
      { label: 'Next run', value: snapshot.schedule?.next_run_at ? new Date(snapshot.schedule.next_run_at).toLocaleTimeString() : 'Awaiting cycle' },
    ]
  }, [snapshot])

  async function saveSettings() {
    setBusy(true)
    try {
      const payload = await updateOrganizationTradeAutomation(buildPayload(form), automationScope)
      setSnapshot(payload)
      setForm(buildForm(payload))
      pushToast('Autonomous trading settings saved.', 'success')
    } catch (err) {
      pushToast(err?.response?.data?.detail || err?.message || 'Automation settings could not be saved.', 'error')
    } finally {
      setBusy(false)
    }
  }

  async function runAction(action) {
    setActionBusyKey(action)
    try {
      const payload = await runOrganizationTradeAutomationAction(action, automationScope)
      setSnapshot(payload)
      setForm(buildForm(payload))
      pushToast(`Automation action ${action.replace(/_/g, ' ')} completed.`, 'success')
    } catch (err) {
      pushToast(err?.response?.data?.detail || err?.message || 'Automation action failed.', 'error')
    } finally {
      setActionBusyKey('')
    }
  }

  async function runProductionTrustAction(action) {
    setProductionTrustBusyKey(action)
    try {
      const payload = action === 'support_bundle'
        ? await exportOrganizationTradeAutomationSupportBundle()
        : await testOrganizationTradeAutomationAlertDelivery()
      const refreshed = await getOrganizationTradeAutomationProductionTrust().catch(() => null)
      if (refreshed) setProductionTrust(refreshed)
      const path = payload?.zip?.path || payload?.artifact?.path || payload?.directory
      pushToast(
        action === 'support_bundle'
          ? `Support bundle exported${path ? `: ${path}` : '.'}`
          : payload?.configured
            ? 'Production Trust alert test wrote a delivery proof.'
            : 'Alert delivery is not configured yet.',
        payload?.ok === false ? 'warning' : 'success',
      )
    } catch (err) {
      pushToast(err?.response?.data?.detail || err?.message || 'Production Trust action failed.', 'error')
    } finally {
      setProductionTrustBusyKey('')
    }
  }

  async function scanDesk(deskKey) {
    setDeskBusyKey(deskKey)
    try {
      const payload = await scanOrganizationTradeAutomationDesk(deskKey, { force: true })
      const nextDesks = await getOrganizationTradeAutomationDesks()
      setDeskSnapshot(nextDesks)
      const scanned = payload?.diagnostics?.summary?.scanned_count ?? payload?.desk?.runtime?.scanned_count ?? 0
      const eligible = payload?.diagnostics?.summary?.eligible_count ?? payload?.desk?.runtime?.eligible_count ?? 0
      pushToast(`${humanizeValue(deskKey)} scanned ${scanned} symbols and found ${eligible} eligible candidate${Number(eligible) === 1 ? '' : 's'}.`, 'success')
    } catch (err) {
      pushToast(err?.response?.data?.detail || err?.message || 'Desk scan failed.', 'error')
    } finally {
      setDeskBusyKey('')
    }
  }

  async function refreshProxyWorkflow(deskKey) {
    const key = String(deskKey || selectedProxyWorkflowDeskKey || 'equity_long_short').trim()
    if (!key) return
    setProxyWorkflowBusy(true)
    setProxyWorkflowError('')
    setSelectedProxyWorkflowDeskKey(key)
    try {
      const payload = await getOrganizationTradeAutomationDeskCandidateDiagnostics(key, { refresh: true })
      setProxyWorkflowDiagnostics(payload)
      const scanned = payload?.summary?.scanned_count ?? 0
      const evidence = payload?.summary?.candidate_evidence_count ?? 0
      pushToast(`${humanizeValue(key)} proxy workflow scanned ${scanned} symbols and produced ${evidence} candidate evidence row${Number(evidence) === 1 ? '' : 's'}.`, 'success')
    } catch (err) {
      const detail = err?.response?.data?.detail || err?.message || 'Proxy workflow diagnostics could not be refreshed.'
      setProxyWorkflowError(detail)
      pushToast(detail, 'error')
    } finally {
      setProxyWorkflowBusy(false)
    }
  }

  async function applyPreset(presetKey) {
    setBusy(true)
    try {
      const payload = await updateOrganizationTradeAutomation(buildPresetPayload(presetKey, snapshot), automationScope)
      setSnapshot(payload)
      setForm(buildForm(payload))
      const presetLabel =
        presetKey === 'prep'
          ? 'Prep profile'
          : presetKey === 'paper'
            ? 'Paper autopilot'
            : presetKey === 'pre_market'
              ? 'Pre-market mode'
              : presetKey === 'after_hours'
                ? 'After-hours mode'
                : 'Live pilot'
      pushToast(`${presetLabel} applied.`, 'success')
    } catch (err) {
      pushToast(err?.response?.data?.detail || err?.message || 'Automation preset could not be applied.', 'error')
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return (
      <SectionCard title={sectionTitle} subtitle="Loading the unattended control plane.">
        <FeedbackState tone="warning" title="Loading autonomous control plane" description="Reading the current arm state, route, and worker-cycle snapshot." />
      </SectionCard>
    )
  }

  if (error) {
    return (
      <SectionCard title={sectionTitle} subtitle="Automation controls are unavailable right now.">
        <ErrorState title="Automation settings unavailable" description={error} actionLabel="Retry" onAction={loadSnapshot} />
      </SectionCard>
    )
  }

  const rolloutReadiness = snapshot?.rollout_readiness || {}
  const brokerRoutes = snapshot?.broker_routes || {}
  const availableActions = snapshot?.available_actions || {}
  const historyItems = Array.isArray(snapshot?.history) ? snapshot.history.slice(0, 6) : []
  const performance = snapshot?.performance || {}
  const guardrails = snapshot?.guardrails || {}
  const brokerBalances = snapshot?.broker_balances || {}
  const performanceCards = Array.isArray(performance.cards) ? performance.cards : []
  const guardrailCards = Array.isArray(guardrails.cards) ? guardrails.cards : []
  const recentClosed = Array.isArray(performance.recent_closed) ? performance.recent_closed : []
  const recentEvents = Array.isArray(performance.recent_events) ? performance.recent_events : []
  const performanceMetrics = performance?.metrics || {}
  const guardrailMetrics = guardrails?.metrics || {}
  const brokerRouteCards = Object.values(brokerRoutes)
    .filter(Boolean)
    .map((route) => ({
      key: route.key || route.label,
      label: route.label || 'Alpaca route',
      value: route.value || 'Unknown',
      helper: route.active ? 'Current execution path' : route.detail || '',
      tone: route.active ? 'positive' : route.tone || 'default',
    }))
  const brokerBalanceCards = [
    {
      key: 'alpaca-paper-balance',
      label: 'Alpaca paper',
      value: formatMoney(brokerBalances?.alpaca_paper?.equity),
      helper: `Cash ${formatMoney(brokerBalances?.alpaca_paper?.cash)} | BP ${formatMoney(brokerBalances?.alpaca_paper?.buying_power)} | ${humanizeValue(brokerBalances?.alpaca_paper?.status, 'Missing')}`,
      tone: brokerBalances?.alpaca_paper?.status === 'ready' ? 'positive' : 'warning',
    },
  ]
  const rankedEntryGate = buildRankedEntryGateModel(rolloutReadiness)
  const rankedEntryGateCards = Array.isArray(rankedEntryGate?.metrics) ? rankedEntryGate.metrics : []
  const validationSample = buildValidationSampleModel(snapshot)
  const validationSampleCards = Array.isArray(validationSample?.metrics) ? validationSample.metrics : []
  const collectionPhase = buildCollectionPhaseModel(snapshot)
  const collectionPhaseCards = Array.isArray(collectionPhase?.metrics) ? collectionPhase.metrics : []
  const runtimeTelemetry = buildAutomationTelemetrySnapshot(snapshot)
  const optionDiagnostics = buildOptionAutomationDiagnostics(snapshot, optionsSnapshot)
  const controlPlane = buildControlPlaneModel(snapshot)
  const controlPlaneCards = Array.isArray(controlPlane?.metrics) ? controlPlane.metrics : []
  const controlPlaneOverrideRows = controlPlane.activeOverrides.length ? controlPlane.activeOverrides : controlPlane.runtimeOverrides
  const shadowValidation = controlPlane.shadowValidation
  const shadowValidationCards = Array.isArray(shadowValidation?.metrics) ? shadowValidation.metrics : []
  const shadowValidationScenarios = Array.isArray(shadowValidation?.scenarios) ? shadowValidation.scenarios : []
  const paperCanary = buildPaperCanaryModel(snapshot)
  const paperCanaryCards = Array.isArray(paperCanary?.metrics) ? paperCanary.metrics : []
  const paperCanarySessions = Array.isArray(paperCanary?.sessions) ? paperCanary.sessions : []
  const livePilotReadiness = buildLivePilotReadinessModel(snapshot)
  const livePilotReadinessCards = Array.isArray(livePilotReadiness?.metrics) ? livePilotReadiness.metrics : []
  const livePilotReadinessIssues = [
    ...(Array.isArray(livePilotReadiness?.blockers)
      ? livePilotReadiness.blockers.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'blocker' }))
      : []),
    ...(Array.isArray(livePilotReadiness?.warnings)
      ? livePilotReadiness.warnings.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'warning' }))
      : []),
    ...(Array.isArray(livePilotReadiness?.operatorActions)
      ? livePilotReadiness.operatorActions.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'action' }))
      : []),
  ]
  const livePilotSoak = buildLivePilotSoakModel(snapshot)
  const livePilotSoakCards = Array.isArray(livePilotSoak?.metrics) ? livePilotSoak.metrics : []
  const livePilotSoakIssues = [
    ...(Array.isArray(livePilotSoak?.blockers)
      ? livePilotSoak.blockers.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'blocker' }))
      : []),
    ...(Array.isArray(livePilotSoak?.warnings)
      ? livePilotSoak.warnings.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'warning' }))
      : []),
  ]
  const livePilotCanary = buildLivePilotCanaryModel(snapshot)
  const livePilotCanaryCards = Array.isArray(livePilotCanary?.metrics) ? livePilotCanary.metrics : []
  const livePilotCanarySessions = Array.isArray(livePilotCanary?.sessions) ? livePilotCanary.sessions : []
  const livePilotCanaryIssues = [
    ...(Array.isArray(livePilotCanary?.blockers)
      ? livePilotCanary.blockers.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'blocker' }))
      : []),
    ...(Array.isArray(livePilotCanary?.warnings)
      ? livePilotCanary.warnings.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'warning' }))
      : []),
  ]
  const livePilotExpansion = buildLivePilotExpansionModel(snapshot)
  const livePilotExpansionCards = Array.isArray(livePilotExpansion?.metrics) ? livePilotExpansion.metrics : []
  const livePilotExpansionIssues = [
    ...(Array.isArray(livePilotExpansion?.blockers)
      ? livePilotExpansion.blockers.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'blocker' }))
      : []),
    ...(Array.isArray(livePilotExpansion?.warnings)
      ? livePilotExpansion.warnings.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'warning' }))
      : []),
  ]
  const livePilotExpansionCanary = buildLivePilotExpansionCanaryModel(snapshot)
  const livePilotExpansionCanaryCards = Array.isArray(livePilotExpansionCanary?.metrics) ? livePilotExpansionCanary.metrics : []
  const livePilotExpansionCanarySessions = Array.isArray(livePilotExpansionCanary?.sessions) ? livePilotExpansionCanary.sessions : []
  const livePilotExpansionCanaryIssues = [
    ...(Array.isArray(livePilotExpansionCanary?.blockers)
      ? livePilotExpansionCanary.blockers.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'blocker' }))
      : []),
    ...(Array.isArray(livePilotExpansionCanary?.warnings)
      ? livePilotExpansionCanary.warnings.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'warning' }))
      : []),
  ]
  const livePilotWindow = buildLivePilotWindowModel(snapshot)
  const livePilotWindowCards = Array.isArray(livePilotWindow?.metrics) ? livePilotWindow.metrics : []
  const livePilotWindowIssues = [
    ...(Array.isArray(livePilotWindow?.blockers)
      ? livePilotWindow.blockers.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'blocker' }))
      : []),
    ...(Array.isArray(livePilotWindow?.warnings)
      ? livePilotWindow.warnings.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'warning' }))
      : []),
  ]
  const livePilotWindowCanary = buildLivePilotWindowCanaryModel(snapshot)
  const livePilotWindowCanaryCards = Array.isArray(livePilotWindowCanary?.metrics) ? livePilotWindowCanary.metrics : []
  const livePilotWindowCanarySessions = Array.isArray(livePilotWindowCanary?.sessions) ? livePilotWindowCanary.sessions : []
  const livePilotWindowCanaryIssues = [
    ...(Array.isArray(livePilotWindowCanary?.blockers)
      ? livePilotWindowCanary.blockers.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'blocker' }))
      : []),
    ...(Array.isArray(livePilotWindowCanary?.warnings)
      ? livePilotWindowCanary.warnings.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'warning' }))
      : []),
  ]
  const livePilotPromotionReport = buildLivePilotPromotionReportModel(snapshot)
  const livePilotPromotionReportCards = Array.isArray(livePilotPromotionReport?.metrics) ? livePilotPromotionReport.metrics : []
  const livePilotPromotionEvidence = Array.isArray(livePilotPromotionReport?.evidenceRows) ? livePilotPromotionReport.evidenceRows : []
  const livePilotPromotionIssues = [
    ...(Array.isArray(livePilotPromotionReport?.blockers)
      ? livePilotPromotionReport.blockers.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'blocker' }))
      : []),
    ...(Array.isArray(livePilotPromotionReport?.warnings)
      ? livePilotPromotionReport.warnings.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'warning' }))
      : []),
    ...(Array.isArray(livePilotPromotionReport?.operatorActions)
      ? livePilotPromotionReport.operatorActions.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'action' }))
      : []),
  ]
  const limitedLiveRolloutGate = buildLimitedLiveRolloutGateModel(snapshot)
  const limitedLiveRolloutCards = Array.isArray(limitedLiveRolloutGate?.metrics) ? limitedLiveRolloutGate.metrics : []
  const limitedLiveRolloutIssues = [
    ...(Array.isArray(limitedLiveRolloutGate?.blockers)
      ? limitedLiveRolloutGate.blockers.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'blocker' }))
      : []),
    ...(Array.isArray(limitedLiveRolloutGate?.warnings)
      ? limitedLiveRolloutGate.warnings.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'warning' }))
      : []),
  ]
  const limitedLiveRolloutOrders = Array.isArray(limitedLiveRolloutGate?.orders) ? limitedLiveRolloutGate.orders : []
  const limitedLiveRolloutCanary = buildLimitedLiveRolloutCanaryModel(snapshot)
  const limitedLiveRolloutCanaryCards = Array.isArray(limitedLiveRolloutCanary?.metrics) ? limitedLiveRolloutCanary.metrics : []
  const limitedLiveRolloutCanarySessions = Array.isArray(limitedLiveRolloutCanary?.sessions) ? limitedLiveRolloutCanary.sessions : []
  const limitedLiveRolloutCanaryIssues = [
    ...(Array.isArray(limitedLiveRolloutCanary?.blockers)
      ? limitedLiveRolloutCanary.blockers.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'blocker' }))
      : []),
    ...(Array.isArray(limitedLiveRolloutCanary?.warnings)
      ? limitedLiveRolloutCanary.warnings.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'warning' }))
      : []),
  ]
  const limitedLiveCapExpansionReport = buildLimitedLiveCapExpansionReportModel(snapshot)
  const limitedLiveCapExpansionCards = Array.isArray(limitedLiveCapExpansionReport?.metrics) ? limitedLiveCapExpansionReport.metrics : []
  const limitedLiveCapExpansionEvidence = Array.isArray(limitedLiveCapExpansionReport?.evidenceRows) ? limitedLiveCapExpansionReport.evidenceRows : []
  const limitedLiveCapExpansionIssues = [
    ...(Array.isArray(limitedLiveCapExpansionReport?.blockers)
      ? limitedLiveCapExpansionReport.blockers.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'blocker' }))
      : []),
    ...(Array.isArray(limitedLiveCapExpansionReport?.warnings)
      ? limitedLiveCapExpansionReport.warnings.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'warning' }))
      : []),
    ...(Array.isArray(limitedLiveCapExpansionReport?.operatorActions)
      ? limitedLiveCapExpansionReport.operatorActions.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'action' }))
      : []),
  ]
  const limitedLiveCapExpansionGate = buildLimitedLiveCapExpansionGateModel(snapshot)
  const limitedLiveCapExpansionGateCards = Array.isArray(limitedLiveCapExpansionGate?.metrics) ? limitedLiveCapExpansionGate.metrics : []
  const limitedLiveCapExpansionGateIssues = [
    ...(Array.isArray(limitedLiveCapExpansionGate?.blockers)
      ? limitedLiveCapExpansionGate.blockers.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'blocker' }))
      : []),
    ...(Array.isArray(limitedLiveCapExpansionGate?.warnings)
      ? limitedLiveCapExpansionGate.warnings.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'warning' }))
      : []),
  ]
  const limitedLiveCapExpansionGateOrders = Array.isArray(limitedLiveCapExpansionGate?.orders) ? limitedLiveCapExpansionGate.orders : []
  const limitedLiveCapExpansionCanary = buildLimitedLiveCapExpansionCanaryModel(snapshot)
  const limitedLiveCapExpansionCanaryCards = Array.isArray(limitedLiveCapExpansionCanary?.metrics) ? limitedLiveCapExpansionCanary.metrics : []
  const limitedLiveCapExpansionCanarySessions = Array.isArray(limitedLiveCapExpansionCanary?.sessions) ? limitedLiveCapExpansionCanary.sessions : []
  const limitedLiveCapExpansionCanaryIssues = [
    ...(Array.isArray(limitedLiveCapExpansionCanary?.blockers)
      ? limitedLiveCapExpansionCanary.blockers.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'blocker' }))
      : []),
    ...(Array.isArray(limitedLiveCapExpansionCanary?.warnings)
      ? limitedLiveCapExpansionCanary.warnings.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'warning' }))
      : []),
  ]
  const limitedLiveNextTierCapReport = buildLimitedLiveNextTierCapReportModel(snapshot)
  const limitedLiveNextTierCapCards = Array.isArray(limitedLiveNextTierCapReport?.metrics) ? limitedLiveNextTierCapReport.metrics : []
  const limitedLiveNextTierCapEvidence = Array.isArray(limitedLiveNextTierCapReport?.evidenceRows) ? limitedLiveNextTierCapReport.evidenceRows : []
  const limitedLiveNextTierCapIssues = [
    ...(Array.isArray(limitedLiveNextTierCapReport?.blockers)
      ? limitedLiveNextTierCapReport.blockers.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'blocker' }))
      : []),
    ...(Array.isArray(limitedLiveNextTierCapReport?.warnings)
      ? limitedLiveNextTierCapReport.warnings.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'warning' }))
      : []),
    ...(Array.isArray(limitedLiveNextTierCapReport?.operatorActions)
      ? limitedLiveNextTierCapReport.operatorActions.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'action' }))
      : []),
  ]
  const limitedLiveNextTierCapGate = buildLimitedLiveNextTierCapGateModel(snapshot)
  const limitedLiveNextTierCapGateCards = Array.isArray(limitedLiveNextTierCapGate?.metrics) ? limitedLiveNextTierCapGate.metrics : []
  const limitedLiveNextTierCapGateIssues = [
    ...(Array.isArray(limitedLiveNextTierCapGate?.blockers)
      ? limitedLiveNextTierCapGate.blockers.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'blocker' }))
      : []),
    ...(Array.isArray(limitedLiveNextTierCapGate?.warnings)
      ? limitedLiveNextTierCapGate.warnings.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'warning' }))
      : []),
  ]
  const limitedLiveNextTierCapGateOrders = Array.isArray(limitedLiveNextTierCapGate?.orders) ? limitedLiveNextTierCapGate.orders : []
  const limitedLiveCapLadder = snapshot?.limited_live_cap_ladder || {}
  const limitedLiveLadderTiers = Array.isArray(limitedLiveCapLadder?.tiers) ? limitedLiveCapLadder.tiers : []
  const limitedLiveBrokerReconciliation = snapshot?.limited_live_broker_reconciliation || {}
  const limitedLiveSessionCloseout = snapshot?.limited_live_session_closeout || {}
  const limitedLiveNextTierCapCanary = snapshot?.limited_live_next_tier_cap_canary || {}
  const limitedLiveApprovalLedger = snapshot?.limited_live_approval_ledger || {}
  const limitedLiveHigherCapReport = snapshot?.limited_live_higher_cap_report || {}
  const limitedLiveLadderToneFor = (status) => {
    const normalized = String(status || '').trim().toLowerCase()
    if (['clean', 'ready_for_operator_review', 'ready_to_request_higher_cap', 'submitted', 'active_policy', 'ready'].includes(normalized)) return 'positive'
    if (['blocked', 'failed', 'mismatch', 'active'].includes(normalized)) return 'negative'
    if (['warning', 'needs_operator_review', 'not_run', 'not_prepared'].includes(normalized)) return 'warning'
    return 'neutral'
  }
  const limitedLiveLadderCards = [
    { label: 'Next-tier canary', value: humanizeValue(limitedLiveNextTierCapCanary.status, 'Not run'), tone: limitedLiveLadderToneFor(limitedLiveNextTierCapCanary.status) },
    { label: 'Live reconcile', value: humanizeValue(limitedLiveBrokerReconciliation.status, 'Not run'), tone: limitedLiveLadderToneFor(limitedLiveBrokerReconciliation.status) },
    { label: 'Session closeout', value: humanizeValue(limitedLiveSessionCloseout.status, 'Not run'), tone: limitedLiveLadderToneFor(limitedLiveSessionCloseout.status) },
    { label: 'Higher-cap report', value: humanizeValue(limitedLiveHigherCapReport.status, 'Not run'), tone: limitedLiveLadderToneFor(limitedLiveHigherCapReport.status) },
  ]
  const limitedLiveLadderIssues = [
    ...['limited_live_next_tier_cap_canary', 'limited_live_broker_reconciliation', 'limited_live_session_closeout', 'limited_live_higher_cap_report']
      .flatMap((key) => {
        const report = snapshot?.[key] || {}
        return [
          ...(Array.isArray(report.blockers) ? report.blockers.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), source: key, severity: 'blocker' })) : []),
          ...(Array.isArray(report.warnings) ? report.warnings.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), source: key, severity: 'warning' })) : []),
        ]
      }),
  ]
  const limitedLiveApprovalEntries = Array.isArray(limitedLiveApprovalLedger?.entries) ? limitedLiveApprovalLedger.entries : []
  const paperBrokerReconciliation = buildPaperBrokerReconciliationModel(snapshot)
  const paperBrokerReconciliationCards = Array.isArray(paperBrokerReconciliation?.metrics) ? paperBrokerReconciliation.metrics : []
  const paperBrokerIssues = [
    ...(Array.isArray(paperBrokerReconciliation?.blockers)
      ? paperBrokerReconciliation.blockers.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'blocker' }))
      : []),
    ...(Array.isArray(paperBrokerReconciliation?.warnings)
      ? paperBrokerReconciliation.warnings.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'warning' }))
      : []),
  ]
  const paperOrderLifecycleSoak = buildPaperOrderLifecycleSoakModel(snapshot)
  const paperOrderLifecycleCards = Array.isArray(paperOrderLifecycleSoak?.metrics) ? paperOrderLifecycleSoak.metrics : []
  const paperOrderLifecycleIssues = [
    ...(Array.isArray(paperOrderLifecycleSoak?.blockers)
      ? paperOrderLifecycleSoak.blockers.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'blocker' }))
      : []),
    ...(Array.isArray(paperOrderLifecycleSoak?.warnings)
      ? paperOrderLifecycleSoak.warnings.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'warning' }))
      : []),
  ]
  const paperOrderLifecycleCanary = buildPaperOrderLifecycleCanaryModel(snapshot)
  const paperOrderLifecycleCanaryCards = Array.isArray(paperOrderLifecycleCanary?.metrics) ? paperOrderLifecycleCanary.metrics : []
  const paperOrderLifecycleCanarySessions = Array.isArray(paperOrderLifecycleCanary?.sessions) ? paperOrderLifecycleCanary.sessions : []
  const paperOrderLifecycleCanaryIssues = [
    ...(Array.isArray(paperOrderLifecycleCanary?.blockers)
      ? paperOrderLifecycleCanary.blockers.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'blocker' }))
      : []),
    ...(Array.isArray(paperOrderLifecycleCanary?.warnings)
      ? paperOrderLifecycleCanary.warnings.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'warning' }))
      : []),
  ]
  const replayLab = buildReplayLabModel(snapshot)
  const replayLabCards = Array.isArray(replayLab?.metrics) ? replayLab.metrics : []
  const replayLabIssues = [
    ...(Array.isArray(replayLab?.blockers)
      ? replayLab.blockers.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'blocker' }))
      : []),
    ...(Array.isArray(replayLab?.warnings)
      ? replayLab.warnings.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'warning' }))
      : []),
  ]
  const replayLabRecommendations = Array.isArray(replayLab?.recommendations) ? replayLab.recommendations : []
  const replayLabStressResults = Array.isArray(replayLab?.stressResults) ? replayLab.stressResults : []
  const replayLabSensitivity = Array.isArray(replayLab?.sensitivity) ? replayLab.sensitivity : []
  const transactionCostCalibration = buildTransactionCostCalibrationModel(snapshot)
  const transactionCostCards = Array.isArray(transactionCostCalibration?.metrics) ? transactionCostCalibration.metrics : []
  const transactionCostIssues = [
    ...(Array.isArray(transactionCostCalibration?.blockers)
      ? transactionCostCalibration.blockers.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'blocker' }))
      : []),
    ...(Array.isArray(transactionCostCalibration?.warnings)
      ? transactionCostCalibration.warnings.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'warning' }))
      : []),
  ]
  const transactionCostRows = [
    ...(Array.isArray(transactionCostCalibration?.weakSymbols)
      ? transactionCostCalibration.weakSymbols.map((item) => ({ ...item, group: 'symbol' }))
      : []),
    ...(Array.isArray(transactionCostCalibration?.weakSetups)
      ? transactionCostCalibration.weakSetups.map((item) => ({ ...item, group: 'setup' }))
      : []),
    ...(Array.isArray(transactionCostCalibration?.liquidityBuckets)
      ? transactionCostCalibration.liquidityBuckets.map((item) => ({ ...item, group: 'liquidity' }))
      : []),
  ]
  const transactionCostRecommendations = Array.isArray(transactionCostCalibration?.recommendations)
    ? transactionCostCalibration.recommendations
    : []
  const aiReview = buildAiReviewModel(snapshot)
  const aiReviewCards = Array.isArray(aiReview?.metrics) ? aiReview.metrics : []
  const accuracyCalibration = snapshot?.accuracy_calibration || {}
  const accuracyStatus = String(accuracyCalibration.status || 'not_run').trim().toLowerCase()
  const accuracyTone = accuracyStatus === 'weak'
    ? 'negative'
    : accuracyStatus === 'calibrated'
      ? 'positive'
      : accuracyStatus === 'watch' || accuracyStatus === 'collecting'
        ? 'warning'
        : 'neutral'
  const accuracyCards = [
    { label: 'Decision accuracy', value: accuracyCalibration.decision_pnl_accuracy == null ? '--' : `${Number(accuracyCalibration.decision_pnl_accuracy).toFixed(1)}`, tone: accuracyTone },
    { label: 'Expectancy', value: accuracyCalibration.calibrated_expectancy == null ? '--' : formatMoney(accuracyCalibration.calibrated_expectancy), tone: Number(accuracyCalibration.calibrated_expectancy || 0) >= 0 ? 'positive' : 'negative' },
    { label: 'Hit rate', value: accuracyCalibration.hit_rate == null ? '--' : `${(Number(accuracyCalibration.hit_rate) * 100).toFixed(1)}%`, tone: Number(accuracyCalibration.hit_rate || 0) >= 0.5 ? 'positive' : 'warning' },
    { label: 'Confidence error', value: accuracyCalibration.confidence_error == null ? '--' : Number(accuracyCalibration.confidence_error).toFixed(2), tone: Number(accuracyCalibration.confidence_error || 0) > 0.45 ? 'negative' : Number(accuracyCalibration.confidence_error || 0) > 0.35 ? 'warning' : 'positive' },
    { label: 'Selected delta', value: formatMoney(accuracyCalibration.selected_vs_rejected_delta), tone: Number(accuracyCalibration.selected_vs_rejected_delta || 0) >= 0 ? 'positive' : 'warning' },
    { label: 'Accuracy note', value: accuracyCalibration.related_note_id ? 'Linked' : '--', tone: accuracyCalibration.related_note_id ? 'positive' : 'neutral' },
  ]
  const accuracyRows = [
    ...(Array.isArray(accuracyCalibration.best_patterns)
      ? accuracyCalibration.best_patterns.map((item) => ({ ...item, group: 'best' }))
      : []),
    ...(Array.isArray(accuracyCalibration.weak_patterns)
      ? accuracyCalibration.weak_patterns.map((item) => ({ ...item, group: 'weak' }))
      : []),
  ]
  const dailyObjective = snapshot?.daily_objective || {}
  const dailyObjectiveStatus = String(dailyObjective.status || 'not_run').trim().toLowerCase()
  const dailyObjectiveTone = dailyObjectiveStatus === 'loss_budget_locked'
    ? 'negative'
    : dailyObjectiveStatus === 'target_reached' || dailyObjectiveStatus === 'target_band_reached'
      ? 'positive'
      : dailyObjectiveStatus === 'tracking'
        ? 'warning'
        : 'neutral'
  const objectiveRangeLabel = dailyObjective.objective_range_label || '1-2% weekly'
  const dailyObjectiveCards = [
    { label: 'Weekly target band', value: `${formatMoney(dailyObjective.target_min_dollars ?? form.weeklyProfitTargetMinDollars)}-${formatMoney(dailyObjective.target_dollars ?? form.weeklyProfitTargetMaxDollars)}`, tone: dailyObjectiveTone },
    { label: 'Stretch progress', value: dailyObjective.target_progress_pct == null ? '--' : `${Number(dailyObjective.target_progress_pct).toFixed(1)}%`, tone: dailyObjectiveTone },
    { label: 'Minimum gap', value: formatMoney(dailyObjective.target_min_gap), tone: Number(dailyObjective.target_min_gap || 0) <= 0 ? 'positive' : 'warning' },
    { label: 'Daily max loss', value: formatMoney(dailyObjective.loss_budget_dollars), tone: dailyObjective.entries_blocked ? 'negative' : 'neutral' },
    { label: 'Risk used', value: dailyObjective.loss_budget_used_pct == null ? '--' : `${Number(dailyObjective.loss_budget_used_pct).toFixed(1)}%`, tone: Number(dailyObjective.loss_budget_used_pct || 0) >= 100 ? 'negative' : Number(dailyObjective.loss_budget_used_pct || 0) >= 70 ? 'warning' : 'neutral' },
    { label: 'Objective note', value: dailyObjective.related_note_id ? 'Linked' : '--', tone: dailyObjective.related_note_id ? 'positive' : 'neutral' },
  ]
  const paperEvidence = snapshot?.paper_evidence_quality || {}
  const paperEvidenceStatus = String(paperEvidence.status || 'not_run').trim().toLowerCase()
  const paperEvidenceTone = paperEvidenceStatus === 'blocked'
    ? 'negative'
    : paperEvidenceStatus === 'ready'
      ? 'positive'
      : paperEvidenceStatus === 'warning' || paperEvidenceStatus === 'collecting'
        ? 'warning'
        : 'neutral'
  const paperEvidenceCards = [
    { label: 'Candidate telemetry', value: String(paperEvidence.candidate_count ?? 0), tone: Number(paperEvidence.candidate_count || 0) > 0 ? 'positive' : 'warning' },
    { label: 'Selected / rejected', value: `${paperEvidence.selected_candidate_count ?? 0} / ${paperEvidence.rejected_candidate_count ?? 0}`, tone: Number(paperEvidence.selected_candidate_count || 0) > 0 ? 'positive' : 'neutral' },
    { label: 'Edge coverage', value: paperEvidence.edge_coverage_pct == null ? '--' : `${Number(paperEvidence.edge_coverage_pct).toFixed(1)}%`, tone: Number(paperEvidence.edge_coverage_pct || 0) >= 100 ? 'positive' : 'warning' },
    { label: 'Spread coverage', value: paperEvidence.spread_coverage_pct == null ? '--' : `${Number(paperEvidence.spread_coverage_pct).toFixed(1)}%`, tone: Number(paperEvidence.spread_coverage_pct || 0) >= 100 ? 'positive' : 'warning' },
    { label: 'Paper fills', value: String(paperEvidence.paper_fill_count ?? 0), tone: Number(paperEvidence.paper_fill_count || 0) > 0 ? 'positive' : 'neutral' },
    { label: 'Evidence note', value: paperEvidence.related_note_id ? 'Linked' : '--', tone: paperEvidence.related_note_id ? 'positive' : 'neutral' },
  ]
  const paperEvidenceIssues = [
    ...(Array.isArray(paperEvidence.blockers)
      ? paperEvidence.blockers.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'blocker' }))
      : []),
    ...(Array.isArray(paperEvidence.warnings)
      ? paperEvidence.warnings.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'warning' }))
      : []),
  ]
  const lossContainment = snapshot?.loss_containment || {}
  const lossContainmentStatus = String(lossContainment.status || 'not_run').trim().toLowerCase()
  const lossContainmentTone = lossContainmentStatus === 'action_required' || lossContainmentStatus === 'blocked'
    ? 'negative'
    : lossContainmentStatus === 'watch'
      ? 'warning'
      : lossContainmentStatus === 'clean'
        ? 'positive'
        : 'neutral'
  const lossContainmentCards = [
    { label: 'Open heat', value: lossContainment.open_heat_pct == null ? '--' : `${Number(lossContainment.open_heat_pct).toFixed(2)}%`, tone: Number(lossContainment.open_heat_pct || 0) >= Number(lossContainment.max_open_heat_pct || form.lossContainmentMaxOpenHeatPct || 0.35) ? 'negative' : lossContainmentTone },
    { label: 'Unrealized PnL', value: formatMoney(lossContainment.unrealized_pnl), tone: Number(lossContainment.unrealized_pnl || 0) < 0 ? 'warning' : 'positive' },
    { label: 'Worst open loss', value: lossContainment.worst_position?.current_r == null ? '--' : `${Number(lossContainment.worst_position.current_r).toFixed(2)}R`, tone: Number(lossContainment.worst_position?.current_r || 0) < 0 ? 'negative' : 'neutral' },
    { label: 'Worst MAE', value: lossContainment.worst_position?.mae_pct == null ? '--' : `${Number(lossContainment.worst_position.mae_pct).toFixed(2)}%`, tone: Number(lossContainment.worst_position?.mae_pct || 0) > 0 ? 'warning' : 'neutral' },
    { label: 'Defensive actions', value: String(Array.isArray(lossContainment.defensive_actions) ? lossContainment.defensive_actions.length : 0), tone: Array.isArray(lossContainment.defensive_actions) && lossContainment.defensive_actions.length ? 'negative' : 'positive' },
    { label: 'Containment note', value: lossContainment.related_note_id ? 'Linked' : '--', tone: lossContainment.related_note_id ? 'positive' : 'neutral' },
  ]
  const lossContainmentIssues = [
    ...(Array.isArray(lossContainment.blockers)
      ? lossContainment.blockers.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'blocker' }))
      : []),
    ...(Array.isArray(lossContainment.warnings)
      ? lossContainment.warnings.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'warning' }))
      : []),
  ]
  const lossContainmentActions = Array.isArray(lossContainment.defensive_actions) ? lossContainment.defensive_actions : []
  const exitWatchdog = snapshot?.exit_execution_watchdog || {}
  const exitWatchdogStatus = String(exitWatchdog.status || 'not_run').trim().toLowerCase()
  const exitWatchdogTone = exitWatchdogStatus === 'halt' || exitWatchdogStatus === 'blocked'
    ? 'negative'
    : exitWatchdogStatus === 'watch'
      ? 'warning'
      : exitWatchdogStatus === 'clean'
        ? 'positive'
        : 'neutral'
  const exitWatchdogCards = [
    { label: 'Pending exits', value: String(exitWatchdog.pending_exit_count ?? 0), tone: Number(exitWatchdog.pending_exit_count || 0) ? 'warning' : exitWatchdogTone },
    { label: 'Confirmed exits', value: String(exitWatchdog.confirmed_exit_count ?? 0), tone: Number(exitWatchdog.confirmed_exit_count || 0) ? 'positive' : 'neutral' },
    { label: 'Stuck exits', value: String(exitWatchdog.stuck_exit_count ?? 0), tone: Number(exitWatchdog.stuck_exit_count || 0) ? 'negative' : 'positive' },
    { label: 'Failed exits', value: String(exitWatchdog.failed_exit_count ?? 0), tone: Number(exitWatchdog.failed_exit_count || 0) ? 'negative' : 'positive' },
    { label: 'Worst delay', value: exitWatchdog.worst_delay_seconds == null ? '--' : `${Number(exitWatchdog.worst_delay_seconds).toFixed(0)}s`, tone: Number(exitWatchdog.worst_delay_seconds || 0) > Number(exitWatchdog.max_confirmation_seconds || form.exitWatchdogMaxConfirmationSeconds || 60) ? 'negative' : 'neutral' },
    { label: 'Escalation', value: humanizeValue(exitWatchdog.escalation_status, 'Clear'), tone: exitWatchdog.manual_action_required ? 'negative' : exitWatchdog.escalation_status === 'watch' ? 'warning' : 'positive' },
    { label: 'Watchdog note', value: exitWatchdog.related_note_id ? 'Linked' : '--', tone: exitWatchdog.related_note_id ? 'positive' : 'neutral' },
  ]
  const exitWatchdogIssues = [
    ...(Array.isArray(exitWatchdog.blockers)
      ? exitWatchdog.blockers.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'blocker' }))
      : []),
    ...(Array.isArray(exitWatchdog.warnings)
      ? exitWatchdog.warnings.map((item) => ({ ...(item && typeof item === 'object' ? item : { detail: String(item || '') }), severity: 'warning' }))
      : []),
  ]
  const exitWatchdogEvaluations = Array.isArray(exitWatchdog.exit_evaluations) ? exitWatchdog.exit_evaluations : []
  const exitWatchdogRescueItems = Array.isArray(exitWatchdog.manual_rescue_checklist) ? exitWatchdog.manual_rescue_checklist : []
  const exitWatchdogReconciliation = exitWatchdog.escalation_reconciliation || {}
  const lastCandidate = runtimeTelemetry.candidate
  const lastRejection = runtimeTelemetry.rejection
  const pathEvaluations = runtimeTelemetry.pathEvaluations
  const candidateDiagnosticError = candidateDiagnostics?.diagnostic_error || ''
  const candidateDiagnosticSummary = candidateDiagnostics?.summary || {}
  const candidateDiagnosticUniverse = candidateDiagnostics?.universe || {}
  const candidateDiagnosticSizing = candidateDiagnostics?.sizing || {}
  const candidateDiagnosticItems = Array.isArray(candidateDiagnostics?.candidates) ? candidateDiagnostics.candidates : []
  const candidateDiagnosticTopRows = candidateDiagnosticItems.slice(0, 45)
  const candidateAiSummary = candidateDiagnosticSummary.ai_evidence_review || {}
  const candidateMissedReview = candidateDiagnosticSummary.missed_trade_ai_review || {}
  const candidateLifecycle = candidateDiagnosticSummary.candidate_lifecycle || {}
  const candidateRouteabilityCounts = candidateDiagnosticSummary.routeability_counts || {}
  const automationDesks = Array.isArray(deskSnapshot?.items) ? deskSnapshot.items : []
  const institutionalDeskCatalog = Array.isArray(deskSnapshot?.desk_catalog) ? deskSnapshot.desk_catalog : []
  const institutionalCoverageGroups = [
    {
      key: 'active',
      title: 'Active paper desks',
      description: 'Scheduled engines that can route through Alpaca paper after all risk gates pass.',
      items: institutionalDeskCatalog.filter((desk) => String(desk.execution_status || '').toLowerCase() === 'active'),
    },
    {
      key: 'proxy_only',
      title: 'Proxy-supported desks',
      description: 'Institutional lanes with proxy exposure and diagnostics, but no standalone order engine.',
      items: institutionalDeskCatalog.filter((desk) => String(desk.execution_status || '').toLowerCase() === 'proxy_only'),
    },
    {
      key: 'research_only',
      title: 'Research-only desks',
      description: 'Evidence and planning lanes that cannot submit unattended orders.',
      items: institutionalDeskCatalog.filter((desk) => String(desk.execution_status || '').toLowerCase() === 'research_only'),
    },
    {
      key: 'unsupported',
      title: 'Unsupported future desks',
      description: 'Modeled for future coverage. Direct execution is not connected for the current provider.',
      items: institutionalDeskCatalog.filter((desk) => String(desk.execution_status || '').toLowerCase() === 'unsupported'),
    },
  ]
  const proxyWorkflowDeskOptions = institutionalDeskCatalog.filter(
    (desk) => desk.promotion_wave === 'equity_etf_vol_wave_1',
  )
  const selectedProxyWorkflowDesk =
    proxyWorkflowDeskOptions.find((desk) => desk.desk_key === selectedProxyWorkflowDeskKey) ||
    proxyWorkflowDeskOptions[0] ||
    null
  const activeProxyWorkflowDeskKey = selectedProxyWorkflowDesk?.desk_key || selectedProxyWorkflowDeskKey || 'equity_long_short'
  const proxyWorkflowSummary = proxyWorkflowDiagnostics?.summary || {}
  const proxyWorkflowUniverse = proxyWorkflowDiagnostics?.universe || {}
  const proxyWorkflowCandidateRows = Array.isArray(proxyWorkflowDiagnostics?.candidates)
    ? proxyWorkflowDiagnostics.candidates.slice(0, 12)
    : []
  const automationDeskGlobal = deskSnapshot?.global || {}
  const marketOpsPhase = marketOpsSnapshot?.phase || {}
  const marketOpsComponents = Array.isArray(marketOpsSnapshot?.components) ? marketOpsSnapshot.components : []
  const marketOpsContinuousOpsComponent = marketOpsComponents.find((component) => component?.key === 'continuous_ops') || {}
  const marketOpsContinuousOpsMetadata = marketOpsContinuousOpsComponent.metadata || {}
  const marketOpsComponentCards = marketOpsComponents.map((component) => ({
    key: `market-ops:${component.key}`,
    label: component.label || humanizeValue(component.key, 'Component'),
    value: component.label && component.status ? humanizeValue(component.status) : humanizeValue(component.status, '--'),
    tone: String(component.status || '').toLowerCase() === 'ready'
      ? 'positive'
      : ['blocked', 'killed'].includes(String(component.status || '').toLowerCase())
        ? 'negative'
        : 'warning',
    helper: component.detail || component.next_action || '',
  }))
  const marketOpsDeskRows = Array.isArray(marketOpsSnapshot?.desks?.items)
    ? marketOpsSnapshot.desks.items.slice(0, 5)
    : []
  const marketOpsNoTrade = marketOpsSnapshot?.no_trade_escalation || noTradeReport || {}
  const marketOpsLinks = marketOpsSnapshot?.links || {}
  const watchdogStatus = String(watchdogSnapshot?.status || marketOpsSnapshot?.status || 'degraded').trim().toLowerCase()
  const watchdogTone = watchdogStatus === 'ready' || watchdogStatus === 'watching'
    ? 'positive'
    : watchdogStatus === 'blocked' || watchdogStatus === 'killed'
      ? 'negative'
      : 'warning'
  const watchdogPhase = watchdogSnapshot?.phase || marketOpsPhase || {}
  const watchdogLinks = watchdogSnapshot?.links || marketOpsLinks || {}
  const watchdogCards = Array.isArray(watchdogSnapshot?.cards)
    ? watchdogSnapshot.cards
    : Array.isArray(watchdogSnapshot?.components)
      ? watchdogSnapshot.components
      : marketOpsComponents.filter((component) => [
        'backend_api',
        'continuous_ops',
        'alpaca_paper',
        'worker_heartbeat',
        'desk_scans',
        'deep_analysis',
        'candidate_diagnostics',
        'against_market_proxy',
        'against_market',
        'hft_watchdog',
        'alpaca_reconciliation',
      ].includes(component.key))
  const watchdogMetricCards = watchdogCards.slice(0, 16).map((component) => ({
    key: `market-watchdog:${component.key}`,
    label: component.label || humanizeValue(component.key, 'Watchdog card'),
    value: humanizeValue(component.status, 'Watching'),
    tone: String(component.status || '').toLowerCase() === 'ready' || String(component.status || '').toLowerCase() === 'watching'
      ? 'positive'
      : ['blocked', 'killed'].includes(String(component.status || '').toLowerCase())
        ? 'negative'
        : 'warning',
    helper: component.blocker || component.next_action || component.detail || '',
  }))
  const entryWindowExplainer =
    marketOpsSnapshot?.entry_window_explainer ||
    candidateDiagnosticSummary.entry_window_explainer ||
    candidateDiagnostics?.entry_window_explainer ||
    {}
  const deepAnalysisMonitor = marketOpsSnapshot?.deep_analysis_monitor || automationDeskGlobal.deep_analysis || candidateDiagnostics?.deep_analysis || {}
  const institutionalRiskAllocator = marketOpsSnapshot?.institutional_risk_allocator || {}
  const sectorCorrelationHeat = marketOpsSnapshot?.sector_correlation_heat || institutionalRiskAllocator.sector_correlation_heat || {}
  const alpacaReconciliationConsole = marketOpsSnapshot?.alpaca_reconciliation_console || {}
  const orderEvidencePackets = marketOpsSnapshot?.order_evidence_packets || {}
  const diagnosticsExports = marketOpsSnapshot?.diagnostics_exports || noTradeReport?.diagnostics_exports || {}
  const readinessCache = marketOpsSnapshot?.readiness_cache || {}
  const runtimeSupervisor = marketOpsSnapshot?.runtime_supervisor || {}
  const expectedSettingsProof = marketOpsSnapshot?.expected_settings_proof || {}
  const incidentTimeline = marketOpsSnapshot?.incident_timeline || {}
  const closeArtifactIndex = marketOpsSnapshot?.close_artifact_index || {}
  const candidateLifecycleArtifact = marketOpsSnapshot?.candidate_lifecycle_artifact || candidateDiagnosticSummary.candidate_lifecycle_artifact || {}
  const missedMoveLeaderboard = marketOpsSnapshot?.missed_move_leaderboard || noTradeReport?.missed_move_leaderboard || candidateDiagnosticSummary.missed_move_leaderboard || {}
  const aiRefereeDashboard = marketOpsSnapshot?.ai_referee_dashboard || {}
  const allocatorDashboard = marketOpsSnapshot?.allocator_dashboard || institutionalRiskAllocator.allocator_dashboard || {}
  const executionQualitySummary = marketOpsSnapshot?.execution_quality_summary || {}
  const productionWeaknessClosure = marketOpsSnapshot?.production_weakness_closure || {}
  const next50TradingIntelligence = marketOpsSnapshot?.next_50_trading_intelligence || {}
  const next50InstitutionalEdge = marketOpsSnapshot?.next_50_institutional_edge || {}
  const next50EnterpriseDiligence = marketOpsSnapshot?.next_50_enterprise_diligence || {}
  const next50MarketEdgeTradeCapture = marketOpsSnapshot?.next_50_market_edge_trade_capture || {}
  const next50ResearchMemoryStrategyPromotion = marketOpsSnapshot?.next_50_research_memory_strategy_promotion || {}
  const next100EdgeFactoryProductionScale = marketOpsSnapshot?.next_100_edge_factory_production_scale || {}
  const next500QuantEvidenceOsEdge = marketOpsSnapshot?.next_500_quant_evidence_os_edge || {}
  const next1000QuantEvidenceOsScale = marketOpsSnapshot?.next_1000_quant_evidence_os_scale || {}
  const next500QuantEvidenceOsCompounding = marketOpsSnapshot?.next_500_quant_evidence_os_compounding || {}
  const next500QuantEvidenceOsInstitutionalMoat = marketOpsSnapshot?.next_500_quant_evidence_os_institutional_moat || {}
  const next500QuantEvidenceOsAdaptiveEdge = marketOpsSnapshot?.next_500_quant_evidence_os_adaptive_edge || {}
  const next500QuantEvidenceOsDecisionIntelligence = marketOpsSnapshot?.next_500_quant_evidence_os_decision_intelligence || {}
  const next500QuantEvidenceOsAutonomousImprovement = marketOpsSnapshot?.next_500_quant_evidence_os_autonomous_improvement || {}
  const next500QuantEvidenceOsMarketAdaptation = marketOpsSnapshot?.next_500_quant_evidence_os_market_adaptation || {}
  const next1000QuantEvidenceOsFrontierEdge = marketOpsSnapshot?.next_1000_quant_evidence_os_frontier_edge || {}
  const next500QuantEvidenceOsTradeSelectionEdge = marketOpsSnapshot?.next_500_quant_evidence_os_trade_selection_edge || {}
  const next500QuantEvidenceOsRealtimeAlphaOps = marketOpsSnapshot?.next_500_quant_evidence_os_realtime_alpha_ops || {}
  const next500QuantEvidenceOsAdaptiveExecutionIntelligence =
    marketOpsSnapshot?.next_500_quant_evidence_os_adaptive_execution_intelligence || {}
  const next500QuantEvidenceOsPortfolioOutcomeIntelligence =
    marketOpsSnapshot?.next_500_quant_evidence_os_portfolio_outcome_intelligence || {}
  const next5000QuantEvidenceOsInstitutionalOperatingEdge =
    marketOpsSnapshot?.next_5000_quant_evidence_os_institutional_operating_edge || {}
  const tradeSelectionEdgeContext =
    marketOpsSnapshot?.trade_selection_edge_context ||
    candidateDiagnosticSummary.trade_selection_edge_context ||
    next500QuantEvidenceOsTradeSelectionEdge.trade_selection_edge_context ||
    {}
  const realtimeAlphaOpsContext =
    marketOpsSnapshot?.realtime_alpha_ops_context ||
    candidateDiagnosticSummary.realtime_alpha_ops_context ||
    next500QuantEvidenceOsRealtimeAlphaOps.realtime_alpha_ops_context ||
    {}
  const adaptiveExecutionIntelligenceContext =
    marketOpsSnapshot?.adaptive_execution_intelligence_context ||
    candidateDiagnosticSummary.adaptive_execution_intelligence_context ||
    next500QuantEvidenceOsAdaptiveExecutionIntelligence.adaptive_execution_intelligence_context ||
    {}
  const portfolioOutcomeIntelligenceContext =
    marketOpsSnapshot?.portfolio_outcome_intelligence_context ||
    candidateDiagnosticSummary.portfolio_outcome_intelligence_context ||
    next500QuantEvidenceOsPortfolioOutcomeIntelligence.portfolio_outcome_intelligence_context ||
    {}
  const institutionalOperatingEdgeContext =
    marketOpsSnapshot?.institutional_operating_edge_context ||
    candidateDiagnosticSummary.institutional_operating_edge_context ||
    next5000QuantEvidenceOsInstitutionalOperatingEdge.institutional_operating_edge_context ||
    {}
  const againstMarketProxyContext =
    marketOpsSnapshot?.against_market_proxy_context ||
    noTradeReport?.against_market_proxy_context ||
    candidateDiagnosticSummary.against_market_proxy_context ||
    {}
  const rawEvidenceMillionTarget =
    marketOpsSnapshot?.evidence_million_target ||
    noTradeReport?.evidence_million_target ||
    candidateDiagnosticSummary.evidence_million_target ||
    {}
  const productionTrustSnapshot =
    productionTrust ||
    watchdogSnapshot?.production_trust ||
    marketOpsSnapshot?.production_trust ||
    {}
  const productionTrustSections = Array.isArray(productionTrustSnapshot.sections) ? productionTrustSnapshot.sections : []
  const productionTrustAlertDelivery = productionTrustSnapshot.alert_delivery || marketOpsSnapshot?.alert_delivery || {}
  const productionTrustOnboarding = productionTrustSnapshot.onboarding || marketOpsSnapshot?.onboarding_checklist || {}
  const productionTrustEvidenceQuality = productionTrustSnapshot.evidence_quality || marketOpsSnapshot?.evidence_quality || watchdogSnapshot?.evidence_quality || {}
  const productionTrustReplayProof = productionTrustSnapshot.replay_proof || marketOpsSnapshot?.replay_proof || watchdogSnapshot?.replay_proof || {}
  const productionTrustProviderReliability = productionTrustSnapshot.provider_reliability || marketOpsSnapshot?.provider_reliability || watchdogSnapshot?.provider_reliability || {}
  const productionTrustReleaseValidation = productionTrustSnapshot.release_validation || marketOpsSnapshot?.release_validation || {}
  const productionTrustSupportBundle = productionTrustSnapshot.support_bundle || {}
  const productionTrustStatus = String(productionTrustSnapshot.status || 'needs_attention').trim().toLowerCase()
  const productionTrustTone = productionTrustStatus === 'ready'
    ? 'positive'
    : productionTrustStatus === 'blocked'
      ? 'negative'
      : 'warning'
  const productionTrustCards = [
    {
      label: 'Alert delivery',
      value: humanizeValue(productionTrustAlertDelivery.status, 'Not configured'),
      tone: productionTrustAlertDelivery.enabled ? 'positive' : 'warning',
      helper: productionTrustAlertDelivery.next_action || 'SMTP/webhook alerts are disabled until configured.',
    },
    {
      label: 'Onboarding',
      value: `${productionTrustOnboarding.completed_count ?? 0}/${productionTrustOnboarding.total_count ?? 0}`,
      tone: productionTrustOnboarding.status === 'ready' ? 'positive' : 'warning',
      helper: productionTrustOnboarding.next_action || 'Customer launch checklist is driven by live backend status.',
    },
    {
      label: 'Evidence quality',
      value: `${Number(productionTrustEvidenceQuality.quality_score || 0).toFixed(0)}%`,
      tone: String(productionTrustEvidenceQuality.status || '').toLowerCase() === 'ready' ? 'positive' : 'warning',
      helper: `${formatCompactNumber(productionTrustEvidenceQuality.observed_event_count || 0)} observed | useful ${Number((productionTrustEvidenceQuality.useful_event_rate || 0) * 100).toFixed(0)}%`,
    },
    {
      label: 'Replay proof',
      value: humanizeValue(productionTrustReplayProof.status, 'Not configured'),
      tone: productionTrustReplayProof.status === 'ready' ? 'positive' : 'neutral',
      helper: productionTrustReplayProof.question_answers?.what_gate_blocked_it_then || 'Replay is evidence-only and cannot submit orders.',
    },
    {
      label: 'Provider reliability',
      value: humanizeValue(productionTrustProviderReliability.status, 'Watching'),
      tone: productionTrustProviderReliability.status === 'ready' ? 'positive' : productionTrustProviderReliability.status === 'blocked' ? 'negative' : 'warning',
      helper: productionTrustProviderReliability.next_action || 'Quote, OHLCV, timeout, and fallback states are checked.',
    },
    {
      label: 'Release validation',
      value: `${productionTrustReleaseValidation.passed_count ?? 0}/${productionTrustReleaseValidation.count ?? 0}`,
      tone: productionTrustReleaseValidation.status === 'ready' ? 'positive' : 'warning',
      helper: productionTrustReleaseValidation.next_action || 'Release readiness includes copy, secret, and live-autonomy scans.',
    },
  ]
  const evidenceMillionTarget = {
    ...rawEvidenceMillionTarget,
    rate_per_hour: rawEvidenceMillionTarget.rate_per_hour ?? marketOpsContinuousOpsMetadata.evidence_rate_per_hour,
    eta_hours: rawEvidenceMillionTarget.eta_hours ?? marketOpsContinuousOpsMetadata.evidence_eta_hours,
    eta_days: rawEvidenceMillionTarget.eta_days ?? marketOpsContinuousOpsMetadata.evidence_eta_days,
  }
  const evidenceMillionObserved = Number(evidenceMillionTarget.observed_event_count || 0)
  const evidenceMillionLiveObserved = Number(evidenceMillionTarget.live_observed_evidence || evidenceMillionObserved)
  const evidenceMillionSimulation = Number(evidenceMillionTarget.simulation_evidence || 0)
  const evidenceMillionQuality = evidenceMillionTarget.evidence_quality || productionTrustEvidenceQuality || {}
  const evidenceAcceleratorContext =
    marketOpsSnapshot?.evidence_accelerator_context ||
    evidenceMillionTarget.evidence_accelerator ||
    watchdogSnapshot?.evidence_accelerator_context ||
    {}
  const marketPossibilityEngineContext =
    marketOpsSnapshot?.market_possibility_engine_context ||
    candidateDiagnosticSummary.market_possibility_engine_context ||
    noTradeReport?.market_possibility_engine_context ||
    {}
  const simulationEvidenceStore =
    marketOpsSnapshot?.simulation_evidence_store ||
    evidenceMillionTarget.market_possibility_engine ||
    {}
  const evidenceMillionGoal = Number(evidenceMillionTarget.target_event_count || 100000000)
  const evidenceMillionProgressPct = Number.isFinite(Number(evidenceMillionTarget.progress_pct))
    ? Number(evidenceMillionTarget.progress_pct)
    : evidenceMillionGoal > 0
      ? (evidenceMillionObserved / evidenceMillionGoal) * 100
      : 0
  const evidenceMillionEtaDays = Number(evidenceMillionTarget.eta_days)
  const evidenceMillionEtaHours = Number(evidenceMillionTarget.eta_hours)
  const evidenceMillionEtaLabel = Number.isFinite(evidenceMillionEtaDays)
    ? `${evidenceMillionEtaDays.toFixed(1)} days`
    : Number.isFinite(evidenceMillionEtaHours)
      ? `${(evidenceMillionEtaHours / 24).toFixed(1)} days`
      : 'Collecting'
  const evidenceMillionRate = Number(evidenceMillionTarget.rate_per_hour)
  const roadmapEvidenceActivation = marketOpsSnapshot?.roadmap_evidence_activation || {}
  const readOnlyActivationAudit = marketOpsSnapshot?.read_only_activation_audit || {}
  const customerSafeEmptyStates = marketOpsSnapshot?.customer_safe_empty_states || {}
  const quantEvidenceControlPlane =
    marketOpsSnapshot?.quant_evidence_control_plane ||
    candidateDiagnosticSummary.quant_evidence_control_plane ||
    automationDeskGlobal.quant_evidence_control_plane ||
    snapshot?.quant_evidence_control_plane ||
    {}
  const quantEvidencePillars = Array.isArray(quantEvidenceControlPlane.evidence_pillars)
    ? quantEvidenceControlPlane.evidence_pillars
    : []
  const quantEvidenceQuestions = quantEvidenceControlPlane.operator_questions || {}
  const institutionalPositionPolicy =
    quantEvidenceControlPlane.position_policy ||
    automationDeskGlobal.position_policy ||
    automationDeskGlobal.institutional_position_policy ||
    candidateDiagnosticSummary.institutional_position_policy ||
    snapshot?.institutional_position_policy ||
    {}
  const positionPromotion =
    automationDeskGlobal.position_promotion ||
    marketOpsSnapshot?.position_promotion ||
    snapshot?.position_promotion ||
    {}
  const tradeAutomationReadiness = snapshot?.trade_automation_readiness || {}
  const readinessStatus = String(tradeAutomationReadiness.status || 'warning').trim().toLowerCase()
  const readinessTone = readinessStatus === 'ready' ? 'positive' : readinessStatus === 'blocked' ? 'negative' : 'warning'
  const readinessCategories = Array.isArray(tradeAutomationReadiness.categories) ? tradeAutomationReadiness.categories : []
  const readinessCards = readinessCategories.map((category) => ({
    key: category.key,
    label: category.label,
    value: `${Number(category.percent || 0).toFixed(0)}%`,
    tone: String(category.status || '').toLowerCase() === 'ready' ? 'positive' : String(category.status || '').toLowerCase() === 'blocked' ? 'negative' : 'warning',
  }))
  const readinessIssues = [
    ...(Array.isArray(tradeAutomationReadiness.blockers)
      ? tradeAutomationReadiness.blockers.map((detail, index) => ({ severity: 'blocker', detail, key: `blocker-${index}` }))
      : []),
    ...(Array.isArray(tradeAutomationReadiness.warnings)
      ? tradeAutomationReadiness.warnings.map((detail, index) => ({ severity: 'warning', detail, key: `warning-${index}` }))
      : []),
  ]

  const renderMetricCard = (item, fallbackKey) => {
    const { key, ...metricProps } = item || {}
    return <MetricCard key={key || metricProps.label || fallbackKey} {...metricProps} />
  }

  return (
    <SectionCard
      title={sectionTitle}
      subtitle={sectionSubtitle}
      eyebrow={sectionEyebrow}
    >
      <FeedbackState
        tone={getToneForStatus(snapshot?.status?.key)}
        title={snapshot?.status?.label || 'Automation'}
        description={snapshot?.status?.detail || 'The unattended control plane is ready.'}
      />
      <FeedbackState
        tone={readinessTone}
        title={`Trade Automation Readiness: ${Number(tradeAutomationReadiness.overall_percent || 0).toFixed(0)}%`}
        description={tradeAutomationReadiness.next_action || 'Readiness checks are loading from the backend snapshot.'}
      />
      <ExecutionProviderDiagnostics />
      {readinessCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {readinessCards.map((item, index) => renderMetricCard(item, `trade-readiness-${index}`))}
        </section>
      ) : null}
      <section className="section-stack">
        <FeedbackState
          tone={watchdogTone}
          title={`Market Watchdog: ${watchdogSnapshot?.label || humanizeValue(watchdogStatus, 'Needs attention')}`}
          description={watchdogSnapshot?.next_action || watchdogPhase.description || 'Market Watchdog is checking backend/API, frontend, Alpaca paper, worker heartbeat, desk scans, deep analysis, candidate diagnostics, reconciliation, kill switch, and no-trade checkpoints.'}
        />
        <div className="metric-strip">
          <span>Phase {humanizeValue(watchdogPhase.phase, 'Unknown')}</span>
          <span>Next {watchdogPhase.next_checkpoint || marketOpsPhase.next_checkpoint || 'Awaiting checkpoint'}</span>
          <span>{watchdogSnapshot?.paper_route_only === false || marketOpsSnapshot?.paper_route_only === false ? 'Route needs review' : 'Alpaca paper execution'}</span>
          <span>Against market {againstMarketProxyContext.routeable_proxy_count ?? 0}/{againstMarketProxyContext.signal_count ?? 0}</span>
          <span>{againstMarketProxyContext.direct_short_authority ? 'Short authority needs review' : 'No direct shorts'}</span>
          <span>No-trade stage {humanizeValue(marketOpsNoTrade.stage || marketOpsNoTrade.escalation_stage, 'Monitoring')}</span>
          <span>{marketOpsNoTrade.zero_trades_by_noon ? 'Noon report active' : marketOpsNoTrade.zero_trades_by_1030 ? '10:30 refresh window' : 'No escalation'}</span>
          <span>Strong missed {marketOpsNoTrade.strong_missed_opportunity_count ?? 0}</span>
          <span>Watchdog cards {watchdogCards.length || 0}</span>
          <span>Trade state writes {watchdogSnapshot?.writes_trade_state ? 'needs review' : 'off'}</span>
          <span>Proof artifact on demand</span>
          <span>Evidence active {roadmapEvidenceActivation.active_bundle_count ?? 0}/{roadmapEvidenceActivation.bundle_count ?? 0}</span>
          <span>Activation audit {readOnlyActivationAudit.read_only_count ?? 0}/{readOnlyActivationAudit.checked_bundle_count ?? 0}</span>
          <span>Paper evidence writes {roadmapEvidenceActivation.can_write_artifacts ? 'on' : 'off'}</span>
          <span>Production closure {productionWeaknessClosure.closed_count ?? 0}/{productionWeaknessClosure.item_count ?? 50}</span>
          <span>Next 50 intelligence {next50TradingIntelligence.implemented_count ?? 0}/{next50TradingIntelligence.item_count ?? 50}</span>
          <span>Institutional edge {next50InstitutionalEdge.implemented_count ?? 0}/{next50InstitutionalEdge.item_count ?? 50}</span>
          <span>Enterprise diligence {next50EnterpriseDiligence.implemented_count ?? 0}/{next50EnterpriseDiligence.item_count ?? 50}</span>
          <span>Market edge {next50MarketEdgeTradeCapture.implemented_count ?? 0}/{next50MarketEdgeTradeCapture.item_count ?? 50}</span>
          <span>Research memory {next50ResearchMemoryStrategyPromotion.implemented_count ?? 0}/{next50ResearchMemoryStrategyPromotion.item_count ?? 50}</span>
          <span>Edge factory {next100EdgeFactoryProductionScale.implemented_count ?? 0}/{next100EdgeFactoryProductionScale.item_count ?? 100}</span>
          <span>Live mirrors off {next100EdgeFactoryProductionScale.live_enabled_count ?? 0}/{next100EdgeFactoryProductionScale.live_item_count ?? 100}</span>
          <span>Quant Evidence OS {next500QuantEvidenceOsEdge.implemented_count ?? 0}/{next500QuantEvidenceOsEdge.item_count ?? 500}</span>
          <span>Next 500 live off {next500QuantEvidenceOsEdge.live_enabled_count ?? 0}/{next500QuantEvidenceOsEdge.live_item_count ?? 500}</span>
          <span>Scale layer {next1000QuantEvidenceOsScale.implemented_count ?? 0}/{next1000QuantEvidenceOsScale.item_count ?? 1000}</span>
          <span>Next 1000 live off {next1000QuantEvidenceOsScale.live_enabled_count ?? 0}/{next1000QuantEvidenceOsScale.live_item_count ?? 1000}</span>
          <span>Compounding {next500QuantEvidenceOsCompounding.implemented_count ?? 0}/{next500QuantEvidenceOsCompounding.item_count ?? 500}</span>
          <span>Compounding live off {next500QuantEvidenceOsCompounding.live_enabled_count ?? 0}/{next500QuantEvidenceOsCompounding.live_item_count ?? 500}</span>
          <span>Institutional moat {next500QuantEvidenceOsInstitutionalMoat.implemented_count ?? 0}/{next500QuantEvidenceOsInstitutionalMoat.item_count ?? 500}</span>
          <span>Moat live off {next500QuantEvidenceOsInstitutionalMoat.live_enabled_count ?? 0}/{next500QuantEvidenceOsInstitutionalMoat.live_item_count ?? 500}</span>
          <span>Adaptive edge {next500QuantEvidenceOsAdaptiveEdge.implemented_count ?? 0}/{next500QuantEvidenceOsAdaptiveEdge.item_count ?? 500}</span>
          <span>Adaptive live off {next500QuantEvidenceOsAdaptiveEdge.live_enabled_count ?? 0}/{next500QuantEvidenceOsAdaptiveEdge.live_item_count ?? 500}</span>
          <span>Decision intelligence {next500QuantEvidenceOsDecisionIntelligence.implemented_count ?? 0}/{next500QuantEvidenceOsDecisionIntelligence.item_count ?? 500}</span>
          <span>Decision live off {next500QuantEvidenceOsDecisionIntelligence.live_enabled_count ?? 0}/{next500QuantEvidenceOsDecisionIntelligence.live_item_count ?? 500}</span>
          <span>Improvement governance {next500QuantEvidenceOsAutonomousImprovement.implemented_count ?? 0}/{next500QuantEvidenceOsAutonomousImprovement.item_count ?? 500}</span>
          <span>Improvement live off {next500QuantEvidenceOsAutonomousImprovement.live_enabled_count ?? 0}/{next500QuantEvidenceOsAutonomousImprovement.live_item_count ?? 500}</span>
          <span>Market adaptation {next500QuantEvidenceOsMarketAdaptation.implemented_count ?? 0}/{next500QuantEvidenceOsMarketAdaptation.item_count ?? 500}</span>
          <span>Adaptation live off {next500QuantEvidenceOsMarketAdaptation.live_enabled_count ?? 0}/{next500QuantEvidenceOsMarketAdaptation.live_item_count ?? 500}</span>
          <span>Frontier edge {next1000QuantEvidenceOsFrontierEdge.implemented_count ?? 0}/{next1000QuantEvidenceOsFrontierEdge.item_count ?? 1000}</span>
          <span>Frontier live off {next1000QuantEvidenceOsFrontierEdge.live_enabled_count ?? 0}/{next1000QuantEvidenceOsFrontierEdge.live_item_count ?? 1000}</span>
          <span>Adaptive execution {next500QuantEvidenceOsAdaptiveExecutionIntelligence.implemented_count ?? 0}/{next500QuantEvidenceOsAdaptiveExecutionIntelligence.item_count ?? 500}</span>
          <span>Execution live off {next500QuantEvidenceOsAdaptiveExecutionIntelligence.live_enabled_count ?? 0}/{next500QuantEvidenceOsAdaptiveExecutionIntelligence.live_item_count ?? 500}</span>
        </div>
        {watchdogMetricCards.length ? (
          <section className="metrics-grid metrics-grid--compact">
            {watchdogMetricCards.map((item, index) => renderMetricCard(item, `market-watchdog-${index}`))}
          </section>
        ) : null}
        <FeedbackState
          tone={productionTrustTone}
          title={`Production Trust Center: ${humanizeValue(productionTrustSnapshot.status, 'Needs attention')}`}
          description={productionTrustSnapshot.next_action || 'Launch readiness, alert delivery, onboarding, support bundle, evidence quality, replay proof, provider reliability, and release validation are tracked without order authority.'}
        />
        <div className="metric-strip">
          <span>{productionTrustSnapshot.can_submit_orders ? 'Order authority needs review' : 'No order authority'}</span>
          <span>{productionTrustSnapshot.can_submit_live_orders ? 'Live authority needs review' : 'No autonomous live orders'}</span>
          <span>Alert delivery {humanizeValue(productionTrustAlertDelivery.status, 'Not configured')}</span>
          <span>Support bundle {humanizeValue(productionTrustSupportBundle.status, 'Not exported')}</span>
          <span>Replay {productionTrustReplayProof.evidence_only === false ? 'needs review' : 'evidence only'}</span>
          <span>Provider {humanizeValue(productionTrustProviderReliability.status, 'Watching')}</span>
        </div>
        <section className="metrics-grid metrics-grid--compact">
          {productionTrustCards.map((item, index) => renderMetricCard(item, `production-trust-${index}`))}
        </section>
        {productionTrustSections.length ? (
          <div className="chip-row">
            {productionTrustSections.map((section) => (
              <span className="chip chip--inline" key={`production-trust-section-${section.key || section.label}`}>
                {section.label || humanizeValue(section.key, 'Trust section')}: {humanizeValue(section.status, 'Watching')}
              </span>
            ))}
          </div>
        ) : null}
        <div className="action-row">
          <Button
            type="button"
            variant="ghost"
            onClick={() => runProductionTrustAction('alert_test')}
            disabled={Boolean(productionTrustBusyKey)}
          >
            {productionTrustBusyKey === 'alert_test' ? 'Testing alert...' : 'Test alert path'}
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={() => runProductionTrustAction('support_bundle')}
            disabled={Boolean(productionTrustBusyKey)}
          >
            {productionTrustBusyKey === 'support_bundle' ? 'Exporting bundle...' : 'Export support bundle'}
          </Button>
          <a className="chip chip--inline" href="/api/orgs/trade-automation/production-trust" target="_blank" rel="noreferrer">
            Trust API
          </a>
          <a className="chip chip--inline" href="/api/orgs/trade-automation/evidence-quality" target="_blank" rel="noreferrer">
            Evidence quality
          </a>
          <a className="chip chip--inline" href="/api/orgs/trade-automation/replay-report" target="_blank" rel="noreferrer">
            Replay proof
          </a>
        </div>
        <FeedbackState
          tone={entryWindowExplainer.entry_allowed ? 'positive' : ['blocked_by_safety', 'close_cleanup'].includes(String(entryWindowExplainer.state || '').toLowerCase()) ? 'negative' : 'warning'}
          title={`Entry window: ${humanizeValue(entryWindowExplainer.state, 'Waiting for proof')}`}
          description={entryWindowExplainer.next_action || 'Entries need an open market window, fresh data, candidate evidence, risk approval, and Alpaca paper routing.'}
        />
        <section className="metrics-grid metrics-grid--compact">
          {[
            {
              label: 'Deep analysis',
              value: humanizeValue(deepAnalysisMonitor.status, 'Idle'),
              tone: deepAnalysisMonitor.circuit_open || deepAnalysisMonitor.status === 'deep_analysis_failed' ? 'negative' : Number(deepAnalysisMonitor.inflight_count || 0) > 0 ? 'warning' : 'neutral',
              helper: `Inflight ${deepAnalysisMonitor.inflight_count ?? 0} | failures ${deepAnalysisMonitor.failure_count ?? deepAnalysisMonitor.failed_count ?? 0}`,
            },
            {
              label: 'Against Market',
              value: `${againstMarketProxyContext.routeable_proxy_count ?? 0}/${againstMarketProxyContext.signal_count ?? 0}`,
              tone: Number(againstMarketProxyContext.routeable_proxy_count || 0) > 0
                ? 'positive'
                : Number(againstMarketProxyContext.signal_count || 0) > 0
                  ? 'warning'
                  : 'neutral',
              helper: againstMarketProxyContext.next_action || 'Paper-only buy orders in inverse proxies; direct shorts and leveraged inverse ETFs stay off.',
            },
            {
              label: 'Sector heat',
              value: sectorCorrelationHeat.crowding_detected ? 'Crowded' : 'Clear',
              tone: sectorCorrelationHeat.crowding_detected ? 'warning' : 'positive',
              helper: sectorCorrelationHeat.next_action || 'Position capacity is secondary to notional, sector, and correlation heat.',
            },
            {
              label: 'Alpaca reconciliation',
              value: humanizeValue(alpacaReconciliationConsole.status, 'Loading'),
              tone: alpacaReconciliationConsole.status === 'blocked' ? 'negative' : alpacaReconciliationConsole.status === 'ready' ? 'positive' : 'warning',
              helper: `Open ${alpacaReconciliationConsole.local_broker_match?.open_count ?? 0} | Pending ${alpacaReconciliationConsole.local_broker_match?.pending_count ?? 0}`,
            },
            {
              label: 'Order packets',
              value: `${orderEvidencePackets.packet_ready_count ?? 0}/${orderEvidencePackets.packet_count ?? 0}`,
              tone: Number(orderEvidencePackets.packet_count || 0) === 0 ? 'neutral' : Number(orderEvidencePackets.packet_ready_count || 0) === Number(orderEvidencePackets.packet_count || 0) ? 'positive' : 'warning',
              helper: 'Candidate, risk, AI, receipt, reconciliation, and execution evidence.',
            },
          ].map((item, index) => renderMetricCard(item, `market-ops-proof-${index}`))}
        </section>
        <section className="metrics-grid metrics-grid--compact">
          {[
            {
              label: 'Readiness cache',
              value: readinessCache.cache_age_seconds == null ? humanizeValue(readinessCache.status, 'Unknown') : `${Math.round(Number(readinessCache.cache_age_seconds))}s`,
              tone: readinessCache.status === 'stale' ? 'warning' : readinessCache.status === 'fresh' ? 'positive' : 'neutral',
              helper: readinessCache.next_action || 'Ready endpoint age is tracked for production proof.',
            },
            {
              label: 'Runtime supervisor',
              value: humanizeValue(runtimeSupervisor.status, 'Unknown'),
              tone: runtimeSupervisor.status === 'ready' ? 'positive' : 'warning',
              helper: `API ${runtimeSupervisor.backend?.port ?? 8000} | Web ${runtimeSupervisor.frontend?.port ?? 5173}`,
            },
            {
              label: 'Settings proof',
              value: `${expectedSettingsProof.passed_count ?? 0}/${expectedSettingsProof.count ?? 0}`,
              tone: expectedSettingsProof.status === 'ready' ? 'positive' : 'warning',
              helper: 'Route, account floor, ticker board, risk caps, kill switch, and desks.',
            },
            {
              label: 'Lifecycle artifact',
              value: String(candidateLifecycleArtifact.tracked_count ?? candidateLifecycle.tracked_count ?? 0),
              tone: Number(candidateLifecycleArtifact.tracked_count ?? candidateLifecycle.tracked_count ?? 0) > 0 ? 'positive' : 'neutral',
              helper: candidateLifecycleArtifact.next_action || 'Append-only candidate lifecycle proof.',
            },
            {
              label: 'AI referee',
              value: `${aiRefereeDashboard.reviewed_count ?? candidateAiSummary.reviewed_count ?? 0} reviewed`,
              tone: aiRefereeDashboard.status === 'enabled' ? 'positive' : 'neutral',
              helper: `${humanizeValue(aiRefereeDashboard.mode, 'Shadow review')} | cannot override risk gates.`,
            },
            {
              label: 'Execution quality',
              value: `${executionQualitySummary.packet_ready_count ?? orderEvidencePackets.packet_ready_count ?? 0}/${executionQualitySummary.packet_count ?? orderEvidencePackets.packet_count ?? 0}`,
              tone: executionQualitySummary.reconciliation_state === 'review_required' ? 'warning' : 'neutral',
              helper: executionQualitySummary.next_action || 'Fill, slippage, latency, receipt, and reconciliation proof.',
            },
            {
              label: 'Production closure',
              value: `${productionWeaknessClosure.closed_count ?? 0}/${productionWeaknessClosure.item_count ?? 50}`,
              tone: Number(productionWeaknessClosure.strong_failure_count || 0) > 0 ? 'negative' : Number(productionWeaknessClosure.weak_open_count || 0) > 0 ? 'warning' : 'positive',
              helper: `${productionWeaknessClosure.strong_failure_count ?? 0} strong failures | ${productionWeaknessClosure.weak_open_count ?? 0} weak open items`,
            },
            {
              label: 'Evidence activation',
              value: `${roadmapEvidenceActivation.active_bundle_count ?? 0}/${roadmapEvidenceActivation.bundle_count ?? 0}`,
              tone: Number(roadmapEvidenceActivation.active_bundle_count || 0) === Number(roadmapEvidenceActivation.bundle_count || 0) && Number(roadmapEvidenceActivation.bundle_count || 0) > 0 ? 'positive' : 'warning',
              helper: roadmapEvidenceActivation.next_action || 'Roadmap layers now feed active paper evidence workflows.',
            },
            {
              label: 'Activation audit',
              value: `${readOnlyActivationAudit.read_only_count ?? 0} inactive`,
              tone: Number(readOnlyActivationAudit.read_only_count || 0) > 0 || Number(readOnlyActivationAudit.inactive_count || 0) > 0 || Number(readOnlyActivationAudit.item_read_only_count || 0) > 0 || Number(readOnlyActivationAudit.inactive_item_count || 0) > 0 ? 'warning' : 'positive',
              helper: readOnlyActivationAudit.next_action || 'All roadmap layers are active paper evidence inputs.',
            },
            {
              label: 'Active evidence items',
              value: String(roadmapEvidenceActivation.active_item_count ?? 0),
              tone: Number(roadmapEvidenceActivation.live_enabled_count || 0) > 0 ? 'warning' : 'positive',
              helper: `${humanizeValue(roadmapEvidenceActivation.mutation, 'Paper evidence state')} | no direct order mutation.`,
            },
            {
              label: 'Next 50 intelligence',
              value: `${next50TradingIntelligence.implemented_count ?? 0}/${next50TradingIntelligence.item_count ?? 50}`,
              tone: Number(next50TradingIntelligence.degraded_count || 0) > 0 ? 'warning' : 'positive',
              helper: `${next50TradingIntelligence.data_pending_count ?? 0} waiting for market data | active paper evidence`,
            },
            {
              label: 'Institutional edge',
              value: `${next50InstitutionalEdge.implemented_count ?? 0}/${next50InstitutionalEdge.item_count ?? 50}`,
              tone: Number(next50InstitutionalEdge.degraded_count || 0) > 0 ? 'warning' : 'positive',
              helper: 'Research memory, data quality, governance, customer ops, and scale proof.',
            },
            {
              label: 'Enterprise diligence',
              value: `${next50EnterpriseDiligence.implemented_count ?? 0}/${next50EnterpriseDiligence.item_count ?? 50}`,
              tone: Number(next50EnterpriseDiligence.degraded_count || 0) > 0 ? 'warning' : 'positive',
              helper: 'Security, audit, reliability, deployment, and commercial proof.',
            },
            {
              label: 'Market edge',
              value: `${next50MarketEdgeTradeCapture.implemented_count ?? 0}/${next50MarketEdgeTradeCapture.item_count ?? 50}`,
              tone: Number(next50MarketEdgeTradeCapture.degraded_count || 0) > 0 ? 'warning' : 'positive',
              helper: 'Entry capture, missed moves, AI verdicts, and heat-aware allocation proof.',
            },
            {
              label: 'Research memory',
              value: `${next50ResearchMemoryStrategyPromotion.implemented_count ?? 0}/${next50ResearchMemoryStrategyPromotion.item_count ?? 50}`,
              tone: Number(next50ResearchMemoryStrategyPromotion.degraded_count || 0) > 0 ? 'warning' : 'positive',
              helper: 'Replay, regime memory, promotion gates, and daily strategy improvement proof.',
            },
            {
              label: 'Edge factory',
              value: `${next100EdgeFactoryProductionScale.implemented_count ?? 0}/${next100EdgeFactoryProductionScale.item_count ?? 100}`,
              tone: Number(next100EdgeFactoryProductionScale.degraded_count || 0) > 0 ? 'warning' : 'positive',
              helper: 'Data quality, signals, entries, exits, sizing, desks, regimes, memory, promotion, and trust.',
            },
            {
              label: 'Live mirror',
              value: `${next100EdgeFactoryProductionScale.live_enabled_count ?? 0}/${next100EdgeFactoryProductionScale.live_item_count ?? 100} on`,
              tone: Number(next100EdgeFactoryProductionScale.live_enabled_count || 0) > 0 ? 'warning' : 'neutral',
              helper: 'Live-capable diagnostics are present but disabled until live-control gates are explicitly enabled.',
            },
            {
              label: 'Quant Evidence OS',
              value: `${next500QuantEvidenceOsEdge.implemented_count ?? 0}/${next500QuantEvidenceOsEdge.item_count ?? 500}`,
              tone: Number(next500QuantEvidenceOsEdge.degraded_count || 0) > 0 ? 'warning' : 'positive',
              helper: 'Evidence graph, missed moves, regimes, AI referee, allocator, execution proof, and enterprise readiness.',
            },
            {
              label: 'Next 500 live mirrors',
              value: `${next500QuantEvidenceOsEdge.live_enabled_count ?? 0}/${next500QuantEvidenceOsEdge.live_item_count ?? 500} on`,
              tone: Number(next500QuantEvidenceOsEdge.live_enabled_count || 0) > 0 ? 'warning' : 'neutral',
              helper: 'Every edge workstream has a live-compatible mirror, but live remains off.',
            },
            {
              label: 'Quant OS scale',
              value: `${next1000QuantEvidenceOsScale.implemented_count ?? 0}/${next1000QuantEvidenceOsScale.item_count ?? 1000}`,
              tone: Number(next1000QuantEvidenceOsScale.degraded_count || 0) > 0 ? 'warning' : 'positive',
              helper: 'Scale layer for evidence graph, research memory, risk, execution proof, adapters, and release acceptance.',
            },
            {
              label: 'Next 1000 live mirrors',
              value: `${next1000QuantEvidenceOsScale.live_enabled_count ?? 0}/${next1000QuantEvidenceOsScale.live_item_count ?? 1000} on`,
              tone: Number(next1000QuantEvidenceOsScale.live_enabled_count || 0) > 0 ? 'warning' : 'neutral',
              helper: 'Live-compatible metadata is visible for diligence, but live submission remains disabled.',
            },
            {
              label: 'Evidence compounding',
              value: `${next500QuantEvidenceOsCompounding.implemented_count ?? 0}/${next500QuantEvidenceOsCompounding.item_count ?? 500}`,
              tone: Number(next500QuantEvidenceOsCompounding.degraded_count || 0) > 0 ? 'warning' : 'positive',
              helper: 'Adds proof QA, alpha decay, replay, customer proof rooms, adapter certification, and release governance.',
            },
            {
              label: 'Compounding live mirrors',
              value: `${next500QuantEvidenceOsCompounding.live_enabled_count ?? 0}/${next500QuantEvidenceOsCompounding.live_item_count ?? 500} on`,
              tone: Number(next500QuantEvidenceOsCompounding.live_enabled_count || 0) > 0 ? 'warning' : 'neutral',
              helper: 'The compounding layer is live-compatible for diligence, but live remains off.',
            },
            {
              label: 'Institutional moat',
              value: `${next500QuantEvidenceOsInstitutionalMoat.implemented_count ?? 0}/${next500QuantEvidenceOsInstitutionalMoat.item_count ?? 500}`,
              tone: Number(next500QuantEvidenceOsInstitutionalMoat.degraded_count || 0) > 0 ? 'warning' : 'positive',
              helper: 'Adds buyer diligence, proof rooms, governance, model risk, compliance exports, and product moat packaging.',
            },
            {
              label: 'Moat live mirrors',
              value: `${next500QuantEvidenceOsInstitutionalMoat.live_enabled_count ?? 0}/${next500QuantEvidenceOsInstitutionalMoat.live_item_count ?? 500} on`,
              tone: Number(next500QuantEvidenceOsInstitutionalMoat.live_enabled_count || 0) > 0 ? 'warning' : 'neutral',
              helper: 'Institutional moat capabilities are visible for diligence, but live remains off.',
            },
            {
              label: 'Adaptive edge',
              value: `${next500QuantEvidenceOsAdaptiveEdge.implemented_count ?? 0}/${next500QuantEvidenceOsAdaptiveEdge.item_count ?? 500}`,
              tone: Number(next500QuantEvidenceOsAdaptiveEdge.degraded_count || 0) > 0 ? 'warning' : 'positive',
              helper: 'Adds adaptive calibration, missed-edge replay, risk explanations, execution simulation, and buyer proof rooms.',
            },
            {
              label: 'Adaptive live mirrors',
              value: `${next500QuantEvidenceOsAdaptiveEdge.live_enabled_count ?? 0}/${next500QuantEvidenceOsAdaptiveEdge.live_item_count ?? 500} on`,
              tone: Number(next500QuantEvidenceOsAdaptiveEdge.live_enabled_count || 0) > 0 ? 'warning' : 'neutral',
              helper: 'Adaptive edge capabilities are active for paper evidence; live remains off.',
            },
            {
              label: 'Decision intelligence',
              value: `${next500QuantEvidenceOsDecisionIntelligence.implemented_count ?? 0}/${next500QuantEvidenceOsDecisionIntelligence.item_count ?? 500}`,
              tone: Number(next500QuantEvidenceOsDecisionIntelligence.degraded_count || 0) > 0 ? 'warning' : 'positive',
              helper: 'Adds decision context, causal blocker attribution, experiments, confidence scoring, and operator-safe actions.',
            },
            {
              label: 'Decision live mirrors',
              value: `${next500QuantEvidenceOsDecisionIntelligence.live_enabled_count ?? 0}/${next500QuantEvidenceOsDecisionIntelligence.live_item_count ?? 500} on`,
              tone: Number(next500QuantEvidenceOsDecisionIntelligence.live_enabled_count || 0) > 0 ? 'warning' : 'neutral',
              helper: 'Decision intelligence is active for paper evidence; live remains off.',
            },
            {
              label: 'Improvement governance',
              value: `${next500QuantEvidenceOsAutonomousImprovement.implemented_count ?? 0}/${next500QuantEvidenceOsAutonomousImprovement.item_count ?? 500}`,
              tone: Number(next500QuantEvidenceOsAutonomousImprovement.degraded_count || 0) > 0 ? 'warning' : 'positive',
              helper: 'Adds safe learning loops, paper experiments, blocker audits, playbook versioning, and release gates.',
            },
            {
              label: 'Improvement live mirrors',
              value: `${next500QuantEvidenceOsAutonomousImprovement.live_enabled_count ?? 0}/${next500QuantEvidenceOsAutonomousImprovement.live_item_count ?? 500} on`,
              tone: Number(next500QuantEvidenceOsAutonomousImprovement.live_enabled_count || 0) > 0 ? 'warning' : 'neutral',
              helper: 'Autonomous improvement is active for paper evidence; live remains off.',
            },
            {
              label: 'Market adaptation',
              value: `${next500QuantEvidenceOsMarketAdaptation.implemented_count ?? 0}/${next500QuantEvidenceOsMarketAdaptation.item_count ?? 500}`,
              tone: Number(next500QuantEvidenceOsMarketAdaptation.degraded_count || 0) > 0 ? 'warning' : 'positive',
              helper: 'Adds market regime adapters, sector learning, liquidity shock response, desk conflict resolution, and readiness forecasts.',
            },
            {
              label: 'Adaptation live mirrors',
              value: `${next500QuantEvidenceOsMarketAdaptation.live_enabled_count ?? 0}/${next500QuantEvidenceOsMarketAdaptation.live_item_count ?? 500} on`,
              tone: Number(next500QuantEvidenceOsMarketAdaptation.live_enabled_count || 0) > 0 ? 'warning' : 'neutral',
              helper: 'Market adaptation is active for paper evidence; live remains off.',
            },
            {
              label: 'Frontier edge',
              value: `${next1000QuantEvidenceOsFrontierEdge.implemented_count ?? 0}/${next1000QuantEvidenceOsFrontierEdge.item_count ?? 1000}`,
              tone: Number(next1000QuantEvidenceOsFrontierEdge.degraded_count || 0) > 0 ? 'warning' : 'positive',
              helper: 'Adds customer trust proof, adapter portability, release acceptance, security boundaries, and competitive moat scoring.',
            },
            {
              label: 'Frontier live mirrors',
              value: `${next1000QuantEvidenceOsFrontierEdge.live_enabled_count ?? 0}/${next1000QuantEvidenceOsFrontierEdge.live_item_count ?? 1000} on`,
              tone: Number(next1000QuantEvidenceOsFrontierEdge.live_enabled_count || 0) > 0 ? 'warning' : 'neutral',
              helper: 'Frontier edge is active for paper evidence; live remains off.',
            },
            {
              label: 'Trade Selection Edge',
              value: `${next500QuantEvidenceOsTradeSelectionEdge.implemented_count ?? 0}/${next500QuantEvidenceOsTradeSelectionEdge.item_count ?? 500}`,
              tone: Number(next500QuantEvidenceOsTradeSelectionEdge.degraded_count || 0) > 0 ? 'warning' : 'positive',
              helper: `${humanizeValue(tradeSelectionEdgeContext.usage_mode, 'Influence ranking')} | paper evidence state | no autonomous live orders.`,
            },
            {
              label: 'Selection influence',
              value: `${tradeSelectionEdgeContext.score_influence?.max_uprank ?? 5}/-${Math.abs(tradeSelectionEdgeContext.score_influence?.max_downrank ?? -10)}`,
              tone: next500QuantEvidenceOsTradeSelectionEdge.can_submit_orders ? 'negative' : 'neutral',
              helper: 'Bounded ranking influence only; risk gates, stale data, cooldown, and reconciliation stay authoritative.',
            },
            {
              label: 'Real-Time Alpha Ops',
              value: `${next500QuantEvidenceOsRealtimeAlphaOps.implemented_count ?? 0}/${next500QuantEvidenceOsRealtimeAlphaOps.item_count ?? 500}`,
              tone: Number(next500QuantEvidenceOsRealtimeAlphaOps.degraded_count || 0) > 0 ? 'warning' : 'positive',
              helper: `${humanizeValue(realtimeAlphaOpsContext.usage_mode, 'Influence ranking')} | paper evidence state | no autonomous live orders.`,
            },
            {
              label: 'Alpha ops influence',
              value: `+${realtimeAlphaOpsContext.score_influence?.max_uprank ?? 3}/-${Math.abs(realtimeAlphaOpsContext.score_influence?.max_downrank ?? -6)}`,
              tone: next500QuantEvidenceOsRealtimeAlphaOps.can_submit_orders ? 'negative' : 'neutral',
              helper: 'Setup state, adaptive thresholds, regime fit, memory, and allocator fit can move ranking; gates stay final.',
            },
            {
              label: 'Adaptive Execution Intelligence',
              value: `${next500QuantEvidenceOsAdaptiveExecutionIntelligence.implemented_count ?? 0}/${next500QuantEvidenceOsAdaptiveExecutionIntelligence.item_count ?? 500}`,
              tone: Number(next500QuantEvidenceOsAdaptiveExecutionIntelligence.degraded_count || 0) > 0 ? 'warning' : 'positive',
              helper: `${humanizeValue(adaptiveExecutionIntelligenceContext.usage_mode, 'Influence ranking and allocator')} | paper evidence state | no autonomous live orders.`,
            },
            {
              label: 'Execution influence',
              value: `+${adaptiveExecutionIntelligenceContext.score_influence?.max_uprank ?? 2.5}/-${Math.abs(adaptiveExecutionIntelligenceContext.score_influence?.max_downrank ?? -7)}`,
              tone: next500QuantEvidenceOsAdaptiveExecutionIntelligence.can_submit_orders ? 'negative' : 'neutral',
              helper: 'Execution quality, entry timing, exit context, sizing confidence, and slippage can move ranking; risk gates stay final.',
            },
            {
              label: 'Portfolio Outcome Intelligence',
              value: `${next500QuantEvidenceOsPortfolioOutcomeIntelligence.implemented_count ?? 0}/${next500QuantEvidenceOsPortfolioOutcomeIntelligence.item_count ?? 500}`,
              tone: Number(next500QuantEvidenceOsPortfolioOutcomeIntelligence.degraded_count || 0) > 0 ? 'warning' : 'positive',
              helper: `${humanizeValue(portfolioOutcomeIntelligenceContext.usage_mode, 'Influence portfolio ranking and allocator')} | paper evidence state | no autonomous live orders.`,
            },
            {
              label: 'Portfolio influence',
              value: `+${portfolioOutcomeIntelligenceContext.score_influence?.max_uprank ?? 2}/-${Math.abs(portfolioOutcomeIntelligenceContext.score_influence?.max_downrank ?? -8)}`,
              tone: next500QuantEvidenceOsPortfolioOutcomeIntelligence.can_submit_orders ? 'negative' : 'neutral',
              helper: 'Portfolio heat, drawdown resilience, capital efficiency, and outcome memory can move ranking; risk gates stay final.',
            },
            {
              label: 'Institutional Operating Edge',
              value: `${next5000QuantEvidenceOsInstitutionalOperatingEdge.implemented_count ?? 0}/${next5000QuantEvidenceOsInstitutionalOperatingEdge.item_count ?? 5000}`,
              tone: Number(next5000QuantEvidenceOsInstitutionalOperatingEdge.degraded_count || 0) > 0 ? 'warning' : 'positive',
              helper: `${humanizeValue(institutionalOperatingEdgeContext.usage_mode, 'Influence operating ranking and allocator')} | paper evidence state | no autonomous live orders.`,
            },
            {
              label: 'Operating influence',
              value: `+${institutionalOperatingEdgeContext.score_influence?.max_uprank ?? 1.25}/-${Math.abs(institutionalOperatingEdgeContext.score_influence?.max_downrank ?? -9)}`,
              tone: next5000QuantEvidenceOsInstitutionalOperatingEdge.can_submit_orders ? 'negative' : 'neutral',
              helper: 'Operating resilience, governance confidence, data integrity, and market-session proof can move ranking; risk gates stay final.',
            },
            {
              label: 'Evidence 100M',
              value: `${evidenceMillionObserved.toLocaleString()}/${evidenceMillionGoal.toLocaleString()}`,
              tone: evidenceMillionTarget.status === 'degraded' ? 'warning' : 'positive',
              helper: `${evidenceMillionProgressPct.toFixed(2)}% toward real evidence observations; this is evidence memory, not order authority.`,
            },
            {
              label: '100M live mirror',
              value: evidenceMillionTarget.live_mirror?.enabled ? 'On' : 'Off',
              tone: evidenceMillionTarget.live_mirror?.enabled ? 'warning' : 'neutral',
              helper: 'Live-compatible progress metadata is visible for diligence, but autonomous live orders stay disabled.',
            },
          ].map((item, index) => renderMetricCard(item, `market-ops-v2-${index}`))}
        </section>
        {Array.isArray(productionWeaknessClosure.items) && productionWeaknessClosure.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Production weakness closure</caption>
              <thead>
                <tr>
                  <th scope="col">Group</th>
                  <th scope="col">Update</th>
                  <th scope="col">State</th>
                  <th scope="col">Evidence</th>
                </tr>
              </thead>
              <tbody>
                {productionWeaknessClosure.items.slice(0, 10).map((item) => (
                  <tr key={`production-weakness:${item.key}`}>
                    <td>{item.group || 'Production'}</td>
                    <td><strong>{item.label || humanizeValue(item.key, 'Update')}</strong><br />{item.endpoint ? <a className="chip chip--inline" href={item.endpoint}>Open proof</a> : 'Customer-visible proof'}</td>
                    <td>{item.strong_failure ? 'Strong failure' : humanizeValue(item.status, 'Tracked')}</td>
                    <td>{typeof item.evidence === 'string' ? item.evidence : item.evidence == null ? '--' : JSON.stringify(item.evidence).slice(0, 120)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(roadmapEvidenceActivation.items) && roadmapEvidenceActivation.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Roadmap evidence activation</caption>
              <thead>
                <tr>
                  <th scope="col">Layer</th>
                  <th scope="col">Active items</th>
                  <th scope="col">Mutation</th>
                  <th scope="col">Used by</th>
                </tr>
              </thead>
              <tbody>
                {roadmapEvidenceActivation.items.slice(0, 12).map((item) => (
                  <tr key={`roadmap-evidence:${item.key}`}>
                    <td><strong>{humanizeValue(item.key, 'Evidence layer')}</strong><br />{humanizeValue(item.operational_mode, 'Paper evidence active')}</td>
                    <td>{item.paper_operational_item_count ?? 0}/{item.item_count ?? 0}</td>
                    <td>{humanizeValue(item.mutation, 'Paper evidence state')}<br />{item.can_submit_orders ? 'Order mutation needs review' : 'No direct order mutation'}</td>
                    <td>{Array.isArray(item.used_by) ? item.used_by.slice(0, 4).map((value) => humanizeValue(value)).join(', ') : 'Market Ops'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(readOnlyActivationAudit.items) && readOnlyActivationAudit.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Evidence activation audit</caption>
              <thead>
                <tr>
                  <th scope="col">Layer</th>
                  <th scope="col">Activation</th>
                  <th scope="col">Inactive flags</th>
                  <th scope="col">Used by</th>
                </tr>
              </thead>
              <tbody>
                {readOnlyActivationAudit.items.slice(0, 12).map((item) => (
                  <tr key={`read-only-audit:${item.key}`}>
                    <td><strong>{humanizeValue(item.key, 'Evidence layer')}</strong><br />{humanizeValue(item.operational_mode, 'Paper evidence active')}</td>
                    <td>{item.active ? 'Active paper evidence' : 'Needs activation'}<br />{humanizeValue(item.mutation, 'Paper evidence state')}</td>
                    <td>{item.read_only ? 'Inactive' : 'None'}<br />Items {item.read_only_item_count ?? 0}/{item.checked_item_count ?? item.item_count ?? 0}</td>
                    <td>{Array.isArray(item.used_by) ? item.used_by.slice(0, 4).map((value) => humanizeValue(value)).join(', ') : 'Market Ops'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(next50TradingIntelligence.items) && next50TradingIntelligence.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Next 50 trading intelligence</caption>
              <thead>
                <tr>
                  <th scope="col">Group</th>
                  <th scope="col">Update</th>
                  <th scope="col">State</th>
                  <th scope="col">Evidence</th>
                </tr>
              </thead>
              <tbody>
                {next50TradingIntelligence.items.slice(0, 10).map((item) => (
                  <tr key={`next-50-intelligence:${item.key}`}>
                    <td>{item.group || 'Trading intelligence'}</td>
                    <td><strong>{item.label || humanizeValue(item.key, 'Update')}</strong><br />{item.endpoint ? <a className="chip chip--inline" href={item.endpoint}>Open proof</a> : 'Active paper evidence'}</td>
                    <td>{item.data_pending ? 'Waiting for market data' : humanizeValue(item.status, 'Tracked')}</td>
                    <td>{typeof item.evidence === 'string' ? item.evidence : item.evidence == null ? '--' : JSON.stringify(item.evidence).slice(0, 120)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(next50InstitutionalEdge.items) && next50InstitutionalEdge.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Next 50 institutional edge</caption>
              <thead>
                <tr>
                  <th scope="col">Group</th>
                  <th scope="col">Update</th>
                  <th scope="col">State</th>
                  <th scope="col">Evidence</th>
                </tr>
              </thead>
              <tbody>
                {next50InstitutionalEdge.items.slice(0, 10).map((item) => (
                  <tr key={`next-50-institutional:${item.key}`}>
                    <td>{item.group || 'Institutional edge'}</td>
                    <td><strong>{item.label || humanizeValue(item.key, 'Update')}</strong><br />{item.endpoint ? <a className="chip chip--inline" href={item.endpoint}>Open proof</a> : 'Institutional proof'}</td>
                    <td>{humanizeValue(item.status, 'Tracked')}</td>
                    <td>{typeof item.evidence === 'string' ? item.evidence : item.evidence == null ? '--' : JSON.stringify(item.evidence).slice(0, 120)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(next50EnterpriseDiligence.items) && next50EnterpriseDiligence.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Next 50 enterprise diligence</caption>
              <thead>
                <tr>
                  <th scope="col">Group</th>
                  <th scope="col">Update</th>
                  <th scope="col">State</th>
                  <th scope="col">Evidence</th>
                </tr>
              </thead>
              <tbody>
                {next50EnterpriseDiligence.items.slice(0, 10).map((item) => (
                  <tr key={`next-50-enterprise:${item.key}`}>
                    <td>{item.group || 'Enterprise diligence'}</td>
                    <td><strong>{item.label || humanizeValue(item.key, 'Update')}</strong><br />{item.endpoint ? <a className="chip chip--inline" href={item.endpoint}>Open proof</a> : 'Customer diligence proof'}</td>
                    <td>{humanizeValue(item.status, 'Tracked')}</td>
                    <td>{typeof item.evidence === 'string' ? item.evidence : item.evidence == null ? '--' : JSON.stringify(item.evidence).slice(0, 120)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(next50MarketEdgeTradeCapture.items) && next50MarketEdgeTradeCapture.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Next 50 market edge and trade capture</caption>
              <thead>
                <tr>
                  <th scope="col">Group</th>
                  <th scope="col">Update</th>
                  <th scope="col">State</th>
                  <th scope="col">Evidence</th>
                </tr>
              </thead>
              <tbody>
                {next50MarketEdgeTradeCapture.items.slice(0, 10).map((item) => (
                  <tr key={`next-50-market-edge:${item.key}`}>
                    <td>{item.group || 'Market edge'}</td>
                    <td><strong>{item.label || humanizeValue(item.key, 'Update')}</strong><br />{item.endpoint ? <a className="chip chip--inline" href={item.endpoint}>Open proof</a> : 'Trade-capture proof'}</td>
                    <td>{humanizeValue(item.status, 'Tracked')}</td>
                    <td>{typeof item.evidence === 'string' ? item.evidence : item.evidence == null ? '--' : JSON.stringify(item.evidence).slice(0, 120)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(next50ResearchMemoryStrategyPromotion.items) && next50ResearchMemoryStrategyPromotion.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Next 50 research memory and strategy promotion</caption>
              <thead>
                <tr>
                  <th scope="col">Group</th>
                  <th scope="col">Update</th>
                  <th scope="col">State</th>
                  <th scope="col">Evidence</th>
                </tr>
              </thead>
              <tbody>
                {next50ResearchMemoryStrategyPromotion.items.slice(0, 10).map((item) => (
                  <tr key={`next-50-research-memory:${item.key}`}>
                    <td>{item.group || 'Research memory'}</td>
                    <td><strong>{item.label || humanizeValue(item.key, 'Update')}</strong><br />{item.endpoint ? <a className="chip chip--inline" href={item.endpoint}>Open proof</a> : 'Promotion evidence'}</td>
                    <td>{humanizeValue(item.status, 'Tracked')}</td>
                    <td>{typeof item.evidence === 'string' ? item.evidence : item.evidence == null ? '--' : JSON.stringify(item.evidence).slice(0, 120)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(next100EdgeFactoryProductionScale.items) && next100EdgeFactoryProductionScale.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Next 100 edge factory and production scale</caption>
              <thead>
                <tr>
                  <th scope="col">Group</th>
                  <th scope="col">Update</th>
                  <th scope="col">Paper</th>
                  <th scope="col">Live mirror</th>
                </tr>
              </thead>
              <tbody>
                {next100EdgeFactoryProductionScale.items.slice(0, 12).map((item) => (
                  <tr key={`next-100-edge-factory:${item.key}`}>
                    <td>{item.group || 'Edge factory'}</td>
                    <td><strong>{item.label || humanizeValue(item.key, 'Update')}</strong><br />{item.endpoint ? <a className="chip chip--inline" href={item.endpoint}>Open proof</a> : 'Paper proof'}</td>
                    <td>{humanizeValue(item.paper_status || item.status, 'Tracked')}</td>
                    <td>{item.live_enabled ? 'On' : humanizeValue(item.live_status, 'Available off')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(next500QuantEvidenceOsEdge.items) && next500QuantEvidenceOsEdge.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Next 500 Quant Evidence OS edge</caption>
              <thead>
                <tr>
                  <th scope="col">Workstream</th>
                  <th scope="col">Update</th>
                  <th scope="col">Paper</th>
                  <th scope="col">Live mirror</th>
                </tr>
              </thead>
              <tbody>
                {next500QuantEvidenceOsEdge.items.slice(0, 15).map((item) => (
                  <tr key={`next-500-quant-evidence:${item.key}`}>
                    <td>{item.group || 'Quant Evidence OS'}</td>
                    <td><strong>{item.label || humanizeValue(item.key, 'Update')}</strong><br />{item.edge_thesis || item.competitive_edge || 'Evidence, risk, and missed-opportunity proof'}</td>
                    <td>{humanizeValue(item.paper_status || item.status, 'Tracked')}</td>
                    <td>{item.live_enabled ? 'On' : humanizeValue(item.live_status, 'Available off')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(next1000QuantEvidenceOsScale.items) && next1000QuantEvidenceOsScale.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Next 1000 Quant Evidence OS scale layer</caption>
              <thead>
                <tr>
                  <th scope="col">Workstream</th>
                  <th scope="col">Update</th>
                  <th scope="col">Paper</th>
                  <th scope="col">Live mirror</th>
                </tr>
              </thead>
              <tbody>
                {next1000QuantEvidenceOsScale.items.slice(0, 20).map((item) => (
                  <tr key={`next-1000-quant-scale:${item.key}`}>
                    <td>{item.group || 'Quant Evidence OS scale'}</td>
                    <td><strong>{item.label || humanizeValue(item.key, 'Update')}</strong><br />{item.edge_thesis || item.competitive_edge || 'Evidence scale, risk proof, and portability'}</td>
                    <td>{humanizeValue(item.paper_status || item.status, 'Tracked')}</td>
                    <td>{item.live_enabled ? 'On' : humanizeValue(item.live_status, 'Available off')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(next500QuantEvidenceOsCompounding.items) && next500QuantEvidenceOsCompounding.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Next 500 Quant Evidence OS compounding layer</caption>
              <thead>
                <tr>
                  <th scope="col">Workstream</th>
                  <th scope="col">Update</th>
                  <th scope="col">Paper</th>
                  <th scope="col">Live mirror</th>
                </tr>
              </thead>
              <tbody>
                {next500QuantEvidenceOsCompounding.items.slice(0, 20).map((item) => (
                  <tr key={`next-500-compounding:${item.key}`}>
                    <td>{item.group || 'Evidence compounding'}</td>
                    <td><strong>{item.label || humanizeValue(item.key, 'Update')}</strong><br />{item.edge_thesis || item.competitive_edge || 'Compounds daily proof into reusable product edge'}</td>
                    <td>{humanizeValue(item.paper_status || item.status, 'Tracked')}</td>
                    <td>{item.live_enabled ? 'On' : humanizeValue(item.live_status, 'Available off')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(next500QuantEvidenceOsInstitutionalMoat.items) && next500QuantEvidenceOsInstitutionalMoat.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Next 500 Quant Evidence OS institutional moat</caption>
              <thead>
                <tr>
                  <th scope="col">Workstream</th>
                  <th scope="col">Update</th>
                  <th scope="col">Paper</th>
                  <th scope="col">Live mirror</th>
                </tr>
              </thead>
              <tbody>
                {next500QuantEvidenceOsInstitutionalMoat.items.slice(0, 20).map((item) => (
                  <tr key={`next-500-moat:${item.key}`}>
                    <td>{item.group || 'Institutional moat'}</td>
                    <td><strong>{item.label || humanizeValue(item.key, 'Update')}</strong><br />{item.edge_thesis || item.competitive_edge || 'Turns proof, governance, and trust into product edge'}</td>
                    <td>{humanizeValue(item.paper_status || item.status, 'Tracked')}</td>
                    <td>{item.live_enabled ? 'On' : humanizeValue(item.live_status, 'Available off')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(expectedSettingsProof.checks) && expectedSettingsProof.checks.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Expected settings proof</caption>
              <thead>
                <tr>
                  <th scope="col">Check</th>
                  <th scope="col">Expected</th>
                  <th scope="col">Actual</th>
                  <th scope="col">State</th>
                </tr>
              </thead>
              <tbody>
                {expectedSettingsProof.checks.map((check) => (
                  <tr key={`expected-setting:${check.key || check.label}`}>
                    <td><strong>{check.label || humanizeValue(check.key, 'Check')}</strong></td>
                    <td>{Array.isArray(check.expected) ? check.expected.join(', ') : String(check.expected ?? '--')}</td>
                    <td>{Array.isArray(check.actual) ? check.actual.join(', ') : String(check.actual ?? '--')}</td>
                    <td>{check.passed ? 'Pass' : 'Needs review'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(incidentTimeline.items) && incidentTimeline.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Market Session incident timeline</caption>
              <thead>
                <tr>
                  <th scope="col">Event</th>
                  <th scope="col">Status</th>
                  <th scope="col">Detail</th>
                  <th scope="col">Next action</th>
                </tr>
              </thead>
              <tbody>
                {incidentTimeline.items.slice(0, 8).map((event, index) => (
                  <tr key={`incident:${event.type || index}:${index}`}>
                    <td><strong>{humanizeValue(event.type, 'Event')}</strong><br />{event.at ? new Date(event.at).toLocaleTimeString() : 'Now'}</td>
                    <td>{humanizeValue(event.status, 'Observed')}</td>
                    <td>{event.detail || '--'}</td>
                    <td>{event.next_action || 'Keep monitoring.'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(missedMoveLeaderboard.items) && missedMoveLeaderboard.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Missed-move leaderboard</caption>
              <thead>
                <tr>
                  <th scope="col">Ticker</th>
                  <th scope="col">Setup</th>
                  <th scope="col">Blocker</th>
                  <th scope="col">Severity</th>
                  <th scope="col">Would catch now</th>
                </tr>
              </thead>
              <tbody>
                {missedMoveLeaderboard.items.slice(0, 8).map((row, index) => (
                  <tr key={`missed-move:${row.ticker || index}:${index}`}>
                    <td><strong>{row.ticker || '--'}</strong><br />{humanizeValue(row.desk, 'Desk')}</td>
                    <td>{humanizeValue(row.setup_type, 'Opportunity')}</td>
                    <td>{humanizeValue(row.blocker, 'Unknown')}</td>
                    <td>{humanizeValue(row.severity, 'Meaningful')} / {row.count ?? 1}</td>
                    <td>{row.would_catch_now ? 'Yes' : 'Needs review'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        <div className="metric-strip">
          <span>Empty state: {humanizeValue(customerSafeEmptyStates.current, 'Waiting for candidate')}</span>
          <span>{customerSafeEmptyStates.market_closed?.title || 'Market closed'} is a session state</span>
          <span>{customerSafeEmptyStates.waiting_for_deep_analysis?.title || 'Waiting for deep analysis'}</span>
          <span>{customerSafeEmptyStates.risk_blocked?.title || 'Risk gate blocked'}</span>
        </div>
        {marketOpsComponentCards.length ? (
          <section className="metrics-grid metrics-grid--compact">
            {marketOpsComponentCards.map((item, index) => renderMetricCard(item, `market-ops-${index}`))}
          </section>
        ) : (
          <FeedbackState
            tone="warning"
            title="Market Watchdog data loading"
            description="The operator panel will show backend/API, frontend, Alpaca paper, worker heartbeat, desk scans, deep analysis, candidate diagnostics, no-trade checkpoint, HFT watchdog, reconciliation, and kill switch cards when the endpoint responds."
          />
        )}
        <div className="stack-row stack-row--wrap">
          <a className="chip chip--inline" href={watchdogLinks.candidate_diagnostics || '/api/orgs/trade-automation/candidate-diagnostics'}>Candidate diagnostics</a>
          <a className="chip chip--inline" href={watchdogLinks.daily_ledger || watchdogLinks.safety_ledger || '/api/orgs/trade-automation/daily-ledger'}>Daily ledger</a>
          <a className="chip chip--inline" href={watchdogLinks.no_trade_report || '/api/orgs/trade-automation/no-trade-report'}>No-trade report</a>
          <a className="chip chip--inline" href={watchdogLinks.market_day_report || '/api/orgs/trade-automation/market-day-report'}>Market-day report</a>
          <a className="chip chip--inline" href={watchdogLinks.alpaca_paper_readiness || '/api/orgs/trade-automation/alpaca-paper-readiness'}>Alpaca paper readiness</a>
          <a className="chip chip--inline" href={watchdogLinks.hft_watchdog_latest || '/api/orgs/trade-automation/hft-watchdog/latest'}>HFT watchdog latest</a>
          {Object.entries(diagnosticsExports).slice(0, 5).map(([key, exportItem]) => (
            <a
              className="chip chip--inline"
              href={exportItem?.endpoint || '#'}
              key={`diagnostics-export:${key}`}
            >
              Export {exportItem?.label || humanizeValue(key)}
            </a>
          ))}
          {Array.isArray(closeArtifactIndex.items) ? closeArtifactIndex.items.slice(0, 4).map((artifact) => (
            <a
              className="chip chip--inline"
              href={artifact.endpoint || '#'}
              key={`close-artifact:${artifact.key || artifact.label}`}
            >
              Proof {artifact.label || humanizeValue(artifact.key)}
            </a>
          )) : null}
        </div>
        {marketOpsDeskRows.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Market session desk SLA</caption>
              <thead>
                <tr>
                  <th scope="col">Desk</th>
                  <th scope="col">Due state</th>
                  <th scope="col">Deep analysis</th>
                  <th scope="col">Evidence</th>
                  <th scope="col">Next action</th>
                </tr>
              </thead>
              <tbody>
                {marketOpsDeskRows.map((desk) => (
                  <tr key={`market-ops-desk:${desk.desk_key}`}>
                    <td><strong>{desk.label || humanizeValue(desk.desk_key, 'Desk')}</strong><br />{desk.stale ? 'Stale' : 'Freshness OK'} / SLA {desk.freshness_sla_seconds ?? '--'}s</td>
                    <td>{humanizeValue(desk.due_state, 'Waiting')}<br />Last {desk.last_scan_at ? new Date(desk.last_scan_at).toLocaleTimeString() : 'Not scanned'}</td>
                    <td>{humanizeValue(desk.deep_analysis_status, 'Idle')}</td>
                    <td>{desk.opportunity_count ?? 0} opportunities / {desk.eligible_count ?? 0} eligible<br />Blocker {humanizeValue(desk.top_blocker, 'None')}</td>
                    <td>{desk.next_action || 'Keep scanning under current gates.'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(next500QuantEvidenceOsAdaptiveEdge.items) && next500QuantEvidenceOsAdaptiveEdge.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Next 500 Quant Evidence OS adaptive edge</caption>
              <thead>
                <tr>
                  <th scope="col">Workstream</th>
                  <th scope="col">Update</th>
                  <th scope="col">Live mirror</th>
                  <th scope="col">Edge</th>
                </tr>
              </thead>
              <tbody>
                {next500QuantEvidenceOsAdaptiveEdge.items.slice(0, 20).map((item) => (
                  <tr key={`next-500-adaptive-edge:${item.key}`}>
                    <td>{item.group || 'Adaptive edge'}</td>
                    <td><strong>{item.label || humanizeValue(item.key, 'Update')}</strong><br />{item.edge_thesis || item.customer_value || 'Adaptive paper evidence'}</td>
                    <td>{item.live_enabled ? 'On' : 'Off'}<br />{item.can_submit_live_orders ? 'Live needs review' : 'No live order authority'}</td>
                    <td>{item.competitive_edge || 'Evidence loops, missed-edge replay, risk proof, and execution simulation.'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(next500QuantEvidenceOsDecisionIntelligence.items) && next500QuantEvidenceOsDecisionIntelligence.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Next 500 Quant Evidence OS decision intelligence</caption>
              <thead>
                <tr>
                  <th scope="col">Workstream</th>
                  <th scope="col">Update</th>
                  <th scope="col">Live mirror</th>
                  <th scope="col">Edge</th>
                </tr>
              </thead>
              <tbody>
                {next500QuantEvidenceOsDecisionIntelligence.items.slice(0, 20).map((item) => (
                  <tr key={`next-500-decision-intelligence:${item.key}`}>
                    <td>{item.group || 'Decision intelligence'}</td>
                    <td><strong>{item.label || humanizeValue(item.key, 'Update')}</strong><br />{item.edge_thesis || item.customer_value || 'Paper decision intelligence'}</td>
                    <td>{item.live_enabled ? 'On' : 'Off'}<br />{item.can_submit_live_orders ? 'Live needs review' : 'No live order authority'}</td>
                    <td>{item.competitive_edge || 'Decision context, causal blockers, paper experiments, confidence scoring, and proof automation.'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(next500QuantEvidenceOsAutonomousImprovement.items) && next500QuantEvidenceOsAutonomousImprovement.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Next 500 Quant Evidence OS autonomous improvement</caption>
              <thead>
                <tr>
                  <th scope="col">Workstream</th>
                  <th scope="col">Update</th>
                  <th scope="col">Live mirror</th>
                  <th scope="col">Edge</th>
                </tr>
              </thead>
              <tbody>
                {next500QuantEvidenceOsAutonomousImprovement.items.slice(0, 20).map((item) => (
                  <tr key={`next-500-autonomous-improvement:${item.key}`}>
                    <td>{item.group || 'Autonomous improvement'}</td>
                    <td><strong>{item.label || humanizeValue(item.key, 'Update')}</strong><br />{item.edge_thesis || item.customer_value || 'Paper autonomous improvement governance'}</td>
                    <td>{item.live_enabled ? 'On' : 'Off'}<br />{item.can_submit_live_orders ? 'Live needs review' : 'No live order authority'}</td>
                    <td>{item.competitive_edge || 'Safe learning loops, paper experiments, blocker audits, release gates, and enterprise proof.'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(next500QuantEvidenceOsMarketAdaptation.items) && next500QuantEvidenceOsMarketAdaptation.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Next 500 Quant Evidence OS market adaptation</caption>
              <thead>
                <tr>
                  <th scope="col">Workstream</th>
                  <th scope="col">Update</th>
                  <th scope="col">Live mirror</th>
                  <th scope="col">Edge</th>
                </tr>
              </thead>
              <tbody>
                {next500QuantEvidenceOsMarketAdaptation.items.slice(0, 20).map((item) => (
                  <tr key={`next-500-market-adaptation:${item.key}`}>
                    <td>{item.group || 'Market adaptation'}</td>
                    <td><strong>{item.label || humanizeValue(item.key, 'Update')}</strong><br />{item.edge_thesis || item.customer_value || 'Paper market adaptation network'}</td>
                    <td>{item.live_enabled ? 'On' : 'Off'}<br />{item.can_submit_live_orders ? 'Live needs review' : 'No live order authority'}</td>
                    <td>{item.competitive_edge || 'Market-aware evidence adaptation, cross-desk conflict handling, data-quality SLAs, and buyer proof.'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(next1000QuantEvidenceOsFrontierEdge.items) && next1000QuantEvidenceOsFrontierEdge.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Next 1000 Quant Evidence OS frontier edge</caption>
              <thead>
                <tr>
                  <th scope="col">Workstream</th>
                  <th scope="col">Update</th>
                  <th scope="col">Live mirror</th>
                  <th scope="col">Edge</th>
                </tr>
              </thead>
              <tbody>
                {next1000QuantEvidenceOsFrontierEdge.items.slice(0, 20).map((item) => (
                  <tr key={`next-1000-frontier-edge:${item.key}`}>
                    <td>{item.group || 'Frontier edge'}</td>
                    <td><strong>{item.label || humanizeValue(item.key, 'Update')}</strong><br />{item.edge_thesis || item.customer_value || 'Paper frontier-edge evidence layer'}</td>
                    <td>{item.live_enabled ? 'On' : 'Off'}<br />{item.can_submit_live_orders ? 'Live needs review' : 'No live order authority'}</td>
                    <td>{item.competitive_edge || 'Customer trust proof, adaptive evidence, adapter portability, and institutional diligence.'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(next500QuantEvidenceOsTradeSelectionEdge.items) && next500QuantEvidenceOsTradeSelectionEdge.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Trade Selection Edge</caption>
              <thead>
                <tr>
                  <th scope="col">Workstream</th>
                  <th scope="col">Active use</th>
                  <th scope="col">Score influence</th>
                  <th scope="col">Safety</th>
                </tr>
              </thead>
              <tbody>
                {next500QuantEvidenceOsTradeSelectionEdge.items.slice(0, 20).map((item) => (
                  <tr key={`next-500-trade-selection-edge:${item.key}`}>
                    <td>{item.group || 'Trade selection edge'}</td>
                    <td><strong>{item.label || humanizeValue(item.key, 'Update')}</strong><br />{item.edge_thesis || item.customer_value || 'Candidate evidence feeds paper ranking and proof.'}</td>
                    <td>{humanizeValue(tradeSelectionEdgeContext.usage_mode, 'Influence ranking')}<br />+{tradeSelectionEdgeContext.score_influence?.max_uprank ?? 5} / {tradeSelectionEdgeContext.score_influence?.max_downrank ?? -10}</td>
                    <td>{item.can_submit_live_orders ? 'Live needs review' : 'No autonomous live orders'}<br />{tradeSelectionEdgeContext.score_influence?.hard_gates_remain_authoritative === false ? 'Gate policy needs review' : 'Risk gates stay final'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(next500QuantEvidenceOsRealtimeAlphaOps.items) && next500QuantEvidenceOsRealtimeAlphaOps.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Real-Time Alpha Ops</caption>
              <thead>
                <tr>
                  <th scope="col">Workstream</th>
                  <th scope="col">Active evidence</th>
                  <th scope="col">Influence</th>
                  <th scope="col">Safety</th>
                </tr>
              </thead>
              <tbody>
                {next500QuantEvidenceOsRealtimeAlphaOps.items.slice(0, 20).map((item) => (
                  <tr key={`next-500-realtime-alpha-ops:${item.key}`}>
                    <td>{item.group || 'Real-time alpha ops'}</td>
                    <td><strong>{item.label || humanizeValue(item.key, 'Update')}</strong><br />{item.edge_thesis || item.customer_value || 'Real-time setup evidence feeds paper ranking and proof.'}</td>
                    <td>{humanizeValue(realtimeAlphaOpsContext.usage_mode, 'Influence ranking')}<br />+{realtimeAlphaOpsContext.score_influence?.max_uprank ?? 3} / {realtimeAlphaOpsContext.score_influence?.max_downrank ?? -6}</td>
                    <td>{item.can_submit_live_orders ? 'Live needs review' : 'No autonomous live orders'}<br />{realtimeAlphaOpsContext.score_influence?.hard_gates_remain_authoritative === false ? 'Gate policy needs review' : 'Risk gates stay final'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(next500QuantEvidenceOsAdaptiveExecutionIntelligence.items) && next500QuantEvidenceOsAdaptiveExecutionIntelligence.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Adaptive Execution Intelligence</caption>
              <thead>
                <tr>
                  <th scope="col">Workstream</th>
                  <th scope="col">Active evidence</th>
                  <th scope="col">Influence</th>
                  <th scope="col">Safety</th>
                </tr>
              </thead>
              <tbody>
                {next500QuantEvidenceOsAdaptiveExecutionIntelligence.items.slice(0, 20).map((item) => (
                  <tr key={`next-500-adaptive-execution-intelligence:${item.key}`}>
                    <td>{item.group || 'Adaptive execution intelligence'}</td>
                    <td><strong>{item.label || humanizeValue(item.key, 'Update')}</strong><br />{item.edge_thesis || item.customer_value || 'Execution-quality evidence feeds paper ranking, allocator context, and proof.'}</td>
                    <td>{humanizeValue(adaptiveExecutionIntelligenceContext.usage_mode, 'Influence ranking and allocator')}<br />+{adaptiveExecutionIntelligenceContext.score_influence?.max_uprank ?? 2.5} / {adaptiveExecutionIntelligenceContext.score_influence?.max_downrank ?? -7}</td>
                    <td>{item.can_submit_live_orders ? 'Live needs review' : 'No autonomous live orders'}<br />{adaptiveExecutionIntelligenceContext.score_influence?.hard_gates_remain_authoritative === false ? 'Gate policy needs review' : 'Risk gates stay final'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(next500QuantEvidenceOsPortfolioOutcomeIntelligence.items) && next500QuantEvidenceOsPortfolioOutcomeIntelligence.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Portfolio Outcome Intelligence</caption>
              <thead>
                <tr>
                  <th scope="col">Workstream</th>
                  <th scope="col">Active evidence</th>
                  <th scope="col">Influence</th>
                  <th scope="col">Safety</th>
                </tr>
              </thead>
              <tbody>
                {next500QuantEvidenceOsPortfolioOutcomeIntelligence.items.slice(0, 20).map((item) => (
                  <tr key={`next-500-portfolio-outcome-intelligence:${item.key}`}>
                    <td>{item.group || 'Portfolio outcome intelligence'}</td>
                    <td><strong>{item.label || humanizeValue(item.key, 'Update')}</strong><br />{item.edge_thesis || item.customer_value || 'Portfolio outcome evidence feeds paper ranking, allocator context, heat governance, and proof.'}</td>
                    <td>{humanizeValue(portfolioOutcomeIntelligenceContext.usage_mode, 'Influence portfolio ranking and allocator')}<br />+{portfolioOutcomeIntelligenceContext.score_influence?.max_uprank ?? 2} / {portfolioOutcomeIntelligenceContext.score_influence?.max_downrank ?? -8}</td>
                    <td>{item.can_submit_live_orders ? 'Live needs review' : 'No autonomous live orders'}<br />{portfolioOutcomeIntelligenceContext.score_influence?.hard_gates_remain_authoritative === false ? 'Gate policy needs review' : 'Risk gates stay final'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {Array.isArray(next5000QuantEvidenceOsInstitutionalOperatingEdge.items) && next5000QuantEvidenceOsInstitutionalOperatingEdge.items.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Institutional Operating Edge</caption>
              <thead>
                <tr>
                  <th scope="col">Workstream</th>
                  <th scope="col">Active evidence</th>
                  <th scope="col">Influence</th>
                  <th scope="col">Safety</th>
                </tr>
              </thead>
              <tbody>
                {next5000QuantEvidenceOsInstitutionalOperatingEdge.items.slice(0, 20).map((item) => (
                  <tr key={`next-5000-institutional-operating-edge:${item.key}`}>
                    <td>{item.group || 'Institutional operating edge'}</td>
                    <td><strong>{item.label || humanizeValue(item.key, 'Update')}</strong><br />{item.edge_thesis || item.customer_value || 'Institutional operating evidence feeds paper ranking, allocator context, market ops, and proof.'}</td>
                    <td>{humanizeValue(institutionalOperatingEdgeContext.usage_mode, 'Influence operating ranking and allocator')}<br />+{institutionalOperatingEdgeContext.score_influence?.max_uprank ?? 1.25} / {institutionalOperatingEdgeContext.score_influence?.max_downrank ?? -9}</td>
                    <td>{item.can_submit_live_orders ? 'Live needs review' : 'No autonomous live orders'}<br />{institutionalOperatingEdgeContext.score_influence?.hard_gates_remain_authoritative === false ? 'Gate policy needs review' : 'Risk gates stay final'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
      <section className="section-stack">
        <FeedbackState
          tone={quantEvidenceControlPlane.status === 'ready' ? 'positive' : quantEvidenceControlPlane.status === 'blocked' || quantEvidenceControlPlane.status === 'killed' ? 'negative' : 'warning'}
          title={quantEvidenceControlPlane.product_name || 'Quant Evidence Operating System'}
          description={quantEvidenceControlPlane.adoption_wedge || 'Evidence, risk, missed-opportunity review, AI referee, and execution proof are unified above the active Alpaca paper desks.'}
        />
        <div className="metric-strip">
          <span>{quantEvidenceControlPlane.paper_route_only === false ? 'Route needs review' : 'Alpaca paper execution only'}</span>
          <span>{quantEvidenceControlPlane.can_submit_live_orders ? 'Live order path needs review' : 'No autonomous live orders'}</span>
          <span>Position policy {humanizeValue(institutionalPositionPolicy.mode, 'Risk allocated')}</span>
          <span>Evidence {formatCompactNumber(evidenceMillionObserved)} / {formatCompactNumber(evidenceMillionGoal)}</span>
          <span>{evidenceMillionProgressPct.toFixed(2)}% to 1M observations</span>
          <span>Position capacity stays risk-governed</span>
          <span>{institutionalPositionPolicy.buying_power_is_ceiling === false ? 'Buying power policy needs review' : 'Buying power is ceiling'}</span>
        </div>
        <section className="metrics-grid metrics-grid--compact">
          {quantEvidencePillars.length ? quantEvidencePillars.slice(0, 6).map((pillar) => renderMetricCard({
            key: `quant-evidence:${pillar.key}`,
            label: pillar.label || humanizeValue(pillar.key, 'Evidence pillar'),
            value: Array.isArray(pillar.metric) ? String(pillar.metric.length) : String(pillar.metric ?? humanizeValue(pillar.status, '--')),
            tone: ['active', 'paper_only', 'shadow_review', 'risk_allocated', 'collective_account_heat'].includes(String(pillar.status || '').toLowerCase()) ? 'positive' : String(pillar.status || '').toLowerCase().includes('disabled') ? 'warning' : 'neutral',
            detail: pillar.detail || humanizeValue(pillar.status, '--'),
          }, pillar.key)) : [
            { label: 'Opportunity proof', value: String(candidateLifecycle.tracked_count ?? 0), tone: Number(candidateLifecycle.tracked_count || 0) > 0 ? 'positive' : 'neutral', detail: 'Candidate lifecycle rows explain scans, rejects, and follow-up windows.' },
            { label: 'AI evidence review', value: String(candidateAiSummary.reviewed_count ?? 0), tone: Number(candidateAiSummary.reviewed_count || 0) > 0 ? 'positive' : 'neutral', detail: 'AI is evidence review only; final gates remain authoritative.' },
            { label: 'Missed opportunities', value: String((candidateDiagnosticSummary.missed_opportunities || []).length || 0), tone: 'warning', detail: 'Rejected moves stay reviewable without forcing trades.' },
          ].map((item, index) => renderMetricCard(item, `quant-evidence-fallback-${index}`))}
        </section>
        <div className="table-shell">
          <table className="list-table">
            <caption>Institutional proof questions</caption>
            <thead>
              <tr>
                <th scope="col">Question</th>
                <th scope="col">Answerable</th>
                <th scope="col">Evidence</th>
                <th scope="col">Endpoint</th>
              </tr>
            </thead>
            <tbody>
              {[
                ['Why no trade?', quantEvidenceQuestions.why_no_trade],
                ['Why this trade?', quantEvidenceQuestions.why_this_trade],
                ['Which desk deserved capital?', quantEvidenceQuestions.which_desk_deserved_capital],
                ['Prove no live bypass', quantEvidenceQuestions.prove_no_live_bypass],
              ].map(([label, item]) => {
                const evidence = item?.evidence && typeof item.evidence === 'object'
                  ? Object.entries(item.evidence).slice(0, 3).map(([key, value]) => `${humanizeValue(key)} ${Array.isArray(value) ? value.length : typeof value === 'object' ? JSON.stringify(value) : String(value)}`).join(' | ')
                  : item?.evidence
                return (
                  <tr key={`quant-proof:${label}`}>
                    <td><strong>{label}</strong></td>
                    <td>{item?.answerable ? 'Yes' : 'Needs more session data'}</td>
                    <td>{evidence || 'Evidence will populate after the next scan or market-day report.'}</td>
                    <td>{item?.endpoint ? <a className="chip chip--inline" href={item.endpoint}>Open proof</a> : '--'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <FeedbackState
          tone={institutionalPositionPolicy.needs_settings_update ? 'warning' : 'positive'}
          title="Institutional position policy"
          description={institutionalPositionPolicy.detail || 'Risk allocation is governed by open notional, loss budget, desk budgets, heat, and duplicate-order gates rather than a simple five-position cap.'}
        />
        <FeedbackState
          tone={String(evidenceMillionTarget.status || '').toLowerCase() === 'degraded' ? 'warning' : 'positive'}
          title="Evidence 100M counter"
          description={evidenceMillionTarget.next_action || 'The main progress counter now tracks real observed evidence events toward 100,000,000. Position capacity remains governed by risk policy, not by this counter.'}
        />
        <section className="metrics-grid metrics-grid--compact">
          {[
            {
              label: 'Observed evidence',
              value: formatCompactNumber(evidenceMillionObserved),
              tone: evidenceMillionObserved > 0 ? 'positive' : 'neutral',
              helper: `${evidenceMillionObserved.toLocaleString()} real observations collected from candidates, blockers, AI review, diagnostics, and session proof.`,
            },
            {
              label: 'Live observed',
              value: formatCompactNumber(evidenceMillionLiveObserved),
              tone: evidenceMillionLiveObserved > 0 ? 'positive' : 'neutral',
              helper: 'Only deduplicated live runtime observations count toward the 100M headline.',
            },
            {
              label: 'Simulation evidence',
              value: formatCompactNumber(evidenceMillionSimulation),
              tone: evidenceMillionSimulation > 0 ? 'positive' : 'neutral',
              helper: 'Scenario and replay evidence improves ranking and reports, but stays outside the live 100M counter.',
            },
            {
              label: 'Evidence goal',
              value: formatCompactNumber(evidenceMillionGoal),
              tone: 'neutral',
              helper: 'Target is one hundred million observed evidence events, not simulated roadmap items.',
            },
            {
              label: 'Progress',
              value: `${evidenceMillionProgressPct.toFixed(2)}%`,
              tone: evidenceMillionProgressPct >= 100 ? 'positive' : 'neutral',
              helper: `${Math.max(evidenceMillionGoal - evidenceMillionObserved, 0).toLocaleString()} observations remaining.`,
            },
            {
              label: 'ETA',
              value: evidenceMillionEtaLabel,
              tone: Number.isFinite(evidenceMillionRate) && evidenceMillionRate > 0 ? 'positive' : 'neutral',
              helper: Number.isFinite(evidenceMillionRate)
                ? `${formatCompactNumber(evidenceMillionRate)} observations/hour at the current observed rate.`
                : 'ETA appears after continuous evidence collection establishes a rate.',
            },
            {
              label: 'Useful quality',
              value: `${Number((evidenceMillionQuality.useful_event_ratio || 0) * 100).toFixed(0)}%`,
              tone: Number(evidenceMillionQuality.useful_event_ratio || 0) >= 0.7 ? 'positive' : 'warning',
              helper: `Duplicates ${Number((evidenceMillionQuality.duplicate_ratio || 0) * 100).toFixed(1)}% | stale ${Number((evidenceMillionQuality.stale_ratio || 0) * 100).toFixed(1)}%.`,
            },
            {
              label: 'Accelerator',
              value: `${formatCompactNumber(evidenceAcceleratorContext.current_useful_event_count || 0)}/beat`,
              tone: String(evidenceAcceleratorContext.status || '').toLowerCase() === 'degraded' ? 'warning' : 'positive',
              helper: `Cap ${formatCompactNumber(evidenceAcceleratorContext.configured_max_events_per_minute || 1500)}/minute; rate-limited ${formatCompactNumber(evidenceAcceleratorContext.rate_limited_count || 0)}.`,
            },
            {
              label: 'Possibility engine',
              value: `${Number((marketPossibilityEngineContext.average_scenario_probability || simulationEvidenceStore.average_scenario_probability || 0) * 100).toFixed(0)}%`,
              tone: 'neutral',
              helper: `Bounded influence +${marketPossibilityEngineContext.max_uprank ?? 4}/-${Math.abs(Number(marketPossibilityEngineContext.max_downrank ?? -8))}; no order authority.`,
            },
          ].map((item, index) => renderMetricCard(item, `evidence-million-progress-${index}`))}
        </section>
        <div className="metric-strip">
          <span>{evidenceMillionTarget.paper_route_only === false ? 'Route needs review' : 'Alpaca paper evidence'}</span>
          <span>{evidenceMillionTarget.can_submit_orders ? 'Order authority needs review' : 'No order authority'}</span>
          <span>{evidenceMillionTarget.live_mirror?.enabled ? 'Live mirror on' : 'Live mirror off'}</span>
          <span>{evidenceMillionTarget.simulation_counts_toward_live_million ? 'Simulation count needs review' : 'Simulation tracked separately'}</span>
          <span>Market Possibility Engine cannot override hard gates</span>
          <span>Position capacity remains risk-governed at {institutionalPositionPolicy.current_max_open_positions ?? positionPromotion.current_max_open_positions ?? '--'}</span>
        </div>
      </section>
      <section className="section-stack">
        <FeedbackState
          tone="positive"
          title="Desk Command Center"
          description={`Account objective: +1-2% weekly collective. Daily loss budget: -0.5% collective. Fast desks pursue qualified weekly progress; swing and macro contribute without chasing. Global route: ${humanizeValue(automationDeskGlobal.route, 'Alpaca paper')}; live-money submission remains disabled.`}
        />
        <section className="metrics-grid metrics-grid--compact">
          {[
            {
              label: 'Allocator mode',
              value: humanizeValue(allocatorDashboard.mode, 'Evidence quality'),
              tone: 'positive',
              helper: allocatorDashboard.next_action || 'Desks compete by evidence quality and heat, not slot count.',
            },
            {
              label: 'Capital conflicts',
              value: String((allocatorDashboard.capital_contention_reasons || []).length || allocatorDashboard.conflict_detector?.conflict_count || 0),
              tone: Number((allocatorDashboard.capital_contention_reasons || []).length || allocatorDashboard.conflict_detector?.conflict_count || 0) > 0 ? 'warning' : 'positive',
              helper: 'Same-symbol, same-sector, and capital contention are surfaced before entries.',
            },
            {
              label: 'Sector buckets',
              value: String(Object.keys(allocatorDashboard.sector_heat || sectorCorrelationHeat.candidate_pressure_by_bucket || {}).length),
              tone: 'neutral',
              helper: 'Open and candidate pressure are grouped by sector/correlation heat.',
            },
            {
              label: 'Position capacity',
              value: String(allocatorDashboard.position_slot_audit_trail?.current_allowed ?? institutionalPositionPolicy.current_max_open_positions ?? '--'),
              tone: 'neutral',
              helper: allocatorDashboard.position_slot_audit_trail?.detail || 'Capacity is allowed exposure, not a risk target.',
            },
          ].map((item, index) => renderMetricCard(item, `allocator-dashboard-${index}`))}
        </section>
        {Array.isArray(allocatorDashboard.desk_deserved_capital_today) && allocatorDashboard.desk_deserved_capital_today.length ? (
          <div className="table-shell">
            <table className="list-table">
              <caption>Desk deserved-capital report</caption>
              <thead>
                <tr>
                  <th scope="col">Desk</th>
                  <th scope="col">Opportunity</th>
                  <th scope="col">Open notional</th>
                  <th scope="col">Budget</th>
                </tr>
              </thead>
              <tbody>
                {allocatorDashboard.desk_deserved_capital_today.slice(0, 5).map((desk, index) => (
                  <tr key={`deserved-capital:${desk.desk_key || index}`}>
                    <td><strong>{desk.label || humanizeValue(desk.desk_key, 'Desk')}</strong></td>
                    <td>{desk.opportunity_count ?? 0}</td>
                    <td>{desk.open_notional == null ? '--' : formatMoney(desk.open_notional)}</td>
                    <td>{desk.risk_budget_pct == null ? '--' : `${Number(desk.risk_budget_pct).toFixed(0)}%`} / {desk.max_open_notional_pct == null ? '--' : `${Number(desk.max_open_notional_pct).toFixed(0)}%`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        <div className="metric-strip">
          <span>Objective {automationDeskGlobal?.daily_objective?.objective_range_label || humanizeValue(automationDeskGlobal?.daily_objective?.objective_mode, 'Weekly 1-2%')}</span>
          <span>Target band {automationDeskGlobal?.daily_objective?.target_min_dollars == null ? '--' : `${formatMoney(automationDeskGlobal.daily_objective.target_min_dollars)}-${formatMoney(automationDeskGlobal.daily_objective.target_dollars)}`}</span>
          <span>Loss budget {automationDeskGlobal?.daily_objective?.loss_budget_pct == null ? '--' : `${Number(automationDeskGlobal.daily_objective.loss_budget_pct).toFixed(2)}%`}</span>
          <span>{automationDeskGlobal?.daily_objective?.entries_blocked ? `Entries blocked: ${humanizeValue(automationDeskGlobal.daily_objective.entry_block_reason, 'objective lock')}` : 'Entries allowed by objective'}</span>
          <span>Unattended route Alpaca paper only</span>
          <span>Desk opportunities {automationDeskGlobal?.desk_intelligence?.opportunity_count ?? 0}</span>
          <span>Conflicts {automationDeskGlobal?.desk_intelligence?.conflict_count ?? 0}</span>
        </div>
        <div className="table-shell">
          <table className="list-table">
            <caption>Multi-horizon automation desks</caption>
            <thead>
              <tr>
                <th scope="col">Desk</th>
                <th scope="col">Objective role</th>
                <th scope="col">Cadence</th>
                <th scope="col">Risk budget</th>
                <th scope="col">Runtime</th>
                <th scope="col">Last scan</th>
                <th scope="col">Next action</th>
              </tr>
            </thead>
            <tbody>
              {automationDesks.length ? automationDesks.map((desk) => {
                const runtime = desk.runtime || {}
                const cadence = desk.cadence || {}
                const riskBudget = desk.risk_budget || {}
                const intelligence = desk.execution_intelligence || {}
                return (
                  <tr key={`automation-desk:${desk.desk_key}`}>
                    <td>
                      <strong>{desk.label || humanizeValue(desk.desk_key, 'Desk')}</strong>
                      <br />
                      <span>{humanizeValue(desk.strategy_family, '--')} | {desk.enabled && desk.armed ? 'Armed' : 'Paused'}</span>
                    </td>
                    <td>{humanizeValue(desk.objective_role, 'Primary intraday')}<br />Pressure {humanizeValue(desk.target_pressure, 'Normal')}<br />Contribution {desk.target_contribution_today == null ? '--' : formatMoney(desk.target_contribution_today)}</td>
                    <td>{cadence.interval || '--'} / {cadence.cycle_interval_seconds ? `${cadence.cycle_interval_seconds}s` : '--'}<br />Hold {cadence.max_hold_minutes ? `${cadence.max_hold_minutes}m` : '--'}</td>
                    <td>{riskBudget.risk_budget_pct == null ? '--' : `${Number(riskBudget.risk_budget_pct).toFixed(0)}%`} risk<br />{riskBudget.max_open_notional_pct == null ? '--' : `${Number(riskBudget.max_open_notional_pct).toFixed(0)}%`} open / {riskBudget.max_positions ?? '--'} positions</td>
                    <td>{runtime.scanned_count ?? 0} scanned<br />{runtime.deep_analyzed_count ?? 0} deep / {runtime.eligible_count ?? 0} eligible<br />Readiness {humanizeValue(intelligence.trade_readiness, 'Waiting')}</td>
                    <td>{runtime.last_scan_at ? new Date(runtime.last_scan_at).toLocaleString() : 'Not scanned yet'}<br />No-trade reason {humanizeValue(intelligence.no_trade_root_cause || desk.desk_reason_for_no_trade || runtime.top_blocker, 'None')}</td>
                    <td>
                      <div className="stack-row stack-row--wrap">
                        <span>{desk.next_action || 'Waiting for schedule.'}</span>
                        <Button type="button" variant="ghost" onClick={() => scanDesk(desk.desk_key)} disabled={Boolean(deskBusyKey)}>
                          {deskBusyKey === desk.desk_key ? 'Scanning...' : 'Scan desk'}
                        </Button>
                      </div>
                    </td>
                  </tr>
                )
              }) : (
                <tr>
                  <td colSpan={7}>Desk scheduler data is loading. The existing intraday automation profile remains active.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <FeedbackState
          tone="neutral"
          title="Institutional Coverage"
          description="The platform models every institutional lane, but unattended execution remains Alpaca paper-only. Proxy exposure and research-only lanes cannot submit orders."
        />
        <section className="metrics-grid metrics-grid--compact">
          {institutionalCoverageGroups.map((group) => (
            <MetricCard
              key={`institutional-coverage:${group.key}`}
              label={group.title}
              value={String(group.items.length)}
              tone={group.key === 'active' ? 'positive' : group.key === 'unsupported' ? 'neutral' : 'warning'}
              detail={group.description}
            />
          ))}
        </section>
        <div className="table-shell">
          <table className="list-table">
            <caption>Institutional desk coverage by support maturity</caption>
            <thead>
              <tr>
                <th scope="col">Desk</th>
                <th scope="col">Execution status</th>
                <th scope="col">Support maturity</th>
                <th scope="col">Coverage</th>
                <th scope="col">Provider / proxy</th>
                <th scope="col">Engine coverage</th>
                <th scope="col">Instruments</th>
                <th scope="col">Risk / hold</th>
                <th scope="col">Next action</th>
              </tr>
            </thead>
            <tbody>
              {institutionalDeskCatalog.length ? institutionalDeskCatalog.map((desk) => {
                const status = String(desk.execution_status || '').trim().toLowerCase()
                const statusLabel =
                  desk.execution_status_label ||
                  (status === 'active'
                    ? 'Active paper desk'
                    : status === 'proxy_only'
                      ? 'Proxy-only desk'
                      : status === 'unsupported'
                        ? 'Unsupported for current provider'
                        : 'Research-only desk')
                const tickers = Array.isArray(desk.allowed_tickers) ? desk.allowed_tickers : []
                const assetScope = Array.isArray(desk.asset_scope) ? desk.asset_scope : []
                const instruments = Array.isArray(desk.allowed_instruments) ? desk.allowed_instruments : []
                const supportMaturity = desk.support_maturity || {}
                const supportCompleted = Array.isArray(supportMaturity.completed) ? supportMaturity.completed : []
                const dataRequirements = Array.isArray(desk.data_requirements) ? desk.data_requirements : []
                const promotionRequirements = Array.isArray(desk.promotion_requirements) ? desk.promotion_requirements : []
                const proxySymbols = Array.isArray(desk.proxy_instrument_symbols) ? desk.proxy_instrument_symbols : []
                const engineCoverage = desk.engine_coverage && typeof desk.engine_coverage === 'object' ? desk.engine_coverage : {}
                const engineRows =
                  desk.engine_coverage_summary && Array.isArray(desk.engine_coverage_summary.states)
                    ? desk.engine_coverage_summary.states
                    : Object.entries(engineCoverage).map(([engineKey, state]) => ({
                        engine_key: engineKey,
                        label: humanizeValue(engineKey),
                        state,
                        state_label: formatEngineStateLabel(state),
                      }))
                return (
                  <tr key={`desk-catalog:${desk.desk_key}`}>
                    <td>
                      <strong>{desk.label || humanizeValue(desk.desk_key, 'Desk')}</strong>
                      <br />
                      <span>{desk.description || 'Controlled desk lane.'}</span>
                    </td>
                    <td>{statusLabel}<br />{desk.can_submit_orders ? 'Risk-gated Alpaca paper' : 'Not routeable yet'}</td>
                    <td>
                      {formatSupportMaturity(desk.support_maturity_stage || supportMaturity.stage)}
                      <br />
                      {supportCompleted.length ? supportCompleted.map((item) => formatSupportMaturity(item)).join(', ') : 'Modeled'}
                    </td>
                    <td>{assetScope.length ? assetScope.map((item) => humanizeValue(item)).join(', ') : '--'}<br />{tickers.length ? tickers.slice(0, 8).join(', ') : 'No routeable symbols'}{tickers.length > 8 ? ` +${tickers.length - 8}` : ''}</td>
                    <td>
                      {desk.provider_capability || 'Provider capability not connected.'}
                      <br />
                      Proxy {proxySymbols.length ? proxySymbols.slice(0, 8).join(', ') : 'none'}{proxySymbols.length > 8 ? ` +${proxySymbols.length - 8}` : ''}
                    </td>
                    <td>
                      {engineRows.length ? engineRows.map((item) => (
                        <span className="chip chip--inline" key={`${desk.desk_key}:${item.engine_key}`}>
                          {item.label || humanizeValue(item.engine_key)}: {item.state_label || formatEngineStateLabel(item.state)}
                        </span>
                      )) : 'No engine coverage'}
                    </td>
                    <td>{instruments.length ? instruments.map((item) => humanizeValue(item)).join(', ') : 'Research-only'}</td>
                    <td>{desk.risk_budget_pct == null ? '--' : `${Number(desk.risk_budget_pct).toFixed(0)}%`} budget<br />Hold {humanizeValue(desk.holding_period, '--')} / {desk.max_positions ?? 0} positions</td>
                    <td>
                      {desk.next_action || desk.why_no_trade || 'Keep this lane as controlled coverage.'}
                      <br />
                      Evidence needed before promotion: {promotionRequirements.length ? promotionRequirements.slice(0, 3).join(', ') : 'None'}
                      {dataRequirements.length ? <><br />Data: {dataRequirements.slice(0, 3).join(', ')}</> : null}
                    </td>
                  </tr>
                )
              }) : (
                <tr>
                  <td colSpan={9}>Desk catalog is loading. Active Alpaca paper desks remain unchanged.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="section-stack">
          <FeedbackState
            tone={proxyWorkflowError ? 'warning' : Number(proxyWorkflowSummary.candidate_evidence_count || 0) > 0 ? 'positive' : 'neutral'}
            title="Proxy workflow"
            description={
              proxyWorkflowError ||
              'Refresh a first-wave institutional lane for Candidate evidence. These lanes route through active Alpaca paper desks after operator review and are not standalone order engines.'
            }
          />
          <div className="stack-row stack-row--wrap">
            {proxyWorkflowDeskOptions.length ? proxyWorkflowDeskOptions.map((desk) => (
              <Button
                key={`proxy-workflow-desk:${desk.desk_key}`}
                type="button"
                variant={activeProxyWorkflowDeskKey === desk.desk_key ? 'solid' : 'ghost'}
                onClick={() => setSelectedProxyWorkflowDeskKey(desk.desk_key)}
                disabled={proxyWorkflowBusy}
              >
                {desk.label || humanizeValue(desk.desk_key)}
              </Button>
            )) : (
              <span>Proxy workflows are loading.</span>
            )}
            <Button
              type="button"
              variant="ghost"
              onClick={() => refreshProxyWorkflow(activeProxyWorkflowDeskKey)}
              disabled={proxyWorkflowBusy || !activeProxyWorkflowDeskKey}
            >
              {proxyWorkflowBusy ? 'Refreshing evidence...' : 'Refresh proxy evidence'}
            </Button>
          </div>
          <section className="metrics-grid metrics-grid--compact">
            {[
              { label: 'Selected lane', value: selectedProxyWorkflowDesk?.label || humanizeValue(activeProxyWorkflowDeskKey, 'Desk'), tone: 'neutral' },
              { label: 'Support maturity', value: formatSupportMaturity(selectedProxyWorkflowDesk?.support_maturity_stage || selectedProxyWorkflowDesk?.support_maturity?.stage), tone: 'neutral' },
              { label: 'Scanned proxies', value: proxyWorkflowSummary.scanned_count == null ? '--' : String(proxyWorkflowSummary.scanned_count), tone: Number(proxyWorkflowSummary.scanned_count || 0) > 0 ? 'positive' : 'neutral' },
              { label: 'Candidate evidence', value: proxyWorkflowSummary.candidate_evidence_count == null ? '--' : String(proxyWorkflowSummary.candidate_evidence_count), tone: Number(proxyWorkflowSummary.candidate_evidence_count || 0) > 0 ? 'positive' : 'warning' },
              { label: 'Suggested engine', value: Array.isArray(proxyWorkflowSummary.matching_active_engines) && proxyWorkflowSummary.matching_active_engines.length ? proxyWorkflowSummary.matching_active_engines.map((item) => humanizeValue(item)).join(', ') : humanizeValue(proxyWorkflowCandidateRows[0]?.suggested_engine, 'Awaiting refresh'), tone: 'neutral' },
              { label: 'Execution', value: proxyWorkflowSummary.execution_blocked_for_catalog_desk ? 'Not standalone' : 'Awaiting evidence', tone: 'warning' },
            ].map((item, index) => renderMetricCard(item, `proxy-workflow-${index}`))}
          </section>
          <div className="info-grid">
            <FeedbackState
              tone="neutral"
              title="Route through active Alpaca paper desk"
              description={selectedProxyWorkflowDesk?.routeability_reason || 'Proxy workflow evidence can inform active desks, but this catalog lane cannot submit orders directly.'}
            />
            <FeedbackState
              tone="warning"
              title="Evidence needed before promotion"
              description={
                Array.isArray(selectedProxyWorkflowDesk?.promotion_requirements) && selectedProxyWorkflowDesk.promotion_requirements.length
                  ? selectedProxyWorkflowDesk.promotion_requirements.slice(0, 4).join(', ')
                  : 'Promotion evidence has not been defined for this lane yet.'
              }
            />
          </div>
          <div className="table-shell">
            <table className="list-table">
              <caption>Proxy workflow candidate evidence</caption>
              <thead>
                <tr>
                  <th scope="col">Symbol</th>
                  <th scope="col">Score</th>
                  <th scope="col">Data freshness</th>
                  <th scope="col">Suggested engine</th>
                  <th scope="col">Blocker</th>
                  <th scope="col">Next safe action</th>
                </tr>
              </thead>
              <tbody>
                {proxyWorkflowCandidateRows.length ? proxyWorkflowCandidateRows.map((item, index) => {
                  const scores = item.scores || {}
                  return (
                    <tr key={`proxy-workflow-candidate:${item.ticker || item.symbol || index}`}>
                      <td>
                        <strong>{item.ticker || item.symbol || '--'}</strong>
                        <br />
                        <span>{humanizeValue(item.stage, 'Proxy workflow')} | Candidate evidence</span>
                      </td>
                      <td>
                        {[item.proxy_workflow_score != null ? `Proxy ${Number(item.proxy_workflow_score).toFixed(1)}` : null, item.stage_one_score != null ? `S1 ${Number(item.stage_one_score).toFixed(1)}` : null]
                          .filter(Boolean)
                          .join(' | ') || '--'}
                        <br />
                        {Object.entries(scores)
                          .filter(([key]) => !['stage_one_score', 'proxy_workflow_score'].includes(key))
                          .slice(0, 3)
                          .map(([key, value]) => `${humanizeValue(key)} ${Number(value).toFixed(0)}`)
                          .join(' | ') || 'Score components pending'}
                      </td>
                      <td>
                        {item.data_freshness?.latest_bar_at ? new Date(item.data_freshness.latest_bar_at).toLocaleString() : 'No latest bar'}
                        <br />
                        {humanizeValue(item.quote_freshness?.state, 'Unknown')}
                      </td>
                      <td>{humanizeValue(item.suggested_engine, '--')}<br />{item.paper_routeable_via_existing_engine ? 'Route through active Alpaca paper desk' : 'Not routeable yet'}</td>
                      <td>{humanizeValue(item.blocker, 'None')}<br />{item.execution_blocked_for_catalog_desk ? 'Not a standalone order engine' : 'Execution review required'}</td>
                      <td>{item.next_action || item.handoff_reason || 'Review proxy evidence before promotion.'}</td>
                    </tr>
                  )
                }) : (
                  <tr>
                    <td colSpan={6}>
                      Select a first-wave desk and refresh proxy evidence. The workflow will scan symbols/proxies, explain blockers, and keep mutation: none.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <FeedbackState
            tone="neutral"
            title="Not a standalone order engine"
            description={`Proxy symbols: ${
              Array.isArray(proxyWorkflowUniverse.proxy_symbols) && proxyWorkflowUniverse.proxy_symbols.length
                ? proxyWorkflowUniverse.proxy_symbols.slice(0, 12).join(', ')
                : Array.isArray(selectedProxyWorkflowDesk?.proxy_instrument_symbols) && selectedProxyWorkflowDesk.proxy_instrument_symbols.length
                  ? selectedProxyWorkflowDesk.proxy_instrument_symbols.slice(0, 12).join(', ')
                  : 'awaiting selected desk metadata'
            }. All execution remains broker_paper through the five active Alpaca paper engines.`}
          />
        </div>
      </section>
      {readinessCategories.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Trade Automation readiness checks</caption>
            <thead>
              <tr>
                <th>Area</th>
                <th>Score</th>
                <th>Status</th>
                <th>Next action</th>
              </tr>
            </thead>
            <tbody>
              {readinessCategories.map((category) => (
                <tr key={category.key || category.label}>
                  <td>{category.label || humanizeValue(category.key, '--')}</td>
                  <td>{Number(category.percent || 0).toFixed(0)}%</td>
                  <td>{humanizeValue(category.status, '--')}</td>
                  <td>{category.next_action || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {readinessIssues.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Trade Automation readiness blockers and warnings</caption>
            <thead>
              <tr>
                <th>Type</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {readinessIssues.slice(0, 12).map((item) => (
                <tr key={item.key}>
                  <td>{humanizeValue(item.severity, '--')}</td>
                  <td>{item.detail || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {collectionPhase ? (
        <FeedbackState
          tone={collectionPhase.tone}
          title={collectionPhase.title}
          description={collectionPhase.description}
        />
      ) : null}
      {form.executionIntent === 'broker_live' ? (
        <FeedbackState
          tone={rolloutReadiness?.allows_live_rollout ? 'positive' : rolloutReadiness?.status === 'locked' ? 'negative' : 'warning'}
          title={rolloutReadiness?.label || 'Live readiness'}
          description={rolloutReadiness?.detail || 'Alpaca live automation stays behind the paper gate until readiness clears.'}
        />
      ) : null}
      {rankedEntryGate ? (
        <FeedbackState
          tone={rankedEntryGate.tone}
          title={rankedEntryGate.title}
          description={rankedEntryGate.description}
        />
      ) : null}
      {validationSample ? (
        <FeedbackState
          tone={validationSample.tone}
          title={validationSample.title}
          description={validationSample.description}
        />
      ) : null}
      {snapshot?.runtime?.last_error ? (
        <FeedbackState tone="negative" title="Last cycle error" description={snapshot.runtime.last_error} />
      ) : null}
      {lastRejection ? (
        <FeedbackState
          tone="warning"
          title={`Last rejection: ${String(lastRejection.reason || 'filter').replace(/_/g, ' ')}`}
          description={lastRejection.detail || 'The last cycle rejected a candidate.'}
        />
      ) : null}
      {guardrails?.status?.label ? (
        <FeedbackState
          tone={guardrails.status.tone || 'neutral'}
          title={`Guardrails: ${guardrails.status.label}`}
          description={guardrails.status.detail || 'Automation capital locks are loaded.'}
        />
      ) : null}
      {lossContainment ? (
        <FeedbackState
          tone={lossContainmentTone}
          title={`Loss containment: ${humanizeValue(lossContainment.status, 'Not run')}`}
          description={`${Number(lossContainment.open_heat_pct || 0).toFixed(2)}% open heat | ${lossContainment.entries_blocked ? 'new entries blocked' : 'new entries allowed'} | ${lossContainmentActions.length} defensive action(s)`}
        />
      ) : null}
      {exitWatchdog ? (
        <FeedbackState
          tone={exitWatchdogTone}
          title={`Exit watchdog: ${humanizeValue(exitWatchdog.status, 'Not run')}`}
          description={`${exitWatchdog.pending_exit_count ?? 0} pending | ${exitWatchdog.stuck_exit_count ?? 0} stuck | ${exitWatchdog.entries_blocked ? 'new entries blocked' : 'new entries allowed'}`}
        />
      ) : null}
      {controlPlane ? (
        <FeedbackState
          tone={controlPlane.tone}
          title={controlPlane.title}
          description={controlPlane.description}
        />
      ) : null}
      {shadowValidation ? (
        <FeedbackState
          tone={shadowValidation.tone}
          title={shadowValidation.title}
          description={shadowValidation.description}
        />
      ) : null}
      {paperBrokerReconciliation ? (
        <FeedbackState
          tone={paperBrokerReconciliation.tone}
          title={paperBrokerReconciliation.title}
          description={paperBrokerReconciliation.description}
        />
      ) : null}
      {paperOrderLifecycleSoak ? (
        <FeedbackState
          tone={paperOrderLifecycleSoak.tone}
          title={paperOrderLifecycleSoak.title}
          description={paperOrderLifecycleSoak.description}
        />
      ) : null}
      {paperOrderLifecycleCanary ? (
        <FeedbackState
          tone={paperOrderLifecycleCanary.tone}
          title={paperOrderLifecycleCanary.title}
          description={paperOrderLifecycleCanary.description}
        />
      ) : null}
      {paperCanary ? (
        <FeedbackState
          tone={paperCanary.tone}
          title={paperCanary.title}
          description={paperCanary.description}
        />
      ) : null}
      {livePilotReadiness ? (
        <FeedbackState
          tone={livePilotReadiness.tone}
          title={livePilotReadiness.title}
          description={livePilotReadiness.description}
        />
      ) : null}
      {livePilotSoak ? (
        <FeedbackState
          tone={livePilotSoak.tone}
          title={livePilotSoak.title}
          description={livePilotSoak.description}
        />
      ) : null}
      {livePilotCanary ? (
        <FeedbackState
          tone={livePilotCanary.tone}
          title={livePilotCanary.title}
          description={livePilotCanary.description}
        />
      ) : null}
      {livePilotExpansion ? (
        <FeedbackState
          tone={livePilotExpansion.tone}
          title={livePilotExpansion.title}
          description={livePilotExpansion.description}
        />
      ) : null}
      {livePilotExpansionCanary ? (
        <FeedbackState
          tone={livePilotExpansionCanary.tone}
          title={livePilotExpansionCanary.title}
          description={livePilotExpansionCanary.description}
        />
      ) : null}
      {livePilotWindow ? (
        <FeedbackState
          tone={livePilotWindow.tone}
          title={livePilotWindow.title}
          description={livePilotWindow.description}
        />
      ) : null}
      {livePilotWindowCanary ? (
        <FeedbackState
          tone={livePilotWindowCanary.tone}
          title={livePilotWindowCanary.title}
          description={livePilotWindowCanary.description}
        />
      ) : null}
      {livePilotPromotionReport ? (
        <FeedbackState
          tone={livePilotPromotionReport.tone}
          title={livePilotPromotionReport.title}
          description={livePilotPromotionReport.description}
        />
      ) : null}
      {limitedLiveRolloutGate ? (
        <FeedbackState
          tone={limitedLiveRolloutGate.tone}
          title={limitedLiveRolloutGate.title}
          description={limitedLiveRolloutGate.description}
        />
      ) : null}
      {limitedLiveRolloutCanary ? (
        <FeedbackState
          tone={limitedLiveRolloutCanary.tone}
          title={limitedLiveRolloutCanary.title}
          description={limitedLiveRolloutCanary.description}
        />
      ) : null}
      {limitedLiveCapExpansionReport ? (
        <FeedbackState
          tone={limitedLiveCapExpansionReport.tone}
          title={limitedLiveCapExpansionReport.title}
          description={limitedLiveCapExpansionReport.description}
        />
      ) : null}
      {limitedLiveCapExpansionGate ? (
        <FeedbackState
          tone={limitedLiveCapExpansionGate.tone}
          title={limitedLiveCapExpansionGate.title}
          description={limitedLiveCapExpansionGate.description}
        />
      ) : null}
      {limitedLiveCapExpansionCanary ? (
        <FeedbackState
          tone={limitedLiveCapExpansionCanary.tone}
          title={limitedLiveCapExpansionCanary.title}
          description={limitedLiveCapExpansionCanary.description}
        />
      ) : null}
      {limitedLiveNextTierCapReport ? (
        <FeedbackState
          tone={limitedLiveNextTierCapReport.tone}
          title={limitedLiveNextTierCapReport.title}
          description={limitedLiveNextTierCapReport.description}
        />
      ) : null}
      {limitedLiveNextTierCapGate ? (
        <FeedbackState
          tone={limitedLiveNextTierCapGate.tone}
          title={limitedLiveNextTierCapGate.title}
          description={limitedLiveNextTierCapGate.description}
        />
      ) : null}
      {limitedLiveCapLadder ? (
        <FeedbackState
          tone={limitedLiveLadderToneFor(limitedLiveHigherCapReport.status || limitedLiveNextTierCapCanary.status || limitedLiveCapLadder.status)}
          title="Live Rollout Ladder"
          description={`$100 -> $250 -> $500 -> $1000 policy loaded. Latest next-tier canary: ${humanizeValue(limitedLiveNextTierCapCanary.status, 'not run')}; higher-cap report: ${humanizeValue(limitedLiveHigherCapReport.status, 'not run')}.`}
        />
      ) : null}
      {brokerRoutes?.broker_live ? (
        <FeedbackState
          tone={brokerRoutes.broker_live.active ? 'positive' : brokerRoutes.broker_live.connected ? 'warning' : 'neutral'}
          title={`Alpaca live: ${brokerRoutes.broker_live.value || 'Unavailable'}`}
          description={brokerRoutes.broker_live.detail || 'Alpaca live status is not available.'}
        />
      ) : null}
      {performance?.status?.label ? (
        <FeedbackState
          tone={performance.status.tone || 'neutral'}
          title={`Automation health: ${performance.status.label}`}
          description={performance.status.detail || 'Automation reporting is ready.'}
        />
      ) : null}
      {optionDiagnostics ? (
        <FeedbackState
          tone={optionDiagnostics.tone}
          title={optionDiagnostics.title}
          description={optionDiagnostics.description}
        />
      ) : null}
      {aiReview ? (
        <FeedbackState
          tone={aiReview.tone}
          title={aiReview.title}
          description={aiReview.description}
        />
      ) : null}

      <section className="metrics-grid metrics-grid--compact">
        {metrics.map((item, index) => renderMetricCard(item, `metric-${index}`))}
      </section>
      {limitedLiveLadderCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {limitedLiveLadderCards.map((item, index) => renderMetricCard(item, `limited-live-ladder-${index}`))}
        </section>
      ) : null}
      {limitedLiveLadderTiers.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Live Rollout Ladder</caption>
            <thead>
              <tr>
                <th>Rung</th>
                <th>Cap</th>
                <th>Allowance</th>
                <th>Gate</th>
                <th>Canary</th>
                <th>Orders</th>
              </tr>
            </thead>
            <tbody>
              {limitedLiveLadderTiers.map((tier) => (
                <tr key={tier.key || tier.label}>
                  <td>{tier.label || humanizeValue(tier.key, '--')}</td>
                  <td>{formatMoney(tier.max_notional)}</td>
                  <td>{tier.advisory_only ? 'Advisory' : tier.allowance_active ? 'Active' : humanizeValue(tier.allowance_status, 'Inactive')}</td>
                  <td>{humanizeValue(tier.gate_status, 'Not run')}</td>
                  <td>{humanizeValue(tier.canary_status, 'Not run')}</td>
                  <td>{tier.consumed_order_count ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {limitedLiveLadderIssues.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Limited-live ladder blockers and warnings</caption>
            <thead>
              <tr>
                <th>Type</th>
                <th>Source</th>
                <th>Key</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {limitedLiveLadderIssues.slice(0, 12).map((item, index) => (
                <tr key={`${item.source || 'ladder'}:${item.key || index}`}>
                  <td>{humanizeValue(item.severity, '--')}</td>
                  <td>{humanizeValue(item.source || item.component, '--')}</td>
                  <td>{humanizeValue(item.key, '--')}</td>
                  <td>{item.detail || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {limitedLiveApprovalEntries.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Limited-live approval ledger</caption>
            <thead>
              <tr>
                <th>Time</th>
                <th>Event</th>
                <th>Status</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {limitedLiveApprovalEntries.slice(0, 8).map((item, index) => (
                <tr key={`${item.event_type || 'ledger'}:${item.at || index}`}>
                  <td>{item.at || '--'}</td>
                  <td>{humanizeValue(item.event_type, '--')}</td>
                  <td>{humanizeValue(item.status, '--')}</td>
                  <td>{item.detail || item.disabled_reason || item.rollback_reason || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <TradeAutomationAccountSummary summary={snapshot?.account_summary} profileKey={snapshot?.profile_key} />
      {brokerBalanceCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {brokerBalanceCards.map((item, index) => renderMetricCard(item, `broker-balance-${index}`))}
        </section>
      ) : null}
      {brokerRouteCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {brokerRouteCards.map((item, index) => renderMetricCard(item, `broker-route-${index}`))}
        </section>
      ) : null}
      {rankedEntryGateCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {rankedEntryGateCards.map((item, index) => renderMetricCard(item, `ranked-entry-${index}`))}
        </section>
      ) : null}
      {validationSampleCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {validationSampleCards.map((item, index) => renderMetricCard(item, `validation-sample-${index}`))}
        </section>
      ) : null}
      {collectionPhaseCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {collectionPhaseCards.map((item, index) => renderMetricCard(item, `collection-phase-${index}`))}
        </section>
      ) : null}
      {optionDiagnostics?.metrics?.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {optionDiagnostics.metrics.map((item, index) => renderMetricCard(item, `option-diagnostic-${index}`))}
        </section>
      ) : null}
      {controlPlaneCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {controlPlaneCards.map((item, index) => renderMetricCard(item, `state-control-${index}`))}
        </section>
      ) : null}
      {shadowValidationCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {shadowValidationCards.map((item, index) => renderMetricCard(item, `state-shadow-${index}`))}
        </section>
      ) : null}
      {paperBrokerReconciliationCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {paperBrokerReconciliationCards.map((item, index) => renderMetricCard(item, `paper-broker-${index}`))}
        </section>
      ) : null}
      {paperOrderLifecycleCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {paperOrderLifecycleCards.map((item, index) => renderMetricCard(item, `paper-order-lifecycle-${index}`))}
        </section>
      ) : null}
      {paperOrderLifecycleCanaryCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {paperOrderLifecycleCanaryCards.map((item, index) => renderMetricCard(item, `paper-order-lifecycle-canary-${index}`))}
        </section>
      ) : null}
      {paperCanaryCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {paperCanaryCards.map((item, index) => renderMetricCard(item, `paper-canary-${index}`))}
        </section>
      ) : null}
      {livePilotReadinessCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {livePilotReadinessCards.map((item, index) => renderMetricCard(item, `live-pilot-readiness-${index}`))}
        </section>
      ) : null}
      {livePilotSoakCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {livePilotSoakCards.map((item, index) => renderMetricCard(item, `live-pilot-soak-${index}`))}
        </section>
      ) : null}
      {livePilotCanaryCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {livePilotCanaryCards.map((item, index) => renderMetricCard(item, `live-pilot-canary-${index}`))}
        </section>
      ) : null}
      {livePilotExpansionCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {livePilotExpansionCards.map((item, index) => renderMetricCard(item, `live-pilot-expansion-${index}`))}
        </section>
      ) : null}
      {livePilotExpansionCanaryCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {livePilotExpansionCanaryCards.map((item, index) => renderMetricCard(item, `live-pilot-expansion-canary-${index}`))}
        </section>
      ) : null}
      {livePilotWindowCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {livePilotWindowCards.map((item, index) => renderMetricCard(item, `live-pilot-window-${index}`))}
        </section>
      ) : null}
      {livePilotWindowCanaryCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {livePilotWindowCanaryCards.map((item, index) => renderMetricCard(item, `live-pilot-window-canary-${index}`))}
        </section>
      ) : null}
      {livePilotPromotionReportCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {livePilotPromotionReportCards.map((item, index) => renderMetricCard(item, `live-pilot-promotion-${index}`))}
        </section>
      ) : null}
      {limitedLiveRolloutCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {limitedLiveRolloutCards.map((item, index) => renderMetricCard(item, `limited-live-rollout-${index}`))}
        </section>
      ) : null}
      {limitedLiveRolloutCanaryCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {limitedLiveRolloutCanaryCards.map((item, index) => renderMetricCard(item, `limited-live-rollout-canary-${index}`))}
        </section>
      ) : null}
      {limitedLiveCapExpansionCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {limitedLiveCapExpansionCards.map((item, index) => renderMetricCard(item, `limited-live-cap-expansion-${index}`))}
        </section>
      ) : null}
      {limitedLiveCapExpansionGateCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {limitedLiveCapExpansionGateCards.map((item, index) => renderMetricCard(item, `limited-live-cap-expansion-gate-${index}`))}
        </section>
      ) : null}
      {limitedLiveCapExpansionCanaryCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {limitedLiveCapExpansionCanaryCards.map((item, index) => renderMetricCard(item, `limited-live-cap-expansion-canary-${index}`))}
        </section>
      ) : null}
      {limitedLiveNextTierCapCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {limitedLiveNextTierCapCards.map((item, index) => renderMetricCard(item, `limited-live-next-tier-cap-${index}`))}
        </section>
      ) : null}
      {limitedLiveNextTierCapGateCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {limitedLiveNextTierCapGateCards.map((item, index) => renderMetricCard(item, `limited-live-next-tier-cap-gate-${index}`))}
        </section>
      ) : null}
      {dailyObjectiveCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {dailyObjectiveCards.map((item, index) => renderMetricCard(item, `daily-objective-${index}`))}
        </section>
      ) : null}
      <FeedbackState
        compact
        tone={dailyObjectiveTone}
        title="Weekly 1-2% objective"
        description={`${formatMoney(dailyObjective.total_pnl)} ${dailyObjective.objective_period_label || 'this week'} | ${dailyObjective.stretch_target_reached ? 'stretch target reached' : dailyObjective.minimum_target_reached ? `${formatMoney(dailyObjective.target_gap)} stretch gap` : `${formatMoney(dailyObjective.target_min_gap)} minimum gap`} | ${dailyObjective.entries_blocked ? 'new paper entries blocked by objective or daily loss budget' : 'target-only, entries remain governed by risk controls'}`}
      />
      {paperEvidenceCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {paperEvidenceCards.map((item, index) => renderMetricCard(item, `paper-evidence-${index}`))}
        </section>
      ) : null}
      <FeedbackState
        compact
        tone={paperEvidenceTone}
        title="Monday paper evidence"
        description={`${humanizeValue(paperEvidence.status, 'Not run')} | ${paperEvidence.edge_coverage_pct ?? 0}% edge coverage | ${paperEvidence.spread_coverage_pct ?? 0}% spread coverage | ${paperEvidence.note_coverage ? 'notes linked' : 'notes pending'}`}
      />
      {replayLabCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {replayLabCards.map((item, index) => renderMetricCard(item, `replay-lab-${index}`))}
        </section>
      ) : null}
      <FeedbackState
        compact
        tone={replayLab.tone}
        title="Paper replay lab"
        description={replayLab.description}
      />
      {transactionCostCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {transactionCostCards.map((item, index) => renderMetricCard(item, `transaction-cost-${index}`))}
        </section>
      ) : null}
      <FeedbackState
        compact
        tone={transactionCostCalibration.tone}
        title="Paper cost, liquidity, and fill quality"
        description={transactionCostCalibration.description}
      />
      {lossContainmentCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {lossContainmentCards.map((item, index) => renderMetricCard(item, `loss-containment-${index}`))}
        </section>
      ) : null}
      <FeedbackState
        compact
        tone={lossContainmentTone}
        title="Loss containment"
        description={`${humanizeValue(lossContainment.status, 'Not run')} | ${lossContainment.entries_blocked ? 'new entries blocked' : 'new entries allowed'} | ${Array.isArray(lossContainment.defensive_actions) ? lossContainment.defensive_actions.length : 0} defensive action(s)`}
      />
      {exitWatchdogCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {exitWatchdogCards.map((item, index) => renderMetricCard(item, `exit-watchdog-${index}`))}
        </section>
      ) : null}
      <FeedbackState
        compact
        tone={exitWatchdogTone}
        title="Exit execution watchdog"
        description={`${humanizeValue(exitWatchdog.status, 'Not run')} | ${exitWatchdog.pending_exit_count ?? 0} pending | ${exitWatchdog.entries_blocked ? 'new entries blocked' : 'confirmation clear'}`}
      />
      {accuracyCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {accuracyCards.map((item, index) => renderMetricCard(item, `accuracy-calibration-${index}`))}
        </section>
      ) : null}
      <FeedbackState
        compact
        tone={accuracyTone}
        title="Decision-PnL accuracy"
        description={`${humanizeValue(accuracyCalibration.status, 'Not run')} | ${accuracyCalibration.sample_count ?? 0}/${accuracyCalibration.min_samples ?? form.accuracyCalibrationMinSamples} samples | ${Number(accuracyCalibration.missed_opportunity_count || 0)} missed opportunity signal(s)`}
      />
      {aiReviewCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {aiReviewCards.map((item, index) => renderMetricCard(item, `ai-review-${index}`))}
        </section>
      ) : null}
      {performanceCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {performanceCards.map((item, index) => renderMetricCard(item, `performance-${index}`))}
        </section>
      ) : null}
      {guardrailCards.length ? (
        <section className="metrics-grid metrics-grid--compact">
          {guardrailCards.map((item, index) => renderMetricCard(item, `guardrail-${index}`))}
        </section>
      ) : null}
      {performanceCards.length ? (
        <FeedbackState
          compact
          tone={performance?.status?.tone || 'neutral'}
          title={isPersonalMode ? 'Autonomous desk scorecard' : 'Automation scorecard'}
          description={`Closed ${performanceMetrics.closed_trade_count ?? 0} automation trades | ${formatMoney(performanceMetrics.total_pnl)} total PnL | ${formatPercent(performanceMetrics.win_rate)} win rate`}
        />
      ) : null}
      {guardrailCards.length ? (
        <FeedbackState
          compact
          tone={guardrails?.status?.tone || 'neutral'}
          title="Capital locks"
          description={`${formatMoney(guardrailMetrics.today_realized_pnl)} today | ${guardrailMetrics.entries_today ?? 0} entries | ${formatMoney(guardrailMetrics.open_notional)} open notional`}
        />
      ) : null}

      <div className="ui-field-grid ui-field-grid--settings">
        <ToggleField label="Enable automation" hint="Keep settings saved and allow the server worker to evaluate unattended cycles." checked={form.enabled} onChange={(e) => setForm((current) => ({ ...current, enabled: e.target.checked }))} />
        <SelectField label="Execution route" hint={collectionPhase?.active ? 'Collection phase hard-locks routing to Alpaca paper until current-route validation clears.' : 'Alpaca paper is the safest unattended route until live readiness is actually clear.'} value={form.executionIntent} onChange={(e) => setForm((current) => ({ ...current, executionIntent: e.target.value }))} disabled={collectionPhase?.active}>
          {EXECUTION_INTENT_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </SelectField>
        <TextField label="Automation board" hint="Comma-separated symbols scanned every cycle." value={form.tickers} onChange={(e) => setForm((current) => ({ ...current, tickers: e.target.value.toUpperCase() }))} />
        <SelectField label="Interval" hint="Use the same intraday frame you want the unattended board to qualify." value={form.interval} onChange={(e) => setForm((current) => ({ ...current, interval: e.target.value }))}>
          {INTERVAL_OPTIONS.map((option) => <option key={option} value={option}>{option}</option>)}
        </SelectField>
        <TextField label="Horizon" type="number" min="1" max="50" value={form.horizon} onChange={(e) => setForm((current) => ({ ...current, horizon: e.target.value }))} />
        <TextField label="Cycle interval (seconds)" type="number" min="15" step="15" value={form.cycleIntervalSeconds} onChange={(e) => setForm((current) => ({ ...current, cycleIntervalSeconds: e.target.value }))} />
        <TextField label="Ticker cooldown (minutes)" type="number" min="0" step="1" value={form.cooldownMinutes} onChange={(e) => setForm((current) => ({ ...current, cooldownMinutes: e.target.value }))} />
        <TextField
          label="Effective funds"
          hint={
            snapshot?.effective_funds_detail
              ? `${snapshot.effective_funds_detail} Actual funds source: ${humanizeValue(snapshot?.actual_funds_source, '--')}.`
              : `Sizing uses the Alpaca live balance for this profile. Source: ${humanizeValue(snapshot?.funds_source, '--')}.`
          }
          value={form.accountSize}
          readOnly
          disabled
        />
        <TextField
          label="Sizing multiplier"
          hint="Deployable sizing funds = min(buying power, actual broker balance × multiplier). Drawdown still tracks actual equity."
          type="number"
          min="1"
          max="10"
          step="0.05"
          value={form.effectiveFundsMultiplier}
          onChange={(e) => setForm((current) => ({ ...current, effectiveFundsMultiplier: e.target.value }))}
        />
        <TextField label="Risk % / trade" hint="Keep unattended intraday sizing small. 0.50% is the current ranked-entry baseline." type="number" min="0.05" max="5" step="0.05" value={form.riskPercent} onChange={(e) => setForm((current) => ({ ...current, riskPercent: e.target.value }))} />
        <ToggleField label="Weekly 1-2% objective" hint="Paper-first weekly target tracking and candidate ranking overlay. It does not promise returns or bypass risk gates." checked={form.dailyObjectiveEnabled} onChange={(e) => setForm((current) => ({ ...current, dailyObjectiveEnabled: e.target.checked }))} />
        <TextField label="Weekly minimum target $" hint="Minimum weekly objective floor used for progress, notes, and candidate prioritization." type="number" min="1" max="1000000" step="50" value={form.weeklyProfitTargetMinDollars} onChange={(e) => setForm((current) => ({ ...current, weeklyProfitTargetMinDollars: e.target.value }))} />
        <TextField label="Weekly stretch target $" hint="Stretch target where protect mode can block new entries after evidence is clean." type="number" min="1" max="1000000" step="50" value={form.weeklyProfitTargetMaxDollars} onChange={(e) => setForm((current) => ({ ...current, weeklyProfitTargetMaxDollars: e.target.value }))} />
        <TextField label="Weekly minimum %" hint="Lower bound of the weekly operating objective." type="number" min="0.1" max="10" step="0.1" value={form.weeklyProfitTargetMinPct} onChange={(e) => setForm((current) => ({ ...current, weeklyProfitTargetMinPct: e.target.value }))} />
        <TextField label="Weekly stretch %" hint="Upper bound of the weekly operating objective and protect threshold." type="number" min="0.1" max="10" step="0.1" value={form.weeklyProfitTargetMaxPct} onChange={(e) => setForm((current) => ({ ...current, weeklyProfitTargetMaxPct: e.target.value }))} />
        <TextField label="Daily loss budget %" hint="Hard paper new-entry stop when same-day objective PnL breaches this loss budget." type="number" min="0.1" max="10" step="0.1" value={form.dailyLossBudgetPct} onChange={(e) => setForm((current) => ({ ...current, dailyLossBudgetPct: e.target.value }))} />
        <ToggleField label="Objective live scope" hint="Off by default. Live caps still only move through the limited-live safety ladder." checked={form.dailyObjectiveApplyToLive} onChange={(e) => setForm((current) => ({ ...current, dailyObjectiveApplyToLive: e.target.checked }))} />
        <ToggleField label="Paper evidence collection" hint="Records ranked-candidate telemetry and Monday evidence for calibration." checked={form.paperEvidenceCollectionEnabled} onChange={(e) => setForm((current) => ({ ...current, paperEvidenceCollectionEnabled: e.target.checked }))} />
        <ToggleField label="Paper evidence auto review" hint="Runs the paper evidence summary after the session with objective, accuracy, and replay reviews." checked={form.paperEvidenceAutoReviewEnabled} onChange={(e) => setForm((current) => ({ ...current, paperEvidenceAutoReviewEnabled: e.target.checked }))} />
        <ToggleField label="Require edge telemetry" hint="Paper entries need expected edge and edge-to-cost evidence before selection." checked={form.paperEvidenceRequireEdgeTelemetry} onChange={(e) => setForm((current) => ({ ...current, paperEvidenceRequireEdgeTelemetry: e.target.checked }))} />
        <ToggleField label="Require spread telemetry" hint="Paper entries need spread evidence before selection." checked={form.paperEvidenceRequireSpreadTelemetry} onChange={(e) => setForm((current) => ({ ...current, paperEvidenceRequireSpreadTelemetry: e.target.checked }))} />
        <ToggleField label="Replay lab" hint="Paper-first what-if optimizer for candidate ranking, risk settings, stress, loss containment, and objective behavior." checked={form.replayLabEnabled} onChange={(e) => setForm((current) => ({ ...current, replayLabEnabled: e.target.checked }))} />
        <ToggleField label="Replay auto review" hint="Run post-close after paper objective, accuracy, loss, and canary evidence is available." checked={form.replayLabAutoReviewEnabled} onChange={(e) => setForm((current) => ({ ...current, replayLabAutoReviewEnabled: e.target.checked }))} />
        <TextField label="Replay sessions" hint="Trading-session lookback used for baseline, sensitivity, and stress replay." type="number" min="1" max="60" step="1" value={form.replayLabWindowSessions} onChange={(e) => setForm((current) => ({ ...current, replayLabWindowSessions: e.target.value }))} />
        <TextField label="Replay min trades" hint="Closed paper trades required before replay makes stronger capacity recommendations." type="number" min="1" max="500" step="1" value={form.replayLabMinTrades} onChange={(e) => setForm((current) => ({ ...current, replayLabMinTrades: e.target.value }))} />
        <TextField label="Replay max changes" hint="Maximum advisory setting recommendations shown in one replay review." type="number" min="0" max="12" step="1" value={form.replayLabMaxRecommendedSettingChanges} onChange={(e) => setForm((current) => ({ ...current, replayLabMaxRecommendedSettingChanges: e.target.value }))} />
        <ToggleField label="Replay live scope" hint="Off by default. Replay can inform later live reports but cannot raise caps or bypass the ladder." checked={form.replayLabApplyToLive} onChange={(e) => setForm((current) => ({ ...current, replayLabApplyToLive: e.target.checked }))} />
        <ToggleField label="Transaction cost calibration" hint="Paper-first spread, slippage, liquidity, and fill-quality calibration for candidate ranking." checked={form.transactionCostCalibrationEnabled} onChange={(e) => setForm((current) => ({ ...current, transactionCostCalibrationEnabled: e.target.checked }))} />
        <ToggleField label="Cost auto review" hint="Runs post-close after paper evidence, accuracy calibration, and replay lab finish." checked={form.transactionCostCalibrationAutoReviewEnabled} onChange={(e) => setForm((current) => ({ ...current, transactionCostCalibrationAutoReviewEnabled: e.target.checked }))} />
        <TextField label="Cost min samples" hint="Paper fills required before cost penalties become trusted." type="number" min="1" max="500" step="1" value={form.transactionCostCalibrationMinSamples} onChange={(e) => setForm((current) => ({ ...current, transactionCostCalibrationMinSamples: e.target.value }))} />
        <TextField label="Cost stale sessions" hint="Recent trading-session lookback for cost calibration freshness." type="number" min="1" max="60" step="1" value={form.transactionCostCalibrationStaleAfterSessions} onChange={(e) => setForm((current) => ({ ...current, transactionCostCalibrationStaleAfterSessions: e.target.value }))} />
        <TextField label="Max cost penalty" hint="Largest candidate score penalty from realized cost drift." type="number" min="0" max="100" step="1" value={form.transactionCostCalibrationMaxCandidatePenalty} onChange={(e) => setForm((current) => ({ ...current, transactionCostCalibrationMaxCandidatePenalty: e.target.value }))} />
        <ToggleField label="Cost live scope" hint="Off by default. Live reports may view evidence, but routing remains ladder-gated." checked={form.transactionCostCalibrationApplyToLive} onChange={(e) => setForm((current) => ({ ...current, transactionCostCalibrationApplyToLive: e.target.checked }))} />
        <ToggleField label="Loss containment" hint="Paper-first open-position heat, MAE, stale quote, and defensive-exit guard." checked={form.lossContainmentEnabled} onChange={(e) => setForm((current) => ({ ...current, lossContainmentEnabled: e.target.checked }))} />
        <ToggleField label="Paper defensive exits" hint="Use existing paper close mechanics when hard loss-containment rules breach." checked={form.lossContainmentAutoClosePaper} onChange={(e) => setForm((current) => ({ ...current, lossContainmentAutoClosePaper: e.target.checked }))} />
        <TextField label="Max open heat %" hint="Block new entries when unrealized open losses exceed this equity percentage." type="number" min="0.05" max="10" step="0.05" value={form.lossContainmentMaxOpenHeatPct} onChange={(e) => setForm((current) => ({ ...current, lossContainmentMaxOpenHeatPct: e.target.value }))} />
        <TextField label="Max position loss R" hint="Trigger a defensive paper exit when one position exceeds this loss multiple." type="number" min="0.05" max="10" step="0.05" value={form.lossContainmentMaxPositionLossR} onChange={(e) => setForm((current) => ({ ...current, lossContainmentMaxPositionLossR: e.target.value }))} />
        <TextField label="Max MAE %" hint="Trigger a defensive paper exit when adverse excursion exceeds this percent." type="number" min="0.05" max="25" step="0.05" value={form.lossContainmentMaxPositionMaePct} onChange={(e) => setForm((current) => ({ ...current, lossContainmentMaxPositionMaePct: e.target.value }))} />
        <TextField label="Profit protect trigger R" hint="Start watching profit giveback after a position reaches this favorable R." type="number" min="0.05" max="25" step="0.05" value={form.lossContainmentProfitProtectTriggerR} onChange={(e) => setForm((current) => ({ ...current, lossContainmentProfitProtectTriggerR: e.target.value }))} />
        <TextField label="Profit protect floor R" hint="Exit if a winner gives back below this R after the trigger." type="number" min="-10" max="25" step="0.05" value={form.lossContainmentProfitProtectFloorR} onChange={(e) => setForm((current) => ({ ...current, lossContainmentProfitProtectFloorR: e.target.value }))} />
        <TextField label="Time stop minutes" hint="Flag non-positive open positions that overstay this limit." type="number" min="1" max="480" step="1" value={form.lossContainmentTimeStopMinutes} onChange={(e) => setForm((current) => ({ ...current, lossContainmentTimeStopMinutes: e.target.value }))} />
        <TextField label="Stale quote seconds" hint="Block new entries when open-position quote evidence is stale." type="number" min="15" max="3600" step="15" value={form.lossContainmentStaleQuoteSeconds} onChange={(e) => setForm((current) => ({ ...current, lossContainmentStaleQuoteSeconds: e.target.value }))} />
        <ToggleField label="Loss live scope" hint="Off by default. Live containment remains advisory unless explicitly enabled." checked={form.lossContainmentApplyToLive} onChange={(e) => setForm((current) => ({ ...current, lossContainmentApplyToLive: e.target.checked }))} />
        <ToggleField label="Exit watchdog" hint="Verifies defensive exits reach terminal broker/local proof before new risk is added." checked={form.exitWatchdogEnabled} onChange={(e) => setForm((current) => ({ ...current, exitWatchdogEnabled: e.target.checked }))} />
        <TextField label="Exit confirmation seconds" hint="Maximum wait for full terminal proof after a defensive exit request." type="number" min="10" max="3600" step="5" value={form.exitWatchdogMaxConfirmationSeconds} onChange={(e) => setForm((current) => ({ ...current, exitWatchdogMaxConfirmationSeconds: e.target.value }))} />
        <TextField label="Partial exit minutes" hint="Maximum partial-exit age before the watchdog treats it as stuck." type="number" min="1" max="120" step="1" value={form.exitWatchdogMaxPartialMinutes} onChange={(e) => setForm((current) => ({ ...current, exitWatchdogMaxPartialMinutes: e.target.value }))} />
        <ToggleField label="Block on unconfirmed exit" hint="Blocks new entries while defensive exits lack terminal confirmation." checked={form.exitWatchdogBlockEntriesOnUnconfirmedExit} onChange={(e) => setForm((current) => ({ ...current, exitWatchdogBlockEntriesOnUnconfirmedExit: e.target.checked }))} />
        <ToggleField label="Exit live scope" hint="Off by default. Live watchdog evidence remains advisory unless explicitly enabled." checked={form.exitWatchdogApplyToLive} onChange={(e) => setForm((current) => ({ ...current, exitWatchdogApplyToLive: e.target.checked }))} />
        <ToggleField label="Accuracy calibration" hint="Paper-first decision-PnL calibration. It penalizes patterns that lose after costs." checked={form.accuracyCalibrationEnabled} onChange={(e) => setForm((current) => ({ ...current, accuracyCalibrationEnabled: e.target.checked }))} />
        <TextField label="Accuracy min samples" hint="Closed paper outcomes required before calibration applies stronger ranking penalties." type="number" min="1" max="500" step="1" value={form.accuracyCalibrationMinSamples} onChange={(e) => setForm((current) => ({ ...current, accuracyCalibrationMinSamples: e.target.value }))} />
        <TextField label="Accuracy stale sessions" hint="Recent session window used when showing calibration freshness." type="number" min="1" max="60" step="1" value={form.accuracyCalibrationStaleAfterSessions} onChange={(e) => setForm((current) => ({ ...current, accuracyCalibrationStaleAfterSessions: e.target.value }))} />
        <TextField label="Max calibration penalty" hint="Largest score penalty the decision-PnL layer can apply to one candidate." type="number" min="0" max="100" step="1" value={form.accuracyCalibrationMaxCandidatePenalty} onChange={(e) => setForm((current) => ({ ...current, accuracyCalibrationMaxCandidatePenalty: e.target.value }))} />
        <ToggleField label="Accuracy live scope" hint="Off by default. Live promotion can view calibration evidence, but live caps remain ladder-controlled." checked={form.accuracyCalibrationApplyToLive} onChange={(e) => setForm((current) => ({ ...current, accuracyCalibrationApplyToLive: e.target.checked }))} />
        <ToggleField label="Auto trade equities" checked={form.autoTradeEquities} onChange={(e) => setForm((current) => ({ ...current, autoTradeEquities: e.target.checked }))} />
        <ToggleField label="Auto trade listed options" hint="Run listed-option entries in parallel with the equity board when a clean contract exists." checked={form.autoTradeListedOptions} onChange={(e) => setForm((current) => ({ ...current, autoTradeListedOptions: e.target.checked }))} />
        <SelectField label="Order type" hint="Limit routing is safer for unattended same-session entries." value={form.orderType} onChange={(e) => setForm((current) => ({ ...current, orderType: e.target.value }))}>
          {ORDER_TYPE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </SelectField>
        <SelectField label="Time in force" hint="Session-flex equity automation uses DAY_EXT; listed options stay on DAY." value={form.timeInForce} onChange={(e) => setForm((current) => ({ ...current, timeInForce: e.target.value }))}>
          {TIME_IN_FORCE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </SelectField>
        <TextField label="Max open positions" type="number" min="1" max="30" step="1" value={form.maxOpenPositions} onChange={(e) => setForm((current) => ({ ...current, maxOpenPositions: e.target.value }))} />
        <TextField label="Max notional / trade" type="number" min="100" step="100" value={form.maxNotionalPerTrade} onChange={(e) => setForm((current) => ({ ...current, maxNotionalPerTrade: e.target.value }))} />
        <ToggleField label="Regular hours only" hint="Explicit opt-in for core-session-only equity entries. Listed options remain regular-session only regardless of this toggle." checked={form.regularHoursOnly} onChange={(e) => setForm((current) => ({ ...current, regularHoursOnly: e.target.checked }))} />
        <ToggleField label="Auto sync working orders" checked={form.autoSyncOrders} onChange={(e) => setForm((current) => ({ ...current, autoSyncOrders: e.target.checked }))} />
        <ToggleField label="Auto manage open positions" hint="React to monitored stop and exit signals before the close-cleanup window." checked={form.autoManagePositions} onChange={(e) => setForm((current) => ({ ...current, autoManagePositions: e.target.checked }))} />
        <ToggleField label="Auto flatten before close" checked={form.autoFlattenBeforeClose} onChange={(e) => setForm((current) => ({ ...current, autoFlattenBeforeClose: e.target.checked }))} />
        <TextField label="Flatten before close (minutes)" type="number" min="1" max="90" step="1" value={form.flattenBeforeCloseMinutes} onChange={(e) => setForm((current) => ({ ...current, flattenBeforeCloseMinutes: e.target.value }))} />
        <ToggleField label="Long only equities" hint="This still blocks bearish equity entries. Listed-option puts remain allowed because they are long premium positions." checked={form.longOnly} onChange={(e) => setForm((current) => ({ ...current, longOnly: e.target.checked }))} />
        <ToggleField label="Fractional shares only" checked={form.fractionalSharesOnly} onChange={(e) => setForm((current) => ({ ...current, fractionalSharesOnly: e.target.checked }))} />
        <ToggleField label="Fast model" checked={form.useFastModel} onChange={(e) => setForm((current) => ({ ...current, useFastModel: e.target.checked }))} />
        <ToggleField label="State control" hint="Score live adaptation health and move the profile through healthy, watch, de-risk, and halt states." checked={form.stateControlEnabled} onChange={(e) => setForm((current) => ({ ...current, stateControlEnabled: e.target.checked }))} />
        <ToggleField label="State auto throttle" hint="Apply runtime-only risk overlays while watch or de-risk is active." checked={form.stateControlAutoThrottleEnabled} onChange={(e) => setForm((current) => ({ ...current, stateControlAutoThrottleEnabled: e.target.checked }))} />
        <ToggleField label="State auto halt" hint="Allow hard faults to set the safety lock. Clearing still requires operator action." checked={form.stateControlAutoHaltEnabled} onChange={(e) => setForm((current) => ({ ...current, stateControlAutoHaltEnabled: e.target.checked }))} />
        <TextField label="Watch score" type="number" min="1" max="100" step="1" value={form.stateControlWatchScore} onChange={(e) => setForm((current) => ({ ...current, stateControlWatchScore: e.target.value }))} />
        <TextField label="De-risk score" type="number" min="1" max="100" step="1" value={form.stateControlDeriskScore} onChange={(e) => setForm((current) => ({ ...current, stateControlDeriskScore: e.target.value }))} />
        <TextField label="Halt score" type="number" min="0" max="99" step="1" value={form.stateControlHaltScore} onChange={(e) => setForm((current) => ({ ...current, stateControlHaltScore: e.target.value }))} />
        <TextField label="Recovery cycles" hint="Clean evaluations required before watch or de-risk can recover automatically." type="number" min="1" max="20" step="1" value={form.stateControlRecoveryCycles} onChange={(e) => setForm((current) => ({ ...current, stateControlRecoveryCycles: e.target.value }))} />
        <ToggleField label="Paper canary" hint="Aggregate daily AI, state-control, shadow, order, PnL, slippage, and Notes evidence for paper promotion readiness." checked={form.paperCanaryEnabled} onChange={(e) => setForm((current) => ({ ...current, paperCanaryEnabled: e.target.checked }))} />
        <ToggleField label="Scheduled canary" hint="Run the paper canary automatically once per New York trading day after the close buffer." checked={form.paperCanaryAutoReviewEnabled} onChange={(e) => setForm((current) => ({ ...current, paperCanaryAutoReviewEnabled: e.target.checked }))} />
        <TextField label="Canary window" hint="Recent New York trading sessions included in promotion readiness." type="number" min="1" max="20" step="1" value={form.paperCanaryWindowSessions} onChange={(e) => setForm((current) => ({ ...current, paperCanaryWindowSessions: e.target.value }))} />
        <TextField label="Clean sessions required" hint="Clean paper sessions required before the canary can say ready to consider live pilot." type="number" min="1" max="20" step="1" value={form.paperCanaryRequiredCleanSessions} onChange={(e) => setForm((current) => ({ ...current, paperCanaryRequiredCleanSessions: e.target.value }))} />
        <ToggleField label="Lifecycle canary" hint="Require multi-session paper order lifecycle evidence before paper promotion readiness." checked={form.paperOrderLifecycleCanaryEnabled} onChange={(e) => setForm((current) => ({ ...current, paperOrderLifecycleCanaryEnabled: e.target.checked }))} />
        <ToggleField label="Lifecycle auto submit" hint="Off by default. When explicitly enabled, only personal_paper can submit one tiny guarded paper-only soak per session." checked={form.paperOrderLifecycleAutoSubmitEnabled} onChange={(e) => setForm((current) => ({ ...current, paperOrderLifecycleAutoSubmitEnabled: e.target.checked }))} />
        <TextField label="Lifecycle window" hint="Recent New York sessions included in the lifecycle canary." type="number" min="1" max="20" step="1" value={form.paperOrderLifecycleWindowSessions} onChange={(e) => setForm((current) => ({ ...current, paperOrderLifecycleWindowSessions: e.target.value }))} />
        <TextField label="Lifecycle clean sessions" hint="Clean paper lifecycle sessions required before paper canary readiness can pass." type="number" min="1" max="20" step="1" value={form.paperOrderLifecycleRequiredCleanSessions} onChange={(e) => setForm((current) => ({ ...current, paperOrderLifecycleRequiredCleanSessions: e.target.value }))} />
        <ToggleField label="Tiny live soak" hint="Off by default. Enables only the two-step manual prepare/run live pilot proof path." checked={form.livePilotSoakEnabled} onChange={(e) => setForm((current) => ({ ...current, livePilotSoakEnabled: e.target.checked }))} />
        <TextField label="Live soak symbol" hint="Ticker used for the tiny manual live pilot order." value={form.livePilotSymbol} onChange={(e) => setForm((current) => ({ ...current, livePilotSymbol: e.target.value.toUpperCase() }))} />
        <TextField label="Live soak cap" hint="Hard notional cap. The backend also caps this at $10." type="number" min="1" max="10" step="1" value={form.livePilotMaxNotional} onChange={(e) => setForm((current) => ({ ...current, livePilotMaxNotional: e.target.value }))} />
        <TextField label="Live approval TTL" hint="Minutes that a manual live pilot approval remains usable." type="number" min="1" max="60" step="1" value={form.livePilotApprovalTtlMinutes} onChange={(e) => setForm((current) => ({ ...current, livePilotApprovalTtlMinutes: e.target.value }))} />
        <TextField label="Live cancel timeout" hint="Seconds recorded for the tiny live soak cancel confirmation window." type="number" min="5" max="120" step="5" value={form.livePilotCancelTimeoutSeconds} onChange={(e) => setForm((current) => ({ ...current, livePilotCancelTimeoutSeconds: e.target.value }))} />
        <ToggleField label="Live canary" hint="Aggregate repeated manual tiny live soak evidence before any live expansion." checked={form.livePilotCanaryEnabled} onChange={(e) => setForm((current) => ({ ...current, livePilotCanaryEnabled: e.target.checked }))} />
        <ToggleField label="Scheduled live canary" hint="Review tiny live soak evidence once per New York trading day after the close buffer. It never submits or cancels orders." checked={form.livePilotCanaryAutoReviewEnabled} onChange={(e) => setForm((current) => ({ ...current, livePilotCanaryAutoReviewEnabled: e.target.checked }))} />
        <TextField label="Live canary window" hint="Recent New York sessions included in tiny live pilot readiness." type="number" min="1" max="20" step="1" value={form.livePilotCanaryWindowSessions} onChange={(e) => setForm((current) => ({ ...current, livePilotCanaryWindowSessions: e.target.value }))} />
        <TextField label="Live canary clean sessions" hint="Clean tiny live sessions required before the canary can say ready." type="number" min="1" max="20" step="1" value={form.livePilotCanaryRequiredCleanSessions} onChange={(e) => setForm((current) => ({ ...current, livePilotCanaryRequiredCleanSessions: e.target.value }))} />
        <ToggleField label="Live expansion" hint="Off by default. Enables only the two-step operator-approved single-order live pilot expansion." checked={form.livePilotExpansionEnabled} onChange={(e) => setForm((current) => ({ ...current, livePilotExpansionEnabled: e.target.checked }))} />
        <TextField label="Expansion cap" hint="Hard notional cap. The backend also caps this at $25." type="number" min="1" max="25" step="1" value={form.livePilotExpansionMaxNotional} onChange={(e) => setForm((current) => ({ ...current, livePilotExpansionMaxNotional: e.target.value }))} />
        <TextField label="Expansion daily orders" hint="Maximum approved expansion orders per New York session." type="number" min="1" max="3" step="1" value={form.livePilotExpansionMaxDailyOrders} onChange={(e) => setForm((current) => ({ ...current, livePilotExpansionMaxDailyOrders: e.target.value }))} />
        <TextField label="Expansion approval TTL" hint="Minutes that an operator approval remains usable." type="number" min="1" max="30" step="1" value={form.livePilotExpansionApprovalTtlMinutes} onChange={(e) => setForm((current) => ({ ...current, livePilotExpansionApprovalTtlMinutes: e.target.value }))} />
        <ToggleField label="Expansion canary" hint="Aggregate repeated operator-approved live expansion evidence before any supervised live window." checked={form.livePilotExpansionCanaryEnabled} onChange={(e) => setForm((current) => ({ ...current, livePilotExpansionCanaryEnabled: e.target.checked }))} />
        <ToggleField label="Scheduled expansion canary" hint="Review live expansion evidence once per New York trading day after the close buffer. It never submits or cancels orders." checked={form.livePilotExpansionCanaryAutoReviewEnabled} onChange={(e) => setForm((current) => ({ ...current, livePilotExpansionCanaryAutoReviewEnabled: e.target.checked }))} />
        <TextField label="Expansion canary window" hint="Recent New York sessions included in live expansion readiness." type="number" min="1" max="20" step="1" value={form.livePilotExpansionCanaryWindowSessions} onChange={(e) => setForm((current) => ({ ...current, livePilotExpansionCanaryWindowSessions: e.target.value }))} />
        <TextField label="Expansion canary clean sessions" hint="Clean capped live expansion sessions required before the canary can say ready." type="number" min="1" max="20" step="1" value={form.livePilotExpansionCanaryRequiredCleanSessions} onChange={(e) => setForm((current) => ({ ...current, livePilotExpansionCanaryRequiredCleanSessions: e.target.value }))} />
        <ToggleField label="Supervised live pilot" hint="Off by default. Enables only the operator-approved one-trade live pilot window." checked={form.livePilotWindowEnabled} onChange={(e) => setForm((current) => ({ ...current, livePilotWindowEnabled: e.target.checked }))} />
        <TextField label="Pilot window cap" hint="Hard notional cap. The backend also caps this at $50." type="number" min="1" max="50" step="1" value={form.livePilotWindowMaxNotional} onChange={(e) => setForm((current) => ({ ...current, livePilotWindowMaxNotional: e.target.value }))} />
        <TextField label="Pilot session orders" hint="V1 is hard capped to one order per New York session." type="number" min="1" max="1" step="1" value={form.livePilotWindowMaxSessionOrders} onChange={(e) => setForm((current) => ({ ...current, livePilotWindowMaxSessionOrders: e.target.value }))} />
        <TextField label="Pilot approval TTL" hint="Minutes that an operator approval remains usable for entry." type="number" min="1" max="30" step="1" value={form.livePilotWindowApprovalTtlMinutes} onChange={(e) => setForm((current) => ({ ...current, livePilotWindowApprovalTtlMinutes: e.target.value }))} />
        <TextField label="Pilot window minutes" hint="Operator-managed window duration recorded in the approval." type="number" min="5" max="240" step="5" value={form.livePilotWindowDurationMinutes} onChange={(e) => setForm((current) => ({ ...current, livePilotWindowDurationMinutes: e.target.value }))} />
        <ToggleField label="Supervised canary" hint="Aggregate repeated one-trade supervised live pilot windows before any broader live rollout review." checked={form.livePilotWindowCanaryEnabled} onChange={(e) => setForm((current) => ({ ...current, livePilotWindowCanaryEnabled: e.target.checked }))} />
        <ToggleField label="Scheduled supervised canary" hint="Review supervised live pilot window evidence once per New York trading day after the close buffer. It never places, cancels, or closes orders." checked={form.livePilotWindowCanaryAutoReviewEnabled} onChange={(e) => setForm((current) => ({ ...current, livePilotWindowCanaryAutoReviewEnabled: e.target.checked }))} />
        <TextField label="Supervised canary window" hint="Recent New York sessions included in supervised live pilot readiness." type="number" min="1" max="20" step="1" value={form.livePilotWindowCanaryWindowSessions} onChange={(e) => setForm((current) => ({ ...current, livePilotWindowCanaryWindowSessions: e.target.value }))} />
        <TextField label="Supervised clean sessions" hint="Clean supervised live pilot sessions required before the canary can say ready." type="number" min="1" max="20" step="1" value={form.livePilotWindowCanaryRequiredCleanSessions} onChange={(e) => setForm((current) => ({ ...current, livePilotWindowCanaryRequiredCleanSessions: e.target.value }))} />
        <ToggleField label="Promotion report" hint="Aggregate the full paper-to-supervised-live evidence ladder before requesting limited-live rollout approval." checked={form.livePilotPromotionReportEnabled} onChange={(e) => setForm((current) => ({ ...current, livePilotPromotionReportEnabled: e.target.checked }))} />
        <ToggleField label="Scheduled promotion report" hint="Review promotion evidence once per New York trading day after the close buffer. It never places, cancels, closes, enables, arms, or changes live gates." checked={form.livePilotPromotionReportAutoReviewEnabled} onChange={(e) => setForm((current) => ({ ...current, livePilotPromotionReportAutoReviewEnabled: e.target.checked }))} />
        <TextField label="Promotion clean sessions" hint="Clean supervised live pilot sessions required before the promotion report can say ready." type="number" min="1" max="20" step="1" value={form.livePilotPromotionRequiredWindowCleanSessions} onChange={(e) => setForm((current) => ({ ...current, livePilotPromotionRequiredWindowCleanSessions: e.target.value }))} />
        <TextField label="Promotion stale days" hint="Evidence older than this is blocked until refreshed." type="number" min="1" max="30" step="1" value={form.livePilotPromotionStaleAfterDays} onChange={(e) => setForm((current) => ({ ...current, livePilotPromotionStaleAfterDays: e.target.value }))} />
        <ToggleField label="Limited-live rollout" hint="Allow operator-approved runtime-only Alpaca live routing after the promotion report is ready." checked={form.limitedLiveRolloutEnabled} onChange={(e) => setForm((current) => ({ ...current, limitedLiveRolloutEnabled: e.target.checked }))} />
        <TextField label="Limited-live cap" hint="Maximum notional per live entry while the runtime allowance is active." type="number" min="1" max="100" step="1" value={form.limitedLiveRolloutMaxNotional} onChange={(e) => setForm((current) => ({ ...current, limitedLiveRolloutMaxNotional: e.target.value }))} />
        <TextField label="Limited-live orders" hint="Maximum live entries in the active New York session. V1 is capped to one." type="number" min="1" max="1" step="1" value={form.limitedLiveRolloutMaxSessionOrders} onChange={(e) => setForm((current) => ({ ...current, limitedLiveRolloutMaxSessionOrders: e.target.value }))} />
        <TextField label="Limited-live minutes" hint="Runtime allowance duration after manual activation." type="number" min="5" max="240" step="5" value={form.limitedLiveRolloutDurationMinutes} onChange={(e) => setForm((current) => ({ ...current, limitedLiveRolloutDurationMinutes: e.target.value }))} />
        <TextField label="Limited-live approval TTL" hint="Minutes a prepared approval remains valid before activation." type="number" min="1" max="30" step="1" value={form.limitedLiveRolloutApprovalTtlMinutes} onChange={(e) => setForm((current) => ({ ...current, limitedLiveRolloutApprovalTtlMinutes: e.target.value }))} />
        <ToggleField label="Limited-live canary" hint="Review repeated limited-live rollout evidence before any cap expansion." checked={form.limitedLiveRolloutCanaryEnabled} onChange={(e) => setForm((current) => ({ ...current, limitedLiveRolloutCanaryEnabled: e.target.checked }))} />
        <ToggleField label="Scheduled limited-live canary" hint="Review limited-live rollout evidence once per New York trading day after the close buffer. It never places, cancels, or closes orders." checked={form.limitedLiveRolloutCanaryAutoReviewEnabled} onChange={(e) => setForm((current) => ({ ...current, limitedLiveRolloutCanaryAutoReviewEnabled: e.target.checked }))} />
        <TextField label="Limited-live canary window" hint="Recent New York sessions included in limited-live rollout readiness." type="number" min="1" max="20" step="1" value={form.limitedLiveRolloutCanaryWindowSessions} onChange={(e) => setForm((current) => ({ ...current, limitedLiveRolloutCanaryWindowSessions: e.target.value }))} />
        <TextField label="Limited-live clean sessions" hint="Clean limited-live rollout sessions required before operator review." type="number" min="1" max="20" step="1" value={form.limitedLiveRolloutCanaryRequiredCleanSessions} onChange={(e) => setForm((current) => ({ ...current, limitedLiveRolloutCanaryRequiredCleanSessions: e.target.value }))} />
        <TextField label="Limited-live stale days" hint="Days before promotion evidence becomes stale for the canary." type="number" min="1" max="30" step="1" value={form.limitedLiveRolloutCanaryStaleAfterDays} onChange={(e) => setForm((current) => ({ ...current, limitedLiveRolloutCanaryStaleAfterDays: e.target.value }))} />
        <ToggleField label="Cap expansion report" hint="Review limited-live canary evidence before requesting a larger cap. Advisory only." checked={form.limitedLiveCapExpansionReportEnabled} onChange={(e) => setForm((current) => ({ ...current, limitedLiveCapExpansionReportEnabled: e.target.checked }))} />
        <ToggleField label="Scheduled cap report" hint="Review cap-expansion readiness once per New York trading day after close. It never changes caps." checked={form.limitedLiveCapExpansionReportAutoReviewEnabled} onChange={(e) => setForm((current) => ({ ...current, limitedLiveCapExpansionReportAutoReviewEnabled: e.target.checked }))} />
        <TextField label="Cap report clean sessions" hint="Clean limited-live rollout sessions required before the report can recommend an expansion request." type="number" min="1" max="20" step="1" value={form.limitedLiveCapExpansionRequiredCleanSessions} onChange={(e) => setForm((current) => ({ ...current, limitedLiveCapExpansionRequiredCleanSessions: e.target.value }))} />
        <TextField label="Cap report stale days" hint="Days before cap-expansion evidence becomes stale." type="number" min="1" max="30" step="1" value={form.limitedLiveCapExpansionStaleAfterDays} onChange={(e) => setForm((current) => ({ ...current, limitedLiveCapExpansionStaleAfterDays: e.target.value }))} />
        <TextField label="Target limited-live cap" hint="Advisory target for the next separate operator-approved cap expansion gate." type="number" min="1" max="5000" step="1" value={form.limitedLiveCapExpansionTargetMaxNotional} onChange={(e) => setForm((current) => ({ ...current, limitedLiveCapExpansionTargetMaxNotional: e.target.value }))} />
        <ToggleField label="Cap expansion gate" hint="Allow a manual runtime-only expanded cap after the cap report is ready and base limited-live rollout is active." checked={form.limitedLiveCapExpansionEnabled} onChange={(e) => setForm((current) => ({ ...current, limitedLiveCapExpansionEnabled: e.target.checked }))} />
        <TextField label="Expanded cap" hint="Maximum notional per live entry while the cap-expansion allowance is active. V1 clamps at $250." type="number" min="1" max="250" step="1" value={form.limitedLiveCapExpansionMaxNotional} onChange={(e) => setForm((current) => ({ ...current, limitedLiveCapExpansionMaxNotional: e.target.value }))} />
        <TextField label="Expansion orders" hint="Maximum expanded-cap live entries in the active New York session. V1 is capped to one." type="number" min="1" max="1" step="1" value={form.limitedLiveCapExpansionMaxSessionOrders} onChange={(e) => setForm((current) => ({ ...current, limitedLiveCapExpansionMaxSessionOrders: e.target.value }))} />
        <TextField label="Expansion minutes" hint="Runtime expanded-cap allowance duration after manual activation." type="number" min="5" max="240" step="5" value={form.limitedLiveCapExpansionDurationMinutes} onChange={(e) => setForm((current) => ({ ...current, limitedLiveCapExpansionDurationMinutes: e.target.value }))} />
        <TextField label="Expansion approval TTL" hint="Minutes a prepared cap-expansion approval remains valid before activation." type="number" min="1" max="30" step="1" value={form.limitedLiveCapExpansionApprovalTtlMinutes} onChange={(e) => setForm((current) => ({ ...current, limitedLiveCapExpansionApprovalTtlMinutes: e.target.value }))} />
        <ToggleField label="Expanded-cap canary" hint="Review repeated limited-live cap expansion sessions before any larger cap recommendation." checked={form.limitedLiveCapExpansionCanaryEnabled} onChange={(e) => setForm((current) => ({ ...current, limitedLiveCapExpansionCanaryEnabled: e.target.checked }))} />
        <ToggleField label="Scheduled expanded-cap canary" hint="Review expanded-cap evidence once per New York trading day after close. It never places, cancels, closes, or changes caps." checked={form.limitedLiveCapExpansionCanaryAutoReviewEnabled} onChange={(e) => setForm((current) => ({ ...current, limitedLiveCapExpansionCanaryAutoReviewEnabled: e.target.checked }))} />
        <TextField label="Expanded-cap canary window" hint="Recent New York sessions included in expanded-cap readiness." type="number" min="1" max="20" step="1" value={form.limitedLiveCapExpansionCanaryWindowSessions} onChange={(e) => setForm((current) => ({ ...current, limitedLiveCapExpansionCanaryWindowSessions: e.target.value }))} />
        <TextField label="Expanded-cap clean sessions" hint="Clean expanded-cap sessions required before operator review." type="number" min="1" max="20" step="1" value={form.limitedLiveCapExpansionCanaryRequiredCleanSessions} onChange={(e) => setForm((current) => ({ ...current, limitedLiveCapExpansionCanaryRequiredCleanSessions: e.target.value }))} />
        <TextField label="Expanded-cap stale days" hint="Days before cap-expansion report evidence becomes stale for the canary." type="number" min="1" max="30" step="1" value={form.limitedLiveCapExpansionCanaryStaleAfterDays} onChange={(e) => setForm((current) => ({ ...current, limitedLiveCapExpansionCanaryStaleAfterDays: e.target.value }))} />
        <ToggleField label="Next-tier cap report" hint="Review expanded-cap canary evidence before requesting a larger limited-live cap. Advisory only." checked={form.limitedLiveNextTierCapReportEnabled} onChange={(e) => setForm((current) => ({ ...current, limitedLiveNextTierCapReportEnabled: e.target.checked }))} />
        <ToggleField label="Scheduled next-tier cap report" hint="Review next-tier cap readiness once per New York trading day after close. It never changes caps." checked={form.limitedLiveNextTierCapReportAutoReviewEnabled} onChange={(e) => setForm((current) => ({ ...current, limitedLiveNextTierCapReportAutoReviewEnabled: e.target.checked }))} />
        <TextField label="Next-tier clean sessions" hint="Clean expanded-cap sessions required before the report can recommend a larger cap request." type="number" min="1" max="20" step="1" value={form.limitedLiveNextTierCapRequiredCleanSessions} onChange={(e) => setForm((current) => ({ ...current, limitedLiveNextTierCapRequiredCleanSessions: e.target.value }))} />
        <TextField label="Next-tier stale days" hint="Days before expanded-cap evidence becomes stale for the report." type="number" min="1" max="30" step="1" value={form.limitedLiveNextTierCapStaleAfterDays} onChange={(e) => setForm((current) => ({ ...current, limitedLiveNextTierCapStaleAfterDays: e.target.value }))} />
        <TextField label="Target next-tier cap" hint="Advisory target for the next separate operator-approved cap gate." type="number" min="1" max="10000" step="1" value={form.limitedLiveNextTierCapTargetMaxNotional} onChange={(e) => setForm((current) => ({ ...current, limitedLiveNextTierCapTargetMaxNotional: e.target.value }))} />
        <ToggleField label="Next-tier cap gate" hint="Allow a manual, runtime-only gate to temporarily authorize the $500 cap. Disabled by default." checked={form.limitedLiveNextTierCapEnabled} onChange={(e) => setForm((current) => ({ ...current, limitedLiveNextTierCapEnabled: e.target.checked }))} />
        <TextField label="Next-tier gate cap" hint="Hard cap for the runtime next-tier allowance." type="number" min="1" max="500" step="1" value={form.limitedLiveNextTierCapMaxNotional} onChange={(e) => setForm((current) => ({ ...current, limitedLiveNextTierCapMaxNotional: e.target.value }))} />
        <TextField label="Next-tier gate minutes" hint="How long an activated next-tier allowance can remain valid." type="number" min="5" max="240" step="1" value={form.limitedLiveNextTierCapDurationMinutes} onChange={(e) => setForm((current) => ({ ...current, limitedLiveNextTierCapDurationMinutes: e.target.value }))} />
        <TextField label="Next-tier approval TTL" hint="Minutes a prepared approval stays valid before activation." type="number" min="1" max="30" step="1" value={form.limitedLiveNextTierCapApprovalTtlMinutes} onChange={(e) => setForm((current) => ({ ...current, limitedLiveNextTierCapApprovalTtlMinutes: e.target.value }))} />
        <TextField label="Next-tier session orders" hint="Maximum higher-cap orders per New York session. V1 remains one." type="number" min="1" max="1" step="1" value={form.limitedLiveNextTierCapMaxSessionOrders} onChange={(e) => setForm((current) => ({ ...current, limitedLiveNextTierCapMaxSessionOrders: e.target.value }))} />
        <ToggleField label="Daily AI notes" hint="Write structured good and bad automation observations into Notes each trading day." checked={form.aiDailyReviewEnabled} onChange={(e) => setForm((current) => ({ ...current, aiDailyReviewEnabled: e.target.checked }))} />
        <ToggleField label="AI auto adjust" hint="Allow the local optimizer to apply bounded setting changes after the post-close review." checked={form.aiAutoAdjustEnabled} onChange={(e) => setForm((current) => ({ ...current, aiAutoAdjustEnabled: e.target.checked }))} />
        <ToggleField label="AI adjust live" hint="Live profiles still obey the live readiness gate and safety locks." checked={form.aiAdjustLiveEnabled} onChange={(e) => setForm((current) => ({ ...current, aiAdjustLiveEnabled: e.target.checked }))} />
        <TextField label="AI min closes" hint="Minimum closed automation trades before non-safety tuning can expand risk or capacity." type="number" min="0" max="100" step="1" value={form.aiReviewMinTrades} onChange={(e) => setForm((current) => ({ ...current, aiReviewMinTrades: e.target.value }))} />
        <TextField label="AI max changes / day" type="number" min="0" max="12" step="1" value={form.aiMaxDailySettingChanges} onChange={(e) => setForm((current) => ({ ...current, aiMaxDailySettingChanges: e.target.value }))} />
        <TextField label="AI max step %" type="number" min="1" max="50" step="1" value={form.aiMaxStepPct} onChange={(e) => setForm((current) => ({ ...current, aiMaxStepPct: e.target.value }))} />
        <TextField label="Cycle entry rank limit" hint="Only the highest-ranked candidates per cycle are allowed to route automatically." type="number" min="1" max="10" step="1" value={form.cycleEntryRankLimit} onChange={(e) => setForm((current) => ({ ...current, cycleEntryRankLimit: e.target.value }))} />
        <TextField label="Max gross leverage" hint="Gross exposure cap across open and working automation-owned positions." type="number" min="0.1" max="10" step="0.1" value={form.maxGrossLeverage} onChange={(e) => setForm((current) => ({ ...current, maxGrossLeverage: e.target.value }))} />
        <TextField label="Max single position %" hint="Cap each routed position as a percent of current equity." type="number" min="1" max="100" step="0.5" value={form.maxSinglePositionPct} onChange={(e) => setForm((current) => ({ ...current, maxSinglePositionPct: e.target.value }))} />
        <TextField label="Max bucket %" hint="Cap proxy-correlation bucket exposure as a percent of current equity." type="number" min="1" max="100" step="0.5" value={form.maxCorrelatedBucketPct} onChange={(e) => setForm((current) => ({ ...current, maxCorrelatedBucketPct: e.target.value }))} />
        <TextField label="Min edge/cost ratio" hint="Candidates below this edge-to-cost floor are blocked before routing." type="number" min="0" max="25" step="0.1" value={form.minEdgeToCostRatio} onChange={(e) => setForm((current) => ({ ...current, minEdgeToCostRatio: e.target.value }))} />
        <ToggleField label="Allow pyramiding" hint="Only add once to winners; averaging down stays blocked." checked={form.allowPyramiding} onChange={(e) => setForm((current) => ({ ...current, allowPyramiding: e.target.checked }))} />
        <ToggleField label="Require liquidity fields" hint="Block candidates when spread or liquidity telemetry is missing." checked={form.requireLiquidityFields} onChange={(e) => setForm((current) => ({ ...current, requireLiquidityFields: e.target.checked }))} />
        <TextField label="Max total open notional" hint="Stop adding unattended exposure once automation-owned open and working notional reaches this cap." type="number" min="100" step="100" value={form.maxTotalOpenNotional} onChange={(e) => setForm((current) => ({ ...current, maxTotalOpenNotional: e.target.value }))} />
        <TextField label="Max daily loss (R)" hint="Automation stands down once same-day automation realized PnL breaches this many risk units." type="number" min="0.25" max="25" step="0.25" value={form.maxDailyLossR} onChange={(e) => setForm((current) => ({ ...current, maxDailyLossR: e.target.value }))} />
        <TextField label="Max consecutive losses" type="number" min="1" max="25" step="1" value={form.maxConsecutiveLosses} onChange={(e) => setForm((current) => ({ ...current, maxConsecutiveLosses: e.target.value }))} />
        <TextField label="Max entries / day" type="number" min="1" max="100" step="1" value={form.maxDailyEntries} onChange={(e) => setForm((current) => ({ ...current, maxDailyEntries: e.target.value }))} />
        <TextField label="Max entries / symbol / day" type="number" min="1" max="25" step="1" value={form.maxDailyEntriesPerSymbol} onChange={(e) => setForm((current) => ({ ...current, maxDailyEntriesPerSymbol: e.target.value }))} />
        <TextField label="Max consecutive cycle errors" hint="Auto-stop unattended trading if the worker hits this many cycle failures in a row." type="number" min="1" max="25" step="1" value={form.maxErrorStreak} onChange={(e) => setForm((current) => ({ ...current, maxErrorStreak: e.target.value }))} />
      </div>

      <ActionBar className="settings-action-bar">
        <Button type="button" variant="ghost" onClick={() => applyPreset('prep')} disabled={busy || Boolean(actionBusyKey)}>
          Prep profile
        </Button>
        <Button type="button" variant="ghost" onClick={() => applyPreset('paper')} disabled={busy || Boolean(actionBusyKey)}>
          Paper autopilot
        </Button>
        <Button type="button" variant="ghost" onClick={() => applyPreset('pre_market')} disabled={busy || Boolean(actionBusyKey)}>
          Pre-market mode
        </Button>
        <Button type="button" variant="ghost" onClick={() => applyPreset('after_hours')} disabled={busy || Boolean(actionBusyKey)}>
          After-hours mode
        </Button>
        <Button type="button" variant="ghost" onClick={() => applyPreset('pilot')} disabled={busy || Boolean(actionBusyKey) || Boolean(collectionPhase?.active)}>
          Live pilot
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('reset_from_template')} disabled={busy || Boolean(actionBusyKey)}>
          {actionBusyKey === 'reset_from_template' ? 'Resetting...' : 'Reset from template'}
        </Button>
      </ActionBar>

      <ActionBar className="settings-action-bar">
        <Button type="button" variant="solid" onClick={saveSettings} disabled={busy}>
          {busy ? 'Saving...' : 'Save automation'}
        </Button>
        <Button type="button" variant="ghost" onClick={loadSnapshot} disabled={busy || Boolean(actionBusyKey)}>
          Refresh
        </Button>
      </ActionBar>

      <ActionBar className="settings-action-bar">
        <Button type="button" variant="solid" onClick={() => runAction('arm')} disabled={!availableActions.can_arm || Boolean(actionBusyKey)}>
          {actionBusyKey === 'arm' ? 'Arming...' : 'Arm'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('disarm')} disabled={!availableActions.can_disarm || Boolean(actionBusyKey)}>
          {actionBusyKey === 'disarm' ? 'Disarming...' : 'Disarm'}
        </Button>
        <Button type="button" variant="subtle" onClick={() => runAction('kill_switch')} disabled={!availableActions.can_kill || Boolean(actionBusyKey)}>
          {actionBusyKey === 'kill_switch' ? 'Stopping...' : 'Kill switch'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('clear_kill_switch')} disabled={!availableActions.can_clear_kill || Boolean(actionBusyKey)}>
          {actionBusyKey === 'clear_kill_switch' ? 'Clearing...' : 'Clear kill'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_cycle')} disabled={!availableActions.can_run_cycle || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_cycle' ? 'Running...' : 'Run now'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_state_control_review')} disabled={!availableActions.can_run_state_control_review || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_state_control_review' ? 'Reviewing...' : 'Run state review'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_state_control_shadow_validation')} disabled={!availableActions.can_run_state_control_shadow_validation || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_state_control_shadow_validation' ? 'Validating...' : 'Run shadow validation'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_paper_broker_reconciliation')} disabled={!availableActions.can_run_paper_broker_reconciliation || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_paper_broker_reconciliation' ? 'Reconciling...' : 'Run broker reconciliation'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_paper_order_lifecycle_soak')} disabled={!availableActions.can_run_paper_order_lifecycle_soak || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_paper_order_lifecycle_soak' ? 'Soaking...' : 'Run order lifecycle soak'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_paper_order_lifecycle_canary_review')} disabled={!availableActions.can_run_paper_order_lifecycle_canary_review || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_paper_order_lifecycle_canary_review' ? 'Reviewing...' : 'Run lifecycle canary'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_paper_canary_review')} disabled={!availableActions.can_run_paper_canary_review || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_paper_canary_review' ? 'Reviewing...' : 'Run paper canary'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_live_pilot_readiness_review')} disabled={!availableActions.can_run_live_pilot_readiness_review || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_live_pilot_readiness_review' ? 'Reviewing...' : 'Run live readiness'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('prepare_live_pilot_soak')} disabled={!availableActions.can_prepare_live_pilot_soak || Boolean(actionBusyKey)}>
          {actionBusyKey === 'prepare_live_pilot_soak' ? 'Preparing...' : 'Prepare live pilot'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_live_pilot_soak')} disabled={!availableActions.can_run_live_pilot_soak || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_live_pilot_soak' ? 'Running...' : 'Run tiny live soak'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_live_pilot_canary_review')} disabled={!availableActions.can_run_live_pilot_canary_review || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_live_pilot_canary_review' ? 'Reviewing...' : 'Run live canary'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('prepare_live_pilot_expansion')} disabled={!availableActions.can_prepare_live_pilot_expansion || Boolean(actionBusyKey)}>
          {actionBusyKey === 'prepare_live_pilot_expansion' ? 'Preparing...' : 'Prepare live expansion'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_live_pilot_expansion')} disabled={!availableActions.can_run_live_pilot_expansion || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_live_pilot_expansion' ? 'Running...' : 'Run approved expansion'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_live_pilot_expansion_canary_review')} disabled={!availableActions.can_run_live_pilot_expansion_canary_review || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_live_pilot_expansion_canary_review' ? 'Reviewing...' : 'Run expansion canary'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('prepare_live_pilot_window')} disabled={!availableActions.can_prepare_live_pilot_window || Boolean(actionBusyKey)}>
          {actionBusyKey === 'prepare_live_pilot_window' ? 'Preparing...' : 'Prepare live window'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_live_pilot_window_entry')} disabled={!availableActions.can_run_live_pilot_window_entry || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_live_pilot_window_entry' ? 'Entering...' : 'Enter live window'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_live_pilot_window_exit')} disabled={!availableActions.can_run_live_pilot_window_exit || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_live_pilot_window_exit' ? 'Exiting...' : 'Exit live window'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_live_pilot_window_canary_review')} disabled={!availableActions.can_run_live_pilot_window_canary_review || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_live_pilot_window_canary_review' ? 'Reviewing...' : 'Run supervised canary'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_live_pilot_promotion_report')} disabled={!availableActions.can_run_live_pilot_promotion_report || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_live_pilot_promotion_report' ? 'Reviewing...' : 'Run promotion report'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('prepare_limited_live_rollout')} disabled={!availableActions.can_prepare_limited_live_rollout || Boolean(actionBusyKey)}>
          {actionBusyKey === 'prepare_limited_live_rollout' ? 'Preparing...' : 'Prepare limited rollout'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('activate_limited_live_rollout')} disabled={!availableActions.can_activate_limited_live_rollout || Boolean(actionBusyKey)}>
          {actionBusyKey === 'activate_limited_live_rollout' ? 'Activating...' : 'Activate limited rollout'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('rollback_limited_live_rollout')} disabled={!availableActions.can_rollback_limited_live_rollout || Boolean(actionBusyKey)}>
          {actionBusyKey === 'rollback_limited_live_rollout' ? 'Rolling back...' : 'Rollback limited rollout'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_limited_live_rollout_canary_review')} disabled={!availableActions.can_run_limited_live_rollout_canary_review || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_limited_live_rollout_canary_review' ? 'Reviewing...' : 'Run limited-live canary'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_limited_live_cap_expansion_report')} disabled={!availableActions.can_run_limited_live_cap_expansion_report || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_limited_live_cap_expansion_report' ? 'Reviewing...' : 'Run cap expansion report'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('prepare_limited_live_cap_expansion')} disabled={!availableActions.can_prepare_limited_live_cap_expansion || Boolean(actionBusyKey)}>
          {actionBusyKey === 'prepare_limited_live_cap_expansion' ? 'Preparing...' : 'Prepare cap expansion'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('activate_limited_live_cap_expansion')} disabled={!availableActions.can_activate_limited_live_cap_expansion || Boolean(actionBusyKey)}>
          {actionBusyKey === 'activate_limited_live_cap_expansion' ? 'Activating...' : 'Activate cap expansion'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('rollback_limited_live_cap_expansion')} disabled={!availableActions.can_rollback_limited_live_cap_expansion || Boolean(actionBusyKey)}>
          {actionBusyKey === 'rollback_limited_live_cap_expansion' ? 'Rolling back...' : 'Rollback cap expansion'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_limited_live_cap_expansion_canary_review')} disabled={!availableActions.can_run_limited_live_cap_expansion_canary_review || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_limited_live_cap_expansion_canary_review' ? 'Reviewing...' : 'Run expanded-cap canary'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_limited_live_next_tier_cap_report')} disabled={!availableActions.can_run_limited_live_next_tier_cap_report || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_limited_live_next_tier_cap_report' ? 'Reviewing...' : 'Run next-tier cap report'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('prepare_limited_live_next_tier_cap')} disabled={!availableActions.can_prepare_limited_live_next_tier_cap || Boolean(actionBusyKey)}>
          {actionBusyKey === 'prepare_limited_live_next_tier_cap' ? 'Preparing...' : 'Prepare next-tier cap'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('activate_limited_live_next_tier_cap')} disabled={!availableActions.can_activate_limited_live_next_tier_cap || Boolean(actionBusyKey)}>
          {actionBusyKey === 'activate_limited_live_next_tier_cap' ? 'Activating...' : 'Activate next-tier cap'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('rollback_limited_live_next_tier_cap')} disabled={!availableActions.can_rollback_limited_live_next_tier_cap || Boolean(actionBusyKey)}>
          {actionBusyKey === 'rollback_limited_live_next_tier_cap' ? 'Rolling back...' : 'Rollback next-tier cap'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_limited_live_broker_reconciliation')} disabled={!availableActions.can_run_limited_live_broker_reconciliation || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_limited_live_broker_reconciliation' ? 'Reconciling...' : 'Run live reconciliation'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_limited_live_session_closeout')} disabled={!availableActions.can_run_limited_live_session_closeout || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_limited_live_session_closeout' ? 'Closing out...' : 'Run live closeout'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_limited_live_next_tier_cap_canary_review')} disabled={!availableActions.can_run_limited_live_next_tier_cap_canary_review || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_limited_live_next_tier_cap_canary_review' ? 'Reviewing...' : 'Run $500 canary'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('submit_limited_live_operator_checklist')} disabled={!availableActions.can_submit_limited_live_operator_checklist || Boolean(actionBusyKey)}>
          {actionBusyKey === 'submit_limited_live_operator_checklist' ? 'Submitting...' : 'Submit cap checklist'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_limited_live_higher_cap_report')} disabled={!availableActions.can_run_limited_live_higher_cap_report || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_limited_live_higher_cap_report' ? 'Reviewing...' : 'Run $1000 report'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_daily_objective_review')} disabled={!availableActions.can_run_daily_objective_review || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_daily_objective_review' ? 'Reviewing...' : 'Run objective review'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_accuracy_calibration_review')} disabled={!availableActions.can_run_accuracy_calibration_review || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_accuracy_calibration_review' ? 'Reviewing...' : 'Run accuracy review'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_paper_evidence_review')} disabled={!availableActions.can_run_paper_evidence_review || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_paper_evidence_review' ? 'Reviewing...' : 'Run paper evidence'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_replay_lab_review')} disabled={!availableActions.can_run_replay_lab_review || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_replay_lab_review' ? 'Replaying...' : 'Run replay lab'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_transaction_cost_calibration_review')} disabled={!availableActions.can_run_transaction_cost_calibration_review || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_transaction_cost_calibration_review' ? 'Reviewing...' : 'Run cost calibration'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_loss_containment_review')} disabled={!availableActions.can_run_loss_containment_review || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_loss_containment_review' ? 'Reviewing...' : 'Run loss review'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_exit_watchdog_review')} disabled={!availableActions.can_run_exit_watchdog_review || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_exit_watchdog_review' ? 'Reviewing...' : 'Run exit watchdog'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => runAction('run_ai_review')} disabled={!availableActions.can_run_ai_review || Boolean(actionBusyKey)}>
          {actionBusyKey === 'run_ai_review' ? 'Reviewing...' : 'Run AI review'}
        </Button>
      </ActionBar>

      {controlPlaneOverrideRows.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>State control overrides</caption>
            <thead>
              <tr>
                <th scope="col">Field</th>
                <th scope="col">Baseline</th>
                <th scope="col">Effective</th>
                <th scope="col">Reason</th>
              </tr>
            </thead>
            <tbody>
              {controlPlaneOverrideRows.slice(0, 10).map((item, index) => (
                <tr key={`${item.field || 'override'}:${index}`}>
                  <td>{String(item.field || '--').replace(/_/g, ' ')}</td>
                  <td>{String(item.before ?? '--')}</td>
                  <td>{String(item.effective ?? '--')}</td>
                  <td>{item.reason || 'State control applied a runtime-only overlay.'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {shadowValidationScenarios.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>State control shadow validation</caption>
            <thead>
              <tr>
                <th scope="col">Scenario</th>
                <th scope="col">Status</th>
                <th scope="col">State</th>
                <th scope="col">Score</th>
                <th scope="col">Expected overlays</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {shadowValidationScenarios.slice(0, 8).map((item, index) => (
                <tr key={`${item.id || 'scenario'}:${index}`}>
                  <td>{item.label || String(item.id || '--').replace(/_/g, ' ')}</td>
                  <td>{humanizeValue(item.status, '--')}</td>
                  <td>{humanizeValue(item.state, '--')}</td>
                  <td>{Number.isFinite(Number(item.score)) ? Number(item.score).toFixed(0) : '--'}</td>
                  <td>{Array.isArray(item.active_overrides) ? String(item.active_overrides.length) : '0'}</td>
                  <td>{item.detail || item.description || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {paperBrokerIssues.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Paper broker reconciliation</caption>
            <thead>
              <tr>
                <th scope="col">Severity</th>
                <th scope="col">Signal</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {paperBrokerIssues.slice(0, 8).map((item, index) => (
                <tr key={`${item.key || 'paper-broker'}:${index}`}>
                  <td>{humanizeValue(item.severity, '--')}</td>
                  <td>{String(item.key || 'reconciliation').replace(/_/g, ' ')}</td>
                  <td>{item.detail || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {paperOrderLifecycleIssues.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Paper order lifecycle soak</caption>
            <thead>
              <tr>
                <th scope="col">Severity</th>
                <th scope="col">Signal</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {paperOrderLifecycleIssues.slice(0, 8).map((item, index) => (
                <tr key={`${item.key || 'paper-order-lifecycle'}:${index}`}>
                  <td>{humanizeValue(item.severity, '--')}</td>
                  <td>{String(item.key || 'lifecycle').replace(/_/g, ' ')}</td>
                  <td>{item.detail || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {paperOrderLifecycleCanaryIssues.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Paper order lifecycle canary</caption>
            <thead>
              <tr>
                <th scope="col">Severity</th>
                <th scope="col">Session</th>
                <th scope="col">Signal</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {paperOrderLifecycleCanaryIssues.slice(0, 10).map((item, index) => (
                <tr key={`${item.key || 'paper-order-lifecycle-canary'}:${index}`}>
                  <td>{humanizeValue(item.severity, '--')}</td>
                  <td>{item.session_day || '--'}</td>
                  <td>{String(item.key || 'lifecycle_canary').replace(/_/g, ' ')}</td>
                  <td>{item.detail || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {paperOrderLifecycleCanarySessions.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Lifecycle canary sessions</caption>
            <thead>
              <tr>
                <th scope="col">Session</th>
                <th scope="col">Status</th>
                <th scope="col">Soak</th>
                <th scope="col">Terminal</th>
                <th scope="col">Broker order</th>
                <th scope="col">Reconcile</th>
                <th scope="col">Ledger open</th>
                <th scope="col">Blockers</th>
              </tr>
            </thead>
            <tbody>
              {paperOrderLifecycleCanarySessions.slice(0, 5).map((item, index) => {
                const soak = item.lifecycle_soak || {}
                const broker = item.paper_broker_reconciliation || {}
                const ledger = item.ledger || {}
                const blockers = Array.isArray(item.blockers) ? item.blockers : []
                return (
                  <tr key={`${item.session_day || 'lifecycle-session'}:${index}`}>
                    <td>{item.session_day || '--'}</td>
                    <td>{humanizeValue(item.status, '--')}</td>
                    <td>{humanizeValue(soak.status, 'Missing')}</td>
                    <td>{humanizeValue(soak.terminal_state, '--')}</td>
                    <td>{soak.broker_order_id ? String(soak.broker_order_id).slice(0, 12) : '--'}</td>
                    <td>{humanizeValue(broker.status, 'Missing')}</td>
                    <td>{String(ledger.unresolved_count ?? 0)}</td>
                    <td>{blockers.length ? blockers.map((blocker) => String(blocker.key || 'blocker').replace(/_/g, ' ')).join(', ') : 'None'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {paperCanarySessions.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Paper canary sessions</caption>
            <thead>
              <tr>
                <th scope="col">Session</th>
                <th scope="col">Status</th>
                <th scope="col">State</th>
                <th scope="col">Shadow</th>
                <th scope="col">Broker</th>
                <th scope="col">Lifecycle</th>
                <th scope="col">PnL</th>
                <th scope="col">Slippage</th>
                <th scope="col">Blockers</th>
              </tr>
            </thead>
            <tbody>
              {paperCanarySessions.slice(0, 5).map((item, index) => {
                const pnl = item.pnl || {}
                const slippage = item.slippage || {}
                const blockers = Array.isArray(item.blockers) ? item.blockers : []
                const stateControl = item.state_control || {}
                const shadow = item.shadow_validation || {}
                const broker = item.paper_broker_reconciliation || {}
                const lifecycle = item.paper_order_lifecycle_soak || {}
                return (
                  <tr key={`${item.session_day || 'session'}:${index}`}>
                    <td>{item.session_day || '--'}</td>
                    <td>{humanizeValue(item.status, '--')}</td>
                    <td>{humanizeValue(stateControl.state, 'Healthy')}</td>
                    <td>{humanizeValue(shadow.status, 'Missing')}</td>
                    <td>{humanizeValue(broker.status, 'Missing')}</td>
                    <td>{humanizeValue(lifecycle.status, 'Missing')}</td>
                    <td>{formatMoney(pnl.realized_pnl)}</td>
                    <td>{slippage.average_abs_bps == null ? '--' : `${Number(slippage.average_abs_bps).toFixed(1)} bps`}</td>
                    <td>{blockers.length ? blockers.map((blocker) => String(blocker.key || 'blocker').replace(/_/g, ' ')).join(', ') : 'None'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {livePilotReadinessIssues.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Live pilot readiness</caption>
            <thead>
              <tr>
                <th scope="col">Type</th>
                <th scope="col">Component</th>
                <th scope="col">Signal</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {livePilotReadinessIssues.slice(0, 12).map((item, index) => (
                <tr key={`${item.key || 'live-pilot-readiness'}:${index}`}>
                  <td>{humanizeValue(item.severity, '--')}</td>
                  <td>{humanizeValue(item.component, '--')}</td>
                  <td>{String(item.key || 'operator_action').replace(/_/g, ' ')}</td>
                  <td>{item.detail || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {livePilotSoakIssues.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Tiny live pilot soak</caption>
            <thead>
              <tr>
                <th scope="col">Type</th>
                <th scope="col">Component</th>
                <th scope="col">Signal</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {livePilotSoakIssues.slice(0, 12).map((item, index) => (
                <tr key={`${item.key || 'live-pilot-soak'}:${index}`}>
                  <td>{humanizeValue(item.severity, '--')}</td>
                  <td>{humanizeValue(item.component, '--')}</td>
                  <td>{String(item.key || 'live_pilot_soak').replace(/_/g, ' ')}</td>
                  <td>{item.detail || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {livePilotCanaryIssues.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Tiny live pilot canary</caption>
            <thead>
              <tr>
                <th scope="col">Type</th>
                <th scope="col">Session</th>
                <th scope="col">Signal</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {livePilotCanaryIssues.slice(0, 12).map((item, index) => (
                <tr key={`${item.key || 'live-pilot-canary'}:${index}`}>
                  <td>{humanizeValue(item.severity, '--')}</td>
                  <td>{item.session_day || '--'}</td>
                  <td>{String(item.key || 'live_pilot_canary').replace(/_/g, ' ')}</td>
                  <td>{item.detail || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {livePilotExpansionIssues.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Live pilot expansion</caption>
            <thead>
              <tr>
                <th scope="col">Type</th>
                <th scope="col">Component</th>
                <th scope="col">Signal</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {livePilotExpansionIssues.slice(0, 12).map((item, index) => (
                <tr key={`${item.key || 'live-pilot-expansion'}:${index}`}>
                  <td>{humanizeValue(item.severity, '--')}</td>
                  <td>{humanizeValue(item.component, '--')}</td>
                  <td>{String(item.key || 'live_pilot_expansion').replace(/_/g, ' ')}</td>
                  <td>{item.detail || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {livePilotExpansionCanaryIssues.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Live pilot expansion canary</caption>
            <thead>
              <tr>
                <th scope="col">Type</th>
                <th scope="col">Session</th>
                <th scope="col">Signal</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {livePilotExpansionCanaryIssues.slice(0, 12).map((item, index) => (
                <tr key={`${item.key || 'live-pilot-expansion-canary'}:${index}`}>
                  <td>{humanizeValue(item.severity, '--')}</td>
                  <td>{item.session_day || '--'}</td>
                  <td>{String(item.key || 'live_pilot_expansion_canary').replace(/_/g, ' ')}</td>
                  <td>{item.detail || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {livePilotWindowIssues.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Supervised live pilot window</caption>
            <thead>
              <tr>
                <th scope="col">Type</th>
                <th scope="col">Component</th>
                <th scope="col">Signal</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {livePilotWindowIssues.slice(0, 12).map((item, index) => (
                <tr key={`${item.key || 'live-pilot-window'}:${index}`}>
                  <td>{humanizeValue(item.severity, '--')}</td>
                  <td>{humanizeValue(item.component, '--')}</td>
                  <td>{String(item.key || 'live_pilot_window').replace(/_/g, ' ')}</td>
                  <td>{item.detail || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {livePilotWindowCanaryIssues.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Supervised live pilot canary</caption>
            <thead>
              <tr>
                <th scope="col">Type</th>
                <th scope="col">Session</th>
                <th scope="col">Signal</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {livePilotWindowCanaryIssues.slice(0, 12).map((item, index) => (
                <tr key={`${item.key || 'live-pilot-window-canary'}:${index}`}>
                  <td>{humanizeValue(item.severity, '--')}</td>
                  <td>{item.session_day || '--'}</td>
                  <td>{String(item.key || 'live_pilot_window_canary').replace(/_/g, ' ')}</td>
                  <td>{item.detail || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {livePilotPromotionIssues.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Live pilot promotion report</caption>
            <thead>
              <tr>
                <th scope="col">Type</th>
                <th scope="col">Component</th>
                <th scope="col">Signal</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {livePilotPromotionIssues.slice(0, 14).map((item, index) => (
                <tr key={`${item.key || 'live-pilot-promotion'}:${index}`}>
                  <td>{humanizeValue(item.severity, '--')}</td>
                  <td>{humanizeValue(item.component, '--')}</td>
                  <td>{String(item.key || 'live_pilot_promotion').replace(/_/g, ' ')}</td>
                  <td>{item.detail || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {livePilotPromotionEvidence.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Promotion evidence checklist</caption>
            <thead>
              <tr>
                <th scope="col">Evidence</th>
                <th scope="col">Status</th>
                <th scope="col">Ready</th>
                <th scope="col">Age</th>
                <th scope="col">Note</th>
                <th scope="col">Issues</th>
              </tr>
            </thead>
            <tbody>
              {livePilotPromotionEvidence.map((item, index) => (
                <tr key={`${item.key || item.label || 'promotion-evidence'}:${index}`}>
                  <td>{item.label || humanizeValue(item.key, 'Evidence')}</td>
                  <td>{humanizeValue(item.status, 'Missing')}</td>
                  <td>{item.ready ? 'Yes' : 'No'}</td>
                  <td>{item.age_days == null ? '--' : `${Number(item.age_days).toFixed(1)}d`}</td>
                  <td>{item.note_id ? 'Linked' : '--'}</td>
                  <td>{`${item.blocker_count || 0} blockers / ${item.warning_count || 0} warnings`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {limitedLiveRolloutIssues.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Limited-live rollout gate</caption>
            <thead>
              <tr>
                <th scope="col">Type</th>
                <th scope="col">Component</th>
                <th scope="col">Signal</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {limitedLiveRolloutIssues.slice(0, 12).map((item, index) => (
                <tr key={`${item.key || 'limited-live-rollout'}:${index}`}>
                  <td>{humanizeValue(item.severity, '--')}</td>
                  <td>{humanizeValue(item.component, '--')}</td>
                  <td>{String(item.key || 'limited_live_rollout').replace(/_/g, ' ')}</td>
                  <td>{item.detail || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {limitedLiveRolloutOrders.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Limited-live order evidence</caption>
            <thead>
              <tr>
                <th scope="col">Ticker</th>
                <th scope="col">Order</th>
                <th scope="col">Broker order</th>
                <th scope="col">Status</th>
                <th scope="col">Limit</th>
              </tr>
            </thead>
            <tbody>
              {limitedLiveRolloutOrders.slice(0, 3).map((item, index) => (
                <tr key={`${item.order_id || item.broker_order_id || 'limited-order'}:${index}`}>
                  <td>{item.ticker || '--'}</td>
                  <td>{item.order_id || '--'}</td>
                  <td>{item.broker_order_id || '--'}</td>
                  <td>{humanizeValue(item.broker_status, '--')}</td>
                  <td>{item.limit_price == null ? '--' : String(item.limit_price)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {limitedLiveRolloutCanaryIssues.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Limited-live rollout canary</caption>
            <thead>
              <tr>
                <th scope="col">Type</th>
                <th scope="col">Session</th>
                <th scope="col">Signal</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {limitedLiveRolloutCanaryIssues.slice(0, 12).map((item, index) => (
                <tr key={`${item.key || 'limited-live-rollout-canary'}:${index}`}>
                  <td>{humanizeValue(item.severity, '--')}</td>
                  <td>{item.session_day || '--'}</td>
                  <td>{String(item.key || 'limited_live_rollout_canary').replace(/_/g, ' ')}</td>
                  <td>{item.detail || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {limitedLiveRolloutCanarySessions.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Limited-live rollout canary sessions</caption>
            <thead>
              <tr>
                <th scope="col">Session</th>
                <th scope="col">Status</th>
                <th scope="col">Rollout</th>
                <th scope="col">Terminal</th>
                <th scope="col">Orders</th>
                <th scope="col">Reconcile</th>
                <th scope="col">Events</th>
                <th scope="col">Blockers</th>
              </tr>
            </thead>
            <tbody>
              {limitedLiveRolloutCanarySessions.slice(0, 5).map((item, index) => {
                const rollout = item.limited_live_rollout || {}
                const events = item.order_events || {}
                return (
                  <tr key={`${item.session_day || 'limited-session'}:${index}`}>
                    <td>{item.session_day || '--'}</td>
                    <td>{humanizeValue(item.status, '--')}</td>
                    <td>{humanizeValue(rollout.status, 'Missing')}</td>
                    <td>{humanizeValue(item.terminal_state, '--')}</td>
                    <td>{item.consumed_order_count ?? 0}</td>
                    <td>{humanizeValue(item.reconciliation_status, 'Missing')}</td>
                    <td>{events.count ?? 0}</td>
                    <td>{(item.blockers || []).length}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {limitedLiveCapExpansionIssues.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Limited-live cap expansion report</caption>
            <thead>
              <tr>
                <th scope="col">Type</th>
                <th scope="col">Component</th>
                <th scope="col">Signal</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {limitedLiveCapExpansionIssues.slice(0, 12).map((item, index) => (
                <tr key={`${item.key || 'limited-live-cap-expansion'}:${index}`}>
                  <td>{humanizeValue(item.severity, '--')}</td>
                  <td>{humanizeValue(item.component, '--')}</td>
                  <td>{String(item.key || 'limited_live_cap_expansion').replace(/_/g, ' ')}</td>
                  <td>{item.detail || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {limitedLiveCapExpansionEvidence.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Cap expansion evidence checklist</caption>
            <thead>
              <tr>
                <th scope="col">Evidence</th>
                <th scope="col">Status</th>
                <th scope="col">Ready</th>
                <th scope="col">Age</th>
                <th scope="col">Note</th>
              </tr>
            </thead>
            <tbody>
              {limitedLiveCapExpansionEvidence.slice(0, 6).map((item, index) => (
                <tr key={`${item.key || 'cap-evidence'}:${index}`}>
                  <td>{item.label || humanizeValue(item.key, '--')}</td>
                  <td>{humanizeValue(item.status, '--')}</td>
                  <td>{item.ready ? 'Yes' : 'No'}</td>
                  <td>{item.age_days == null ? '--' : `${Number(item.age_days).toFixed(2)}d`}</td>
                  <td>{item.note_id ? 'Linked' : 'Missing'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {limitedLiveCapExpansionGateIssues.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Limited-live cap expansion gate</caption>
            <thead>
              <tr>
                <th scope="col">Type</th>
                <th scope="col">Component</th>
                <th scope="col">Signal</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {limitedLiveCapExpansionGateIssues.slice(0, 12).map((item, index) => (
                <tr key={`${item.key || 'limited-live-cap-expansion-gate'}:${index}`}>
                  <td>{humanizeValue(item.severity, '--')}</td>
                  <td>{humanizeValue(item.component, '--')}</td>
                  <td>{String(item.key || 'limited_live_cap_expansion_gate').replace(/_/g, ' ')}</td>
                  <td>{item.detail || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {limitedLiveCapExpansionGateOrders.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Cap expansion order evidence</caption>
            <thead>
              <tr>
                <th scope="col">Ticker</th>
                <th scope="col">Order</th>
                <th scope="col">Broker</th>
                <th scope="col">Type</th>
                <th scope="col">Route</th>
              </tr>
            </thead>
            <tbody>
              {limitedLiveCapExpansionGateOrders.slice(0, 3).map((item, index) => (
                <tr key={`${item.order_id || item.broker_order_id || 'cap-expansion-order'}:${index}`}>
                  <td>{item.ticker || '--'}</td>
                  <td>{item.order_id || '--'}</td>
                  <td>{item.broker_order_id || '--'}</td>
                  <td>{humanizeValue(item.order_type, '--')}</td>
                  <td>{humanizeValue(item.route_family, '--')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {limitedLiveCapExpansionCanaryIssues.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Limited-live cap expansion canary</caption>
            <thead>
              <tr>
                <th scope="col">Type</th>
                <th scope="col">Session</th>
                <th scope="col">Signal</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {limitedLiveCapExpansionCanaryIssues.slice(0, 12).map((item, index) => (
                <tr key={`${item.key || 'limited-live-cap-expansion-canary'}:${index}`}>
                  <td>{humanizeValue(item.severity, '--')}</td>
                  <td>{item.session_day || '--'}</td>
                  <td>{String(item.key || 'limited_live_cap_expansion_canary').replace(/_/g, ' ')}</td>
                  <td>{item.detail || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {limitedLiveCapExpansionCanarySessions.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Expanded-cap canary sessions</caption>
            <thead>
              <tr>
                <th scope="col">Session</th>
                <th scope="col">Status</th>
                <th scope="col">Gate</th>
                <th scope="col">Terminal</th>
                <th scope="col">Current cap</th>
                <th scope="col">Expanded cap</th>
                <th scope="col">Orders</th>
                <th scope="col">Reconcile</th>
                <th scope="col">Events</th>
                <th scope="col">Blockers</th>
              </tr>
            </thead>
            <tbody>
              {limitedLiveCapExpansionCanarySessions.slice(0, 5).map((item, index) => {
                const gate = item.limited_live_cap_expansion_gate || {}
                const events = item.order_events || {}
                return (
                  <tr key={`${item.session_day || 'expanded-cap-session'}:${index}`}>
                    <td>{item.session_day || '--'}</td>
                    <td>{humanizeValue(item.status, '--')}</td>
                    <td>{humanizeValue(gate.status, 'Missing')}</td>
                    <td>{humanizeValue(item.terminal_state, '--')}</td>
                    <td>{item.current_max_notional == null ? '--' : `$${Number(item.current_max_notional).toFixed(2)}`}</td>
                    <td>{item.expanded_max_notional == null ? '--' : `$${Number(item.expanded_max_notional).toFixed(2)}`}</td>
                    <td>{item.consumed_order_count ?? 0}</td>
                    <td>{humanizeValue(item.reconciliation_status, 'Missing')}</td>
                    <td>{events.count ?? 0}</td>
                    <td>{(item.blockers || []).length}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {limitedLiveNextTierCapIssues.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Next-tier cap report</caption>
            <thead>
              <tr>
                <th scope="col">Type</th>
                <th scope="col">Component</th>
                <th scope="col">Signal</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {limitedLiveNextTierCapIssues.slice(0, 12).map((item, index) => (
                <tr key={`${item.key || 'limited-live-next-tier-cap'}:${index}`}>
                  <td>{humanizeValue(item.severity, '--')}</td>
                  <td>{humanizeValue(item.component, '--')}</td>
                  <td>{String(item.key || 'limited_live_next_tier_cap').replace(/_/g, ' ')}</td>
                  <td>{item.detail || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {limitedLiveNextTierCapEvidence.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Next-tier cap evidence checklist</caption>
            <thead>
              <tr>
                <th scope="col">Evidence</th>
                <th scope="col">Status</th>
                <th scope="col">Ready</th>
                <th scope="col">Age</th>
                <th scope="col">Note</th>
              </tr>
            </thead>
            <tbody>
              {limitedLiveNextTierCapEvidence.slice(0, 6).map((item, index) => (
                <tr key={`${item.key || 'next-tier-cap-evidence'}:${index}`}>
                  <td>{item.label || humanizeValue(item.key, '--')}</td>
                  <td>{humanizeValue(item.status, '--')}</td>
                  <td>{item.ready ? 'Yes' : 'No'}</td>
                  <td>{item.age_days == null ? '--' : `${Number(item.age_days).toFixed(2)}d`}</td>
                  <td>{item.note_id ? 'Linked' : 'Missing'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {limitedLiveNextTierCapGateIssues.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Next-tier cap gate blockers and warnings</caption>
            <thead>
              <tr>
                <th scope="col">Type</th>
                <th scope="col">Component</th>
                <th scope="col">Signal</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {limitedLiveNextTierCapGateIssues.slice(0, 12).map((item, index) => (
                <tr key={`${item.key || 'limited-live-next-tier-cap-gate'}:${index}`}>
                  <td>{humanizeValue(item.severity, '--')}</td>
                  <td>{humanizeValue(item.component, '--')}</td>
                  <td>{String(item.key || 'limited_live_next_tier_cap_gate').replace(/_/g, ' ')}</td>
                  <td>{item.detail || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {limitedLiveNextTierCapGateOrders.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Next-tier cap consumed orders</caption>
            <thead>
              <tr>
                <th scope="col">Ticker</th>
                <th scope="col">Order</th>
                <th scope="col">Broker order</th>
                <th scope="col">Type</th>
                <th scope="col">Route</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {limitedLiveNextTierCapGateOrders.slice(0, 3).map((item, index) => (
                <tr key={`${item.order_id || item.broker_order_id || 'next-tier-cap-order'}:${index}`}>
                  <td>{item.ticker || '--'}</td>
                  <td>{item.order_id || '--'}</td>
                  <td>{item.broker_order_id || '--'}</td>
                  <td>{humanizeValue(item.order_type, '--')}</td>
                  <td>{humanizeValue(item.route_family, '--')}</td>
                  <td>{humanizeValue(item.broker_status, '--')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {livePilotCanarySessions.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Live canary sessions</caption>
            <thead>
              <tr>
                <th scope="col">Session</th>
                <th scope="col">Status</th>
                <th scope="col">Soak</th>
                <th scope="col">Terminal</th>
                <th scope="col">Broker order</th>
                <th scope="col">Reconcile</th>
                <th scope="col">Readiness</th>
                <th scope="col">Blockers</th>
              </tr>
            </thead>
            <tbody>
              {livePilotCanarySessions.slice(0, 5).map((item, index) => {
                const soak = item.live_pilot_soak || {}
                const readiness = item.live_pilot_readiness || {}
                const blockers = Array.isArray(item.blockers) ? item.blockers : []
                return (
                  <tr key={`${item.session_day || 'live-canary-session'}:${index}`}>
                    <td>{item.session_day || '--'}</td>
                    <td>{humanizeValue(item.status, '--')}</td>
                    <td>{humanizeValue(soak.status, 'Missing')}</td>
                    <td>{humanizeValue(soak.terminal_state, '--')}</td>
                    <td>{soak.broker_order_id ? String(soak.broker_order_id).slice(0, 12) : '--'}</td>
                    <td>{humanizeValue(soak.reconciliation_status, 'Missing')}</td>
                    <td>{humanizeValue(readiness.status, 'Missing')}</td>
                    <td>{blockers.length ? blockers.map((blocker) => String(blocker.key || 'blocker').replace(/_/g, ' ')).join(', ') : 'None'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {livePilotExpansionCanarySessions.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Live expansion canary sessions</caption>
            <thead>
              <tr>
                <th scope="col">Session</th>
                <th scope="col">Status</th>
                <th scope="col">Expansion</th>
                <th scope="col">Terminal</th>
                <th scope="col">Candidate</th>
                <th scope="col">Broker order</th>
                <th scope="col">Reconcile</th>
                <th scope="col">Slippage</th>
                <th scope="col">Readiness</th>
                <th scope="col">Blockers</th>
              </tr>
            </thead>
            <tbody>
              {livePilotExpansionCanarySessions.slice(0, 5).map((item, index) => {
                const expansion = item.live_pilot_expansion || {}
                const readiness = item.live_pilot_readiness || {}
                const candidate = item.candidate || expansion.selected_candidate || {}
                const blockers = Array.isArray(item.blockers) ? item.blockers : []
                return (
                  <tr key={`${item.session_day || 'live-expansion-canary-session'}:${index}`}>
                    <td>{item.session_day || '--'}</td>
                    <td>{humanizeValue(item.status, '--')}</td>
                    <td>{humanizeValue(expansion.status, 'Missing')}</td>
                    <td>{humanizeValue(expansion.terminal_state, '--')}</td>
                    <td>{candidate.ticker || candidate.symbol || '--'}</td>
                    <td>{expansion.broker_order_id ? String(expansion.broker_order_id).slice(0, 12) : '--'}</td>
                    <td>{humanizeValue(expansion.reconciliation_status, 'Missing')}</td>
                    <td>{item.slippage?.abs_bps == null ? '--' : `${Number(item.slippage.abs_bps).toFixed(1)} bps`}</td>
                    <td>{humanizeValue(readiness.status, 'Missing')}</td>
                    <td>{blockers.length ? blockers.map((blocker) => String(blocker.key || 'blocker').replace(/_/g, ' ')).join(', ') : 'None'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {livePilotWindowCanarySessions.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Supervised live pilot canary sessions</caption>
            <thead>
              <tr>
                <th scope="col">Session</th>
                <th scope="col">Status</th>
                <th scope="col">Window</th>
                <th scope="col">Terminal</th>
                <th scope="col">Candidate</th>
                <th scope="col">Broker order</th>
                <th scope="col">Reconcile</th>
                <th scope="col">Slippage</th>
                <th scope="col">Readiness</th>
                <th scope="col">Blockers</th>
              </tr>
            </thead>
            <tbody>
              {livePilotWindowCanarySessions.slice(0, 5).map((item, index) => {
                const window = item.live_pilot_window || {}
                const readiness = item.live_pilot_readiness || {}
                const candidate = item.candidate || window.selected_candidate || {}
                const blockers = Array.isArray(item.blockers) ? item.blockers : []
                return (
                  <tr key={`${item.session_day || 'live-window-canary-session'}:${index}`}>
                    <td>{item.session_day || '--'}</td>
                    <td>{humanizeValue(item.status, '--')}</td>
                    <td>{humanizeValue(window.status, 'Missing')}</td>
                    <td>{humanizeValue(window.terminal_state, '--')}</td>
                    <td>{candidate.ticker || candidate.symbol || '--'}</td>
                    <td>{window.broker_order_id ? String(window.broker_order_id).slice(0, 12) : '--'}</td>
                    <td>{humanizeValue(window.reconciliation_status, 'Missing')}</td>
                    <td>{item.slippage?.abs_bps == null ? '--' : `${Number(item.slippage.abs_bps).toFixed(1)} bps`}</td>
                    <td>{humanizeValue(readiness.status, 'Missing')}</td>
                    <td>{blockers.length ? blockers.map((blocker) => String(blocker.key || 'blocker').replace(/_/g, ' ')).join(', ') : 'None'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {(aiReview.appliedChanges.length || aiReview.skippedChanges.length) ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>AI setting review</caption>
            <thead>
              <tr>
                <th scope="col">Field</th>
                <th scope="col">Before</th>
                <th scope="col">After</th>
                <th scope="col">Reason</th>
              </tr>
            </thead>
            <tbody>
              {[...aiReview.appliedChanges, ...aiReview.skippedChanges].slice(0, 8).map((item, index) => (
                <tr key={`${item.field || 'change'}:${index}`}>
                  <td>{String(item.field || '--').replace(/_/g, ' ')}</td>
                  <td>{String(item.before ?? '--')}</td>
                  <td>{String(item.after ?? '--')}</td>
                  <td>{item.skip_reason || item.reason || 'AI review recorded the setting decision.'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {accuracyRows.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Decision-PnL accuracy patterns</caption>
            <thead>
              <tr>
                <th scope="col">Group</th>
                <th scope="col">Pattern</th>
                <th scope="col">Samples</th>
                <th scope="col">Expectancy</th>
                <th scope="col">Hit rate</th>
                <th scope="col">Slippage</th>
              </tr>
            </thead>
            <tbody>
              {accuracyRows.slice(0, 10).map((item, index) => (
                <tr key={`${item.group || 'pattern'}:${item.pattern_key || index}`}>
                  <td>{humanizeValue(item.group, '--')}</td>
                  <td>{String(item.pattern_key || '--').replaceAll('|', ' / ').replace(/_/g, ' ')}</td>
                  <td>{item.sample_count ?? '--'}</td>
                  <td>{formatMoney(item.expectancy)}</td>
                  <td>{item.hit_rate == null ? '--' : `${(Number(item.hit_rate) * 100).toFixed(1)}%`}</td>
                  <td>{item.average_slippage_bps == null ? '--' : `${Number(item.average_slippage_bps).toFixed(1)} bps`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {paperEvidenceIssues.length ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Monday paper evidence blockers</caption>
            <thead>
              <tr>
                <th scope="col">Severity</th>
                <th scope="col">Key</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {paperEvidenceIssues.slice(0, 8).map((item, index) => (
                <tr key={`paper-evidence-issue:${item.key || index}`}>
                  <td>{humanizeValue(item.severity, 'Issue')}</td>
                  <td>{humanizeValue(item.key, '--')}</td>
                  <td>{item.detail || 'Paper evidence collection recorded this condition.'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {(replayLabRecommendations.length || replayLabStressResults.length || replayLabSensitivity.length || replayLabIssues.length) ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Replay lab what-if results</caption>
            <thead>
              <tr>
                <th scope="col">Type</th>
                <th scope="col">Item</th>
                <th scope="col">PnL / state</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {replayLabSensitivity.slice(0, 6).map((item, index) => (
                <tr key={`replay-sensitivity:${item.scenario || index}`}>
                  <td>Sensitivity</td>
                  <td>{humanizeValue(item.scenario, '--')}</td>
                  <td>{item.pnl_delta == null ? '--' : formatMoney(item.pnl_delta)}</td>
                  <td>{item.detail || 'Settings sensitivity replay.'}</td>
                </tr>
              ))}
              {replayLabStressResults.slice(0, 6).map((item, index) => (
                <tr key={`replay-stress:${item.scenario || index}`}>
                  <td>Stress</td>
                  <td>{humanizeValue(item.scenario, '--')}</td>
                  <td>{humanizeValue(item.status, '--')}</td>
                  <td>{item.detail || `Drawdown ${formatMoney(item.max_drawdown)}; breaches ${item.loss_budget_breaches ?? 0}.`}</td>
                </tr>
              ))}
              {replayLabRecommendations.slice(0, 6).map((item, index) => (
                <tr key={`replay-rec:${item.field || index}`}>
                  <td>Recommendation</td>
                  <td>{humanizeValue(item.field, '--')}</td>
                  <td>{humanizeValue(item.direction, '--')}</td>
                  <td>{item.reason || 'Replay lab advisory recommendation.'}</td>
                </tr>
              ))}
              {replayLabIssues.slice(0, 6).map((item, index) => (
                <tr key={`replay-issue:${item.key || index}`}>
                  <td>{humanizeValue(item.severity, 'Issue')}</td>
                  <td>{humanizeValue(item.key, '--')}</td>
                  <td>--</td>
                  <td>{item.detail || 'Replay lab recorded this condition.'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {(transactionCostRows.length || transactionCostRecommendations.length || transactionCostIssues.length) ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Transaction cost calibration</caption>
            <thead>
              <tr>
                <th scope="col">Type</th>
                <th scope="col">Bucket</th>
                <th scope="col">Samples</th>
                <th scope="col">Cost / state</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {transactionCostRows.slice(0, 8).map((item, index) => (
                <tr key={`transaction-cost-row:${item.group || 'bucket'}:${item.bucket || index}`}>
                  <td>{humanizeValue(item.group, '--')}</td>
                  <td>{String(item.bucket || '--').replaceAll('|', ' / ').replace(/_/g, ' ')}</td>
                  <td>{item.sample_count ?? '--'}</td>
                  <td>{item.average_cost_error_bps == null ? formatMoney(item.total_pnl) : `${Number(item.average_cost_error_bps).toFixed(1)} bps`}</td>
                  <td>{item.cost_negative_count ? `${item.cost_negative_count} cost-negative trade(s)` : 'Cost bucket recorded.'}</td>
                </tr>
              ))}
              {transactionCostRecommendations.slice(0, 6).map((item, index) => (
                <tr key={`transaction-cost-rec:${item.field || index}`}>
                  <td>Recommendation</td>
                  <td>{humanizeValue(item.field, '--')}</td>
                  <td>--</td>
                  <td>{humanizeValue(item.direction, '--')}</td>
                  <td>{item.reason || 'Cost calibration advisory recommendation.'}</td>
                </tr>
              ))}
              {transactionCostIssues.slice(0, 6).map((item, index) => (
                <tr key={`transaction-cost-issue:${item.key || index}`}>
                  <td>{humanizeValue(item.severity, 'Issue')}</td>
                  <td>{humanizeValue(item.key, '--')}</td>
                  <td>--</td>
                  <td>{humanizeValue(item.severity, '--')}</td>
                  <td>{item.detail || 'Transaction cost calibration recorded this condition.'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {(lossContainmentActions.length || lossContainmentIssues.length) ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Loss containment actions and blockers</caption>
            <thead>
              <tr>
                <th scope="col">Type</th>
                <th scope="col">Ticker</th>
                <th scope="col">Action</th>
                <th scope="col">Risk</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {lossContainmentActions.slice(0, 8).map((item, index) => (
                <tr key={`loss-action:${item.trade_id || item.order_id || item.ticker || index}`}>
                  <td>Action</td>
                  <td>{item.ticker || '--'}</td>
                  <td>{humanizeValue(item.action, '--')}</td>
                  <td>
                    {[
                      item.current_r == null ? null : `${Number(item.current_r).toFixed(2)}R`,
                      item.unrealized_pnl == null ? null : formatMoney(item.unrealized_pnl),
                    ].filter(Boolean).join(' | ') || '--'}
                  </td>
                  <td>{humanizeValue(item.reason, item.auto_close_eligible ? 'Paper defensive close eligible.' : 'Advisory only.')}</td>
                </tr>
              ))}
              {lossContainmentIssues.slice(0, 8).map((item, index) => (
                <tr key={`loss-issue:${item.key || item.ticker || index}`}>
                  <td>{humanizeValue(item.severity, 'Issue')}</td>
                  <td>{item.ticker || '--'}</td>
                  <td>{humanizeValue(item.key, '--')}</td>
                  <td>--</td>
                  <td>{item.detail || 'Loss containment recorded this condition.'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {(exitWatchdogEvaluations.length || exitWatchdogIssues.length || exitWatchdogRescueItems.length || exitWatchdogReconciliation.status) ? (
        <div className="table-shell">
          <table className="list-table">
            <caption>Exit execution watchdog</caption>
            <thead>
              <tr>
                <th scope="col">Type</th>
                <th scope="col">Ticker</th>
                <th scope="col">State</th>
                <th scope="col">Delay</th>
                <th scope="col">Evidence</th>
              </tr>
            </thead>
            <tbody>
              {exitWatchdogEvaluations.slice(0, 8).map((item, index) => (
                <tr key={`exit-watchdog:${item.trade_id || item.order_id || item.ticker || index}`}>
                  <td>Exit</td>
                  <td>{item.ticker || '--'}</td>
                  <td>{humanizeValue(item.status, '--')}</td>
                  <td>{item.elapsed_seconds == null ? '--' : `${Number(item.elapsed_seconds).toFixed(0)}s`}</td>
                  <td>
                    {[
                      item.terminal_evidence?.source === 'closed_trade_ledger' ? 'Closed ledger proof' : null,
                      item.terminal_evidence?.event_key ? humanizeValue(item.terminal_evidence.event_key, 'Terminal proof') : null,
                      item.latest_order_event?.event_key ? humanizeValue(item.latest_order_event.event_key, 'Order proof') : null,
                      item.latest_option_exit_event?.event_type ? humanizeValue(item.latest_option_exit_event.event_type, 'Option exit proof') : null,
                      item.reason ? humanizeValue(item.reason, '') : null,
                    ].filter(Boolean).join(' | ') || 'Waiting for broker/local proof.'}
                  </td>
                </tr>
              ))}
              {exitWatchdogIssues.slice(0, 8).map((item, index) => (
                <tr key={`exit-watchdog-issue:${item.key || item.ticker || index}`}>
                  <td>{humanizeValue(item.severity, 'Issue')}</td>
                  <td>{item.ticker || '--'}</td>
                  <td>{humanizeValue(item.key, '--')}</td>
                  <td>--</td>
                  <td>{item.detail || 'Exit watchdog recorded this condition.'}</td>
                </tr>
              ))}
              {exitWatchdogRescueItems.slice(0, 8).map((item, index) => (
                <tr key={`exit-watchdog-rescue:${item.key || index}`}>
                  <td>Rescue</td>
                  <td>--</td>
                  <td>{humanizeValue(item.status, '--')}</td>
                  <td>--</td>
                  <td>{item.label ? `${item.label}: ${item.detail || ''}` : item.detail || 'Manual rescue item.'}</td>
                </tr>
              ))}
              {exitWatchdogReconciliation.status ? (
                <tr>
                  <td>Reconcile</td>
                  <td>--</td>
                  <td>{humanizeValue(exitWatchdogReconciliation.status, '--')}</td>
                  <td>--</td>
                  <td>
                    {[
                      exitWatchdogReconciliation.broker_available === false ? 'Broker unavailable' : null,
                      exitWatchdogReconciliation.ledger_consistency ? `Ledger ${humanizeValue(exitWatchdogReconciliation.ledger_consistency, '')}` : null,
                      exitWatchdogReconciliation.related_note_id ? 'Reconciliation note linked' : null,
                    ].filter(Boolean).join(' | ') || 'Broker/local reconciliation evidence attached.'}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      ) : null}

      <section className="section-stack">
        <FeedbackState
          tone={candidateDiagnosticError ? 'warning' : Number(candidateDiagnosticSummary.eligible_count || 0) > 0 ? 'positive' : 'warning'}
          title={candidateDiagnosticError ? 'Candidate diagnostics unavailable' : 'Why no trade?'}
          description={
            candidateDiagnosticError ||
            `${candidateDiagnosticUniverse.label || 'Scan board'} checked ${candidateDiagnosticSummary.scanned_count ?? 0} symbol${Number(candidateDiagnosticSummary.scanned_count || 0) === 1 ? '' : 's'} and found ${candidateDiagnosticSummary.eligible_count ?? 0} current auto-entry candidate${Number(candidateDiagnosticSummary.eligible_count || 0) === 1 ? '' : 's'}. Broader scope adds candidates; risk caps and ticket sizing stay unchanged.`
          }
        />
        <section className="metrics-grid metrics-grid--compact">
          {[
            { label: 'Scan board', value: candidateDiagnosticUniverse.ticker_count ? `${candidateDiagnosticUniverse.ticker_count} symbols` : '--', tone: 'neutral' },
            { label: 'Stage one', value: candidateDiagnosticSummary.stage_one_count != null ? String(candidateDiagnosticSummary.stage_one_count) : '--', tone: 'neutral' },
            { label: 'Deep analyzed', value: candidateDiagnosticSummary.deep_analyzed_count != null ? String(candidateDiagnosticSummary.deep_analyzed_count) : '--', tone: Number(candidateDiagnosticSummary.deep_analyzed_count || 0) > 0 ? 'positive' : 'warning' },
            { label: 'Eligible now', value: String(candidateDiagnosticSummary.eligible_count ?? 0), tone: Number(candidateDiagnosticSummary.eligible_count || 0) > 0 ? 'positive' : 'warning' },
            { label: 'Top blocker', value: humanizeValue(candidateDiagnosticSummary.top_blocker, 'None'), tone: candidateDiagnosticSummary.top_blocker ? 'warning' : 'positive' },
            { label: 'AI referee', value: `${candidateAiSummary.approved_count ?? 0} approved`, tone: Number(candidateAiSummary.approved_count || 0) > 0 ? 'positive' : 'neutral' },
            { label: 'AI reason codes', value: String(Object.keys(candidateAiSummary.reason_code_counts || {}).length), tone: Object.keys(candidateAiSummary.reason_code_counts || {}).length ? 'warning' : 'positive' },
            { label: 'Miss review', value: candidateMissedReview.catch_rate == null ? `${candidateMissedReview.reviewed_count ?? 0} rows` : `${Math.round(Number(candidateMissedReview.catch_rate) * 100)}% catchable`, tone: Number(candidateMissedReview.catchable_now_count || 0) > 0 ? 'warning' : 'neutral' },
            { label: 'Lifecycle rows', value: String(candidateLifecycle.tracked_count ?? candidateDiagnosticItems.length), tone: Number(candidateLifecycle.tracked_count || 0) > 0 ? 'positive' : 'neutral' },
            { label: 'Routeable / blocked', value: `${candidateRouteabilityCounts.routeable ?? 0} / ${Number(candidateDiagnosticSummary.blocked_count || 0)}`, tone: Number(candidateRouteabilityCounts.routeable || 0) > 0 ? 'positive' : 'warning' },
            {
              label: 'Against market',
              value: `${againstMarketProxyContext.routeable_proxy_count ?? 0}/${againstMarketProxyContext.signal_count ?? 0}`,
              tone: Number(againstMarketProxyContext.routeable_proxy_count || 0) > 0
                ? 'positive'
                : Number(againstMarketProxyContext.signal_count || 0) > 0
                  ? 'warning'
                  : 'neutral',
            },
            { label: 'Ticket cap', value: candidateDiagnosticSizing.dynamic_max_notional_per_trade == null ? '--' : formatMoney(candidateDiagnosticSizing.dynamic_max_notional_per_trade), tone: 'neutral' },
          ].map((item, index) => renderMetricCard(item, `candidate-diagnostic-${index}`))}
        </section>
        <div className="metric-strip">
          <span>Against Market mode {humanizeValue(againstMarketProxyContext.mode, 'Against market proxy')}</span>
          <span>Proxy universe {(againstMarketProxyContext.proxy_universe || candidateDiagnosticUniverse.against_market_proxy_universe || ['SH', 'PSQ', 'DOG', 'RWM']).join(', ')}</span>
          <span>{againstMarketProxyContext.can_submit_live_orders ? 'Live order authority needs review' : 'No autonomous live orders'}</span>
          <span>{againstMarketProxyContext.direct_short_authority ? 'Direct shorts need review' : 'Direct shorts off'}</span>
        </div>
        {Object.keys(candidateAiSummary.evidence_incomplete_buckets || {}).length ? (
          <div className="metric-strip">
            {Object.entries(candidateAiSummary.evidence_incomplete_buckets || {}).slice(0, 6).map(([key, value]) => (
              <span key={`ai-incomplete:${key}`}>{humanizeValue(key)} {value}</span>
            ))}
          </div>
        ) : null}
        <div className="table-shell">
          <table className="list-table">
            <caption>45-symbol liquid scan board diagnostics</caption>
            <thead>
              <tr>
                <th scope="col">Ticker</th>
                <th scope="col">Lifecycle</th>
                <th scope="col">Stage</th>
                <th scope="col">Status</th>
                <th scope="col">Blocker</th>
                <th scope="col">AI referee</th>
                <th scope="col">Route / quote</th>
                <th scope="col">Against Market</th>
                <th scope="col">Scores</th>
                <th scope="col">Edge/cost</th>
                <th scope="col">Next safe action</th>
              </tr>
            </thead>
            <tbody>
              {candidateDiagnosticTopRows.length ? candidateDiagnosticTopRows.map((item, index) => {
                const scores = item.scores || {}
                return (
                  <tr key={`candidate-diagnostic:${item.ticker || index}`}>
                    <td>{item.ticker || '--'}</td>
                    <td>{item.candidate_lifecycle_id || '--'}<br />Rejected {item.rejected_at ? new Date(item.rejected_at).toLocaleTimeString() : 'No'}</td>
                    <td>{humanizeValue(item.stage, 'Stage one')}</td>
                    <td>{item.eligible ? 'Eligible' : humanizeValue(item.status, 'Blocked')}</td>
                    <td>{item.blocker ? humanizeValue(item.blocker, '--') : 'None'}</td>
                    <td>
                      {[
                        humanizeValue(item.ai_evidence_review?.verdict, 'Waiting'),
                        item.ai_evidence_review?.confidence != null ? `${Math.round(Number(item.ai_evidence_review.confidence) * 100)}%` : null,
                      ].filter(Boolean).join(' | ')}
                    </td>
                    <td>
                      {item.routeability?.candidate_routeable ? 'Routeable' : humanizeValue(item.routeability?.blocked_reason, 'Blocked')}
                      <br />
                      Quote {item.quote_age_seconds == null ? '--' : `${Number(item.quote_age_seconds).toFixed(0)}s`}
                    </td>
                    <td>
                      {item.paper_routeable_against_market ? 'Proxy ready' : humanizeValue(item.against_market_usage_status, 'Waiting')}
                      <br />
                      {item.proxy_symbol || item.against_market_proxy?.proxy_symbol || '--'} {item.bearish_confirmation_score == null ? '' : `${Number(item.bearish_confirmation_score).toFixed(0)}`}
                    </td>
                    <td>
                      {[
                        item.stage_one_score != null ? `S1 ${Number(item.stage_one_score).toFixed(1)}` : scores.stage_one_score != null ? `S1 ${Number(scores.stage_one_score).toFixed(1)}` : null,
                        item.deep_score != null ? `Deep ${Number(item.deep_score).toFixed(1)}` : scores.deep_score != null ? `Deep ${Number(scores.deep_score).toFixed(1)}` : null,
                        scores.execution_score != null ? `Exec ${Number(scores.execution_score).toFixed(1)}` : null,
                        scores.portfolio_score != null ? `Port ${Number(scores.portfolio_score).toFixed(1)}` : null,
                        scores.daily_objective_score != null ? `Obj ${Number(scores.daily_objective_score).toFixed(1)}` : null,
                      ].filter(Boolean).join(' | ') || '--'}
                    </td>
                    <td>{scores.edge_to_cost_ratio != null ? `${Number(scores.edge_to_cost_ratio).toFixed(1)}x` : '--'}</td>
                    <td>{item.next_action || item.detail || 'Wait for a cleaner setup.'}</td>
                  </tr>
                )
              }) : (
                <tr>
                  <td colSpan={11}>Diagnostics will populate after the scan board can be evaluated. No trade is forced while this is empty.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <div className="table-shell">
        <table className="list-table">
          <caption>Last ranked-entry candidate</caption>
          <thead>
            <tr>
              <th scope="col">Ticker</th>
              <th scope="col">Scores</th>
              <th scope="col">Edge/cost</th>
              <th scope="col">Rank</th>
              <th scope="col">Bucket</th>
              <th scope="col">Eligible</th>
              <th scope="col">AI referee</th>
            </tr>
          </thead>
          <tbody>
            {lastCandidate ? (
              <tr>
                <td>{lastCandidate.ticker || 'Unknown'}</td>
                <td>
                  {[
                    lastCandidate.alpha_score != null ? `Alpha ${Number(lastCandidate.alpha_score).toFixed(1)}` : null,
                    lastCandidate.accuracy_calibrated_score != null ? `Acc ${Number(lastCandidate.accuracy_calibrated_score).toFixed(1)}` : null,
                    lastCandidate.daily_objective_score != null ? `Obj ${Number(lastCandidate.daily_objective_score).toFixed(1)}` : null,
                    lastCandidate.execution_score != null ? `Exec ${Number(lastCandidate.execution_score).toFixed(1)}` : null,
                    lastCandidate.portfolio_score != null ? `Port ${Number(lastCandidate.portfolio_score).toFixed(1)}` : null,
                  ].filter(Boolean).join(' | ') || '--'}
                </td>
                <td>{lastCandidate.edge_to_cost_ratio != null ? `${Number(lastCandidate.edge_to_cost_ratio).toFixed(1)}x` : '--'}</td>
                <td>{lastCandidate.portfolio_rank ?? '--'}</td>
                <td>{String(lastCandidate.proxy_correlation_bucket || '--').replaceAll('_', ' ')}</td>
                <td>{lastCandidate.auto_entry_eligible ? 'Yes' : 'No'}</td>
                <td>{humanizeValue(lastCandidate.ai_evidence_review?.verdict, 'Waiting')}</td>
              </tr>
            ) : (
              <tr>
                <td colSpan={7}>No candidate telemetry has been captured yet. Run a cycle to inspect the current ranked-entry leader.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="table-shell">
        <table className="list-table">
          <caption>Path evaluations</caption>
          <thead>
            <tr>
              <th scope="col">Path</th>
              <th scope="col">Ticker</th>
              <th scope="col">Status</th>
              <th scope="col">Exec score</th>
              <th scope="col">Detail</th>
            </tr>
          </thead>
          <tbody>
            {pathEvaluations.length ? pathEvaluations.map((item, index) => (
              <tr key={`${item.instrument_type || 'path'}:${index}`}>
                <td>{String(item.instrument_type || '--').replaceAll('_', ' ')}</td>
                <td>{item.ticker || '--'}</td>
                <td>{String(item.status || 'idle').replace(/_/g, ' ')}</td>
                <td>{item.execution_score != null ? Number(item.execution_score).toFixed(1) : '--'}</td>
                <td>{item.detail || 'No path detail recorded.'}</td>
              </tr>
            )) : (
              <tr>
                <td colSpan={5}>Path-specific equity and option telemetry will show up here after the automation evaluates a watchlist cycle.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="table-shell">
        <table className="list-table">
          <caption>Recent automation history</caption>
          <thead>
            <tr>
              <th scope="col">Time</th>
              <th scope="col">Event</th>
              <th scope="col">Detail</th>
            </tr>
          </thead>
          <tbody>
            {historyItems.length ? historyItems.map((item, index) => (
              <tr key={`${item.at || item.type || 'history'}:${index}`}>
                <td>{item.at ? new Date(item.at).toLocaleString() : 'Recent'}</td>
                <td>{String(item.type || item.action || item.decision || 'Cycle').replace(/_/g, ' ')}</td>
                <td>{item.detail || item.reason || item.ticker || 'Automation updated its control state.'}</td>
              </tr>
            )) : (
              <tr>
                <td colSpan={3}>No automation history yet. Save the route, arm it, and the worker will start logging unattended cycles here.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="table-shell">
        <table className="list-table">
          <caption>Recent automation outcomes</caption>
          <thead>
            <tr>
              <th scope="col">Closed</th>
              <th scope="col">Ticker</th>
              <th scope="col">Status</th>
              <th scope="col">PnL</th>
            </tr>
          </thead>
          <tbody>
            {recentClosed.length ? recentClosed.map((item, index) => (
              <tr key={`${item.trade_id || item.ticker || 'closed'}:${index}`}>
                <td>{item.closed_at ? new Date(item.closed_at).toLocaleString() : 'Recent'}</td>
                <td>{item.ticker || 'Unknown'}</td>
                <td>{String(item.status || 'Closed').replace(/_/g, ' ')}</td>
                <td>{formatMoney(item.realized_pnl)}</td>
              </tr>
            )) : (
              <tr>
                <td colSpan={4}>No closed automation trades yet. Once the worker finishes a few unattended cycles, realized PnL and repair behavior will show up here.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="table-shell">
        <table className="list-table">
          <caption>Recent automation order events</caption>
          <thead>
            <tr>
              <th scope="col">Time</th>
              <th scope="col">Ticker</th>
              <th scope="col">Event</th>
              <th scope="col">Detail</th>
            </tr>
          </thead>
          <tbody>
            {recentEvents.length ? recentEvents.map((item, index) => (
              <tr key={`${item.id || item.trade_id || item.ticker || 'event'}:${index}`}>
                <td>{item.created_at ? new Date(item.created_at).toLocaleString() : 'Recent'}</td>
                <td>{item.ticker || 'Unknown'}</td>
                <td>{item.label || 'Event'}</td>
                <td>
                  {item.detail || 'Automation updated the order lifecycle.'}
                  {item.slippage_bps != null ? ` (${Number(item.slippage_bps).toFixed(1)} bps)` : ''}
                </td>
              </tr>
            )) : (
              <tr>
                <td colSpan={4}>Automation-linked order events will show up here once unattended entries or broker reconciliations start recording lifecycle changes.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </SectionCard>
  )
}
