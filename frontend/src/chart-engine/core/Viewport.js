import { clamp, toNumber } from './math.js'

const FUTURE_PADDING_BARS = 12
const MIN_VISIBLE_BARS = 24
const MAX_RESTORE_RANGE_MULTIPLIER = 8
const MAX_PRICE_RANGE_MULTIPLIER = 12
const MAX_LEADING_WHITESPACE_MULTIPLIER = 0.9
const MAX_TRAILING_WHITESPACE_MULTIPLIER = 1.4

function visibleBarsForInterval(interval) {
  switch (String(interval || '').toLowerCase()) {
    case '1m':
      return 180
    case '5m':
      return 140
    case '15m':
      return 120
    case '30m':
      return 110
    case '1h':
      return 100
    case '4h':
      return 90
    case '1d':
      return 120
    default:
      return 120
  }
}

function findNearestIndexByTime(rows, timestamp) {
  const target = new Date(timestamp || 0).getTime()
  if (!Number.isFinite(target) || !rows.length) return null

  let bestIndex = 0
  let bestDistance = Number.POSITIVE_INFINITY
  for (const [index, row] of rows.entries()) {
    const rowTime = new Date(row.rawTime || 0).getTime()
    if (!Number.isFinite(rowTime)) continue
    const distance = Math.abs(rowTime - target)
    if (distance < bestDistance) {
      bestDistance = distance
      bestIndex = index
    }
  }

  return bestIndex
}

function derivePriceRange(rows, startIndex, endIndex, extraValues = []) {
  if (!rows.length) return { minPrice: 0, maxPrice: 1 }
  const lowerIndex = clamp(Math.floor(startIndex), 0, rows.length - 1)
  const upperIndex = clamp(Math.ceil(Math.min(endIndex, rows.length - 1)), 0, rows.length - 1)
  const visibleRows = rows.slice(lowerIndex, upperIndex + 1)
  const rangeValues = [
    ...visibleRows.flatMap((row) => [row.low, row.high]),
    ...extraValues.filter((value) => Number.isFinite(value)),
  ]
  const low = Math.min(...rangeValues)
  const high = Math.max(...rangeValues)
  const span = Math.max(high - low, 0.5)
  const padding = span * 0.08

  return {
    minPrice: low - padding,
    maxPrice: high + padding,
  }
}

function buildVisibleSpan(rows, interval) {
  const preferred = visibleBarsForInterval(interval)
  return Math.max(
    Math.min(preferred, rows.length + FUTURE_PADDING_BARS + 16),
    MIN_VISIBLE_BARS,
  )
}

function isSaneLogicalRange(rows, startIndex, endIndex, interval) {
  if (!Number.isFinite(startIndex) || !Number.isFinite(endIndex) || endIndex <= startIndex) {
    return false
  }

  const latestIndex = rows.length - 1
  const span = endIndex - startIndex
  const maxReasonableSpan = Math.max(
    buildVisibleSpan(rows, interval) * MAX_RESTORE_RANGE_MULTIPLIER,
    rows.length + FUTURE_PADDING_BARS * 6,
    MIN_VISIBLE_BARS,
  )

  if (span > maxReasonableSpan) return false
  if (endIndex < -FUTURE_PADDING_BARS * 2) return false
  if (startIndex > latestIndex + FUTURE_PADDING_BARS * 6) return false
  if (!hasReasonableWhitespace(rows, startIndex, endIndex)) return false

  return true
}

function hasReasonableWhitespace(rows, startIndex, endIndex) {
  if (!rows.length) return true
  const span = Math.max(endIndex - startIndex, MIN_VISIBLE_BARS)
  const leadingWhitespace = Math.max(0, -startIndex)
  const trailingWhitespace = Math.max(0, endIndex - (rows.length - 1))
  const maxLeadingWhitespace = Math.max(FUTURE_PADDING_BARS * 2, span * MAX_LEADING_WHITESPACE_MULTIPLIER)
  const maxTrailingWhitespace = Math.max(FUTURE_PADDING_BARS * 3, span * MAX_TRAILING_WHITESPACE_MULTIPLIER)

  return leadingWhitespace <= maxLeadingWhitespace && trailingWhitespace <= maxTrailingWhitespace
}

function isSanePriceRange(savedRange, derivedRange) {
  const savedSpan = savedRange.maxPrice - savedRange.minPrice
  const derivedSpan = Math.max(derivedRange.maxPrice - derivedRange.minPrice, 1e-6)

  if (!Number.isFinite(savedSpan) || savedSpan <= 0) return false
  if (savedSpan > derivedSpan * MAX_PRICE_RANGE_MULTIPLIER) return false
  if (savedSpan < derivedSpan / MAX_PRICE_RANGE_MULTIPLIER) return false
  if (savedRange.maxPrice < derivedRange.minPrice - derivedSpan * 4) return false
  if (savedRange.minPrice > derivedRange.maxPrice + derivedSpan * 4) return false

  return true
}

export function fitPriceRangeToViewport(rows, viewport, extraValues = []) {
  const derived = derivePriceRange(rows, viewport.startIndex, viewport.endIndex, extraValues)
  return {
    ...viewport,
    ...derived,
  }
}

function clampLogicalRange(rows, startIndex, endIndex) {
  const span = Math.max(endIndex - startIndex, MIN_VISIBLE_BARS)
  const maxLeadingWhitespace = Math.max(FUTURE_PADDING_BARS * 10, span * MAX_LEADING_WHITESPACE_MULTIPLIER)
  const maxTrailingWhitespace = Math.max(FUTURE_PADDING_BARS * 14, span * MAX_TRAILING_WHITESPACE_MULTIPLIER)
  const minStart = -maxLeadingWhitespace
  const maxEnd = Math.max(rows.length - 1 + maxTrailingWhitespace, minStart + span)
  let nextStart = startIndex
  let nextEnd = endIndex

  if (nextStart < minStart) {
    const delta = minStart - nextStart
    nextStart += delta
    nextEnd += delta
  }

  if (nextEnd > maxEnd) {
    const delta = nextEnd - maxEnd
    nextStart -= delta
    nextEnd -= delta
  }

  if (nextStart < minStart) {
    nextStart = minStart
    nextEnd = minStart + span
  }

  if (nextEnd > maxEnd) {
    nextEnd = maxEnd
    nextStart = maxEnd - span
  }

  return {
    startIndex: nextStart,
    endIndex: nextEnd,
  }
}

function normalizeRange(rows, startIndex, endIndex) {
  const span = Math.max(endIndex - startIndex, MIN_VISIBLE_BARS)
  const center = (startIndex + endIndex) / 2
  return clampLogicalRange(
    rows,
    center - span / 2,
    center + span / 2,
  )
}

export function buildInitialViewport(rows, interval, extraValues = []) {
  if (!rows.length) {
    return {
      startIndex: 0,
      endIndex: 60,
      minPrice: 0,
      maxPrice: 1,
    }
  }

  const barsVisible = buildVisibleSpan(rows, interval)
  const lastIndex = rows.length - 1
  const endIndex = lastIndex + FUTURE_PADDING_BARS
  const startIndex = endIndex - barsVisible
  const priceRange = derivePriceRange(rows, startIndex, endIndex, extraValues)

  return {
    startIndex,
    endIndex,
    ...priceRange,
  }
}

export function resetViewport(rows, interval, extraValues = []) {
  return buildInitialViewport(rows, interval, extraValues)
}

export function fitTimeRangeToViewport(rows, interval, currentViewport, extraValues = []) {
  const nextViewport = buildInitialViewport(rows, interval, extraValues)
  if (!currentViewport) return nextViewport

  return {
    ...currentViewport,
    startIndex: nextViewport.startIndex,
    endIndex: nextViewport.endIndex,
  }
}

export function resolveViewport(rows, savedViewport, interval, extraValues = []) {
  const fallback = buildInitialViewport(rows, interval, extraValues)
  if (!rows.length) return fallback

  let startIndex = fallback.startIndex
  let endIndex = fallback.endIndex

  if (Array.isArray(savedViewport?.xaxisLogicalRange) && savedViewport.xaxisLogicalRange.length === 2) {
    const from = toNumber(savedViewport.xaxisLogicalRange[0])
    const to = toNumber(savedViewport.xaxisLogicalRange[1])
    if (from !== null && to !== null && isSaneLogicalRange(rows, from, to, interval)) {
      startIndex = from
      endIndex = to
    }
  } else if (Array.isArray(savedViewport?.xaxisRange) && savedViewport.xaxisRange.length === 2) {
    const fromIndex = findNearestIndexByTime(rows, savedViewport.xaxisRange[0])
    const toIndex = findNearestIndexByTime(rows, savedViewport.xaxisRange[1])
    if (
      fromIndex !== null &&
      toIndex !== null &&
      isSaneLogicalRange(rows, fromIndex, toIndex, interval)
    ) {
      startIndex = fromIndex
      endIndex = toIndex
    }
  }

  const normalizedRange = normalizeRange(rows, startIndex, endIndex)
  const savedMin = Array.isArray(savedViewport?.yaxisRange) ? toNumber(savedViewport.yaxisRange[0]) : null
  const savedMax = Array.isArray(savedViewport?.yaxisRange) ? toNumber(savedViewport.yaxisRange[1]) : null
  const derivedPriceRange = derivePriceRange(
    rows,
    normalizedRange.startIndex,
    normalizedRange.endIndex,
    extraValues,
  )
  const savedPriceRange =
    savedMin !== null && savedMax !== null && savedMax > savedMin
      ? { minPrice: savedMin, maxPrice: savedMax }
      : null

  return {
    ...normalizedRange,
    ...(savedPriceRange && isSanePriceRange(savedPriceRange, derivedPriceRange)
      ? savedPriceRange
      : derivedPriceRange),
  }
}

export function panViewport(viewport, deltaIndex, deltaPrice, rows) {
  const nextRange = normalizeRange(
    rows,
    viewport.startIndex + deltaIndex,
    viewport.endIndex + deltaIndex,
  )

  return {
    startIndex: nextRange.startIndex,
    endIndex: nextRange.endIndex,
    minPrice: viewport.minPrice + deltaPrice,
    maxPrice: viewport.maxPrice + deltaPrice,
  }
}

export function zoomViewport(viewport, rows, anchorIndex, zoomFactor) {
  const span = Math.max(viewport.endIndex - viewport.startIndex, MIN_VISIBLE_BARS)
  const nextSpan = clamp(
    span * zoomFactor,
    MIN_VISIBLE_BARS,
    20000,
  )
  const clampedAnchor = Number.isFinite(anchorIndex)
    ? anchorIndex
    : viewport.startIndex + span / 2
  const ratio = span > 0 ? (clampedAnchor - viewport.startIndex) / span : 0.5
  const nextStart = clampedAnchor - nextSpan * ratio
  const nextEnd = nextStart + nextSpan
  const nextRange = normalizeRange(rows, nextStart, nextEnd)

  return {
    ...viewport,
    startIndex: nextRange.startIndex,
    endIndex: nextRange.endIndex,
  }
}

export function viewportToPersist(rows, viewport) {
  if (!rows.length || !viewport) return null

  const clampedRange = clampLogicalRange(rows, viewport.startIndex, viewport.endIndex)
  const lowerIndex = clamp(Math.round(clampedRange.startIndex), 0, rows.length - 1)
  const upperIndex = clamp(Math.round(Math.min(clampedRange.endIndex, rows.length - 1)), 0, rows.length - 1)

  return {
    xaxisRange: [rows[lowerIndex]?.rawTime || null, rows[upperIndex]?.rawTime || null],
    xaxisLogicalRange: [
      Number(clampedRange.startIndex.toFixed(4)),
      Number(clampedRange.endIndex.toFixed(4)),
    ],
    yaxisRange: [
      Number(viewport.minPrice.toFixed(6)),
      Number(viewport.maxPrice.toFixed(6)),
    ],
  }
}
