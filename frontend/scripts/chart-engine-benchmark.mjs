import { performance } from 'node:perf_hooks'
import {
  buildChartLayout,
  normalizeChartRows,
  resolveViewport,
  resetViewport,
  zoomViewport,
} from '../src/chart-engine/index.js'
import { buildChartPayload } from './chart-engine-fixture.mjs'

function summarize(samples) {
  const sorted = [...samples].sort((left, right) => left - right)
  const sum = sorted.reduce((total, value) => total + value, 0)
  const averageMs = sum / sorted.length
  const p95Index = Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))
  return {
    iterations: sorted.length,
    minMs: Number(sorted[0].toFixed(4)),
    avgMs: Number(averageMs.toFixed(4)),
    p95Ms: Number(sorted[p95Index].toFixed(4)),
    maxMs: Number(sorted.at(-1).toFixed(4)),
  }
}

function measure(iterations, callback) {
  const samples = []
  for (let index = 0; index < iterations; index += 1) {
    const start = performance.now()
    callback()
    samples.push(performance.now() - start)
  }
  return summarize(samples)
}

const payload = buildChartPayload({ count: 5000, intervalMinutes: 1 })
const rows = normalizeChartRows(payload)

for (let index = 0; index < 10; index += 1) {
  const viewport = resetViewport(rows, '1m')
  const restored = resolveViewport(
    rows,
    {
      xaxisLogicalRange: [420, 820],
      yaxisRange: [118, 136],
    },
    '1m',
  )
  zoomViewport(restored, rows, 650, 0.84)
  buildChartLayout(1440, 900, {
    showVolumePane: true,
    showRsiPane: true,
    showMacdPane: true,
    paneRatios: {
      price: 0.58,
      volume: 0.16,
      rsi: 0.12,
      macd: 0.14,
    },
  })
  void viewport
}

const normalizeSummary = measure(50, () => {
  normalizeChartRows(payload)
})

const viewportSummary = measure(200, () => {
  const viewport = resetViewport(rows, '1m')
  const restored = resolveViewport(
    rows,
    {
      xaxisLogicalRange: [420, 820],
      yaxisRange: [118, 136],
    },
    '1m',
  )
  zoomViewport(restored, rows, 650, 0.84)
  void viewport
})

const layoutSummary = measure(500, () => {
  buildChartLayout(1440, 900, {
    showVolumePane: true,
    showRsiPane: true,
    showMacdPane: true,
    paneRatios: {
      price: 0.58,
      volume: 0.16,
      rsi: 0.12,
      macd: 0.14,
    },
  })
})

const report = {
  fixture: {
    candles: payload.candles.length,
    normalizedRows: rows.length,
    width: 1440,
    height: 900,
    interval: '1m',
  },
  metrics: {
    normalizeChartRows: normalizeSummary,
    viewportPipeline: viewportSummary,
    buildChartLayout: layoutSummary,
  },
}

console.log(JSON.stringify(report, null, 2))
