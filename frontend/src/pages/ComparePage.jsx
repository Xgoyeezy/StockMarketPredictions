import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { compareTickers, getBootstrap, saveWorkspace } from '../api/client'
import ActionBar from '../components/ActionBar'
import Button from '../components/Button'
import Chip from '../components/Chip'
import CandlestickChart from '../components/CandlestickChart'
import EmptyState from '../components/EmptyState'
import EducationCallout from '../components/EducationCallout'
import ErrorState from '../components/ErrorState'
import FeedbackState from '../components/FeedbackState'
import { SelectField, TextField } from '../components/FormFields'
import InlineMeta from '../components/InlineMeta'
import ListTable from '../components/ListTable'
import LoadingBlock from '../components/LoadingBlock'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import StatusBadge from '../components/StatusBadge'
import TickerInput from '../components/TickerInput'
import TickerHub from '../components/TickerHub'
import { formatValueFlowText } from '../components/ValueFlow'
import WorkflowGuide, { buildWorkflowSteps } from '../components/WorkflowGuide'
import { useToast } from '../context/ToastContext'
import { usePreferences } from '../context/PreferencesContext'
import usePageActionShortcuts, { focusFirstMatching } from '../hooks/usePageActionShortcuts'
import useKeyboardListNavigation from '../hooks/useKeyboardListNavigation'
import {
  buildEventWindowModel,
  buildIntervalModel,
  buildTradingSessionModel,
  getStyleIntervalOptions,
} from '../utils/intradayModel'
import {
  buildIntradayBoardMode,
  buildIntradayCandidateQueue,
  buildIntradayOpportunityState,
} from '../utils/intradayBoardModel'
import { buildIntradayPresetGuide, getIntradayPresetProfile } from '../utils/intradayPresetModel'
import { isTickerValid } from '../utils/validators'

function normalizeTickers(raw) {
  const seen = new Set()
  return String(raw || '')
    .split(',')
    .map((item) => item.trim().toUpperCase())
    .filter((item) => item && !seen.has(item) && seen.add(item))
    .slice(0, 12)
}

function buildCompareFormErrors(form) {
  const errors = {}
  const rawTickers = String(form?.tickers || '')
    .split(',')
    .map((item) => item.trim().toUpperCase())
    .filter(Boolean)
  const invalidTickers = rawTickers.filter((item) => !isTickerValid(item))
  const validTickers = normalizeTickers(form?.tickers)
  const horizon = Number(form?.horizon)

  if (!rawTickers.length) {
    errors.tickers = 'Enter at least two tickers to compare.'
  } else if (invalidTickers.length) {
    errors.tickers = `Fix invalid ticker${invalidTickers.length === 1 ? '' : 's'}: ${invalidTickers.join(', ')}.`
  } else if (validTickers.length < 2) {
    errors.tickers = 'Enter at least two unique valid tickers to compare.'
  }

  if (!Number.isInteger(horizon) || horizon < 1 || horizon > 50) {
    errors.horizon = 'Horizon must be a whole number between 1 and 50 bars.'
  }

  return errors
}

function omitKeys(record, fields) {
  const next = { ...record }
  fields.forEach((field) => {
    delete next[field]
  })
  return next
}

function toNumber(value) {
  const normalized = Number(value)
  return Number.isFinite(normalized) ? normalized : null
}

function summarizeInlineCopy(value, maxLength = 120) {
  const normalized = String(value || '')
    .replace(/\s+/g, ' ')
    .trim()
  if (!normalized) return ''
  if (normalized.length <= maxLength) return normalized
  return `${normalized.slice(0, Math.max(0, maxLength - 1)).trimEnd()}...`
}

function formatLabel(value, fallback = 'Unknown') {
  const normalized = String(value || '').trim()
  if (!normalized) return fallback
  return normalized.replaceAll('_', ' ').replace(/\b\w/g, (character) => character.toUpperCase())
}

function formatNumber(value, digits = 2) {
  const normalized = toNumber(value)
  if (normalized === null) return '—'
  return normalized.toFixed(digits)
}

function formatPercent(value, { ratio = false, digits = 1 } = {}) {
  const normalized = toNumber(value)
  if (normalized === null) return '—'
  const percentage = ratio ? normalized * 100 : normalized
  return `${percentage.toFixed(digits)}%`
}

function formatPrice(value) {
  const normalized = toNumber(value)
  if (normalized === null) return '—'
  return normalized.toFixed(normalized >= 100 ? 2 : 3)
}

function formatDecisionTone(value) {
  const normalized = String(value || '').trim().toUpperCase()
  if (['VALID TRADE', 'BULLISH'].includes(normalized)) return 'positive'
  if (['PASS', 'WAIT FOR BREAKOUT', 'BEARISH'].includes(normalized)) return 'warning'
  if (['REJECT', 'AVOID'].includes(normalized)) return 'negative'
  return 'neutral'
}

function intervalToMinutes(interval) {
  const normalized = String(interval || '').trim().toLowerCase()
  const intervalMap = {
    '1m': 1,
    '5m': 5,
    '15m': 15,
    '30m': 30,
    '1h': 60,
    '4h': 240,
    '1d': 1440,
  }
  return intervalMap[normalized] || 5
}

function formatForecastHorizon(interval, horizon) {
  const steps = Math.max(1, Math.round(toNumber(horizon) || 1))
  const totalMinutes = intervalToMinutes(interval) * steps
  let durationLabel = `${totalMinutes}m`
  if (totalMinutes >= 1440 && totalMinutes % 1440 === 0) {
    durationLabel = `${totalMinutes / 1440}d`
  } else if (totalMinutes >= 60 && totalMinutes % 60 === 0) {
    durationLabel = `${totalMinutes / 60}h`
  } else if (totalMinutes > 60) {
    durationLabel = `${(totalMinutes / 60).toFixed(1)}h`
  }
  return `${steps} bar${steps === 1 ? '' : 's'} (~${durationLabel})`
}

function formatFreshnessTone(value) {
  const normalized = String(value || '').trim().toLowerCase()
  if (normalized === 'fresh') return 'positive'
  if (normalized === 'stale') return 'warning'
  return 'neutral'
}

function formatCount(value) {
  const normalized = toNumber(value)
  return normalized === null ? '—' : Math.round(normalized).toLocaleString()
}

function formatCompactNumber(value) {
  const normalized = toNumber(value)
  if (normalized === null) return 'â€”'
  return new Intl.NumberFormat('en-US', {
    notation: 'compact',
    maximumFractionDigits: normalized >= 100 ? 0 : 1,
  }).format(normalized)
}

function clampPercent(value) {
  const numeric = toNumber(value)
  if (numeric === null) return 0
  return Math.max(0, Math.min(100, numeric))
}

function normalizeScore(value, { ratio = false } = {}) {
  const numeric = toNumber(value)
  if (numeric === null) return 0
  return clampPercent(ratio ? numeric * 100 : numeric)
}

function toneToFallbackScore(tone) {
  if (tone === 'positive') return 84
  if (tone === 'negative') return 24
  return 52
}

function resolveInstitutionalFlowProfile(row) {
  const rawFlow = row?.institutional_flow && typeof row.institutional_flow === 'object' ? row.institutional_flow : {}
  const score = toNumber(rawFlow?.score ?? row?.institutional_flow_score)
  const controlledUniverse = Boolean(rawFlow?.controlled_universe ?? row?.ranking_context?.controlled_universe)
  const avgDollarVolume = toNumber(rawFlow?.avg_dollar_volume)
  const optionLiquidityScore = toNumber(rawFlow?.option_liquidity_score)
  const notes = Array.isArray(rawFlow?.notes) ? rawFlow.notes.filter(Boolean) : []

  let tone = 'warning'
  if (score !== null) {
    if (score >= 0.72) tone = 'positive'
    else if (score < 0.48) tone = 'negative'
  }

  const label =
    String(rawFlow?.label || row?.institutional_flow_label || '').trim() ||
    (score === null ? 'Flow pending' : tone === 'positive' ? 'Flow strong' : tone === 'negative' ? 'Flow weak' : 'Flow mixed')

  const summaryParts = []
  if (controlledUniverse) summaryParts.push('Controlled universe')
  if (avgDollarVolume !== null) summaryParts.push(`Avg $${formatCompactNumber(avgDollarVolume)} / bar`)
  if (optionLiquidityScore !== null) summaryParts.push(`Opt liq ${Math.round(optionLiquidityScore * 100)}`)

  const summary = summaryParts.join(' · ') || 'Flow quality still needs live liquidity confirmation.'

  return {
    ...rawFlow,
    score,
    tone,
    label,
    controlledUniverse,
    avgDollarVolume,
    optionLiquidityScore,
    notes,
    summary,
    detail: notes[0] || summary,
  }
}

function resolveNewsProfile(source, fallbackChart = null) {
  const rawNews =
    source?.news_sentiment && typeof source.news_sentiment === 'object'
      ? source.news_sentiment
      : fallbackChart?.news_sentiment && typeof fallbackChart.news_sentiment === 'object'
        ? fallbackChart.news_sentiment
        : {}
  const score = toNumber(rawNews?.sentiment_score)
  const confidence = toNumber(rawNews?.confidence)
  const articleCount = toNumber(rawNews?.article_count)
  const sourceLabel = String(rawNews?.source || '').trim() || 'News feed'
  const headlines = Array.isArray(rawNews?.headlines) ? rawNews.headlines.filter(Boolean) : []
  const topHeadline = headlines[0] && typeof headlines[0] === 'object' ? headlines[0] : null

  let tone = 'neutral'
  if ((articleCount || 0) > 0) {
    if (score !== null && score >= 0.18) tone = 'positive'
    else if (score !== null && score <= -0.18) tone = 'negative'
    else tone = 'warning'
  }

  const label =
    String(rawNews?.label || '').trim() ||
    ((articleCount || 0) > 0 ? 'News watch' : 'No recent news')
  const summaryParts = []
  if ((articleCount || 0) > 0) {
    summaryParts.push(`${Math.round(articleCount)} article${Math.round(articleCount) === 1 ? '' : 's'}`)
    if (confidence !== null) summaryParts.push(`${formatPercent(confidence, { ratio: true })} confidence`)
    summaryParts.push(sourceLabel)
  } else {
    summaryParts.push('No recent articles')
  }
  const headlineDetail = topHeadline?.title
    ? summarizeInlineCopy(
        `${topHeadline.title}${topHeadline.publisher ? ` — ${topHeadline.publisher}` : ''}`,
        140,
      )
    : ''

  return {
    ...rawNews,
    score,
    confidence,
    articleCount,
    tone,
    label,
    sourceLabel,
    headlines,
    topHeadline,
    summary: summaryParts.join(' Â· '),
    detail: headlineDetail || 'News context is still thin for this setup.',
  }
}

function resolveOptionExecutionProfile(row) {
  const rawProfile =
    row?.option_execution_profile && typeof row.option_execution_profile === 'object'
      ? row.option_execution_profile
      : {}
  const executionScore = toNumber(rawProfile?.execution_score ?? row?.option_execution_score)
  const contractQualityTier =
    String(rawProfile?.contract_quality_tier || row?.contract_quality_tier || '').trim().toLowerCase() || 'pending'
  const qualityTone =
    contractQualityTier === 'strong'
      ? 'positive'
      : contractQualityTier === 'acceptable'
        ? 'warning'
        : contractQualityTier === 'weak'
          ? 'negative'
          : 'warning'
  const rejectReasons = Array.isArray(rawProfile?.reject_reasons)
    ? rawProfile.reject_reasons.map((reason) => summarizeInlineCopy(reason, 120)).filter(Boolean)
    : []
  const quoteAgeSeconds = toNumber(rawProfile?.quote_age_seconds)
  const detailParts = [
    rawProfile?.liquidity_tier ? formatLabel(rawProfile.liquidity_tier) : null,
    rawProfile?.dte_bucket ? formatLabel(rawProfile.dte_bucket) : null,
    rawProfile?.moneyness_bucket ? formatLabel(rawProfile.moneyness_bucket) : null,
    quoteAgeSeconds === null ? null : `Quote ${Math.round(quoteAgeSeconds)}s`,
  ].filter(Boolean)

  return {
    executionScore,
    scoreLabel: executionScore === null ? 'Pending' : `${Math.round(executionScore)}/100`,
    contractQualityTier,
    qualityLabel: contractQualityTier === 'pending' ? 'Quality pending' : formatLabel(contractQualityTier),
    qualityTone,
    rejectSummary: rejectReasons.slice(0, 2).join(' | '),
    detail: rejectReasons[0] || detailParts.join(' | ') || 'Option execution checks are still loading.',
  }
}

function resolveVehicleProfile(row) {
  const recommendation = String(row?.vehicle_recommendation || '').trim().toLowerCase()
  const optionExecutionProfile = resolveOptionExecutionProfile(row)
  const tone =
    recommendation === 'listed_option'
      ? 'positive'
      : recommendation === 'equity'
        ? 'warning'
        : recommendation === 'stand_down'
          ? 'negative'
          : 'warning'
  const label =
    recommendation === 'listed_option'
      ? 'Option preferred'
      : recommendation === 'equity'
        ? 'Stock preferred'
        : recommendation === 'stand_down'
          ? 'Stand down'
          : 'Vehicle pending'

  return {
    recommendation: recommendation || 'pending',
    label,
    tone,
    reason:
      summarizeInlineCopy(row?.vehicle_reason, 160) ||
      (recommendation === 'listed_option'
        ? 'Contract quality is good enough to express the setup with options.'
        : recommendation === 'equity'
          ? 'The stock route is cleaner than the option chain for this setup.'
          : recommendation === 'stand_down'
            ? 'Neither stock nor option execution is clean enough right now.'
            : 'Vehicle selection is still loading.'),
    optionExecutionProfile,
  }
}

function resolveTrustProfile({
  confidenceScore,
  freshnessStatus,
  regimeStrengthScore,
  resolvedCount,
  eventConfidencePenalty,
}) {
  const freshness = String(freshnessStatus || '').trim().toLowerCase()
  let score = 0

  if (freshness === 'fresh') score += 1
  else if (freshness === 'stale') score -= 1

  if (confidenceScore !== null) {
    if (confidenceScore >= 0.62) score += 1
    else if (confidenceScore < 0.48) score -= 1
  }

  if (regimeStrengthScore !== null) {
    if (regimeStrengthScore >= 0.6) score += 1
    else if (regimeStrengthScore < 0.45) score -= 1
  }

  if (resolvedCount !== null) {
    if (resolvedCount >= 8) score += 1
    else if (resolvedCount < 3) score -= 1
  }

  if (eventConfidencePenalty !== null && eventConfidencePenalty > 0.08) score -= 1

  if (score >= 2) {
    return {
      label: 'High trust',
      tone: 'positive',
      detail: 'Fresh inputs, stronger regime support, and enough resolved history are reinforcing this forecast.',
    }
  }

  if (score >= 0) {
    return {
      label: 'Conditional',
      tone: 'warning',
      detail: 'This forecast is usable, but at least one support layer is thin enough that execution discipline matters more.',
    }
  }

  return {
    label: 'Fragile',
    tone: 'negative',
    detail: 'Thin support, stale inputs, or weak regime context mean this read should be treated more like a watchlist prompt than a conviction call.',
  }
}

function resolveExecutionProfile({
  spreadPct,
  volume,
  openInterest,
  freshnessStatus,
}) {
  let score = 0
  const normalizedFreshness = String(freshnessStatus || '').trim().toLowerCase()

  if (spreadPct !== null) {
    if (spreadPct <= 6) score += 1
    else if (spreadPct > 12) score -= 1
  }

  if (volume !== null && openInterest !== null) {
    if (volume >= 100 && openInterest >= 500) score += 1
    else if (volume < 25 || openInterest < 100) score -= 1
  }

  if (normalizedFreshness === 'stale') score -= 1

  const spreadLabel = spreadPct === null ? 'Spread pending' : `${formatPercent(spreadPct, { digits: 1 })} spread`
  const participationLabel =
    volume === null && openInterest === null
      ? 'Vol / OI pending'
      : `Vol ${formatCount(volume)} | OI ${formatCount(openInterest)}`

  if (score >= 2) {
    return {
      label: 'Execution clean',
      tone: 'positive',
      routeLabel: 'Marketable routing can work if urgency matters.',
      detail: 'Spread and participation are supportive enough that execution drag should be manageable.',
      spreadLabel,
      participationLabel,
    }
  }

  if (score >= 0) {
    return {
      label: 'Use price control',
      tone: 'warning',
      routeLabel: 'Prefer a priced order over immediacy.',
      detail: 'Liquidity is usable, but spread drag is meaningful enough that price control should matter.',
      spreadLabel,
      participationLabel,
    }
  }

  return {
    label: 'Fragile fills',
    tone: 'negative',
    routeLabel: 'Do not assume forecast edge survives a sloppy fill.',
    detail: 'Thin participation or wide spreads can overwhelm a modest forecast edge here.',
    spreadLabel,
    participationLabel,
  }
}

function resolveTargetQualityProfile({
  resolvedCount,
  averageError,
  empiricalHitRate,
  averageProbabilityUp,
  calibrationScope,
}) {
  const scopeLabel = calibrationScope ? formatLabel(calibrationScope) : 'Unknown'
  const edge = empiricalHitRate !== null && averageProbabilityUp !== null
    ? empiricalHitRate - averageProbabilityUp
    : null

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
      detail: `${scopeLabel} calibration has enough resolved history to be treated as a repeatable edge check instead of a fresh pattern.`,
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
      detail: `${scopeLabel} calibration is live and usable, but the sample is still maturing and should not be over-trusted.`,
    }
  }

  return {
    label: 'Thin sample',
    tone: 'negative',
    detail: 'Resolved history is still too thin to treat this forecast as a durable recurring edge on its own.',
  }
}

function resolveDriftProfile({
  confidenceScore,
  freshnessStatus,
  regimeStrengthScore,
  resolvedCount,
  averageError,
  empiricalHitRate,
  averageProbabilityUp,
  eventConfidencePenalty,
}) {
  const freshness = String(freshnessStatus || '').trim().toLowerCase()
  const edge =
    empiricalHitRate !== null && averageProbabilityUp !== null
      ? empiricalHitRate - averageProbabilityUp
      : null

  let riskFlags = 0
  if (freshness === 'stale') riskFlags += 1
  if (confidenceScore !== null && confidenceScore < 0.48) riskFlags += 1
  if (regimeStrengthScore !== null && regimeStrengthScore < 0.45) riskFlags += 1
  if (averageError !== null && averageError > 0.24) riskFlags += 1
  if (edge !== null && edge < -0.03) riskFlags += 1
  if (eventConfidencePenalty !== null && eventConfidencePenalty > 0.08) riskFlags += 1

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
      action: 'Pause or heavily down-weight until the inputs recover.',
      detail: 'Support layers are degrading enough that the forecast should not be treated as a live edge right now.',
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
      action: 'Keep it live, but reduce trust and require tighter review before acting.',
      detail: 'At least one support layer is slipping, so this setup should be treated as conditionally degrading instead of stable.',
    }
  }

  return {
    label: 'Stable',
    tone: 'positive',
    action: 'No drift warning is active.',
    detail: 'Fresh inputs, acceptable calibration error, and stable support mean the signal is not currently showing obvious decay.',
  }
}

function resolveBenchmarkProfile({
  probabilityUp,
  averageProbabilityUp,
  technicalProbabilityUp,
  resolvedCount,
  calibrationScope,
}) {
  if (
    probabilityUp !== null &&
    averageProbabilityUp !== null &&
    resolvedCount !== null &&
    resolvedCount >= 8
  ) {
    const edge = probabilityUp - averageProbabilityUp
    return {
      label: `${formatLabel(calibrationScope || 'global')} baseline`,
      tone: edge >= 0.03 ? 'positive' : edge <= -0.03 ? 'negative' : 'warning',
      comparison: `${formatPercent(probabilityUp, { ratio: true })} vs ${formatPercent(averageProbabilityUp, { ratio: true })}`,
      detail: 'This setup is trying to beat the resolved calibration baseline instead of just sounding directional.',
    }
  }

  if (probabilityUp !== null && technicalProbabilityUp !== null) {
    const edge = probabilityUp - technicalProbabilityUp
    return {
      label: 'Technical base',
      tone: edge >= 0.02 ? 'positive' : edge <= -0.02 ? 'negative' : 'warning',
      comparison: `${formatPercent(probabilityUp, { ratio: true })} vs ${formatPercent(technicalProbabilityUp, { ratio: true })}`,
      detail: 'This setup is being judged against the raw technical probability before calibration layers are applied.',
    }
  }

  if (probabilityUp !== null) {
    const edge = probabilityUp - 0.5
    return {
      label: 'Neutral 50/50',
      tone: edge >= 0.03 ? 'positive' : edge <= -0.03 ? 'negative' : 'warning',
      comparison: `${formatPercent(probabilityUp, { ratio: true })} vs 50.0%`,
      detail: 'Without enough sample history, the forecast should at least beat a neutral up/down baseline.',
    }
  }

  return {
    label: 'No benchmark',
    tone: 'warning',
    comparison: 'Pending',
    detail: 'There is not enough forecast context here to define a meaningful benchmark yet.',
  }
}

function resolveMemoryProfile({
  marketRegime,
  bestRegime,
  weakestRegime,
  bestDriver,
  weakestDriver,
}) {
  const activeRegime = String(marketRegime || '').trim().toLowerCase()
  const bestRegimeName = String(bestRegime?.market_regime || '').trim().toLowerCase()
  const weakestRegimeName = String(weakestRegime?.market_regime || '').trim().toLowerCase()
  const bestDriverLabel = formatLabel(bestDriver?.driver || 'unknown')
  const weakestDriverLabel = formatLabel(weakestDriver?.driver || 'unknown')

  if (activeRegime && weakestRegimeName && activeRegime === weakestRegimeName) {
    return {
      label: 'Weak regime memory',
      tone: 'negative',
      detail: `The active ${formatLabel(marketRegime)} regime has been one of the weakest resolved states. ${weakestDriverLabel} has also been the least supportive driver.`,
    }
  }

  if (activeRegime && bestRegimeName && activeRegime === bestRegimeName) {
    return {
      label: 'Known strong regime',
      tone: 'positive',
      detail: `The active ${formatLabel(marketRegime)} regime has resolved well historically. ${bestDriverLabel} has been the most supportive driver in that memory stack.`,
    }
  }

  if (bestRegimeName || weakestRegimeName || bestDriver?.driver || weakestDriver?.driver) {
    return {
      label: 'Mixed memory',
      tone: 'warning',
      detail: `Best memory sits in ${formatLabel(bestRegime?.market_regime || 'another regime')}, weakest in ${formatLabel(weakestRegime?.market_regime || 'another regime')}. Drivers are mixed between ${bestDriverLabel} and ${weakestDriverLabel}.`,
    }
  }

  return {
    label: 'No memory',
    tone: 'warning',
    detail: 'There is not enough resolved regime or driver memory here to say where the edge tends to hold up or break down.',
  }
}

function resolveSessionMemoryProfile({
  sessionLabel,
  bestSession,
  weakestSession,
}) {
  const activeSession = String(sessionLabel || '').trim().toLowerCase().replaceAll('-', '_')
  const bestSessionName = String(bestSession || '').trim().toLowerCase()
  const weakestSessionName = String(weakestSession || '').trim().toLowerCase()

  if (activeSession && weakestSessionName && activeSession === weakestSessionName) {
    return {
      label: 'Weak session memory',
      tone: 'negative',
      detail: `The active ${formatLabel(sessionLabel)} session has been one of the weakest resolved trading windows for this setup.`,
    }
  }

  if (activeSession && bestSessionName && activeSession === bestSessionName) {
    return {
      label: 'Known strong session',
      tone: 'positive',
      detail: `The active ${formatLabel(sessionLabel)} session has historically been one of the most supportive windows for this setup.`,
    }
  }

  if (bestSessionName || weakestSessionName) {
    return {
      label: 'Mixed session memory',
      tone: 'warning',
      detail: `Best session memory sits in ${formatLabel(bestSession || 'another session')}, while ${formatLabel(weakestSession || 'another session')} has been weaker.`,
    }
  }

  return {
    label: 'No session memory',
    tone: 'warning',
    detail: 'There is not enough resolved session history here to say whether this edge behaves better or worse outside regular hours.',
  }
}

function resolveEventMemoryProfile({
  eventRisk,
  nextEventName,
  bestEventWindow,
  weakestEventWindow,
}) {
  const nextEvent = String(nextEventName || '').trim().toLowerCase()
  const activeEventWindow =
    !eventRisk
      ? 'quiet_window'
      : nextEvent.includes('earnings')
        ? 'earnings_window'
        : nextEvent
          ? 'macro_window'
          : 'event_window'
  const bestEventName = String(bestEventWindow || '').trim().toLowerCase()
  const weakestEventName = String(weakestEventWindow || '').trim().toLowerCase()

  if (activeEventWindow && weakestEventName && activeEventWindow === weakestEventName) {
    return {
      label: 'Weak event memory',
      tone: 'negative',
      detail: `The current ${formatLabel(activeEventWindow)} state has been one of the weakest resolved event windows for this setup.`,
    }
  }

  if (activeEventWindow && bestEventName && activeEventWindow === bestEventName) {
    return {
      label: 'Known strong event window',
      tone: 'positive',
      detail: `The current ${formatLabel(activeEventWindow)} state has historically been one of the most supportive event windows for this setup.`,
    }
  }

  if (bestEventName || weakestEventName) {
    return {
      label: 'Mixed event memory',
      tone: 'warning',
      detail: `Best event memory sits in ${formatLabel(bestEventWindow || 'another window')}, while ${formatLabel(weakestEventWindow || 'another window')} has been weaker.`,
    }
  }

  return {
    label: 'No event memory',
    tone: 'warning',
    detail: 'There is not enough resolved event-window history yet to say whether this edge behaves better in quiet, macro, or earnings conditions.',
  }
}

function resolveEventPriorityProfile(row, marketModel = {}) {
  const eventModel = buildEventWindowModel({
    tradingStyle: marketModel.tradingStyle,
    eventContext: {
      event_risk: row?.event_risk,
      next_event_name: row?.next_event_name,
      next_event_days: row?.next_event_days,
      event_window_label: row?.event_window_label,
      session_label: row?.session_label,
    },
    intradayEventGuardMinutes: marketModel.intradayEventGuardMinutes,
    sessionModel: marketModel.sessionModel,
  })

  return {
    active: Boolean(eventModel.active),
    label: eventModel.badgeLabel || eventModel.label || 'Catalyst watch',
    tone: eventModel.tone || 'neutral',
    detail: eventModel.detail || 'Catalyst pressure is still shaping this compare rank.',
    daysUntil: eventModel.daysUntil ?? null,
  }
}

function resolveDecisionGateProfile({
  tradeDecision,
  trustTone,
  executionTone,
  targetQualityTone,
  benchmarkTone,
  driftTone,
  eventMemoryTone,
  sessionMemoryTone,
  memoryTone,
}) {
  const normalizedDecision = String(tradeDecision || '').trim().toUpperCase()
  const blockingReasons = []
  const cautionReasons = []
  const supportTones = [eventMemoryTone, sessionMemoryTone, memoryTone]
  const supportNegativeCount = supportTones.filter((tone) => tone === 'negative').length
  const supportWarningCount = supportTones.filter((tone) => tone === 'warning').length

  if (normalizedDecision && normalizedDecision !== 'VALID TRADE') {
    blockingReasons.push(normalizedDecision === 'REJECT' ? 'model reject' : 'model not green-lit')
  }

  const coreChecks = [
    { tone: trustTone, negative: 'fragile trust', warning: 'conditional trust' },
    { tone: executionTone, negative: 'fragile execution', warning: 'route still needs price control' },
    { tone: targetQualityTone, negative: 'thin sample', warning: 'sample still developing' },
    { tone: benchmarkTone, negative: 'below baseline', warning: 'benchmark edge is narrow' },
    { tone: driftTone, negative: 'kill switch', warning: 'drift under watch' },
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
      action: 'Do not promote this setup yet.',
      detail: `Blocked by ${blockingReasons.slice(0, 2).join(' and ')}${blockingReasons.length > 2 ? ', plus more.' : '.'}`,
    }
  }

  const coreAllPositive =
    normalizedDecision === 'VALID TRADE' &&
    trustTone === 'positive' &&
    executionTone === 'positive' &&
    targetQualityTone === 'positive' &&
    benchmarkTone === 'positive' &&
    driftTone === 'positive'

  if (coreAllPositive && supportNegativeCount === 0 && supportWarningCount === 0) {
    return {
      label: 'Promote',
      tone: 'positive',
      action: 'Promote this as a live candidate first.',
      detail: 'Trust, execution, sample quality, benchmark edge, drift, and historical memory are all clearing together.',
    }
  }

  return {
    label: 'Review gate',
    tone: 'warning',
    action: 'Keep it reviewable, but do not auto-promote it yet.',
    detail: cautionReasons.length
      ? `${cautionReasons.length} review flag${cautionReasons.length === 1 ? '' : 's'}: ${cautionReasons.slice(0, 2).join(' and ')}${cautionReasons.length > 2 ? ', plus more.' : '.'}`
      : 'This setup is usable, but the full stack is not clearing together strongly enough to auto-promote.',
  }
}

function buildCandidateQueue(rows = []) {
  const normalizedRows = Array.isArray(rows) ? rows : []
  const sorted = [...normalizedRows].sort((left, right) => {
    const tierRank = { promote: 0, review: 1, stand_down: 2 }
    const leftTierRank = tierRank[String(left.rankingTier || '').trim().toLowerCase()] ?? 3
    const rightTierRank = tierRank[String(right.rankingTier || '').trim().toLowerCase()] ?? 3
    if (leftTierRank !== rightTierRank) return leftTierRank - rightTierRank
    const toneRank = { positive: 0, warning: 1, negative: 2 }
    const leftRank = toneRank[left.decisionGateTone] ?? 3
    const rightRank = toneRank[right.decisionGateTone] ?? 3
    if (leftRank !== rightRank) return leftRank - rightRank
    const leftScore = toNumber(left.rankingScore ?? left.setupScore) ?? Number.NEGATIVE_INFINITY
    const rightScore = toNumber(right.rankingScore ?? right.setupScore) ?? Number.NEGATIVE_INFINITY
    if (leftScore !== rightScore) return rightScore - leftScore
    const leftConfidence = toNumber(left.chartPayload?.forecast?.confidence_score) ?? Number.NEGATIVE_INFINITY
    const rightConfidence = toNumber(right.chartPayload?.forecast?.confidence_score) ?? Number.NEGATIVE_INFINITY
    if (leftConfidence !== rightConfidence) return rightConfidence - leftConfidence
    return String(left.ticker || '').localeCompare(String(right.ticker || ''))
  })

  const promoted = sorted.filter((row) => row.rankingTier === 'promote' && row.decisionGateTone !== 'negative').slice(0, 3)
  if (promoted.length) {
    return { mode: 'promote', rows: promoted }
  }
  const reviewable = sorted.filter((row) => row.rankingTier !== 'stand_down' && row.decisionGateTone !== 'negative').slice(0, 3)
  if (reviewable.length) {
    return { mode: 'review', rows: reviewable }
  }
  return {
    mode: 'review',
    rows: sorted.slice(0, 3),
  }
}

function resolveTargetProfile({ row, strategy, forecast, interval, horizon }) {
  const hasDirectional = toNumber(row?.probability_up) !== null
  const hasVolatility =
    toNumber(strategy?.current_sigma_pct) !== null || toNumber(forecast?.adjusted_expected_move) !== null

  const horizonLabel = formatForecastHorizon(interval, horizon)

  if (hasDirectional && hasVolatility) {
    return {
      label: 'Directional move with volatility context',
      shortLabel: 'Direction + vol',
      useLabel: 'Best for ranking names under one shared horizon, not for blanket market timing.',
      trustLabel: 'Read the directional probability together with expected move and sigma before acting.',
      horizonLabel,
    }
  }

  if (hasDirectional) {
    return {
      label: 'Directional move',
      shortLabel: 'Direction',
      useLabel: 'Best for conditional direction over the selected bar window.',
      trustLabel: 'This is a horizon-bound directional read, not a broad market forecast.',
      horizonLabel,
    }
  }

  if (hasVolatility) {
    return {
      label: 'Volatility envelope',
      shortLabel: 'Volatility',
      useLabel: 'Best for expected movement and regime context under the selected horizon.',
      trustLabel: 'Use this as movement context and sizing support rather than as a pure directional claim.',
      horizonLabel,
    }
  }

  return {
    label: 'Relative setup rank',
    shortLabel: 'Ranking',
    useLabel: 'Best for comparing setup quality across names using one common window.',
    trustLabel: 'Treat the compare board as a ranking surface when direct forecast detail is thin.',
    horizonLabel,
  }
}

function buildComparisonRows(payload, marketModel = {}) {
  const charts = payload?.charts || {}
  const leaderScore = toNumber(payload?.leader?.ranking_score ?? payload?.leader?.setup_score)
  return (payload?.rows || []).map((row, index) => {
    const chart = charts[row.ticker] || {}
    const strategy = chart.strategy || {}
    const forecast = chart.forecast || {}
    const freshness = chart.freshness || {}
    const rankingContext = row.ranking_context || {}
    const forecastFraming = row.forecast_framing || chart.forecast_framing || null
    const targetProfile = forecastFraming
      ? {
          label: forecastFraming.label || 'Relative setup rank',
          shortLabel: forecastFraming.short_label || 'Ranking',
          useLabel: forecastFraming.use_label || 'Best for comparing setup quality across names using one common window.',
          trustLabel: forecastFraming.trust_label || 'Treat the compare board as a ranking surface when direct forecast detail is thin.',
          horizonLabel: forecastFraming.horizon_label || formatForecastHorizon(payload?.interval, payload?.horizon),
        }
      : resolveTargetProfile({
          row,
          strategy,
          forecast,
          interval: payload?.interval,
          horizon: payload?.horizon,
        })
    const setupScore = toNumber(row.setup_score)
    const rankingScore = toNumber(row.ranking_score ?? rankingContext.score ?? row.setup_score)
    const rankGap = leaderScore !== null && rankingScore !== null ? leaderScore - rankingScore : null
    const sigmaPct = toNumber(strategy.current_sigma_pct)
    const confidenceScore = toNumber(forecast.confidence_score)
    const expectedMove = toNumber(forecast.adjusted_expected_move)
    const regimeStrengthScore = toNumber(forecast.regime_strength_score)
    const institutionalFlowProfile = resolveInstitutionalFlowProfile(row)
    const newsProfile = resolveNewsProfile(row, chart)
    const vehicleProfile = resolveVehicleProfile(row)
    const journalCalibration = forecast?.journal_calibration || {}
    const resolvedCount = toNumber(forecast?.journal_calibration?.resolved_count)
    const empiricalHitRate = toNumber(forecast?.journal_calibration?.empirical_hit_rate)
    const averageError = toNumber(forecast?.journal_calibration?.average_error)
    const averageProbabilityUp = toNumber(forecast?.journal_calibration?.average_probability_up)
    const calibrationScope = String(forecast?.journal_calibration?.calibration_scope || '').trim()
    const eventConfidencePenalty = toNumber(forecast?.contribution_breakdown?.event_confidence_penalty)
    const spreadPct = toNumber(row.spread_pct)
    const volume = toNumber(row.volume)
    const openInterest = toNumber(row.open_interest)
    const trustProfile = resolveTrustProfile({
      confidenceScore,
      freshnessStatus: freshness.status,
      regimeStrengthScore,
      resolvedCount,
      eventConfidencePenalty,
    })
    const executionProfile = resolveExecutionProfile({
      spreadPct,
      volume,
      openInterest,
      freshnessStatus: freshness.status,
    })
    const targetQualityProfile = resolveTargetQualityProfile({
      resolvedCount,
      averageError,
      empiricalHitRate,
      averageProbabilityUp,
      calibrationScope,
    })
    const eventMemoryProfile = resolveEventMemoryProfile({
      eventRisk: Boolean(row.event_risk),
      nextEventName: row.next_event_name,
      bestEventWindow: journalCalibration.best_event_window?.event_window_label,
      weakestEventWindow: journalCalibration.weakest_event_window?.event_window_label,
    })
    const eventPriorityProfile = resolveEventPriorityProfile(row, marketModel)
    const sessionMemoryProfile = resolveSessionMemoryProfile({
      sessionLabel: freshness.session_label,
      bestSession: journalCalibration.best_session?.session_label,
      weakestSession: journalCalibration.weakest_session?.session_label,
    })
    const memoryProfile = resolveMemoryProfile({
      marketRegime: forecast.market_regime,
      bestRegime: journalCalibration.best_regime,
      weakestRegime: journalCalibration.weakest_regime,
      bestDriver: journalCalibration.best_driver,
      weakestDriver: journalCalibration.weakest_driver,
    })
    const benchmarkProfile = resolveBenchmarkProfile({
      probabilityUp: toNumber(row.probability_up),
      averageProbabilityUp,
      technicalProbabilityUp: toNumber(forecast.technical_probability_up),
      resolvedCount,
      calibrationScope,
    })
    const driftProfile = resolveDriftProfile({
      confidenceScore,
      freshnessStatus: freshness.status,
      regimeStrengthScore,
      resolvedCount,
      averageError,
      empiricalHitRate,
      averageProbabilityUp,
      eventConfidencePenalty,
    })
    const decisionGateProfile = resolveDecisionGateProfile({
      tradeDecision: row.trade_decision,
      trustTone: trustProfile.tone,
      executionTone: executionProfile.tone,
      targetQualityTone: targetQualityProfile.tone,
      benchmarkTone: benchmarkProfile.tone,
      driftTone: driftProfile.tone,
      eventMemoryTone: eventMemoryProfile.tone,
      sessionMemoryTone: sessionMemoryProfile.tone,
      memoryTone: memoryProfile.tone,
    })
    const intradayState = buildIntradayOpportunityState({
      tradingStyle: marketModel.tradingStyle,
      sessionModel: marketModel.sessionModel,
      rankingTier: rankingContext.tier || row.ranking_tier || 'review',
      rankingScore,
      setupScore,
      decisionTone: decisionGateProfile.tone,
      executionTone: executionProfile.tone,
      trustTone: trustProfile.tone,
      eventTone: eventPriorityProfile.tone,
      driftTone: driftProfile.tone,
      sessionMemoryTone: sessionMemoryProfile.tone,
      freshnessTone: formatFreshnessTone(freshness.status),
      regimeStrengthScore,
      confidenceScore,
    })
    return {
      key: `${row.ticker}-${row.contract_symbol || row.verdict || index}`,
      ticker: row.ticker,
      rank: toNumber(row.board_rank ?? rankingContext.board_rank) ?? index + 1,
      verdict: row.verdict || '—',
      direction: row.direction || '—',
      setupScore,
      rankingScore,
      scoreLabel: formatNumber(rankingScore ?? setupScore, 1),
      rankGapLabel: rankGap === null ? 'Leader' : `${rankGap.toFixed(1)} pts back`,
      rankingLabel: rankingContext.label || row.ranking_label || 'Reviewable',
      rankingTier: rankingContext.tier || row.ranking_tier || 'review',
      rankingSummary: rankingContext.summary || row.ranking_summary || '',
      componentSummary: rankingContext.component_summary || 'Board breakdown pending',
      convictionLabel: row.conviction_label || '—',
      setupGrade: row.setup_grade || '—',
      alignmentLabel: row.alignment_label || '—',
      tradeDecision: row.trade_decision || '—',
      rejectReason: row.reject_reason || '',
      livePriceValue: toNumber(row.live_price ?? row.close),
      livePriceLabel: formatPrice(row.live_price ?? row.close),
      targetPriceLabel: formatPrice(row.target_price),
      entryLowPrice: toNumber(row.entry_low_price),
      entryHighPrice: toNumber(row.entry_high_price),
      entryZoneLabel:
        row.entry_low_price != null && row.entry_high_price != null
          ? formatValueFlowText(formatPrice(row.entry_low_price), formatPrice(row.entry_high_price))
          : '—',
      contractSymbol: row.contract_symbol || 'No surfaced contract',
      executionAction: row.execution_action || strategy.latest_action || '—',
      tradeStatus: row.trade_status || '—',
      probabilityLabel: formatPercent(row.probability_up, { ratio: true }),
      confidenceScore,
      confidenceLabel: formatPercent(confidenceScore, { ratio: true }),
      regimeStrengthScore,
      regimeStrengthLabel: formatPercent(regimeStrengthScore, { ratio: true }),
      flowScore: institutionalFlowProfile.score,
      flowLabel: institutionalFlowProfile.label,
      flowTone: institutionalFlowProfile.tone,
      flowSummary: institutionalFlowProfile.summary,
      flowDetail: institutionalFlowProfile.detail,
      vehicleRecommendation: vehicleProfile.recommendation,
      vehicleLabel: vehicleProfile.label,
      vehicleTone: vehicleProfile.tone,
      vehicleReason: vehicleProfile.reason,
      optionExecutionScoreValue: vehicleProfile.optionExecutionProfile.executionScore,
      optionExecutionScoreLabel: vehicleProfile.optionExecutionProfile.scoreLabel,
      contractQualityTier: vehicleProfile.optionExecutionProfile.contractQualityTier,
      contractQualityLabel: vehicleProfile.optionExecutionProfile.qualityLabel,
      contractQualityTone: vehicleProfile.optionExecutionProfile.qualityTone,
      optionExecutionDetail: vehicleProfile.optionExecutionProfile.detail,
      optionExecutionRejectSummary: vehicleProfile.optionExecutionProfile.rejectSummary,
      newsScore: newsProfile.score,
      newsLabel: newsProfile.label,
      newsTone: newsProfile.tone,
      newsSummary: newsProfile.summary,
      newsDetail: newsProfile.detail,
      newsArticleCountLabel: newsProfile.articleCount === null ? 'â€”' : formatCount(newsProfile.articleCount),
      newsConfidenceLabel:
        newsProfile.articleCount && newsProfile.confidence !== null
          ? formatPercent(newsProfile.confidence, { ratio: true })
          : 'â€”',
      newsSourceLabel: newsProfile.sourceLabel,
      resolvedCountLabel: formatCount(resolvedCount),
      empiricalHitRateLabel: formatPercent(empiricalHitRate, { ratio: true }),
      averageErrorLabel: formatPercent(averageError, { ratio: true, digits: 2 }),
      calibrationScopeLabel: calibrationScope ? formatLabel(calibrationScope) : 'Unknown',
      sigmaLabel: formatPercent(sigmaPct, { ratio: true, digits: 2 }),
      expectedMoveLabel: formatPercent(expectedMove, { ratio: true, digits: 2 }),
      expectedPriceValue: toNumber(forecast.expected_price),
      expectedPriceLabel: formatPrice(forecast.expected_price),
      targetLabel: targetProfile.label,
      targetShortLabel: targetProfile.shortLabel,
      targetUseLabel: targetProfile.useLabel,
      interpretiveTrustLabel: targetProfile.trustLabel,
      horizonLabel: targetProfile.horizonLabel,
      regimeLabel: forecast.market_regime || '—',
      forecastLabel: forecast.label || 'No forecast label',
      sessionLabel: freshness.session_label || '—',
      freshnessStatus: freshness.status || 'unknown',
      freshnessTone: formatFreshnessTone(freshness.status),
      trustLabel: trustProfile.label,
      trustTone: trustProfile.tone,
      trustDetail: trustProfile.detail,
      executionLabel: executionProfile.label,
      executionTone: executionProfile.tone,
      executionRouteLabel: executionProfile.routeLabel,
      executionDetail: executionProfile.detail,
      spreadContextLabel: executionProfile.spreadLabel,
      participationLabel: executionProfile.participationLabel,
      targetQualityLabel: targetQualityProfile.label,
      targetQualityTone: targetQualityProfile.tone,
      targetQualityDetail: targetQualityProfile.detail,
      eventPriorityActive: eventPriorityProfile.active,
      eventPriorityLabel: eventPriorityProfile.label,
      eventPriorityTone: eventPriorityProfile.tone,
      eventPriorityDetail: eventPriorityProfile.detail,
      eventPriorityDays: eventPriorityProfile.daysUntil,
      eventMemoryLabel: eventMemoryProfile.label,
      eventMemoryTone: eventMemoryProfile.tone,
      eventMemoryDetail: eventMemoryProfile.detail,
      sessionMemoryLabel: sessionMemoryProfile.label,
      sessionMemoryTone: sessionMemoryProfile.tone,
      sessionMemoryDetail: sessionMemoryProfile.detail,
      memoryLabel: memoryProfile.label,
      memoryTone: memoryProfile.tone,
      memoryDetail: memoryProfile.detail,
      benchmarkLabel: benchmarkProfile.label,
      benchmarkTone: benchmarkProfile.tone,
      benchmarkComparison: benchmarkProfile.comparison,
      benchmarkDetail: benchmarkProfile.detail,
      driftLabel: driftProfile.label,
      driftTone: driftProfile.tone,
      driftAction: driftProfile.action,
      driftDetail: driftProfile.detail,
      decisionGateLabel: decisionGateProfile.label,
      decisionGateTone: decisionGateProfile.tone,
      decisionGateAction: decisionGateProfile.action,
      decisionGateDetail: decisionGateProfile.detail,
      intradayLabel: intradayState.label,
      intradayTone: intradayState.tone,
      intradayDetail: intradayState.detail,
      intradayBucket: intradayState.bucket,
      intradayPriorityScore: intradayState.priorityScore,
      chartPayload: chart,
    }
  })
}

function parseCompareWorkflowParams(search) {
  const params = new URLSearchParams(search || '')
  const tickers = normalizeTickers(params.get('tickers') || '')
  const interval = String(params.get('interval') || '').trim() || ''
  const rawHorizon = toNumber(params.get('horizon'))
  const parsedHorizon = rawHorizon === null ? null : Math.max(1, Math.round(rawHorizon))
  const focusTicker = String(params.get('focusTicker') || '').trim().toUpperCase()
  const workflowFrom = String(params.get('workflowFrom') || '').trim().toLowerCase()
  const workflowAutoload = String(params.get('workflowAutoload') || '').trim() === '1'
  return {
    tickers,
    tickersLabel: tickers.join(', '),
    interval,
    horizon: parsedHorizon || null,
    focusTicker,
    workflowFrom,
    workflowAutoload,
    hasAny: Boolean(tickers.length || interval || parsedHorizon || focusTicker || workflowFrom),
  }
}

function buildDashboardWorkflowUrl({
  ticker = '',
  interval = '5m',
  horizon = 5,
  source = 'compare',
  tickers = [],
  focusTicker = '',
}) {
  const params = new URLSearchParams()
  if (ticker) {
    params.set('ticker', String(ticker).trim().toUpperCase())
  }
  params.set('interval', String(interval || '5m'))
  params.set('horizon', String(Math.max(1, Math.round(Number(horizon) || 5))))
  params.set('workflowFrom', source)
  if (tickers.length) {
    params.set('compareTickers', tickers.join(','))
  }
  if (focusTicker) {
    params.set('compareFocusTicker', String(focusTicker).trim().toUpperCase())
  }
  return `/?${params.toString()}`
}

function buildCompareVisualPillars(row) {
  const benchmarkScore =
    row.benchmarkTone === 'positive'
      ? 84
      : row.benchmarkTone === 'warning'
        ? 54
        : row.benchmarkTone === 'negative'
          ? 24
          : 48
  const driftScore =
    row.driftTone === 'negative'
      ? 22
      : row.driftTone === 'warning'
        ? 52
        : row.driftTone === 'positive'
          ? 86
          : 62
  const flowScore =
    row.flowScore === null || row.flowScore === undefined
      ? toneToFallbackScore(row.flowTone)
      : normalizeScore(row.flowScore, { ratio: true })
  const newsScore =
    row.newsScore === null || row.newsScore === undefined
      ? toneToFallbackScore(row.newsTone)
      : normalizeScore(Math.abs(row.newsScore), { ratio: true })
  return [
    { key: 'rank', label: 'Rank', value: normalizeScore(row.rankingScore ?? row.setupScore), tone: row.intradayTone || 'default' },
    { key: 'confidence', label: 'Confidence', value: normalizeScore(row.confidenceScore, { ratio: true }), tone: row.trustTone || 'default' },
    { key: 'regime', label: 'Regime', value: normalizeScore(row.regimeStrengthScore, { ratio: true }), tone: 'positive' },
    { key: 'news', label: 'News', value: newsScore, tone: row.newsTone || 'default' },
    { key: 'flow', label: 'Flow', value: flowScore, tone: row.flowTone || 'default' },
    { key: 'benchmark', label: 'Benchmark', value: benchmarkScore, tone: row.benchmarkTone || 'default' },
    { key: 'drift', label: 'Drift', value: driftScore, tone: row.driftTone || 'default' },
  ]
}

function buildComparePathModel(row) {
  const live = toNumber(row.livePriceValue)
  const expected = toNumber(row.expectedPriceValue)
  const entryLow = toNumber(row.entryLowPrice)
  const entryHigh = toNumber(row.entryHighPrice)
  const points = [live, expected, entryLow, entryHigh].filter((value) => value !== null)
  if (points.length < 2) return null
  const lower = Math.min(...points)
  const upper = Math.max(...points)
  const span = Math.max(upper - lower, 0.01)
  const project = (value) => {
    const numeric = toNumber(value)
    if (numeric === null) return null
    return clampPercent(((numeric - lower) / span) * 100)
  }
  return {
    lower,
    upper,
    live,
    expected,
    entryLow,
    entryHigh,
    livePct: project(live),
    expectedPct: project(expected),
    entryLowPct: project(entryLow),
    entryHighPct: project(entryHigh),
  }
}

function CompareSnapshotCard({ row, onOpenDesk }) {
  const pillars = buildCompareVisualPillars(row)
  const pathModel = buildComparePathModel(row)

  return (
    <article className={`compare-snapshot-card compare-snapshot-card--${row.intradayTone || 'default'}`}>
      <div className="compare-snapshot-card__header">
        <div>
          <div className="compare-snapshot-card__ticker-row">
            <strong className="compare-snapshot-card__ticker">{row.ticker}</strong>
            <span className="compare-snapshot-card__rank">#{row.rank}</span>
          </div>
          <div className="compare-snapshot-card__price">
            {row.livePriceValue === null ? '—' : `$${formatPrice(row.livePriceValue)}`}
          </div>
          <div className="compare-snapshot-card__forecast">
            {row.targetShortLabel} | {row.horizonLabel}
          </div>
        </div>
        <div className="compare-snapshot-card__badges">
          <StatusBadge tone={row.intradayTone}>{row.intradayLabel}</StatusBadge>
          <StatusBadge tone={formatDecisionTone(row.tradeDecision)}>{row.tradeDecision}</StatusBadge>
          <StatusBadge tone={row.executionTone}>{row.executionLabel}</StatusBadge>
          <StatusBadge tone={row.vehicleTone}>{row.vehicleLabel}</StatusBadge>
          <StatusBadge tone={row.contractQualityTone}>{row.contractQualityLabel}</StatusBadge>
        </div>
      </div>

      <div className="compare-snapshot-card__subhead">
        <span>{row.forecastLabel}</span>
        <span>{row.newsLabel}</span>
        <span>{row.flowLabel}</span>
        <span>{row.optionExecutionScoreLabel}</span>
        <span>{row.regimeLabel}</span>
        <span>{row.executionRouteLabel}</span>
      </div>

      <div className="compare-snapshot-pillars" aria-label={`${row.ticker} research pillars`}>
        {pillars.map((pillar) => (
          <div key={pillar.key} className="compare-snapshot-pillars__item">
            <div className="compare-snapshot-pillars__label-row">
              <span>{pillar.label}</span>
              <strong>{Math.round(pillar.value)}</strong>
            </div>
            <div className="compare-snapshot-pillars__track">
              <div className={`compare-snapshot-pillars__fill compare-snapshot-pillars__fill--${pillar.tone}`} style={{ width: `${pillar.value}%` }} />
            </div>
          </div>
        ))}
      </div>

      <div className="compare-snapshot-card__path">
        <div className="compare-snapshot-card__path-head">
          <span>Price path</span>
          <strong>{pathModel ? `${formatPrice(pathModel.lower)} to ${formatPrice(pathModel.upper)}` : 'Path pending'}</strong>
        </div>
        {pathModel ? (
          <>
            <div className="compare-snapshot-path">
              <div className="compare-snapshot-path__rail" />
              {pathModel.entryLowPct !== null ? (
                <span
                  className="compare-snapshot-path__band compare-snapshot-path__band--entry"
                  style={{
                    left: `${pathModel.entryLowPct}%`,
                    width: `${Math.max(2, (pathModel.entryHighPct ?? pathModel.entryLowPct) - pathModel.entryLowPct)}%`,
                  }}
                />
              ) : null}
              {pathModel.expectedPct !== null ? <span className="compare-snapshot-path__marker compare-snapshot-path__marker--target" style={{ left: `${pathModel.expectedPct}%` }} /> : null}
              {pathModel.livePct !== null ? <span className="compare-snapshot-path__marker compare-snapshot-path__marker--live" style={{ left: `${pathModel.livePct}%` }} /> : null}
            </div>
            <div className="compare-snapshot-path__legend">
              <span>Entry {row.entryZoneLabel}</span>
              <span>Live {row.livePriceLabel}</span>
              <span>Expected {row.expectedPriceLabel}</span>
            </div>
          </>
        ) : (
          <p className="compare-snapshot-card__path-empty">Entry zone and expected price are not both available yet.</p>
        )}
      </div>

      <div className="compare-snapshot-card__notes">
        <p>{row.vehicleLabel}: {row.vehicleReason}</p>
        <p>{row.optionExecutionRejectSummary || row.optionExecutionDetail}</p>
        <p>{row.decisionGateAction}</p>
        <p>{row.newsSummary}</p>
        <p>{row.newsDetail}</p>
        <p>{row.flowDetail}</p>
        <p>{row.trustDetail}</p>
        <p>{row.executionDetail}</p>
      </div>

      <div className="compare-snapshot-card__footer">
        <Button type="button" variant="ghost" onClick={onOpenDesk}>
          Open on desk
        </Button>
      </div>
    </article>
  )
}

export default function ComparePage() {
  const location = useLocation()
  const navigate = useNavigate()
  const { preferences } = usePreferences()
  const { pushToast } = useToast()
  const workflowAutoloadRef = useRef('')
  const [bootstrap, setBootstrap] = useState(null)
  const [form, setForm] = useState({
    tickers: (preferences?.watchlistDefaults || ['SPY', 'QQQ', 'NVDA']).slice(0, 5).join(', '),
    interval: preferences?.defaultInterval || '5m',
    horizon: preferences?.defaultHorizon || 5,
  })
  const [payload, setPayload] = useState(null)
  const [selectedTicker, setSelectedTicker] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [formErrors, setFormErrors] = useState({})
  const [actionIssue, setActionIssue] = useState(null)
  const tradingStyle = String(preferences?.tradingStyle || 'intraday').trim().toLowerCase() === 'intraday' ? 'intraday' : 'swing'
  const intradayPresetProfile = getIntradayPresetProfile(preferences?.intradayPreset)
  const intradayPresetGuide = buildIntradayPresetGuide({ preset: preferences?.intradayPreset, page: 'compare' })
  const intradayLoadGuide = buildIntradayPresetGuide({ preset: preferences?.intradayPreset, page: 'watchlist' })
  const intervalOptions = useMemo(
    () => getStyleIntervalOptions(tradingStyle, bootstrap?.defaults?.supported_intervals || []),
    [bootstrap?.defaults?.supported_intervals, tradingStyle],
  )
  const sessionModel = useMemo(
    () =>
      buildTradingSessionModel({
        tradingStyle,
        regularHoursOnly: preferences?.regularHoursOnly === true,
        openingRangeMinutes: preferences?.openingRangeMinutes,
        flattenBeforeCloseMinutes: preferences?.flattenBeforeCloseMinutes,
      }),
    [
      preferences?.flattenBeforeCloseMinutes,
      preferences?.openingRangeMinutes,
      preferences?.regularHoursOnly,
      tradingStyle,
    ],
  )
  const intervalModel = useMemo(
    () =>
      buildIntervalModel({
        tradingStyle,
        interval: form.interval,
        horizon: form.horizon,
      }),
    [form.horizon, form.interval, tradingStyle],
  )
  const compareMarketModel = useMemo(
    () => ({
      tradingStyle,
      intradayEventGuardMinutes: preferences?.intradayEventGuardMinutes,
      sessionModel,
    }),
    [preferences?.intradayEventGuardMinutes, sessionModel, tradingStyle],
  )
  const boardMode = useMemo(
    () => buildIntradayBoardMode({ tradingStyle, sessionModel, intervalModel }),
    [intervalModel, sessionModel, tradingStyle],
  )
  const workflowParams = useMemo(() => parseCompareWorkflowParams(location.search), [location.search])
  const candidateQueueNavigation = useKeyboardListNavigation({ selector: '.candidate-queue__item', layout: 'grid' })
  const leaderboardNavigation = useKeyboardListNavigation({ selector: '.table-row-action', layout: 'list' })

  usePageActionShortcuts({
    focusInput: () => focusFirstMatching(['#compare-ticker-input']),
    focusResult: () => focusFirstMatching([
      '.candidate-queue__grid .candidate-queue__item',
      '.ui-list-table .table-row-action',
    ]),
  })

  useEffect(() => {
    getBootstrap('compare')
      .then((data) => setBootstrap(data))
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (!intervalOptions.length) return
    setForm((state) => {
      if (intervalOptions.includes(state.interval)) return state
      const fallbackInterval = intervalOptions[0] || preferences?.defaultInterval || '5m'
      return {
        ...state,
        interval: fallbackInterval,
      }
    })
  }, [intervalOptions, preferences?.defaultInterval])

  useEffect(() => {
    if (!workflowParams.hasAny) return
    setForm((state) => ({
      ...state,
      tickers: workflowParams.tickersLabel || state.tickers,
      interval: workflowParams.interval || state.interval,
      horizon: workflowParams.horizon || state.horizon,
    }))
    setFormErrors({})
    setActionIssue(null)
    if (workflowParams.focusTicker) {
      setSelectedTicker(workflowParams.focusTicker)
    }
  }, [workflowParams])

  useEffect(() => {
    if (!workflowParams.workflowAutoload || workflowParams.tickers.length < 2) return
    const requestKey = [
      workflowParams.tickers.join(','),
      workflowParams.interval || form.interval,
      workflowParams.horizon || form.horizon,
      workflowParams.focusTicker,
    ].join('|')
    if (workflowAutoloadRef.current === requestKey) return
    workflowAutoloadRef.current = requestKey
    let cancelled = false

    async function runWorkflowCompare() {
      const tickers = workflowParams.tickers
      const interval = workflowParams.interval || form.interval
      const horizon = workflowParams.horizon || form.horizon
      const data = await runComparison(tickers, {
        interval,
        horizon,
        focusTicker: workflowParams.focusTicker,
        successMessage:
          workflowParams.workflowFrom === 'watchlist'
            ? 'Loaded watchlist leaders into compare.'
            : `Loaded ${tickers.length} tickers into compare.`,
      })
      if (cancelled || !data) return
    }

    void runWorkflowCompare()
    return () => {
      cancelled = true
    }
  }, [workflowParams, form.interval, form.horizon, pushToast])

  const comparisonRows = useMemo(
    () => buildComparisonRows(payload, compareMarketModel),
    [compareMarketModel, payload],
  )
  const candidateQueue = useMemo(
    () => buildIntradayCandidateQueue(comparisonRows, compareMarketModel),
    [compareMarketModel, comparisonRows],
  )
  const activeRow = useMemo(
    () => comparisonRows.find((row) => row.ticker === selectedTicker) || comparisonRows[0] || null,
    [comparisonRows, selectedTicker],
  )
  const snapshotRows = useMemo(
    () => comparisonRows.slice(0, Math.min(comparisonRows.length, 6)),
    [comparisonRows],
  )
  const controlledUniverse = useMemo(() => {
    const candidates = bootstrap?.defaults?.controlled_liquid_universe
    if (Array.isArray(candidates) && candidates.length) {
      return candidates
    }
    if (Array.isArray(preferences?.watchlistDefaults) && preferences.watchlistDefaults.length) {
      return preferences.watchlistDefaults
    }
    return normalizeTickers(form.tickers)
  }, [bootstrap, preferences?.watchlistDefaults, form.tickers])

  const summaryCards = useMemo(() => {
    const summary = payload?.summary || {}
    const rankingBoard = summary?.ranking_board || {}
    const confidenceValues = comparisonRows.map((row) => toNumber(row.chartPayload?.forecast?.confidence_score)).filter((value) => value !== null)
    const sigmaValues = comparisonRows.map((row) => toNumber(row.chartPayload?.strategy?.current_sigma_pct)).filter((value) => value !== null)
    const highTrustCount = comparisonRows.filter((row) => row.trustTone === 'positive').length
    const fragileCount = comparisonRows.filter((row) => row.trustTone === 'negative').length
    const cleanExecutionCount = comparisonRows.filter((row) => row.executionTone === 'positive').length
    const fragileExecutionCount = comparisonRows.filter((row) => row.executionTone === 'negative').length
    const establishedCount = comparisonRows.filter((row) => row.targetQualityTone === 'positive').length
    const thinSampleCount = comparisonRows.filter((row) => row.targetQualityTone === 'negative').length
    const urgentCatalystCount = comparisonRows.filter((row) => row.eventPriorityActive && row.eventPriorityDays !== null && row.eventPriorityDays <= 3).length
    const catalystQueueCount = comparisonRows.filter((row) => row.eventPriorityActive).length
    const strongEventCount = comparisonRows.filter((row) => row.eventMemoryTone === 'positive').length
    const weakEventCount = comparisonRows.filter((row) => row.eventMemoryTone === 'negative').length
    const strongSessionCount = comparisonRows.filter((row) => row.sessionMemoryTone === 'positive').length
    const weakSessionCount = comparisonRows.filter((row) => row.sessionMemoryTone === 'negative').length
    const strongMemoryCount = comparisonRows.filter((row) => row.memoryTone === 'positive').length
    const weakMemoryCount = comparisonRows.filter((row) => row.memoryTone === 'negative').length
    const beatBaselineCount = comparisonRows.filter((row) => row.benchmarkTone === 'positive').length
    const belowBaselineCount = comparisonRows.filter((row) => row.benchmarkTone === 'negative').length
    const killSwitchCount = comparisonRows.filter((row) => row.driftTone === 'negative').length
    const watchDriftCount = comparisonRows.filter((row) => row.driftTone === 'warning').length
    const readyNowCount = comparisonRows.filter((row) => row.intradayBucket === 'ready').length
    const patienceCount = comparisonRows.filter((row) => row.intradayBucket === 'patience').length
    const guardedCount = comparisonRows.filter((row) => row.intradayBucket === 'guarded').length
    const cleanupCount = comparisonRows.filter((row) => row.intradayBucket === 'cleanup' || row.intradayBucket === 'prep').length
    const boardMetricTone = ['positive', 'warning', 'negative'].includes(boardMode.tone) ? boardMode.tone : 'default'
    const promoteCount = toNumber(rankingBoard.promote_count) ?? comparisonRows.filter((row) => row.rankingTier === 'promote').length
    const reviewCount = toNumber(rankingBoard.review_count) ?? comparisonRows.filter((row) => row.rankingTier === 'review').length
    const standDownCount = toNumber(rankingBoard.stand_down_count) ?? comparisonRows.filter((row) => row.rankingTier === 'stand_down').length
    const primaryTargetLabel = comparisonRows[0]?.targetShortLabel || '—'
    const horizonLabel = comparisonRows[0]?.horizonLabel || formatForecastHorizon(payload?.interval, payload?.horizon)
    const avgConfidence = confidenceValues.length
      ? confidenceValues.reduce((sum, value) => sum + value, 0) / confidenceValues.length
      : null
    const avgSigma = sigmaValues.length
      ? sigmaValues.reduce((sum, value) => sum + value, 0) / sigmaValues.length
      : null
    const rankingValues = comparisonRows
      .map((row) => toNumber(row.rankingScore ?? row.setupScore))
      .filter((value) => value !== null)
    const avgRankingScore = rankingValues.length
      ? rankingValues.reduce((sum, value) => sum + value, 0) / rankingValues.length
      : null
    if (tradingStyle === 'intraday') {
      return [
        { label: 'Compared', value: summary.count ?? 0, helper: `${summary.valid_trades ?? 0} valid setups` },
        { label: 'Board mode', value: boardMode.label, helper: sessionModel.label, tone: boardMetricTone },
        { label: 'Ready now', value: readyNowCount, helper: 'Names that fit the current tape', tone: readyNowCount > 0 ? 'positive' : 'default' },
        { label: 'Patience only', value: patienceCount, helper: 'Names that still need cleaner tape', tone: patienceCount > 0 ? 'warning' : 'default' },
        { label: 'Guarded', value: guardedCount, helper: 'Blocked by event, fills, or drift', tone: guardedCount > 0 ? 'negative' : 'default' },
        { label: 'Cleanup bias', value: cleanupCount, helper: 'Prep or flatten-first names', tone: cleanupCount > 0 ? 'warning' : 'default' },
        { label: 'Leader', value: payload?.leader?.ticker || '—', helper: payload?.leader?.ranking_label || payload?.leader?.setup_grade || 'No leader yet' },
        { label: 'Avg board score', value: avgRankingScore === null ? '—' : formatNumber(avgRankingScore, 1), helper: `${summary.bullish_count ?? 0} bullish / ${summary.bearish_count ?? 0} bearish` },
        { label: 'Avg confidence', value: avgConfidence === null ? '—' : formatPercent(avgConfidence, { ratio: true }), helper: 'Forecast confidence across compared names' },
        { label: 'Execution mix', value: `${cleanExecutionCount} clean`, helper: `${fragileExecutionCount} setups still need strict price control` },
        { label: 'Event watch', value: `${urgentCatalystCount} urgent`, helper: `${catalystQueueCount} names still carry catalyst pressure` },
        { label: 'Drift watch', value: `${killSwitchCount} kill switch`, helper: `${watchDriftCount} setups are degrading but still reviewable` },
      ]
    }
    return [
      { label: 'Compared', value: summary.count ?? 0, helper: `${summary.valid_trades ?? 0} valid setups` },
      { label: 'Promote first', value: promoteCount, helper: `${reviewCount} reviewable / ${standDownCount} stand down` },
      { label: 'Leader', value: payload?.leader?.ticker || '—', helper: payload?.leader?.ranking_label || payload?.leader?.setup_grade || 'No leader yet' },
      { label: 'Avg board score', value: avgRankingScore === null ? '—' : formatNumber(avgRankingScore, 1), helper: `${summary.bullish_count ?? 0} bullish / ${summary.bearish_count ?? 0} bearish` },
      { label: 'Forecast target', value: primaryTargetLabel, helper: 'Shared compare framing for this run' },
      { label: 'Horizon', value: horizonLabel, helper: `${payload?.interval || form.interval} bars across every name` },
      { label: 'Avg confidence', value: avgConfidence === null ? '—' : formatPercent(avgConfidence, { ratio: true }), helper: 'Forecast confidence across compared names' },
      { label: 'Avg sigma', value: avgSigma === null ? '—' : formatPercent(avgSigma, { ratio: true, digits: 2 }), helper: 'Current strategy volatility band width' },
      { label: 'Trust mix', value: `${highTrustCount} high`, helper: `${fragileCount} fragile forecasts need extra review` },
      { label: 'Execution mix', value: `${cleanExecutionCount} clean`, helper: `${fragileExecutionCount} setups need stricter price control` },
      { label: 'Sample quality', value: `${establishedCount} established`, helper: `${thinSampleCount} setups still rely on thin calibration history` },
      { label: 'Calendar pressure', value: `${urgentCatalystCount} urgent`, helper: `${catalystQueueCount} names have catalysts on deck` },
      { label: 'Event memory', value: `${strongEventCount} strong`, helper: `${weakEventCount} setups sit in historically weak event windows` },
      { label: 'Session memory', value: `${strongSessionCount} strong`, helper: `${weakSessionCount} setups sit in historically weak session states` },
      { label: 'Regime memory', value: `${strongMemoryCount} strong`, helper: `${weakMemoryCount} setups are sitting in historically weak memory states` },
      { label: 'Benchmark edge', value: `${beatBaselineCount} above`, helper: `${belowBaselineCount} setups are failing their current benchmark` },
      { label: 'Drift watch', value: `${killSwitchCount} kill switch`, helper: `${watchDriftCount} setups are degrading but still reviewable` },
      { label: 'Decision gate', value: `${promoteCount} clear`, helper: `${standDownCount} setups should stand down instead of being promoted` },
    ]
  }, [boardMode.label, boardMode.tone, comparisonRows, form.interval, payload, sessionModel.label, tradingStyle])

  async function runComparison(tickers, options = {}) {
    const interval = options.interval || form.interval
    const horizon = Number(options.horizon ?? form.horizon)
    const successMessage = options.successMessage || ''
    const focusTicker = options.focusTicker || ''
    try {
      setLoading(true)
      setError('')
      setActionIssue(null)
      const data = await compareTickers({
        tickers,
        interval,
        horizon,
        points_limit: 250,
        regular_hours_only: preferences?.regularHoursOnly === true,
      })
      setPayload(data)
      setSelectedTicker(focusTicker || data?.leader?.ticker || tickers[0] || '')
      if (successMessage) {
        pushToast(successMessage, 'success')
      }
      return data
    } catch (err) {
      const message = err?.response?.data?.detail || err.message || 'Comparison failed.'
      setError(message)
      pushToast(message, 'error')
      return null
    } finally {
      setLoading(false)
    }
  }

  function handleLoadControlledBoard() {
    const nextTickers = controlledUniverse.slice(0, 6)
    if (!nextTickers.length) {
      pushToast('No liquid-board compare set is available yet.', 'warning')
      return
    }
    setForm((state) => ({ ...state, tickers: nextTickers.join(', ') }))
    setFormErrors((current) => omitKeys(current, ['tickers']))
    setActionIssue(null)
    setSelectedTicker(nextTickers[0] || '')
    pushToast(`Loaded ${nextTickers.length} liquid-board names into compare.`, 'success')
  }

  async function handleSubmit(event) {
    event.preventDefault()
    const nextErrors = buildCompareFormErrors(form)
    if (Object.keys(nextErrors).length) {
      setFormErrors(nextErrors)
      pushToast('Fix the highlighted compare fields and try again.', 'error')
      return
    }
    const tickers = normalizeTickers(form.tickers)
    setFormErrors({})
    await runComparison(tickers, {
      successMessage: `Compared ${tickers.length} tickers.`,
    })
  }

  async function handleSaveWorkspace() {
    const nextErrors = buildCompareFormErrors(form)
    if (Object.keys(nextErrors).length) {
      setFormErrors(nextErrors)
      setActionIssue({
        tone: 'warning',
        title: 'Compare board is not ready to save',
        description: 'Fix the active compare inputs before saving this board layout.',
      })
      pushToast('Fix the highlighted compare fields before saving the board.', 'error')
      return
    }
    if (!payload) {
      setActionIssue({
        tone: 'info',
        title: 'Run the board before saving it',
        description: 'Save the compare board after a compare run so the stored workspace includes the current ranking and validation artifact.',
      })
      pushToast('Run the compare board once before saving the layout.', 'warning')
      return
    }
    try {
      const tickers = normalizeTickers(form.tickers)
      setActionIssue(null)
      await saveWorkspace({
        name: `compare-${tickers.join('-').slice(0, 40)}`,
        page: 'compare',
        payload: {
          ...form,
          tickers,
          validation_artifact: payload?.validation_artifact || null,
        },
        notes: tradingStyle === 'intraday' ? 'Saved from the intraday compare board.' : 'Saved from the compare board.',
        tags: [
          tradingStyle === 'intraday' ? 'intraday-board' : null,
          'compare-board',
          'candidate-board',
          payload?.validation_artifact ? 'validation-artifact' : null,
        ].filter(Boolean),
      })
      pushToast(tradingStyle === 'intraday' ? 'Intraday compare board saved.' : 'Compare board saved.', 'success')
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || (tradingStyle === 'intraday' ? 'Failed to save the intraday compare board.' : 'Failed to save the compare board.'), 'error')
    }
  }

  const currentCompareTickers = normalizeTickers(form.tickers)
  const canRetryCompare = currentCompareTickers.length >= 2
  const canSaveBoard = Boolean(payload) && !Object.keys(buildCompareFormErrors(form)).length

  return (
    <>
      {error ? (
        <ErrorState
          title={tradingStyle === 'intraday' ? 'Intraday compare board unavailable' : 'Compare board unavailable'}
          description={error}
          actionLabel={canRetryCompare ? 'Run compare again' : (tradingStyle === 'intraday' ? intradayLoadGuide.actionLabel : 'Load liquid board')}
          onAction={() => {
            if (canRetryCompare) {
              void runComparison(currentCompareTickers, {
                successMessage: `Compared ${currentCompareTickers.length} tickers.`,
              })
            } else {
              handleLoadControlledBoard()
            }
          }}
        />
      ) : null}
      <PageIntro
        kicker="Compare board"
        title={tradingStyle === 'intraday' ? intradayPresetGuide.title : 'Qualify the liquid board by target and horizon'}
        description={
          tradingStyle === 'intraday'
            ? intradayPresetGuide.description
            : 'Use the compare board to rank liquid-board leaders under one shared interval and horizon, then read direction, volatility context, and execution readiness in the same place.'
        }
        helper={
          tradingStyle === 'intraday'
            ? intradayPresetGuide.helper
            : 'Read this page top to bottom: confirm the active session, set the shared frame, confirm the queue, then inspect the selected leader before opening the desk.'
        }
        badge={
          workflowParams.workflowFrom === 'watchlist'
            ? `${tradingStyle === 'intraday' ? `${intradayPresetProfile.shortLabel} | ` : ''}${boardMode.label} handoff | ${sessionModel.label}`
            : `${tradingStyle === 'intraday' ? `${intradayPresetProfile.shortLabel} | ` : ''}${boardMode.label} | ${sessionModel.label}`
        }
        actions={(
          <ActionBar compact>
            <Chip tone="neutral" size="sm">/ focus tickers</Chip>
            <Chip tone="neutral" size="sm">Shift+J jump to queue</Chip>
            <Button type="button" variant="subtle" onClick={handleSaveWorkspace} disabled={!canSaveBoard}>
              {tradingStyle === 'intraday' ? 'Save intraday board' : 'Save compare board'}
            </Button>
          </ActionBar>
        )}
      />
      <FeedbackState
        tone={boardMode.tone}
        title={`${boardMode.label} | ${intervalModel.label}`}
        description={`${tradingStyle === 'intraday' ? `${intradayPresetProfile.description} ` : ''}${boardMode.detail} ${intervalModel.recommendedDetail} ${preferences?.regularHoursOnly === true ? 'Regular-hours routing is explicitly selected.' : 'Session-flex routing is available if the name still clears execution quality.'}`}
      />
      <WorkflowGuide
        showSteps={false}
        phaseLabel="Phase 2 - Qualify"
        phaseTone="warning"
        title={tradingStyle === 'intraday' ? `Use compare to decide which ${intradayPresetProfile.shortLabel.toLowerCase()} leader still survives under one shared frame.` : 'Use compare to decide which leader still survives when the framing is held constant.'}
        description={
          tradingStyle === 'intraday'
            ? `${intradayPresetProfile.description} The compare board is a same-session qualification surface.`
            : 'The compare board is a qualification surface. It should tell you whether the top name is still trustworthy once direction, volatility, execution, and catalyst pressure are read together.'
        }
        steps={buildWorkflowSteps(1)}
        cards={[
          {
            label: 'Use this page for',
            value: tradingStyle === 'intraday' ? `Stress-test ${intradayPresetProfile.shortLabel.toLowerCase()} leaders under one shared horizon.` : 'Stress-test leaders under one shared horizon.',
            detail: tradingStyle === 'intraday'
              ? 'A row only means something relative to the same interval, horizon, and session phase.'
              : 'A row only means something relative to the same interval, horizon, and target definition.',
          },
          {
            label: 'Best next move',
            value: tradingStyle === 'intraday' ? 'Advance only names that still fit the current tape once fills and catalyst pressure are visible together.' : 'Advance only names that clear trust, execution, and calendar checks together.',
            detail: tradingStyle === 'intraday'
              ? `The best ${intradayPresetProfile.shortLabel.toLowerCase()} candidate is the name that still fits the session, not the one with the loudest score.`
              : 'The best candidate is the one that stays clean after the rank is decomposed, not the one with the loudest score.',
            tone: 'positive',
            actionLabel: activeRow ? `Open ${activeRow.ticker} on desk` : 'Open leader on desk',
            onAction: () =>
              navigate(
                buildDashboardWorkflowUrl({
                  ticker: activeRow?.ticker,
                  interval: form.interval,
                  horizon: form.horizon,
                  tickers: comparisonRows.map((row) => row.ticker).filter(Boolean),
                  focusTicker: activeRow?.ticker,
                }),
              ),
            actionDisabled: !activeRow?.ticker,
          },
          {
            label: 'Do not ignore',
            value: tradingStyle === 'intraday' ? 'Relative rank is not the same thing as same-session readiness.' : 'Relative rank is not the same thing as a safe trade.',
            detail: tradingStyle === 'intraday'
              ? 'A comparison winner can still be too fragile if the session is thin, event pressure is active, or the exit window is shrinking.'
              : 'A comparison winner can still be too fragile if event pressure, spread drag, or thin sample quality are unresolved.',
            tone: 'warning',
          },
        ]}
      />

        <SectionCard
          eyebrow={tradingStyle === 'intraday' ? 'Shared intraday frame' : 'Shared frame'}
          title={tradingStyle === 'intraday' ? 'Intraday comparison controls' : 'Comparison controls'}
          subtitle={
            tradingStyle === 'intraday'
              ? 'Rank multiple tickers under the same interval, horizon, and session-aware intraday framing.'
              : 'Rank multiple tickers under the same interval, horizon, and forecast framing.'
          }
        >
          <form className="analysis-form analysis-form--wide" onSubmit={handleSubmit}>
          <TickerInput
            id="compare-ticker-suggestions"
            inputId="compare-ticker-input"
            label="Tickers"
            hint="Use at least two valid symbols under one shared interval and horizon."
            error={formErrors.tickers}
            required
            value={form.tickers}
            onChange={(value) => {
              setForm((state) => ({ ...state, tickers: value }))
              setFormErrors((current) => omitKeys(current, ['tickers']))
              setActionIssue(null)
            }}
            placeholder="SPY, QQQ, NVDA, AAPL"
          />
          <SelectField
            label="Interval"
            hint={tradingStyle === 'intraday' ? `Intraday mode favors ${intervalOptions.slice(0, 3).join(', ')} first.` : `Swing mode keeps ${intervalOptions.slice(0, 3).join(', ')} closer to the front.`}
            value={form.interval}
            onChange={(e) => setForm((s) => ({ ...s, interval: e.target.value }))}
          >
            {intervalOptions.map((interval) => <option key={interval} value={interval}>{interval}</option>)}
          </SelectField>
          <TextField
            label="Horizon"
            hint={intervalModel.recommendedDetail}
            error={formErrors.horizon}
            type="number"
            min="1"
            max="50"
            value={form.horizon}
            onChange={(e) => {
              setForm((s) => ({ ...s, horizon: Number(e.target.value) }))
              setFormErrors((current) => omitKeys(current, ['horizon']))
              setActionIssue(null)
            }}
          />
          <Button type="submit" variant="solid" disabled={loading}>
            {loading ? 'Comparing...' : 'Compare'}
          </Button>
          <Button type="button" variant="ghost" onClick={handleLoadControlledBoard}>
            {tradingStyle === 'intraday' ? intradayLoadGuide.actionLabel : 'Load liquid board'}
          </Button>
        </form>
        {actionIssue ? (
          <FeedbackState
            compact
            tone={actionIssue.tone}
            title={actionIssue.title}
            description={actionIssue.description}
          />
        ) : null}
        <TickerHub
          compact
          activeTicker={selectedTicker}
          onSelectTicker={(ticker) => setSelectedTicker(ticker)}
          onLoadFavorites={(favorites) => setForm((state) => ({ ...state, tickers: favorites.slice(0, 8).join(', ') }))}
        />
      </SectionCard>
      <EducationCallout
        topic="compare-workflow"
        title={tradingStyle === 'intraday' ? `Use compare as a ${intradayPresetProfile.shortLabel.toLowerCase()} qualification surface, not a forced entry screen.` : 'Use compare as a ranking surface, not a blanket market-timing screen.'}
        body={
          tradingStyle === 'intraday'
            ? `${intradayPresetProfile.description} The compare board is strongest when it explains which leaders still fit the active session, not when it tries to force every top score into an order ticket.`
            : 'The compare board is strongest when it explains what is being predicted, over what horizon, and how much of the read comes from direction versus volatility context.'
        }
        bullets={[
          'Probability up is the directional part of the read.',
          'Expected move and current sigma add the volatility layer behind the rank.',
          tradingStyle === 'intraday'
            ? `${intradayPresetGuide.helper} Read every row under the same shared horizon and session phase before you compare names.`
            : 'Every row should be read under the same shared horizon before you compare names.',
        ]}
        linkLabel="Open compare guide"
      />

      {loading && !payload ? (
        <LoadingBlock
          label={tradingStyle === 'intraday' ? 'Building intraday compare board' : 'Building compare board'}
          detail={
            tradingStyle === 'intraday'
              ? 'Ranking the current symbols under one shared same-session frame so the front-of-queue leader is easier to qualify.'
              : 'Ranking the current symbols under one shared horizon so the leader is easier to qualify.'
          }
        />
      ) : null}

      {payload ? (
        <>
          <section className="metrics-grid">
            {summaryCards.map((item) => <MetricCard key={item.label} {...item} />)}
          </section>

          <SectionCard
            eyebrow="Visual research"
            title="Leader snapshot board"
            subtitle="Read the strongest names as compact research cards before you commit to the queue or the full leaderboard."
          >
            {snapshotRows.length ? (
              <div className="compare-snapshot-board">
                {snapshotRows.map((row) => (
                  <CompareSnapshotCard
                    key={`${row.key}-snapshot`}
                    row={row}
                    onOpenDesk={() =>
                      navigate(
                        buildDashboardWorkflowUrl({
                          ticker: row.ticker,
                          interval: form.interval,
                          horizon: form.horizon,
                          tickers: comparisonRows.map((entry) => entry.ticker).filter(Boolean),
                          focusTicker: row.ticker,
                        }),
                      )
                    }
                  />
                ))}
              </div>
            ) : (
              <EmptyState
                title="No snapshot board yet"
                description="Run a comparison first, then the strongest names will render here as visual research cards."
              />
            )}
          </SectionCard>

          <SectionCard
            eyebrow="Decision queue"
            title="Candidate queue"
            subtitle={candidateQueue.detail}
          >
            {candidateQueue.rows.length ? (
              <div
                ref={candidateQueueNavigation.containerRef}
                className="candidate-queue__grid"
                onKeyDown={candidateQueueNavigation.onKeyDown}
              >
                {candidateQueue.rows.map((row) => (
                  <Button
                    key={`${row.key}-candidate`}
                    type="button"
                    variant="ghost"
                    size="sm"
                    className={`candidate-queue__item candidate-queue__item--${row.decisionGateTone}`}
                    onClick={() => setSelectedTicker(row.ticker)}
                  >
                    <div className="candidate-queue__meta">
                      <strong>{row.ticker}</strong>
                      <span className={`execution-state-badge execution-state-badge--${row.decisionGateTone}`}>
                        {row.decisionGateLabel}
                      </span>
                    </div>
                    <div className="ui-list-cell__badges">
                      <StatusBadge tone={row.intradayTone}>{row.intradayLabel}</StatusBadge>
                      <StatusBadge tone={row.rankingTier === 'promote' ? 'positive' : row.rankingTier === 'stand_down' ? 'negative' : 'warning'}>
                        {row.rankingLabel}
                      </StatusBadge>
                      <StatusBadge tone={formatDecisionTone(row.tradeDecision)}>{row.tradeDecision}</StatusBadge>
                      <StatusBadge tone={row.executionTone}>{row.executionLabel}</StatusBadge>
                      <StatusBadge tone={row.vehicleTone}>{row.vehicleLabel}</StatusBadge>
                      <StatusBadge tone={row.contractQualityTone}>{row.contractQualityLabel}</StatusBadge>
                      {row.eventPriorityActive ? (
                        <StatusBadge tone={row.eventPriorityTone}>{row.eventPriorityLabel}</StatusBadge>
                      ) : null}
                    </div>
                    <div className="candidate-queue__stack">
                      <span>{row.decisionGateAction}</span>
                      <span>{row.vehicleReason}</span>
                      <span>{row.intradayDetail}</span>
                      <span>{row.decisionGateDetail}</span>
                      <span>{row.rankingSummary || `${row.rankingLabel} on the shared liquid board.`}</span>
                      <span>{row.optionExecutionRejectSummary || row.optionExecutionDetail}</span>
                      {row.eventPriorityActive ? <span>{row.eventPriorityDetail}</span> : null}
                      <InlineMeta
                        as="span"
                        items={[
                          `Board ${row.scoreLabel}`,
                          `Prob ${row.probabilityLabel}`,
                          `Confidence ${row.confidenceLabel}`,
                          row.newsLabel,
                          row.optionExecutionScoreLabel,
                        ]}
                      />
                    </div>
                  </Button>
                ))}
              </div>
            ) : (
              <EmptyState
                title={tradingStyle === 'intraday' ? `No ${intradayPresetProfile.shortLabel.toLowerCase()} queue yet` : 'No candidate queue yet'}
                description={
                  tradingStyle === 'intraday'
                    ? `Start here with ${intradayLoadGuide.actionLabel.toLowerCase()} or at least two symbols, then let this page turn them into a focused ${intradayPresetProfile.shortLabel.toLowerCase()} qualification queue.`
                    : 'Start here with the liquid board or at least two symbols, then let this page turn them into a focused qualification queue.'
                }
                actionLabel={tradingStyle === 'intraday' ? intradayLoadGuide.actionLabel : 'Load liquid board'}
                onAction={handleLoadControlledBoard}
                secondaryActionLabel="Open watchlist"
                onSecondaryAction={() => navigate('/watchlist')}
              />
            )}
          </SectionCard>

          <section className="content-grid content-grid--wide">
            <SectionCard
              eyebrow="Relative rank"
              title="Comparison leaderboard"
              subtitle={activeRow ? `${activeRow.ticker} is selected for target, horizon, and execution review.` : 'Run a comparison to inspect the field.'}
            >
              <ListTable>
                <table
                  ref={leaderboardNavigation.containerRef}
                  className="signal-table ui-list-table"
                  onKeyDown={leaderboardNavigation.onKeyDown}
                >
                  <caption className="ui-visually-hidden">Comparison candidate ranking table</caption>
                  <thead>
                    <tr>
                      <th scope="col">Rank</th>
                      <th scope="col">Setup</th>
                      <th scope="col">Score</th>
                      <th scope="col">Forecast target</th>
                      <th scope="col">Horizon</th>
                      <th scope="col">Volatility</th>
                      <th scope="col">Decision</th>
                      <th scope="col">Execution</th>
                      <th scope="col">Contract</th>
                    </tr>
                  </thead>
                  <tbody>
                    {comparisonRows.map((row) => (
                      <tr key={row.key}>
                        <td>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="table-link table-row-action"
                            onClick={() => setSelectedTicker(row.ticker)}
                          >
                            #{row.rank}
                          </Button>
                        </td>
                        <td>
                          <div className="ui-list-cell">
                            <div className="ui-list-cell__title">{row.ticker}</div>
                            <InlineMeta as="div" className="ui-list-cell__meta" items={[row.verdict, row.direction]} />
                            <div className="ui-list-cell__badges">
                              <StatusBadge tone={formatDecisionTone(row.tradeDecision)}>{row.tradeDecision}</StatusBadge>
                              <StatusBadge tone="neutral">{row.regimeLabel}</StatusBadge>
                              <StatusBadge tone={row.newsTone}>{row.newsLabel}</StatusBadge>
                              <StatusBadge tone={row.flowTone}>{row.flowLabel}</StatusBadge>
                              <StatusBadge tone={row.vehicleTone}>{row.vehicleLabel}</StatusBadge>
                              <StatusBadge tone={row.contractQualityTone}>{row.contractQualityLabel}</StatusBadge>
                              {row.eventPriorityActive ? (
                                <StatusBadge tone={row.eventPriorityTone}>{row.eventPriorityLabel}</StatusBadge>
                              ) : null}
                            </div>
                          </div>
                        </td>
                        <td>
                          <div className="ui-list-cell">
                            <div className="ui-list-cell__title">{row.scoreLabel}</div>
                            <div className="ui-list-cell__meta">{`${row.rankGapLabel} · ${row.rankingLabel}`}</div>
                            <div className="ui-list-cell__stack">
                              <span>{row.componentSummary}</span>
                              {row.eventPriorityActive ? <span>{row.eventPriorityDetail}</span> : null}
                              <span>{row.setupGrade || row.convictionLabel}</span>
                              <span>{row.alignmentLabel}</span>
                            </div>
                          </div>
                        </td>
                        <td>
                          <div className="ui-list-cell">
                            <div className="ui-list-cell__title">{row.targetShortLabel}</div>
                            <div className="ui-list-cell__meta">{row.probabilityLabel} probability up</div>
                            <div className="ui-list-cell__stack">
                              <span>{row.intradayLabel}</span>
                              <span>Confidence {row.confidenceLabel}</span>
                              <span>{row.decisionGateLabel}</span>
                              <span>{row.trustLabel}</span>
                              <span>{row.targetQualityLabel}</span>
                              <span>{row.eventMemoryLabel}</span>
                              <span>{row.sessionMemoryLabel}</span>
                              <span>{row.memoryLabel}</span>
                              <span>{row.benchmarkLabel}</span>
                              <span>{row.driftLabel}</span>
                              <span>{row.vehicleReason}</span>
                              <span>{row.optionExecutionScoreLabel} | {row.contractQualityLabel}</span>
                              <span>{row.optionExecutionRejectSummary || row.optionExecutionDetail}</span>
                              <span>{row.newsSummary}</span>
                              <span>{row.flowSummary}</span>
                              <span>{row.targetUseLabel}</span>
                            </div>
                          </div>
                        </td>
                        <td>
                          <div className="ui-list-cell">
                            <div className="ui-list-cell__title">{row.horizonLabel}</div>
                            <div className="ui-list-cell__meta">{row.forecastLabel}</div>
                            <div className="ui-list-cell__stack">
                              <span>Expected move {row.expectedMoveLabel}</span>
                              <span>Expected price {row.expectedPriceLabel}</span>
                            </div>
                          </div>
                        </td>
                        <td>
                          <div className="ui-list-cell">
                            <div className="ui-list-cell__title">{row.sigmaLabel}</div>
                            <InlineMeta as="div" className="ui-list-cell__meta" items={[row.regimeLabel, row.sessionLabel]} />
                            <div className="ui-list-cell__stack">
                              <span>Live {row.livePriceLabel}</span>
                              <span>Entry {row.entryZoneLabel}</span>
                            </div>
                          </div>
                        </td>
                        <td>
                          <div className="ui-list-cell">
                            <div className="ui-list-cell__badges">
                              <StatusBadge tone={formatDecisionTone(row.executionAction)}>{row.executionAction}</StatusBadge>
                            </div>
                            <div className="ui-list-cell__meta">{row.tradeStatus}</div>
                            {row.rejectReason ? <div className="ui-list-cell__stack"><span>{row.rejectReason}</span></div> : null}
                          </div>
                        </td>
                        <td>
                          <div className="ui-list-cell">
                            <div className="ui-list-cell__title">{row.executionLabel}</div>
                            <div className="ui-list-cell__meta">{row.executionRouteLabel}</div>
                            <div className="ui-list-cell__stack">
                              <span>{row.spreadContextLabel}</span>
                              <span>{row.participationLabel}</span>
                            </div>
                          </div>
                        </td>
                        <td>
                          <div className="ui-list-cell">
                            <div className="ui-list-cell__title">{row.contractSymbol}</div>
                            <div className="ui-list-cell__meta">Target {row.targetLabel}</div>
                            <div className="ui-list-cell__stack">
                              <span>{row.executionDetail}</span>
                            </div>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </ListTable>
            </SectionCard>

            <SectionCard
              eyebrow="Current leader"
              title="Leader snapshot"
              subtitle={activeRow ? `${activeRow.ticker} target, horizon, and volatility framing.` : 'Select a ticker to inspect the current leader.'}
            >
              {activeRow ? (
                <>
                  <div className="ui-list-cell" style={{ marginBottom: 16 }}>
                    <div className="ui-list-cell__title">{activeRow.targetLabel}</div>
                    <InlineMeta as="div" className="ui-list-cell__meta" items={[activeRow.horizonLabel, activeRow.forecastLabel]} />
                    <div className="ui-list-cell__badges">
                      <StatusBadge tone={activeRow.freshnessTone}>{activeRow.freshnessStatus}</StatusBadge>
                      <StatusBadge tone="neutral">{activeRow.regimeLabel}</StatusBadge>
                      <StatusBadge tone={activeRow.newsTone}>{activeRow.newsLabel}</StatusBadge>
                      <StatusBadge tone={activeRow.flowTone}>{activeRow.flowLabel}</StatusBadge>
                      <StatusBadge tone={activeRow.vehicleTone}>{activeRow.vehicleLabel}</StatusBadge>
                      <StatusBadge tone={activeRow.contractQualityTone}>{activeRow.contractQualityLabel}</StatusBadge>
                      <StatusBadge tone={activeRow.intradayTone}>{activeRow.intradayLabel}</StatusBadge>
                      <StatusBadge tone={formatDecisionTone(activeRow.tradeDecision)}>{activeRow.tradeDecision}</StatusBadge>
                      <StatusBadge tone={activeRow.decisionGateTone}>{activeRow.decisionGateLabel}</StatusBadge>
                      <StatusBadge tone={activeRow.trustTone}>{activeRow.trustLabel}</StatusBadge>
                      <StatusBadge tone={activeRow.targetQualityTone}>{activeRow.targetQualityLabel}</StatusBadge>
                      {activeRow.eventPriorityActive ? (
                        <StatusBadge tone={activeRow.eventPriorityTone}>{activeRow.eventPriorityLabel}</StatusBadge>
                      ) : null}
                      <StatusBadge tone={activeRow.eventMemoryTone}>{activeRow.eventMemoryLabel}</StatusBadge>
                      <StatusBadge tone={activeRow.sessionMemoryTone}>{activeRow.sessionMemoryLabel}</StatusBadge>
                      <StatusBadge tone={activeRow.memoryTone}>{activeRow.memoryLabel}</StatusBadge>
                      <StatusBadge tone={activeRow.benchmarkTone}>{activeRow.benchmarkLabel}</StatusBadge>
                      <StatusBadge tone={activeRow.driftTone}>{activeRow.driftLabel}</StatusBadge>
                    </div>
                    <div className="ui-list-cell__stack">
                      <span>{activeRow.targetUseLabel}</span>
                      <span>{activeRow.intradayDetail}</span>
                      <span>{activeRow.decisionGateAction}</span>
                      <span>{activeRow.decisionGateDetail}</span>
                      <span>{activeRow.rankingSummary}</span>
                      <span>{activeRow.interpretiveTrustLabel}</span>
                      <span>{activeRow.vehicleReason}</span>
                      <span>{activeRow.optionExecutionScoreLabel} | {activeRow.contractQualityLabel}</span>
                      <span>{activeRow.optionExecutionRejectSummary || activeRow.optionExecutionDetail}</span>
                      <span>{activeRow.newsSummary}</span>
                      <span>{activeRow.newsDetail}</span>
                      <span>{activeRow.flowSummary}</span>
                      <span>{activeRow.flowDetail}</span>
                      <span>{activeRow.trustDetail}</span>
                      <span>{activeRow.targetQualityDetail}</span>
                      {activeRow.eventPriorityActive ? <span>{activeRow.eventPriorityDetail}</span> : null}
                      <span>{activeRow.eventMemoryDetail}</span>
                      <span>{activeRow.sessionMemoryDetail}</span>
                      <span>{activeRow.memoryDetail}</span>
                      <span>{activeRow.benchmarkDetail}</span>
                      <span>{activeRow.driftDetail}</span>
                    </div>
                  </div>
                  <div className="key-value-grid">
                  <div className="key-value-row"><span>Ticker</span><strong>{activeRow.ticker}</strong></div>
                  <div className="key-value-row"><span>Verdict</span><strong>{activeRow.verdict}</strong></div>
                  <div className="key-value-row"><span>Forecast target</span><strong>{activeRow.targetShortLabel}</strong></div>
                  <div className="key-value-row"><span>Horizon</span><strong>{activeRow.horizonLabel}</strong></div>
                  <div className="key-value-row"><span>Trade decision</span><strong>{activeRow.tradeDecision}</strong></div>
                  <div className="key-value-row"><span>Intraday posture</span><strong>{activeRow.intradayLabel}</strong></div>
                  <div className="key-value-row"><span>Execution action</span><strong>{activeRow.executionAction}</strong></div>
                  <div className="key-value-row"><span>Execution quality</span><strong>{activeRow.executionLabel}</strong></div>
                  <div className="key-value-row"><span>Route fit</span><strong>{activeRow.executionRouteLabel}</strong></div>
                  <div className="key-value-row"><span>Vehicle</span><strong>{activeRow.vehicleLabel}</strong></div>
                  <div className="key-value-row"><span>Vehicle reason</span><strong>{activeRow.vehicleReason}</strong></div>
                  <div className="key-value-row"><span>Option execution</span><strong>{activeRow.optionExecutionScoreLabel}</strong></div>
                  <div className="key-value-row"><span>Contract quality</span><strong>{activeRow.contractQualityLabel}</strong></div>
                  <div className="key-value-row"><span>News</span><strong>{activeRow.newsLabel}</strong></div>
                  <div className="key-value-row"><span>News coverage</span><strong>{activeRow.newsArticleCountLabel}</strong></div>
                  <div className="key-value-row"><span>News confidence</span><strong>{activeRow.newsConfidenceLabel}</strong></div>
                  <div className="key-value-row"><span>News source</span><strong>{activeRow.newsSourceLabel}</strong></div>
                  <div className="key-value-row"><span>Institutional flow</span><strong>{activeRow.flowLabel}</strong></div>
                  <div className="key-value-row"><span>Setup score</span><strong>{activeRow.scoreLabel}</strong></div>
                  <div className="key-value-row"><span>Probability up</span><strong>{activeRow.probabilityLabel}</strong></div>
                  <div className="key-value-row"><span>Confidence</span><strong>{activeRow.confidenceLabel}</strong></div>
                  <div className="key-value-row"><span>Decision gate</span><strong>{activeRow.decisionGateLabel}</strong></div>
                  <div className="key-value-row"><span>Trust</span><strong>{activeRow.trustLabel}</strong></div>
                  <div className="key-value-row"><span>Sample quality</span><strong>{activeRow.targetQualityLabel}</strong></div>
                  <div className="key-value-row"><span>Calendar pressure</span><strong>{activeRow.eventPriorityLabel}</strong></div>
                  <div className="key-value-row"><span>Event memory</span><strong>{activeRow.eventMemoryLabel}</strong></div>
                  <div className="key-value-row"><span>Session memory</span><strong>{activeRow.sessionMemoryLabel}</strong></div>
                  <div className="key-value-row"><span>Regime memory</span><strong>{activeRow.memoryLabel}</strong></div>
                  <div className="key-value-row"><span>Benchmark</span><strong>{activeRow.benchmarkLabel}</strong></div>
                  <div className="key-value-row"><span>Benchmark compare</span><strong>{activeRow.benchmarkComparison}</strong></div>
                  <div className="key-value-row"><span>Drift state</span><strong>{activeRow.driftLabel}</strong></div>
                  <div className="key-value-row"><span>Current sigma</span><strong>{activeRow.sigmaLabel}</strong></div>
                  <div className="key-value-row"><span>Expected move</span><strong>{activeRow.expectedMoveLabel}</strong></div>
                  <div className="key-value-row"><span>Regime</span><strong>{activeRow.regimeLabel}</strong></div>
                  <div className="key-value-row"><span>Regime strength</span><strong>{activeRow.regimeStrengthLabel}</strong></div>
                  <div className="key-value-row"><span>Resolved</span><strong>{activeRow.resolvedCountLabel}</strong></div>
                  <div className="key-value-row"><span>Hit rate</span><strong>{activeRow.empiricalHitRateLabel}</strong></div>
                  <div className="key-value-row"><span>Avg error</span><strong>{activeRow.averageErrorLabel}</strong></div>
                  <div className="key-value-row"><span>Scope</span><strong>{activeRow.calibrationScopeLabel}</strong></div>
                  <div className="key-value-row"><span>Contract</span><strong>{activeRow.contractSymbol}</strong></div>
                  <div className="key-value-row"><span>Spread</span><strong>{activeRow.spreadContextLabel}</strong></div>
                  <div className="key-value-row"><span>Participation</span><strong>{activeRow.participationLabel}</strong></div>
                  <div className="key-value-row"><span>Session</span><strong>{activeRow.sessionLabel}</strong></div>
                  <div className="key-value-row"><span>Freshness</span><strong>{activeRow.freshnessStatus}</strong></div>
                  <div className="key-value-row"><span>Live price</span><strong>{activeRow.livePriceLabel}</strong></div>
                  <div className="key-value-row"><span>Expected price</span><strong>{activeRow.expectedPriceLabel}</strong></div>
                  <div className="key-value-row"><span>Entry zone</span><strong>{activeRow.entryZoneLabel}</strong></div>
                  <div className="key-value-row"><span>Target</span><strong>{activeRow.targetLabel}</strong></div>
                </div>
                </>
              ) : (
                <EmptyState
                  title="No setup selected"
                  description={
                    comparisonRows.length
                      ? tradingStyle === 'intraday'
                        ? `Select a ticker from the comparison table to review whether the ${intradayPresetProfile.shortLabel.toLowerCase()} case still deserves desk attention.`
                        : 'Select a ticker from the comparison table to review its instrument and volatility context.'
                      : tradingStyle === 'intraday'
                        ? `Start here by running ${intradayLoadGuide.actionLabel.toLowerCase()} so this panel can turn the field into one focused ${intradayPresetProfile.shortLabel.toLowerCase()} setup.`
                        : 'Start here by running the compare board so this panel can turn the field into one focused setup.'
                  }
                  actionLabel={tradingStyle === 'intraday' ? intradayLoadGuide.actionLabel : 'Load liquid board'}
                  onAction={handleLoadControlledBoard}
                  secondaryActionLabel={!comparisonRows.length ? 'Open watchlist' : ''}
                  onSecondaryAction={!comparisonRows.length ? () => navigate('/watchlist') : null}
                />
              )}
            </SectionCard>
          </section>

          <section className="content-grid content-grid--wide">
            <SectionCard
              eyebrow="Visual context"
              title="Selected chart"
              subtitle={activeRow ? `${activeRow.ticker} chart snapshot with comparison overlays.` : 'Select a ticker from the leaderboard to view its chart.'}
            >
              {activeRow?.chartPayload ? (
                <CandlestickChart
                  payload={activeRow.chartPayload}
                  ticker={activeRow.ticker}
                  interval={form.interval}
                  height={560}
                  autoRefreshLabel="Compare snapshot"
                />
              ) : (
                <EmptyState
                  title="No chart selected"
                  description="Select a ticker from the leaderboard to view its chart."
                />
              )}
            </SectionCard>

            <SectionCard
              eyebrow="Read before route"
              title="Forecast framing and notes"
              subtitle="Why the selected setup is winning, and how to read the forecast correctly."
            >
              {activeRow ? (
                <div className="ui-list-cell">
                  <div className="ui-list-cell__title">{activeRow.forecastLabel}</div>
                  <div className="ui-list-cell__meta">{activeRow.rejectReason || 'No reject reason. This name is carrying the strongest relative setup score right now.'}</div>
                  <div className="ui-list-cell__badges">
                    <StatusBadge tone={formatDecisionTone(activeRow.tradeDecision)}>{activeRow.tradeDecision}</StatusBadge>
                    <StatusBadge tone={formatDecisionTone(activeRow.executionAction)}>{activeRow.executionAction}</StatusBadge>
                    <StatusBadge tone={activeRow.intradayTone}>{activeRow.intradayLabel}</StatusBadge>
                    <StatusBadge tone={activeRow.flowTone}>{activeRow.flowLabel}</StatusBadge>
                    <StatusBadge tone={activeRow.vehicleTone}>{activeRow.vehicleLabel}</StatusBadge>
                    <StatusBadge tone={activeRow.contractQualityTone}>{activeRow.contractQualityLabel}</StatusBadge>
                    <StatusBadge tone="neutral">{activeRow.targetShortLabel}</StatusBadge>
                    <StatusBadge tone={activeRow.freshnessTone}>{activeRow.freshnessStatus}</StatusBadge>
                    <StatusBadge tone={activeRow.decisionGateTone}>{activeRow.decisionGateLabel}</StatusBadge>
                    <StatusBadge tone={activeRow.trustTone}>{activeRow.trustLabel}</StatusBadge>
                    <StatusBadge tone={activeRow.targetQualityTone}>{activeRow.targetQualityLabel}</StatusBadge>
                    {activeRow.eventPriorityActive ? (
                      <StatusBadge tone={activeRow.eventPriorityTone}>{activeRow.eventPriorityLabel}</StatusBadge>
                    ) : null}
                    <StatusBadge tone={activeRow.eventMemoryTone}>{activeRow.eventMemoryLabel}</StatusBadge>
                    <StatusBadge tone={activeRow.sessionMemoryTone}>{activeRow.sessionMemoryLabel}</StatusBadge>
                    <StatusBadge tone={activeRow.memoryTone}>{activeRow.memoryLabel}</StatusBadge>
                    <StatusBadge tone={activeRow.benchmarkTone}>{activeRow.benchmarkLabel}</StatusBadge>
                    <StatusBadge tone={activeRow.driftTone}>{activeRow.driftLabel}</StatusBadge>
                  </div>
                  <div className="ui-list-cell__stack">
                    <span>{activeRow.targetUseLabel}</span>
                    <span>{activeRow.intradayDetail}</span>
                    <span>{activeRow.decisionGateAction}</span>
                    <span>{activeRow.decisionGateDetail}</span>
                    <span>{activeRow.interpretiveTrustLabel}</span>
                    <span>{activeRow.vehicleReason}</span>
                    <span>{activeRow.optionExecutionScoreLabel} | {activeRow.contractQualityLabel}</span>
                    <span>{activeRow.optionExecutionRejectSummary || activeRow.optionExecutionDetail}</span>
                    <span>{activeRow.flowSummary}</span>
                    <span>{activeRow.flowDetail}</span>
                    <span>{activeRow.trustDetail}</span>
                    <span>{activeRow.targetQualityDetail}</span>
                    {activeRow.eventPriorityActive ? <span>{activeRow.eventPriorityDetail}</span> : null}
                    <span>{activeRow.eventMemoryDetail}</span>
                    <span>{activeRow.sessionMemoryDetail}</span>
                    <span>{activeRow.memoryDetail}</span>
                    <span>{activeRow.benchmarkComparison}</span>
                    <span>{activeRow.benchmarkDetail}</span>
                    <span>{activeRow.driftAction}</span>
                    <span>{activeRow.driftDetail}</span>
                    <span>{activeRow.executionDetail}</span>
                  </div>
                </div>
              ) : (
                <EmptyState
                  title="No forecast framing yet"
                  description={
                    comparisonRows.length
                      ? tradingStyle === 'intraday'
                        ? `Select the current leader to review why the ${intradayPresetProfile.shortLabel.toLowerCase()} case is winning and what could still block it.`
                        : 'Select the current leader to review why it is winning and what could still block it.'
                      : tradingStyle === 'intraday'
                        ? `Start here by running ${intradayLoadGuide.actionLabel.toLowerCase()}, then inspect why the selected ticker is winning and what could still block the ${intradayPresetProfile.shortLabel.toLowerCase()} idea.`
                        : 'Start here by running the compare board, then inspect why the selected ticker is winning and what could still block it.'
                  }
                  actionLabel={tradingStyle === 'intraday' ? intradayLoadGuide.actionLabel : 'Load liquid board'}
                  onAction={handleLoadControlledBoard}
                  secondaryActionLabel={!comparisonRows.length ? 'Open watchlist' : ''}
                  onSecondaryAction={!comparisonRows.length ? () => navigate('/watchlist') : null}
                />
              )}
              {payload?.errors?.length ? (
                <div className="stack-list" style={{ marginTop: 18 }}>
                  {payload.errors.map((item) => (
                    <div key={`${item.ticker}-${item.error}`} className="workspace-summary-card">
                      <span>{item.ticker}</span>
                      <strong>{item.error}</strong>
                    </div>
                  ))}
                </div>
              ) : null}
            </SectionCard>
          </section>
        </>
      ) : null}
    </>
  )
}
