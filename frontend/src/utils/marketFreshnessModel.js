function normalizeStatus(value) {
  return String(value || '').trim().toLowerCase()
}

function normalizePhase(value) {
  return String(value || '').trim().toLowerCase()
}

function toNumber(value) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

function buildRegularSessionWaitMessage({ freshness, phase, sessionLabel }) {
  const ticker = String(freshness?.ticker || '').trim().toUpperCase() || 'this symbol'
  const interval = String(freshness?.interval || '').trim().toLowerCase() || '5m'
  const ageMinutes = toNumber(freshness?.latest_bar_age_minutes)
  const ageLead =
    ageMinutes !== null
      ? `Latest ${interval} bar for ${ticker} is ${ageMinutes} minutes old.`
      : `Latest ${interval} bar for ${ticker} is not available yet.`

  if (phase === 'premarket') {
    return `${ageLead} ${sessionLabel || 'Premarket planning'} is outside the desk's regular-session window, so the desk is waiting for core-session bars.`
  }
  if (phase === 'after_hours') {
    return `${ageLead} Regular-hours mode is active, so the desk is waiting for the next core session instead of treating after-hours gaps like a feed fault.`
  }
  if (phase === 'weekend') {
    return `${ageLead} The market is closed for the weekend, so the desk is waiting for the next regular session.`
  }
  return `${ageLead} The market is outside the desk's regular-session window, so the desk is waiting for the next regular session.`
}

export function buildSessionAwareFreshness({ freshness = null, sessionModel = null } = {}) {
  if (!freshness || typeof freshness !== 'object') return null

  const phase = normalizePhase(sessionModel?.phase)
  const status = normalizeStatus(freshness?.status)
  const regularHoursOnly = Boolean(sessionModel?.regularHoursOnly)
  const offSessionPhase = ['premarket', 'after_hours', 'overnight', 'weekend'].includes(phase)

  if (!regularHoursOnly || !offSessionPhase || !['stale', 'warning'].includes(status)) {
    return freshness
  }

  return {
    ...freshness,
    status: 'awaiting_regular_session',
    warning: false,
    stale: false,
    feed_expected: false,
    session_policy: 'regular_hours_only',
    message: buildRegularSessionWaitMessage({
      freshness,
      phase,
      sessionLabel: String(sessionModel?.label || freshness?.session_label || '').trim(),
    }),
  }
}

export function buildSessionAwareFreshnessAlert({ freshness = null } = {}) {
  if (!freshness || typeof freshness !== 'object') return null

  const status = normalizeStatus(freshness.status)
  const sessionMode = normalizePhase(freshness.session_mode || freshness.session)
  if (status === 'awaiting_regular_session') {
    return null
  }
  if (['fresh', 'idle'].includes(status) && sessionMode === 'pre_market') {
    return {
      ...freshness,
      title: 'Pre-market equity mode active',
      tone: 'warning',
      message:
        freshness.message ||
        'Pre-market equity routing is live with limit-only DAY_EXT orders, smaller size, and stricter liquidity checks.',
    }
  }
  if (['fresh', 'idle'].includes(status) && sessionMode === 'after_hours') {
    return {
      ...freshness,
      title: 'After-hours equity mode active',
      tone: 'warning',
      message:
        freshness.message ||
        'After-hours equity routing is live with conservative size, strict spread limits, and no aggressive averaging.',
    }
  }
  if (status === 'stale') {
    return {
      ...freshness,
      title: 'Market data lag detected',
      tone: 'negative',
    }
  }
  if (status === 'warning') {
    return {
      ...freshness,
      title: 'Market data needs attention',
      tone: 'warning',
    }
  }
  return null
}
