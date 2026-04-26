function toNumber(value) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

function formatNumber(value, digits = 1, suffix = '') {
  const numeric = toNumber(value)
  return numeric === null ? null : `${numeric.toFixed(digits)}${suffix}`
}

export function formatBucketLabel(bucket) {
  const normalized = String(bucket || '').trim()
  if (!normalized) return '--'
  return normalized.replaceAll('_', ' ').replace(/\b\w/g, (character) => character.toUpperCase())
}

export function buildSignalTelemetry(row = {}) {
  const alphaScore = toNumber(row.alpha_score)
  const executionScore = toNumber(row.execution_score)
  const portfolioScore = toNumber(row.portfolio_score)
  const edgeToCostRatio = toNumber(row.edge_to_cost_ratio)
  const portfolioRank = toNumber(row.portfolio_rank)
  const proxyCorrelationBucket = String(row.proxy_correlation_bucket || '').trim()
  const autoEntryEligible = Boolean(row.auto_entry_eligible)
  const rejectReason = String(row.reject_reason || '').trim() || null

  return {
    alphaScore,
    executionScore,
    portfolioScore,
    edgeToCostRatio,
    portfolioRank,
    proxyCorrelationBucket,
    autoEntryEligible,
    rejectReason,
    rankingSummary: [
      alphaScore === null ? null : `Alpha ${formatNumber(alphaScore)}`,
      executionScore === null ? null : `Exec ${formatNumber(executionScore)}`,
      portfolioScore === null ? null : `Port ${formatNumber(portfolioScore)}`,
    ].filter(Boolean),
    automationSummary: [
      edgeToCostRatio === null ? null : `Edge/cost ${formatNumber(edgeToCostRatio, 1, 'x')}`,
      portfolioRank === null ? null : `Rank #${Math.round(portfolioRank)}`,
      proxyCorrelationBucket ? formatBucketLabel(proxyCorrelationBucket) : null,
    ].filter(Boolean),
    eligibilityLabel: autoEntryEligible ? 'Auto entry eligible' : 'Auto entry blocked',
    rejectionSummary: rejectReason,
  }
}
