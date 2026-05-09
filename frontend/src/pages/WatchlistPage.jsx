import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getBootstrap, getFrontendFilters, getLiveBatch, getWatchlist, saveWorkspace } from '../api/client'
import ActionBar from '../components/ActionBar'
import Button from '../components/Button'
import EmptyState from '../components/EmptyState'
import ErrorState from '../components/ErrorState'
import FeedbackState from '../components/FeedbackState'
import { SelectField, TextField, ToggleField } from '../components/FormFields'
import ListTable from '../components/ListTable'
import LoadingBlock from '../components/LoadingBlock'
import TickerHub from '../components/TickerHub'
import SectionCard from '../components/SectionCard'
import SignalTable from '../components/SignalTable'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import StatusBadge from '../components/StatusBadge'
import WorkflowGuide, { buildWorkflowSteps } from '../components/WorkflowGuide'
import usePolling from '../hooks/usePolling'
import { useToast } from '../context/ToastContext'
import { usePreferences } from '../context/PreferencesContext'
import { appConfig } from '../config/appConfig'
import {
  buildEventWindowModel,
  buildIntervalModel,
  buildTradingSessionModel,
  getStyleIntervalOptions,
} from '../utils/intradayModel'
import {
  buildIntradayBoardMode,
  buildIntradayOpportunityState,
} from '../utils/intradayBoardModel'
import { buildIntradayPresetGuide, getIntradayPresetProfile } from '../utils/intradayPresetModel'
import { buildSignalTelemetry } from '../utils/signalTelemetry'
import { parseTickerList } from '../utils/validators'

const compactFormatter = new Intl.NumberFormat('en-US', {
  notation: 'compact',
  maximumFractionDigits: 1,
})

function toNumber(value) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

function formatLabel(value, fallback = 'Unknown') {
  const normalized = String(value || '').trim()
  if (!normalized) return fallback
  return normalized.replaceAll('_', ' ').replace(/\b\w/g, (character) => character.toUpperCase())
}

function summarizeInlineCopy(value, maxLength = 120) {
  const normalized = String(value || '')
    .replace(/\s+/g, ' ')
    .trim()
  if (!normalized) return ''
  if (normalized.length <= maxLength) return normalized
  return `${normalized.slice(0, Math.max(0, maxLength - 1)).trimEnd()}...`
}

function formatPercent(value, { ratio = true, digits = 1 } = {}) {
  const numeric = toNumber(value)
  if (numeric === null) return '--'
  const percentage = ratio ? numeric * 100 : numeric
  return `${percentage.toFixed(digits)}%`
}

function formatPrice(value) {
  const numeric = toNumber(value)
  if (numeric === null) return '--'
  return numeric >= 100 ? numeric.toFixed(2) : numeric.toFixed(3)
}

function formatCompact(value) {
  const numeric = toNumber(value)
  return numeric === null ? '--' : compactFormatter.format(numeric)
}

function resolveRankingContext(row) {
  const rawContext = row?.ranking_context
  const score = toNumber(rawContext?.score ?? row?.ranking_score ?? row?.setup_score)
  const tier = String(rawContext?.tier || row?.ranking_tier || '').trim().toLowerCase() || 'review'
  const tone = String(rawContext?.tone || '').trim().toLowerCase() || (
    tier === 'promote' ? 'positive' : tier === 'stand_down' ? 'negative' : 'warning'
  )
  const label =
    String(rawContext?.label || row?.ranking_label || '').trim() ||
    (tier === 'promote' ? 'Promote first' : tier === 'stand_down' ? 'Stand down' : 'Reviewable')
  const componentSummary =
    String(rawContext?.component_summary || row?.ranking_summary || '').trim() ||
    `SET ${row?.setup_score ?? '--'}`
  return {
    ...rawContext,
    score,
    tier,
    tone,
    label,
    boardRank: toNumber(rawContext?.board_rank ?? row?.board_rank),
    boardGap: toNumber(rawContext?.board_gap ?? row?.ranking_gap),
    componentSummary,
    summary: String(rawContext?.summary || row?.ranking_summary || '').trim(),
  }
}

function resolveExecutionContext(row) {
  const rawContext = row?.execution_context
  const fillTone = String(rawContext?.fill_tone || row?.execution_fill_tone || '').trim().toLowerCase() || 'warning'
  return {
    ...rawContext,
    fillTone,
    fillLabel: String(rawContext?.fill_label || row?.execution_fill_label || '').trim() || 'Use price control',
    summary: String(rawContext?.summary || '').trim() || 'Execution posture is still forming.',
  }
}

function resolveEventContext(row) {
  const rawContext = row?.event_context
  const nextEventName = String(row?.next_event_name || '').trim()
  const nextEventDate = String(row?.next_event_date || '').trim()
  const eventWindowLabel =
    String(rawContext?.event_window_label || row?.event_window_label || '').trim().toLowerCase() ||
    (!Boolean(row?.event_risk)
      ? 'quiet_window'
      : nextEventName.toLowerCase().includes('earnings')
        ? 'earnings_window'
        : nextEventName
          ? 'macro_window'
          : 'event_window')
  const tradePosture =
    String(rawContext?.trade_posture || row?.trade_posture || '').trim().toLowerCase() ||
    (Boolean(row?.event_risk) ? 'defer' : eventWindowLabel === 'quiet_window' ? 'clear' : 'caution')
  const eventSeverity =
    String(rawContext?.event_severity || row?.event_severity || '').trim().toLowerCase() ||
    (Boolean(row?.event_risk) ? 'high' : tradePosture === 'caution' ? 'medium' : 'low')
  const summary =
    String(rawContext?.summary || row?.event_reason || '').trim() ||
    (Boolean(row?.event_risk)
      ? `${nextEventName || 'A known catalyst'} is close enough to distort normal stop logic and widen spreads.`
      : nextEventName
        ? `${nextEventName} is on deck, so treat this setup as more conditional until the catalyst window clears.`
        : 'No near-term catalyst window is active.')

  return {
    ...rawContext,
    event_risk: Boolean(rawContext?.event_risk ?? row?.event_risk),
    event_window_label: eventWindowLabel,
    trade_posture: tradePosture,
    event_severity: eventSeverity,
    primary_event_label:
      String(rawContext?.primary_event_label || row?.event_label || '').trim() ||
      (eventWindowLabel === 'earnings_window'
        ? 'Earnings window'
        : eventWindowLabel === 'macro_window'
          ? 'Macro window'
          : eventWindowLabel === 'corporate_window'
            ? 'Corporate window'
            : Boolean(row?.event_risk)
              ? 'Event risk'
              : 'Quiet window'),
    summary,
    next_event_name: String(rawContext?.next_event_name || nextEventName).trim(),
    next_event_date: String(rawContext?.next_event_date || nextEventDate).trim(),
    next_event_days: toNumber(rawContext?.next_event_days ?? row?.next_event_days),
    session_label: String(rawContext?.session_label || row?.event_session_label || '').trim().toLowerCase(),
  }
}

function eventContextTone(context) {
  if (Boolean(context?.event_risk) || String(context?.trade_posture || '').trim().toLowerCase() === 'defer') {
    return 'negative'
  }
  if (
    String(context?.trade_posture || '').trim().toLowerCase() === 'caution' ||
    ['critical', 'high', 'medium'].includes(String(context?.event_severity || '').trim().toLowerCase())
  ) {
    return 'warning'
  }
  return 'positive'
}

function buildWatchlistEventFrame(row, marketModel = {}) {
  const eventContext = resolveEventContext(row)
  const eventModel = buildEventWindowModel({
    tradingStyle: marketModel.tradingStyle,
    eventContext,
    intradayEventGuardMinutes: marketModel.intradayEventGuardMinutes,
    sessionModel: marketModel.sessionModel,
  })
  const detailParts = [eventContext.summary]
  if (eventModel.detail) {
    detailParts.unshift(eventModel.detail)
  }
  if (eventContext.trade_posture && eventContext.trade_posture !== 'clear' && !eventModel.detail?.includes('Posture:')) {
    detailParts.push(`Posture: ${formatLabel(eventContext.trade_posture)}.`)
  }
  if (eventContext.next_event_name) {
    detailParts.push(`Next: ${eventContext.next_event_name}.`)
  }
  return {
    tone: eventModel.tone || eventContextTone(eventContext),
    label: eventModel.label || eventContext.primary_event_label || 'Event window',
    detail: detailParts.filter(Boolean).join(' '),
  }
}

function buildWatchlistCalendarPriority(row, marketModel = {}) {
  const eventContext = resolveEventContext(row)
  const eventModel = buildEventWindowModel({
    tradingStyle: marketModel.tradingStyle,
    eventContext,
    intradayEventGuardMinutes: marketModel.intradayEventGuardMinutes,
    sessionModel: marketModel.sessionModel,
  })
  return {
    active: Boolean(eventModel.active),
    tone: eventModel.tone || eventContextTone(eventContext),
    label: eventModel.badgeLabel || eventModel.label || 'Catalyst watch',
    detail: eventModel.detail || 'Event pressure is still shaping this board rank.',
    daysUntil: eventModel.daysUntil ?? toNumber(eventContext?.next_event_days),
  }
}

function buildWatchlistRiskItems(rows, marketModel = {}) {
  return rows
    .filter((row) => {
      const eventFrame = buildWatchlistEventFrame(row, marketModel)
      return eventFrame.tone !== 'positive' || (toNumber(row.regime_strength_score) ?? 1) < 0.45
    })
    .slice(0, 4)
    .map((row) => {
      const eventFrame = buildWatchlistEventFrame(row, marketModel)
      const regimeStrengthScore = toNumber(row.regime_strength_score)
      const tone =
        eventFrame.tone !== 'positive'
          ? eventFrame.tone
          : regimeStrengthScore !== null && regimeStrengthScore < 0.45
            ? 'warning'
            : 'info'
      return {
        key: `${row.ticker}-${row.trade_decision || row.verdict || 'watch'}`,
        ticker: row.ticker,
        tone,
        label: eventFrame.tone !== 'positive' ? eventFrame.label : 'Fragile regime',
        detail:
          eventFrame.tone !== 'positive'
            ? eventFrame.detail
            : `Regime strength is ${formatPercent(regimeStrengthScore)}. Treat this setup as more conditional than a clean trend continuation.`,
      }
    })
}

function buildWatchlistOpportunityState(row, marketModel = {}) {
  const ranking = resolveRankingContext(row)
  const execution = resolveExecutionContext(row)
  const eventFrame = buildWatchlistEventFrame(row, marketModel)
  return buildIntradayOpportunityState({
    tradingStyle: marketModel.tradingStyle,
    sessionModel: marketModel.sessionModel,
    rankingTier: ranking.tier,
    rankingScore: ranking.score,
    setupScore: toNumber(row?.setup_score),
    decisionTone: ranking.tone,
    executionTone: execution.fillTone,
    eventTone: eventFrame.tone,
    regimeStrengthScore: toNumber(row?.regime_strength_score),
  })
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

function buildEventStrengthScore(eventFrame, calendarPriority) {
  if (calendarPriority?.active) {
    if (calendarPriority.tone === 'negative') return 18
    if (calendarPriority.tone === 'warning') return 42
  }
  if (eventFrame?.tone === 'negative') return 24
  if (eventFrame?.tone === 'warning') return 48
  return 86
}

function buildExecutionStrengthScore(execution) {
  if (execution?.fillTone === 'negative') return 25
  if (execution?.fillTone === 'warning') return 58
  if (execution?.fillTone === 'positive') return 86
  return 62
}

function resolveInstitutionalFlow(row) {
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
  if (avgDollarVolume !== null) summaryParts.push(`Avg $${formatCompact(avgDollarVolume)} / bar`)
  if (optionLiquidityScore !== null) summaryParts.push(`Opt liq ${Math.round(optionLiquidityScore * 100)}`)

  return {
    ...rawFlow,
    score,
    tone,
    label,
    controlledUniverse,
    avgDollarVolume,
    optionLiquidityScore,
    notes,
    summary: summaryParts.join(' | ') || label,
  }
}

function resolveNewsProfile(row) {
  const rawNews = row?.news_sentiment && typeof row.news_sentiment === 'object' ? row.news_sentiment : {}
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
    if (confidence !== null) summaryParts.push(`${formatPercent(confidence)} confidence`)
    summaryParts.push(sourceLabel)
  } else {
    summaryParts.push('No recent articles')
  }
  const detail = topHeadline?.title
    ? summarizeInlineCopy(
        `${topHeadline.title}${topHeadline.publisher ? ` — ${topHeadline.publisher}` : ''}`,
        140,
      )
    : 'News context is still thin for this setup.'

  return {
    ...rawNews,
    score,
    confidence,
    articleCount,
    tone,
    label,
    sourceLabel,
    summary: summaryParts.join(' | '),
    detail,
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
  const metaParts = [
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
    detail: rejectReasons[0] || metaParts.join(' | ') || 'Option execution checks are still loading.',
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
      summarizeInlineCopy(row?.vehicle_reason, 140) ||
      (recommendation === 'listed_option'
        ? 'Contract quality is good enough to express the setup with options.'
        : recommendation === 'equity'
          ? 'The setup can still work, but the stock route is cleaner than the option chain.'
          : recommendation === 'stand_down'
            ? 'Neither stock nor option execution is clean enough right now.'
            : 'Vehicle selection is still loading.'),
    optionExecutionProfile,
  }
}

function buildInstitutionalFlowStrengthScore(flow) {
  const score = toNumber(flow?.score)
  if (score === null) {
    return toneToFallbackScore(flow?.tone)
  }
  return normalizeScore(score, { ratio: true })
}

function toneToFallbackScore(tone) {
  if (tone === 'positive') return 84
  if (tone === 'negative') return 24
  return 52
}

function buildDriftRangeModel(row, livePrice) {
  const live = toNumber(livePrice ?? row?.live_price)
  const target = toNumber(row?.target_price ?? row?.expected_underlying_target)
  const stop = toNumber(row?.stop_loss)
  const points = [live, target, stop].filter((value) => value !== null)
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
    live,
    target,
    stop,
    lower,
    upper,
    livePct: project(live),
    targetPct: project(target),
    stopPct: project(stop),
  }
}

function buildWatchlistVisualPillars(row, marketModel) {
  const ranking = resolveRankingContext(row)
  const execution = resolveExecutionContext(row)
  const eventFrame = buildWatchlistEventFrame(row, marketModel)
  const calendarPriority = buildWatchlistCalendarPriority(row, marketModel)
  const institutionalFlow = resolveInstitutionalFlow(row)
  const news = resolveNewsProfile(row)
  return [
    { key: 'board', label: 'Board', value: normalizeScore(ranking.score), tone: ranking.tone || 'default' },
    { key: 'setup', label: 'Setup', value: normalizeScore(row?.setup_score), tone: resolveRankingContext(row).tone || 'default' },
    { key: 'regime', label: 'Regime', value: normalizeScore(row?.regime_strength_score, { ratio: true }), tone: 'positive' },
    { key: 'event', label: 'Event', value: buildEventStrengthScore(eventFrame, calendarPriority), tone: eventFrame.tone || 'default' },
    { key: 'news', label: 'News', value: news.score === null ? toneToFallbackScore(news.tone) : normalizeScore(Math.abs(news.score), { ratio: true }), tone: news.tone || 'default' },
    { key: 'flow', label: 'Flow', value: buildInstitutionalFlowStrengthScore(institutionalFlow), tone: institutionalFlow.tone || 'default' },
    { key: 'fills', label: 'Fills', value: buildExecutionStrengthScore(execution), tone: execution.fillTone || 'default' },
  ]
}

function WatchlistDriftCard({ row, livePrice, marketModel, tradingStyle, onCompare }) {
  const ranking = resolveRankingContext(row)
  const execution = resolveExecutionContext(row)
  const eventFrame = buildWatchlistEventFrame(row, marketModel)
  const calendarPriority = buildWatchlistCalendarPriority(row, marketModel)
  const institutionalFlow = resolveInstitutionalFlow(row)
  const newsProfile = resolveNewsProfile(row)
  const vehicleProfile = resolveVehicleProfile(row)
  const opportunity = buildWatchlistOpportunityState(row, marketModel)
  const pillars = buildWatchlistVisualPillars(row, marketModel)
  const range = buildDriftRangeModel(row, livePrice)
  const effectivePrice = toNumber(livePrice ?? row?.live_price)
  const priceDelta = toNumber(row?.price_change ?? row?.live_change ?? row?.change)
  const priceDeltaPct = toNumber(row?.price_change_pct ?? row?.percent_change ?? row?.change_pct)
  const flowNote = institutionalFlow.notes[0] || institutionalFlow.summary

  return (
    <article className={`watchlist-drift-card watchlist-drift-card--${opportunity.tone || 'default'}`}>
      <div className="watchlist-drift-card__header">
        <div>
          <div className="watchlist-drift-card__ticker-row">
            <strong className="watchlist-drift-card__ticker">{row?.ticker || '--'}</strong>
            <span className="watchlist-drift-card__interval">{String(row?.interval || '').trim() || '--'}</span>
          </div>
          <div className="watchlist-drift-card__price">
            {effectivePrice === null ? '--' : `$${formatPrice(effectivePrice)}`}
          </div>
          <div className={`watchlist-drift-card__change ${(priceDelta ?? 0) >= 0 ? 'watchlist-drift-card__change--up' : 'watchlist-drift-card__change--down'}`}>
            {priceDelta === null ? '--' : `${priceDelta >= 0 ? '+' : ''}${formatPrice(priceDelta)}`} {priceDeltaPct === null ? '' : `(${formatPercent(priceDeltaPct, { ratio: false, digits: 2 })})`}
          </div>
        </div>
        <div className="watchlist-drift-card__badges">
          <span className={`execution-state-badge execution-state-badge--${opportunity.tone}`}>{opportunity.label}</span>
          <span className={`execution-state-badge execution-state-badge--${ranking.tone}`}>{ranking.label}</span>
          <StatusBadge value={row?.trade_decision} />
          <StatusBadge tone={vehicleProfile.tone}>{vehicleProfile.label}</StatusBadge>
          <StatusBadge tone={vehicleProfile.optionExecutionProfile.qualityTone}>
            {vehicleProfile.optionExecutionProfile.qualityLabel}
          </StatusBadge>
        </div>
      </div>

      <div className="watchlist-drift-card__subhead">
        <span>{formatLabel(row?.market_regime || opportunity.label)}</span>
        <span>{eventFrame.label}</span>
        <span>{newsProfile.label}</span>
        <span>{institutionalFlow.label}</span>
        <span>{vehicleProfile.optionExecutionProfile.scoreLabel}</span>
        <span>{execution.fillLabel}</span>
      </div>

      <div className="watchlist-drift-pillars" aria-label={`${row?.ticker || 'Ticker'} visual score pillars`}>
        {pillars.map((pillar) => (
          <div key={pillar.key} className="watchlist-drift-pillars__item">
            <div className="watchlist-drift-pillars__label-row">
              <span>{pillar.label}</span>
              <strong>{Math.round(pillar.value)}</strong>
            </div>
            <div className="watchlist-drift-pillars__track">
              <div className={`watchlist-drift-pillars__fill watchlist-drift-pillars__fill--${pillar.tone}`} style={{ width: `${pillar.value}%` }} />
            </div>
          </div>
        ))}
      </div>

      <div className="watchlist-drift-card__range-block">
        <div className="watchlist-drift-card__range-header">
          <span>{tradingStyle === 'intraday' ? 'Session path' : 'Trade path'}</span>
          <strong>{range ? `${formatPrice(range.lower)} to ${formatPrice(range.upper)}` : 'Target / stop pending'}</strong>
        </div>
        {range ? (
          <>
            <div className="watchlist-drift-range">
              <div className="watchlist-drift-range__rail" />
              {range.stopPct !== null ? <span className="watchlist-drift-range__marker watchlist-drift-range__marker--stop" style={{ left: `${range.stopPct}%` }} /> : null}
              {range.targetPct !== null ? <span className="watchlist-drift-range__marker watchlist-drift-range__marker--target" style={{ left: `${range.targetPct}%` }} /> : null}
              {range.livePct !== null ? <span className="watchlist-drift-range__marker watchlist-drift-range__marker--live" style={{ left: `${range.livePct}%` }} /> : null}
            </div>
            <div className="watchlist-drift-range__legend">
              <span>Stop {range.stop === null ? '--' : `$${formatPrice(range.stop)}`}</span>
              <span>Live {range.live === null ? '--' : `$${formatPrice(range.live)}`}</span>
              <span>Target {range.target === null ? '--' : `$${formatPrice(range.target)}`}</span>
            </div>
          </>
        ) : (
          <p className="watchlist-drift-card__range-empty">Target and stop levels are not both available for this row yet.</p>
        )}
      </div>

      <div className="watchlist-drift-card__notes">
        <p>{vehicleProfile.label}: {vehicleProfile.reason}</p>
        <p>{vehicleProfile.optionExecutionProfile.rejectSummary || vehicleProfile.optionExecutionProfile.detail}</p>
        <p>{opportunity.detail}</p>
        <p>{newsProfile.summary}</p>
        <p>{newsProfile.detail}</p>
        <p>{flowNote}</p>
        <p>{calendarPriority.active ? calendarPriority.detail : ranking.summary || execution.summary}</p>
      </div>

      <div className="watchlist-drift-card__footer">
        <Button type="button" variant="ghost" onClick={onCompare}>
          Open in compare
        </Button>
      </div>
    </article>
  )
}

function buildCompareWorkflowUrl({ tickers = [], interval = '5m', horizon = 5, focusTicker = '', source = 'watchlist' }) {
  const params = new URLSearchParams()
  if (tickers.length) {
    params.set('tickers', tickers.join(','))
  }
  params.set('interval', String(interval || '5m'))
  params.set('horizon', String(Math.max(1, Math.round(Number(horizon) || 5))))
  if (focusTicker) {
    params.set('focusTicker', String(focusTicker).trim().toUpperCase())
  }
  params.set('workflowAutoload', '1')
  params.set('workflowFrom', source)
  return `/compare?${params.toString()}`
}

const WATCHLIST_SNAPSHOT_STORAGE_KEY = 'own-account-watchlist-snapshot-v1'

function readWatchlistSnapshot() {
  if (typeof window === 'undefined' || !window.localStorage) {
    return null
  }
  try {
    const raw = window.localStorage.getItem(WATCHLIST_SNAPSHOT_STORAGE_KEY)
    if (!raw) return null
    const snapshot = JSON.parse(raw)
    if (!snapshot || typeof snapshot !== 'object') return null
    return snapshot
  } catch {
    return null
  }
}

function writeWatchlistSnapshot(snapshot) {
  if (typeof window === 'undefined' || !window.localStorage) {
    return
  }
  try {
    window.localStorage.setItem(WATCHLIST_SNAPSHOT_STORAGE_KEY, JSON.stringify(snapshot))
  } catch {
    // Ignore local storage write failures. The board still works without a cached snapshot.
  }
}

export default function WatchlistPage() {
  const navigate = useNavigate()
  const cachedSnapshot = useMemo(() => readWatchlistSnapshot(), [])
  const [bootstrap, setBootstrap] = useState(null)
  const [rows, setRows] = useState(() => cachedSnapshot?.rows || [])
  const [summary, setSummary] = useState(() => cachedSnapshot?.summary || null)
  const [validationArtifact, setValidationArtifact] = useState(() => cachedSnapshot?.validationArtifact || null)
  const [liveMap, setLiveMap] = useState(() => cachedSnapshot?.liveMap || {})
  const [form, setForm] = useState(() => ({
    tickers: cachedSnapshot?.form?.tickers || 'SPY,QQQ,AAPL,MSFT',
    interval: cachedSnapshot?.form?.interval || '5m',
    horizon: cachedSnapshot?.form?.horizon || 5,
    limit: cachedSnapshot?.form?.limit || 6,
    sortBy: cachedSnapshot?.form?.sortBy || 'ranking_score',
    descending: cachedSnapshot?.form?.descending ?? true,
  }))
  const [loading, setLoading] = useState(() => !cachedSnapshot)
  const [refreshingBoard, setRefreshingBoard] = useState(false)
  const [error, setError] = useState('')
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [filters, setFilters] = useState({ sort_fields: ['ranking_score', 'setup_score'] })
  const { pushToast } = useToast()
  const { preferences } = usePreferences()
  const [lastUpdated, setLastUpdated] = useState(() => cachedSnapshot?.lastUpdated || '')
  const tradingStyle = String(preferences?.tradingStyle || 'intraday').trim().toLowerCase() === 'intraday' ? 'intraday' : 'swing'
  const intradayPresetProfile = getIntradayPresetProfile(preferences?.intradayPreset)
  const intradayPresetGuide = buildIntradayPresetGuide({ preset: preferences?.intradayPreset, page: 'watchlist' })
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
  const watchlistMarketModel = useMemo(
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
  const sortFieldOptions = useMemo(() => {
    const labels = {
      ranking_score: tradingStyle === 'intraday' ? 'Intraday board score' : 'Board score',
      setup_score: tradingStyle === 'intraday' ? 'Setup quality' : 'Setup score',
      verdict: 'Model verdict',
      trade_decision: tradingStyle === 'intraday' ? 'Decision posture' : 'Trade decision',
      ticker: 'Ticker',
      live_price: 'Live price',
    }
    return (filters.sort_fields || ['setup_score']).map((field) => ({
      value: field,
      label: labels[field] || formatLabel(field),
    }))
  }, [filters.sort_fields, tradingStyle])

  const loadWatchlist = useCallback(async () => {
    try {
      setRefreshingBoard(true)
      setError('')
      const tickers = parseTickerList(form.tickers)
      if (!tickers.length) {
        throw new Error('Enter at least one valid ticker.')
      }
      const payload = await getWatchlist({
        tickers,
        interval: form.interval,
        horizon: form.horizon,
        limit: form.limit,
        sort_by: form.sortBy,
        descending: form.descending,
        regular_hours_only: preferences?.regularHoursOnly === true,
        include_contract_lookup: false,
        include_event_lookup: false,
        include_alignment: false,
        use_fast_model: true,
      })
      setRows(payload.results || payload.rows || [])
      setSummary(payload.summary || null)
      setValidationArtifact(payload.validation_artifact || null)
      const live = await getLiveBatch(tickers)
      const priceMap = live.prices || Object.fromEntries((live.rows || []).map((row) => [row.ticker, row.live_price]))
      setLiveMap(priceMap)
      const updatedAt = new Date().toLocaleTimeString()
      setLastUpdated(updatedAt)
      writeWatchlistSnapshot({
        form,
        rows: payload.results || payload.rows || [],
        summary: payload.summary || null,
        validationArtifact: payload.validation_artifact || null,
        liveMap: priceMap,
        lastUpdated: updatedAt,
      })
    } catch (err) {
      setValidationArtifact(null)
      const message = err?.response?.data?.detail || err.message || 'Failed to load watchlist.'
      setError(message)
      pushToast(message, 'error')
    } finally {
      setRefreshingBoard(false)
    }
  }, [form, pushToast])

  useEffect(() => {
    async function boot() {
      try {
        const [bootstrapData, filterData] = await Promise.all([getBootstrap('watchlist'), getFrontendFilters()])
        setBootstrap(bootstrapData)
        setFilters(filterData)
        const nextIntervalOptions = getStyleIntervalOptions(
          preferences?.tradingStyle,
          bootstrapData.defaults.supported_intervals || [],
        )
        const preferredInterval = preferences?.defaultInterval || bootstrapData.defaults.default_interval
        const fallbackTickers = Array.isArray(bootstrapData?.defaults?.default_scan_tickers)
          ? bootstrapData.defaults.default_scan_tickers.slice(0, 4).join(', ')
          : ''
        const bootstrapPreview = bootstrapData?.watchlist_preview || null
        if (!cachedSnapshot && bootstrapPreview) {
          setRows((current) => (current.length ? current : (bootstrapPreview.results || bootstrapPreview.rows || [])))
          setSummary((current) => current || bootstrapPreview.summary || null)
          setValidationArtifact((current) => current || bootstrapPreview.validation_artifact || null)
          setLastUpdated((current) => current || 'Bootstrap preview')
        }
        setForm((state) => ({
          ...state,
          tickers: preferences?.watchlistTickers || fallbackTickers || state.tickers,
          interval: nextIntervalOptions.includes(preferredInterval)
            ? preferredInterval
            : nextIntervalOptions[0] || bootstrapData.defaults.default_interval,
          horizon: preferences?.defaultHorizon || bootstrapData.defaults.default_horizon,
        }))
        setAutoRefresh(Boolean(preferences?.autoRefreshWatchlist ?? true))
      } finally {
        setLoading(false)
      }
    }
    boot().catch(() => {})
  }, [cachedSnapshot, preferences])

  useEffect(() => {
    loadWatchlist()
  }, [loadWatchlist])

  usePolling(loadWatchlist, preferences?.pollingMs || appConfig.defaultPollingMs, autoRefresh)

  const controlledUniverse = useMemo(() => {
    const candidates = bootstrap?.defaults?.controlled_liquid_universe
    if (Array.isArray(candidates) && candidates.length) {
      return candidates
    }
    return parseTickerList(bootstrap?.defaults?.default_scan_tickers || form.tickers)
  }, [bootstrap, form.tickers])

  const metrics = useMemo(() => {
    const validTrades = rows.filter((row) => row.trade_decision === 'VALID TRADE').length
    const rankingBoard = summary?.ranking_board || {}
    const promoteCount = toNumber(rankingBoard.promote_count) ?? rows.filter((row) => resolveRankingContext(row).tier === 'promote').length
    const reviewCount = toNumber(rankingBoard.review_count) ?? rows.filter((row) => resolveRankingContext(row).tier === 'review').length
    const standDownCount = toNumber(rankingBoard.stand_down_count) ?? rows.filter((row) => resolveRankingContext(row).tier === 'stand_down').length
    const eventWindows = rows.filter((row) => buildWatchlistEventFrame(row, watchlistMarketModel).tone !== 'positive').length
    const urgentCatalysts = rows.filter((row) => {
      const calendarPriority = buildWatchlistCalendarPriority(row, watchlistMarketModel)
      return calendarPriority.active && calendarPriority.daysUntil !== null && calendarPriority.daysUntil <= 3
    }).length
    const fragileRegimes = rows.filter((row) => {
      const regimeStrengthScore = toNumber(row.regime_strength_score)
      return regimeStrengthScore !== null && regimeStrengthScore < 0.45
    }).length
    const leader = rankingBoard.leader || rows[0] || null
    const opportunityStates = rows.map((row) => buildWatchlistOpportunityState(row, watchlistMarketModel))
    const readyCount = opportunityStates.filter((item) => item.bucket === 'ready').length
    const patienceCount = opportunityStates.filter((item) => item.bucket === 'patience').length
    const guardedCount = opportunityStates.filter((item) => item.bucket === 'guarded').length
    const cleanupCount = opportunityStates.filter((item) => item.bucket === 'cleanup' || item.bucket === 'prep').length
    const boardMetricTone = ['positive', 'warning', 'negative'].includes(boardMode.tone) ? boardMode.tone : 'default'

    if (tradingStyle === 'intraday') {
      return [
        { label: 'Rows', value: rows.length },
        { label: 'Board mode', value: boardMode.label, helper: sessionModel.label, tone: boardMetricTone },
        { label: 'Ready now', value: readyCount, helper: 'Names that fit the current tape', tone: readyCount > 0 ? 'positive' : 'default' },
        { label: 'Patience only', value: patienceCount, helper: 'Midday or partial-clear names', tone: patienceCount > 0 ? 'warning' : 'default' },
        { label: 'Guarded', value: guardedCount, helper: 'Blocked by event, fills, or weak board posture', tone: guardedCount > 0 ? 'negative' : 'default' },
        { label: 'Cleanup bias', value: cleanupCount, helper: 'Prep or flatten-first names', tone: cleanupCount > 0 ? 'warning' : 'default' },
        { label: 'Valid trades', value: validTrades, tone: validTrades > 0 ? 'positive' : 'default' },
        { label: 'Event watch', value: eventWindows, helper: `${urgentCatalysts} urgent same-session catalysts`, tone: eventWindows > 0 ? 'warning' : 'default' },
        { label: 'Fragile regimes', value: fragileRegimes, tone: fragileRegimes > 0 ? 'warning' : 'default' },
        { label: 'Board leader', value: leader?.ticker || '--', helper: leader?.ranking_label || leader?.ranking_context?.label || 'No leader yet' },
        { label: 'Updated', value: lastUpdated || '--' },
      ]
    }
    return [
      { label: 'Rows', value: rows.length },
      { label: 'Promote first', value: promoteCount, tone: promoteCount > 0 ? 'positive' : 'default' },
      { label: 'Reviewable', value: reviewCount, tone: reviewCount > 0 ? 'warning' : 'default' },
      { label: 'Stand down', value: standDownCount, tone: standDownCount > 0 ? 'negative' : 'default' },
      { label: 'Valid Trades', value: validTrades, tone: validTrades > 0 ? 'positive' : 'default' },
      { label: 'Event windows', value: eventWindows, tone: eventWindows > 0 ? 'warning' : 'default' },
      { label: 'Urgent catalysts', value: urgentCatalysts, helper: 'Catalysts inside 3 days', tone: urgentCatalysts > 0 ? 'warning' : 'default' },
      { label: 'Fragile regimes', value: fragileRegimes, tone: fragileRegimes > 0 ? 'warning' : 'default' },
      { label: 'Board leader', value: leader?.ticker || '--', helper: leader?.ranking_label || leader?.ranking_context?.label || 'No leader yet' },
      { label: 'Updated', value: lastUpdated || '--' },
    ]
  }, [boardMode.label, boardMode.tone, lastUpdated, rows, sessionModel.label, summary, tradingStyle, watchlistMarketModel])

  const watchlistRiskItems = useMemo(
    () => buildWatchlistRiskItems(rows, watchlistMarketModel),
    [rows, watchlistMarketModel],
  )
  const compareWorkflowTickers = useMemo(() => {
    const topRows = rows.slice(0, 6).map((row) => String(row?.ticker || '').trim().toUpperCase()).filter(Boolean)
    if (topRows.length >= 2) return topRows
    return controlledUniverse.slice(0, 6).map((ticker) => String(ticker || '').trim().toUpperCase()).filter(Boolean)
  }, [rows, controlledUniverse])
  const compareWorkflowLeader = compareWorkflowTickers[0] || ''
  const hasBoardRows = rows.length > 0
  const visualBoardRows = useMemo(() => rows.slice(0, Math.min(rows.length, 6)), [rows])

  if (loading) {
    return (
      <LoadingBlock
        label={tradingStyle === 'intraday' ? 'Loading intraday liquid board' : 'Loading liquid board'}
        detail={
          tradingStyle === 'intraday'
            ? 'Scoring the active symbols under one shared session frame so rank, catalyst pressure, and fill posture stay aligned before anything reaches the desk.'
            : 'Scoring the active symbols under one shared frame so rank, event pressure, and execution posture stay aligned.'
        }
      />
    )
  }

  return (
    <>
      {error ? (
        <ErrorState
          title={tradingStyle === 'intraday' ? 'Intraday liquid board unavailable' : 'Liquid board unavailable'}
          description={error}
          actionLabel={tradingStyle === 'intraday' ? 'Reload intraday board' : 'Reload board'}
          onAction={loadWatchlist}
        />
      ) : null}
      <PageIntro
        kicker={tradingStyle === 'intraday' ? 'Intraday liquid board' : 'Liquid board'}
        title={tradingStyle === 'intraday' ? intradayPresetGuide.title : 'Rank the liquid board under one shared frame'}
        description={
          tradingStyle === 'intraday'
            ? intradayPresetGuide.description
            : 'Review live liquid-board names with the same interval, horizon, event-risk, and regime context before you promote anything into the desk.'
        }
        helper={
          tradingStyle === 'intraday'
            ? intradayPresetGuide.helper
            : 'Scan in order: session clock first, then board health, then the liquid-board table, then the structured signal rows.'
        }
        badge={tradingStyle === 'intraday' ? `${intradayPresetProfile.shortLabel} | ${boardMode.label} | ${rows.length} live rows` : `${boardMode.label} | ${rows.length} live rows`}
        actions={(
          <Button type="button" variant="subtle" onClick={loadWatchlist}>
            {tradingStyle === 'intraday' ? 'Refresh intraday board' : 'Refresh board'}
          </Button>
        )}
      />
      <WorkflowGuide
        showSteps={false}
        phaseLabel="Phase 1 - Find signal"
        phaseTone="positive"
        title={
          tradingStyle === 'intraday'
            ? `Use the ${intradayPresetProfile.shortLabel.toLowerCase()} board to decide which names deserve the next same-session minute of attention.`
            : 'Use the board to decide which names deserve the next minute of attention.'
        }
        description={
          tradingStyle === 'intraday'
            ? `${intradayPresetProfile.description} This surface should narrow the same-session queue, not force a trade or reward loose curiosity clicks.`
            : 'This surface is strongest when it narrows the field, not when it tries to force a trade. Ranking, event pressure, and execution posture should stay visible together.'
        }
        steps={buildWorkflowSteps(0)}
        cards={[
          {
            label: 'Use this page for',
            value: tradingStyle === 'intraday' ? `Find the strongest names for the current ${intradayPresetProfile.shortLabel.toLowerCase()} window.` : 'Find the strongest names under one shared frame.',
            detail: tradingStyle === 'intraday'
              ? 'Keep interval, horizon, and session logic constant so the queue means the same thing row to row and weak names fall out early.'
              : 'Keep interval, horizon, and board logic constant so the ranking means the same thing row to row.',
          },
          {
            label: 'Best next move',
            value: tradingStyle === 'intraday' ? 'Promote only the names that still fit the live tape into compare or the desk.' : 'Promote only the clearest rows into compare or the desk.',
            detail: tradingStyle === 'intraday'
              ? `Start with the ${intradayPresetProfile.shortLabel.toLowerCase()} queue, then confirm the leaders still survive catalyst pressure, fill quality, and same-session execution evidence.`
              : 'Start with "Promote first" names, then confirm they still survive calendar pressure and fill quality.',
            tone: 'positive',
            actionLabel: 'Open top board in compare',
            onAction: () =>
              navigate(
                buildCompareWorkflowUrl({
                  tickers: compareWorkflowTickers,
                  interval: form.interval,
                  horizon: form.horizon,
                  focusTicker: compareWorkflowLeader,
                }),
              ),
            actionDisabled: compareWorkflowTickers.length < 2,
          },
          {
            label: 'Do not ignore',
            value: tradingStyle === 'intraday' ? 'Catalyst pressure, weak fills, and late-session timing can overrule rank.' : 'Catalyst badges and fragile regimes can overrule rank.',
            detail: tradingStyle === 'intraday'
              ? 'A strong board score is still conditional when the setup enters the event guard, drifts into midday chop, or reaches the close buffer.'
              : 'A strong board score is conditional when the setup is entering earnings, macro, or weak-regime conditions.',
            tone: 'warning',
          },
        ]}
      />
      <TickerHub
        compact
        onSelectTicker={(ticker) => setForm((state) => ({ ...state, tickers: ticker }))}
        onLoadFavorites={(favorites) => setForm((state) => ({ ...state, tickers: favorites.join(',') }))}
      />

      <FeedbackState
        tone={boardMode.tone}
        title={`${boardMode.label} | ${intervalModel.label}`}
        description={`${tradingStyle === 'intraday' ? `${intradayPresetProfile.description} ` : ''}${boardMode.detail} ${intervalModel.recommendedDetail} ${preferences?.regularHoursOnly === true ? 'Regular-hours routing is explicitly selected.' : 'Extended-hours routing is available if the setup still deserves it.'}`}
      />

      {refreshingBoard ? (
        <FeedbackState
          tone="default"
          title="Refreshing board"
          description="The liquid board is updating in the background. The page stays usable while the latest rankings arrive."
        />
      ) : null}

      <section className="metrics-grid">
        {metrics.map((item) => <MetricCard key={item.label} {...item} />)}
      </section>
      {visualBoardRows.length ? (
        <SectionCard
          eyebrow={tradingStyle === 'intraday' ? 'Visual board' : 'Research board'}
          title={tradingStyle === 'intraday' ? 'Stock snapshot board' : 'Stock snapshot board'}
          subtitle={
            tradingStyle === 'intraday'
              ? 'A faster research view for the top ranked names, with score pillars, execution evidence, and target/stop path in one card.'
              : 'A faster research view for the top ranked names, with score pillars, event pressure, and target/stop path in one card.'
          }
        >
          <div className="watchlist-drift-board">
            {visualBoardRows.map((row) => (
              <WatchlistDriftCard
                key={`visual-${row.ticker}`}
                row={row}
                livePrice={liveMap[row.ticker] ?? row.live_price}
                marketModel={watchlistMarketModel}
                tradingStyle={tradingStyle}
                onCompare={() =>
                  navigate(
                    buildCompareWorkflowUrl({
                      tickers: compareWorkflowTickers.length ? compareWorkflowTickers : [row.ticker],
                      interval: form.interval,
                      horizon: form.horizon,
                      focusTicker: row.ticker,
                    }),
                  )
                }
              />
            ))}
          </div>
        </SectionCard>
      ) : null}
      <SectionCard
        eyebrow={tradingStyle === 'intraday' ? 'Session queue' : 'Primary board'}
        title={tradingStyle === 'intraday' ? boardMode.label : 'Liquid board'}
        subtitle={
          tradingStyle === 'intraday'
            ? `${summary?.ranking_board?.board_name || 'Liquid ranking board'} tuned for the current session, with intraday posture, event guard, fill quality, and regime context in one own-account queue.`
            : `${summary?.ranking_board?.board_name || 'Liquid ranking board'} with live prices, event windows, calendar-priority badges, execution posture, and regime context.`
        }
        actions={(
          <ActionBar compact>
            <ToggleField
              label="Auto refresh"
              hint="Keep the board polling during the session."
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="watchlist-action-toggle"
            />
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                const nextTickers = controlledUniverse.slice(0, Math.max(4, Math.min(Number(form.limit) || 8, controlledUniverse.length)))
                if (!nextTickers.length) {
                  pushToast('No liquid board is available yet.', 'warning')
                  return
                }
                setForm((state) => ({ ...state, tickers: nextTickers.join(', '), sortBy: 'ranking_score', descending: true }))
                pushToast(`Loaded ${nextTickers.length} liquid-board names.`, 'success')
              }}
            >
              {tradingStyle === 'intraday' ? intradayPresetGuide.actionLabel : 'Load liquid board'}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={async () => {
                try {
                  await saveWorkspace({
                    name: `watchlist-${form.interval}-${form.limit}`,
                    page: 'watchlist',
                    payload: {
                      tickers: form.tickers,
                      interval: form.interval,
                      horizon: form.horizon,
                      limit: form.limit,
                      sortBy: form.sortBy,
                      descending: form.descending,
                      autoRefresh,
                      validation_artifact: validationArtifact,
                    },
                    notes: tradingStyle === 'intraday' ? 'Saved from the intraday liquid board.' : 'Saved from liquid board.',
                    tags: [
                      tradingStyle === 'intraday' ? 'intraday-board' : null,
                      'liquid-board',
                      'candidate-board',
                      validationArtifact ? 'validation-artifact' : null,
                    ].filter(Boolean),
                  })
                  pushToast(tradingStyle === 'intraday' ? 'Intraday-board workspace saved.' : 'Liquid-board workspace saved.', 'success')
                } catch (err) {
                  pushToast(err?.response?.data?.detail || err.message || (tradingStyle === 'intraday' ? 'Failed to save intraday-board workspace.' : 'Failed to save liquid-board workspace.'), 'error')
                }
              }}
            >
              {tradingStyle === 'intraday' ? 'Save intraday board' : 'Save workspace'}
            </Button>
          </ActionBar>
        )}
      >
        <div className="ui-field-grid ui-field-grid--watchlist">
          <TextField
            label="Ticker basket"
            hint="Comma-separated symbols for the live board."
            value={form.tickers}
            onChange={(e) => setForm((state) => ({ ...state, tickers: e.target.value }))}
            placeholder="Comma-separated tickers"
          />
          <SelectField
            label="Interval"
            hint={tradingStyle === 'intraday' ? `Intraday mode favors ${intervalOptions.slice(0, 3).join(', ')} first.` : `Swing mode keeps ${intervalOptions.slice(0, 3).join(', ')} closer to the front.`}
            value={form.interval}
            onChange={(e) => setForm((state) => ({ ...state, interval: e.target.value }))}
          >
            {intervalOptions.map((interval) => <option key={interval} value={interval}>{interval}</option>)}
          </SelectField>
          <TextField
            label="Horizon"
            hint={intervalModel.recommendedDetail}
            type="number"
            min="1"
            max="50"
            value={form.horizon}
            onChange={(e) => setForm((state) => ({ ...state, horizon: Number(e.target.value) }))}
          />
          <TextField
            label="Row limit"
            hint="Maximum rows kept on the board."
            type="number"
            min="1"
            max="50"
            value={form.limit}
            onChange={(e) => setForm((state) => ({ ...state, limit: Number(e.target.value) }))}
          />
            <SelectField
              label="Sort field"
            hint={tradingStyle === 'intraday' ? 'What the intraday board should prioritize first.' : 'What the board should rank first.'}
              value={form.sortBy}
              onChange={(e) => setForm((state) => ({ ...state, sortBy: e.target.value }))}
            >
            {sortFieldOptions.map((field) => <option key={field.value} value={field.value}>{field.label}</option>)}
            </SelectField>
          <ToggleField
            label="Descending"
            hint="Keep the strongest rows at the top."
            checked={form.descending}
            onChange={(e) => setForm((state) => ({ ...state, descending: e.target.checked }))}
          />
        </div>

        <ActionBar className="watchlist-board-actions">
          <Button type="button" variant="solid" onClick={loadWatchlist}>Refresh board</Button>
          <Button
            type="button"
            variant="subtle"
            onClick={() => {
              const nextTickers = controlledUniverse.slice(0, Math.max(4, Math.min(Number(form.limit) || 8, controlledUniverse.length)))
              if (!nextTickers.length) {
                pushToast('No liquid board is available yet.', 'warning')
                return
              }
              setForm((state) => ({ ...state, tickers: nextTickers.join(', '), sortBy: 'ranking_score', descending: true }))
              pushToast(`Loaded ${nextTickers.length} liquid-board names.`, 'success')
            }}
          >
            Use liquid board
          </Button>
        </ActionBar>

        {watchlistRiskItems.length ? (
          <div className="watchlist-risk-strip">
            <div className="watchlist-risk-strip__header">
              <span>Calendar and regime watch</span>
              <strong>{watchlistRiskItems.length} setup{watchlistRiskItems.length === 1 ? '' : 's'} need extra context</strong>
            </div>
            <div className="watchlist-risk-strip__items">
              {watchlistRiskItems.map((item) => (
                <div key={item.key} className={`watchlist-risk-item watchlist-risk-item--${item.tone}`}>
                  <div className="watchlist-risk-item__topline">
                    <strong>{item.ticker}</strong>
                    <span className={`execution-state-badge execution-state-badge--${item.tone}`}>{item.label}</span>
                  </div>
                  <p>{item.detail}</p>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {hasBoardRows ? (
          <ListTable>
            <table className="signal-table ui-list-table">
              <caption className="ui-visually-hidden">Liquid-board ranking table</caption>
              <thead>
                <tr>
                  <th scope="col">Ticker</th>
                  <th scope="col">Board rank</th>
                  <th scope="col">Live</th>
                  <th scope="col">Ranking</th>
                  <th scope="col">Decision</th>
                  <th scope="col">Event window</th>
                  <th scope="col">Execution</th>
                  <th scope="col">Regime</th>
                  <th scope="col">Target</th>
                  <th scope="col">Stop</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const eventFrame = buildWatchlistEventFrame(row, watchlistMarketModel)
                  const calendarPriority = buildWatchlistCalendarPriority(row, watchlistMarketModel)
                  const ranking = resolveRankingContext(row)
                  const execution = resolveExecutionContext(row)
                  const institutionalFlow = resolveInstitutionalFlow(row)
                  const newsProfile = resolveNewsProfile(row)
                  const vehicleProfile = resolveVehicleProfile(row)
                  const intradayState = buildWatchlistOpportunityState(row, watchlistMarketModel)
                  const telemetry = buildSignalTelemetry(row)
                  return (
                    <tr key={row.ticker}>
                      <td>
                        <div className="ui-list-cell">
                          <div className="ui-list-cell__title">{row.ticker}</div>
                          <div className="ui-list-cell__meta">{row.verdict || formatLabel(row.market_regime)}</div>
                        </div>
                      </td>
                      <td>
                        <div className="ui-list-cell__stack">
                          <span>#{ranking.boardRank ?? '--'}</span>
                          <span className="ui-list-cell__meta">
                            {ranking.boardGap === null ? 'Leader' : `${ranking.boardGap.toFixed(1)} pts back`}
                          </span>
                        </div>
                      </td>
                      <td>{formatPrice(liveMap[row.ticker] ?? row.live_price)}</td>
                      <td>
                        <div className="ui-list-cell__stack">
                          <span className={`execution-state-badge execution-state-badge--${intradayState.tone}`}>
                            {intradayState.label}
                          </span>
                          <span className={`execution-state-badge execution-state-badge--${ranking.tone}`}>
                            {ranking.label}
                          </span>
                          {calendarPriority.active ? (
                            <span className={`execution-state-badge execution-state-badge--${calendarPriority.tone}`}>
                              {calendarPriority.label}
                            </span>
                          ) : null}
                          <span className={`execution-state-badge execution-state-badge--${newsProfile.tone}`}>
                            {newsProfile.label}
                          </span>
                          <span className={`execution-state-badge execution-state-badge--${vehicleProfile.tone}`}>
                            {vehicleProfile.label}
                          </span>
                          <span className={`execution-state-badge execution-state-badge--${vehicleProfile.optionExecutionProfile.qualityTone}`}>
                            {vehicleProfile.optionExecutionProfile.qualityLabel}
                          </span>
                          <span>{ranking.score === null ? '--' : `${ranking.score.toFixed(1)} board score`}</span>
                          <span className={`execution-state-badge execution-state-badge--${institutionalFlow.tone}`}>
                            {institutionalFlow.label}
                          </span>
                          <span className="ui-list-cell__meta">{intradayState.detail}</span>
                          <span className="ui-list-cell__meta">{ranking.componentSummary}</span>
                          {telemetry.rankingSummary.length ? <span className="ui-list-cell__meta">{telemetry.rankingSummary.join(' | ')}</span> : null}
                          <span className="ui-list-cell__meta">{newsProfile.summary}</span>
                          <span className="ui-list-cell__meta">{institutionalFlow.summary}</span>
                          <span className="ui-list-cell__meta">
                            {vehicleProfile.label} | {vehicleProfile.optionExecutionProfile.scoreLabel} | {vehicleProfile.optionExecutionProfile.qualityLabel}
                          </span>
                          <span className="ui-list-cell__meta">{vehicleProfile.reason}</span>
                          {calendarPriority.active ? <span className="ui-list-cell__meta">{calendarPriority.detail}</span> : null}
                        </div>
                      </td>
                      <td>
                        <div className="ui-list-cell__stack">
                          <StatusBadge value={row.trade_decision} />
                          <span className={`execution-state-badge execution-state-badge--${telemetry.autoEntryEligible ? 'positive' : 'warning'}`}>
                            {telemetry.eligibilityLabel}
                          </span>
                          {telemetry.automationSummary.length ? <span className="ui-list-cell__meta">{telemetry.automationSummary.join(' | ')}</span> : null}
                          {telemetry.rejectionSummary ? <span className="ui-list-cell__meta">{telemetry.rejectionSummary}</span> : null}
                        </div>
                      </td>
                      <td>
                        <div className="ui-list-cell__stack">
                          <span className={`execution-state-badge execution-state-badge--${eventFrame.tone}`}>
                            {eventFrame.label}
                          </span>
                          {calendarPriority.active ? (
                            <span className={`execution-state-badge execution-state-badge--${calendarPriority.tone}`}>
                              {calendarPriority.label}
                            </span>
                          ) : null}
                          <span className="ui-list-cell__meta">{eventFrame.detail}</span>
                        </div>
                      </td>
                      <td>
                        <div className="ui-list-cell__stack">
                          <span className={`execution-state-badge execution-state-badge--${execution.fillTone}`}>
                            {execution.fillLabel}
                          </span>
                          {telemetry.edgeToCostRatio !== null ? <span className="ui-list-cell__meta">{`Edge/cost ${telemetry.edgeToCostRatio.toFixed(1)}x`}</span> : null}
                          <span className="ui-list-cell__meta">{execution.summary}</span>
                        </div>
                      </td>
                      <td>
                        <div className="ui-list-cell__stack">
                          <span>{formatLabel(row.market_regime)}</span>
                          <span className="ui-list-cell__meta">
                            {toNumber(row.regime_strength_score) === null
                              ? 'Strength pending'
                              : `${formatPercent(row.regime_strength_score)} strength`}
                          </span>
                          {ranking.summary ? <span className="ui-list-cell__meta">{ranking.summary}</span> : null}
                        </div>
                      </td>
                      <td>{formatPrice(row.target_price ?? row.expected_underlying_target)}</td>
                      <td>{formatPrice(row.stop_loss)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </ListTable>
        ) : (
          <EmptyState
            title={tradingStyle === 'intraday' ? 'No intraday board yet' : 'No ranked board yet'}
            description={
              tradingStyle === 'intraday'
                ? `Start here with ${intradayPresetGuide.actionLabel.toLowerCase()}, then refresh the board to rank names under one shared same-session frame before you open the desk.`
                : 'Start here with the liquid board or your own ticker basket, then refresh the board to rank names under one shared decision frame.'
            }
            actionLabel={tradingStyle === 'intraday' ? intradayPresetGuide.actionLabel : 'Use liquid board'}
            onAction={() => {
              const nextTickers = controlledUniverse.slice(0, Math.max(4, Math.min(Number(form.limit) || 8, controlledUniverse.length)))
              if (!nextTickers.length) {
                pushToast('No liquid board is available yet.', 'warning')
                return
              }
              setForm((state) => ({ ...state, tickers: nextTickers.join(', '), sortBy: 'ranking_score', descending: true }))
              pushToast(`Loaded ${nextTickers.length} ${tradingStyle === 'intraday' ? intradayPresetProfile.shortLabel.toLowerCase() : 'liquid-board'} names.`, 'success')
            }}
            secondaryActionLabel="Open compare"
            onSecondaryAction={
              compareWorkflowTickers.length >= 2
                ? () => navigate(buildCompareWorkflowUrl({
                    tickers: compareWorkflowTickers,
                    interval: form.interval,
                    horizon: form.horizon,
                    focusTicker: compareWorkflowLeader,
                  }))
                : null
            }
          />
        )}
      </SectionCard>
      <SectionCard
        eyebrow={tradingStyle === 'intraday' ? 'Structured intraday scan' : 'Structured scan'}
        title={tradingStyle === 'intraday' ? 'Signal view' : 'Signal view'}
        subtitle={
          tradingStyle === 'intraday'
            ? 'Structured trade rows for quick same-session review once the intraday board narrows the field to names that can still survive live routing.'
            : 'Structured trade rows for quick review.'
        }
      >
        <SignalTable rows={rows} />
      </SectionCard>
    </>
  )
}
