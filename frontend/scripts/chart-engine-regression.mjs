import assert from 'node:assert/strict'
import {
  buildChartLayout,
  fitPriceRangeToViewport,
  hasRenderableChartRows,
  normalizeChartRows,
  resolveViewport,
  resetViewport,
  viewportToPersist,
  zoomViewport,
} from '../src/chart-engine/index.js'
import { buildChartPayload } from './chart-engine-fixture.mjs'

function round(value, digits = 4) {
  return Number(Number(value).toFixed(digits))
}

const payload = buildChartPayload({ count: 360, intervalMinutes: 5 })
assert.equal(hasRenderableChartRows(payload), true, 'fixture payload should be renderable')

const rows = normalizeChartRows(payload)
assert.equal(rows.length, 360, 'regression fixture should preserve the full candle count')

const initialViewport = resetViewport(rows, '5m')
const restoredViewport = resolveViewport(
  rows,
  {
    xaxisLogicalRange: [42, 126],
    yaxisRange: [101.25, 112.75],
  },
  '5m',
)
const zoomedViewport = zoomViewport(restoredViewport, rows, 92, 0.72)
const fittedViewport = fitPriceRangeToViewport(rows, {
  ...zoomedViewport,
  minPrice: 0,
  maxPrice: 1,
})
const persistedViewport = viewportToPersist(rows, fittedViewport)
const layout = buildChartLayout(1280, 760, {
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

const snapshot = {
  rows: {
    count: rows.length,
    firstTime: rows[0]?.rawTime,
    lastTime: rows.at(-1)?.rawTime,
    firstClose: round(rows[0]?.close),
    lastClose: round(rows.at(-1)?.close),
    volumeSumFirst3: round(rows.slice(0, 3).reduce((sum, row) => sum + row.volume, 0), 0),
  },
  initialViewport: {
    startIndex: round(initialViewport.startIndex),
    endIndex: round(initialViewport.endIndex),
    minPrice: round(initialViewport.minPrice),
    maxPrice: round(initialViewport.maxPrice),
  },
  fittedViewport: {
    startIndex: round(fittedViewport.startIndex),
    endIndex: round(fittedViewport.endIndex),
    minPrice: round(fittedViewport.minPrice),
    maxPrice: round(fittedViewport.maxPrice),
  },
  persistedViewport: {
    xaxisLogicalRange: persistedViewport?.xaxisLogicalRange?.map((value) => round(value)) ?? null,
    xaxisRange: persistedViewport?.xaxisRange ?? null,
    yaxisRange: persistedViewport?.yaxisRange?.map((value) => round(value, 6)) ?? null,
  },
  layout: {
    paneOrder: layout.paneOrder,
    paneRatios: Object.fromEntries(
      Object.entries(layout.paneRatios).map(([key, value]) => [key, round(value, 6)]),
    ),
    priceHeight: layout.panes.price.height,
    volumeHeight: layout.panes.volume.height,
    rsiHeight: layout.panes.rsi.height,
    macdHeight: layout.panes.macd.height,
    interactionHeight: layout.interactionArea.height,
  },
}

const expectedSnapshot = {
  rows: {
    count: 360,
    firstTime: '2026-04-17T13:30:00.000Z',
    lastTime: '2026-04-18T19:25:00.000Z',
    firstClose: 100.42,
    lastClose: 129.8764,
    volumeSumFirst3: 136290,
  },
  initialViewport: {
    startIndex: 231,
    endIndex: 371,
    minPrice: 117.8462,
    maxPrice: 131.2368,
  },
  fittedViewport: {
    startIndex: 56,
    endIndex: 116.48,
    minPrice: 103.5332,
    maxPrice: 110.7188,
  },
  persistedViewport: {
    xaxisLogicalRange: [56, 116.48],
    xaxisRange: ['2026-04-17T18:10:00.000Z', '2026-04-17T23:10:00.000Z'],
    yaxisRange: [103.533248, 110.718752],
  },
  layout: {
    paneOrder: ['price', 'volume', 'rsi', 'macd'],
    paneRatios: {
      price: 0.579882,
      volume: 0.159763,
      rsi: 0.119822,
      macd: 0.140533,
    },
    priceHeight: 392,
    volumeHeight: 108,
    rsiHeight: 81,
    macdHeight: 95,
    interactionHeight: 706,
  },
}

assert.deepStrictEqual(snapshot, expectedSnapshot, 'chart regression snapshot should remain stable')

console.log('chart-engine regression checks passed')
