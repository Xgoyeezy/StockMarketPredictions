import { buildChartLayout } from './Panes.js'
import { buildTimeScale } from './TimeScale.js'
import { buildPriceScale } from './PriceScale.js'
import {
  buildNicePriceTicks,
  formatCompactValue,
  formatTimeAxisLabel,
  getSessionType,
  targetPriceTickCount,
  targetTimeTickCount,
} from './math.js'
import { drawGrid } from '../renderers/GridRenderer.js'
import { drawAxes } from '../renderers/AxisRenderer.js'
import { drawCandles } from '../renderers/CandleRenderer.js'
import { drawLineSeries } from '../renderers/LineRenderer.js'
import { drawCrosshair } from '../renderers/CrosshairRenderer.js'
import { drawHistogram } from '../renderers/HistogramRenderer.js'

const UI_FONT_STACK = "'Segoe UI Variable', 'Aptos', 'Segoe UI', system-ui, sans-serif"

function getVisibleIndexBounds(rows, viewport) {
  return {
    startIndex: Math.max(Math.floor(viewport.startIndex), 0),
    endIndex: Math.min(Math.ceil(viewport.endIndex), rows.length - 1),
  }
}

function buildTimeTicks(rows, timeScale, viewport, interval) {
  const { startIndex, endIndex } = getVisibleIndexBounds(rows, viewport)
  const visibleCount = Math.max(endIndex - startIndex + 1, 1)
  const desiredCount = targetTimeTickCount(timeScale.width)
  const step = Math.max(Math.floor(visibleCount / desiredCount), 1)
  const ticks = []
  let previousLabel = null
  let previousX = Number.NEGATIVE_INFINITY
  const minimumPixelGap = Math.max(timeScale.width / Math.max(desiredCount, 1), 72)

  for (let index = startIndex; index <= endIndex; index += step) {
    const row = rows[index]
    if (!row) continue
    const label = formatTimeAxisLabel(row.rawTime, interval)
    if (!label || label === previousLabel) continue
    const x = timeScale.indexToX(index)
    const estimatedWidth = Math.max(label.length * 7, 44)
    if (x - previousX < Math.max(minimumPixelGap, estimatedWidth + 12)) continue
    ticks.push({
      index,
      label,
    })
    previousLabel = label
    previousX = x
  }

  const lastRow = rows[endIndex]
  if (lastRow) {
    const lastLabel = formatTimeAxisLabel(lastRow.rawTime, interval)
    const lastIndex = endIndex
    const lastX = timeScale.indexToX(lastIndex)
    const existingLastTick = ticks.at(-1)
    if (
      lastLabel &&
      (!existingLastTick ||
        existingLastTick.index !== lastIndex &&
        lastX - timeScale.indexToX(existingLastTick.index) >= 56)
    ) {
      ticks.push({
        index: lastIndex,
        label: lastLabel,
      })
    }
  }

  return ticks
}

function buildSessionSegments(rows, viewport) {
  const { startIndex, endIndex } = getVisibleIndexBounds(rows, viewport)
  const segments = []
  let current = null

  for (let index = startIndex; index <= endIndex; index += 1) {
    const row = rows[index]
    if (!row) continue
    const type = getSessionType(row.rawTime)
    if (!current || current.type !== type) {
      current = { type, startIndex: index, endIndex: index }
      segments.push(current)
    } else {
      current.endIndex = index
    }
  }

  return segments
}

function findVisibleExtremes(rows, viewport) {
  const { startIndex, endIndex } = getVisibleIndexBounds(rows, viewport)
  let high = null
  let low = null

  for (let index = startIndex; index <= endIndex; index += 1) {
    const row = rows[index]
    if (!row) continue
    if (!high || row.high > high.value) {
      high = { index, value: row.high }
    }
    if (!low || row.low < low.value) {
      low = { index, value: row.low }
    }
  }

  return { high, low }
}

function findIndexByTimestamp(rows, timestamp) {
  if (!timestamp || !rows.length) return null
  const target = new Date(timestamp).getTime()
  if (!Number.isFinite(target)) return null

  let bestIndex = null
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

function buildVolumePaneState(rows, viewport, volumeAverageSeries = []) {
  const { startIndex, endIndex } = getVisibleIndexBounds(rows, viewport)
  let maxValue = 0

  for (let index = startIndex; index <= endIndex; index += 1) {
    const row = rows[index]
    if (!row) continue
    maxValue = Math.max(maxValue, row.volume || 0, volumeAverageSeries[index] || 0)
  }

  const paddedMax = Math.max(maxValue * 1.12, 1)
  return {
    minValue: 0,
    maxValue: paddedMax,
    lastVolume: rows.at(-1)?.volume ?? null,
    lastAverage: volumeAverageSeries.at(-1) ?? null,
  }
}

function buildRsiPaneState(rsiSeries = []) {
  const lastRsi = rsiSeries.at(-1) ?? null
  return {
    minValue: 0,
    maxValue: 100,
    lastRsi,
  }
}

function buildMacdPaneState(viewport, macdSeries = [], macdSignalSeries = [], macdHistogramSeries = []) {
  const { startIndex, endIndex } = getVisibleIndexBounds(macdSeries, viewport)
  let minValue = Infinity
  let maxValue = -Infinity

  for (let index = startIndex; index <= endIndex; index += 1) {
    const values = [macdSeries[index], macdSignalSeries[index], macdHistogramSeries[index]]
    for (const value of values) {
      if (!Number.isFinite(value)) continue
      minValue = Math.min(minValue, value)
      maxValue = Math.max(maxValue, value)
    }
  }

  if (!Number.isFinite(minValue) || !Number.isFinite(maxValue)) {
    minValue = -1
    maxValue = 1
  }

  minValue = Math.min(minValue, 0)
  maxValue = Math.max(maxValue, 0)
  const span = Math.max(maxValue - minValue, 1e-6)
  const padding = span * 0.16

  return {
    minValue: minValue - padding,
    maxValue: maxValue + padding,
    lastMacd: macdSeries.at(-1) ?? null,
    lastSignal: macdSignalSeries.at(-1) ?? null,
    lastHistogram: macdHistogramSeries.at(-1) ?? null,
  }
}

function measureTextCached(cache, ctx, text, font) {
  const key = `${font}::${text}`
  if (cache.has(key)) {
    return cache.get(key)
  }

  const previousFont = ctx.font
  if (ctx.font !== font) {
    ctx.font = font
  }
  const width = ctx.measureText(text).width
  if (ctx.font !== previousFont) {
    ctx.font = previousFont
  }
  cache.set(key, width)
  return width
}

function drawFloatingLabel(ctx, x, y, text, color, align = 'left', bounds = null, metricsCache = null) {
  ctx.save()
    const font = `11px ${UI_FONT_STACK}`
  ctx.font = font
  const textWidth = metricsCache
    ? measureTextCached(metricsCache, ctx, text, font)
    : ctx.measureText(text).width
  const paddingX = 8
  const width = textWidth + paddingX * 2
  const height = 22
  let left = align === 'right' ? x - width : x
  let top = y - height / 2

  if (bounds) {
    left = Math.min(Math.max(left, bounds.left), bounds.right - width)
    top = Math.min(Math.max(top, bounds.top), bounds.bottom - height)
  }

  ctx.fillStyle = color
  ctx.beginPath()
  ctx.roundRect(left, top, width, height, 7)
  ctx.fill()

  ctx.fillStyle = '#f4fbff'
  ctx.textBaseline = 'middle'
  ctx.textAlign = 'left'
  ctx.fillText(text, left + paddingX, y)
  ctx.restore()
}

function drawPaneLabel(ctx, pane, text, tone = 'rgba(18, 18, 18, 0.94)', color = '#e2e2e2', metricsCache = null) {
  ctx.save()
    const font = `10px ${UI_FONT_STACK}`
  ctx.font = font
  ctx.textBaseline = 'middle'
  ctx.textAlign = 'left'
  const width =
    (metricsCache ? measureTextCached(metricsCache, ctx, text, font) : ctx.measureText(text).width) + 16
  const height = 20
  const left = pane.left + 10
  const top = pane.top + 10
  ctx.fillStyle = tone
  ctx.beginPath()
  ctx.roundRect(left, top, width, height, 7)
  ctx.fill()
  ctx.fillStyle = color
  ctx.fillText(text, left + 8, top + height / 2)
  ctx.restore()
}

function drawPaneGuideLines(ctx, pane, scale, levels, options = {}) {
  const {
    strokeStyle = 'rgba(92, 92, 92, 0.16)',
    lineDash = [4, 4],
    labelColor = '#8a8a8a',
  } = options

  ctx.save()
  ctx.strokeStyle = strokeStyle
  ctx.lineWidth = 1
  ctx.setLineDash(lineDash)
  ctx.fillStyle = labelColor
    ctx.font = `10px ${UI_FONT_STACK}`
  ctx.textBaseline = 'bottom'
  ctx.textAlign = 'left'

  for (const level of levels) {
    const y = Math.round(scale.priceToY(level)) + 0.5
    ctx.beginPath()
    ctx.moveTo(pane.left, y)
    ctx.lineTo(pane.left + pane.width, y)
    ctx.stroke()
    ctx.fillText(String(level), pane.left + 8, y - 4)
  }

  ctx.setLineDash([])
  ctx.restore()
}

function resolveLabelY(rawY, axis, placed = [], gap = 24) {
  let y = rawY
  for (const usedY of placed) {
    if (Math.abs(y - usedY) < gap) {
      y = usedY + gap
    }
  }
  y = Math.min(Math.max(y, axis.top + 12), axis.top + axis.height - 12)
  placed.push(y)
  return y
}

function drawReferenceLine(ctx, pane, axis, scale, value, color, label, options = {}) {
  const {
    showLine = true,
    showLabel = true,
    lineDash = [4, 4],
    lineWidth = 1,
    labelY = null,
    occupiedYs = null,
    metricsCache = null,
  } = options
  if (!Number.isFinite(value) || !pane || !axis || !scale) return

  const rawY = Math.round(scale.priceToY(value)) + 0.5
  ctx.save()
  if (showLine) {
    ctx.strokeStyle = color
    ctx.lineWidth = lineWidth
    ctx.setLineDash(lineDash)
    ctx.beginPath()
    ctx.moveTo(pane.left, rawY)
    ctx.lineTo(pane.left + pane.width, rawY)
    ctx.stroke()
    ctx.setLineDash([])
  }
  if (showLabel) {
    const nextLabelY =
      labelY ?? (occupiedYs ? resolveLabelY(rawY, axis, occupiedYs) : rawY)
    drawFloatingLabel(
      ctx,
      axis.left + 6,
      nextLabelY,
      label,
      color,
      'left',
      {
        left: axis.left + 4,
        right: axis.left + axis.width - 4,
        top: axis.top + 4,
        bottom: axis.top + axis.height - 4,
      },
      metricsCache,
    )
  }
  ctx.restore()
  return rawY
}

function drawLatestBarHighlight(ctx, rows, viewport, timeScale, layout) {
  if (!rows.length) return
  const latestIndex = rows.length - 1
  if (latestIndex < viewport.startIndex || latestIndex > viewport.endIndex) return

  const x = timeScale.indexToX(latestIndex)
  const width = Math.max(timeScale.barSpacing * 0.9, 8)
  ctx.save()
  ctx.fillStyle = 'rgba(96, 96, 96, 0.08)'

  for (const pane of Object.values(layout.panes || {})) {
    ctx.fillRect(
      x - width / 2,
      pane.top,
      width,
      pane.height,
    )
  }

  ctx.restore()
}

function drawSessionBackgrounds(ctx, layout, timeScale, sessionSegments, metricsCache = null) {
  const fills = {
    premarket: 'rgba(96, 96, 96, 0.06)',
    regular: 'rgba(255, 255, 255, 0.02)',
    afterhours: 'rgba(155, 107, 255, 0.06)',
  }
  const labels = {
    premarket: 'PRE',
    regular: 'REG',
    afterhours: 'AH',
  }

  ctx.save()

  for (const segment of sessionSegments) {
    if (!fills[segment.type]) continue
    const startX = timeScale.indexToX(segment.startIndex) - timeScale.barSpacing / 2
    const endX = timeScale.indexToX(segment.endIndex) + timeScale.barSpacing / 2
    const width = Math.max(endX - startX, 1)

    for (const pane of Object.values(layout.panes || {})) {
      ctx.fillStyle = fills[segment.type]
      ctx.fillRect(startX, pane.top, width, pane.height)

      ctx.strokeStyle = 'rgba(92, 92, 92, 0.14)'
      ctx.lineWidth = 1
      ctx.setLineDash([3, 5])
      ctx.beginPath()
      ctx.moveTo(Math.round(startX) + 0.5, pane.top)
      ctx.lineTo(Math.round(startX) + 0.5, pane.top + pane.height)
      ctx.stroke()
      ctx.setLineDash([])
    }

    if (width >= 72) {
      drawFloatingLabel(
        ctx,
        startX + 8,
        layout.panes.price.top + 16,
        labels[segment.type],
        segment.type === 'regular' ? '#4a4a4a' : '#363636',
        'left',
        {
          left: layout.panes.price.left + 4,
          right: layout.panes.price.left + layout.panes.price.width - 4,
          top: layout.panes.price.top + 4,
          bottom: layout.panes.price.top + layout.panes.price.height - 4,
        },
        metricsCache,
      )
    }
  }

  ctx.restore()
}

function drawExtremeMarker(ctx, pane, timeScale, priceScale, marker, label, color, align, metricsCache = null) {
  if (!marker || !pane) return
  const x = timeScale.indexToX(marker.index)
  const y = priceScale.priceToY(marker.value)

  ctx.save()
  ctx.strokeStyle = color
  ctx.fillStyle = color
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.moveTo(x, y)
  ctx.lineTo(align === 'left' ? x - 24 : x + 24, y)
  ctx.stroke()
  ctx.beginPath()
  ctx.arc(x, y, 3, 0, Math.PI * 2)
  ctx.fill()
  ctx.restore()

  drawFloatingLabel(
    ctx,
    align === 'left' ? x - 28 : x + 8,
    y,
    label,
    color,
    align === 'left' ? 'right' : 'left',
    {
      left: pane.left + 8,
      right: pane.left + pane.width - 8,
      top: pane.top + 6,
      bottom: pane.top + pane.height - 6,
    },
    metricsCache,
  )
}

function drawOverlayLastValueLabels(ctx, layout, priceScale, overlays = [], occupiedYs = [], metricsCache = null) {
  const axis = layout.axes.price
  if (!axis) return

  const placed = occupiedYs
  const sortedOverlays = [...overlays]
    .filter((overlay) => Number.isFinite(overlay?.lastValue))
    .sort((left, right) => right.lastValue - left.lastValue)

  for (const overlay of sortedOverlays) {
    const y = resolveLabelY(priceScale.priceToY(overlay.lastValue), axis, placed)

    drawFloatingLabel(
      ctx,
      axis.left + 6,
      y,
      `${overlay.label} ${overlay.lastValue.toFixed(2)}`,
      overlay.color,
      'left',
      {
        left: axis.left + 4,
        right: axis.left + axis.width - 4,
        top: axis.top + 4,
        bottom: axis.top + axis.height - 4,
      },
      metricsCache,
    )
  }
}

function drawPriceMarkers(ctx, layout, priceScale, markers = [], occupiedYs = [], metricsCache = null) {
  const pane = layout.panes.price
  const axis = layout.axes.price
  if (!pane || !axis || !markers.length) return

  const sortedMarkers = [...markers]
    .filter((marker) => Number.isFinite(marker?.price))
    .sort((left, right) => {
      const leftPriority = left.priority ?? 50
      const rightPriority = right.priority ?? 50
      if (leftPriority !== rightPriority) return leftPriority - rightPriority
      return (right.price ?? 0) - (left.price ?? 0)
    })

  for (const marker of sortedMarkers) {
    drawReferenceLine(ctx, pane, axis, priceScale, marker.price, marker.color, marker.label, {
      lineDash: marker.lineDash || [4, 4],
      lineWidth: marker.lineWidth || 1,
      occupiedYs,
      metricsCache,
    })
  }
}

function drawDrawingHandle(ctx, x, y, color) {
  ctx.save()
  ctx.fillStyle = '#f4fbff'
  ctx.strokeStyle = color
  ctx.lineWidth = 1.5
  ctx.beginPath()
  ctx.arc(x, y, 4, 0, Math.PI * 2)
  ctx.fill()
  ctx.stroke()
  ctx.restore()
}

function drawDrawingObjects(
  ctx,
  rows,
  layout,
  timeScale,
  priceScale,
  drawings = [],
  selectedDrawingId = null,
  metricsCache = null,
) {
  const pane = layout.panes.price
  if (!pane || !drawings.length) return

  const resolveRayEndpoint = (x0, y0, x1, y1) => {
    const deltaX = x1 - x0
    const deltaY = y1 - y0
    if (Math.abs(deltaX) <= 0.5) {
      return { x: x1, y: y1 }
    }
    const boundaryX = deltaX >= 0 ? pane.left + pane.width : pane.left
    const ratio = (boundaryX - x0) / deltaX
    return {
      x: boundaryX,
      y: y0 + deltaY * ratio,
    }
  }

  for (const drawing of drawings) {
    const color = drawing.color || '#9b6bff'
    const label = drawing.label || 'Drawing'
    const type = drawing.type
    const isSelected = Boolean(selectedDrawingId && drawing.id === selectedDrawingId)

    if (type === 'hline' && Number.isFinite(drawing.price)) {
      const y = Math.round(priceScale.priceToY(drawing.price)) + 0.5
      ctx.save()
      ctx.strokeStyle = color
      ctx.lineWidth = isSelected ? 1.9 : 1.15
      ctx.setLineDash(drawing.dash === 'dash' ? [7, 5] : [2, 4])
      ctx.beginPath()
      ctx.moveTo(pane.left, y)
      ctx.lineTo(pane.left + pane.width, y)
      ctx.stroke()
      ctx.setLineDash([])
      ctx.restore()
      if (isSelected) {
        drawDrawingHandle(ctx, pane.left + 26, y, color)
        drawDrawingHandle(ctx, pane.left + pane.width - 26, y, color)
      }
      drawFloatingLabel(
        ctx,
        pane.left + 10,
        y,
        label,
        color,
        'left',
        {
          left: pane.left + 8,
          right: pane.left + pane.width - 8,
          top: pane.top + 8,
          bottom: pane.top + pane.height - 8,
        },
        metricsCache,
      )
      continue
    }

    if (type === 'note' && drawing.x0 && Number.isFinite(drawing.y0)) {
      const index = findIndexByTimestamp(rows, drawing.x0)
      if (index === null) continue
      const x = timeScale.indexToX(index)
      const y = priceScale.priceToY(drawing.y0)
      ctx.save()
      ctx.fillStyle = color
      ctx.strokeStyle = 'rgba(8, 14, 24, 0.72)'
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.arc(x, y, 5, 0, Math.PI * 2)
      ctx.fill()
      ctx.stroke()
      ctx.restore()
      if (isSelected) {
        ctx.save()
        ctx.strokeStyle = color
        ctx.lineWidth = 1.5
        ctx.beginPath()
        ctx.arc(x, y, 9, 0, Math.PI * 2)
        ctx.stroke()
        ctx.restore()
      }
      drawFloatingLabel(
        ctx,
        x + 10,
        y - 12,
        label,
        color,
        'left',
        {
          left: pane.left + 8,
          right: pane.left + pane.width - 8,
          top: pane.top + 8,
          bottom: pane.top + pane.height - 8,
        },
        metricsCache,
      )
      continue
    }

    if (!drawing.x0 || !drawing.x1 || !Number.isFinite(drawing.y0) || !Number.isFinite(drawing.y1)) {
      continue
    }

    const x0Index = findIndexByTimestamp(rows, drawing.x0)
    const x1Index = findIndexByTimestamp(rows, drawing.x1)
    if (x0Index === null || x1Index === null) continue
    const x0 = timeScale.indexToX(x0Index)
    const x1 = timeScale.indexToX(x1Index)
    const y0 = priceScale.priceToY(drawing.y0)
    const y1 = priceScale.priceToY(drawing.y1)

    if (type === 'rectangle') {
      const left = Math.min(x0, x1)
      const top = Math.min(y0, y1)
      const width = Math.max(Math.abs(x1 - x0), 1)
      const height = Math.max(Math.abs(y1 - y0), 1)
      ctx.save()
      ctx.fillStyle = `${color}22`
      ctx.strokeStyle = color
      ctx.lineWidth = isSelected ? 2 : 1.3
      ctx.beginPath()
      ctx.rect(left, top, width, height)
      ctx.fill()
      ctx.stroke()
      ctx.restore()
      if (isSelected) {
        drawDrawingHandle(ctx, left, top, color)
        drawDrawingHandle(ctx, left + width, top, color)
        drawDrawingHandle(ctx, left, top + height, color)
        drawDrawingHandle(ctx, left + width, top + height, color)
      }
      drawFloatingLabel(
        ctx,
        left + 8,
        top + 12,
        label,
        color,
        'left',
        {
          left: pane.left + 8,
          right: pane.left + pane.width - 8,
          top: pane.top + 8,
          bottom: pane.top + pane.height - 8,
        },
        metricsCache,
      )
      continue
    }

    const dashed = type === 'measure'
    const isRay = type === 'ray'
    const lineEnd = isRay ? resolveRayEndpoint(x0, y0, x1, y1) : { x: x1, y: y1 }
    ctx.save()
    ctx.strokeStyle = color
    ctx.lineWidth = isSelected ? 2.2 : 1.5
    if (dashed) {
      ctx.setLineDash([4, 4])
    }
    ctx.beginPath()
    ctx.moveTo(x0, y0)
    ctx.lineTo(lineEnd.x, lineEnd.y)
    ctx.stroke()
    ctx.setLineDash([])
    ctx.fillStyle = color
    ctx.beginPath()
    ctx.arc(x0, y0, 3, 0, Math.PI * 2)
    ctx.arc(x1, y1, 3, 0, Math.PI * 2)
    ctx.fill()
    ctx.restore()
    if (isSelected) {
      drawDrawingHandle(ctx, x0, y0, color)
      drawDrawingHandle(ctx, x1, y1, color)
    }

    let resolvedLabel = label
    if (type === 'measure') {
      const delta = drawing.y1 - drawing.y0
      const percent = drawing.y0 ? (delta / drawing.y0) * 100 : null
      resolvedLabel = Number.isFinite(percent)
        ? `Measure ${delta >= 0 ? '+' : ''}${delta.toFixed(2)} (${percent.toFixed(2)}%)`
        : `Measure ${delta >= 0 ? '+' : ''}${delta.toFixed(2)}`
    }
    if (type === 'ray') {
      resolvedLabel = String(label || '').toLowerCase().includes('ray') ? label : `${label} ray`
    }

    drawFloatingLabel(
      ctx,
      Math.min(x0, lineEnd.x) + Math.abs(lineEnd.x - x0) / 2 - 36,
      Math.min(y0, lineEnd.y) + Math.abs(lineEnd.y - y0) / 2,
      resolvedLabel,
      color,
      'left',
      {
        left: pane.left + 8,
        right: pane.left + pane.width - 8,
        top: pane.top + 8,
        bottom: pane.top + pane.height - 8,
      },
      metricsCache,
    )
  }
}

function buildScene(model, width, height) {
  const {
    rows,
    viewport,
    interval,
    showVolumePane = true,
    showRsiPane = true,
    showMacdPane = true,
    paneRatios = null,
    volumeAverageSeries = [],
    rsiSeries = [],
    macdSeries = [],
    macdSignalSeries = [],
    macdHistogramSeries = [],
  } = model

  const layout = buildChartLayout(width, height, {
    showVolumePane,
    showRsiPane,
    showMacdPane,
    paneRatios,
  })
  const timeScale = buildTimeScale({
    left: layout.panes.price.left,
    width: layout.panes.price.width,
    startIndex: viewport.startIndex,
    endIndex: viewport.endIndex,
  })
  const priceScale = buildPriceScale({
    top: layout.panes.price.top,
    height: layout.panes.price.height,
    minPrice: viewport.minPrice,
    maxPrice: viewport.maxPrice,
  })
  const paneScales = {
    price: priceScale,
  }
  const paneTicks = {
    price: buildNicePriceTicks(
      viewport.minPrice,
      viewport.maxPrice,
      targetPriceTickCount(layout.panes.price.height),
    ),
  }

  const volumePaneState =
    showVolumePane && layout.panes.volume
      ? buildVolumePaneState(rows, viewport, volumeAverageSeries)
      : null
  const rsiPaneState =
    showRsiPane && layout.panes.rsi
      ? buildRsiPaneState(rsiSeries)
      : null
  const macdPaneState =
    showMacdPane && layout.panes.macd
      ? buildMacdPaneState(viewport, macdSeries, macdSignalSeries, macdHistogramSeries)
      : null

  if (volumePaneState && layout.panes.volume) {
    paneScales.volume = buildPriceScale({
      top: layout.panes.volume.top,
      height: layout.panes.volume.height,
      minPrice: volumePaneState.minValue,
      maxPrice: volumePaneState.maxValue,
    })
    paneTicks.volume = buildNicePriceTicks(
      volumePaneState.minValue,
      volumePaneState.maxValue,
      targetPriceTickCount(layout.panes.volume.height),
      formatCompactValue,
    )
  }

  if (rsiPaneState && layout.panes.rsi) {
    paneScales.rsi = buildPriceScale({
      top: layout.panes.rsi.top,
      height: layout.panes.rsi.height,
      minPrice: rsiPaneState.minValue,
      maxPrice: rsiPaneState.maxValue,
    })
    paneTicks.rsi = buildNicePriceTicks(
      rsiPaneState.minValue,
      rsiPaneState.maxValue,
      5,
    )
  }

  if (macdPaneState && layout.panes.macd) {
    paneScales.macd = buildPriceScale({
      top: layout.panes.macd.top,
      height: layout.panes.macd.height,
      minPrice: macdPaneState.minValue,
      maxPrice: macdPaneState.maxValue,
    })
    paneTicks.macd = buildNicePriceTicks(
      macdPaneState.minValue,
      macdPaneState.maxValue,
      targetPriceTickCount(layout.panes.macd.height),
    )
  }

  return {
    layout,
    timeScale,
    paneScales,
    paneTicks,
    priceScale,
    timeTicks: buildTimeTicks(rows, timeScale, viewport, interval),
    extremes: findVisibleExtremes(rows, viewport),
    sessionSegments: buildSessionSegments(rows, viewport),
    volumePaneState,
    rsiPaneState,
    macdPaneState,
  }
}

export class ChartEngine {
  constructor(canvas = null) {
    this.layers = {
      background: null,
      series: null,
      overlay: null,
    }
    this.contexts = {
      background: null,
      series: null,
      overlay: null,
    }
    this.width = 0
    this.height = 0
    this.pixelRatio = 1
    this.scene = null
    this.textMetricsCache = new Map()
    if (canvas) this.attachCanvas(canvas)
  }

  attachCanvas(canvas) {
    this.attachLayers({ seriesCanvas: canvas })
  }

  attachLayers({ backgroundCanvas = null, seriesCanvas = null, overlayCanvas = null } = {}) {
    this.layers.background = backgroundCanvas
    this.layers.series = seriesCanvas
    this.layers.overlay = overlayCanvas

    this.contexts.background = backgroundCanvas ? backgroundCanvas.getContext('2d') : null
    this.contexts.series = seriesCanvas ? seriesCanvas.getContext('2d') : null
    this.contexts.overlay = overlayCanvas ? overlayCanvas.getContext('2d') : null
  }

  resize(width, height, pixelRatio = window.devicePixelRatio || 1) {
    this.width = width
    this.height = height
    this.pixelRatio = pixelRatio
    this.textMetricsCache.clear()

    for (const [key, canvas] of Object.entries(this.layers)) {
      const ctx = this.contexts[key]
      if (!canvas || !ctx) continue
      canvas.width = Math.round(width * pixelRatio)
      canvas.height = Math.round(height * pixelRatio)
      canvas.style.width = `${width}px`
      canvas.style.height = `${height}px`
      ctx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0)
    }
  }

  clearLayer(layerKey) {
    const ctx = this.contexts[layerKey]
    if (!ctx) return
    ctx.clearRect(0, 0, this.width, this.height)
  }

  renderOverlay(crosshair = null) {
    if (!this.scene) return null
    const overlayContext = this.contexts.overlay || this.contexts.series
    if (!overlayContext) return this.scene

    if (this.contexts.overlay) {
      this.clearLayer('overlay')
    } else {
      this.render(this.scene.model)
      return this.scene
    }

    drawCrosshair(overlayContext, this.scene.layout, crosshair, this.scene.timeScale)
    return this.scene
  }

  render(model) {
    const seriesContext = this.contexts.series
    if (!seriesContext) return null

    const {
      rows,
      viewport,
      chartStyle,
      tickerAccent,
      crosshair,
      livePrice,
      selectedPrice,
      drawings = [],
      overlays = [],
      priceMarkers = [],
      selectedDrawingId = null,
      volumeAverageSeries = [],
      rsiSeries = [],
      macdSeries = [],
      macdSignalSeries = [],
      macdHistogramSeries = [],
    } = model

    const backgroundContext = this.contexts.background || seriesContext
    const scene = buildScene(model, this.width, this.height)
    const {
      layout,
      timeScale,
      paneScales,
      paneTicks,
      priceScale,
      timeTicks,
      extremes,
      sessionSegments,
      volumePaneState,
      rsiPaneState,
      macdPaneState,
    } = scene

    this.clearLayer('background')
    this.clearLayer('series')
    if (this.contexts.overlay) {
      this.clearLayer('overlay')
    }

    backgroundContext.fillStyle = '#0c111b'
    backgroundContext.fillRect(0, 0, this.width, this.height)
    drawSessionBackgrounds(backgroundContext, layout, timeScale, sessionSegments, this.textMetricsCache)
    drawGrid(backgroundContext, layout, timeScale, paneScales, timeTicks, paneTicks)
    drawAxes(backgroundContext, layout, timeScale, paneScales, timeTicks, paneTicks)

    drawLatestBarHighlight(seriesContext, rows, viewport, timeScale, layout)

    if (chartStyle === 'line') {
      drawLineSeries(seriesContext, rows, timeScale, priceScale, viewport, tickerAccent)
    } else {
      drawCandles(seriesContext, rows, timeScale, priceScale, viewport)
    }

    for (const overlay of overlays) {
      drawLineSeries(seriesContext, rows, timeScale, priceScale, viewport, overlay.color, {
        valueAccessor: (_row, index) => overlay.series[index],
        points: overlay.futurePoints || null,
        lineWidth: overlay.lineWidth || (overlay.name === 'vwap' ? 1.9 : 1.55),
        lineDash: overlay.lineDash || [],
      })
    }

    drawDrawingObjects(
      seriesContext,
      rows,
      layout,
      timeScale,
      priceScale,
      drawings,
      selectedDrawingId,
      this.textMetricsCache,
    )

    if (paneScales.volume && layout.panes.volume) {
      drawHistogram(seriesContext, rows, timeScale, paneScales.volume, viewport)
      drawLineSeries(seriesContext, rows, timeScale, paneScales.volume, viewport, '#f4b942', {
        valueAccessor: (_row, index) => volumeAverageSeries[index],
        lineWidth: 1.6,
      })
    }

    if (paneScales.rsi && layout.panes.rsi) {
      drawPaneGuideLines(seriesContext, layout.panes.rsi, paneScales.rsi, [30, 50, 70], {
        strokeStyle: 'rgba(92, 92, 92, 0.14)',
      })
      drawLineSeries(seriesContext, rows, timeScale, paneScales.rsi, viewport, '#b388ff', {
        valueAccessor: (_row, index) => rsiSeries[index],
        lineWidth: 1.9,
      })
    }

    if (paneScales.macd && layout.panes.macd) {
      drawPaneGuideLines(seriesContext, layout.panes.macd, paneScales.macd, [0], {
        strokeStyle: 'rgba(92, 92, 92, 0.18)',
        lineDash: [],
      })
      drawHistogram(seriesContext, rows, timeScale, paneScales.macd, viewport, {
        valueAccessor: (_row, index) => macdHistogramSeries[index],
        colorAccessor: (_row, index) =>
          (macdHistogramSeries[index] || 0) >= 0
            ? 'rgba(36, 213, 161, 0.52)'
            : 'rgba(255, 107, 107, 0.52)',
        baseValue: 0,
      })
      drawLineSeries(seriesContext, rows, timeScale, paneScales.macd, viewport, '#7a7a7a', {
        valueAccessor: (_row, index) => macdSeries[index],
        lineWidth: 1.8,
      })
      drawLineSeries(seriesContext, rows, timeScale, paneScales.macd, viewport, '#f4b942', {
        valueAccessor: (_row, index) => macdSignalSeries[index],
        lineWidth: 1.6,
      })
    }

    const occupiedPriceAxisYs = []

    drawReferenceLine(
      seriesContext,
      layout.panes.price,
      layout.axes.price,
      priceScale,
      livePrice,
      '#3da5ff',
      Number.isFinite(livePrice) ? `Live ${livePrice.toFixed(2)}` : 'Live',
      {
        occupiedYs: occupiedPriceAxisYs,
        lineDash: [],
        lineWidth: 1.2,
        metricsCache: this.textMetricsCache,
      },
    )
    if (Number.isFinite(selectedPrice)) {
      drawReferenceLine(
        seriesContext,
        layout.panes.price,
        layout.axes.price,
        priceScale,
        selectedPrice,
        '#ffd43b',
        `Pick ${selectedPrice.toFixed(2)}`,
        {
          occupiedYs: occupiedPriceAxisYs,
          lineDash: [2, 4],
          metricsCache: this.textMetricsCache,
        },
      )
    }

    drawPriceMarkers(seriesContext, layout, priceScale, priceMarkers, occupiedPriceAxisYs, this.textMetricsCache)
    drawOverlayLastValueLabels(seriesContext, layout, priceScale, overlays, occupiedPriceAxisYs, this.textMetricsCache)

    if (paneScales.volume && layout.panes.volume && volumePaneState) {
      const volumeLabel = `Vol ${formatCompactValue(volumePaneState.lastVolume)} | Avg ${formatCompactValue(volumePaneState.lastAverage)}`
      const averageLabel = `Avg ${formatCompactValue(volumePaneState.lastAverage)}`
      drawPaneLabel(seriesContext, layout.panes.volume, volumeLabel, 'rgba(18, 18, 18, 0.94)', '#e2e2e2', this.textMetricsCache)
      drawReferenceLine(
        seriesContext,
        layout.panes.volume,
        layout.axes.volume,
        paneScales.volume,
        volumePaneState.lastVolume,
        'rgba(96, 96, 96, 0.92)',
        `Vol ${formatCompactValue(volumePaneState.lastVolume)}`,
        { showLine: false, metricsCache: this.textMetricsCache },
      )
      if (Number.isFinite(volumePaneState.lastAverage)) {
        drawReferenceLine(
          seriesContext,
          layout.panes.volume,
          layout.axes.volume,
          paneScales.volume,
          volumePaneState.lastAverage,
          'rgba(244, 185, 66, 0.92)',
          averageLabel,
          { showLine: false, metricsCache: this.textMetricsCache },
        )
      }
    }

    if (paneScales.rsi && layout.panes.rsi && rsiPaneState) {
      const rsiLabel = `RSI 14 ${Number.isFinite(rsiPaneState.lastRsi) ? rsiPaneState.lastRsi.toFixed(2) : '--'}`
      drawPaneLabel(seriesContext, layout.panes.rsi, rsiLabel, 'rgba(24, 24, 24, 0.94)', '#e3d8ff', this.textMetricsCache)
      if (Number.isFinite(rsiPaneState.lastRsi)) {
        drawReferenceLine(
          seriesContext,
          layout.panes.rsi,
          layout.axes.rsi,
          paneScales.rsi,
          rsiPaneState.lastRsi,
          'rgba(179, 136, 255, 0.94)',
          rsiLabel,
          { showLine: false, metricsCache: this.textMetricsCache },
        )
      }
    }

    if (paneScales.macd && layout.panes.macd && macdPaneState) {
      const macdLabel = `MACD ${Number.isFinite(macdPaneState.lastMacd) ? macdPaneState.lastMacd.toFixed(2) : '--'} | Sig ${Number.isFinite(macdPaneState.lastSignal) ? macdPaneState.lastSignal.toFixed(2) : '--'} | Hist ${Number.isFinite(macdPaneState.lastHistogram) ? macdPaneState.lastHistogram.toFixed(2) : '--'}`
      drawPaneLabel(seriesContext, layout.panes.macd, macdLabel, 'rgba(20, 20, 20, 0.94)', '#e0e0e0', this.textMetricsCache)

      if (Number.isFinite(macdPaneState.lastMacd)) {
        drawReferenceLine(
          seriesContext,
          layout.panes.macd,
          layout.axes.macd,
          paneScales.macd,
          macdPaneState.lastMacd,
          'rgba(96, 96, 96, 0.94)',
          `MACD ${macdPaneState.lastMacd.toFixed(2)}`,
          { showLine: false, metricsCache: this.textMetricsCache },
        )
      }
      if (Number.isFinite(macdPaneState.lastSignal)) {
        drawReferenceLine(
          seriesContext,
          layout.panes.macd,
          layout.axes.macd,
          paneScales.macd,
          macdPaneState.lastSignal,
          'rgba(244, 185, 66, 0.94)',
          `Sig ${macdPaneState.lastSignal.toFixed(2)}`,
          { showLine: false, metricsCache: this.textMetricsCache },
        )
      }
    }

    drawExtremeMarker(
      seriesContext,
      layout.panes.price,
      timeScale,
      priceScale,
      extremes.high,
      `High ${extremes.high?.value?.toFixed?.(2) ?? ''}`.trim(),
        '#22c55e',
      'right',
      this.textMetricsCache,
    )
    drawExtremeMarker(
      seriesContext,
      layout.panes.price,
      timeScale,
      priceScale,
      extremes.low,
      `Low ${extremes.low?.value?.toFixed?.(2) ?? ''}`.trim(),
      '#ff6b6b',
      'left',
      this.textMetricsCache,
    )

    this.scene = {
      ...scene,
      priceScale,
      volumeScale: paneScales.volume || null,
      rsiScale: paneScales.rsi || null,
      macdScale: paneScales.macd || null,
      model,
    }

    this.renderOverlay(crosshair)
    return this.scene
  }
}
