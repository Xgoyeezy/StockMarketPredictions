const ET_PARTS_FORMATTER = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  weekday: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
  hourCycle: 'h23',
})

const INTERVAL_MINUTES = {
  '1m': 1,
  '5m': 5,
  '15m': 15,
  '30m': 30,
  '1h': 60,
  '4h': 240,
  '1d': 1440,
}

const STYLE_PRIMARY_INTERVALS = {
  swing: ['15m', '1h', '4h', '1d'],
  intraday: ['1m', '5m', '15m', '30m', '1h'],
}

const STYLE_DEFAULT_INTERVALS = {
  swing: '1h',
  intraday: '5m',
}

const STYLE_DEFAULT_HORIZONS = {
  swing: 20,
  intraday: 5,
}

const STYLE_RECOMMENDED_HORIZONS = {
  swing: {
    '15m': 12,
    '1h': 8,
    '4h': 6,
    '1d': 5,
  },
  intraday: {
    '1m': 10,
    '5m': 5,
    '15m': 4,
    '30m': 3,
    '1h': 2,
    '4h': 1,
    '1d': 1,
  },
}

const PREMARKET_START_MINUTES = 4 * 60
const MARKET_OPEN_MINUTES = 9 * 60 + 30
const MIDDAY_START_MINUTES = 11 * 60 + 30
const AFTERNOON_START_MINUTES = 14 * 60
const POWER_HOUR_START_MINUTES = 15 * 60
const MARKET_CLOSE_MINUTES = 16 * 60
const AFTER_HOURS_CLOSE_MINUTES = 20 * 60

function toNumber(value) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

function clampNumber(value, fallback, min, max) {
  const numeric = toNumber(value)
  if (numeric === null) return fallback
  return Math.min(Math.max(Math.round(numeric), min), max)
}

function getEtParts(now = new Date()) {
  const values = {}
  for (const part of ET_PARTS_FORMATTER.formatToParts(now)) {
    if (part.type !== 'literal') {
      values[part.type] = part.value
    }
  }
  const hour = Number(values.hour || '0')
  const minute = Number(values.minute || '0')
  return {
    weekday: values.weekday || '',
    hour,
    minute,
    minutesSinceMidnight: hour * 60 + minute,
    timeLabel: `${values.weekday || ''} ${values.hour || '00'}:${values.minute || '00'} ET`.trim(),
  }
}

function normalizeTradingStyle(value = 'swing') {
  return String(value || '').trim().toLowerCase() === 'intraday' ? 'intraday' : 'swing'
}

export function formatMinuteWindow(value, fallback = '--') {
  const numeric = toNumber(value)
  if (numeric === null) return fallback
  if (numeric % 60 === 0) {
    return `${numeric / 60}h`
  }
  return `${numeric}m`
}

function formatDurationLabel(totalMinutes) {
  const numeric = toNumber(totalMinutes)
  if (numeric === null) return '--'
  if (numeric >= 1440 && numeric % 1440 === 0) {
    return `${numeric / 1440}d`
  }
  if (numeric >= 60 && numeric % 60 === 0) {
    return `${numeric / 60}h`
  }
  if (numeric > 60) {
    return `${(numeric / 60).toFixed(1)}h`
  }
  return `${numeric}m`
}

function normalizeEventWindowLabel(eventWindowLabel = '', nextEventName = '', eventRisk = false) {
  const explicit = String(eventWindowLabel || '').trim().toLowerCase()
  if (explicit) return explicit
  const nextEvent = String(nextEventName || '').trim().toLowerCase()
  if (!eventRisk) return 'quiet_window'
  if (nextEvent.includes('earnings')) return 'earnings_window'
  if (nextEvent.includes('cpi') || nextEvent.includes('fomc') || nextEvent.includes('jobs') || nextEvent.includes('macro')) {
    return 'macro_window'
  }
  if (nextEvent) return 'corporate_window'
  return 'event_window'
}

export function getStyleIntervalOptions(tradingStyle = 'swing', supportedIntervals = []) {
  const style = normalizeTradingStyle(tradingStyle)
  const source = Array.isArray(supportedIntervals) && supportedIntervals.length
    ? supportedIntervals
    : Object.keys(INTERVAL_MINUTES)
  const allowed = source.filter((interval, index) => source.indexOf(interval) === index)
  const preferred = STYLE_PRIMARY_INTERVALS[style].filter((interval) => allowed.includes(interval))
  const secondary = allowed.filter((interval) => !preferred.includes(interval))
  return [...preferred, ...secondary]
}

export function getStyleQuickIntervals(tradingStyle = 'swing', supportedIntervals = []) {
  const style = normalizeTradingStyle(tradingStyle)
  const allowed = getStyleIntervalOptions(style, supportedIntervals)
  const preferred = STYLE_PRIMARY_INTERVALS[style].filter((interval) => allowed.includes(interval))
  return preferred.length ? preferred : allowed.slice(0, 5)
}

export function getStyleDefaultInterval(tradingStyle = 'swing', supportedIntervals = []) {
  const style = normalizeTradingStyle(tradingStyle)
  const ordered = getStyleIntervalOptions(style, supportedIntervals)
  const preferred = STYLE_DEFAULT_INTERVALS[style]
  if (ordered.includes(preferred)) return preferred
  return ordered[0] || preferred
}

export function getStyleDefaultHorizon(tradingStyle = 'swing', interval = '') {
  const style = normalizeTradingStyle(tradingStyle)
  return STYLE_RECOMMENDED_HORIZONS[style]?.[String(interval || '').trim()] || STYLE_DEFAULT_HORIZONS[style]
}

export function buildIntervalModel({ tradingStyle = 'swing', interval = '5m', horizon = 5 } = {}) {
  const style = normalizeTradingStyle(tradingStyle)
  const normalizedInterval = String(interval || getStyleDefaultInterval(style)).trim()
  const intervalMinutes = INTERVAL_MINUTES[normalizedInterval] || 5
  const normalizedHorizon = clampNumber(horizon, getStyleDefaultHorizon(style, normalizedInterval), 1, 50)
  const totalMinutes = intervalMinutes * normalizedHorizon
  const recommendedHorizon = getStyleDefaultHorizon(style, normalizedInterval)
  const recommendedDuration = formatDurationLabel(intervalMinutes * recommendedHorizon)
  const holdingDuration = formatDurationLabel(totalMinutes)
  const caution = style === 'intraday' && totalMinutes > 240

  if (style === 'intraday') {
    if (totalMinutes <= 30) {
      return {
        tone: 'positive',
        label: 'Fast intraday frame',
        detail: `${normalizedInterval} x ${normalizedHorizon} bars keeps the hold window near ${holdingDuration}, which still fits the opening-drive and momentum part of the day.`,
        recommendedHorizon,
        recommendedDetail: `Intraday default for ${normalizedInterval} is about ${recommendedHorizon} bars (~${recommendedDuration}).`,
        caution: false,
        totalMinutes,
      }
    }
    if (totalMinutes <= 180) {
      return {
        tone: 'positive',
        label: 'Core intraday frame',
        detail: `${normalizedInterval} x ${normalizedHorizon} bars holds the idea inside a same-session window (~${holdingDuration}) without drifting too far into overnight logic.`,
        recommendedHorizon,
        recommendedDetail: `Intraday default for ${normalizedInterval} is about ${recommendedHorizon} bars (~${recommendedDuration}).`,
        caution: false,
        totalMinutes,
      }
    }
    if (totalMinutes <= 390) {
      return {
        tone: 'warning',
        label: 'Extended day hold',
        detail: `${normalizedInterval} x ${normalizedHorizon} bars stretches the setup across most of the regular session (~${holdingDuration}). Make sure the edge still behaves like a day-trade and not a swing carry.`,
        recommendedHorizon,
        recommendedDetail: `Trim closer to ${recommendedHorizon} bars (~${recommendedDuration}) if you want a tighter intraday loop.`,
        caution: true,
        totalMinutes,
      }
    }
    return {
      tone: 'negative',
      label: 'Too slow for intraday',
      detail: `${normalizedInterval} x ${normalizedHorizon} bars projects roughly ${holdingDuration}. That is closer to multi-session thinking than a same-day operating frame.`,
      recommendedHorizon,
      recommendedDetail: `For intraday mode, ${normalizedInterval} usually works better closer to ${recommendedHorizon} bars (~${recommendedDuration}).`,
      caution,
      totalMinutes,
    }
  }

  if (totalMinutes >= 1440) {
    return {
      tone: 'positive',
      label: 'Multi-session frame',
      detail: `${normalizedInterval} x ${normalizedHorizon} bars projects roughly ${holdingDuration}, which fits broader swing follow-through and replay learning.`,
      recommendedHorizon,
      recommendedDetail: `Swing default for ${normalizedInterval} is about ${recommendedHorizon} bars (~${recommendedDuration}).`,
      caution: false,
      totalMinutes,
    }
  }

  if (totalMinutes >= 240) {
    return {
      tone: 'warning',
      label: 'Hybrid frame',
      detail: `${normalizedInterval} x ${normalizedHorizon} bars keeps the setup inside hours instead of days (~${holdingDuration}). That is usable, but it behaves more like fast swing review than classic intraday momentum.`,
      recommendedHorizon,
      recommendedDetail: `Swing default for ${normalizedInterval} is about ${recommendedHorizon} bars (~${recommendedDuration}).`,
      caution: false,
      totalMinutes,
    }
  }

  return {
    tone: 'warning',
    label: 'Fast for swing mode',
    detail: `${normalizedInterval} x ${normalizedHorizon} bars only holds the setup for about ${holdingDuration}. That is still workable, but it is closer to day-trading cadence than a broader swing frame.`,
    recommendedHorizon,
    recommendedDetail: `Broader swing follow-through usually starts closer to ${recommendedHorizon} bars (~${recommendedDuration}).`,
    caution: false,
    totalMinutes,
  }
}

export function buildTradingSessionModel({
  tradingStyle = 'swing',
  regularHoursOnly = false,
  openingRangeMinutes = 15,
  flattenBeforeCloseMinutes = 10,
  now = new Date(),
} = {}) {
  const style = normalizeTradingStyle(tradingStyle)
  const openingRange = clampNumber(openingRangeMinutes, 15, 5, 60)
  const flattenWindow = clampNumber(flattenBeforeCloseMinutes, 10, 1, 60)
  const parts = getEtParts(now)
  const minutes = parts.minutesSinceMidnight
  const isWeekend = parts.weekday === 'Sat' || parts.weekday === 'Sun'
  const openingRangeEnd = MARKET_OPEN_MINUTES + openingRange
  const closeBufferStart = Math.max(MARKET_OPEN_MINUTES, MARKET_CLOSE_MINUTES - flattenWindow)

  if (isWeekend) {
    return {
      phase: 'weekend',
      label: 'Weekend prep',
      tone: 'neutral',
      timeLabel: parts.timeLabel,
      detail:
        style === 'intraday'
          ? 'Market is closed, so intraday work should stay in planning, replay, and calendar review mode.'
          : 'Market is closed, so this is a good time for higher-timeframe review and prep.',
      preferredInterval: getStyleDefaultInterval(style),
      preferredHorizon: getStyleDefaultHorizon(style, getStyleDefaultInterval(style)),
      regularHoursOnly: Boolean(regularHoursOnly),
    }
  }

  if (minutes < PREMARKET_START_MINUTES || minutes >= AFTER_HOURS_CLOSE_MINUTES) {
    return {
      phase: 'overnight',
      label: 'Overnight prep',
      tone: style === 'intraday' ? 'warning' : 'neutral',
      timeLabel: parts.timeLabel,
      detail:
        style === 'intraday'
          ? 'The active session is closed. Use this time for watchlist prep, catalyst review, and opening-range planning instead of fresh execution.'
          : 'The active session is closed. Focus on planning and broader review rather than execution.',
      preferredInterval: getStyleDefaultInterval(style),
      preferredHorizon: getStyleDefaultHorizon(style, getStyleDefaultInterval(style)),
      regularHoursOnly: Boolean(regularHoursOnly),
    }
  }

  if (minutes < MARKET_OPEN_MINUTES) {
    return {
      phase: 'premarket',
      label: regularHoursOnly ? 'Premarket planning' : 'Pre-market session',
      tone: style === 'intraday' ? 'warning' : 'neutral',
      timeLabel: parts.timeLabel,
      detail: regularHoursOnly
        ? 'Regular-hours-only mode keeps pre-market work in planning until the core session opens.'
        : 'Pre-market equity routing is active with limit-only price control, smaller size, and stricter liquidity checks.',
      preferredInterval: style === 'intraday' ? '5m' : getStyleDefaultInterval(style),
      preferredHorizon: style === 'intraday' ? 5 : getStyleDefaultHorizon(style, getStyleDefaultInterval(style)),
      regularHoursOnly: Boolean(regularHoursOnly),
    }
  }

  if (minutes < openingRangeEnd) {
    return {
      phase: 'opening_range',
      label: `Opening range (${formatMinuteWindow(openingRange)})`,
      tone: 'positive',
      timeLabel: parts.timeLabel,
      detail:
        style === 'intraday'
          ? `The first ${openingRange}m are active now. Favor tighter intraday frames and let breakout logic respect the opening range before size expands.`
          : 'The market is in the opening range. Swing mode can still use the read, but it should avoid turning opening volatility into forced execution.',
      preferredInterval: style === 'intraday' ? (openingRange <= 10 ? '1m' : '5m') : getStyleDefaultInterval(style),
      preferredHorizon: style === 'intraday' ? 5 : getStyleDefaultHorizon(style, getStyleDefaultInterval(style)),
      regularHoursOnly: Boolean(regularHoursOnly),
    }
  }

  if (minutes < MIDDAY_START_MINUTES) {
    return {
      phase: 'morning_session',
      label: 'Morning drive',
      tone: 'positive',
      timeLabel: parts.timeLabel,
      detail:
        style === 'intraday'
          ? 'This is still the cleanest part of the regular session for intraday continuation and orderly post-open follow-through.'
          : 'The market is fully open and directional context is forming. Swing setups can use this for cleaner confirmation instead of chasing the first burst.',
      preferredInterval: style === 'intraday' ? '5m' : getStyleDefaultInterval(style),
      preferredHorizon: style === 'intraday' ? 6 : getStyleDefaultHorizon(style, getStyleDefaultInterval(style)),
      regularHoursOnly: Boolean(regularHoursOnly),
    }
  }

  if (minutes < AFTERNOON_START_MINUTES) {
    return {
      phase: 'midday',
      label: 'Midday compression',
      tone: style === 'intraday' ? 'warning' : 'neutral',
      timeLabel: parts.timeLabel,
      detail:
        style === 'intraday'
          ? 'Expect slower tape, more fake breaks, and a higher bar for new entries. Favor only the cleanest setups or wait for better session energy.'
          : 'Midday noise is more likely, so use this stretch for review and patience rather than forcing entries.',
      preferredInterval: style === 'intraday' ? '15m' : getStyleDefaultInterval(style),
      preferredHorizon: style === 'intraday' ? 4 : getStyleDefaultHorizon(style, getStyleDefaultInterval(style)),
      regularHoursOnly: Boolean(regularHoursOnly),
    }
  }

  if (minutes < POWER_HOUR_START_MINUTES) {
    return {
      phase: 'afternoon_session',
      label: 'Afternoon reset',
      tone: style === 'intraday' ? 'neutral' : 'positive',
      timeLabel: parts.timeLabel,
      detail:
        style === 'intraday'
          ? 'The tape is rebuilding after midday. This is a cleaner time to narrow the board again before the final hour.'
          : 'Afternoon structure is cleaner again, which can help broader setups confirm without the opening burst.',
      preferredInterval: style === 'intraday' ? '5m' : getStyleDefaultInterval(style),
      preferredHorizon: style === 'intraday' ? 4 : getStyleDefaultHorizon(style, getStyleDefaultInterval(style)),
      regularHoursOnly: Boolean(regularHoursOnly),
    }
  }

  if (minutes < closeBufferStart) {
    return {
      phase: 'power_hour',
      label: 'Power hour',
      tone: 'warning',
      timeLabel: parts.timeLabel,
      detail:
        style === 'intraday'
          ? 'Volatility often returns in the last hour. New trades need cleaner exits because the closing buffer is approaching.'
          : 'The final hour can create strong confirmation, but it is still late-session movement and should not force oversized execution.',
      preferredInterval: style === 'intraday' ? '5m' : getStyleDefaultInterval(style),
      preferredHorizon: style === 'intraday' ? 3 : getStyleDefaultHorizon(style, getStyleDefaultInterval(style)),
      regularHoursOnly: Boolean(regularHoursOnly),
    }
  }

  if (minutes < MARKET_CLOSE_MINUTES) {
    return {
      phase: 'closing_window',
      label: `Closing buffer (${formatMinuteWindow(flattenWindow)})`,
      tone: style === 'intraday' ? 'negative' : 'warning',
      timeLabel: parts.timeLabel,
      detail:
        style === 'intraday'
          ? `New intraday entries should slow down here. Keep the focus on flattening or reducing same-day risk before the close buffer ends.`
          : 'The close is near. This is better for review and risk cleanup than for forcing fresh entries.',
      preferredInterval: style === 'intraday' ? '5m' : getStyleDefaultInterval(style),
      preferredHorizon: style === 'intraday' ? 2 : getStyleDefaultHorizon(style, getStyleDefaultInterval(style)),
      regularHoursOnly: Boolean(regularHoursOnly),
    }
  }

  return {
    phase: 'after_hours',
    label: regularHoursOnly ? 'After-hours locked' : 'After-hours session',
    tone: regularHoursOnly ? 'negative' : 'warning',
    timeLabel: parts.timeLabel,
    detail: regularHoursOnly
      ? 'Regular-hours mode is active, so fresh execution should wait for the next core session.'
      : 'After-hours routing is available, but spreads and event risk are more fragile than the regular session.',
    preferredInterval: style === 'intraday' ? '15m' : getStyleDefaultInterval(style),
    preferredHorizon: style === 'intraday' ? 3 : getStyleDefaultHorizon(style, getStyleDefaultInterval(style)),
    regularHoursOnly: Boolean(regularHoursOnly),
  }
}

export function buildEventWindowModel({
  tradingStyle = 'swing',
  eventContext = null,
  intradayEventGuardMinutes = 30,
  sessionModel = null,
} = {}) {
  const style = normalizeTradingStyle(tradingStyle)
  const nextEventName = String(eventContext?.next_event_name || '').trim()
  const nextEventDays = toNumber(eventContext?.next_event_days)
  const eventRisk = Boolean(eventContext?.event_risk)
  const eventWindowLabel = normalizeEventWindowLabel(
    eventContext?.event_window_label,
    nextEventName,
    eventRisk,
  )
  const guardLabel = formatMinuteWindow(clampNumber(intradayEventGuardMinutes, 30, 0, 180))
  const eventBaseLabel =
    eventWindowLabel === 'earnings_window'
      ? 'Earnings'
      : eventWindowLabel === 'macro_window'
        ? 'Macro'
        : eventWindowLabel === 'corporate_window'
          ? 'Corporate'
          : eventWindowLabel === 'quiet_window'
            ? 'Quiet'
            : 'Catalyst'

  if (style === 'intraday') {
    if (!nextEventName && !eventRisk) {
      return {
        active: false,
        tone: 'positive',
        label: 'Quiet session',
        badgeLabel: 'Quiet queue',
        detail: 'No same-session catalyst is active right now, so the intraday board can prioritize session structure over calendar defense.',
        daysUntil: null,
      }
    }

    if ((nextEventDays !== null && nextEventDays <= 0) || sessionModel?.phase === 'opening_range' && eventRisk) {
      return {
        active: true,
        tone: 'negative',
        label: 'Same-session catalyst',
        badgeLabel: `${eventBaseLabel} today`,
        detail: `${nextEventName || 'A known catalyst'} is inside the same-session event window. Treat the ${guardLabel} guard as no-initiation territory unless the setup has already resolved cleanly.`,
        daysUntil: nextEventDays,
      }
    }

    if (nextEventDays !== null && nextEventDays === 1) {
      return {
        active: true,
        tone: 'warning',
        label: 'Next-session catalyst',
        badgeLabel: `${eventBaseLabel} 1d`,
        detail: `${nextEventName || 'A known catalyst'} lands before the next full session, so late-day entries should avoid carrying fragile intraday edge into the open.`,
        daysUntil: nextEventDays,
      }
    }

    if (eventRisk || nextEventName) {
      return {
        active: true,
        tone: 'warning',
        label: 'Catalyst on deck',
        badgeLabel: `${eventBaseLabel} watch`,
        detail: `${nextEventName || 'A catalyst'} is close enough that intraday setups should keep horizon tight and respect the ${guardLabel} event buffer.`,
        daysUntil: nextEventDays,
      }
    }

    return {
      active: false,
      tone: 'positive',
      label: 'Quiet session',
      badgeLabel: 'Quiet queue',
      detail: 'No same-session catalyst is active right now, so the intraday board can prioritize session structure over calendar defense.',
      daysUntil: nextEventDays,
    }
  }

  if (!nextEventName && !eventRisk) {
    return {
      active: false,
      tone: 'positive',
      label: 'Quiet calendar',
      badgeLabel: 'Quiet queue',
      detail: 'No near-term catalyst is distorting the broader setup horizon.',
      daysUntil: null,
    }
  }

  if (nextEventDays !== null && nextEventDays <= 1) {
    return {
      active: true,
      tone: 'warning',
      label: 'Catalyst close',
      badgeLabel: `${eventBaseLabel} ${nextEventDays === 0 ? 'today' : '1d'}`,
      detail: `${nextEventName || 'A catalyst'} is close enough that the setup should stay conditional until the event window clears.`,
      daysUntil: nextEventDays,
    }
  }

  return {
    active: true,
    tone: eventRisk ? 'warning' : 'neutral',
    label: 'Catalyst on deck',
    badgeLabel: nextEventDays === null ? `${eventBaseLabel} on deck` : `${eventBaseLabel} ${nextEventDays}d`,
    detail: `${nextEventName || 'A catalyst'} remains close enough that timing and holding-period assumptions should stay event-aware.`,
    daysUntil: nextEventDays,
  }
}

export function buildIntradayModelSummary({
  tradingStyle = 'swing',
  preferences = {},
  now = new Date(),
} = {}) {
  const style = normalizeTradingStyle(tradingStyle)
  const sessionModel = buildTradingSessionModel({
    tradingStyle: style,
    regularHoursOnly: preferences?.regularHoursOnly === true,
    openingRangeMinutes: preferences?.openingRangeMinutes,
    flattenBeforeCloseMinutes: preferences?.flattenBeforeCloseMinutes,
    now,
  })
  const intervalModel = buildIntervalModel({
    tradingStyle: style,
    interval: preferences?.defaultInterval || getStyleDefaultInterval(style),
    horizon: preferences?.defaultHorizon || getStyleDefaultHorizon(style, preferences?.defaultInterval),
  })

  return {
    sessionModel,
    intervalModel,
    openingRangeLabel: formatMinuteWindow(preferences?.openingRangeMinutes ?? 15),
    eventGuardLabel: formatMinuteWindow(preferences?.intradayEventGuardMinutes ?? 30),
    flattenLabel: formatMinuteWindow(preferences?.flattenBeforeCloseMinutes ?? 10),
    styleLabel: style === 'intraday' ? 'Intraday' : 'Swing',
    summary:
      style === 'intraday'
        ? `Intraday mode is tuned around a ${formatMinuteWindow(preferences?.openingRangeMinutes ?? 15)} opening range, a ${formatMinuteWindow(preferences?.intradayEventGuardMinutes ?? 30)} event buffer, and a ${formatMinuteWindow(preferences?.flattenBeforeCloseMinutes ?? 10)} close-out window.`
        : 'Swing mode keeps broader interval and holding-period context, but the session clock still informs when execution should stay patient.',
  }
}
