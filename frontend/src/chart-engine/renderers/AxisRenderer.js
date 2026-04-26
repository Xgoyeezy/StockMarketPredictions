const UI_FONT_STACK = "'Segoe UI Variable', 'Aptos', 'Segoe UI', system-ui, sans-serif"

export function drawAxes(ctx, layout, timeScale, paneScales, timeTicks, paneTicks) {
  ctx.save()
  ctx.fillStyle = '#0c111b'

  for (const axis of Object.values(layout.axes || {})) {
    ctx.fillRect(axis.left, axis.top, axis.width, axis.height)
  }
  ctx.fillRect(layout.timeAxis.left, layout.timeAxis.top, layout.timeAxis.width, layout.timeAxis.height)

  ctx.strokeStyle = 'rgba(92, 92, 92, 0.14)'
  ctx.lineWidth = 1

  for (const [paneKey, pane] of Object.entries(layout.panes || {})) {
    const axis = layout.axes?.[paneKey]
    if (!pane || !axis) continue
    ctx.beginPath()
    ctx.moveTo(pane.left + pane.width + 0.5, pane.top)
    ctx.lineTo(pane.left + pane.width + 0.5, pane.top + pane.height)
    ctx.stroke()
  }

  const lastPaneKey = layout.paneOrder?.[layout.paneOrder.length - 1] || 'price'
  const lastPane = layout.panes[lastPaneKey] || layout.panes.price
  ctx.beginPath()
  ctx.moveTo(lastPane.left, lastPane.top + lastPane.height + 0.5)
  ctx.lineTo(lastPane.left + lastPane.width, lastPane.top + lastPane.height + 0.5)
  ctx.stroke()

  for (let index = 1; index < (layout.paneOrder?.length || 0); index += 1) {
    const paneKey = layout.paneOrder[index]
    const pane = layout.panes[paneKey]
    if (!pane) continue
    ctx.beginPath()
    ctx.moveTo(layout.panes.price.left, pane.top - layout.paneGap / 2 + 0.5)
    ctx.lineTo(layout.panes.price.left + layout.panes.price.width, pane.top - layout.paneGap / 2 + 0.5)
    ctx.stroke()
  }

  ctx.fillStyle = '#8a8a8a'
  ctx.font = `11px ${UI_FONT_STACK}`
  ctx.textBaseline = 'middle'
  ctx.textAlign = 'left'

  for (const [paneKey, ticks] of Object.entries(paneTicks || {})) {
    const axis = layout.axes?.[paneKey]
    const scale = paneScales?.[paneKey]
    if (!axis || !scale) continue
    for (const tick of ticks || []) {
      const y = scale.priceToY(tick.value)
      ctx.fillText(tick.label, axis.left + 10, y)
    }
  }

  ctx.textBaseline = 'top'
  ctx.textAlign = 'center'
  for (const tick of timeTicks || []) {
    const x = timeScale.indexToX(tick.index)
    ctx.fillText(
      tick.label,
      x,
      layout.timeAxis.top + 8,
      Math.max(timeScale.barSpacing * 6, 64),
    )
  }

  ctx.restore()
}
