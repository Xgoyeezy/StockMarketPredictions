import { clamp } from './math.js'

const OUTER_PADDING = 12
const PRICE_AXIS_WIDTH = 76
const TIME_AXIS_HEIGHT = 30
const PANE_GAP = 10
const PANE_MIN_HEIGHT = {
  price: 140,
  volume: 76,
  rsi: 74,
  macd: 82,
}

function buildBaseLayout(width, height) {
  const plotWidth = Math.max(width - OUTER_PADDING * 2 - PRICE_AXIS_WIDTH, 80)
  const plotHeight = Math.max(height - OUTER_PADDING * 2 - TIME_AXIS_HEIGHT, 80)

  return {
    width,
    height,
    plotWidth,
    plotHeight,
    plotLeft: OUTER_PADDING,
    axisLeft: Math.max(width - OUTER_PADDING - PRICE_AXIS_WIDTH, OUTER_PADDING),
    timeAxisTop: Math.max(height - OUTER_PADDING - TIME_AXIS_HEIGHT, OUTER_PADDING),
  }
}

export function getVisiblePaneKeys(options = {}) {
  const {
    showVolumePane = true,
    showRsiPane = true,
    showMacdPane = true,
  } = options

  const keys = ['price']
  if (showVolumePane) keys.push('volume')
  if (showRsiPane) keys.push('rsi')
  if (showMacdPane) keys.push('macd')
  return keys
}

function buildDefaultHeights(availableHeight, options = {}) {
  const visiblePaneKeys = getVisiblePaneKeys(options)
  if (visiblePaneKeys.length === 1) {
    return { price: availableHeight }
  }

  const { showVolumePane = true, showRsiPane = true, showMacdPane = true } = options
  const heights = {}

  if (showVolumePane) {
    heights.volume = clamp(
      Math.round(availableHeight * (showRsiPane || showMacdPane ? 0.16 : 0.22)),
      PANE_MIN_HEIGHT.volume,
      118,
    )
  }
  if (showRsiPane) {
    heights.rsi = clamp(
      Math.round(availableHeight * (showMacdPane ? 0.16 : 0.18)),
      PANE_MIN_HEIGHT.rsi,
      112,
    )
  }
  if (showMacdPane) {
    heights.macd = clamp(
      Math.round(availableHeight * 0.18),
      PANE_MIN_HEIGHT.macd,
      122,
    )
  }

  const lowerHeight = visiblePaneKeys
    .filter((key) => key !== 'price')
    .reduce((sum, key) => sum + (heights[key] || 0), 0)
  heights.price = Math.max(availableHeight - lowerHeight, PANE_MIN_HEIGHT.price)
  return heights
}

function buildDefaultRatios(availableHeight, options = {}) {
  const heights = buildDefaultHeights(availableHeight, options)
  const ratios = {}
  for (const [paneKey, paneHeight] of Object.entries(heights)) {
    ratios[paneKey] = paneHeight / Math.max(availableHeight, 1)
  }
  return ratios
}

function normalizePaneRatios(availableHeight, visiblePaneKeys, paneRatios, options) {
  const fallbackRatios = buildDefaultRatios(availableHeight, options)
  const usableRatios = {}
  let total = 0

  for (const paneKey of visiblePaneKeys) {
    const value = Number(paneRatios?.[paneKey])
    const ratio = Number.isFinite(value) && value > 0 ? value : fallbackRatios[paneKey] || 0
    usableRatios[paneKey] = ratio
    total += ratio
  }

  if (!total) return fallbackRatios

  const normalized = {}
  for (const paneKey of visiblePaneKeys) {
    normalized[paneKey] = usableRatios[paneKey] / total
  }
  return normalized
}

function resolvePaneHeights(availableHeight, visiblePaneKeys, paneRatios, options) {
  const ratios = normalizePaneRatios(availableHeight, visiblePaneKeys, paneRatios, options)
  const heights = {}
  let running = 0

  for (let index = 0; index < visiblePaneKeys.length; index += 1) {
    const paneKey = visiblePaneKeys[index]
    const isLast = index === visiblePaneKeys.length - 1
    const nextHeight = isLast
      ? Math.max(availableHeight - running, 0)
      : Math.max(Math.round(ratios[paneKey] * availableHeight), 0)
    heights[paneKey] = nextHeight
    running += nextHeight
  }

  for (const paneKey of visiblePaneKeys) {
    heights[paneKey] = Math.max(heights[paneKey], PANE_MIN_HEIGHT[paneKey] || 60)
  }

  let totalHeight = visiblePaneKeys.reduce((sum, paneKey) => sum + heights[paneKey], 0)
  let overflow = totalHeight - availableHeight

  if (overflow > 0) {
    const reductionOrder = ['price', ...visiblePaneKeys.filter((paneKey) => paneKey !== 'price').reverse()]
    for (const paneKey of reductionOrder) {
      if (overflow <= 0) break
      const minHeight = PANE_MIN_HEIGHT[paneKey] || 60
      const slack = Math.max(heights[paneKey] - minHeight, 0)
      if (!slack) continue
      const reduction = Math.min(slack, overflow)
      heights[paneKey] -= reduction
      overflow -= reduction
    }
  } else if (overflow < 0) {
    heights.price += Math.abs(overflow)
  }

  const finalTotal = visiblePaneKeys.reduce((sum, paneKey) => sum + heights[paneKey], 0)
  if (finalTotal !== availableHeight) {
    heights.price = Math.max(heights.price + (availableHeight - finalTotal), PANE_MIN_HEIGHT.price)
  }

  return heights
}

function heightsToRatios(heights, availableHeight) {
  const ratios = {}
  for (const [paneKey, paneHeight] of Object.entries(heights)) {
    ratios[paneKey] = paneHeight / Math.max(availableHeight, 1)
  }
  return ratios
}

export function buildSinglePaneLayout(width, height) {
  const base = buildBaseLayout(width, height)
  const plot = {
    left: base.plotLeft,
    top: OUTER_PADDING,
    width: base.plotWidth,
    height: base.plotHeight,
  }

  return {
    width,
    height,
    plot,
    panes: {
      price: plot,
    },
    paneOrder: ['price'],
    paneRatios: { price: 1 },
    availablePlotHeight: base.plotHeight,
    priceAxis: {
      left: base.axisLeft,
      top: OUTER_PADDING,
      width: PRICE_AXIS_WIDTH,
      height: base.plotHeight,
    },
    axes: {
      price: {
        left: base.axisLeft,
        top: OUTER_PADDING,
        width: PRICE_AXIS_WIDTH,
        height: base.plotHeight,
      },
    },
    timeAxis: {
      left: base.plotLeft,
      top: base.timeAxisTop,
      width: base.plotWidth,
      height: TIME_AXIS_HEIGHT,
    },
    interactionArea: {
      left: base.plotLeft,
      top: OUTER_PADDING,
      width: base.plotWidth,
      height: base.plotHeight,
    },
    paneGap: 0,
  }
}

export function buildChartLayout(width, height, options = {}) {
  const visiblePaneKeys = getVisiblePaneKeys(options)
  if (visiblePaneKeys.length === 1) return buildSinglePaneLayout(width, height)

  const base = buildBaseLayout(width, height)
  const lowerPaneCount = visiblePaneKeys.length - 1
  const totalGapHeight = PANE_GAP * lowerPaneCount
  const availableHeight = Math.max(base.plotHeight - totalGapHeight, 180)
  const paneHeights = resolvePaneHeights(availableHeight, visiblePaneKeys, options.paneRatios, options)
  const paneRatios = heightsToRatios(paneHeights, availableHeight)

  const panes = {}
  const axes = {}
  let cursorTop = OUTER_PADDING

  for (const paneKey of visiblePaneKeys) {
    const pane = {
      left: base.plotLeft,
      top: cursorTop,
      width: base.plotWidth,
      height: paneHeights[paneKey],
    }
    panes[paneKey] = pane
    axes[paneKey] = {
      left: base.axisLeft,
      top: pane.top,
      width: PRICE_AXIS_WIDTH,
      height: pane.height,
    }
    cursorTop += pane.height + PANE_GAP
  }

  const pricePlot = panes.price

  return {
    width,
    height,
    plot: pricePlot,
    panes,
    paneOrder: visiblePaneKeys,
    paneRatios,
    availablePlotHeight: availableHeight,
    priceAxis: axes.price,
    volumeAxis: axes.volume || null,
    rsiAxis: axes.rsi || null,
    macdAxis: axes.macd || null,
    axes,
    timeAxis: {
      left: base.plotLeft,
      top: base.timeAxisTop,
      width: base.plotWidth,
      height: TIME_AXIS_HEIGHT,
    },
    interactionArea: {
      left: base.plotLeft,
      top: pricePlot.top,
      width: base.plotWidth,
      height: panes[visiblePaneKeys.at(-1)].top + panes[visiblePaneKeys.at(-1)].height - pricePlot.top,
    },
    paneGap: PANE_GAP,
  }
}
