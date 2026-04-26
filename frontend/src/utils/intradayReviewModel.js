import { buildTradingSessionModel } from './intradayModel'

function toNumber(value) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

function normalizeTradingStyle(value = 'swing') {
  return String(value || '').trim().toLowerCase() === 'intraday' ? 'intraday' : 'swing'
}

function normalizeTone(value, fallback = 'neutral') {
  const tone = String(value || '').trim().toLowerCase()
  if (['positive', 'warning', 'negative', 'neutral', 'info'].includes(tone)) {
    return tone
  }
  return fallback
}

function formatBasisPoints(value, fallback = '--') {
  const numeric = toNumber(value)
  if (numeric === null) return fallback
  return `${numeric.toFixed(1)} bps`
}

function buildFallbackSessionModel(preferences = {}) {
  return buildTradingSessionModel({
    tradingStyle: 'intraday',
    regularHoursOnly: preferences?.regularHoursOnly === true,
    openingRangeMinutes: preferences?.openingRangeMinutes,
    flattenBeforeCloseMinutes: preferences?.flattenBeforeCloseMinutes,
    now: new Date(),
  })
}

function buildRowSessionModel(row, preferences = {}) {
  const timestamp = row?.openedAtRaw || row?.timestampRaw || row?.closedAtRaw
  if (!timestamp) return buildFallbackSessionModel(preferences)
  const parsed = new Date(timestamp)
  if (Number.isNaN(parsed.getTime())) return buildFallbackSessionModel(preferences)
  return buildTradingSessionModel({
    tradingStyle: 'intraday',
    regularHoursOnly: preferences?.regularHoursOnly === true,
    openingRangeMinutes: preferences?.openingRangeMinutes,
    flattenBeforeCloseMinutes: preferences?.flattenBeforeCloseMinutes,
    now: parsed,
  })
}

function classifyByAttribution(attributionKey = '') {
  const key = String(attributionKey || '').trim().toLowerCase()
  return {
    isExecution: key === 'thesis_right_execution_wrong' || key === 'execution_drift',
    isRisk: key === 'sizing_wrong' || key === 'rule_review',
    isThesis: key === 'thesis_wrong_execution_fine' || key === 'thesis_miss',
    isClean: key === 'clean_win' || key === 'flat_review',
  }
}

export function buildIntradayJournalReview(row, { tradingStyle = 'swing', preferences = {} } = {}) {
  if (normalizeTradingStyle(tradingStyle) !== 'intraday') return null

  const sessionModel = buildRowSessionModel(row, preferences)
  const pnl = toNumber(row?.pnl) ?? 0
  const slippageBps = toNumber(row?.fillSlippageBps)
  const toneHint = normalizeTone(row?.attributionTone, pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : 'neutral')
  const attribution = classifyByAttribution(row?.attributionKey)
  const phase = String(sessionModel?.phase || '').trim().toLowerCase()
  const hasMeaningfulDrift = slippageBps !== null && Math.abs(slippageBps) >= 8

  let bucket = 'session'
  let tone = toneHint
  let label = 'Same-session review'
  let detail = `Entry landed during ${sessionModel.label.toLowerCase()}, so the journal should keep the same-session context visible.`

  if (phase === 'opening_range') {
    bucket = 'opening'
    if (attribution.isExecution || hasMeaningfulDrift || pnl <= 0) {
      tone = toneHint === 'positive' ? 'warning' : toneHint
      label = 'Opening-range chase'
      detail = 'The entry landed during the opening-range burst, so timing and price control deserve extra review before you trust the break again.'
    } else {
      tone = 'positive'
      label = 'Clean ORB'
      detail = 'The opening-range entry held together cleanly enough to treat it as a same-session model worth repeating.'
    }
  } else if (phase === 'midday') {
    bucket = 'midday'
    if (pnl <= 0 || attribution.isExecution || attribution.isRisk || attribution.isThesis) {
      tone = toneHint === 'positive' ? 'warning' : toneHint
      label = 'Midday chop'
      detail = 'This trade landed during midday compression, where weak follow-through and fake breaks deserve a stricter patience rule.'
    } else {
      tone = 'positive'
      label = 'Midday patience win'
      detail = 'Midday entries are rare on purpose, so this result is only useful if the trigger stayed cleaner than the surrounding chop.'
    }
  } else if (['power_hour', 'closing_window', 'after_hours'].includes(phase)) {
    bucket = 'late'
    if (pnl <= 0 || attribution.isExecution || attribution.isRisk) {
      tone = toneHint === 'positive' ? 'warning' : toneHint
      label = 'Close cleanup review'
      detail = 'This trade lived in the late-session or cleanup window, so exit discipline matters as much as the original trigger.'
    } else {
      tone = 'warning'
      label = 'Late-session exit'
      detail = 'Late-session continuation can still work, but it should leave behind a clean cleanup rule before the next close.'
    }
  } else if (attribution.isExecution || hasMeaningfulDrift) {
    bucket = 'route'
    tone = toneHint === 'positive' ? 'warning' : toneHint
    label = 'Route drift'
    detail = 'The fill path drifted enough that the route and order posture still deserve same-session repair attention.'
  } else if (attribution.isRisk) {
    bucket = 'discipline'
    tone = toneHint === 'positive' ? 'warning' : toneHint
    label = 'Rule stretch'
    detail = 'Sizing or rule discipline slipped here, so the repair note should tighten the operating rule before the next session.'
  } else if (attribution.isThesis) {
    bucket = 'thesis'
    tone = toneHint
    label = 'Context miss'
    detail = 'The execution may have been fine, but the same-session idea still missed, which usually points back to board quality or catalyst framing.'
  } else if (attribution.isClean && pnl > 0) {
    bucket = 'clean'
    tone = 'positive'
    label = 'Clean session'
    detail = 'This trade is a useful same-session template because the entry, route, and outcome stayed aligned.'
  }

  return {
    bucket,
    tone,
    label,
    detail,
    sessionLabel: sessionModel.label,
    phase,
  }
}

function buildRowCounts(journalRows = [], preferences = {}) {
  return (Array.isArray(journalRows) ? journalRows : []).reduce(
    (accumulator, row) => {
      const review = row?.intradayReview || buildIntradayJournalReview(row, { tradingStyle: 'intraday', preferences })
      if (!review) return accumulator
      const next = { ...accumulator }
      next.total += 1
      next[review.bucket] = (next[review.bucket] || 0) + 1
      return next
    },
    {
      total: 0,
      opening: 0,
      midday: 0,
      late: 0,
      route: 0,
      discipline: 0,
      thesis: 0,
      clean: 0,
      session: 0,
    },
  )
}

function buildPortfolioCards({
  sessionModel,
  openRepairCount,
  routeDriftCount,
  cleanupPressure,
  validationSnapshot = null,
}) {
  const routeQuality = validationSnapshot?.route_quality || {}
  const boardOutcomeReplay = validationSnapshot?.replay_comparisons?.board_outcomes || {}
  return [
    {
      label: 'Cleanup pressure',
      value: cleanupPressure,
      tone: cleanupPressure > 0 ? 'warning' : 'positive',
      helper: `${sessionModel.label} | ${boardOutcomeReplay.open_count ?? 0} unresolved same-session replays`,
    },
    {
      label: 'Route drift',
      value: routeDriftCount,
      tone: routeDriftCount > 0 ? 'warning' : 'positive',
      helper: `Avg abs ${formatBasisPoints(routeQuality.average_abs_slippage_bps)}`,
    },
    {
      label: 'Open same-session repairs',
      value: openRepairCount,
      tone: openRepairCount > 0 ? 'warning' : 'positive',
      helper: 'Repairs should be clear before the next route gets bigger.',
    },
  ]
}

function buildJournalCards({ rowCounts, routeDriftCount }) {
  return [
    {
      label: 'Opening-range reviews',
      value: rowCounts.opening,
      tone: rowCounts.opening > 0 ? 'warning' : 'positive',
      helper: 'Breaks from the first burst should prove they were not just open chasing.',
    },
    {
      label: 'Midday patience misses',
      value: rowCounts.midday,
      tone: rowCounts.midday > 0 ? 'warning' : 'positive',
      helper: 'Midday trades should stay rare unless the tape was unusually clean.',
    },
    {
      label: 'Close cleanup reviews',
      value: Math.max(rowCounts.late, routeDriftCount > 0 ? 1 : 0),
      tone: rowCounts.late > 0 ? 'warning' : routeDriftCount > 0 ? 'neutral' : 'positive',
      helper: 'Late-session and cleanup trades need the strictest same-session exit discipline.',
    },
  ]
}

function buildNotesCards({ sessionModel, routeDriftCount, openRepairCount, rowCounts }) {
  return [
    {
      label: 'Current session',
      value: sessionModel.label,
      tone: sessionModel.tone,
      helper: 'Repair notes should match the session behavior you are actually trying to fix.',
    },
    {
      label: 'Route drift prompts',
      value: routeDriftCount,
      tone: routeDriftCount > 0 ? 'warning' : 'positive',
      helper: 'Use notes to lock in fill, order-type, or urgency changes before the next route.',
    },
    {
      label: 'Open same-session repairs',
      value: Math.max(openRepairCount, rowCounts.midday + rowCounts.late),
      tone: openRepairCount > 0 ? 'warning' : 'positive',
      helper: 'Open repairs should spell out what changes before tomorrow’s opening range.',
    },
  ]
}

export function buildIntradayReviewTemplates(reviewLens) {
  if (!reviewLens?.active) return []
  const templates = []

  templates.push({
    label: 'Opening-range repair',
    title: 'Opening-range repair',
    tags: 'review-loop, intraday, opening-range',
    owner: 'intraday-lane',
    priority: 'high',
    noteType: 'risk_review',
    body: `Opening-range review:
What triggered the entry?
Was the break clean or chased?
What spread, volume, or confirmation rule should tighten before the next open?
What must be true before this setup can route again?`,
  })

  templates.push({
    label: 'Midday patience',
    title: 'Midday patience review',
    tags: 'review-loop, intraday, midday',
    owner: 'intraday-lane',
    priority: 'medium',
    noteType: 'risk_review',
    body: `Midday review:
Was this trade avoidable chop?
What tape feature made it look cleaner than it was?
What patience rule or session filter should block the next midday attempt?
What would have kept this idea in watch mode instead of route mode?`,
  })

  templates.push({
    label: 'Route drift',
    title: 'Same-session route drift',
    tags: 'review-loop, intraday, execution',
    owner: 'execution-lane',
    priority: 'high',
    noteType: 'risk_review',
    body: `Route drift review:
Where did the fill or order posture drift?
Did order type, urgency, or route choice fit the session window?
What route rule should change before the next same-session trade?
How will the desk confirm the fix worked?`,
  })

  templates.push({
    label: 'Close cleanup',
    title: 'Close cleanup review',
    tags: 'review-loop, intraday, cleanup',
    owner: 'risk-lane',
    priority: 'high',
    noteType: 'risk_review',
    body: `Close cleanup review:
Why was this trade still live late in the session?
Did the exit plan shrink appropriately as the close approached?
What flatten, trim, or no-new-entry rule should harden before the next session?
What would a cleaner same-session cleanup have looked like?`,
  })

  return templates
}

export function buildIntradayReviewLens({
  tradingStyle = 'swing',
  preferences = {},
  journalRows = [],
  validationSnapshot = null,
  notesSummary = null,
  tradeSummary = null,
  monitoredTrades = [],
  openTrades = [],
} = {}) {
  if (normalizeTradingStyle(tradingStyle) !== 'intraday') {
    return { active: false }
  }

  const sessionModel = buildFallbackSessionModel(preferences)
  const routeQuality = validationSnapshot?.route_quality || {}
  const boardOutcomeReplay = validationSnapshot?.replay_comparisons?.board_outcomes || {}
  const reviewLoopSummary = notesSummary?.review_loop_summary || { open_count: 0, resolved_count: 0 }
  const attributionSummary = tradeSummary?.attribution_summary || tradeSummary || {}
  const rowCounts = buildRowCounts(journalRows, preferences)
  const monitoredRows = Array.isArray(monitoredTrades) ? monitoredTrades : []
  const liveOpenTrades = Array.isArray(openTrades) ? openTrades : []
  const urgentCleanupCount = monitoredRows.filter(
    (row) => String(row?.monitor_action || '').trim().toUpperCase() !== 'HOLD',
  ).length
  const routeDriftCount = Math.max(
    toNumber(routeQuality.slipped_fill_count) ?? 0,
    toNumber(attributionSummary.execution_review_count) ?? 0,
    rowCounts.route,
  )
  const openRepairCount = Math.max(
    toNumber(reviewLoopSummary.open_count) ?? 0,
    toNumber(boardOutcomeReplay.open_count) ?? 0,
  )
  const cleanupPressure = urgentCleanupCount + Math.max(rowCounts.late, liveOpenTrades.length > 0 && sessionModel.phase === 'closing_window' ? 1 : 0)

  return {
    active: true,
    sessionModel,
    labels: {
      repairLoop: 'Same-session repair loop',
      replayEvidence: 'Same-session replay',
      savedBoards: 'Saved intraday boards',
      boardReplay: 'Leader follow-through',
    },
    rowCounts,
    portfolioCards: buildPortfolioCards({
      sessionModel,
      openRepairCount,
      routeDriftCount,
      cleanupPressure,
      validationSnapshot,
    }),
    journalCards: buildJournalCards({
      rowCounts,
      routeDriftCount,
    }),
    notesCards: buildNotesCards({
      sessionModel,
      routeDriftCount,
      openRepairCount,
      rowCounts,
    }),
    noteTemplates: buildIntradayReviewTemplates({ active: true }),
    openRepairCount,
    routeDriftCount,
    cleanupPressure,
    guideDetail: sessionModel.phase === 'opening_range'
      ? 'The review loop should answer whether the open was clean, chased, or simply too fast for the current rules.'
      : sessionModel.phase === 'midday'
        ? 'The review loop should answer whether the desk stayed patient enough to avoid midday chop and weak follow-through.'
        : sessionModel.phase === 'closing_window'
          ? 'The review loop should answer whether same-session cleanup happened early enough, not just whether the trade made money.'
          : 'The review loop should keep same-session entry quality, fill quality, and cleanup discipline tied together.',
  }
}
