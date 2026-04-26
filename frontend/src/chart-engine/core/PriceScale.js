import { clamp } from './math.js'

export function buildPriceScale({ top, height, minPrice, maxPrice }) {
  const span = Math.max(maxPrice - minPrice, 1e-6)

  return {
    top,
    height,
    minPrice,
    maxPrice,
    span,
    priceToY(price) {
      return top + ((maxPrice - price) / span) * height
    },
    yToPrice(y) {
      return maxPrice - ((y - top) / height) * span
    },
    clampY(y) {
      return clamp(y, top, top + height)
    },
  }
}
