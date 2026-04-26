export function normalizeTicker(value) {
  return String(value || '').trim().toUpperCase()
}

export function isTickerValid(value) {
  return /^[A-Z.\-]{1,8}$/.test(normalizeTicker(value))
}

export function parseTickerList(value) {
  return String(value || '')
    .split(',')
    .map((item) => normalizeTicker(item))
    .filter((item, index, array) => item && isTickerValid(item) && array.indexOf(item) === index)
}

export function validatePositiveNumber(value, fallback = null) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return fallback
  }
  return parsed
}
