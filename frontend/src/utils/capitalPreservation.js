const currencyFormatter = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 2,
})

function toNumber(value) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

function formatPrice(value) {
  const numeric = toNumber(value)
  return numeric === null ? '--' : currencyFormatter.format(numeric)
}

function formatCompactNumber(value, digits = 1) {
  const numeric = toNumber(value)
  return numeric === null ? '--' : Number(numeric).toFixed(digits).replace(/\.0$/, '')
}

function formatRatioPercent(value, digits = 0) {
  const numeric = toNumber(value)
  return numeric === null ? '--' : `${(numeric * 100).toFixed(digits)}%`
}

function formatSignedCurrency(value) {
  const numeric = toNumber(value)
  if (numeric === null) return '--'
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
    signDisplay: 'always',
  }).format(numeric)
}

function getNextReviewResetDate(now = new Date()) {
  const next = new Date(now)
  next.setHours(9, 30, 0, 0)
  next.setDate(next.getDate() + 1)

  while (next.getDay() === 0 || next.getDay() === 6) {
    next.setDate(next.getDate() + 1)
  }

  return next
}

function formatResetLabel(value) {
  if (!(value instanceof Date) || Number.isNaN(value.getTime())) {
    return 'the next regular session'
  }

  return value.toLocaleString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function formatHistoryTimestamp(value) {
  if (!value) return 'Current'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return 'Current'
  return parsed.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

export function buildCapitalPreservationPolicy({
  preferences,
  tradeTicket,
  defaults = {},
}) {
  const accountSize =
    toNumber(tradeTicket?.accountSize) ??
    toNumber(preferences?.defaultAccountSize) ??
    toNumber(defaults.accountSize) ??
    1000
  const riskPercent =
    toNumber(tradeTicket?.riskPercent) ??
    toNumber(preferences?.defaultRiskPercent) ??
    toNumber(defaults.riskPercent) ??
    0.5

  return {
    enabled:
      preferences?.capitalPreservationMode !== undefined
        ? Boolean(preferences.capitalPreservationMode)
        : true,
    tinyAccountMode: Boolean(preferences?.tinyAccountMode),
    fractionalSharesOnly: Boolean(
      preferences?.fractionalSharesOnlyMode !== undefined
        ? preferences?.fractionalSharesOnlyMode
        : preferences?.tinyAccountMode,
    ),
    regularHoursOnly: preferences?.regularHoursOnly === true,
    maxDailyLossR: toNumber(preferences?.maxDailyLossR),
    maxConsecutiveLosses: Math.max(
      1,
      Math.round(toNumber(preferences?.maxConsecutiveLosses) ?? 1),
    ),
    maxOpenPositions: Math.max(
      1,
      Math.round(toNumber(preferences?.maxOpenPositions) ?? 1),
    ),
    maxNotionalPerTrade: toNumber(preferences?.maxNotionalPerTrade),
    equitiesOnly:
      preferences?.equitiesOnlyMode !== undefined
        ? Boolean(preferences.equitiesOnlyMode)
        : true,
    limitOrdersOnly:
      preferences?.limitOrdersOnlyMode !== undefined
        ? Boolean(preferences.limitOrdersOnlyMode)
        : true,
    longOnly:
      preferences?.longOnlyMode !== undefined
        ? Boolean(preferences.longOnlyMode)
        : true,
    promotionGate: {
      enabled:
        preferences?.promotionGateMode !== undefined
          ? Boolean(preferences.promotionGateMode)
          : true,
      minResolvedBoardOutcomes: Math.max(
        1,
        Math.round(toNumber(preferences?.promotionGateMinResolved) ?? 3),
      ),
      minReplayWinRatePercent: Math.min(
        Math.max(Number(preferences?.promotionGateMinWinRatePercent ?? 55) || 55, 1),
        100,
      ),
      maxAverageAbsSlippageBps: Math.max(
        1,
        Number(preferences?.promotionGateMaxAverageAbsSlippageBps ?? 10) || 10,
      ),
      maxWorstAbsSlippageBps: Math.max(
        1,
        Number(preferences?.promotionGateMaxWorstAbsSlippageBps ?? 20) || 20,
      ),
    },
    accountSize,
    riskPercent,
    riskUnitDollars:
      accountSize !== null && riskPercent !== null
        ? accountSize * (riskPercent / 100)
        : null,
  }
}

export function buildCapitalPreservationSummary({
  policy,
  metrics,
  now = new Date(),
}) {
  const openPositionCount = toNumber(metrics?.open_position_count) ?? 0
  const pendingOrderCount = toNumber(metrics?.pending_order_count) ?? 0
  const activeTicketCount =
    toNumber(metrics?.active_ticket_count) ?? (openPositionCount + pendingOrderCount)
  const todayRealizedPnl = toNumber(metrics?.today_realized_pnl) ?? 0
  const consecutiveLosses = Math.max(
    0,
    Math.round(toNumber(metrics?.consecutive_losses) ?? 0),
  )
  const dailyLossLimitDollars =
    policy.maxDailyLossR !== null && policy.riskUnitDollars !== null
      ? policy.maxDailyLossR * policy.riskUnitDollars
      : null
  const dailyLossLocked =
    dailyLossLimitDollars !== null &&
    todayRealizedPnl <= -1 * dailyLossLimitDollars
  const lossStreakLocked =
    policy.maxConsecutiveLosses !== null &&
    consecutiveLosses >= policy.maxConsecutiveLosses
  const positionCapLocked =
    policy.maxOpenPositions !== null &&
    activeTicketCount >= policy.maxOpenPositions
  const reviewOnlyMode = Boolean(policy.enabled && (dailyLossLocked || lossStreakLocked))
  const reviewOnlyResetAt = reviewOnlyMode ? getNextReviewResetDate(now) : null
  const reviewOnlyResetLabel = reviewOnlyMode
    ? formatResetLabel(reviewOnlyResetAt)
    : ''

  let tone = 'positive'
  let label = 'Capital protected'
  let detail = `Max ${policy.maxOpenPositions} active ticket${policy.maxOpenPositions === 1 ? '' : 's'} | ${policy.maxNotionalPerTrade === null ? 'No notional cap' : `${formatPrice(policy.maxNotionalPerTrade)} notional cap`} | ${policy.equitiesOnly ? 'equities only' : 'mixed instruments'} | ${policy.limitOrdersOnly ? 'limit only' : 'flex orders'} | ${policy.fractionalSharesOnly ? 'fractional shares only' : 'whole or mixed share sizing'}.`

  if (!policy.enabled) {
    tone = 'info'
    label = 'Preservation optional'
    detail =
      'Capital preservation mode is off, so the desk is relying on the broader ticket checks and your manual discipline.'
  } else if (dailyLossLocked) {
    tone = 'negative'
    label = 'Stand down'
    detail = `Today is locked. Realized PnL is ${formatSignedCurrency(todayRealizedPnl)}, beyond the ${formatPrice(dailyLossLimitDollars)} daily-loss line. New entries stay in review-only mode until ${reviewOnlyResetLabel}.`
  } else if (lossStreakLocked) {
    tone = 'negative'
    label = 'Stand down'
    detail = `Loss-streak lock is active after ${consecutiveLosses} consecutive losing close${consecutiveLosses === 1 ? '' : 's'}. New entries stay in review-only mode until ${reviewOnlyResetLabel}.`
  } else if (positionCapLocked) {
    tone = 'warning'
    label = 'Review gate'
    detail = `Active-ticket cap reached: ${activeTicketCount} live or working ticket${activeTicketCount === 1 ? '' : 's'} against a max of ${policy.maxOpenPositions}.`
  } else if (activeTicketCount > 0) {
    tone = 'warning'
    label = 'Protected'
    detail = `${activeTicketCount} active ticket${activeTicketCount === 1 ? '' : 's'} already on the desk. Capital preservation is still live, so the next trade must clear the same strict gates.`
  }

  return {
    enabled: policy.enabled,
    tone,
    label,
    detail,
    dailyLossLocked,
    lossStreakLocked,
    positionCapLocked,
    reviewOnlyMode,
    reviewOnlyReason: dailyLossLocked
      ? 'daily_loss'
      : lossStreakLocked
        ? 'loss_streak'
        : null,
    reviewOnlyResetAt: reviewOnlyResetAt?.toISOString?.() || null,
    reviewOnlyResetLabel,
    activeTicketCount,
    openPositionCount,
    pendingOrderCount,
    consecutiveLosses,
    todayRealizedPnl,
    dailyLossLimitDollars,
  }
}

export function formatPromotionGatePolicySummary(policy) {
  const gate = policy && typeof policy === 'object' ? policy : {}
  if (gate.enabled === false) {
    return 'Paper gate off'
  }

  const minResolved = Math.max(1, Math.round(toNumber(gate.minResolvedBoardOutcomes) ?? 3))
  const minWinRatePercent = Math.min(
    Math.max(Number(gate.minReplayWinRatePercent ?? 55) || 55, 1),
    100,
  )
  const maxAverageAbsSlippageBps = Math.max(
    1,
    Number(gate.maxAverageAbsSlippageBps ?? 10) || 10,
  )
  const maxWorstAbsSlippageBps = Math.max(
    1,
    Number(gate.maxWorstAbsSlippageBps ?? 20) || 20,
  )

  return `${minResolved} resolved | ${minWinRatePercent}% win | <=${formatCompactNumber(maxAverageAbsSlippageBps)} avg bps | <=${formatCompactNumber(maxWorstAbsSlippageBps)} worst bps`
}

export function buildPromotionGateSummary({ validationSnapshot, policy }) {
  const snapshot = validationSnapshot && typeof validationSnapshot === 'object' ? validationSnapshot : {}
  const gatePolicy = policy && typeof policy === 'object' ? policy : {}
  const gateEnabled = gatePolicy.enabled !== false
  const minResolvedBoardOutcomes = Math.max(
    1,
    Math.round(toNumber(gatePolicy.minResolvedBoardOutcomes) ?? 3),
  )
  const minReplayWinRatePercent = Math.min(
    Math.max(Number(gatePolicy.minReplayWinRatePercent ?? 55) || 55, 1),
    100,
  )
  const maxAverageAbsSlippageBps = Math.max(
    1,
    Number(gatePolicy.maxAverageAbsSlippageBps ?? 10) || 10,
  )
  const maxWorstAbsSlippageBps = Math.max(
    1,
    Number(gatePolicy.maxWorstAbsSlippageBps ?? 20) || 20,
  )
  const policySummary = formatPromotionGatePolicySummary({
    enabled: gateEnabled,
    minResolvedBoardOutcomes,
    minReplayWinRatePercent,
    maxAverageAbsSlippageBps,
    maxWorstAbsSlippageBps,
  })
  const scorecards = Array.isArray(snapshot?.scorecards) ? snapshot.scorecards : []
  const scorecardMap = new Map(
    scorecards.map((card) => [String(card?.key || '').trim().toLowerCase(), card]).filter(([key]) => key),
  )
  const rankingBoardScorecard = scorecardMap.get('ranking_board') || null
  const executionScorecard = scorecardMap.get('execution_quality') || null
  const benchmarkScorecard = scorecardMap.get('benchmark_check') || null
  const boardOutcomes =
    snapshot?.replay_comparisons?.board_outcomes &&
    typeof snapshot.replay_comparisons.board_outcomes === 'object'
      ? snapshot.replay_comparisons.board_outcomes
      : {}
  const paperLiveSlippage =
    snapshot?.replay_comparisons?.paper_live_slippage &&
    typeof snapshot.replay_comparisons.paper_live_slippage === 'object'
      ? snapshot.replay_comparisons.paper_live_slippage
      : {}
  const resolvedCount = toNumber(boardOutcomes?.resolved_count) ?? 0
  const openCount = toNumber(boardOutcomes?.open_count) ?? 0
  const replayItems = Array.isArray(boardOutcomes?.items) ? boardOutcomes.items : []
  const resolvedItems = replayItems.filter((item) => String(item?.status || '').trim().toLowerCase() === 'resolved')
  const winCount = resolvedItems.filter((item) => (toNumber(item?.pnl_dollars) ?? 0) > 0).length
  const lossCount = resolvedItems.filter((item) => (toNumber(item?.pnl_dollars) ?? 0) < 0).length
  const winRate = resolvedItems.length ? winCount / resolvedItems.length : null
  const averageAbsSlippageBps = toNumber(paperLiveSlippage?.average_abs_slippage_bps)
  const worstAbsSlippageBps = toNumber(paperLiveSlippage?.worst_abs_slippage_bps)
  const slippageReplayCount = toNumber(paperLiveSlippage?.count) ?? 0
  const sampleSummary = `${resolvedCount} resolved | ${openCount} open`
  const replaySummary = resolvedItems.length
    ? `${winCount}W-${lossCount}L | ${formatRatioPercent(winRate, 0)} win`
    : 'No resolved replay yet'
  const slippageSummary = slippageReplayCount
    ? `Avg abs ${formatCompactNumber(averageAbsSlippageBps, 1)} bps | Worst ${formatCompactNumber(worstAbsSlippageBps, 1)} bps`
    : 'No paper/live slippage replay yet'
  const blockingReasons = []
  const cautionReasons = []

  if (!gateEnabled) {
    return {
      label: 'Paper gate optional',
      tone: 'info',
      action: 'Paper-gate thresholds are off, so first capital is relying on manual replay review.',
      basis: `Policy disabled. The last saved thresholds were ${policySummary}.`,
      detail: `${sampleSummary}. ${replaySummary}. ${slippageSummary}.`,
      allowsPromotion: true,
      requiresReview: false,
      blocksPromotion: false,
      policySummary,
      resolvedCount,
      openCount,
      winRate,
      winRateLabel: formatRatioPercent(winRate, 0),
      averageAbsSlippageBps,
      worstAbsSlippageBps,
      averageAbsSlippageLabel: `${formatCompactNumber(averageAbsSlippageBps, 1)} bps`,
      worstAbsSlippageLabel: `${formatCompactNumber(worstAbsSlippageBps, 1)} bps`,
    }
  }

  if (resolvedCount === 0) {
    blockingReasons.push('no resolved board leaders yet')
  } else if (resolvedCount < minResolvedBoardOutcomes) {
    cautionReasons.push('board replay sample is still thin')
  }

  if (rankingBoardScorecard?.tone === 'negative') {
    blockingReasons.push('ranking-board closes are still under water')
  } else if (rankingBoardScorecard?.tone === 'warning') {
    cautionReasons.push('ranking-board closes are only marginal')
  }

  if (executionScorecard?.tone === 'negative') {
    blockingReasons.push('execution drift is still too high')
  } else if (executionScorecard?.tone === 'warning') {
    cautionReasons.push('execution quality is still mixed')
  }

  if (benchmarkScorecard?.tone === 'negative') {
    cautionReasons.push('baseline edge is still missing')
  } else if (benchmarkScorecard?.tone === 'warning') {
    cautionReasons.push('benchmark edge is only marginal')
  }

  if (averageAbsSlippageBps !== null && averageAbsSlippageBps >= maxAverageAbsSlippageBps * 1.75) {
    blockingReasons.push('average live slippage is still elevated')
  } else if (averageAbsSlippageBps !== null && averageAbsSlippageBps >= maxAverageAbsSlippageBps) {
    cautionReasons.push('live slippage is still above the clean range')
  }

  if (worstAbsSlippageBps !== null && worstAbsSlippageBps >= maxWorstAbsSlippageBps * 1.5) {
    blockingReasons.push('worst live slippage is still too wide')
  } else if (worstAbsSlippageBps !== null && worstAbsSlippageBps >= maxWorstAbsSlippageBps) {
    cautionReasons.push('some fills are still breaking the clean range')
  }

  const severeWinRatePercent = Math.max(1, minReplayWinRatePercent - 20)
  if (winRate !== null && resolvedCount >= minResolvedBoardOutcomes && winRate * 100 < severeWinRatePercent) {
    blockingReasons.push('replayed board leaders are not winning enough yet')
  } else if (
    winRate !== null &&
    resolvedCount >= Math.max(2, Math.ceil(minResolvedBoardOutcomes / 2)) &&
    winRate * 100 < minReplayWinRatePercent
  ) {
    cautionReasons.push('replayed board leaders are only slightly positive')
  }

  if (openCount > resolvedCount && resolvedCount < minResolvedBoardOutcomes) {
    cautionReasons.push('more saved leaders are still waiting on resolution than proving out')
  }

  if (blockingReasons.length) {
    return {
      label: 'Paper gate locked',
      tone: 'negative',
      action: 'Keep new first-capital promotion in review until replay evidence and live slippage improve.',
      basis: `Blocked by ${blockingReasons.slice(0, 2).join(' and ')}${blockingReasons.length > 2 ? ', plus more.' : '.'}`,
      detail: `${sampleSummary}. ${replaySummary}. ${slippageSummary}. Policy ${policySummary}.`,
      allowsPromotion: false,
      requiresReview: true,
      blocksPromotion: true,
      policySummary,
      resolvedCount,
      openCount,
      winRate,
      winRateLabel: formatRatioPercent(winRate, 0),
      averageAbsSlippageBps,
      worstAbsSlippageBps,
      averageAbsSlippageLabel: `${formatCompactNumber(averageAbsSlippageBps, 1)} bps`,
      worstAbsSlippageLabel: `${formatCompactNumber(worstAbsSlippageBps, 1)} bps`,
    }
  }

  if (cautionReasons.length) {
    return {
      label: 'Paper gate review',
      tone: 'warning',
      action: 'Let the board rank names, but require manual review before first-capital promotion.',
      basis: `${cautionReasons.length} review flag${cautionReasons.length === 1 ? '' : 's'}: ${cautionReasons.slice(0, 2).join(' and ')}${cautionReasons.length > 2 ? ', plus more.' : '.'}`,
      detail: `${sampleSummary}. ${replaySummary}. ${slippageSummary}. Policy ${policySummary}.`,
      allowsPromotion: false,
      requiresReview: true,
      blocksPromotion: false,
      policySummary,
      resolvedCount,
      openCount,
      winRate,
      winRateLabel: formatRatioPercent(winRate, 0),
      averageAbsSlippageBps,
      worstAbsSlippageBps,
      averageAbsSlippageLabel: `${formatCompactNumber(averageAbsSlippageBps, 1)} bps`,
      worstAbsSlippageLabel: `${formatCompactNumber(worstAbsSlippageBps, 1)} bps`,
    }
  }

  return {
    label: 'Paper gate clear',
    tone: 'positive',
    action: 'Replay evidence and live slippage are healthy enough for controlled first-capital promotion.',
    basis: `Resolved board leaders, execution replay, and benchmark context are all clearing inside policy ${policySummary}.`,
    detail: `${sampleSummary}. ${replaySummary}. ${slippageSummary}.`,
    allowsPromotion: true,
    requiresReview: false,
    blocksPromotion: false,
    policySummary,
    resolvedCount,
    openCount,
    winRate,
    winRateLabel: formatRatioPercent(winRate, 0),
    averageAbsSlippageBps,
    worstAbsSlippageBps,
    averageAbsSlippageLabel: `${formatCompactNumber(averageAbsSlippageBps, 1)} bps`,
    worstAbsSlippageLabel: `${formatCompactNumber(worstAbsSlippageBps, 1)} bps`,
  }
}

export function buildRolloutReadinessSummary(readiness) {
  const payload = readiness && typeof readiness === 'object' ? readiness : {}
  const checks = Array.isArray(payload?.checks) ? payload.checks : []
  const metrics = payload?.metrics || {}
  const cards = checks.length
    ? checks.map((check) => ({
      label: check.label || 'Check',
      value: check.value ?? '--',
      tone: check.tone || 'default',
      helper: check.helper || check.message || '',
    }))
    : [
      {
        label: 'Paper sample',
        value: Number(metrics?.resolved_count || 0),
        helper: `${Number(metrics?.open_count || 0)} awaiting resolution`,
      },
      {
        label: 'Replay win rate',
        value:
          Number.isFinite(Number(metrics?.replay_win_rate))
            ? `${Math.round(Number(metrics.replay_win_rate) * 100)}%`
            : '--',
        helper: `${Number(metrics?.win_count || 0)} wins`,
      },
      {
        label: 'Paper/live drift',
        value:
          Number.isFinite(Number(metrics?.average_abs_slippage_bps))
            ? `${Number(metrics.average_abs_slippage_bps).toFixed(1)} bps`
            : '--',
        helper:
          Number.isFinite(Number(metrics?.worst_abs_slippage_bps))
            ? `Worst ${Number(metrics.worst_abs_slippage_bps).toFixed(1)} bps`
            : 'No saved fill replay yet',
      },
      {
        label: 'Order lifecycle',
        value: Number(metrics?.reject_count || 0) + Number(metrics?.stale_pending_count || 0) > 0 ? 'Review' : 'Clean',
        tone: Number(metrics?.reject_count || 0) + Number(metrics?.stale_pending_count || 0) > 0 ? 'warning' : 'positive',
        helper: `${Number(metrics?.fragile_route_count || 0)} fragile routes`,
      },
    ]

  const priorityChecks = checks.filter((check) => check?.tone === 'negative')
  const reviewChecks = checks.filter((check) => check?.tone === 'warning')
  const unlockChecks = priorityChecks.length ? priorityChecks : reviewChecks.length ? reviewChecks : checks.slice(0, 1)
  const unlockSummary = Boolean(payload?.allows_live_rollout)
    ? 'Broker-live routing is unlocked for a tightly scoped pilot while paper replay, fill drift, and order lifecycle stay stable.'
    : unlockChecks.length
      ? `Broker-live routing unlocks after ${unlockChecks
        .slice(0, 2)
        .map((check) => String(check?.label || 'the next readiness check').trim().toLowerCase())
        .join(' and ')} clear.`
      : 'Broker-live routing unlocks after replay depth, fill drift, and order lifecycle clear together.'
  const nextCheckDetail = unlockChecks
    .map((check) => check?.message || check?.helper || '')
    .filter(Boolean)
    .join(' ')
  const historyPayload = payload?.history && typeof payload.history === 'object' ? payload.history : {}
  const historyItems = Array.isArray(historyPayload?.items)
    ? historyPayload.items.map((item, index) => ({
      key: `${item?.checkpoint_label || 'checkpoint'}-${index}`,
      checkpointLabel: item?.checkpoint_label || 'Checkpoint',
      recordedAt: item?.recorded_at || null,
      recordedLabel: formatHistoryTimestamp(item?.recorded_at),
      label: item?.label || 'Paper first',
      tone: item?.tone || 'warning',
      detail: item?.detail || '',
      resolvedCount: Number(item?.resolved_count || 0),
      sampleCount: Number(item?.sample_count || 0),
      replayWinRate:
        Number.isFinite(Number(item?.replay_win_rate))
          ? `${Math.round(Number(item.replay_win_rate) * 100)}%`
          : '--',
      averageAbsSlippage:
        Number.isFinite(Number(item?.average_abs_slippage_bps))
          ? `${Number(item.average_abs_slippage_bps).toFixed(1)} bps`
          : '--',
    }))
    : []

  return {
    status: payload?.status || 'locked',
    tone: payload?.tone || 'warning',
    label: payload?.label || 'Paper first',
    detail:
      payload?.detail
      || 'Keep broker-live routing locked to paper until replay depth, fill drift, and order lifecycle stabilize.',
    basis:
      payload?.basis
      || 'Paper stability still needs more replay depth and cleaner lifecycle evidence before broker-live routing.',
    allowsLiveRollout: Boolean(payload?.allows_live_rollout),
    cards,
    orderLifecycleSummary: payload?.order_lifecycle?.summary || null,
    unlockSummary,
    nextCheckDetail:
      nextCheckDetail
      || payload?.basis
      || 'Clear the remaining replay, slippage, and lifecycle checks before turning on broker-live routing.',
    historyTrend: historyPayload?.trend || 'unknown',
    historyLabel: historyPayload?.label || 'No broker-live history',
    historyTone: historyPayload?.tone || 'info',
    historyDetail:
      historyPayload?.detail
      || 'Saved boards and fill replay will populate broker-live history once the desk records a few validation checkpoints.',
    historyItems,
  }
}

export function buildLivePilotAuditSummary(audit) {
  const payload = audit && typeof audit === 'object' ? audit : {}
  const items = Array.isArray(payload?.items)
    ? payload.items.map((item, index) => ({
      key: `${item?.event_id || item?.trade_id || item?.created_at || 'pilot'}-${index}`,
      eventId: item?.event_id || null,
      tradeId: item?.trade_id || null,
      ticker: item?.ticker || '--',
      eventLabel: item?.event_label || item?.status || 'Order event',
      status: item?.status || 'recorded',
      detail: item?.detail || '',
      createdAt: item?.created_at || null,
      createdLabel: formatHistoryTimestamp(item?.created_at),
      routeLabel: item?.route_label || 'Broker-live route',
      adapter: item?.adapter || '--',
      gateStatus: item?.gate_status || 'unknown',
      gateTone: item?.gate_tone || 'info',
      gateLabel: item?.gate_label || 'Broker-live gate',
      basis: item?.basis || '',
      historyTrend: item?.history_trend || 'unknown',
      historyLabel: item?.history_label || 'No broker-live history',
      resolvedCount: Number(item?.resolved_count || 0),
      openCount: Number(item?.open_count || 0),
      replayWinRate:
        Number.isFinite(Number(item?.replay_win_rate))
          ? `${Math.round(Number(item.replay_win_rate) * 100)}%`
          : '--',
      averageAbsSlippage:
        Number.isFinite(Number(item?.average_abs_slippage_bps))
          ? `${Number(item.average_abs_slippage_bps).toFixed(1)} bps`
          : '--',
      worstAbsSlippage:
        Number.isFinite(Number(item?.worst_abs_slippage_bps))
          ? `${Number(item.worst_abs_slippage_bps).toFixed(1)} bps`
          : '--',
      slippageSampleCount: Number(item?.slippage_sample_count || 0),
      allowsLiveRollout: Boolean(item?.allows_live_rollout),
    }))
    : []

  const latest = items[0] || null
  const cards = [
    {
      label: 'Pilot attempts',
      value: Number(payload?.count || items.length || 0),
      helper: `${Number(payload?.allowed_count || 0)} cleared | ${Number(payload?.blocked_count || 0)} blocked`,
      tone: Number(payload?.count || items.length || 0) > 0 ? 'default' : 'info',
    },
    {
      label: 'Latest gate',
      value: latest?.gateLabel || 'No broker-live pilot yet',
      helper: latest?.createdLabel || 'Awaiting first live-route attempt',
      tone: latest?.gateTone || payload?.tone || 'info',
    },
    {
      label: 'Replay sample',
      value: latest ? `${latest.resolvedCount} resolved` : '--',
      helper: latest ? `${latest.openCount} open | ${latest.replayWinRate} win` : 'No saved replay basis yet',
      tone: latest?.gateTone || 'default',
    },
    {
      label: 'Live drift',
      value: latest?.averageAbsSlippage || '--',
      helper: latest ? `Worst ${latest.worstAbsSlippage} | ${latest.slippageSampleCount} fill reviews` : 'No live pilot fills reviewed yet',
      tone: latest?.gateTone || 'default',
    },
  ]

  return {
    count: Number(payload?.count || items.length || 0),
    allowedCount: Number(payload?.allowed_count || 0),
    blockedCount: Number(payload?.blocked_count || 0),
    label: payload?.label || 'No broker-live pilot yet',
    tone: payload?.tone || 'info',
    detail:
      payload?.detail
      || 'Broker-live attempts will be recorded here once the desk clears the paper gate and routes a pilot order.',
    latest,
    cards,
    items,
  }
}
