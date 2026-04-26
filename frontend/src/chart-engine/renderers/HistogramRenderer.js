export function drawHistogram(ctx, rows, timeScale, priceScale, viewport, options = {}) {
  const {
    valueAccessor = (row) => row.volume,
    colorAccessor = (row) => (row.close >= row.open ? 'rgba(36, 213, 161, 0.55)' : 'rgba(255, 107, 107, 0.55)'),
    baseValue = 0,
  } = options
  const startIndex = Math.max(Math.floor(viewport.startIndex) - 1, 0)
  const endIndex = Math.min(Math.ceil(viewport.endIndex) + 1, rows.length - 1)
  const barWidth = Math.max(3, Math.min(timeScale.barSpacing * 0.72, 18))
  const baseY = priceScale.priceToY(baseValue)

  ctx.save()
  for (let index = startIndex; index <= endIndex; index += 1) {
    const row = rows[index]
    if (!row) continue
    const value = valueAccessor(row, index)
    if (!Number.isFinite(value)) continue

    const x = timeScale.indexToX(index)
    const y = priceScale.priceToY(value)
    const top = Math.min(baseY, y)
    const height = Math.max(Math.abs(baseY - y), 1)

    ctx.fillStyle = colorAccessor(row, index)
    ctx.fillRect(x - barWidth / 2, top, barWidth, height)
  }
  ctx.restore()
}
