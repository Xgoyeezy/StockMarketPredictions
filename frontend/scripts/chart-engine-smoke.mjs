import assert from 'node:assert/strict'
import {
  buildChartLayout,
  fitPriceRangeToViewport,
  hasRenderableChartRows,
  normalizeChartRows,
  resolveViewport,
  resetViewport,
  zoomViewport,
} from '../src/chart-engine/index.js'

function buildPayload(count = 240) {
  const candles = []
  const startTime = Date.UTC(2026, 3, 17, 13, 30, 0)

  for (let index = 0; index < count; index += 1) {
    const open = 100 + index * 0.15
    const close = open + (index % 3 === 0 ? 0.45 : -0.2)
    const high = Math.max(open, close) + 0.35
    const low = Math.min(open, close) - 0.3
    candles.push({
      datetime: new Date(startTime + index * 5 * 60 * 1000).toISOString(),
      open,
      high,
      low,
      close,
      volume: 50000 + index * 250,
    })
  }

  return { candles }
}

const payload = buildPayload()
assert.equal(hasRenderableChartRows(payload), true, 'chart payload should expose enough normalized rows to render')

const rows = normalizeChartRows(payload)
assert.equal(rows.length, 240, 'normalized chart rows should preserve valid candles')
assert.match(rows[0].rawTime, /T13:30:00.000Z$/, 'normalized rows should expose ISO timestamps')

const defaultViewport = resetViewport(rows, '5m')
assert.ok(defaultViewport.endIndex > defaultViewport.startIndex, 'default viewport should span visible bars')
assert.ok(defaultViewport.maxPrice > defaultViewport.minPrice, 'default viewport should include a valid price range')

const restoredViewport = resolveViewport(
  rows,
  {
    xaxisLogicalRange: [18, 96],
    yaxisRange: [98, 122],
  },
  '5m',
)
assert.equal(restoredViewport.startIndex, 18, 'restored logical range should honor saved start index')
assert.equal(restoredViewport.endIndex, 96, 'restored logical range should honor saved end index')
assert.equal(restoredViewport.minPrice, 98, 'restored viewport should honor saved minimum price')
assert.equal(restoredViewport.maxPrice, 122, 'restored viewport should honor saved maximum price')

const zoomedViewport = zoomViewport(defaultViewport, rows, 120, 0.8)
assert.ok(
  zoomedViewport.endIndex - zoomedViewport.startIndex < defaultViewport.endIndex - defaultViewport.startIndex,
  'zooming in should reduce the visible span',
)

const fittedViewport = fitPriceRangeToViewport(rows, {
  ...defaultViewport,
  startIndex: 30,
  endIndex: 80,
  minPrice: 0,
  maxPrice: 1,
})
assert.ok(fittedViewport.maxPrice > fittedViewport.minPrice, 'fitting price range should restore a valid y-range')
assert.notEqual(fittedViewport.minPrice, 0, 'fitting price range should replace placeholder y-range values')

const layout = buildChartLayout(1280, 760, {
  showVolumePane: true,
  showRsiPane: true,
  showMacdPane: true,
  paneRatios: {
    price: 0.56,
    volume: 0.16,
    rsi: 0.14,
    macd: 0.14,
  },
})
assert.ok(layout.panes.price.height > layout.panes.macd.height, 'price pane should remain the dominant pane')
assert.ok(layout.axes.price.width > 0, 'price axis should exist in multi-pane layout')
assert.ok(layout.interactionArea.height > 0, 'layout should expose an interaction area for the stacked panes')

console.log('chart-engine smoke checks passed')
