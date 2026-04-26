import { useEffect, useMemo, useRef, useState } from 'react'
import { getChart } from '../api/client'
import {
  buildNicePriceTicks,
  formatTimeAxisLabel,
  fitPriceRangeToViewport,
  fitTimeRangeToViewport,
  hasRenderableChartRows,
  normalizeChartRows,
  panViewport,
  resolveViewport,
  targetPriceTickCount,
  targetTimeTickCount,
  toNumber,
  viewportToPersist,
  zoomViewport,
} from '../chart-engine/index.js'
import Button from './Button'
import EmptyState from './EmptyState'
import { formatInlineMeta } from './InlineMeta'
import Kicker from './Kicker'
import SignalDot from './SignalDot'

const SVG_WIDTH = 1200
const MIN_HEIGHT = 520
const PLOT_PADDING = { top: 20, right: 196, bottom: 32, left: 18 }
const VOLUME_PANE_GAP = 10
const RIGHT_EDGE_LABEL_MIN_GAP = 6
const RIGHT_EDGE_LABEL_HEIGHT = 24
const PANE_EDGE_LABEL_MIN_GAP = 4
const PANE_EDGE_LABEL_HEIGHT = 22
const PANE_RATIO_DEFAULTS = {
  price: 0.58,
  volume: 0.16,
  rsi: 0.12,
  macd: 0.14,
}
const PANE_MIN_HEIGHTS = {
  price: 160,
  volume: 88,
  rsi: 88,
  macd: 96,
}
const OVERLAY_ACCENT_PALETTE = ['#22c55e', '#f4b942', '#7a7a7a', '#ff6b6b', '#b388ff', '#ffd43b', '#7a7a7a', '#ff8fab']
const NAMED_OVERLAY_PALETTE = {
  ema_9: '#22c55e',
  ema_21: '#f4b942',
  ema_50: '#7a7a7a',
  ema_200: '#b388ff',
  sma_20: '#7a7a7a',
  sma_50: '#ff8a65',
  sma_200: '#ff6b6b',
  vwap: '#ffd43b',
  idm_upper_band: '#16a34a',
  idm_lower_band: '#ff6b6b',
  idm_vwap: '#ffd43b',
  idm_trailing_stop: '#7a7a7a',
}
const NAMED_OVERLAY_LABELS = {
  ema_9: 'EMA 9',
  ema_21: 'EMA 21',
  ema_50: 'EMA 50',
  ema_200: 'EMA 200',
  sma_20: 'SMA 20',
  sma_50: 'SMA 50',
  sma_200: 'SMA 200',
  vwap: 'VWAP',
  idm_upper_band: 'Breakout upper',
  idm_lower_band: 'Breakout lower',
  idm_vwap: 'Session VWAP',
  idm_trailing_stop: 'Trail stop',
}
const COMPACT_OVERLAY_LABELS = {
  ema_9: 'E9',
  ema_21: 'E21',
  ema_50: 'E50',
  ema_200: 'E200',
  sma_20: 'S20',
  sma_50: 'S50',
  sma_200: 'S200',
  vwap: 'VWAP',
  idm_upper_band: 'Upper',
  idm_lower_band: 'Lower',
  idm_vwap: 'VWAP',
  idm_trailing_stop: 'Trail',
}
const LOWER_PANE_OVERLAYS = new Set(['rsi_14', 'macd', 'macd_signal', 'macd_hist', 'atr_14', 'volume_ratio'])
const RECOVERY_POINTS = {
  '1m': 900,
  '5m': 600,
  '15m': 360,
  '30m': 240,
  '1h': 200,
  '4h': 180,
  '1d': 365,
}
const EASTERN_SESSION_PARTS_FORMATTER = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  weekday: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})
const currencyFormatter = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 2,
})

function formatPrice(value) {
  const numeric = toNumber(value)
  return numeric === null ? '--' : currencyFormatter.format(numeric)
}

function formatSignedNumber(value, digits = 2) {
  const numeric = toNumber(value)
  if (numeric === null) return '--'
  const sign = numeric > 0 ? '+' : ''
  return `${sign}${numeric.toFixed(digits)}`
}

function formatSignedPercent(value, digits = 2) {
  const numeric = toNumber(value)
  if (numeric === null) return '--'
  const sign = numeric > 0 ? '+' : ''
  return `${sign}${numeric.toFixed(digits)}%`
}

function formatCompactNumber(value) {
  const numeric = toNumber(value)
  if (numeric === null) return '--'
  return new Intl.NumberFormat('en-US', {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(numeric)
}

function formatTimestamp(value) {
  if (!value) return '--'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return '--'
  return parsed.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function easternSessionMeta(value) {
  if (!value) return null
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return null
  const parts = Object.fromEntries(
    EASTERN_SESSION_PARTS_FORMATTER
      .formatToParts(parsed)
      .filter((part) => part.type !== 'literal')
      .map((part) => [part.type, part.value]),
  )
  const hour = Number(parts.hour)
  const minute = Number(parts.minute)
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return null
  const minutes = hour * 60 + minute
  const weekday = String(parts.weekday || '').toLowerCase()
  const dateKey = `${parts.year}-${parts.month}-${parts.day}`

  if (weekday === 'sat' || weekday === 'sun' || minutes < 4 * 60 || minutes >= 20 * 60) {
    return { key: 'overnight', shortLabel: 'OVN', fullLabel: 'Overnight', tone: 'overnight', dateKey }
  }
  if (minutes < 9 * 60 + 30) {
    return { key: 'premarket', shortLabel: 'PRE', fullLabel: 'Premarket', tone: 'premarket', dateKey }
  }
  if (minutes < 16 * 60) {
    return { key: 'regular', shortLabel: 'RTH', fullLabel: 'Regular', tone: 'regular', dateKey }
  }
  return { key: 'afterhours', shortLabel: 'AH', fullLabel: 'After hours', tone: 'afterhours', dateKey }
}

function sessionToneFill(tone) {
  switch (tone) {
    case 'premarket':
      return 'rgba(64, 145, 255, 0.08)'
    case 'afterhours':
      return 'rgba(180, 132, 255, 0.08)'
    case 'overnight':
      return 'rgba(92, 92, 92, 0.05)'
    default:
      return 'transparent'
  }
}

function sessionToneStroke(tone) {
  switch (tone) {
    case 'premarket':
      return 'rgba(96, 96, 96, 0.22)'
    case 'afterhours':
      return 'rgba(179, 136, 255, 0.22)'
    case 'overnight':
      return 'rgba(92, 92, 92, 0.18)'
    default:
      return 'rgba(92, 92, 92, 0.16)'
  }
}

function intervalToRecoveryPoints(interval) {
  return RECOVERY_POINTS[String(interval || '').toLowerCase()] || 300
}

function buildLinePath(rows, xForIndex, yForPrice) {
  return rows
    .map((row, index) => {
      const x = xForIndex(index)
      const y = yForPrice(row.close)
      return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`
    })
    .join(' ')
}

function buildMetricPath(rows, xForIndex, valueAccessor, yForValue) {
  return rows
    .map((row, index) => {
      const value = valueAccessor(row)
      if (!Number.isFinite(value)) return null
      const x = xForIndex(index)
      const y = yForValue(value)
      return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`
    })
    .filter(Boolean)
    .join(' ')
}

function compactOrderType(value) {
  switch (String(value || '').trim().toLowerCase()) {
    case 'market':
      return 'MKT'
    case 'limit':
      return 'LMT'
    case 'stop_market':
      return 'STP MKT'
    case 'stop_limit':
      return 'STP LMT'
    case 'trailing_stop':
      return 'TRAIL'
    default:
      return String(value || '').trim().toUpperCase() || 'ORDER'
  }
}

function compactTimeInForce(value) {
  switch (String(value || '').trim().toLowerCase()) {
    case 'day':
      return 'DAY'
    case 'day_ext':
      return 'DAY+AH'
    case 'gtc_90d':
      return 'GTC 90D'
    default:
      return String(value || '').trim().toUpperCase() || ''
  }
}

function buildMarkers({ selectedPrice, pendingGuidePoint, workingOrder, positionMarkers, customGuides }) {
  const markers = []

  const pushMarker = (price, label, tone = 'neutral', options = {}) => {
    const numeric = toNumber(price)
    if (numeric === null || numeric <= 0) return
    markers.push({
      price: numeric,
      label,
      tone,
      chipText: options.chipText || label,
      dashArray: options.dashArray || '6 6',
      strokeWidth: options.strokeWidth || 1.2,
      opacity: options.opacity ?? 0.82,
      priority: options.priority ?? 2,
      clusterKey: options.clusterKey || null,
      clusterLabel: options.clusterLabel || '',
    })
  }

  pushMarker(selectedPrice?.price, 'Staged', 'selected', {
    chipText: `Staged ${formatPrice(selectedPrice?.price)}`,
    dashArray: '4 5',
    strokeWidth: 1.45,
    opacity: 0.96,
    priority: 6,
    clusterKey: 'setup',
    clusterLabel: 'Setup',
  })
  pushMarker(pendingGuidePoint?.price, 'Pending', 'selected', {
    chipText: `Pending ${formatPrice(pendingGuidePoint?.price)}`,
    dashArray: '2 5',
    strokeWidth: 1.35,
    opacity: 0.88,
    priority: 4,
    clusterKey: 'setup',
    clusterLabel: 'Setup',
  })

  const orderTypeLabel = compactOrderType(workingOrder?.orderType)
  const tifLabel = compactTimeInForce(workingOrder?.timeInForce)
  const trailingLabel = toNumber(workingOrder?.trailingPercent)
  const orderMeta = formatInlineMeta([orderTypeLabel, tifLabel])
  pushMarker(workingOrder?.executionPrice, 'Order', 'order', {
    chipText: orderMeta ? `Order ${orderMeta}` : 'Order',
    dashArray: '',
    strokeWidth: 1.5,
    opacity: 0.94,
    priority: 5,
    clusterKey: 'working-order',
    clusterLabel: 'Order',
  })
  pushMarker(workingOrder?.limitPrice, 'Limit', 'order', {
    chipText: `Limit ${formatPrice(workingOrder?.limitPrice)}`,
    dashArray: '8 4',
    strokeWidth: 1.35,
    opacity: 0.9,
    priority: 4,
    clusterKey: 'working-order',
    clusterLabel: 'Order',
  })
  pushMarker(workingOrder?.stopPrice, 'Stop', 'negative', {
    chipText:
      trailingLabel !== null
        ? `Trail ${trailingLabel.toFixed(1)}%`
        : `Stop ${formatPrice(workingOrder?.stopPrice)}`,
    dashArray: '5 4',
    strokeWidth: 1.35,
    opacity: 0.9,
    priority: 4,
    clusterKey: 'working-order',
    clusterLabel: 'Order',
  })

  ;(Array.isArray(positionMarkers) ? positionMarkers : []).forEach((marker, index) => {
    const suffix = `${index + 1}`
    const clusterKey = `position-${suffix}`
    const clusterLabel = `Plan ${suffix}`
    pushMarker(marker?.entryPrice, `Entry ${suffix}`, 'position', {
      chipText: `Entry ${suffix} ${formatPrice(marker?.entryPrice)}`,
      dashArray: '',
      strokeWidth: 1.4,
      opacity: 0.92,
      priority: 4,
      clusterKey,
      clusterLabel,
    })
    pushMarker(marker?.targetPrice, `Target ${suffix}`, 'positive', {
      chipText: `Target ${suffix} ${formatPrice(marker?.targetPrice)}`,
      dashArray: '7 5',
      strokeWidth: 1.3,
      opacity: 0.88,
      priority: 3,
      clusterKey,
      clusterLabel,
    })
    pushMarker(marker?.stopPrice, `Stop ${suffix}`, 'negative', {
      chipText: `Stop ${suffix} ${formatPrice(marker?.stopPrice)}`,
      dashArray: '5 4',
      strokeWidth: 1.3,
      opacity: 0.88,
      priority: 3,
      clusterKey,
      clusterLabel,
    })
  })

  ;(Array.isArray(customGuides) ? customGuides : []).forEach((guide) => {
    if (String(guide?.type || '').trim().toLowerCase() !== 'hline') return
    pushMarker(guide?.price, guide?.label || 'Guide', 'guide', {
      chipText: guide?.label || 'Guide',
      dashArray: '3 5',
      strokeWidth: 1.15,
      opacity: 0.78,
      priority: 1,
    })
  })

  return markers
}

function toneColor(tone, accent) {
  switch (tone) {
    case 'positive':
      return '#22c55e'
    case 'negative':
      return '#ff6b6b'
    case 'selected':
      return accent || '#7a7a7a'
    case 'order':
      return '#ffd43b'
    case 'position':
      return '#b388ff'
    case 'guide':
      return '#8a8a8a'
    default:
      return '#8a8a8a'
  }
}

function feedStatusForPayload(payload) {
  const source = String(payload?.freshness?.source || '').trim().toLowerCase()
  if (source === 'desk-fallback') return { tone: 'warning', label: 'Fallback data' }
  const status = String(payload?.freshness?.status || '').trim().toLowerCase()
  if (status === 'fresh') return { tone: 'live', label: 'Feed live' }
  if (status === 'warning') return { tone: 'warning', label: 'Feed delayed' }
  if (status === 'stale') return { tone: 'historical', label: 'Historical data' }
  return { tone: 'unknown', label: 'Feed unknown' }
}

function sessionLabelForPayload(payload) {
  const direct = String(payload?.freshness?.session_label || '').trim()
  if (direct) return direct
  const fallback = String(payload?.freshness?.session || '').trim()
  if (!fallback) return 'Session unknown'
  return fallback
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function liveLabelForPayload(payload) {
  if (payload?.extended_hours) return 'Extended hours'
  return 'Regular session'
}

function feedToneClass(tone) {
  switch (tone) {
    case 'live':
      return 'fresh-chart__pill--live'
    case 'warning':
      return 'fresh-chart__pill--warning'
    case 'historical':
      return 'fresh-chart__pill--historical'
    default:
      return 'fresh-chart__pill--neutral'
  }
}

function toFinitePriceValues(values) {
  return values.filter((value) => value !== null && Number.isFinite(value))
}

function buildViewportExtras(markers, liveValue) {
  return toFinitePriceValues([...markers.map((marker) => marker.price), toNumber(liveValue)])
}

function hashLabel(value) {
  return String(value || '').split('').reduce((sum, char) => sum + char.charCodeAt(0), 0)
}

function overlayAccent(name) {
  if (NAMED_OVERLAY_PALETTE[name]) return NAMED_OVERLAY_PALETTE[name]
  return OVERLAY_ACCENT_PALETTE[hashLabel(name) % OVERLAY_ACCENT_PALETTE.length]
}

function overlayLabel(name) {
  return NAMED_OVERLAY_LABELS[name] || String(name || '').replaceAll('_', ' ')
}

function compactOverlayLabel(name) {
  return COMPACT_OVERLAY_LABELS[name] || overlayLabel(name)
}

function overlayPriority(name) {
  switch (String(name || '').trim().toLowerCase()) {
    case 'idm_upper_band':
    case 'idm_lower_band':
    case 'idm_trailing_stop':
      return 3
    case 'vwap':
    case 'idm_vwap':
      return 2
    case 'ema_9':
    case 'ema_21':
    case 'ema_50':
    case 'sma_20':
    case 'sma_50':
      return 1
    default:
      return 0
  }
}

function sanitizeSavedViewportState(value) {
  if (!value || typeof value !== 'object') return null

  const nextViewport = {}

  if (Array.isArray(value.xaxisRange) && value.xaxisRange.length === 2) {
    const nextRange = value.xaxisRange.map((entry) => {
      if (entry === null) return null
      if (typeof entry !== 'string' || !entry.trim()) return null
      return Number.isNaN(new Date(entry).getTime()) ? null : entry
    })
    if (nextRange.some((entry) => entry !== null)) {
      nextViewport.xaxisRange = nextRange
    }
  }

  if (Array.isArray(value.xaxisLogicalRange) && value.xaxisLogicalRange.length === 2) {
    const from = toNumber(value.xaxisLogicalRange[0])
    const to = toNumber(value.xaxisLogicalRange[1])
    if (from !== null && to !== null && to > from) {
      nextViewport.xaxisLogicalRange = [from, to]
    }
  }

  if (Array.isArray(value.yaxisRange) && value.yaxisRange.length === 2) {
    const min = toNumber(value.yaxisRange[0])
    const max = toNumber(value.yaxisRange[1])
    if (min !== null && max !== null && max > min) {
      nextViewport.yaxisRange = [min, max]
    }
  }

  const visibility = normalizePaneVisibility(value)
  nextViewport.showVolumePane = visibility.showVolumePane
  nextViewport.showRsiPane = visibility.showRsiPane
  nextViewport.showMacdPane = visibility.showMacdPane
  nextViewport.paneRatios = normalizePaneRatios(value?.paneRatios)

  return nextViewport
}

function rightEdgeLabelWidth(text, { minWidth = 84, maxWidth = 164 } = {}) {
  const contentWidth = 18 + String(text || '').length * 6.15
  return Math.max(minWidth, Math.min(maxWidth, Math.round(contentWidth)))
}

function collapseDenseRightEdgeLabels(labels, { minDistance = 16 } = {}) {
  if (!Array.isArray(labels) || labels.length <= 1) return labels || []
  const selected = []
  const sorted = [...labels].sort((left, right) => {
    const priorityDelta = (right.priority ?? 0) - (left.priority ?? 0)
    if (priorityDelta !== 0) return priorityDelta
    return (left.desiredY ?? 0) - (right.desiredY ?? 0)
  })
  for (const label of sorted) {
    const isOverlay = label.role === 'overlay'
    const overlapsExisting = selected.some((existing) => {
      if (Math.abs((existing.desiredY ?? 0) - (label.desiredY ?? 0)) > minDistance) return false
      if (isOverlay && existing.role === 'overlay') return true
      if (isOverlay && existing.priority >= (label.priority ?? 0)) return true
      return false
    })
    if (!overlapsExisting) {
      selected.push(label)
    }
  }
  return selected
}

function buildMarkerClusterText(label, count) {
  const base = String(label?.clusterLabel || label?.text || 'Level').trim() || 'Level'
  return count > 1 ? `${base} x${count}` : base
}

function mergeMarkerLabelClusters(labels, { minDistance = 18 } = {}) {
  if (!Array.isArray(labels) || labels.length <= 1) return labels || []

  const passthrough = []
  const grouped = new Map()

  labels.forEach((label) => {
    if (label?.role !== 'marker' || !label?.clusterKey) {
      passthrough.push(label)
      return
    }
    const items = grouped.get(label.clusterKey) || []
    items.push(label)
    grouped.set(label.clusterKey, items)
  })

  const merged = []

  grouped.forEach((items) => {
    const sorted = [...items].sort((left, right) => (left.desiredY ?? 0) - (right.desiredY ?? 0))
    let cluster = []

    const commitCluster = () => {
      if (!cluster.length) return
      if (cluster.length === 1) {
        merged.push(cluster[0])
        cluster = []
        return
      }

      const representative = [...cluster].sort((left, right) => {
        const priorityDelta = (right.priority ?? 0) - (left.priority ?? 0)
        if (priorityDelta !== 0) return priorityDelta
        return (left.desiredY ?? 0) - (right.desiredY ?? 0)
      })[0]
      const averageY =
        cluster.reduce((sum, item) => sum + (item.desiredY ?? 0), 0) / Math.max(cluster.length, 1)
      const text = buildMarkerClusterText(representative, cluster.length)

      merged.push({
        ...representative,
        id: `${representative.clusterKey}-${Math.round(averageY)}-${cluster.length}`,
        desiredY: averageY,
        text,
        width: rightEdgeLabelWidth(text, { minWidth: 94, maxWidth: 148 }),
        priority: (representative.priority ?? 0) + 1,
      })
      cluster = []
    }

    sorted.forEach((item) => {
      if (!cluster.length) {
        cluster = [item]
        return
      }
      const previous = cluster[cluster.length - 1]
      if (Math.abs((item.desiredY ?? 0) - (previous.desiredY ?? 0)) <= minDistance) {
        cluster.push(item)
        return
      }
      commitCluster()
      cluster = [item]
    })

    commitCluster()
  })

  return [...passthrough, ...merged]
}

function buildOverlayClusterSummary(labels) {
  const names = Array.from(
    new Set(
      labels
        .map((label) => String(label?.compactLabel || '').trim())
        .filter(Boolean),
    ),
  )
  if (!names.length) return 'Study'
  if (names.length === 1) return names[0]
  if (names.length === 2) return `${names[0]} · ${names[1]}`
  return `${names[0]} · ${names[1]} +${names.length - 2}`
}

function mergeOverlayLabelClusters(labels, { minDistance = 18 } = {}) {
  if (!Array.isArray(labels) || labels.length <= 1) return labels || []
  const sorted = [...labels].sort((left, right) => (left.desiredY ?? 0) - (right.desiredY ?? 0))
  const merged = []
  let cluster = []

  const commitCluster = () => {
    if (!cluster.length) return
    if (cluster.length === 1) {
      merged.push(cluster[0])
      cluster = []
      return
    }

    const representative = [...cluster].sort((left, right) => {
      const priorityDelta = (right.priority ?? 0) - (left.priority ?? 0)
      if (priorityDelta !== 0) return priorityDelta
      return (left.desiredY ?? 0) - (right.desiredY ?? 0)
    })[0]
    const averageY = cluster.reduce((sum, item) => sum + (item.desiredY ?? 0), 0) / cluster.length
    const summary = buildOverlayClusterSummary(cluster)
    const displayValue = formatPrice(representative.displayValue)
    const text = `${summary} ${displayValue}`

    merged.push({
      ...representative,
      id: `overlay-cluster-${Math.round(averageY)}-${cluster.length}-${summary}`,
      desiredY: averageY,
      text,
      width: rightEdgeLabelWidth(text, { minWidth: 104, maxWidth: 156 }),
      priority: (representative.priority ?? 0) + 1,
    })
    cluster = []
  }

  sorted.forEach((label) => {
    if (!cluster.length) {
      cluster = [label]
      return
    }
    const previous = cluster[cluster.length - 1]
    if (Math.abs((label.desiredY ?? 0) - (previous.desiredY ?? 0)) <= minDistance) {
      cluster.push(label)
      return
    }
    commitCluster()
    cluster = [label]
  })
  commitCluster()

  return merged
}

function resolveRightEdgeLabels(labels, { top, bottom, minGap = RIGHT_EDGE_LABEL_MIN_GAP }) {
  if (!Array.isArray(labels) || !labels.length) return []
  const availableHeight = Math.max(bottom - top, 1)
  const filtered = collapseDenseRightEdgeLabels(labels, { minDistance: 14 })
  const compacted = [...filtered]
    .sort((left, right) => {
      const priorityDelta = (right.priority ?? 0) - (left.priority ?? 0)
      if (priorityDelta !== 0) return priorityDelta
      return (left.desiredY ?? 0) - (right.desiredY ?? 0)
    })
    .reduce((selected, label) => {
      const currentHeight = selected.reduce((sum, item) => sum + item.height, 0)
      const projectedCount = selected.length + 1
      const projectedGap = projectedCount <= 1 ? 0 : Math.max(2, minGap)
      const projectedHeight = currentHeight + label.height + projectedGap * Math.max(projectedCount - 1, 0)
      if (projectedHeight <= availableHeight) {
        selected.push(label)
      }
      return selected
    }, [])
  const workingLabels = compacted.length ? compacted : [labels[0]]
  const totalHeight = workingLabels.reduce((sum, label) => sum + label.height, 0)
  const effectiveGap =
    workingLabels.length <= 1
      ? 0
      : Math.max(2, Math.min(minGap, Math.floor((availableHeight - totalHeight) / (workingLabels.length - 1))))

  const placed = [...workingLabels]
    .sort((left, right) => left.desiredY - right.desiredY || right.priority - left.priority)
    .map((label) => ({ ...label, top: 0 }))

  let cursor = top
  for (const label of placed) {
    const desiredTop = Math.max(top, Math.min(bottom - label.height, label.desiredY - label.height / 2))
    label.top = Math.max(cursor, desiredTop)
    cursor = label.top + label.height + effectiveGap
  }

  let nextBottom = bottom
  for (let index = placed.length - 1; index >= 0; index -= 1) {
    const label = placed[index]
    const maxTop = nextBottom - label.height
    if (label.top > maxTop) {
      label.top = maxTop
    }
    nextBottom = label.top - effectiveGap
  }

  if (placed[0].top < top) {
    const delta = top - placed[0].top
    placed.forEach((label) => {
      label.top += delta
    })
  }

  return placed
}

function normalizePaneVisibility(source) {
  return {
    showVolumePane: source?.showVolumePane !== false,
    showRsiPane: source?.showRsiPane !== false,
    showMacdPane: source?.showMacdPane !== false,
  }
}

function normalizePaneRatios(sourceRatios = {}) {
  const nextRatios = {}
  for (const key of Object.keys(PANE_RATIO_DEFAULTS)) {
    const value = toNumber(sourceRatios?.[key])
    nextRatios[key] = value !== null && value > 0 ? value : PANE_RATIO_DEFAULTS[key]
  }
  return nextRatios
}

function resolvePaneHeights(totalHeight, visibility, ratios) {
  const visibleKeys = ['price']
  if (visibility.showVolumePane) visibleKeys.push('volume')
  if (visibility.showRsiPane) visibleKeys.push('rsi')
  if (visibility.showMacdPane) visibleKeys.push('macd')

  const gapCount = Math.max(visibleKeys.length - 1, 0)
  const availableHeight = Math.max(totalHeight - gapCount * VOLUME_PANE_GAP, PANE_MIN_HEIGHTS.price)
  const ratioSum = visibleKeys.reduce((sum, key) => sum + (ratios[key] || PANE_RATIO_DEFAULTS[key]), 0) || 1

  const heights = {}
  let remainingHeight = availableHeight

  visibleKeys.forEach((key, index) => {
    const minHeight = PANE_MIN_HEIGHTS[key] || 72
    const remainingKeys = visibleKeys.length - index - 1
    const reservedForRemaining = visibleKeys
      .slice(index + 1)
      .reduce((sum, nextKey) => sum + (PANE_MIN_HEIGHTS[nextKey] || 72), 0)
    const proportionalHeight = availableHeight * ((ratios[key] || PANE_RATIO_DEFAULTS[key]) / ratioSum)
    const clampedHeight =
      index === visibleKeys.length - 1
        ? remainingHeight
        : Math.max(minHeight, Math.min(remainingHeight - reservedForRemaining, proportionalHeight))
    heights[key] = Math.round(clampedHeight)
    remainingHeight -= heights[key]
  })

  const panes = {}
  let nextTop = PLOT_PADDING.top
  visibleKeys.forEach((key, index) => {
    panes[key] = {
      top: nextTop,
      height: heights[key],
      bottom: nextTop + heights[key],
    }
    nextTop += heights[key]
    if (index < visibleKeys.length - 1) {
      nextTop += VOLUME_PANE_GAP
    }
  })

  return {
    visibleKeys,
    gapCount,
    availableHeight,
    panes,
    stackBottom: visibleKeys.length ? panes[visibleKeys[visibleKeys.length - 1]].bottom : PLOT_PADDING.top + totalHeight,
  }
}

function resizePaneRatios(startRatios, visibility, upperKey, lowerKey, deltaPixels, availableHeight) {
  const nextRatios = normalizePaneRatios(startRatios)
  const pairTotal = (nextRatios[upperKey] || 0) + (nextRatios[lowerKey] || 0)
  if (pairTotal <= 0 || availableHeight <= 0) return nextRatios

  const upperMinRatio = (PANE_MIN_HEIGHTS[upperKey] || 72) / availableHeight
  const lowerMinRatio = (PANE_MIN_HEIGHTS[lowerKey] || 72) / availableHeight
  let upperRatio = nextRatios[upperKey] + (deltaPixels / availableHeight) * pairTotal
  const upperMaxRatio = pairTotal - lowerMinRatio
  upperRatio = Math.max(upperMinRatio, Math.min(upperMaxRatio, upperRatio))
  nextRatios[upperKey] = upperRatio
  nextRatios[lowerKey] = Math.max(lowerMinRatio, pairTotal - upperRatio)
  return nextRatios
}

function buildVolumeAverageRows(rows, period = 20, rsiPeriod = 14) {
  let rollingVolume = 0
  let avgGain = null
  let avgLoss = null
  let emaFast = null
  let emaSlow = null
  let signalLine = null
  const fastMultiplier = 2 / (12 + 1)
  const slowMultiplier = 2 / (26 + 1)
  const signalMultiplier = 2 / (9 + 1)
  const gains = []
  const losses = []
  return rows.map((row, index) => {
    rollingVolume += row.volume || 0
    if (index >= period) {
      rollingVolume -= rows[index - period].volume || 0
    }
    const windowSize = Math.min(index + 1, period)
    const previousClose = index > 0 ? rows[index - 1].close : null
    const change = previousClose !== null ? row.close - previousClose : 0
    const gain = Math.max(change, 0)
    const loss = Math.max(-change, 0)

    gains.push(gain)
    losses.push(loss)

    let rsi14 = null
    if (index === rsiPeriod) {
      const seedGains = gains.slice(1, rsiPeriod + 1)
      const seedLosses = losses.slice(1, rsiPeriod + 1)
      avgGain = seedGains.reduce((sum, value) => sum + value, 0) / rsiPeriod
      avgLoss = seedLosses.reduce((sum, value) => sum + value, 0) / rsiPeriod
    } else if (index > rsiPeriod && avgGain !== null && avgLoss !== null) {
      avgGain = ((avgGain * (rsiPeriod - 1)) + gain) / rsiPeriod
      avgLoss = ((avgLoss * (rsiPeriod - 1)) + loss) / rsiPeriod
    }

    if (index >= rsiPeriod && avgGain !== null && avgLoss !== null) {
      if (avgLoss === 0) {
        rsi14 = 100
      } else {
        const relativeStrength = avgGain / avgLoss
        rsi14 = 100 - (100 / (1 + relativeStrength))
      }
    }

    if (emaFast === null) {
      emaFast = row.close
      emaSlow = row.close
    } else {
      emaFast = ((row.close - emaFast) * fastMultiplier) + emaFast
      emaSlow = ((row.close - emaSlow) * slowMultiplier) + emaSlow
    }

    const macd = emaFast - emaSlow
    if (signalLine === null) {
      signalLine = macd
    } else {
      signalLine = ((macd - signalLine) * signalMultiplier) + signalLine
    }
    const macdHistogram = macd - signalLine

    return {
      ...row,
      averageVolume: windowSize > 0 ? rollingVolume / windowSize : 0,
      rsi14,
      macd,
      macdSignal: signalLine,
      macdHistogram,
    }
  })
}

export default function CustomMarketChart({
  payload,
  ticker = '',
  interval = '5m',
  livePrice,
  selectedPrice,
  onPriceSelect,
  onChartAction,
  onPayloadRecovered,
  height = 620,
  tickerAccent = '#7a7a7a',
  chartStyle = 'candles',
  autoRefreshLabel = '',
  workingOrder = null,
  pendingGuidePoint = null,
  positionMarkers = [],
  customGuides = [],
  hiddenOverlays = {},
  savedViewport = null,
  onViewportChange,
  onResetLayout,
}) {
  const initialSavedViewport = sanitizeSavedViewportState(savedViewport)
  const surfaceRef = useRef(null)
  const svgRef = useRef(null)
  const viewportRef = useRef(null)
  const dragStateRef = useRef(null)
  const hoverFrameRef = useRef(0)
  const pendingHoverIndexRef = useRef(null)
  const suppressClickRef = useRef(false)
  const paneResizeRef = useRef(null)
  const suppressViewportSyncRef = useRef(true)
  const [recoveredPayload, setRecoveredPayload] = useState(null)
  const [recoveryLoading, setRecoveryLoading] = useState(false)
  const [recoveryError, setRecoveryError] = useState('')
  const [hoverIndex, setHoverIndex] = useState(null)
  const [viewportState, setViewportState] = useState(null)
  const [isDragging, setIsDragging] = useState(false)
  const [isResizingPane, setIsResizingPane] = useState(false)
  const [showVolumePane, setShowVolumePane] = useState(initialSavedViewport?.showVolumePane !== false)
  const [showRsiPane, setShowRsiPane] = useState(initialSavedViewport?.showRsiPane !== false)
  const [showMacdPane, setShowMacdPane] = useState(initialSavedViewport?.showMacdPane !== false)
  const [paneRatios, setPaneRatios] = useState(() => normalizePaneRatios(initialSavedViewport?.paneRatios))

  const requestedTicker = String(ticker || payload?.ticker || '').trim().toUpperCase()
  const requestedInterval = String(interval || payload?.interval || '5m').trim().toLowerCase()
  const normalizedSavedViewport = useMemo(() => sanitizeSavedViewportState(savedViewport), [savedViewport])
  const payloadMatchesRequest =
    String(payload?.ticker || '').trim().toUpperCase() === requestedTicker &&
    String(payload?.interval || '').trim().toLowerCase() === requestedInterval
  const recoveredMatchesRequest =
    String(recoveredPayload?.ticker || '').trim().toUpperCase() === requestedTicker &&
    String(recoveredPayload?.interval || '').trim().toLowerCase() === requestedInterval

  useEffect(() => {
    setRecoveredPayload(null)
    setRecoveryError('')
    setHoverIndex(null)
  }, [requestedTicker, requestedInterval])

  useEffect(() => {
    suppressViewportSyncRef.current = true
    const nextVisibility = normalizePaneVisibility(normalizedSavedViewport)
    setShowVolumePane(nextVisibility.showVolumePane)
    setShowRsiPane(nextVisibility.showRsiPane)
    setShowMacdPane(nextVisibility.showMacdPane)
    setPaneRatios(normalizePaneRatios(normalizedSavedViewport?.paneRatios))
  }, [normalizedSavedViewport])

  useEffect(() => {
    if (!requestedTicker) return undefined
    if (payloadMatchesRequest && hasRenderableChartRows(payload)) {
      setRecoveryLoading(false)
      return undefined
    }

    let cancelled = false
    setRecoveryLoading(true)
    setRecoveryError('')

    getChart(requestedTicker, requestedInterval, intervalToRecoveryPoints(requestedInterval))
      .then((nextPayload) => {
        if (cancelled) return
        const normalizedPayload = {
          ...(nextPayload || {}),
          ticker: String(nextPayload?.ticker || requestedTicker || '').toUpperCase(),
          interval: String(nextPayload?.interval || requestedInterval || ''),
          candles: Array.isArray(nextPayload?.candles) ? nextPayload.candles : [],
        }
        setRecoveredPayload(normalizedPayload)
        onPayloadRecovered?.(normalizedPayload)
        if (!hasRenderableChartRows(normalizedPayload)) {
          setRecoveryError(`No chart data returned for ${requestedTicker} ${requestedInterval}.`)
        }
      })
      .catch((error) => {
        if (cancelled) return
        setRecoveryError(error?.response?.data?.detail || error?.message || 'Failed to load chart data.')
      })
      .finally(() => {
        if (!cancelled) {
          setRecoveryLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [payload, payloadMatchesRequest, requestedInterval, requestedTicker, onPayloadRecovered])

  const activePayload = useMemo(() => {
    if (payloadMatchesRequest && hasRenderableChartRows(payload)) return payload
    if (recoveredMatchesRequest && hasRenderableChartRows(recoveredPayload)) return recoveredPayload
    return null
  }, [payload, payloadMatchesRequest, recoveredPayload, recoveredMatchesRequest])

  const overlayDescriptors = useMemo(() => {
    const rawOverlays = activePayload?.overlays && typeof activePayload.overlays === 'object' ? activePayload.overlays : {}
    return Object.entries(rawOverlays)
      .filter(([name, series]) => {
        if (LOWER_PANE_OVERLAYS.has(name)) return false
        if (hiddenOverlays?.[name]) return false
        return Array.isArray(series) && series.some((value) => toNumber(value) !== null)
      })
      .map(([name, series]) => ({
        name,
        label: overlayLabel(name),
        compactLabel: compactOverlayLabel(name),
        color: overlayAccent(name),
        priority: overlayPriority(name),
        series,
      }))
  }, [activePayload, hiddenOverlays])

  const rows = useMemo(() => buildVolumeAverageRows(normalizeChartRows(activePayload)), [activePayload])
  const markers = useMemo(
    () => buildMarkers({ selectedPrice, pendingGuidePoint, workingOrder, positionMarkers, customGuides }),
    [selectedPrice, pendingGuidePoint, workingOrder, positionMarkers, customGuides],
  )

  const effectiveHeight = Math.max(Number(height) || 0, MIN_HEIGHT)
  const plotWidth = SVG_WIDTH - PLOT_PADDING.left - PLOT_PADDING.right
  const plotHeight = effectiveHeight - PLOT_PADDING.top - PLOT_PADDING.bottom
  const paneVisibility = { showVolumePane, showRsiPane, showMacdPane }
  const paneLayout = useMemo(
    () => resolvePaneHeights(plotHeight, paneVisibility, paneRatios),
    [plotHeight, showVolumePane, showRsiPane, showMacdPane, paneRatios],
  )
  const visiblePaneKeys = paneLayout.visibleKeys
  const pricePaneTop = paneLayout.panes.price?.top ?? PLOT_PADDING.top
  const pricePaneHeight = paneLayout.panes.price?.height ?? plotHeight
  const pricePaneBottom = paneLayout.panes.price?.bottom ?? (PLOT_PADDING.top + plotHeight)
  const volumePaneTop = paneLayout.panes.volume?.top ?? pricePaneBottom
  const volumePaneHeight = paneLayout.panes.volume?.height ?? 0
  const volumePaneBottom = paneLayout.panes.volume?.bottom ?? volumePaneTop
  const rsiPaneTop = paneLayout.panes.rsi?.top ?? volumePaneBottom
  const rsiPaneHeight = paneLayout.panes.rsi?.height ?? 0
  const rsiPaneBottom = paneLayout.panes.rsi?.bottom ?? rsiPaneTop
  const macdPaneTop = paneLayout.panes.macd?.top ?? rsiPaneBottom
  const macdPaneHeight = paneLayout.panes.macd?.height ?? 0
  const macdPaneBottom = paneLayout.panes.macd?.bottom ?? macdPaneTop
  const stackBottom = paneLayout.stackBottom

  const lastRow = rows.at(-1) || null
  const activeIndex = hoverIndex !== null && rows[hoverIndex] ? hoverIndex : rows.length - 1
  const activeRow = activeIndex >= 0 ? rows[activeIndex] : null
  const liveValue = toNumber(livePrice) ?? lastRow?.close ?? null
  const firstClose = rows[0]?.close ?? null
  const netChange = liveValue !== null && firstClose !== null ? liveValue - firstClose : null
  const netChangePct = netChange !== null && firstClose ? (netChange / firstClose) * 100 : null
  const feedStatus = feedStatusForPayload(activePayload)
  const sessionLabel = sessionLabelForPayload(activePayload)
  const liveLabel = liveLabelForPayload(activePayload)
  const freshnessMessage = String(activePayload?.freshness?.message || '').trim()
  const viewportExtras = useMemo(() => buildViewportExtras(markers, liveValue), [markers, liveValue])

  useEffect(() => {
    if (!rows.length) {
      viewportRef.current = null
      setViewportState(null)
      return
    }

    const seedViewport = normalizedSavedViewport || viewportToPersist(rows, viewportRef.current)
    const nextViewport = resolveViewport(rows, seedViewport, requestedInterval, viewportExtras)
    viewportRef.current = nextViewport
    setViewportState(nextViewport)
  }, [normalizedSavedViewport, rows, requestedInterval, viewportExtras])

  useEffect(() => {
    return () => {
      if (hoverFrameRef.current) {
        window.cancelAnimationFrame(hoverFrameRef.current)
      }
    }
  }, [])

  useEffect(() => {
    if (suppressViewportSyncRef.current) {
      suppressViewportSyncRef.current = false
      return
    }
    onViewportChange?.({
      showVolumePane,
      showRsiPane,
      showMacdPane,
      paneRatios,
    })
  }, [onViewportChange, showVolumePane, showRsiPane, showMacdPane, paneRatios])

  useEffect(() => {
    function handleWindowPointerMove(event) {
      const resizeState = paneResizeRef.current
      if (!resizeState || resizeState.pointerId !== event.pointerId) return
      const surfaceRect = surfaceRef.current?.getBoundingClientRect()
      if (!surfaceRect?.height) return
      const deltaPixels = (event.clientY - resizeState.startY) * (effectiveHeight / surfaceRect.height)
      setPaneRatios((current) =>
        resizePaneRatios(
          resizeState.startRatios,
          paneVisibility,
          resizeState.upperKey,
          resizeState.lowerKey,
          deltaPixels,
          paneLayout.availableHeight,
        ),
      )
    }

    function handleWindowPointerUp(event) {
      const resizeState = paneResizeRef.current
      if (!resizeState || resizeState.pointerId !== event.pointerId) return
      paneResizeRef.current = null
      setIsResizingPane(false)
    }

    window.addEventListener('pointermove', handleWindowPointerMove)
    window.addEventListener('pointerup', handleWindowPointerUp)
    window.addEventListener('pointercancel', handleWindowPointerUp)

    return () => {
      window.removeEventListener('pointermove', handleWindowPointerMove)
      window.removeEventListener('pointerup', handleWindowPointerUp)
      window.removeEventListener('pointercancel', handleWindowPointerUp)
    }
  }, [effectiveHeight, paneLayout.availableHeight, paneRatios, paneVisibility])

  useEffect(() => {
    const surface = surfaceRef.current
    if (!surface) return undefined

    function handleSurfaceWheel(event) {
      handleWheel(event)
    }

    surface.addEventListener('wheel', handleSurfaceWheel, { passive: false })
    return () => {
      surface.removeEventListener('wheel', handleSurfaceWheel)
    }
  }, [rows, requestedInterval, paneLayout, viewportState, hoverIndex, effectiveHeight])

  const viewport =
    viewportState ||
    (rows.length ? resolveViewport(rows, normalizedSavedViewport, requestedInterval, viewportExtras) : null)
  const visibleRows = useMemo(() => {
    if (!rows.length || !viewport) return rows
    const lowerIndex = Math.max(0, Math.floor(viewport.startIndex))
    const upperIndex = Math.min(rows.length - 1, Math.ceil(viewport.endIndex))
    return rows.slice(lowerIndex, upperIndex + 1)
  }, [rows, viewport])
  const visibleBarCount = visibleRows.length
  const visibleMaxVolume = useMemo(
    () => visibleRows.reduce((maxVolume, row) => Math.max(maxVolume, row.volume || 0, row.averageVolume || 0), 0),
    [visibleRows],
  )
  const visibleRsiRows = useMemo(
    () => visibleRows.filter((row) => Number.isFinite(row.rsi14)),
    [visibleRows],
  )
  const visibleMacdRows = useMemo(
    () => visibleRows.filter((row) => Number.isFinite(row.macd) || Number.isFinite(row.macdSignal) || Number.isFinite(row.macdHistogram)),
    [visibleRows],
  )
  const visibleOverlaySeries = useMemo(
    () =>
      overlayDescriptors.map((descriptor) => {
        const points = visibleRows.flatMap((row) => {
          const value = toNumber(descriptor.series[row.sourceIndex])
          return value === null ? [] : [{ row, value }]
        })
        return {
          ...descriptor,
          points,
          latest: points.at(-1) || null,
        }
      }),
    [overlayDescriptors, visibleRows],
  )
  const candleWidth = visibleBarCount
    ? Math.max(Math.min(plotWidth / Math.max(visibleBarCount, 1) * 0.62, 14), 2)
    : 4
  const lastVisibleRow = visibleRows.at(-1) || null
  const activeAverageVolume = activeRow?.averageVolume ?? null
  const activeRsi = activeRow?.rsi14 ?? null
  const activeMacd = activeRow?.macd ?? null
  const activeMacdSignal = activeRow?.macdSignal ?? null
  const activeMacdHistogram = activeRow?.macdHistogram ?? null

  const xForIndex = (index) => {
    if (!viewport) {
      if (rows.length <= 1) return PLOT_PADDING.left + plotWidth / 2
      return PLOT_PADDING.left + (plotWidth * index) / Math.max(rows.length - 1, 1)
    }
    const logicalSpan = Math.max(viewport.endIndex - viewport.startIndex, 1)
    return PLOT_PADDING.left + ((index - viewport.startIndex) / logicalSpan) * plotWidth
  }

  const yForPrice = (price) => {
    const maxPrice = viewport?.maxPrice ?? 1
    const minPrice = viewport?.minPrice ?? 0
    const span = Math.max(maxPrice - minPrice, 1e-6)
    const ratio = (maxPrice - price) / span
    return pricePaneTop + ratio * pricePaneHeight
  }

  const priceForY = (y) => {
    const clampedY = Math.max(pricePaneTop, Math.min(pricePaneBottom, y))
    const ratio = (clampedY - pricePaneTop) / Math.max(pricePaneHeight, 1)
    const maxPrice = viewport?.maxPrice ?? 1
    const minPrice = viewport?.minPrice ?? 0
    return maxPrice - ratio * (maxPrice - minPrice)
  }

  const yForVolume = (volume) => {
    const span = Math.max(visibleMaxVolume, 1)
    const clampedVolume = Math.max(0, toNumber(volume) ?? 0)
    const ratio = clampedVolume / span
    return volumePaneBottom - ratio * volumePaneHeight
  }

  const yForRsi = (value) => {
    const clampedValue = Math.max(0, Math.min(100, toNumber(value) ?? 0))
    const ratio = clampedValue / 100
    return rsiPaneBottom - ratio * rsiPaneHeight
  }

  const visibleMacdAbsMax = useMemo(() => {
    const maxValue = visibleMacdRows.reduce((currentMax, row) => {
      return Math.max(
        currentMax,
        Math.abs(toNumber(row.macd) ?? 0),
        Math.abs(toNumber(row.macdSignal) ?? 0),
        Math.abs(toNumber(row.macdHistogram) ?? 0),
      )
    }, 0)
    return Math.max(maxValue, 0.01)
  }, [visibleMacdRows])

  const yForMacd = (value) => {
    const numeric = toNumber(value) ?? 0
    const minValue = -visibleMacdAbsMax
    const maxValue = visibleMacdAbsMax
    const span = Math.max(maxValue - minValue, 1e-6)
    const ratio = (maxValue - numeric) / span
    return macdPaneTop + ratio * macdPaneHeight
  }
  const zeroMacdY = yForMacd(0)
  const visibleGeometryRows = useMemo(
    () =>
      visibleRows.map((row) => {
        const x = xForIndex(row.sourceIndex)
        return {
          row,
          x,
          openY: yForPrice(row.open),
          closeY: yForPrice(row.close),
          highY: yForPrice(row.high),
          lowY: yForPrice(row.low),
          volumeY: showVolumePane ? yForVolume(row.volume) : null,
          macdHistogramY: showMacdPane ? yForMacd(row.macdHistogram) : zeroMacdY,
        }
      }),
    [showMacdPane, showVolumePane, visibleRows, viewport, visibleMaxVolume, visibleMacdAbsMax],
  )

  const linePath = useMemo(
    () => buildLinePath(visibleRows, (localIndex) => xForIndex(visibleRows[localIndex].sourceIndex), yForPrice),
    [visibleRows, viewport],
  )
  const volumeAveragePath = useMemo(
    () =>
      buildMetricPath(
        visibleRows,
        (localIndex) => xForIndex(visibleRows[localIndex].sourceIndex),
        (row) => row.averageVolume,
        yForVolume,
      ),
    [visibleRows, viewport, visibleMaxVolume, showVolumePane],
  )
  const rsiPath = useMemo(
    () =>
      buildMetricPath(
        visibleRows,
        (localIndex) => xForIndex(visibleRows[localIndex].sourceIndex),
        (row) => row.rsi14,
        yForRsi,
      ),
    [visibleRows, viewport, showRsiPane],
  )
  const macdPath = useMemo(
    () =>
      buildMetricPath(
        visibleRows,
        (localIndex) => xForIndex(visibleRows[localIndex].sourceIndex),
        (row) => row.macd,
        yForMacd,
      ),
    [visibleRows, viewport, showMacdPane, visibleMacdAbsMax],
  )
  const macdSignalPath = useMemo(
    () =>
      buildMetricPath(
        visibleRows,
        (localIndex) => xForIndex(visibleRows[localIndex].sourceIndex),
        (row) => row.macdSignal,
        yForMacd,
      ),
    [visibleRows, viewport, showMacdPane, visibleMacdAbsMax],
  )

  const priceTicks = useMemo(() => {
    if (!viewport) return []
    const ticks = buildNicePriceTicks(
      viewport.minPrice,
      viewport.maxPrice,
      targetPriceTickCount(pricePaneHeight),
      formatPrice,
    )
    return ticks.map((tick, index) => ({
      id: `price-${index}`,
      price: tick.value,
      label: tick.label,
      y: yForPrice(tick.value),
    }))
  }, [pricePaneHeight, viewport])

  const timeTicks = useMemo(() => {
    if (!visibleRows.length) return []
    const minSpacing = Math.max(plotWidth / Math.max(targetTimeTickCount(plotWidth), 1), 88)
    const ticks = []
    let lastX = Number.NEGATIVE_INFINITY
    let lastLabel = ''

    for (const row of visibleRows) {
      const x = xForIndex(row.sourceIndex)
      const label = formatTimeAxisLabel(row.rawTime, requestedInterval)
      if (!label) continue
      if (x - lastX < minSpacing && label === lastLabel) continue
      if (x - lastX < minSpacing) continue
      ticks.push({
        id: `time-${row.sourceIndex}`,
        label,
        x,
      })
      lastX = x
      lastLabel = label
    }

    const lastRowInView = visibleRows.at(-1)
    if (lastRowInView && ticks.at(-1)?.id !== `time-${lastRowInView.sourceIndex}`) {
      ticks.push({
        id: `time-${lastRowInView.sourceIndex}`,
        label: formatTimeAxisLabel(lastRowInView.rawTime, requestedInterval),
        x: xForIndex(lastRowInView.sourceIndex),
      })
    }
    return ticks
  }, [visibleRows, plotWidth, requestedInterval, viewport])
  const visibleSessionSegments = useMemo(() => {
    if (!visibleRows.length) return []
    const segments = []
    let current = null

    visibleRows.forEach((row) => {
      const session = easternSessionMeta(row.rawTime) || {
        key: 'regular',
        shortLabel: 'RTH',
        fullLabel: 'Regular',
        tone: 'regular',
        dateKey: row.rawTime,
      }
      if (!current || current.key !== session.key || current.dateKey !== session.dateKey) {
        if (current) segments.push(current)
        current = {
          key: session.key,
          shortLabel: session.shortLabel,
          fullLabel: session.fullLabel,
          tone: session.tone,
          dateKey: session.dateKey,
          startIndex: row.sourceIndex,
          endIndex: row.sourceIndex,
        }
      } else {
        current.endIndex = row.sourceIndex
      }
    })
    if (current) segments.push(current)

    return segments.map((segment) => {
      const startX = xForIndex(segment.startIndex)
      const endX = xForIndex(segment.endIndex)
      return {
        ...segment,
        startX,
        endX,
        width: Math.max(endX - startX, candleWidth),
        fill: sessionToneFill(segment.tone),
        stroke: sessionToneStroke(segment.tone),
      }
    })
  }, [candleWidth, visibleRows, viewport])

  const hoverX = activeRow ? xForIndex(activeIndex) : null
  const hoverY = activeRow ? yForPrice(activeRow.close) : null
  const liveY = liveValue !== null ? yForPrice(liveValue) : null
  const showHoverPriceLabel =
    hoverY !== null && !(liveY !== null && Math.abs((activeRow?.close ?? 0) - (liveValue ?? 0)) < 1e-6)
  const activeBarDelta =
    activeRow && activeRow.open !== null && activeRow.close !== null ? activeRow.close - activeRow.open : null
  const activeBarDeltaPct =
    activeBarDelta !== null && activeRow?.open ? (activeBarDelta / activeRow.open) * 100 : null
  const statusMessage = useMemo(() => {
    if (freshnessMessage) return freshnessMessage
    if (feedStatus.tone === 'warning') {
      return 'Feed delayed. Use session shading and structure labels to confirm the active tape.'
    }
    if (feedStatus.tone === 'historical') {
      return 'Historical snapshot. Intraday session shading is preserved, but live execution context may lag.'
    }
    return ''
  }, [feedStatus.tone, freshnessMessage])
  const messageClassName = useMemo(() => {
    if (feedStatus.tone === 'warning') return 'fresh-chart__message fresh-chart__message--warning'
    if (feedStatus.tone === 'historical') return 'fresh-chart__message fresh-chart__message--historical'
    return 'fresh-chart__message'
  }, [feedStatus.tone])
  const rightEdgeLabels = useMemo(() => {
    const labelAnchorX = PLOT_PADDING.left + plotWidth + 8
    const markerLabels = []
    const overlayLabels = []
    const rawLabels = []

    markers.forEach((marker, index) => {
      const color = toneColor(marker.tone, tickerAccent)
      const text = marker.chipText || marker.label
      const width = rightEdgeLabelWidth(text, { minWidth: 92, maxWidth: 170 })
      markerLabels.push({
        id: `marker-${marker.label}-${marker.price}-${index}`,
        desiredY: yForPrice(marker.price),
        role: 'marker',
        text,
        width,
        height: RIGHT_EDGE_LABEL_HEIGHT,
        x: Math.min(labelAnchorX, SVG_WIDTH - width - 10),
        fill: color,
        stroke: color,
        strokeWidth: 0,
        textColor: '#061120',
        textX: 12,
        textAnchor: 'start',
        fontSize: 10.5,
        priority: marker.priority ?? 2,
        clusterKey: marker.clusterKey,
        clusterLabel: marker.clusterLabel,
      })
    })

    rawLabels.push(...mergeMarkerLabelClusters(markerLabels, { minDistance: RIGHT_EDGE_LABEL_HEIGHT + 2 }))

    visibleOverlaySeries.forEach((overlay) => {
      if (!overlay.latest) return
      const text = `${overlay.compactLabel} ${formatPrice(overlay.latest.value)}`
      const width = rightEdgeLabelWidth(text, { minWidth: 86, maxWidth: 138 })
      overlayLabels.push({
        id: `overlay-${overlay.name}`,
        desiredY: yForPrice(overlay.latest.value),
        role: 'overlay',
        text,
        width,
        height: 20,
        x: Math.min(labelAnchorX, SVG_WIDTH - width - 10),
        fill: 'rgba(8, 14, 24, 0.88)',
        stroke: overlay.color,
        strokeWidth: 1,
        textColor: overlay.color,
        textX: 10,
        textAnchor: 'start',
        fontSize: 10,
        priority: 1 + (overlay.priority ?? 0),
        compactLabel: overlay.compactLabel,
        displayValue: overlay.latest.value,
      })
    })

    rawLabels.push(...mergeOverlayLabelClusters(overlayLabels, { minDistance: RIGHT_EDGE_LABEL_HEIGHT + 2 }))

    if (liveY !== null) {
      const text = `Last ${formatPrice(liveValue)}`
      const width = rightEdgeLabelWidth(text, { minWidth: 100, maxWidth: 156 })
      rawLabels.push({
        id: 'live-price',
        desiredY: liveY,
        role: 'live',
        text,
        width,
        height: RIGHT_EDGE_LABEL_HEIGHT,
        x: Math.min(labelAnchorX, SVG_WIDTH - width - 10),
        fill: tickerAccent,
        stroke: tickerAccent,
        strokeWidth: 0,
        textColor: '#061120',
        textX: 12,
        textAnchor: 'start',
        fontSize: 10.75,
        priority: 5,
      })
    }

    if (showHoverPriceLabel && hoverY !== null && rawLabels.length < 9) {
      const text = `Hover ${formatPrice(activeRow?.close)}`
      const width = rightEdgeLabelWidth(text, { minWidth: 104, maxWidth: 156 })
      rawLabels.push({
        id: 'hover-price',
        desiredY: hoverY,
        role: 'hover',
        text,
        width,
        height: RIGHT_EDGE_LABEL_HEIGHT,
        x: Math.min(labelAnchorX, SVG_WIDTH - width - 10),
        fill: 'rgba(96, 96, 96, 0.94)',
        stroke: 'rgba(96, 96, 96, 0.94)',
        strokeWidth: 0,
        textColor: '#061120',
        textX: 12,
        textAnchor: 'start',
        fontSize: 10.75,
        priority: 6,
      })
    }

    return resolveRightEdgeLabels(rawLabels, {
      top: pricePaneTop + 4,
      bottom: pricePaneBottom - 4,
      minGap: RIGHT_EDGE_LABEL_MIN_GAP,
    })
  }, [
    activeRow?.close,
    hoverY,
    liveValue,
    liveY,
    markers,
    plotWidth,
    pricePaneBottom,
    pricePaneTop,
    showHoverPriceLabel,
    tickerAccent,
    visibleOverlaySeries,
  ])
  const rsiRightEdgeLabels = useMemo(() => {
    if (!showRsiPane) return []
    const labelAnchorX = PLOT_PADDING.left + plotWidth + 8
    const rawLabels = []
    if (activeRsi !== null) {
      rawLabels.push({
        id: 'rsi-active',
        desiredY: yForRsi(activeRsi),
        text: `RSI ${activeRsi.toFixed(2)}`,
        width: 104,
        height: PANE_EDGE_LABEL_HEIGHT,
        x: labelAnchorX,
        fill: '#b388ff',
        stroke: '#b388ff',
        strokeWidth: 0,
        textColor: '#061120',
        textX: 52,
        textAnchor: 'middle',
        fontSize: 10.75,
        priority: 4,
      })
      return resolveRightEdgeLabels(rawLabels, {
        top: rsiPaneTop + 4,
        bottom: rsiPaneBottom - 4,
        minGap: PANE_EDGE_LABEL_MIN_GAP,
      })
    }
    ;[30, 50, 70].forEach((level) => {
      rawLabels.push({
        id: `rsi-guide-${level}`,
        desiredY: yForRsi(level),
        text: String(level),
        width: 56,
        height: PANE_EDGE_LABEL_HEIGHT,
        x: labelAnchorX,
        fill: 'rgba(12, 12, 12, 0.92)',
        stroke: 'rgba(96, 96, 96, 0.3)',
        strokeWidth: 1,
        textColor: '#9eb0d0',
        textX: 28,
        textAnchor: 'middle',
        fontSize: 10.5,
        priority: level === 50 ? 0 : 1,
      })
    })
    return resolveRightEdgeLabels(rawLabels, {
      top: rsiPaneTop + 4,
      bottom: rsiPaneBottom - 4,
      minGap: PANE_EDGE_LABEL_MIN_GAP,
    })
  }, [activeRsi, plotWidth, rsiPaneBottom, rsiPaneTop, showRsiPane])
  const macdRightEdgeLabels = useMemo(() => {
    if (!showMacdPane) return []
    const labelAnchorX = PLOT_PADDING.left + plotWidth + 8
    const rawLabels = []
    const summaryValues = [activeMacd, activeMacdSignal].filter((value) => value !== null)
    if (summaryValues.length) {
      const summaryY =
        summaryValues.length === 2 ? (yForMacd(summaryValues[0]) + yForMacd(summaryValues[1])) / 2 : yForMacd(summaryValues[0])
      const summaryParts = []
      if (activeMacd !== null) summaryParts.push(`M ${activeMacd.toFixed(2)}`)
      if (activeMacdSignal !== null) summaryParts.push(`S ${activeMacdSignal.toFixed(2)}`)
      rawLabels.push({
        id: 'macd-summary',
        desiredY: summaryY,
        text: summaryParts.join('  '),
        width: activeMacd !== null && activeMacdSignal !== null ? 124 : 96,
        height: PANE_EDGE_LABEL_HEIGHT,
        x: labelAnchorX,
        fill: 'rgba(24, 24, 24, 0.96)',
        stroke: activeMacdSignal !== null ? '#ffd43b' : '#7a7a7a',
        strokeWidth: 1,
        textColor: '#f3f4f6',
        textX: activeMacd !== null && activeMacdSignal !== null ? 62 : 48,
        textAnchor: 'middle',
        fontSize: 10.5,
        priority: 4,
      })
      if (Math.abs(summaryY - zeroMacdY) > PANE_EDGE_LABEL_HEIGHT + 8) {
        rawLabels.push({
          id: 'macd-zero',
          desiredY: zeroMacdY,
          text: '0.00',
          width: 64,
          height: PANE_EDGE_LABEL_HEIGHT,
          x: labelAnchorX,
          fill: 'rgba(12, 12, 12, 0.92)',
          stroke: 'rgba(96, 96, 96, 0.3)',
          strokeWidth: 1,
          textColor: '#9eb0d0',
          textX: 32,
          textAnchor: 'middle',
          fontSize: 10.5,
          priority: 0,
        })
      }
    } else {
      rawLabels.push({
        id: 'macd-zero',
        desiredY: zeroMacdY,
        text: '0.00',
        width: 64,
        height: PANE_EDGE_LABEL_HEIGHT,
        x: labelAnchorX,
        fill: 'rgba(12, 12, 12, 0.92)',
        stroke: 'rgba(96, 96, 96, 0.3)',
        strokeWidth: 1,
        textColor: '#9eb0d0',
        textX: 32,
        textAnchor: 'middle',
        fontSize: 10.5,
        priority: 0,
      })
    }
    return resolveRightEdgeLabels(rawLabels, {
      top: macdPaneTop + 4,
      bottom: macdPaneBottom - 4,
      minGap: PANE_EDGE_LABEL_MIN_GAP,
    })
  }, [activeMacd, activeMacdSignal, macdPaneBottom, macdPaneTop, plotWidth, showMacdPane, zeroMacdY])
  const paneHandles = useMemo(() => {
    const handles = []
    for (let index = 0; index < visiblePaneKeys.length - 1; index += 1) {
      const upperKey = visiblePaneKeys[index]
      const lowerKey = visiblePaneKeys[index + 1]
      const upperPane = paneLayout.panes[upperKey]
      if (!upperPane) continue
      handles.push({
        id: `${upperKey}-${lowerKey}`,
        upperKey,
        lowerKey,
        top: upperPane.bottom + VOLUME_PANE_GAP / 2,
      })
    }
    return handles
  }, [paneLayout.panes, visiblePaneKeys])

  function commitViewport(nextViewport, { persist = true } = {}) {
    viewportRef.current = nextViewport
    setViewportState(nextViewport)
    const persisted = persist ? viewportToPersist(rows, nextViewport) : null
    if (persisted && persist) {
      onViewportChange?.(persisted)
    }
  }

  function handleResetScale() {
    if (!rows.length || !viewportRef.current) return
    commitViewport(fitPriceRangeToViewport(rows, viewportRef.current, viewportExtras))
  }

  function handleResetRange() {
    if (!rows.length || !viewportRef.current) return
    commitViewport(fitTimeRangeToViewport(rows, requestedInterval, viewportRef.current, viewportExtras))
  }

  function handleResetLayoutLocal() {
    suppressViewportSyncRef.current = true
    const defaultVisibility = normalizePaneVisibility(null)
    const defaultRatios = normalizePaneRatios()
    setShowVolumePane(defaultVisibility.showVolumePane)
    setShowRsiPane(defaultVisibility.showRsiPane)
    setShowMacdPane(defaultVisibility.showMacdPane)
    setPaneRatios(defaultRatios)
    scheduleHoverIndex(null)

    if (rows.length) {
      const nextViewport = resolveViewport(rows, null, requestedInterval, viewportExtras)
      viewportRef.current = nextViewport
      setViewportState(nextViewport)
    } else {
      viewportRef.current = null
      setViewportState(null)
    }

    onResetLayout?.()
  }

  function scheduleHoverIndex(nextIndex) {
    pendingHoverIndexRef.current = nextIndex
    if (hoverFrameRef.current) return
    hoverFrameRef.current = window.requestAnimationFrame(() => {
      hoverFrameRef.current = 0
      setHoverIndex((current) => (current === pendingHoverIndexRef.current ? current : pendingHoverIndexRef.current))
    })
  }

  function localPoint(event) {
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect || !rect.width || !rect.height) return null
    const scaleX = SVG_WIDTH / rect.width
    const scaleY = effectiveHeight / rect.height
    return {
      x: (event.clientX - rect.left) * scaleX,
      y: (event.clientY - rect.top) * scaleY,
    }
  }

  function resolveHoverIndex(point) {
    if (!point || !rows.length) return null
    if (point.y < PLOT_PADDING.top || point.y > PLOT_PADDING.top + plotHeight) {
      return null
    }
    const clampedPlotX = Math.max(PLOT_PADDING.left, Math.min(PLOT_PADDING.left + plotWidth, point.x))
    if (!viewport) {
      const ratio = (clampedPlotX - PLOT_PADDING.left) / Math.max(plotWidth, 1)
      return Math.max(0, Math.min(rows.length - 1, Math.round(ratio * Math.max(rows.length - 1, 0))))
    }

    const ratio = (clampedPlotX - PLOT_PADDING.left) / Math.max(plotWidth, 1)
    const logicalIndex = viewport.startIndex + ratio * Math.max(viewport.endIndex - viewport.startIndex, 1)
    return Math.max(0, Math.min(rows.length - 1, Math.round(logicalIndex)))
  }

  function handlePointerMove(event) {
    const point = localPoint(event)
    if (dragStateRef.current && point && viewportRef.current) {
      const dragState = dragStateRef.current
      const span = Math.max(dragState.startViewport.endIndex - dragState.startViewport.startIndex, 1)
      const priceSpan = Math.max(dragState.startViewport.maxPrice - dragState.startViewport.minPrice, 1e-6)
      const deltaX = point.x - dragState.startPoint.x
      const deltaY = point.y - dragState.startPoint.y
      const moved = Math.abs(deltaX) > 3 || Math.abs(deltaY) > 3
      dragState.hasMoved = dragState.hasMoved || moved
      if (!dragState.hasMoved) return

      const deltaIndex = -(deltaX / Math.max(plotWidth, 1)) * span
      const deltaPrice = dragState.allowPricePan
        ? (deltaY / Math.max(pricePaneHeight, 1)) * priceSpan
        : 0
      const nextViewport = panViewport(dragState.startViewport, deltaIndex, deltaPrice, rows)
      commitViewport(nextViewport, { persist: false })
      scheduleHoverIndex(null)
      return
    }

    scheduleHoverIndex(resolveHoverIndex(point))
  }

  function handlePointerLeave() {
    if (dragStateRef.current) return
    scheduleHoverIndex(null)
  }

  function handlePointerDown(event) {
    if (event.button !== undefined && event.button !== 0) return
    if (event.isPrimary === false) return
    const point = localPoint(event)
    const nextIndex = resolveHoverIndex(point)
    if (point === null || nextIndex === null || !viewportRef.current) return
    dragStateRef.current = {
      pointerId: event.pointerId,
      startPoint: point,
      startViewport: viewportRef.current,
      allowPricePan: point.y <= pricePaneBottom,
      hasMoved: false,
    }
    suppressClickRef.current = false
    setIsDragging(true)
    event.currentTarget.setPointerCapture?.(event.pointerId)
  }

  function handlePointerUp(event) {
    const dragState = dragStateRef.current
    if (!dragState) return
    dragStateRef.current = null
    setIsDragging(false)
    event.currentTarget.releasePointerCapture?.(event.pointerId)
    if (dragState.hasMoved) {
      suppressClickRef.current = true
      commitViewport(viewportRef.current, { persist: true })
      return
    }
    scheduleHoverIndex(resolveHoverIndex(localPoint(event)))
  }

  function handlePointerCancel(event) {
    dragStateRef.current = null
    setIsDragging(false)
    event.currentTarget.releasePointerCapture?.(event.pointerId)
    scheduleHoverIndex(null)
  }

  function handleWheel(event) {
    if (!rows.length || !viewportRef.current) return
    const point = localPoint(event)
    if (!point) return
    const nextIndex = resolveHoverIndex(point)
    if (nextIndex === null) return
    event.preventDefault()
    event.stopPropagation?.()
    const nextViewport = zoomViewport(
      viewportRef.current,
      rows,
      nextIndex,
      event.deltaY < 0 ? 0.88 : 1.16,
    )
    commitViewport(nextViewport, { persist: true })
    scheduleHoverIndex(nextIndex)
  }

  function handleClick(event) {
    if (suppressClickRef.current) {
      suppressClickRef.current = false
      return
    }
    const point = localPoint(event)
    if (!point || ((showVolumePane || showRsiPane || showMacdPane) && point.y > pricePaneBottom)) return
    const nextIndex = resolveHoverIndex(point)
    if (nextIndex === null || !rows[nextIndex]) return
    const nextPoint = {
      price: priceForY(point.y),
      timestamp: rows[nextIndex].rawTime,
      index: nextIndex,
      source: 'fresh-chart',
    }
    onPriceSelect?.(nextPoint)
    onChartAction?.(nextPoint)
  }

  function handlePaneResizeStart(event, upperKey, lowerKey) {
    event.preventDefault()
    event.stopPropagation()
    if (event.button !== undefined && event.button !== 0) return
    if (event.isPrimary === false) return
    paneResizeRef.current = {
      pointerId: event.pointerId,
      startY: event.clientY,
      upperKey,
      lowerKey,
      startRatios: paneRatios,
    }
    setIsResizingPane(true)
  }

  function handleDoubleClick(event) {
    const point = localPoint(event)
    if (!point || point.y > pricePaneBottom) return
    handleResetScale()
  }

  if (recoveryLoading && !rows.length) {
    return (
    <div className="fresh-chart fresh-chart--loading ui-motion-stage ui-motion-stage--soft" style={{ minHeight: effectiveHeight }}>
      <div className="fresh-chart__status ui-motion-stage ui-motion-stage--delay-1">
          <div className="fresh-chart__status-title">Loading chart data</div>
          <div className="fresh-chart__status-copy">
            Pulling {requestedTicker || '--'} {requestedInterval} candles from the live market feed.
          </div>
        </div>
      </div>
    )
  }

  if (!rows.length) {
    return (
      <div className="fresh-chart" style={{ minHeight: effectiveHeight }}>
        <EmptyState
          title="No chart data"
          description={recoveryError || `No ${requestedTicker || '--'} ${requestedInterval} bars are available yet.`}
        />
      </div>
    )
  }

  return (
    <div className="fresh-chart ui-motion-stage ui-motion-stage--soft" style={{ minHeight: effectiveHeight }}>
        <div className="fresh-chart__header ui-motion-stage ui-motion-stage--delay-1">
        <div className="fresh-chart__identity">
            <Kicker as="div" className="fresh-chart__kicker">{feedStatus.label}</Kicker>
          <div className="fresh-chart__symbol-row">
            <SignalDot className="fresh-chart__dot" accent={tickerAccent} size="md" />
            <strong>{requestedTicker || '--'}</strong>
            <span>{requestedInterval}</span>
          </div>
          <div className="fresh-chart__pill-row">
            <span className={`fresh-chart__pill ${feedToneClass(feedStatus.tone)}`}>{feedStatus.label}</span>
            <span className="fresh-chart__pill fresh-chart__pill--neutral">{sessionLabel}</span>
            <span className="fresh-chart__pill fresh-chart__pill--neutral">{liveLabel}</span>
          </div>
        </div>

        <div className="fresh-chart__stats ui-motion-stage ui-motion-stage--delay-2">
          <div className="fresh-chart__stat">
            <span>Last</span>
            <strong>{formatPrice(liveValue)}</strong>
          </div>
          <div className="fresh-chart__stat">
            <span>Change</span>
            <strong className={netChange >= 0 ? 'fresh-chart__value--up' : 'fresh-chart__value--down'}>
              {formatSignedNumber(netChange)} ({formatSignedPercent(netChangePct)})
            </strong>
          </div>
          <div className="fresh-chart__stat">
            <span>Bars</span>
            <strong>{rows.length}</strong>
          </div>
          <div className="fresh-chart__stat">
            <span>Mode</span>
            <strong>{chartStyle === 'line' ? 'Line' : 'Candles'}</strong>
          </div>
          <div className="fresh-chart__stat">
            <span>Refresh</span>
            <strong>{autoRefreshLabel || '--'}</strong>
          </div>
        </div>
      </div>

      <div className="fresh-chart__readout ui-motion-stage ui-motion-stage--delay-3">
        <div className="fresh-chart__toolbar">
          <div className="fresh-chart__toolbar-meta">
            <span>Visible bars</span>
            <strong>{visibleBarCount || rows.length}</strong>
          </div>
          <div className="fresh-chart__toolbar-actions">
            <Button
              type="button"
              variant={showVolumePane ? 'solid' : 'ghost'}
              size="sm"
              className={`fresh-chart__toolbar-button ${showVolumePane ? 'fresh-chart__toolbar-button--active' : ''}`}
              onClick={() => setShowVolumePane((current) => !current)}
              aria-pressed={showVolumePane}
            >
              Volume {showVolumePane ? 'on' : 'off'}
            </Button>
            <Button
              type="button"
              variant={showRsiPane ? 'solid' : 'ghost'}
              size="sm"
              className={`fresh-chart__toolbar-button ${showRsiPane ? 'fresh-chart__toolbar-button--active' : ''}`}
              onClick={() => setShowRsiPane((current) => !current)}
              aria-pressed={showRsiPane}
            >
              RSI {showRsiPane ? 'on' : 'off'}
            </Button>
            <Button
              type="button"
              variant={showMacdPane ? 'solid' : 'ghost'}
              size="sm"
              className={`fresh-chart__toolbar-button ${showMacdPane ? 'fresh-chart__toolbar-button--active' : ''}`}
              onClick={() => setShowMacdPane((current) => !current)}
              aria-pressed={showMacdPane}
            >
              MACD {showMacdPane ? 'on' : 'off'}
            </Button>
            <Button type="button" variant="ghost" size="sm" className="fresh-chart__toolbar-button" onClick={handleResetScale}>
              Reset scale
            </Button>
            <Button type="button" variant="ghost" size="sm" className="fresh-chart__toolbar-button" onClick={handleResetRange}>
              Reset range
            </Button>
            {onResetLayout ? (
              <Button type="button" variant="ghost" size="sm" className="fresh-chart__toolbar-button" onClick={handleResetLayoutLocal}>
                Reset layout
              </Button>
            ) : null}
          </div>
        </div>
        {statusMessage ? <div className={messageClassName}>{statusMessage}</div> : null}
        <div className="fresh-chart__readout-title">
          Latest bar | {formatTimestamp(activeRow?.rawTime)}
        </div>
        <div className="fresh-chart__readout-grid">
          <div><span>O</span><strong>{formatPrice(activeRow?.open)}</strong></div>
          <div><span>H</span><strong>{formatPrice(activeRow?.high)}</strong></div>
          <div><span>L</span><strong>{formatPrice(activeRow?.low)}</strong></div>
          <div><span>C</span><strong>{formatPrice(activeRow?.close)}</strong></div>
          <div><span>Vol</span><strong>{formatCompactNumber(activeRow?.volume)}</strong></div>
          <div><span>Vol avg</span><strong>{formatCompactNumber(activeAverageVolume)}</strong></div>
          <div><span>RSI 14</span><strong>{activeRsi === null ? '--' : activeRsi.toFixed(2)}</strong></div>
          <div><span>MACD</span><strong>{activeMacd === null ? '--' : activeMacd.toFixed(2)}</strong></div>
          <div><span>Signal</span><strong>{activeMacdSignal === null ? '--' : activeMacdSignal.toFixed(2)}</strong></div>
          <div><span>Hist</span><strong>{activeMacdHistogram === null ? '--' : activeMacdHistogram.toFixed(2)}</strong></div>
          <div>
            <span>Bar delta</span>
            <strong
              className={
                activeBarDelta === null
                  ? ''
                  : activeBarDelta >= 0
                    ? 'fresh-chart__value--up'
                    : 'fresh-chart__value--down'
              }
            >
              {formatSignedNumber(activeBarDelta)} ({formatSignedPercent(activeBarDeltaPct)})
            </strong>
          </div>
        </div>
      </div>

        <div ref={surfaceRef} className="fresh-chart__surface ui-motion-stage ui-motion-stage--delay-4">
        <svg
          ref={svgRef}
          className={`fresh-chart__svg ${isDragging ? 'fresh-chart__svg--dragging' : ''} ${isResizingPane ? 'fresh-chart__svg--resizing' : ''}`}
          viewBox={`0 0 ${SVG_WIDTH} ${effectiveHeight}`}
          preserveAspectRatio="none"
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerCancel}
          onPointerLeave={handlePointerLeave}
          onClick={handleClick}
          onDoubleClick={handleDoubleClick}
        >
          <rect x="0" y="0" width={SVG_WIDTH} height={effectiveHeight} rx="22" fill="#0b0b0b" />
          <rect
            x={PLOT_PADDING.left}
            y={PLOT_PADDING.top}
            width={plotWidth}
            height={plotHeight}
            fill="#121212"
            stroke="rgba(112, 112, 112, 0.2)"
            strokeWidth="1"
          />
          {visibleSessionSegments.map((segment) =>
            segment.fill === 'transparent' ? null : (
              <rect
                key={`session-fill-${segment.dateKey}-${segment.key}-${segment.startIndex}`}
                x={segment.startX}
                y={PLOT_PADDING.top}
                width={Math.max(segment.width, candleWidth)}
                height={stackBottom - PLOT_PADDING.top}
                fill={segment.fill}
              />
            ),
          )}
          {visibleSessionSegments.map((segment, index) => (
            <g key={`session-boundary-${segment.dateKey}-${segment.key}-${segment.startIndex}`}>
              {index > 0 ? (
                <line
                  x1={segment.startX}
                  x2={segment.startX}
                  y1={PLOT_PADDING.top}
                  y2={stackBottom}
                  stroke={segment.stroke}
                  strokeDasharray="3 6"
                />
              ) : null}
              {segment.width >= 56 ? (
                <g>
                  <rect
                    x={Math.max(PLOT_PADDING.left + 8, segment.startX + 6)}
                    y={pricePaneTop + 8}
                    width="42"
                    height="18"
                    rx="9"
                    fill="rgba(10, 10, 10, 0.92)"
                    stroke={segment.stroke}
                    strokeWidth="1"
                  />
                  <text
                    x={Math.max(PLOT_PADDING.left + 29, segment.startX + 27)}
                    y={pricePaneTop + 21}
                    fill="#dedede"
                    fontSize="10"
                    fontWeight="700"
                    textAnchor="middle"
                  >
                    {segment.shortLabel}
                  </text>
                </g>
              ) : null}
            </g>
          ))}
          {showVolumePane ? (
            <line
              x1={PLOT_PADDING.left}
              x2={PLOT_PADDING.left + plotWidth}
              y1={pricePaneBottom + VOLUME_PANE_GAP / 2}
              y2={pricePaneBottom + VOLUME_PANE_GAP / 2}
              stroke="rgba(92, 92, 92, 0.16)"
            />
          ) : null}
          {showRsiPane ? (
            <line
              x1={PLOT_PADDING.left}
              x2={PLOT_PADDING.left + plotWidth}
              y1={(showVolumePane ? volumePaneBottom : pricePaneBottom) + VOLUME_PANE_GAP / 2}
              y2={(showVolumePane ? volumePaneBottom : pricePaneBottom) + VOLUME_PANE_GAP / 2}
              stroke="rgba(92, 92, 92, 0.16)"
            />
          ) : null}
          {showMacdPane ? (
            <line
              x1={PLOT_PADDING.left}
              x2={PLOT_PADDING.left + plotWidth}
              y1={(showRsiPane ? rsiPaneBottom : showVolumePane ? volumePaneBottom : pricePaneBottom) + VOLUME_PANE_GAP / 2}
              y2={(showRsiPane ? rsiPaneBottom : showVolumePane ? volumePaneBottom : pricePaneBottom) + VOLUME_PANE_GAP / 2}
              stroke="rgba(92, 92, 92, 0.16)"
            />
          ) : null}

          {priceTicks.map((tick) => (
            <g key={tick.id}>
              <line
                x1={PLOT_PADDING.left}
                x2={PLOT_PADDING.left + plotWidth}
                y1={tick.y}
                y2={tick.y}
                stroke="rgba(112, 112, 112, 0.16)"
                strokeDasharray="4 6"
              />
              <text
                x={PLOT_PADDING.left + plotWidth + 12}
                y={tick.y + 4}
                fill="#d4d4d4"
                fontSize="12"
              >
                {tick.label}
              </text>
            </g>
          ))}

          {timeTicks.map((tick) => (
            <g key={tick.id}>
              <line
                x1={tick.x}
                x2={tick.x}
                y1={PLOT_PADDING.top}
                y2={stackBottom}
                stroke="rgba(112, 112, 112, 0.12)"
              />
              <text
                x={tick.x}
                y={PLOT_PADDING.top + plotHeight + 22}
                fill="#d0d0d0"
                fontSize="12"
                textAnchor="middle"
              >
                {tick.label}
              </text>
            </g>
          ))}

          {lastVisibleRow ? (
            <rect
              x={(visibleGeometryRows.at(-1)?.x ?? xForIndex(lastVisibleRow.sourceIndex)) - candleWidth}
              y={PLOT_PADDING.top}
              width={Math.max(candleWidth * 2, 8)}
              height={pricePaneHeight}
              fill="rgba(96, 96, 96, 0.08)"
              rx="4"
            />
          ) : null}

          {chartStyle === 'line' ? (
            <path
              d={linePath}
              fill="none"
              stroke={tickerAccent}
              strokeWidth="2.4"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          ) : (
            visibleGeometryRows.map(({ row, x, openY, closeY, highY, lowY }) => {
              const rising = row.close >= row.open
      const color = rising ? '#22c55e' : '#ff6b6b'
      const bodyFill = rising ? '#22c55e' : '#ff6b6b'
              const bodyTop = Math.min(openY, closeY)
              const bodyHeight = Math.max(Math.abs(closeY - openY), 2.4)

              return (
                <g key={`${row.rawTime}-${row.sourceIndex}`}>
                  <line x1={x} x2={x} y1={highY} y2={lowY} stroke={color} strokeWidth="1.35" />
                  <rect
                    x={x - candleWidth / 2}
                    y={bodyTop}
                    width={candleWidth}
                    height={bodyHeight}
                    rx="1.2"
                    fill={bodyFill}
                    stroke={color}
                    strokeWidth="0.9"
                  />
                </g>
              )
            })
          )}

          {visibleOverlaySeries.map((overlay) => {
            const overlayPath = buildMetricPath(
              overlay.points.map((point) => ({ ...point.row, overlayValue: point.value })),
              (localIndex) => xForIndex(overlay.points[localIndex].row.sourceIndex),
              (row) => row.overlayValue,
              yForPrice,
            )
            if (!overlayPath) return null
            return (
              <path
                key={`overlay-${overlay.name}`}
                d={overlayPath}
                fill="none"
                stroke={overlay.color}
                strokeWidth="1.6"
                strokeLinejoin="round"
                strokeLinecap="round"
                opacity="0.92"
              />
            )
          })}

          {markers.map((marker, index) => {
            const y = yForPrice(marker.price)
            const color = toneColor(marker.tone, tickerAccent)
            return (
              <g key={`${marker.label}-${marker.price}-${index}-line`}>
                <line
                  x1={PLOT_PADDING.left}
                  x2={PLOT_PADDING.left + plotWidth}
                  y1={y}
                  y2={y}
                  stroke={color}
                  strokeWidth={marker.strokeWidth}
                  strokeDasharray={marker.dashArray || undefined}
                  opacity={marker.opacity ?? 0.82}
                />
                <circle cx={PLOT_PADDING.left + plotWidth} cy={y} r="3" fill={color} opacity="0.96" />
              </g>
            )
          })}

          {liveY !== null ? (
            <g>
              <line
                x1={PLOT_PADDING.left}
                x2={PLOT_PADDING.left + plotWidth}
                y1={liveY}
                y2={liveY}
                stroke={tickerAccent}
                strokeWidth="1.2"
                strokeDasharray="3 5"
                opacity="0.8"
              />
            </g>
          ) : null}

          {hoverX !== null && hoverY !== null ? (
            <g>
              <line
                x1={hoverX}
                x2={hoverX}
                y1={PLOT_PADDING.top}
                y2={stackBottom}
                stroke="rgba(96, 96, 96, 0.45)"
                strokeDasharray="4 4"
              />
              <line
                x1={PLOT_PADDING.left}
                x2={PLOT_PADDING.left + plotWidth}
                y1={hoverY}
                y2={hoverY}
                stroke="rgba(96, 96, 96, 0.32)"
                strokeDasharray="4 4"
              />
            </g>
          ) : null}

          {rightEdgeLabels.map((label) => (
            <g key={label.id}>
              <rect
                x={label.x}
                y={label.top}
                width={label.width}
                height={label.height}
                rx="10"
                fill={label.fill}
                stroke={label.stroke}
                strokeWidth={label.strokeWidth}
              />
              <text
                x={label.x + label.textX}
                y={label.top + label.height / 2 + 4}
                fill={label.textColor}
                fontSize={label.fontSize}
                fontWeight="700"
                textAnchor={label.textAnchor}
              >
                {label.text}
              </text>
            </g>
          ))}

          {showVolumePane ? (
            <g>
              <rect
                x={PLOT_PADDING.left}
                y={volumePaneTop}
                width={plotWidth}
                height={volumePaneHeight}
                fill="rgba(15, 15, 15, 0.96)"
              />
              {visibleGeometryRows.map(({ row, x, volumeY }) => {
                const y = volumeY ?? volumePaneBottom
                const barHeight = Math.max(volumePaneBottom - y, 1.5)
                const rising = row.close >= row.open
                return (
                  <rect
                    key={`volume-${row.rawTime}-${row.sourceIndex}`}
                    x={x - candleWidth / 2}
                    y={y}
                    width={Math.max(candleWidth, 2)}
                    height={barHeight}
                    rx="1.2"
                    fill={rising ? 'rgba(36, 213, 161, 0.72)' : 'rgba(255, 107, 107, 0.72)'}
                  />
                )
              })}
              {volumeAveragePath ? (
                <path
                  d={volumeAveragePath}
                  fill="none"
                  stroke="#ffd43b"
                  strokeWidth="1.8"
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              ) : null}
              <text
                x={PLOT_PADDING.left + 14}
                y={volumePaneTop + 18}
                fill="#d0d0d0"
                fontSize="11"
                letterSpacing="0.12em"
              >
                VOL {formatCompactNumber(activeRow?.volume)} | AVG {formatCompactNumber(activeAverageVolume)}
              </text>
            </g>
          ) : null}
          {showRsiPane ? (
            <g>
              <rect
                x={PLOT_PADDING.left}
                y={rsiPaneTop}
                width={plotWidth}
                height={rsiPaneHeight}
                fill="rgba(13, 13, 13, 0.97)"
              />
              {[30, 50, 70].map((level) => {
                const y = yForRsi(level)
                return (
                  <g key={`rsi-guide-${level}`}>
                    <line
                      x1={PLOT_PADDING.left}
                      x2={PLOT_PADDING.left + plotWidth}
                      y1={y}
                      y2={y}
                      stroke="rgba(96, 96, 96, 0.12)"
                      strokeDasharray={level === 50 ? '4 4' : '3 5'}
                    />
                  </g>
                )
              })}
              {rsiPath ? (
                <path
                  d={rsiPath}
                  fill="none"
                  stroke="#b388ff"
                  strokeWidth="2"
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              ) : null}
              <text
                x={PLOT_PADDING.left + 14}
                y={rsiPaneTop + 18}
                fill="#d7c8ff"
                fontSize="11"
                letterSpacing="0.12em"
              >
                RSI 14 {activeRsi === null ? '--' : activeRsi.toFixed(2)}
              </text>
              {rsiRightEdgeLabels.map((label) => (
                <g key={label.id}>
                  <rect
                    x={label.x}
                    y={label.top}
                    width={label.width}
                    height={label.height}
                    rx="10"
                    fill={label.fill}
                    stroke={label.stroke}
                    strokeWidth={label.strokeWidth}
                  />
                  <text
                    x={label.x + label.textX}
                    y={label.top + label.height / 2 + 4}
                    fill={label.textColor}
                    fontSize={label.fontSize}
                    fontWeight="700"
                    textAnchor={label.textAnchor}
                  >
                    {label.text}
                  </text>
                </g>
              ))}
            </g>
          ) : null}
          {showMacdPane ? (
            <g>
              <rect
                x={PLOT_PADDING.left}
                y={macdPaneTop}
                width={plotWidth}
                height={macdPaneHeight}
                fill="rgba(13, 13, 13, 0.97)"
              />
              <line
                x1={PLOT_PADDING.left}
                x2={PLOT_PADDING.left + plotWidth}
                y1={zeroMacdY}
                y2={zeroMacdY}
                stroke="rgba(96, 96, 96, 0.14)"
                strokeDasharray="4 4"
              />
              {visibleGeometryRows.map(({ row, x, macdHistogramY }) => {
                const barTop = Math.min(zeroMacdY, macdHistogramY)
                const barHeight = Math.max(Math.abs(macdHistogramY - zeroMacdY), 1.5)
                const positive = (toNumber(row.macdHistogram) ?? 0) >= 0
                return (
                  <rect
                    key={`macd-hist-${row.rawTime}-${row.sourceIndex}`}
                    x={x - Math.max(candleWidth * 0.4, 1.5)}
                    y={barTop}
                    width={Math.max(candleWidth * 0.8, 2)}
                    height={barHeight}
                    rx="1"
                    fill={positive ? 'rgba(36, 213, 161, 0.72)' : 'rgba(255, 107, 107, 0.72)'}
                  />
                )
              })}
              {macdPath ? (
                <path
                  d={macdPath}
                  fill="none"
                  stroke="#7a7a7a"
                  strokeWidth="2"
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              ) : null}
              {macdSignalPath ? (
                <path
                  d={macdSignalPath}
                  fill="none"
                  stroke="#ffd43b"
                  strokeWidth="1.8"
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              ) : null}
              <text
                x={PLOT_PADDING.left + 14}
                y={macdPaneTop + 18}
                fill="#d7e7ff"
                fontSize="11"
                letterSpacing="0.12em"
              >
                MACD {activeMacd === null ? '--' : activeMacd.toFixed(2)} | SIG {activeMacdSignal === null ? '--' : activeMacdSignal.toFixed(2)} | HIST {activeMacdHistogram === null ? '--' : activeMacdHistogram.toFixed(2)}
              </text>
              <text
              />
              {macdRightEdgeLabels.map((label) => (
                <g key={label.id}>
                  <rect
                    x={label.x}
                    y={label.top}
                    width={label.width}
                    height={label.height}
                    rx="10"
                    fill={label.fill}
                    stroke={label.stroke}
                    strokeWidth={label.strokeWidth}
                  />
                  <text
                    x={label.x + label.textX}
                    y={label.top + label.height / 2 + 4}
                    fill={label.textColor}
                    fontSize={label.fontSize}
                    fontWeight="700"
                    textAnchor={label.textAnchor}
                  >
                    {label.text}
                  </text>
                </g>
              ))}
            </g>
          ) : null}
        </svg>
        {paneHandles.map((handle) => (
          <Button
            key={handle.id}
            type="button"
            variant="ghost"
            size="sm"
            className="fresh-chart__pane-handle"
            style={{ top: `${(handle.top / effectiveHeight) * 100}%` }}
            onPointerDown={(event) => handlePaneResizeStart(event, handle.upperKey, handle.lowerKey)}
            aria-label={`Resize ${handle.upperKey} and ${handle.lowerKey} panes`}
          >
            <span className="fresh-chart__pane-handle-line" />
          </Button>
        ))}
      </div>
    </div>
  )
}
