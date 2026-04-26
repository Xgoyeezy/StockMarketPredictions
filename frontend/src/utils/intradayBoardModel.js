function toNumber(value) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

function normalizeTradingStyle(value = 'swing') {
  return String(value || '').trim().toLowerCase() === 'intraday' ? 'intraday' : 'swing'
}

function normalizeTone(value, fallback = 'warning') {
  const tone = String(value || '').trim().toLowerCase()
  if (['positive', 'warning', 'negative', 'neutral', 'info'].includes(tone)) {
    return tone
  }
  return fallback
}

function normalizePhase(value = '') {
  return String(value || '').trim().toLowerCase()
}

function toneRank(value) {
  const tone = normalizeTone(value, 'neutral')
  return {
    positive: 0,
    warning: 1,
    info: 1,
    neutral: 2,
    negative: 3,
  }[tone] ?? 2
}

export function buildIntradayBoardMode({
  tradingStyle = 'swing',
  sessionModel = null,
  intervalModel = null,
} = {}) {
  const style = normalizeTradingStyle(tradingStyle)
  const phase = normalizePhase(sessionModel?.phase)

  if (style !== 'intraday') {
    return {
      label: 'Liquid board',
      tone: sessionModel?.tone || 'neutral',
      detail: 'Use one shared board to rank liquid names before any deeper desk work or replay review.',
      queueLabel: 'Board queue',
      compareLabel: 'Compare leaders under one shared frame before you route anything.',
      helper: intervalModel?.recommendedDetail || 'Keep the interval and horizon stable so the ranking means the same thing row to row.',
    }
  }

  const phaseMap = {
    premarket: {
      label: 'Premarket prep board',
      tone: 'warning',
      queueLabel: 'Prep queue',
      detail: 'Use premarket to trim the field, line up the opening range, and avoid treating thin premarket prints like clean intraday entries.',
    },
    opening_range: {
      label: 'Opening-range board',
      tone: 'positive',
      queueLabel: 'ORB queue',
      detail: 'Favor names that can survive the opening-range test with clean liquidity, clean event posture, and enough room to avoid forced chasing.',
    },
    morning_session: {
      label: 'Morning-drive board',
      tone: 'positive',
      queueLabel: 'Drive queue',
      detail: 'This is the cleanest continuation stretch for intraday trading. Favor names that still clear trust, spread, and catalyst pressure together.',
    },
    midday: {
      label: 'Midday patience board',
      tone: 'warning',
      queueLabel: 'Patience queue',
      detail: 'Midday is thinner and more failure-prone. Raise the bar for new entries and treat the board as a patience tool, not a forcing function.',
    },
    afternoon_session: {
      label: 'Afternoon reset board',
      tone: 'neutral',
      queueLabel: 'Reset queue',
      detail: 'The tape is rebuilding after midday. Re-rank the field and focus on names that still have clean continuation structure before power hour.',
    },
    power_hour: {
      label: 'Power-hour board',
      tone: 'warning',
      queueLabel: 'Late-drive queue',
      detail: 'Volatility returns in the final hour, but exits matter more. Favor clean late-session continuation and avoid names that still need too much forgiveness.',
    },
    closing_window: {
      label: 'Close-cleanup board',
      tone: 'negative',
      queueLabel: 'Flatten queue',
      detail: 'The close buffer is active. Use the board to reduce same-session risk, not to manufacture fresh intraday conviction.',
    },
    after_hours: {
      label: 'After-hours board',
      tone: sessionModel?.regularHoursOnly ? 'negative' : 'warning',
      queueLabel: 'After-hours queue',
      detail: sessionModel?.regularHoursOnly
        ? 'Regular-hours mode is active, so this board is for planning and cleanup rather than fresh execution.'
        : 'After-hours routing is available, but the board should treat spread and catalyst fragility as first-class constraints.',
    },
    overnight: {
      label: 'Overnight prep board',
      tone: 'warning',
      queueLabel: 'Prep queue',
      detail: 'The live session is closed. Keep this board focused on tomorrow’s liquid names, not on forcing stale intraday decisions.',
    },
    weekend: {
      label: 'Weekend prep board',
      tone: 'neutral',
      queueLabel: 'Prep queue',
      detail: 'Use the board to plan next-session liquid names, review saved boards, and tighten the catalyst calendar before the week starts.',
    },
  }

  const resolved = phaseMap[phase] || phaseMap.afternoon_session
  return {
    ...resolved,
    helper: intervalModel?.recommendedDetail || 'Keep the interval and horizon stable so the intraday queue stays comparable.',
    compareLabel: `${resolved.detail} Then qualify only the best few names under one shared compare frame.`,
  }
}

export function buildIntradayOpportunityState({
  tradingStyle = 'swing',
  sessionModel = null,
  rankingTier = 'review',
  rankingScore = null,
  setupScore = null,
  decisionTone = 'warning',
  executionTone = 'warning',
  trustTone = 'warning',
  eventTone = 'neutral',
  driftTone = 'positive',
  sessionMemoryTone = 'neutral',
  freshnessTone = 'neutral',
  regimeStrengthScore = null,
  confidenceScore = null,
} = {}) {
  const style = normalizeTradingStyle(tradingStyle)
  const phase = normalizePhase(sessionModel?.phase)
  const tier = String(rankingTier || '').trim().toLowerCase() || 'review'
  const score = toNumber(rankingScore ?? setupScore) ?? 0
  const regimeStrength = toNumber(regimeStrengthScore)
  const confidence = toNumber(confidenceScore)
  const eventRisk = normalizeTone(eventTone, 'neutral')
  const executionRisk = normalizeTone(executionTone, 'warning')
  const trustRisk = normalizeTone(trustTone, 'warning')
  const decisionRisk = normalizeTone(decisionTone, 'warning')
  const driftRisk = normalizeTone(driftTone, 'positive')
  const sessionRisk = normalizeTone(sessionMemoryTone, 'neutral')
  const freshness = normalizeTone(freshnessTone, 'neutral')

  let bucket = 'review'
  let tone = 'warning'
  let label = 'Review first'
  let detail = 'The setup is still usable, but it needs one more review pass before it deserves live attention.'

  if (style !== 'intraday') {
    if (tier === 'promote' && decisionRisk !== 'negative' && executionRisk !== 'negative') {
      bucket = 'ready'
      tone = 'positive'
      label = 'Board leader'
      detail = 'This setup is clearing the shared board strongly enough to deserve the next review step.'
    } else if (tier === 'stand_down' || decisionRisk === 'negative' || executionRisk === 'negative') {
      bucket = 'guarded'
      tone = 'negative'
      label = 'Guarded'
      detail = 'The shared board is still pointing to risk, not promotion.'
    }
  } else if (phase === 'closing_window') {
    bucket = 'cleanup'
    tone = 'negative'
    label = 'Flatten bias'
    detail = 'The closing buffer is active, so new intraday entries should give way to same-session cleanup.'
  } else if (['overnight', 'weekend'].includes(phase) || (['premarket', 'after_hours'].includes(phase) && sessionModel?.regularHoursOnly)) {
    bucket = 'prep'
    tone = 'warning'
    label = 'Prep only'
    detail = 'This name can stay on the prep board, but the current session state does not justify fresh intraday aggression yet.'
  } else if (phase === 'premarket') {
    bucket = tier === 'promote' && decisionRisk !== 'negative' && executionRisk !== 'negative' ? 'ready' : 'review'
    tone = bucket === 'ready' ? 'positive' : 'warning'
    label = bucket === 'ready' ? 'Pre-market ready' : 'Pre-market review'
    detail =
      bucket === 'ready'
        ? 'This name clears the stricter pre-market filter; use limit-only routing, smaller size, and liquidity checks.'
        : 'Pre-market routing is active, but this setup still needs cleaner signal quality or execution context before promotion.'
  } else if (phase === 'after_hours') {
    bucket = tier === 'promote' && decisionRisk !== 'negative' && executionRisk !== 'negative' && eventRisk !== 'negative' ? 'ready' : 'review'
    tone = bucket === 'ready' ? 'warning' : 'negative'
    label = bucket === 'ready' ? 'After-hours eligible' : 'After-hours guarded'
    detail =
      bucket === 'ready'
        ? 'This name can stay in the after-hours queue with conservative size, strict spread control, and no aggressive averaging.'
        : 'After-hours mode is live, but this setup should wait for cleaner liquidity, lower event risk, or the next core session.'
  } else if (eventRisk === 'negative' || executionRisk === 'negative' || driftRisk === 'negative') {
    bucket = 'guarded'
    tone = 'negative'
    label = 'Guarded'
    detail = 'Catalyst pressure, fragile fills, or drift risk are strong enough to block clean same-session promotion.'
  } else if (phase === 'midday') {
    bucket = 'patience'
    tone = tier === 'promote' ? 'warning' : 'neutral'
    label = tier === 'promote' ? 'Patience only' : 'Midday watch'
    detail = 'Midday tape is thinner and more failure-prone, so this setup needs a cleaner trigger than the morning board would require.'
  } else if (phase === 'opening_range') {
    if (tier === 'promote' && decisionRisk !== 'negative' && trustRisk !== 'negative') {
      bucket = 'ready'
      tone = 'positive'
      label = 'ORB ready'
      detail = 'This name is clearing the opening-range test well enough to stay near the front of the same-session queue.'
    } else if (tier !== 'stand_down') {
      bucket = 'review'
      tone = 'warning'
      label = 'Wait for break'
      detail = 'The setup is interesting, but it still needs a cleaner break and cleaner tape than the opening burst is offering right now.'
    }
  } else if (phase === 'morning_session') {
    if (tier === 'promote' && decisionRisk !== 'negative' && trustRisk !== 'negative') {
      bucket = 'ready'
      tone = 'positive'
      label = 'Drive continuation'
      detail = 'The setup still fits the cleanest continuation window of the session and deserves front-of-queue attention.'
    }
  } else if (phase === 'afternoon_session') {
    if (tier === 'promote' && decisionRisk !== 'negative') {
      bucket = 'ready'
      tone = executionRisk === 'positive' ? 'positive' : 'warning'
      label = 'Reset continuation'
      detail = 'The tape is rebuilding after midday, so this setup can still earn promotion if it keeps its fill quality and context.'
    }
  } else if (phase === 'power_hour') {
    if (tier === 'promote' && decisionRisk !== 'negative') {
      bucket = 'review'
      tone = 'warning'
      label = 'Late-drive only'
      detail = 'Late-session momentum can still work here, but the shrinking exit window means the setup needs cleaner follow-through than earlier in the day.'
    }
  }

  if (tier === 'stand_down' && bucket !== 'cleanup' && bucket !== 'prep') {
    bucket = 'guarded'
    tone = 'negative'
    label = 'Stand aside'
    detail = 'The board is already saying this name belongs in the stand-down bucket, so intraday mode should not rescue it.'
  }

  let priorityScore = score

  if (bucket === 'ready') priorityScore += 22
  else if (bucket === 'review') priorityScore += 8
  else if (bucket === 'patience') priorityScore -= 2
  else if (bucket === 'cleanup') priorityScore -= 16
  else if (bucket === 'prep') priorityScore -= 18
  else if (bucket === 'guarded') priorityScore -= 24

  if (decisionRisk === 'positive') priorityScore += 8
  else if (decisionRisk === 'negative') priorityScore -= 12

  if (executionRisk === 'positive') priorityScore += 6
  else if (executionRisk === 'warning') priorityScore -= 2
  else if (executionRisk === 'negative') priorityScore -= 14

  if (trustRisk === 'positive') priorityScore += 5
  else if (trustRisk === 'negative') priorityScore -= 8

  if (eventRisk === 'warning') priorityScore -= 5
  else if (eventRisk === 'negative') priorityScore -= 16

  if (driftRisk === 'warning') priorityScore -= 4
  else if (driftRisk === 'negative') priorityScore -= 10

  if (sessionRisk === 'positive') priorityScore += 4
  else if (sessionRisk === 'negative') priorityScore -= 5

  if (freshness === 'positive') priorityScore += 2
  else if (freshness === 'warning' || freshness === 'negative') priorityScore -= 3

  if (regimeStrength !== null) {
    if (regimeStrength >= 0.6) priorityScore += 4
    else if (regimeStrength < 0.45) priorityScore -= 5
  }

  if (confidence !== null) {
    if (confidence >= 0.62) priorityScore += 4
    else if (confidence < 0.48) priorityScore -= 4
  }

  return {
    bucket,
    tone,
    label,
    detail,
    priorityScore,
  }
}

export function buildIntradayCandidateQueue(rows = [], options = {}) {
  const normalizedRows = Array.isArray(rows) ? rows : []
  const style = normalizeTradingStyle(options?.tradingStyle)
  const boardMode = buildIntradayBoardMode({
    tradingStyle: style,
    sessionModel: options?.sessionModel,
  })

  const sorted = [...normalizedRows].sort((left, right) => {
    const leftPriority = toNumber(left.intradayPriorityScore) ?? Number.NEGATIVE_INFINITY
    const rightPriority = toNumber(right.intradayPriorityScore) ?? Number.NEGATIVE_INFINITY
    if (leftPriority !== rightPriority) return rightPriority - leftPriority

    const leftDecision = toneRank(left.decisionGateTone)
    const rightDecision = toneRank(right.decisionGateTone)
    if (leftDecision !== rightDecision) return leftDecision - rightDecision

    const tierRank = { promote: 0, review: 1, stand_down: 2 }
    const leftTierRank = tierRank[String(left.rankingTier || '').trim().toLowerCase()] ?? 3
    const rightTierRank = tierRank[String(right.rankingTier || '').trim().toLowerCase()] ?? 3
    if (leftTierRank !== rightTierRank) return leftTierRank - rightTierRank

    const leftScore = toNumber(left.rankingScore ?? left.setupScore) ?? Number.NEGATIVE_INFINITY
    const rightScore = toNumber(right.rankingScore ?? right.setupScore) ?? Number.NEGATIVE_INFINITY
    if (leftScore !== rightScore) return rightScore - leftScore

    return String(left.ticker || '').localeCompare(String(right.ticker || ''))
  })

  const ready = sorted
    .filter((row) => row.intradayBucket === 'ready' && row.decisionGateTone !== 'negative')
    .slice(0, 3)
  if (ready.length) {
    return {
      mode: 'promote',
      label: style === 'intraday' ? `${boardMode.queueLabel} ready` : 'Promote queue',
      detail: style === 'intraday'
        ? 'These names fit the current session well enough to deserve first review on the compare board.'
        : 'These names are clearing the shared board strongly enough to deserve first review.',
      rows: ready,
    }
  }

  const reviewable = sorted
    .filter((row) => row.intradayBucket !== 'guarded' && row.decisionGateTone !== 'negative')
    .slice(0, 3)
  if (reviewable.length) {
    return {
      mode: 'review',
      label: style === 'intraday' ? `${boardMode.queueLabel} review` : 'Review queue',
      detail: style === 'intraday'
        ? 'No names are fully session-ready, so this queue falls back to the best review candidates that still fit the current intraday tape.'
        : 'No names are fully clear yet, so this queue falls back to the best review candidates.',
      rows: reviewable,
    }
  }

  return {
    mode: 'guarded',
    label: style === 'intraday' ? `${boardMode.queueLabel} guarded` : 'Guarded queue',
    detail: style === 'intraday'
      ? 'Catalyst pressure, drift, or execution quality are still blocking a clean intraday front-of-queue candidate.'
      : 'The current board still reads more guarded than actionable.',
    rows: sorted.slice(0, 3),
  }
}
