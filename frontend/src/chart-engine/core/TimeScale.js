import { clamp } from './math.js'

export function buildTimeScale({ left, width, startIndex, endIndex }) {
  const span = Math.max(endIndex - startIndex, 1)
  const barSpacing = width / span

  return {
    left,
    width,
    startIndex,
    endIndex,
    span,
    barSpacing,
    indexToX(index) {
      return left + (index - startIndex + 0.5) * barSpacing
    },
    xToIndex(x) {
      return startIndex + (x - left) / barSpacing - 0.5
    },
    clampX(x) {
      return clamp(x, left, left + width)
    },
  }
}
