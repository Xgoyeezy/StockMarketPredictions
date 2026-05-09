import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getBootstrap, getFrontendAlerts, getFrontendFilters, getTradeSummary } from '../api/client'
import ActionBar from '../components/ActionBar'
import Button from '../components/Button'
import Chip from '../components/Chip'
import DataToolbar from '../components/DataToolbar'
import EmptyState from '../components/EmptyState'
import ErrorState from '../components/ErrorState'
import { SelectField, ToggleField } from '../components/FormFields'
import Kicker from '../components/Kicker'
import LoadingBlock from '../components/LoadingBlock'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import StatusBadge from '../components/StatusBadge'
import WorkflowGuide, { buildWorkflowSteps } from '../components/WorkflowGuide'
import usePageActionShortcuts, { focusFirstMatching } from '../hooks/usePageActionShortcuts'
import useKeyboardListNavigation from '../hooks/useKeyboardListNavigation'
import usePolling from '../hooks/usePolling'
import { usePreferences } from '../context/PreferencesContext'
import { buildRolloutReadinessSummary } from '../utils/capitalPreservation'
import {
  normalizeAccountProfile,
  resolveAccountProfileExecutionIntent,
} from '../utils/accountProfileModel'

function toNumber(value) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

function formatLabel(value, fallback = '--') {
  const normalized = String(value || '').trim()
  if (!normalized) return fallback
  return normalized.replaceAll('_', ' ').replace(/\b\w/g, (character) => character.toUpperCase())
}

function formatPercent(value, { ratio = true, digits = 1 } = {}) {
  const numeric = toNumber(value)
  if (numeric === null) return '--'
  const percentage = ratio ? numeric * 100 : numeric
  return `${percentage.toFixed(digits)}%`
}

function formatExecutionIntentLabel(value) {
  const normalized = String(value || 'desk').trim().toLowerCase()
  if (normalized === 'broker_live') return 'Alpaca live'
  if (normalized === 'broker_paper') return 'Alpaca paper'
  return 'Desk only'
}

function buildAlertDeskUrl(alert) {
  const ticker = String(alert?.ticker || '').trim().toUpperCase()
  if (!ticker) return '/'
  const params = new URLSearchParams()
  params.set('ticker', ticker)
  return `/?${params.toString()}`
}

function buildAlertsCompareUrl(alerts = []) {
  const tickers = Array.from(
    new Set(
      (Array.isArray(alerts) ? alerts : [])
        .map((alert) => String(alert?.ticker || '').trim().toUpperCase())
        .filter(Boolean),
    ),
  ).slice(0, 6)

  if (!tickers.length) return '/compare'

  const params = new URLSearchParams()
  params.set('tickers', tickers.join(','))
  params.set('focusTicker', tickers[0])
  params.set('workflowAutoload', '1')
  params.set('workflowFrom', 'alerts')
  return `/compare?${params.toString()}`
}

function severityTone(value) {
  const normalized = String(value || '').trim().toLowerCase()
  if (normalized === 'critical' || normalized === 'high') return 'negative'
  if (normalized === 'medium') return 'warning'
  return 'neutral'
}

function getCurrentSessionLabel(date = new Date()) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(date)
  const hour = Number(parts.find((part) => part.type === 'hour')?.value || '0')
  const minute = Number(parts.find((part) => part.type === 'minute')?.value || '0')
  const minutes = (hour * 60) + minute
  if (minutes >= 4 * 60 && minutes < 9 * 60 + 30) return 'Premarket'
  if (minutes >= 9 * 60 + 30 && minutes < 16 * 60) return 'Regular'
  if (minutes >= 16 * 60 && minutes < 20 * 60) return 'After-hours'
  return 'Overnight'
}

function resolveAlertEventContext(alert) {
  const context = alert?.context || {}
  const rawContext = context.event_context || {}
  const nextEventName = String(rawContext.next_event_name || context.next_event_name || '').trim()
  const nextEventDate = String(rawContext.next_event_date || context.next_event_date || '').trim()
  const eventWindowLabel =
    String(rawContext.event_window_label || context.event_window_label || '').trim().toLowerCase() ||
    (!Boolean(rawContext.event_risk ?? context.event_risk)
      ? 'quiet_window'
      : nextEventName.toLowerCase().includes('earnings')
        ? 'earnings_window'
        : nextEventName
          ? 'macro_window'
          : 'event_window')

  return {
    ...rawContext,
    event_risk: Boolean(rawContext.event_risk ?? context.event_risk),
    event_window_label: eventWindowLabel,
    primary_event_label:
      String(rawContext.primary_event_label || context.event_label || '').trim() ||
      (eventWindowLabel === 'earnings_window'
        ? 'Earnings window'
        : eventWindowLabel === 'macro_window'
          ? 'Macro window'
          : eventWindowLabel === 'corporate_window'
            ? 'Corporate window'
            : Boolean(rawContext.event_risk ?? context.event_risk)
              ? 'Event risk'
              : 'Quiet window'),
    summary:
      String(rawContext.summary || context.event_summary || context.event_reason || '').trim() ||
      (nextEventName
        ? `${nextEventName} is the next known catalyst.`
        : 'No near-term catalyst window is active.'),
    trade_posture: String(rawContext.trade_posture || context.trade_posture || '').trim().toLowerCase(),
    next_event_name: nextEventName,
    next_event_date: nextEventDate,
    next_event_days: toNumber(rawContext.next_event_days ?? context.next_event_days),
  }
}

function alertEventTone(eventContext) {
  if (Boolean(eventContext?.event_risk) || String(eventContext?.trade_posture || '').trim().toLowerCase() === 'defer') {
    return 'negative'
  }
  if (String(eventContext?.trade_posture || '').trim().toLowerCase() === 'caution') return 'warning'
  return 'positive'
}

function buildAlertFrame(alert) {
  const source = String(alert?.source || '').trim().toLowerCase()
  const severity = String(alert?.severity || 'low').trim().toLowerCase()
  const context = alert?.context || {}
  const eventContext = resolveAlertEventContext(alert)
  const eventRisk = Boolean(eventContext.event_risk)
  const daysUntil = toNumber(context.days_until)
  const regimeStrengthScore = toNumber(context.regime_strength_score)

  if (source === 'macro_calendar') {
    return {
      label: 'Macro window',
      tone: daysUntil !== null && daysUntil <= 3 ? 'negative' : 'warning',
      detail:
        daysUntil === null
          ? 'Scheduled macro event. Treat conditional forecasts as more fragile around the release window.'
          : `${daysUntil} day${daysUntil === 1 ? '' : 's'} until the macro release. Liquidity and discount-rate sensitivity can change quickly into the event.`,
    }
  }

  if (eventRisk) {
    return {
      label: eventContext.primary_event_label || 'Event window',
      tone: alertEventTone(eventContext),
      detail: eventContext.summary || 'Known catalyst window is active, so gap risk and spread risk are elevated for this setup.',
    }
  }

  if (String(eventContext.trade_posture || '').trim().toLowerCase() === 'caution') {
    return {
      label: eventContext.primary_event_label || 'Event watch',
      tone: 'warning',
      detail: eventContext.summary || 'A known catalyst is close enough to make this setup more conditional than a normal quiet-window trade.',
    }
  }

  if (source === 'trade_monitor') {
    return {
      label: 'Live position risk',
      tone: severity === 'critical' ? 'negative' : severity === 'high' ? 'warning' : 'info',
      detail: 'This alert is tied to an open position or monitor rule, so the focus is position management rather than a new forecast.',
    }
  }

  return {
    label: 'Setup review',
    tone: severity === 'high' ? 'warning' : 'info',
    detail:
      regimeStrengthScore === null
        ? 'Review the setup under its current session and route context before acting.'
        : `Regime strength is ${formatPercent(regimeStrengthScore)}. Lower values mean the setup should be treated as more conditional live.`,
  }
}

function buildAlertCalendarPressure(alert) {
  const source = String(alert?.source || '').trim().toLowerCase()
  const context = alert?.context || {}
  const eventContext = resolveAlertEventContext(alert)
  const eventRisk = Boolean(eventContext?.event_risk)
  const nextEventName = String(eventContext?.next_event_name || alert?.title || '').trim()
  const nextEventDays =
    toNumber(eventContext?.next_event_days) ?? toNumber(context?.days_until)

  if (source === 'macro_calendar') {
    const timingLabel =
      nextEventDays === null ? 'on deck' : nextEventDays === 0 ? 'today' : nextEventDays === 1 ? '1d' : `${nextEventDays}d`
    const tone =
      nextEventDays === 0 ? 'negative' : nextEventDays !== null && nextEventDays <= 3 ? 'warning' : 'info'
    return {
      active: true,
      label: `Macro ${timingLabel}`,
      tone,
      daysUntil: nextEventDays,
      detail: `${alert?.title || 'Macro event'} is close enough to shape route quality and opening posture across the board.`,
    }
  }

  if (!nextEventName) {
    return {
      active: false,
      label: 'Quiet queue',
      tone: 'info',
      daysUntil: null,
      detail: 'No near-term catalyst is pressuring this alert right now.',
    }
  }

  const windowLabel = String(eventContext?.event_window_label || '').trim().toLowerCase()
  const baseLabel =
    windowLabel === 'earnings_window'
      ? 'Earnings'
      : windowLabel === 'macro_window'
        ? 'Macro'
        : windowLabel === 'corporate_window'
          ? 'Corporate'
          : 'Catalyst'
  const timingLabel =
    nextEventDays === null ? 'on deck' : nextEventDays === 0 ? 'today' : nextEventDays === 1 ? '1d' : `${nextEventDays}d`
  const tone =
    eventRisk || String(eventContext?.trade_posture || '').trim().toLowerCase() === 'defer' || nextEventDays === 0
      ? 'negative'
      : nextEventDays !== null && nextEventDays <= 3
        ? 'warning'
        : alertEventTone(eventContext)

  return {
    active: true,
    label: `${baseLabel} ${timingLabel}`,
    tone,
    daysUntil: nextEventDays,
    detail: `${nextEventName} is close enough that this alert should stay conditional until the catalyst window clears.`,
  }
}

function buildAlertTrustFrame(alert) {
  const source = String(alert?.source || '').trim().toLowerCase()
  const context = alert?.context || {}
  const forecastConfidence = toNumber(context.forecast_confidence)
  const regimeStrengthScore = toNumber(context.regime_strength_score)
  const eventProbabilityShift = toNumber(context.event_probability_shift)

  if (source === 'macro_calendar') {
    return {
      label: 'Context only',
      tone: 'info',
      detail: 'Macro alerts are governance context for the rest of the book, not direct predictive conviction on their own.',
    }
  }

  let score = 0
  if (forecastConfidence !== null) {
    if (forecastConfidence >= 0.62) score += 1
    else if (forecastConfidence < 0.48) score -= 1
  }
  if (regimeStrengthScore !== null) {
    if (regimeStrengthScore >= 0.6) score += 1
    else if (regimeStrengthScore < 0.45) score -= 1
  }
  if (eventProbabilityShift !== null && Math.abs(eventProbabilityShift) >= 0.08) score -= 1

  if (score >= 2) {
    return {
      label: 'High trust',
      tone: 'positive',
      detail: 'Confidence and regime support are both holding up, so this alert is closer to an actionable forecast than a thin prompt.',
    }
  }
  if (score >= 0) {
    return {
      label: 'Conditional',
      tone: 'warning',
      detail: 'There is enough support to keep this on the tape, but not enough to skip execution discipline and review.',
    }
  }
  return {
    label: 'Fragile',
    tone: 'negative',
    detail: 'Thin confidence or weak regime support means this alert should be treated as a watchlist nudge, not a high-conviction route.',
  }
}

function buildAlertExecutionFrame(alert) {
  const source = String(alert?.source || '').trim().toLowerCase()
  const context = alert?.context || {}
  const spreadPct = toNumber(context.spread_pct)
  const volume = toNumber(context.volume)
  const openInterest = toNumber(context.open_interest)

  if (source === 'macro_calendar') {
    return {
      label: 'No route',
      tone: 'info',
      detail: 'Macro alerts set context for execution elsewhere. They are not directly routeable by themselves.',
    }
  }

  let score = 0
  if (spreadPct !== null) {
    if (spreadPct <= 6) score += 1
    else if (spreadPct > 12) score -= 1
  }
  if (volume !== null && openInterest !== null) {
    if (volume >= 100 && openInterest >= 500) score += 1
    else if (volume < 25 || openInterest < 100) score -= 1
  }

  if (score >= 2) {
    return {
      label: 'Execution clean',
      tone: 'positive',
      detail: 'Spread and participation are supportive enough that execution drag should be manageable.',
    }
  }
  if (score >= 0) {
    return {
      label: 'Use price control',
      tone: 'warning',
      detail: 'The setup is tradable, but the fill is sensitive enough that priced routing should matter.',
    }
  }
  return {
    label: 'Fragile fills',
    tone: 'negative',
    detail: 'Wide spreads or thin participation can overwhelm a modest forecast edge here.',
  }
}

function buildAlertTargetQualityFrame(alert) {
  const source = String(alert?.source || '').trim().toLowerCase()
  const context = alert?.context || {}
  const resolvedCount = toNumber(context.resolved_count)
  const averageError = toNumber(context.average_error)
  const empiricalHitRate = toNumber(context.empirical_hit_rate)
  const averageProbabilityUp = toNumber(context.average_probability_up)
  const calibrationScope = formatLabel(context.calibration_scope || 'unknown')
  const edge = empiricalHitRate !== null && averageProbabilityUp !== null
    ? empiricalHitRate - averageProbabilityUp
    : null

  if (source === 'macro_calendar') {
    return {
      label: 'No sample',
      tone: 'info',
      detail: 'Macro alerts are contextual by nature and do not carry a calibration sample for the specific setup.',
    }
  }

  if (
    resolvedCount !== null &&
    resolvedCount >= 20 &&
    averageError !== null &&
    averageError <= 0.18 &&
    edge !== null &&
    edge >= 0
  ) {
    return {
      label: 'Established',
      tone: 'positive',
      detail: `${calibrationScope} calibration has enough resolved history to be treated as a repeatable edge check instead of a fresh pattern.`,
    }
  }

  if (
    resolvedCount !== null &&
    resolvedCount >= 8 &&
    averageError !== null &&
    averageError <= 0.24
  ) {
    return {
      label: 'Developing',
      tone: 'warning',
      detail: `${calibrationScope} calibration is live and usable, but the sample is still maturing and should not be over-trusted.`,
    }
  }

  return {
    label: 'Thin sample',
    tone: 'negative',
    detail: 'Resolved history is still too thin to treat this alert as proof of a durable recurring edge.',
  }
}

function buildAlertDriftFrame(alert) {
  const source = String(alert?.source || '').trim().toLowerCase()
  const context = alert?.context || {}
  const forecastConfidence = toNumber(context.forecast_confidence)
  const regimeStrengthScore = toNumber(context.regime_strength_score)
  const resolvedCount = toNumber(context.resolved_count)
  const averageError = toNumber(context.average_error)
  const empiricalHitRate = toNumber(context.empirical_hit_rate)
  const averageProbabilityUp = toNumber(context.average_probability_up)
  const eventProbabilityShift = toNumber(context.event_probability_shift)
  const edge =
    empiricalHitRate !== null && averageProbabilityUp !== null
      ? empiricalHitRate - averageProbabilityUp
      : null

  if (source === 'macro_calendar') {
    return {
      label: 'Monitor',
      tone: 'info',
      detail: 'Macro alerts change the environment around the forecast, but they are not a direct model-drift kill switch by themselves.',
    }
  }

  let riskFlags = 0
  if (forecastConfidence !== null && forecastConfidence < 0.48) riskFlags += 1
  if (regimeStrengthScore !== null && regimeStrengthScore < 0.45) riskFlags += 1
  if (averageError !== null && averageError > 0.24) riskFlags += 1
  if (edge !== null && edge < -0.03) riskFlags += 1
  if (eventProbabilityShift !== null && Math.abs(eventProbabilityShift) >= 0.08) riskFlags += 1

  if (
    riskFlags >= 3 ||
    (
      resolvedCount !== null &&
      resolvedCount >= 20 &&
      averageError !== null &&
      averageError > 0.24 &&
      edge !== null &&
      edge < -0.03
    )
  ) {
    return {
      label: 'Kill switch',
      tone: 'negative',
      detail: 'Support is degrading enough that this alert should be treated as a pause or heavy down-weight signal, not an active edge.',
    }
  }

  if (
    riskFlags >= 1 ||
    (averageError !== null && averageError > 0.18) ||
    (resolvedCount !== null && resolvedCount < 8)
  ) {
    return {
      label: 'Watch drift',
      tone: 'warning',
      detail: 'At least one support layer is slipping, so this alert needs tighter review before it earns live trust.',
    }
  }

  return {
    label: 'Stable',
    tone: 'positive',
    detail: 'The alert is not currently showing obvious model-drift stress across confidence, regime, and calibration.',
  }
}

function buildAlertBenchmarkFrame(alert) {
  const source = String(alert?.source || '').trim().toLowerCase()
  const context = alert?.context || {}
  const empiricalHitRate = toNumber(context.empirical_hit_rate)
  const averageProbabilityUp = toNumber(context.average_probability_up)
  const resolvedCount = toNumber(context.resolved_count)
  const calibrationScope = formatLabel(context.calibration_scope || 'unknown')
  const edge =
    empiricalHitRate !== null && averageProbabilityUp !== null
      ? empiricalHitRate - averageProbabilityUp
      : null

  if (source === 'macro_calendar') {
    return {
      label: 'Context baseline',
      tone: 'info',
      detail: 'Macro alerts set the environment around forecasts, so their benchmark is contextual rather than directional.',
    }
  }

  if (resolvedCount !== null && resolvedCount >= 8 && edge !== null) {
    return {
      label: `${calibrationScope} baseline`,
      tone: edge >= 0 ? 'positive' : 'negative',
      detail: `${formatPercent(empiricalHitRate)} resolved hit rate versus ${formatPercent(averageProbabilityUp)} model average in the active calibration sample.`,
    }
  }

  return {
    label: 'Neutral baseline',
    tone: 'warning',
    detail: 'Without enough resolved sample depth, the setup should at least beat a neutral directional baseline before it earns extra trust.',
  }
}

function buildAlertMemoryFrame(alert) {
  const source = String(alert?.source || '').trim().toLowerCase()
  const context = alert?.context || {}
  const marketRegime = String(context.market_regime || '').trim().toLowerCase()
  const bestRegime = String(context.best_regime || '').trim().toLowerCase()
  const weakestRegime = String(context.weakest_regime || '').trim().toLowerCase()
  const bestDriver = formatLabel(context.best_driver || 'unknown')
  const weakestDriver = formatLabel(context.weakest_driver || 'unknown')

  if (source === 'macro_calendar') {
    return {
      label: 'No memory',
      tone: 'info',
      detail: 'Macro alerts change the environment, but they do not carry regime-specific calibration memory for one setup.',
    }
  }

  if (marketRegime && weakestRegime && marketRegime === weakestRegime) {
    return {
      label: 'Weak regime memory',
      tone: 'negative',
      detail: `The active ${formatLabel(context.market_regime)} regime has been one of the weakest resolved states. ${weakestDriver} has also been the least supportive driver.`,
    }
  }

  if (marketRegime && bestRegime && marketRegime === bestRegime) {
    return {
      label: 'Known strong regime',
      tone: 'positive',
      detail: `The active ${formatLabel(context.market_regime)} regime has resolved well historically. ${bestDriver} has been the most supportive driver in that memory stack.`,
    }
  }

  if (bestRegime || weakestRegime || context.best_driver || context.weakest_driver) {
    return {
      label: 'Mixed memory',
      tone: 'warning',
      detail: `Best memory sits in ${formatLabel(context.best_regime || 'another regime')}, weakest in ${formatLabel(context.weakest_regime || 'another regime')}. Drivers are mixed between ${bestDriver} and ${weakestDriver}.`,
    }
  }

  return {
    label: 'No memory',
    tone: 'warning',
    detail: 'There is not enough resolved regime or driver history here to say where the edge usually holds up or breaks down.',
  }
}

function buildAlertEventMemoryFrame(alert) {
  const source = String(alert?.source || '').trim().toLowerCase()
  const context = alert?.context || {}
  const eventContext = resolveAlertEventContext(alert)
  const bestEventWindow = String(context.best_event_window || '').trim().toLowerCase()
  const weakestEventWindow = String(context.weakest_event_window || '').trim().toLowerCase()
  const activeEventWindow =
    source === 'macro_calendar'
      ? 'macro_window'
      : String(eventContext.event_window_label || '').trim().toLowerCase() || 'quiet_window'

  if (activeEventWindow && weakestEventWindow && activeEventWindow === weakestEventWindow) {
    return {
      label: 'Weak event memory',
      tone: 'negative',
      detail: `The active ${formatLabel(activeEventWindow)} state has historically been one of the weakest resolved event windows for this setup.`,
    }
  }

  if (activeEventWindow && bestEventWindow && activeEventWindow === bestEventWindow) {
    return {
      label: 'Known strong event window',
      tone: 'positive',
      detail: `The active ${formatLabel(activeEventWindow)} state has historically been one of the most supportive event windows for this setup.`,
    }
  }

  if (bestEventWindow || weakestEventWindow) {
    return {
      label: 'Mixed event memory',
      tone: 'warning',
      detail: `Best event memory sits in ${formatLabel(bestEventWindow || 'another window')}, while ${formatLabel(weakestEventWindow || 'another window')} has been weaker.`,
    }
  }

  return {
    label: source === 'macro_calendar' ? 'No event memory' : 'No event memory',
    tone: source === 'macro_calendar' ? 'info' : 'warning',
    detail:
      source === 'macro_calendar'
        ? 'Macro alerts change the environment, but there is not enough resolved event-window history yet to say how this setup behaves into macro releases.'
        : 'There is not enough resolved event-window history yet to say whether this edge behaves better in quiet, macro, or earnings conditions.',
  }
}

function buildAlertSessionMemoryFrame(alert) {
  const source = String(alert?.source || '').trim().toLowerCase()
  const context = alert?.context || {}
  const currentSession = getCurrentSessionLabel()
  const bestSession = String(context.best_session || '').trim().toLowerCase()
  const weakestSession = String(context.weakest_session || '').trim().toLowerCase()
  const activeSession = currentSession.toLowerCase().replaceAll('-', '_')

  if (source === 'macro_calendar') {
    return {
      label: 'No session memory',
      tone: 'info',
      detail: 'Macro alerts change the environment, but they do not carry a resolved session-memory stack for one setup.',
    }
  }

  if (activeSession && weakestSession && activeSession === weakestSession) {
    return {
      label: 'Weak session memory',
      tone: 'negative',
      detail: `The active ${currentSession} session has been one of the weakest resolved trading windows for this setup.`,
    }
  }

  if (activeSession && bestSession && activeSession === bestSession) {
    return {
      label: 'Known strong session',
      tone: 'positive',
      detail: `The active ${currentSession} session has historically been one of the most supportive windows for this setup.`,
    }
  }

  if (bestSession || weakestSession) {
    return {
      label: 'Mixed session memory',
      tone: 'warning',
      detail: `Best session memory sits in ${formatLabel(context.best_session || 'another session')}, while ${formatLabel(context.weakest_session || 'another session')} has been weaker.`,
    }
  }

  return {
    label: 'No session memory',
    tone: 'warning',
    detail: 'There is not enough resolved session history here to say whether this edge behaves better or worse outside regular hours.',
  }
}

function buildAlertDecisionGateFrame(alert) {
  const source = String(alert?.source || '').trim().toLowerCase()
  const context = alert?.context || {}

  if (source === 'macro_calendar') {
    return {
      label: 'Context only',
      tone: 'info',
      detail: 'Macro alerts shape the environment around a setup, but they are not standalone promotable trades by themselves.',
    }
  }

  const trustFrame = buildAlertTrustFrame(alert)
  const executionFrame = buildAlertExecutionFrame(alert)
  const targetQualityFrame = buildAlertTargetQualityFrame(alert)
  const driftFrame = buildAlertDriftFrame(alert)
  const benchmarkFrame = buildAlertBenchmarkFrame(alert)
  const eventMemoryFrame = buildAlertEventMemoryFrame(alert)
  const sessionMemoryFrame = buildAlertSessionMemoryFrame(alert)
  const memoryFrame = buildAlertMemoryFrame(alert)
  const normalizedDecision = String(context.decision || context.trade_decision || '').trim().toUpperCase()
  const blockingReasons = []
  const cautionReasons = []
  const supportTones = [eventMemoryFrame.tone, sessionMemoryFrame.tone, memoryFrame.tone]
  const supportNegativeCount = supportTones.filter((tone) => tone === 'negative').length
  const supportWarningCount = supportTones.filter((tone) => tone === 'warning').length

  if (normalizedDecision && normalizedDecision !== 'VALID TRADE') {
    blockingReasons.push(normalizedDecision === 'REJECT' ? 'model reject' : 'model not green-lit')
  }

  const coreChecks = [
    { tone: trustFrame.tone, negative: 'fragile trust', warning: 'conditional trust' },
    { tone: executionFrame.tone, negative: 'fragile execution', warning: 'execution still needs price control' },
    { tone: targetQualityFrame.tone, negative: 'thin sample', warning: 'sample still developing' },
    { tone: benchmarkFrame.tone, negative: 'below baseline', warning: 'benchmark edge is narrow' },
    { tone: driftFrame.tone, negative: 'kill switch', warning: 'drift under watch' },
  ]

  coreChecks.forEach(({ tone, negative, warning }) => {
    if (tone === 'negative') blockingReasons.push(negative)
    else if (tone === 'warning') cautionReasons.push(warning)
  })

  if (!blockingReasons.length && supportNegativeCount >= 2) {
    blockingReasons.push('multiple weak memory states')
  }
  if (!blockingReasons.length && supportNegativeCount === 1) {
    cautionReasons.push('one weak memory state')
  }
  if (supportWarningCount) {
    cautionReasons.push(
      supportWarningCount === 1
        ? 'memory is mixed'
        : 'multiple memory layers are mixed',
    )
  }

  if (blockingReasons.length) {
    return {
      label: 'Stand down',
      tone: 'negative',
      detail: `Blocked by ${blockingReasons.slice(0, 2).join(' and ')}${blockingReasons.length > 2 ? ', plus more.' : '.'}`,
    }
  }

  const coreAllPositive =
    normalizedDecision === 'VALID TRADE' &&
    trustFrame.tone === 'positive' &&
    executionFrame.tone === 'positive' &&
    targetQualityFrame.tone === 'positive' &&
    benchmarkFrame.tone === 'positive' &&
    driftFrame.tone === 'positive'

  if (coreAllPositive && supportNegativeCount === 0 && supportWarningCount === 0) {
    return {
      label: 'Gate cleared',
      tone: 'positive',
      detail: 'Trust, execution, sample quality, benchmark edge, drift, and historical memory are clearing together for this setup.',
    }
  }

  return {
    label: 'Review gate',
    tone: 'warning',
    detail: cautionReasons.length
      ? `${cautionReasons.length} review flag${cautionReasons.length === 1 ? '' : 's'}: ${cautionReasons.slice(0, 2).join(' and ')}${cautionReasons.length > 2 ? ', plus more.' : '.'}`
      : 'The setup is usable, but the full stack is not clearing together strongly enough to auto-promote.',
  }
}

function buildAlertCandidateQueue(alerts = []) {
  const normalizedAlerts = (Array.isArray(alerts) ? alerts : [])
    .map((alert) => ({
      alert,
      gate: buildAlertDecisionGateFrame(alert),
      trust: buildAlertTrustFrame(alert),
      execution: buildAlertExecutionFrame(alert),
      calendar: buildAlertCalendarPressure(alert),
    }))
    .filter((item) => String(item.alert?.source || '').trim().toLowerCase() !== 'macro_calendar')
    .sort((left, right) => {
      const toneRank = { positive: 0, warning: 1, negative: 2, info: 3 }
      const leftRank = toneRank[left.gate.tone] ?? 4
      const rightRank = toneRank[right.gate.tone] ?? 4
      if (leftRank !== rightRank) return leftRank - rightRank
      const leftCalendarRank = toneRank[left.calendar.tone] ?? 4
      const rightCalendarRank = toneRank[right.calendar.tone] ?? 4
      if (leftCalendarRank !== rightCalendarRank) return leftCalendarRank - rightCalendarRank
      const leftSeverity = String(left.alert?.severity || '').trim().toLowerCase()
      const rightSeverity = String(right.alert?.severity || '').trim().toLowerCase()
      const severityRank = { critical: 0, high: 1, medium: 2, low: 3 }
      const leftSeverityRank = severityRank[leftSeverity] ?? 4
      const rightSeverityRank = severityRank[rightSeverity] ?? 4
      if (leftSeverityRank !== rightSeverityRank) return leftSeverityRank - rightSeverityRank
      return String(left.alert?.ticker || left.alert?.title || '').localeCompare(String(right.alert?.ticker || right.alert?.title || ''))
    })

  const promoted = normalizedAlerts.filter((item) => item.gate.tone === 'positive').slice(0, 3)
  if (promoted.length) {
    return { mode: 'promote', rows: promoted }
  }

  return {
    mode: 'review',
    rows: normalizedAlerts.filter((item) => item.gate.tone === 'warning').slice(0, 3),
  }
}

function buildAlertContextRows(alert) {
  const context = alert?.context || {}
  const eventContext = resolveAlertEventContext(alert)
  const rows = []

  if (context.decision) {
    rows.push({ key: 'decision', label: 'Decision', value: formatLabel(context.decision) })
  }
  if (eventContext.primary_event_label || eventContext.summary) {
    rows.push({
      key: 'event',
      label: 'Event window',
      value: eventContext.primary_event_label || eventContext.summary || '--',
    })
  }
  if (eventContext.trade_posture) {
    rows.push({ key: 'posture', label: 'Trade posture', value: formatLabel(eventContext.trade_posture) })
  }
  if (context.market_regime) {
    rows.push({ key: 'regime', label: 'Regime', value: formatLabel(context.market_regime) })
  }
  if (toNumber(context.regime_strength_score) !== null) {
    rows.push({
      key: 'regime-strength',
      label: 'Regime strength',
      value: formatPercent(context.regime_strength_score),
    })
  }
  if (toNumber(context.forecast_confidence) !== null) {
    rows.push({
      key: 'forecast-confidence',
      label: 'Forecast confidence',
      value: formatPercent(context.forecast_confidence),
    })
  }
  if (toNumber(context.setup_score) !== null) {
    rows.push({ key: 'setup-score', label: 'Setup score', value: Number(context.setup_score).toFixed(1) })
  }
  if (toNumber(context.resolved_count) !== null) {
    rows.push({ key: 'resolved', label: 'Resolved', value: Math.round(Number(context.resolved_count)).toLocaleString() })
  }
  if (toNumber(context.empirical_hit_rate) !== null) {
    rows.push({ key: 'hit-rate', label: 'Hit rate', value: formatPercent(context.empirical_hit_rate) })
  }
  if (toNumber(context.average_error) !== null) {
    rows.push({ key: 'avg-error', label: 'Avg error', value: formatPercent(context.average_error, { digits: 2 }) })
  }
  if (context.calibration_scope) {
    rows.push({ key: 'scope', label: 'Scope', value: formatLabel(context.calibration_scope) })
  }
  if (toNumber(context.spread_pct) !== null) {
    rows.push({ key: 'spread', label: 'Spread', value: formatPercent(context.spread_pct, { ratio: false }) })
  }
  if (toNumber(context.volume) !== null) {
    rows.push({ key: 'volume', label: 'Volume', value: Math.round(Number(context.volume)).toLocaleString() })
  }
  if (toNumber(context.open_interest) !== null) {
    rows.push({ key: 'open-interest', label: 'Open interest', value: Math.round(Number(context.open_interest)).toLocaleString() })
  }
  if (context.event_date) {
    rows.push({ key: 'event-date', label: 'Event date', value: String(context.event_date) })
  }
  if (eventContext.next_event_date) {
    rows.push({ key: 'next-event-date', label: 'Next catalyst', value: String(eventContext.next_event_date) })
  }
  if (toNumber(context.days_until) !== null) {
    const daysUntil = Number(context.days_until)
    rows.push({
      key: 'days-until',
      label: 'Days until',
      value: `${daysUntil} day${daysUntil === 1 ? '' : 's'}`,
    })
  }
  if (context.target_price != null) {
    rows.push({ key: 'target', label: 'Target', value: String(context.target_price) })
  }
  if (context.stop_loss != null) {
    rows.push({ key: 'stop', label: 'Stop', value: String(context.stop_loss) })
  }
  if (context.monitor_action) {
    rows.push({ key: 'monitor-action', label: 'Monitor action', value: formatLabel(context.monitor_action) })
  }
  if (context.pnl_dollars != null) {
    rows.push({ key: 'pnl', label: 'PnL', value: String(context.pnl_dollars) })
  }

  return rows.slice(0, 6)
}

export default function AlertsPage() {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [alertsPayload, setAlertsPayload] = useState(null)
  const [tradeSummary, setTradeSummary] = useState(null)
  const [bootstrap, setBootstrap] = useState(null)
  const [filters, setFilters] = useState({
    alert_severities: ['all', 'critical', 'high', 'medium', 'low'],
    alert_sources: ['all', 'watchlist', 'trade_monitor', 'macro_calendar'],
  })
  const [severity, setSeverity] = useState('all')
  const [source, setSource] = useState('all')
  const [search, setSearch] = useState('')
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [lastUpdated, setLastUpdated] = useState('')
  const { preferences } = usePreferences()
  const candidateQueueNavigation = useKeyboardListNavigation({ selector: '.candidate-queue__item', layout: 'grid' })

  usePageActionShortcuts({
    focusInput: () => focusFirstMatching(['#alerts-search-input']),
    focusResult: () => focusFirstMatching(['.candidate-queue__grid .candidate-queue__item']),
  })

  const loadAlerts = useCallback(async () => {
    try {
      setError('')
      const [payload, tradeSummaryPayload] = await Promise.all([
        getFrontendAlerts({ limit: 30, minSeverity: severity, search, source }),
        getTradeSummary(),
      ])
      setAlertsPayload(payload)
      setTradeSummary(tradeSummaryPayload)
      setLastUpdated(new Date().toLocaleTimeString())
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load alerts.')
    } finally {
      setLoading(false)
    }
  }, [severity, search, source])

  useEffect(() => {
    Promise.all([getBootstrap('alerts'), getFrontendFilters()])
      .then(([bootstrapPayload, filterPayload]) => {
        setBootstrap(bootstrapPayload)
        setFilters(filterPayload)
      })
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    loadAlerts()
  }, [loadAlerts])
  usePolling(loadAlerts, preferences?.pollingMs || 15000, autoRefresh)

  const metrics = useMemo(() => {
    const severityCounts = alertsPayload?.severity_counts || {}
    const alerts = alertsPayload?.alerts || []
    const calendarFrames = alerts.map((alert) => buildAlertCalendarPressure(alert))
    const eventWindows = alerts.filter((alert) => alert.source === 'macro_calendar' || alert.context?.event_risk).length
    const activeCalendarPressure = calendarFrames.filter((frame) => frame.active).length
    const urgentCalendarPressure = calendarFrames.filter((frame) => frame.active && frame.daysUntil !== null && frame.daysUntil <= 3).length
    const macroOnDeck = alerts.filter((alert) => String(alert.source || '').trim().toLowerCase() === 'macro_calendar').length
    const monitorAlerts = alerts.filter((alert) => String(alert.source || '').toLowerCase() === 'trade_monitor').length
    const fragileForecasts = alerts.filter((alert) => buildAlertTrustFrame(alert).tone === 'negative').length
    const fragileExecution = alerts.filter((alert) => buildAlertExecutionFrame(alert).tone === 'negative').length
    const thinSamples = alerts.filter((alert) => buildAlertTargetQualityFrame(alert).tone === 'negative').length
    const killSwitches = alerts.filter((alert) => buildAlertDriftFrame(alert).tone === 'negative').length
    const belowBaseline = alerts.filter((alert) => buildAlertBenchmarkFrame(alert).tone === 'negative').length
    const weakMemory = alerts.filter((alert) => buildAlertMemoryFrame(alert).tone === 'negative').length
    const weakEventMemory = alerts.filter((alert) => buildAlertEventMemoryFrame(alert).tone === 'negative').length
    const weakSessionMemory = alerts.filter((alert) => buildAlertSessionMemoryFrame(alert).tone === 'negative').length
    const gateCleared = alerts.filter((alert) => buildAlertDecisionGateFrame(alert).tone === 'positive').length
    return [
      { label: 'Visible Alerts', value: alertsPayload?.count ?? 0 },
      { label: 'Critical', value: severityCounts.critical ?? 0, tone: Number(severityCounts.critical || 0) > 0 ? 'negative' : 'default' },
      { label: 'Event windows', value: eventWindows, tone: eventWindows > 0 ? 'warning' : 'default' },
      { label: 'Calendar pressure', value: activeCalendarPressure, helper: `${urgentCalendarPressure} inside 3 days`, tone: activeCalendarPressure > 0 ? 'warning' : 'default' },
      { label: 'Macro on deck', value: macroOnDeck, tone: macroOnDeck > 0 ? 'warning' : 'default' },
      { label: 'Fragile forecasts', value: fragileForecasts, tone: fragileForecasts > 0 ? 'warning' : 'default' },
      { label: 'Fragile fills', value: fragileExecution, tone: fragileExecution > 0 ? 'warning' : 'default' },
      { label: 'Thin samples', value: thinSamples, tone: thinSamples > 0 ? 'warning' : 'default' },
      { label: 'Kill switches', value: killSwitches, tone: killSwitches > 0 ? 'negative' : 'default' },
      { label: 'Below baseline', value: belowBaseline, tone: belowBaseline > 0 ? 'warning' : 'default' },
      { label: 'Gate cleared', value: gateCleared, tone: gateCleared > 0 ? 'positive' : 'default' },
      { label: 'Weak event windows', value: weakEventMemory, tone: weakEventMemory > 0 ? 'warning' : 'default' },
      { label: 'Weak sessions', value: weakSessionMemory, tone: weakSessionMemory > 0 ? 'warning' : 'default' },
      { label: 'Weak memory', value: weakMemory, tone: weakMemory > 0 ? 'warning' : 'default' },
      { label: 'Trade monitor', value: monitorAlerts, tone: monitorAlerts > 0 ? 'warning' : 'default' },
      { label: 'Updated', value: lastUpdated || '--' },
    ]
  }, [alertsPayload, lastUpdated])
  const candidateQueue = useMemo(
    () => buildAlertCandidateQueue(alertsPayload?.alerts || []),
    [alertsPayload],
  )
  const topCandidate = candidateQueue.rows[0] || null
  const alertBoardUrl = useMemo(
    () => buildAlertsCompareUrl(candidateQueue.rows.map((item) => item.alert)),
    [candidateQueue],
  )
  const rolloutReadiness = useMemo(
    () => buildRolloutReadinessSummary(tradeSummary?.rollout_readiness),
    [tradeSummary?.rollout_readiness],
  )
  const effectiveExecutionIntent = resolveAccountProfileExecutionIntent({
    activeAccountProfile: normalizeAccountProfile(preferences?.activeAccountProfile),
    defaultExecutionIntent: preferences?.defaultExecutionIntent,
  })
  const selectedExecutionRouteLabel = formatExecutionIntentLabel(effectiveExecutionIntent)
  const selectedExecutionRouteTone =
    effectiveExecutionIntent === 'broker_live' && !rolloutReadiness.allowsLiveRollout
      ? 'negative'
      : effectiveExecutionIntent === 'broker_live'
        ? 'positive'
        : effectiveExecutionIntent === 'broker_paper'
          ? 'warning'
          : 'info'
  const topCandidateAction = topCandidate
    ? String(topCandidate.alert?.source || '').trim().toLowerCase() === 'trade_monitor'
      ? {
          label: 'Open live risk on trades',
          onClick: () => navigate('/trades'),
        }
      : String(topCandidate.alert?.ticker || '').trim()
        ? {
            label: `Open ${String(topCandidate.alert?.ticker || '').trim().toUpperCase()} on desk`,
            onClick: () => navigate(buildAlertDeskUrl(topCandidate.alert)),
          }
        : {
            label: 'Open alert board',
            onClick: () => navigate(alertBoardUrl),
          }
    : null

  function openAlertCandidate(alert) {
    const normalizedSource = String(alert?.source || '').trim().toLowerCase()
    if (normalizedSource === 'trade_monitor') {
      navigate('/trades')
      return
    }
    if (String(alert?.ticker || '').trim()) {
      navigate(buildAlertDeskUrl(alert))
      return
    }
    navigate(alertBoardUrl)
  }
  const calendarAction =
    Number(metrics.find((item) => item.label === 'Macro on deck')?.value || 0) > 0 ||
    Number(metrics.find((item) => item.label === 'Calendar pressure')?.value || 0) > 0
      ? {
          label: 'Open alert board',
          onClick: () => navigate(alertBoardUrl),
        }
      : Number(metrics.find((item) => item.label === 'Trade monitor')?.value || 0) > 0
        ? {
            label: 'Open live risk on trades',
            onClick: () => navigate('/trades'),
          }
        : {
            label: 'Open alert board',
            onClick: () => navigate(alertBoardUrl),
          }

  if (loading) {
    return (
      <LoadingBlock
        label="Loading alerts feed"
        detail="Collecting alert-driven setups, macro pressure, and monitor signals so the queue opens with the latest interruptions."
      />
    )
  }

  return (
    <>
      {error ? (
        <ErrorState
          title="Alerts feed unavailable"
          description={error}
          actionLabel="Reload alerts"
          onAction={loadAlerts}
        />
      ) : null}
      <PageIntro
        kicker="Alerts center"
        title={bootstrap?.app?.name || 'Trading Alerts'}
        description="Action-oriented alerts across trade monitor, watchlist setups, and macro events, now framed by event windows and conditional risk."
        helper="Start with the candidate queue, then drop into the full alert tape only when the queue still needs more context."
        badge={`Updated ${lastUpdated || '--'}`}
        actions={(
          <ActionBar compact>
            <Chip tone="neutral" size="sm">/ focus alert search</Chip>
            <Chip tone="neutral" size="sm">Shift+J jump to queue</Chip>
            <Button type="button" variant="subtle" onClick={loadAlerts}>
              Refresh alerts
            </Button>
          </ActionBar>
        )}
      />
      <WorkflowGuide
        showSteps={false}
        phaseLabel="Phase 2 - Qualify"
        phaseTone="warning"
        title="Treat alerts as triage, not as automatic trade commands."
        description="This surface should help you sort interruptions into act now, review next, or wait. Alerts are strongest when they preserve the desk hierarchy instead of replacing it."
        steps={buildWorkflowSteps(1)}
        cards={[
          {
            label: 'Use this page for',
            value: 'Triage what changed without losing the board context.',
            detail: 'Read watchlist, macro, and live-position alerts as routing cues that either support or interrupt the current desk plan.',
            actionLabel: 'Open alert board',
            onAction: () => navigate(alertBoardUrl),
          },
          {
            label: 'Best next move',
            value: 'Escalate only the alerts that match the current queue and gate state.',
            detail: 'When an alert reinforces an existing leader or exposes live risk, it deserves attention first.',
            tone: 'positive',
            actionLabel: topCandidateAction?.label,
            onAction: topCandidateAction?.onClick,
            actionDisabled: !topCandidateAction,
          },
          {
            label: 'Do not ignore',
            value: 'Macro windows can invalidate otherwise clean setups.',
            detail: 'A strong candidate should still slow down when the alert is telling you that timing, spread, or regime conditions are changing.',
            tone: 'warning',
            actionLabel: calendarAction.label,
            onAction: calendarAction.onClick,
          },
        ]}
      />
      <section className="metrics-grid">{metrics.map((item) => <MetricCard key={item.label} {...item} />)}</section>
      <SectionCard
        title="Live readiness"
        subtitle="Shared live readiness and the current route posture for this desk."
      >
        <section className="metrics-grid">
          <MetricCard
            label="Selected route"
            value={selectedExecutionRouteLabel}
            tone={selectedExecutionRouteTone}
            helper={
              effectiveExecutionIntent === 'broker_live' && !rolloutReadiness.allowsLiveRollout
    ? 'Alpaca live routing is selected but still locked by live readiness.'
                : effectiveExecutionIntent === 'broker_live'
      ? 'Alpaca live routing is selected and the live gate is clear.'
                  : effectiveExecutionIntent === 'broker_paper'
                    ? 'Alerts are still feeding a connected-paper-first desk.'
                    : 'Alerts are feeding a local desk-first route.'
            }
          />
          {rolloutReadiness.cards.map((item) => <MetricCard key={`rollout-${item.label}`} {...item} />)}
        </section>
        <div className="ui-panel ui-panel--section">
          <div className="ui-panel__kicker">Live gate</div>
          <div className="ui-panel__title">{rolloutReadiness.label}</div>
          <div className="ui-panel__note">
            {rolloutReadiness.detail}
          </div>
          <div className="inline-meta-list">
            <span className="inline-meta-list__item">
              <strong>Unlock:</strong> {rolloutReadiness.unlockSummary}
            </span>
            <span className="inline-meta-list__item">
              <strong>Next check:</strong> {rolloutReadiness.nextCheckDetail}
            </span>
            <span className="inline-meta-list__item">
              <strong>Trend:</strong> {rolloutReadiness.historyLabel}
            </span>
            <span className="inline-meta-list__item">
              <strong>Route now:</strong> {selectedExecutionRouteLabel}
            </span>
          </div>
          {rolloutReadiness.historyItems.length ? (
            <div className="inline-meta-list">
              {rolloutReadiness.historyItems.slice(-3).map((item) => (
                <span key={item.key} className="inline-meta-list__item">
                  <strong>{item.recordedLabel}</strong> {item.label} | {item.replayWinRate} replay | {item.averageAbsSlippage}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </SectionCard>
      <SectionCard
        eyebrow="Priority first"
        title="Candidate queue"
        subtitle={
          candidateQueue.mode === 'promote'
            ? 'These alerts are clearing the gate strongly enough to deserve first attention, with calendar pressure still visible alongside the rank.'
            : 'No alerts are fully clearing yet, so this queue falls back to the strongest review candidates and keeps calendar pressure explicit.'
        }
      >
        {candidateQueue.rows.length ? (
          <div
            ref={candidateQueueNavigation.containerRef}
            className="candidate-queue__grid"
            onKeyDown={candidateQueueNavigation.onKeyDown}
          >
            {candidateQueue.rows.map(({ alert, gate, trust, execution, calendar }, index) => (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className={`candidate-queue__item candidate-queue__item--${gate.tone}`}
                key={`${alert.source}-${alert.ticker || alert.title}-${index}`}
                onClick={() => openAlertCandidate(alert)}
              >
                <div className="candidate-queue__meta">
                  <strong>{alert.ticker || alert.title}</strong>
                  <span className={`execution-state-badge execution-state-badge--${gate.tone}`}>{gate.label}</span>
                </div>
                <div className="ui-list-cell__badges">
                  <StatusBadge tone={trust.tone}>{trust.label}</StatusBadge>
                  <StatusBadge tone={execution.tone}>{execution.label}</StatusBadge>
                  {calendar.active ? <StatusBadge tone={calendar.tone}>{calendar.label}</StatusBadge> : null}
                  <StatusBadge tone="neutral">{formatLabel(alert.source || 'alert')}</StatusBadge>
                </div>
                <div className="candidate-queue__stack">
                  <span>{alert.message}</span>
                  <span>{gate.detail}</span>
                  {calendar.active ? <span>{calendar.detail}</span> : null}
                </div>
              </Button>
            ))}
          </div>
        ) : (
          <EmptyState
            title="No alert-driven candidates"
            description="No alert-driven candidates are ready for the queue yet."
          />
        )}
      </SectionCard>
      <SectionCard
        eyebrow="Full alert tape"
        title="Active alerts"
        subtitle="Filtered alert stream with clearer event, macro, and live-risk framing."
        actions={(
          <DataToolbar
            searchInputId="alerts-search-input"
            searchValue={search}
            onSearchChange={setSearch}
            searchPlaceholder="Search alerts"
            actions={(
              <>
                <SelectField label="Severity" value={severity} onChange={(e) => setSeverity(e.target.value)}>
                  {(filters.alert_severities || ['all']).map((option) => <option key={option} value={option}>{option}</option>)}
                </SelectField>
                <SelectField label="Source" value={source} onChange={(e) => setSource(e.target.value)}>
                  {(filters.alert_sources || ['all']).map((option) => <option key={option} value={option}>{option}</option>)}
                </SelectField>
                <ToggleField
                  label="Auto refresh"
                  hint="Keep the alert tape polling during the session."
                  checked={autoRefresh}
                  onChange={(e) => setAutoRefresh(e.target.checked)}
                />
                <Button type="button" variant="ghost" onClick={loadAlerts}>
                  Refresh
                </Button>
              </>
            )}
          />
        )}
      >
        <div className="alerts-list">
          {(alertsPayload?.alerts || []).map((alert, index) => {
            const frame = buildAlertFrame(alert)
            const calendarFrame = buildAlertCalendarPressure(alert)
            const trustFrame = buildAlertTrustFrame(alert)
            const executionFrame = buildAlertExecutionFrame(alert)
            const targetQualityFrame = buildAlertTargetQualityFrame(alert)
            const driftFrame = buildAlertDriftFrame(alert)
            const benchmarkFrame = buildAlertBenchmarkFrame(alert)
            const memoryFrame = buildAlertMemoryFrame(alert)
            const eventMemoryFrame = buildAlertEventMemoryFrame(alert)
            const sessionMemoryFrame = buildAlertSessionMemoryFrame(alert)
            const decisionGateFrame = buildAlertDecisionGateFrame(alert)
            const contextRows = buildAlertContextRows(alert)
            return (
              <article className={`alert-card alert-card--${String(alert.severity || 'low').toLowerCase()}`} key={`${alert.source}-${alert.ticker || 'market'}-${index}`}>
                <div className="alert-card__head">
                  <div>
                    <Kicker as="div">{alert.source}</Kicker>
                    <h3>{alert.title}</h3>
                  </div>
                  <Chip
                    tone={severityTone(alert.severity)}
                    size="sm"
                    className={`alert-chip alert-chip--${String(alert.severity || 'low').toLowerCase()}`}
                  >
                    {alert.severity}
                  </Chip>
                </div>
                <div className="alert-card__context-row">
                  <span className={`execution-state-badge execution-state-badge--${frame.tone}`}>{frame.label}</span>
                  {calendarFrame.active ? (
                    <span className={`execution-state-badge execution-state-badge--${calendarFrame.tone}`}>{calendarFrame.label}</span>
                  ) : null}
                  <span className={`execution-state-badge execution-state-badge--${decisionGateFrame.tone}`}>{decisionGateFrame.label}</span>
                  <span className={`execution-state-badge execution-state-badge--${trustFrame.tone}`}>{trustFrame.label}</span>
                  <span className={`execution-state-badge execution-state-badge--${executionFrame.tone}`}>{executionFrame.label}</span>
                  <span className={`execution-state-badge execution-state-badge--${targetQualityFrame.tone}`}>{targetQualityFrame.label}</span>
                  <span className={`execution-state-badge execution-state-badge--${eventMemoryFrame.tone}`}>{eventMemoryFrame.label}</span>
                  <span className={`execution-state-badge execution-state-badge--${sessionMemoryFrame.tone}`}>{sessionMemoryFrame.label}</span>
                  <span className={`execution-state-badge execution-state-badge--${memoryFrame.tone}`}>{memoryFrame.label}</span>
                  <span className={`execution-state-badge execution-state-badge--${benchmarkFrame.tone}`}>{benchmarkFrame.label}</span>
                  <span className={`execution-state-badge execution-state-badge--${driftFrame.tone}`}>{driftFrame.label}</span>
                  {alert.ticker ? (
                    <Chip tone="neutral" size="sm">
                      {alert.ticker}
                    </Chip>
                  ) : null}
                </div>
                <p>{alert.message}</p>
                <p className="alert-card__risk-note">{frame.detail}</p>
                {calendarFrame.active ? <p className="alert-card__risk-note">{calendarFrame.detail}</p> : null}
                <p className="alert-card__trust-note">{decisionGateFrame.detail}</p>
                <p className="alert-card__trust-note">{trustFrame.detail}</p>
                <p className="alert-card__trust-note">{executionFrame.detail}</p>
                <p className="alert-card__trust-note">{targetQualityFrame.detail}</p>
                <p className="alert-card__trust-note">{eventMemoryFrame.detail}</p>
                <p className="alert-card__trust-note">{sessionMemoryFrame.detail}</p>
                <p className="alert-card__trust-note">{memoryFrame.detail}</p>
                <p className="alert-card__trust-note">{benchmarkFrame.detail}</p>
                <p className="alert-card__trust-note">{driftFrame.detail}</p>
                {contextRows.length ? (
                  <div className="alert-meta-grid">
                    {contextRows.map((row) => (
                      <div className="workspace-summary-card" key={row.key}>
                        <span>{row.label}</span>
                        <strong>{row.value}</strong>
                      </div>
                    ))}
                  </div>
                ) : null}
              </article>
            )
          })}
          {!alertsPayload?.alerts?.length ? (
            <EmptyState
              title="No alerts matched"
              description="No alerts matched the selected filters."
            />
          ) : null}
        </div>
      </SectionCard>
    </>
  )
}
