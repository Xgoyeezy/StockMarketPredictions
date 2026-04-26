function round(value, digits = 4) {
  return Number(Number(value).toFixed(digits))
}

function buildSyntheticCandles({ count = 240, intervalMinutes = 5 } = {}) {
  const candles = []
  const startTime = Date.UTC(2026, 3, 17, 13, 30, 0)

  for (let index = 0; index < count; index += 1) {
    const drift = index * 0.08
    const wave = Math.sin(index / 9) * 1.15
    const open = 100 + drift + wave
    const close = open + Math.cos(index / 7) * 0.42
    const high = Math.max(open, close) + 0.34 + (index % 5) * 0.02
    const low = Math.min(open, close) - 0.29 - (index % 3) * 0.015
    candles.push({
      datetime: new Date(startTime + index * intervalMinutes * 60 * 1000).toISOString(),
      open: round(open),
      high: round(high),
      low: round(low),
      close: round(close),
      volume: 45000 + index * 180 + (index % 11) * 250,
    })
  }

  return candles
}

function buildEmaSeries(values, period) {
  if (!Array.isArray(values) || values.length === 0) return []
  const multiplier = 2 / (period + 1)
  let previous = Number(values[0]) || 0
  return values.map((value, index) => {
    const numeric = Number(value) || 0
    if (index === 0) {
      previous = numeric
      return round(previous)
    }
    previous = numeric * multiplier + previous * (1 - multiplier)
    return round(previous)
  })
}

function buildRsiSeries(values, period = 14) {
  if (!Array.isArray(values) || values.length === 0) return []
  const result = Array(values.length).fill(null)
  let averageGain = 0
  let averageLoss = 0

  for (let index = 1; index < values.length; index += 1) {
    const delta = Number(values[index]) - Number(values[index - 1])
    const gain = Math.max(delta, 0)
    const loss = Math.max(-delta, 0)

    if (index <= period) {
      averageGain += gain
      averageLoss += loss
      if (index === period) {
        averageGain /= period
        averageLoss /= period
        const rs = averageLoss === 0 ? 100 : averageGain / averageLoss
        result[index] = round(100 - 100 / (1 + rs))
      }
      continue
    }

    averageGain = (averageGain * (period - 1) + gain) / period
    averageLoss = (averageLoss * (period - 1) + loss) / period
    const rs = averageLoss === 0 ? 100 : averageGain / averageLoss
    result[index] = round(100 - 100 / (1 + rs))
  }

  return result
}

export function buildChartDemoPayload({
  ticker = 'SPY',
  interval = '5m',
  period = '5d',
  count = 240,
  intervalMinutes = 5,
} = {}) {
  const candles = buildSyntheticCandles({ count, intervalMinutes })
  const closes = candles.map((candle) => candle.close)
  const highs = candles.map((candle) => candle.high)
  const lows = candles.map((candle) => candle.low)
  const volumes = candles.map((candle) => candle.volume)
  const ema9 = buildEmaSeries(closes, 9)
  const ema12 = buildEmaSeries(closes, 12)
  const ema21 = buildEmaSeries(closes, 21)
  const ema26 = buildEmaSeries(closes, 26)
  const rsi14 = buildRsiSeries(closes, 14)
  const macd = ema12.map((value, index) => round(value - ema26[index]))
  const macdSignal = buildEmaSeries(macd, 9)
  const macdHistogram = macd.map((value, index) => round(value - (macdSignal[index] ?? 0)))

  let cumulativeVolume = 0
  let cumulativePriceVolume = 0
  const vwap = candles.map((candle, index) => {
    cumulativeVolume += volumes[index]
    cumulativePriceVolume += closes[index] * volumes[index]
    return round(cumulativePriceVolume / cumulativeVolume)
  })

  const enrichedCandles = candles.map((candle, index) => ({
    ...candle,
    ema9: ema9[index],
    ema21: ema21[index],
    rsi14: rsi14[index],
    macd: macd[index],
    macdSignal: macdSignal[index],
    macdHistogram: macdHistogram[index],
    averageVolume:
      index < 19
        ? null
        : round(volumes.slice(index - 19, index + 1).reduce((sum, value) => sum + value, 0) / 20, 0),
    typicalPrice: round((highs[index] + lows[index] + closes[index]) / 3),
  }))

  const latestBarAt = enrichedCandles.at(-1)?.datetime || null

  return {
    ticker,
    interval,
    period,
    extended_hours: true,
    point_count: enrichedCandles.length,
    candles: enrichedCandles,
    overlays: {
      ema_9: ema9,
      ema_21: ema21,
      vwap,
    },
    available_indicators: ['ema_9', 'ema_21', 'vwap', 'rsi_14', 'macd', 'macd_signal', 'macd_hist'],
    freshness: {
      ticker,
      interval,
      status: 'fresh',
      warning: false,
      stale: false,
      feed_expected: true,
      session: 'regular',
      session_label: 'Regular',
      latest_bar_at: latestBarAt,
      latest_bar_age_seconds: 12,
      latest_bar_age_minutes: 0.2,
      warning_threshold_seconds: 90,
      stale_threshold_seconds: 240,
      point_count: enrichedCandles.length,
      source: 'chart-demo',
      checked_at: latestBarAt,
      checked_at_et: latestBarAt,
      message: 'Synthetic browser-regression fixture.',
    },
  }
}
