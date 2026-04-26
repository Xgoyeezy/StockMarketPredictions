export function drawCrosshair(ctx, layout, crosshair, timeScale) {
  if (!crosshair) return

  const activePane = crosshair.pane && layout.panes?.[crosshair.pane] ? layout.panes[crosshair.pane] : layout.panes.price
  const verticalTop = layout.panes.price.top
  const verticalBottom = layout.panes.volume
    ? layout.panes.volume.top + layout.panes.volume.height
    : layout.panes.price.top + layout.panes.price.height

  ctx.save()
  ctx.strokeStyle = 'rgba(96, 96, 96, 0.45)'
  ctx.lineWidth = 1
  ctx.setLineDash([4, 4])

  const x = Math.round(timeScale.indexToX(crosshair.index)) + 0.5
  const y = Math.round(crosshair.y) + 0.5

  ctx.beginPath()
  ctx.moveTo(x, verticalTop)
  ctx.lineTo(x, verticalBottom)
  ctx.moveTo(activePane.left, y)
  ctx.lineTo(activePane.left + activePane.width, y)
  ctx.stroke()

  ctx.setLineDash([])
  ctx.fillStyle = '#7a7a7a'
  ctx.beginPath()
  ctx.arc(x, y, 3.5, 0, Math.PI * 2)
  ctx.fill()
  ctx.restore()
}
