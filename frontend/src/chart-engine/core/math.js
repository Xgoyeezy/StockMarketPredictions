export function clamp(value, minimum, maximum) {
  return Math.min(Math.max(value, minimum), maximum)
}

export function toNumber(value) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

const compactValueFormatter = new Intl.NumberFormat('en-US', {
  notation: 'compact',
  maximumFractionDigits: 2,
})

function niceStep(rawStep) {
  if (!Number.isFinite(rawStep) || rawStep <= 0) return 1
  const magnitude = 10 ** Math.floor(Math.log10(rawStep))
  const normalized = rawStep / magnitude

  let multiplier = 1
  if (normalized <= 1) multiplier = 1
  else if (normalized <= 2) multiplier = 2
  else if (normalized <= 2.5) multiplier = 2.5
  else if (normalized <= 5) multiplier = 5
  else multiplier = 10

  return multiplier * magnitude
}

export function formatCompactValue(value) {
  if (!Number.isFinite(value)) return '--'
  return compactValueFormatter.format(value)
}

export function buildNicePriceTicks(minimum, maximum, targetCount = 6, formatter = null) {
  if (!Number.isFinite(minimum) || !Number.isFinite(maximum)) return []
  if (minimum === maximum) {
    return [{ value: minimum, label: formatter ? formatter(minimum) : minimum.toFixed(2) }]
  }

  const step = niceStep(Math.abs(maximum - minimum) / Math.max(targetCount - 1, 1))
  const first = Math.floor(minimum / step) * step
  const last = Math.ceil(maximum / step) * step
  const precision = step >= 1 ? 2 : Math.min(6, Math.max(2, Math.ceil(Math.abs(Math.log10(step)))))
  const ticks = []

  for (let value = first; value <= last + step * 0.5; value += step) {
    ticks.push({
      value,
      label: formatter ? formatter(value) : value.toFixed(precision),
    })
  }

  return ticks
}

export function targetPriceTickCount(height) {
  if (!Number.isFinite(height) || height <= 0) return 6
  return clamp(Math.round(height / 68), 4, 10)
}

export function targetTimeTickCount(width) {
  if (!Number.isFinite(width) || width <= 0) return 6
  return clamp(Math.round(width / 120), 4, 10)
}

export function formatTimeAxisLabel(rawTime, interval) {
  const parsed = new Date(rawTime || 0)
  if (Number.isNaN(parsed.getTime())) return ''

  const lowerInterval = String(interval || '').toLowerCase()
  if (lowerInterval === '1d' || lowerInterval === '4h') {
    return parsed.toLocaleDateString([], { month: 'short', day: 'numeric' })
  }

  const isDayStart = parsed.getHours() === 0 && parsed.getMinutes() === 0
  if (isDayStart) {
    return parsed.toLocaleDateString([], { month: 'short', day: 'numeric' })
  }

  return parsed.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

export function getSessionType(rawTime) {
  const parsed = new Date(rawTime || 0)
  if (Number.isNaN(parsed.getTime())) return 'future'

  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
    .formatToParts(parsed)
    .filter((part) => part.type !== 'literal')
    .reduce((accumulator, part) => ({ ...accumulator, [part.type]: Number(part.value) }), {})

  const totalMinutes = (parts.hour || 0) * 60 + (parts.minute || 0)
  if (totalMinutes >= 4 * 60 && totalMinutes < 9 * 60 + 30) return 'premarket'
  if (totalMinutes >= 9 * 60 + 30 && totalMinutes < 16 * 60) return 'regular'
  if (totalMinutes >= 16 * 60 && totalMinutes <= 20 * 60) return 'afterhours'
  return 'future'
}
