export function drawCandles(ctx, rows, timeScale, priceScale, viewport) {
  const startIndex = Math.max(Math.floor(viewport.startIndex) - 1, 0)
  const endIndex = Math.min(Math.ceil(viewport.endIndex) + 1, rows.length - 1)
  const bodyWidth = Math.max(3, Math.min(timeScale.barSpacing * 0.72, 18))

  ctx.save()
  for (let index = startIndex; index <= endIndex; index += 1) {
    const row = rows[index]
    if (!row) continue

    const x = timeScale.indexToX(index)
    const openY = priceScale.priceToY(row.open)
    const highY = priceScale.priceToY(row.high)
    const lowY = priceScale.priceToY(row.low)
    const closeY = priceScale.priceToY(row.close)
    const bullish = row.close >= row.open
  const color = bullish ? '#22c55e' : '#ff6b6b'
    const bodyTop = Math.min(openY, closeY)
    const bodyHeight = Math.max(Math.abs(closeY - openY), 1)

    ctx.strokeStyle = color
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(x, highY)
    ctx.lineTo(x, lowY)
    ctx.stroke()

    ctx.fillStyle = color
    ctx.fillRect(x - bodyWidth / 2, bodyTop, bodyWidth, bodyHeight)
  }
  ctx.restore()
}
