export function drawLineSeries(ctx, rows, timeScale, priceScale, viewport, color, options = {}) {
  const {
    valueAccessor = (row) => row.close,
    lineWidth = 2,
    lineDash = [],
    points = null,
  } = options
  let started = false

  ctx.save()
  ctx.strokeStyle = color
  ctx.lineWidth = lineWidth
  ctx.setLineDash(lineDash)
  ctx.beginPath()

  if (Array.isArray(points) && points.length) {
    for (const point of points) {
      const index = Number(point?.index)
      const value = Number(point?.value)
      if (!Number.isFinite(index) || !Number.isFinite(value)) continue
      if (index < viewport.startIndex - 1 || index > viewport.endIndex + 1) continue
      const x = timeScale.indexToX(index)
      const y = priceScale.priceToY(value)

      if (!started) {
        ctx.moveTo(x, y)
        started = true
      } else {
        ctx.lineTo(x, y)
      }
    }
  } else {
    const startIndex = Math.max(Math.floor(viewport.startIndex) - 1, 0)
    const endIndex = Math.min(Math.ceil(viewport.endIndex) + 1, rows.length - 1)

    for (let index = startIndex; index <= endIndex; index += 1) {
      const row = rows[index]
      if (!row) continue
      const value = valueAccessor(row, index)
      if (!Number.isFinite(value)) continue
      const x = timeScale.indexToX(index)
      const y = priceScale.priceToY(value)

      if (!started) {
        ctx.moveTo(x, y)
        started = true
      } else {
        ctx.lineTo(x, y)
      }
    }
  }

  if (started) ctx.stroke()
  ctx.setLineDash([])
  ctx.restore()
}
