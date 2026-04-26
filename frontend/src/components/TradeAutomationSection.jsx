import { useEffect, useMemo, useState } from 'react'
import {
  getOptionsAutomationSnapshot,
  getOrganizationTradeAutomation,
  getLinkedBrokerageAccounts,
  runOrganizationTradeAutomationAction,
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
  { value: 'broker_paper', label: 'Broker paper' },
  { value: 'broker_live', label: 'Broker live' },
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
  const isPersonalMode = mode === 'personal'
  const sectionTitle = title || (isPersonalMode ? 'Autonomous desk' : 'Trade automation')
  const sectionSubtitle =
    subtitle ||
    (isPersonalMode
      ? 'Let the workstation prep, scan, route paper orders, and manage exits while you are away. Keep broker-live on a separate pilot gate.'
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
    try {
      if (automationScope.locked) {
        setSnapshot(null)
        setOptionsSnapshot(null)
        setForm(buildForm(null))
        setError(automationScope.lockedReason || 'Bind a brokerage account before configuring automation.')
        return
      }
      const shouldLoadOptions = automationScope.scope === 'personal_paper'
      const [payload, nextOptionsSnapshot] = await Promise.all([
        getOrganizationTradeAutomation(automationScope),
        shouldLoadOptions ? getOptionsAutomationSnapshot() : Promise.resolve(null),
      ])
      setSnapshot(payload)
      setOptionsSnapshot(nextOptionsSnapshot)
      setForm(buildForm(payload))
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || 'Automation settings could not be loaded.')
    } finally {
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
      label: route.label || 'Broker route',
      value: route.value || 'Unknown',
      helper: route.active ? 'Current execution path' : route.detail || '',
      tone: route.active ? 'positive' : route.tone || 'default',
    }))
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
    : dailyObjectiveStatus === 'target_reached'
      ? 'positive'
      : dailyObjectiveStatus === 'tracking'
        ? 'warning'
        : 'neutral'
  const dailyObjectiveCards = [
    { label: '$1000 target', value: formatMoney(dailyObjective.target_dollars ?? form.dailyProfitTargetDollars), tone: dailyObjectiveTone },
    { label: 'Progress', value: dailyObjective.target_progress_pct == null ? '--' : `${Number(dailyObjective.target_progress_pct).toFixed(1)}%`, tone: dailyObjectiveTone },
    { label: 'Target gap', value: formatMoney(dailyObjective.target_gap), tone: Number(dailyObjective.target_gap || 0) <= 0 ? 'positive' : 'warning' },
    { label: '0.5% max loss', value: formatMoney(dailyObjective.loss_budget_dollars), tone: dailyObjective.entries_blocked ? 'negative' : 'neutral' },
    { label: 'Risk used', value: dailyObjective.loss_budget_used_pct == null ? '--' : `${Number(dailyObjective.loss_budget_used_pct).toFixed(1)}%`, tone: Number(dailyObjective.loss_budget_used_pct || 0) >= 100 ? 'negative' : Number(dailyObjective.loss_budget_used_pct || 0) >= 70 ? 'warning' : 'neutral' },
    { label: 'Objective note', value: dailyObjective.related_note_id ? 'Linked' : '--', tone: dailyObjective.related_note_id ? 'positive' : 'neutral' },
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
  const lastCandidate = runtimeTelemetry.candidate
  const lastRejection = runtimeTelemetry.rejection
  const pathEvaluations = runtimeTelemetry.pathEvaluations

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
          title={rolloutReadiness?.label || 'Broker-live readiness'}
          description={rolloutReadiness?.detail || 'Broker-live automation stays behind the paper gate until rollout readiness clears.'}
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
          title={`Live broker: ${brokerRoutes.broker_live.value || 'Unavailable'}`}
          description={brokerRoutes.broker_live.detail || 'Live broker status is not available.'}
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
      {snapshot?.account_summary ? (
        <section className="metrics-grid metrics-grid--compact">
          <MetricCard label="Equity" value={formatMoney(snapshot.account_summary.equity ?? snapshot.account_summary.portfolio_value)} />
          <MetricCard label="Cash" value={formatMoney(snapshot.account_summary.cash)} />
          <MetricCard label="Buying power" value={formatMoney(snapshot.account_summary.buying_power)} />
          <MetricCard label="Profile" value={humanizeValue(snapshot.profile_key, '--')} />
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
        title="Daily 1% objective"
        description={`${formatMoney(dailyObjective.total_pnl)} today | ${dailyObjective.target_reached ? '$1000 target reached' : `${formatMoney(dailyObjective.target_gap)} gap`} | ${dailyObjective.entries_blocked ? 'new paper entries blocked by loss budget' : 'target-only, entries remain governed by risk controls'}`}
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
        <SelectField label="Execution route" hint={collectionPhase?.active ? 'Collection phase hard-locks routing to broker paper until current-route validation clears.' : 'Broker paper is the safest unattended route until broker-live readiness is actually clear.'} value={form.executionIntent} onChange={(e) => setForm((current) => ({ ...current, executionIntent: e.target.value }))} disabled={collectionPhase?.active}>
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
              : `Sizing uses the live broker balance for this profile. Source: ${humanizeValue(snapshot?.funds_source, '--')}.`
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
        <ToggleField label="Daily 1% objective" hint="Paper-first target tracking and candidate ranking overlay. It does not promise returns or stop trading after the target." checked={form.dailyObjectiveEnabled} onChange={(e) => setForm((current) => ({ ...current, dailyObjectiveEnabled: e.target.checked }))} />
        <TextField label="Daily target $" hint="Target-only objective used for progress, notes, and paper candidate prioritization." type="number" min="1" max="1000000" step="50" value={form.dailyProfitTargetDollars} onChange={(e) => setForm((current) => ({ ...current, dailyProfitTargetDollars: e.target.value }))} />
        <TextField label="Daily target %" hint="Reference percent target shown alongside the dollar target." type="number" min="0.1" max="10" step="0.1" value={form.dailyProfitTargetPct} onChange={(e) => setForm((current) => ({ ...current, dailyProfitTargetPct: e.target.value }))} />
        <TextField label="Daily loss budget %" hint="Hard paper new-entry stop when same-day objective PnL breaches this loss budget." type="number" min="0.1" max="10" step="0.1" value={form.dailyLossBudgetPct} onChange={(e) => setForm((current) => ({ ...current, dailyLossBudgetPct: e.target.value }))} />
        <ToggleField label="Objective live scope" hint="Off by default. Live caps still only move through the limited-live safety ladder." checked={form.dailyObjectiveApplyToLive} onChange={(e) => setForm((current) => ({ ...current, dailyObjectiveApplyToLive: e.target.checked }))} />
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
        <TextField label="Max open positions" type="number" min="1" max="25" step="1" value={form.maxOpenPositions} onChange={(e) => setForm((current) => ({ ...current, maxOpenPositions: e.target.value }))} />
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
        <ToggleField label="Limited-live rollout" hint="Allow operator-approved runtime-only broker-live routing after the promotion report is ready." checked={form.limitedLiveRolloutEnabled} onChange={(e) => setForm((current) => ({ ...current, limitedLiveRolloutEnabled: e.target.checked }))} />
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
        <ToggleField label="AI adjust live" hint="Live profiles still obey the broker-live readiness gate and safety locks." checked={form.aiAdjustLiveEnabled} onChange={(e) => setForm((current) => ({ ...current, aiAdjustLiveEnabled: e.target.checked }))} />
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

      {(exitWatchdogEvaluations.length || exitWatchdogIssues.length) ? (
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
            </tbody>
          </table>
        </div>
      ) : null}

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
              </tr>
            ) : (
              <tr>
                <td colSpan={6}>No candidate telemetry has been captured yet. Run a cycle to inspect the current ranked-entry leader.</td>
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
