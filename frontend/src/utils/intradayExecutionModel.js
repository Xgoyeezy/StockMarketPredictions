function toNumber(value) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

function normalizeTradingStyle(value = 'swing') {
  return String(value || '').trim().toLowerCase() === 'intraday' ? 'intraday' : 'swing'
}

function normalizePhase(value = '') {
  return String(value || '').trim().toLowerCase()
}

function formatExecutionIntentLabel(value = 'desk') {
  const normalized = String(value || 'desk').trim().toLowerCase()
  if (normalized === 'broker_live') return 'Broker live'
  if (normalized === 'broker_paper') return 'Broker paper'
  return 'Desk only'
}

function formatOrderTypeLabel(value = 'limit') {
  const normalized = String(value || '').trim().toLowerCase().replaceAll('_', ' ')
  return normalized.replace(/\b\w/g, (character) => character.toUpperCase()) || 'Limit'
}

function formatTimeInForceLabel(value = 'day') {
  const normalized = String(value || '').trim().toLowerCase()
  if (normalized === 'day_ext') return 'Day + AH'
  if (normalized === 'gtc_90d') return 'GTC 90D'
  return 'Day'
}

export function buildIntradayExecutionPlan({
  tradingStyle = 'swing',
  sessionModel = null,
  regularHoursOnly = false,
  reviewOnlyMode = false,
  executionIntent = 'desk',
  orderType = 'limit',
  timeInForce = 'day',
  riskPercent = 0.5,
  rolloutAllowsLive = true,
} = {}) {
  const style = normalizeTradingStyle(tradingStyle)
  const phase = normalizePhase(sessionModel?.phase)
  const normalizedRoute = String(executionIntent || 'desk').trim().toLowerCase()
  const normalizedOrderType = String(orderType || 'limit').trim().toLowerCase()
  const normalizedTimeInForce = String(timeInForce || 'day').trim().toLowerCase()
  const riskBudget = toNumber(riskPercent) ?? 0.5
  const routeLabel = formatExecutionIntentLabel(normalizedRoute)

  if (style !== 'intraday') {
    return {
      tone: 'neutral',
      title: `${routeLabel} | Swing execution`,
      description: 'Swing mode can use broader holding periods, so the desk should focus on route quality and risk sizing rather than same-session cleanup rules.',
      allowsNewEntries: true,
      cleanupOnly: false,
      routeTone: normalizedRoute === 'broker_live' && !rolloutAllowsLive ? 'negative' : normalizedRoute === 'broker_live' ? 'positive' : normalizedRoute === 'broker_paper' ? 'warning' : 'info',
      routeLabel,
      routeDetail:
        normalizedRoute === 'broker_live' && !rolloutAllowsLive
          ? 'Broker-live routing is still locked behind paper stability.'
          : normalizedRoute === 'broker_live'
            ? 'Broker-live routing is clear.'
            : normalizedRoute === 'broker_paper'
              ? 'Broker paper keeps lifecycle review visible before live capital.'
              : 'Desk-only routing keeps the setup local until you want broker execution.',
      riskTone: riskBudget > 1 ? 'warning' : 'positive',
      riskDetail: `Current risk budget is ${riskBudget.toFixed(2)}% per ticket.`,
      cards: [
        { label: 'Execution window', value: sessionModel?.label || 'Any session', helper: 'Swing mode is less dependent on same-session cleanup.' },
        { label: 'Route', value: routeLabel, tone: normalizedRoute === 'broker_live' && !rolloutAllowsLive ? 'negative' : normalizedRoute === 'broker_live' ? 'positive' : normalizedRoute === 'broker_paper' ? 'warning' : 'default' },
        { label: 'Order posture', value: formatOrderTypeLabel(normalizedOrderType), helper: formatTimeInForceLabel(normalizedTimeInForce) },
        { label: 'Risk budget', value: `${riskBudget.toFixed(2)}%`, tone: riskBudget > 1 ? 'warning' : 'positive' },
      ],
    }
  }

  let windowTone = 'positive'
  let windowLabel = 'Core session'
  let windowDetail = 'This is a clean same-session window for new intraday entries if fills and catalyst pressure still cooperate.'
  let allowsNewEntries = true
  let cleanupOnly = false

  if (reviewOnlyMode) {
    windowTone = 'negative'
    windowLabel = 'Review only'
    windowDetail = 'The desk is locked into review-only mode, so same-session cleanup matters more than fresh entries.'
    allowsNewEntries = false
    cleanupOnly = true
  } else if (['weekend', 'overnight'].includes(phase)) {
    windowTone = 'negative'
    windowLabel = 'Prep only'
    windowDetail = 'The live session is closed, so intraday routing should stay in planning and replay mode.'
    allowsNewEntries = false
  } else if (phase === 'premarket') {
    windowTone = regularHoursOnly ? 'negative' : 'warning'
    windowLabel = regularHoursOnly ? 'Premarket prep' : 'Pre-market active'
    windowDetail = regularHoursOnly
      ? 'Regular-hours-only mode keeps premarket in prep status. Build the board now, but wait for the core session to route.'
      : 'Premarket routing is possible, but it needs cleaner liquidity and stricter price control than the regular session.'
    allowsNewEntries = !regularHoursOnly
  } else if (phase === 'opening_range') {
    windowTone = 'positive'
    windowLabel = 'Opening range'
    windowDetail = 'Opening-range entries are allowed, but the tape still needs a clean break and tight price control.'
  } else if (phase === 'morning_session') {
    windowTone = 'positive'
    windowLabel = 'Morning drive'
    windowDetail = 'This is usually the cleanest continuation window for new intraday entries.'
  } else if (phase === 'midday') {
    windowTone = 'warning'
    windowLabel = 'Midday patience'
    windowDetail = 'Midday entries should be more selective because follow-through and liquidity often soften here.'
  } else if (phase === 'afternoon_session') {
    windowTone = 'neutral'
    windowLabel = 'Afternoon reset'
    windowDetail = 'The tape is rebuilding after midday, so keep routing selective and same-session exits visible.'
  } else if (phase === 'power_hour') {
    windowTone = 'warning'
    windowLabel = 'Power hour'
    windowDetail = 'Late-session entries can still work, but the shrinking exit window means route quality and cleanup discipline matter more.'
  } else if (phase === 'closing_window') {
    windowTone = 'negative'
    windowLabel = 'Close cleanup'
    windowDetail = 'The close buffer is active, so the desk should focus on flattening or reducing same-session risk instead of opening new positions.'
    allowsNewEntries = false
    cleanupOnly = true
  } else if (phase === 'after_hours') {
    windowTone = regularHoursOnly ? 'negative' : 'warning'
    windowLabel = regularHoursOnly ? 'After-hours locked' : 'After-hours active'
    windowDetail = regularHoursOnly
      ? 'Regular-hours-only mode blocks fresh after-hours entries.'
      : 'After-hours routing is possible, but spreads and catalyst sensitivity are usually worse than the regular session.'
    allowsNewEntries = !regularHoursOnly
  }

  if (normalizedRoute === 'broker_paper' && !allowsNewEntries && !['weekend', 'overnight'].includes(phase)) {
    windowTone = 'info'
    windowLabel = cleanupOnly ? 'Paper session flex' : 'Paper route flex'
    windowDetail = 'Paper routing stays available outside the live-session window so fills, spread behavior, and route drift can still be observed. Treat the result as execution evidence, not as live-ready confirmation.'
    allowsNewEntries = true
    cleanupOnly = false
  }

  let routeTone =
    normalizedRoute === 'broker_live' && !rolloutAllowsLive
      ? 'negative'
      : normalizedRoute === 'broker_live'
        ? 'positive'
        : normalizedRoute === 'broker_paper'
          ? 'warning'
          : 'info'
  let routeDetail =
    normalizedRoute === 'broker_live' && !rolloutAllowsLive
      ? 'Broker-live routing is still locked behind paper stability.'
      : normalizedRoute === 'broker_live'
        ? 'Broker-live routing is clear, but intraday mode still expects same-session cleanup discipline.'
        : normalizedRoute === 'broker_paper'
          ? 'Broker paper keeps same-session lifecycle review visible before live capital.'
          : 'Desk-only routing keeps the setup local while you validate the intraday idea.'

  if (!allowsNewEntries) {
    routeTone = 'negative'
    routeDetail = cleanupOnly
      ? 'Routing should stay cleanup-first until the same-session lock clears.'
      : 'Routing should stay in prep mode until the live intraday window opens again.'
  }

  let orderTone = 'positive'
  let orderDetail = `${formatOrderTypeLabel(normalizedOrderType)} with ${formatTimeInForceLabel(normalizedTimeInForce)} fits a same-session ticket.`
  if (normalizedOrderType === 'market') {
    orderTone = 'negative'
    orderDetail = 'Market orders are fragile in intraday mode because a thin book can erase a small edge immediately.'
  } else if (normalizedOrderType === 'stop_market' || normalizedOrderType === 'trailing_stop') {
    orderTone = 'warning'
    orderDetail = `${formatOrderTypeLabel(normalizedOrderType)} is usable, but it still needs clean liquidity because the trigger does not guarantee a good fill.`
  }
  if (normalizedTimeInForce === 'gtc_90d') {
    orderTone = 'negative'
    orderDetail = 'GTC 90D does not fit a same-session intraday ticket. Use a day order so the idea expires with the session.'
  } else if (normalizedTimeInForce === 'day_ext') {
    orderTone = regularHoursOnly ? 'negative' : 'warning'
    orderDetail = regularHoursOnly
      ? 'Extended-hours routing is blocked while regular-hours-only mode is active.'
      : 'Day + AH can work, but after-hours routing needs stricter price control than the core session.'
  }

  const recommendedRiskPercent = 0.25
  const hardRiskCeiling = 0.5
  let riskTone = 'positive'
  let riskDetail = `Current risk budget is ${riskBudget.toFixed(2)}% per ticket. Intraday defaults aim for about ${recommendedRiskPercent.toFixed(2)}%.`
  if (riskBudget > hardRiskCeiling) {
    riskTone = 'negative'
    riskDetail = `Current risk budget is ${riskBudget.toFixed(2)}% per ticket. Intraday mode should generally stay at or below ${hardRiskCeiling.toFixed(2)}% to keep same-session drawdowns controlled.`
  } else if (riskBudget > recommendedRiskPercent) {
    riskTone = 'warning'
    riskDetail = `Current risk budget is ${riskBudget.toFixed(2)}% per ticket. Intraday mode is cleaner when routine risk stays near ${recommendedRiskPercent.toFixed(2)}% instead of drifting toward swing-sized tickets.`
  }

  return {
    tone: windowTone,
    title: `${windowLabel} | ${routeLabel}`,
    description: `${windowDetail} ${routeDetail} ${orderDetail} ${riskDetail}`.trim(),
    allowsNewEntries,
    cleanupOnly,
    routeTone,
    routeLabel,
    routeDetail,
    orderTone,
    orderDetail,
    riskTone,
    riskDetail,
    recommendedRiskPercent,
    hardRiskCeiling,
    cards: [
      { label: 'Execution window', value: windowLabel, helper: sessionModel?.label || 'Intraday session', tone: windowTone === 'info' ? 'default' : windowTone },
      { label: 'Route', value: routeLabel, helper: routeDetail, tone: routeTone === 'info' ? 'default' : routeTone },
      { label: 'Order posture', value: formatOrderTypeLabel(normalizedOrderType), helper: formatTimeInForceLabel(normalizedTimeInForce), tone: orderTone },
      { label: 'Risk budget', value: `${riskBudget.toFixed(2)}%`, helper: `Target ${recommendedRiskPercent.toFixed(2)}% | ceiling ${hardRiskCeiling.toFixed(2)}%`, tone: riskTone },
    ],
  }
}
