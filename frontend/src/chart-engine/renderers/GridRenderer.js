export function drawGrid(ctx, layout, timeScale, paneScales, timeTicks, paneTicks) {
  const verticalTop = layout.panes.price.top
  const lastPaneKey = layout.paneOrder?.[layout.paneOrder.length - 1] || 'price'
  const lastPane = layout.panes[lastPaneKey] || layout.panes.price
  const verticalBottom = lastPane.top + lastPane.height

  ctx.save()
  ctx.strokeStyle = 'rgba(92, 92, 92, 0.08)'
  ctx.lineWidth = 1

  for (const [paneKey, ticks] of Object.entries(paneTicks || {})) {
    const pane = layout.panes[paneKey]
    const scale = paneScales[paneKey]
    if (!pane || !scale) continue

    for (const tick of ticks || []) {
      const y = Math.round(scale.priceToY(tick.value)) + 0.5
      ctx.beginPath()
      ctx.moveTo(pane.left, y)
      ctx.lineTo(pane.left + pane.width, y)
      ctx.stroke()
    }
  }

  for (const tick of timeTicks || []) {
    const x = Math.round(timeScale.indexToX(tick.index)) + 0.5
    ctx.beginPath()
    ctx.moveTo(x, verticalTop)
    ctx.lineTo(x, verticalBottom)
    ctx.stroke()
  }

  ctx.restore()
}
