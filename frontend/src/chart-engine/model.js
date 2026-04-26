import { toNumber } from './core/math.js'

/**
 * @typedef {Object} NormalizedChartRow
 * @property {number} sourceIndex
 * @property {string} rawTime
 * @property {number} open
 * @property {number} high
 * @property {number} low
 * @property {number} close
 * @property {number} volume
 */

/**
 * Normalize an incoming timestamp into the chart engine's ISO-8601 time key.
 *
 * @param {string | number | Date | null | undefined} value
 * @returns {string | null}
 */
export function normalizeChartTimestamp(value) {
  if (!value) return null
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return null
  return parsed.toISOString()
}

/**
 * Convert a payload with raw candle objects into the row contract consumed by
 * the chart engine and the workstation chart component.
 *
 * @param {{ candles?: Array<Record<string, unknown>> } | null | undefined} payload
 * @returns {NormalizedChartRow[]}
 */
export function normalizeChartRows(payload) {
  return (Array.isArray(payload?.candles) ? payload.candles : [])
    .map((candle, sourceIndex) => {
      const rawTime = normalizeChartTimestamp(candle?.datetime)
      const open = toNumber(candle?.open)
      const high = toNumber(candle?.high)
      const low = toNumber(candle?.low)
      const close = toNumber(candle?.close)
      const volume = toNumber(candle?.volume) ?? 0
      if (!rawTime || open === null || high === null || low === null || close === null) return null
      if (open <= 0 || high <= 0 || low <= 0 || close <= 0) return null

      return {
        sourceIndex,
        rawTime,
        open,
        high: Math.max(open, high, low, close),
        low: Math.min(open, high, low, close),
        close,
        volume,
      }
    })
    .filter(Boolean)
}

/**
 * Lightweight gate for deciding whether a payload contains enough normalized
 * rows to render a real chart state.
 *
 * @param {{ candles?: Array<Record<string, unknown>> } | null | undefined} payload
 * @returns {boolean}
 */
export function hasRenderableChartRows(payload) {
  return normalizeChartRows(payload).length > 1
}
