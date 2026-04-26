import { useMemo } from 'react'
import EmptyState from './EmptyState'
import { formatInlineMeta } from './InlineMeta'

function toNumber(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function formatAxisValue(value) {
  if (!Number.isFinite(value)) return '0.00'
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

function formatPointLabel(value) {
  return value || 'Point'
}

export default function EquityCurveChart({ points = [] }) {
  const normalizedPoints = useMemo(
    () =>
      (Array.isArray(points) ? points : [])
        .map((point, index) => {
          const value =
            toNumber(point?.equity) ??
            toNumber(point?.cumulative_pnl) ??
            toNumber(point?.total_pnl) ??
            0
          return {
            id: point?.close_time || point?.time || point?.index || `point-${index}`,
            label: point?.close_time || point?.time || `Trade ${index + 1}`,
            value,
          }
        })
        .filter((point) => Number.isFinite(point.value)),
    [points],
  )

  const chartModel = useMemo(() => {
    if (!normalizedPoints.length) return null

    const width = 1000
    const height = 360
    const padding = { top: 26, right: 24, bottom: 32, left: 54 }
    const plotWidth = width - padding.left - padding.right
    const plotHeight = height - padding.top - padding.bottom

    const values = normalizedPoints.map((point) => point.value)
    const minValue = Math.min(...values)
    const maxValue = Math.max(...values)
    const range = Math.max(maxValue - minValue, Math.max(Math.abs(maxValue), Math.abs(minValue), 1) * 0.08, 1)
    const domainMin = minValue - range * 0.12
    const domainMax = maxValue + range * 0.12
    const domainRange = Math.max(domainMax - domainMin, 1)

    const pointsWithCoords = normalizedPoints.map((point, index) => {
      const x =
        normalizedPoints.length === 1
          ? padding.left + plotWidth / 2
          : padding.left + (plotWidth * index) / Math.max(normalizedPoints.length - 1, 1)
      const y = padding.top + ((domainMax - point.value) / domainRange) * plotHeight
      return { ...point, x, y }
    })

    const linePath = pointsWithCoords
      .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
      .join(' ')
    const areaPath = `${linePath} L ${pointsWithCoords.at(-1)?.x.toFixed(2)} ${(height - padding.bottom).toFixed(2)} L ${pointsWithCoords[0]?.x.toFixed(2)} ${(height - padding.bottom).toFixed(2)} Z`

    const latestPoint = pointsWithCoords.at(-1)
    const firstPoint = pointsWithCoords[0]
    const delta = latestPoint && firstPoint ? latestPoint.value - firstPoint.value : 0
    const deltaPct = firstPoint?.value ? (delta / firstPoint.value) * 100 : null
    const tone = delta > 0 ? 'positive' : delta < 0 ? 'negative' : 'neutral'

    const yTicks = Array.from({ length: 5 }, (_, index) => {
      const ratio = index / 4
      const value = domainMax - domainRange * ratio
      const y = padding.top + plotHeight * ratio
      return { value, y }
    })

    return {
      width,
      height,
      padding,
      plotBottom: height - padding.bottom,
      pointsWithCoords,
      linePath,
      areaPath,
      latestPoint,
      firstPoint,
      delta,
      deltaPct,
      tone,
      yTicks,
      minValue,
      maxValue,
    }
  }, [normalizedPoints])

  if (!chartModel) {
    return <EmptyState title="No equity history" description="Run or close trades to build the portfolio curve." />
  }

  const { width, height, plotBottom, pointsWithCoords, linePath, areaPath, latestPoint, delta, deltaPct, tone, yTicks, minValue, maxValue } = chartModel

  return (
    <div className="equity-curve-chart">
      <div className="equity-curve-chart__meta">
        <span className={`equity-curve-chart__chip equity-curve-chart__chip--${tone}`}>
          Net {delta >= 0 ? '+' : ''}{formatAxisValue(delta)}
          {Number.isFinite(deltaPct) ? ` (${deltaPct >= 0 ? '+' : ''}${deltaPct.toFixed(2)}%)` : ''}
        </span>
        <span className="equity-curve-chart__chip">High {formatAxisValue(maxValue)}</span>
        <span className="equity-curve-chart__chip">Low {formatAxisValue(minValue)}</span>
        <span className="equity-curve-chart__chip">
          Latest {latestPoint ? formatAxisValue(latestPoint.value) : '0.00'}
        </span>
      </div>
      <svg
        className="equity-curve-chart__svg"
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label="Portfolio equity curve"
      >
        <defs>
          <linearGradient id="equity-curve-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgba(96, 96, 96, 0.34)" />
            <stop offset="100%" stopColor="rgba(96, 96, 96, 0.02)" />
          </linearGradient>
        </defs>

        {yTicks.map((tick) => (
          <g key={tick.y}>
            <line
              x1="54"
              x2={width - 24}
              y1={tick.y}
              y2={tick.y}
              className="equity-curve-chart__grid-line"
            />
            <text x="12" y={tick.y + 4} className="equity-curve-chart__axis-label">
              {formatAxisValue(tick.value)}
            </text>
          </g>
        ))}

        <path d={areaPath} fill="url(#equity-curve-fill)" className="equity-curve-chart__area" />
        <path d={linePath} className={`equity-curve-chart__line equity-curve-chart__line--${tone}`} />

        {pointsWithCoords.map((point, index) => (
          <g key={point.id}>
            <circle
              cx={point.x}
              cy={point.y}
              r={index === pointsWithCoords.length - 1 ? 4.5 : 3}
              className={`equity-curve-chart__point equity-curve-chart__point--${tone}`}
            />
            {index === pointsWithCoords.length - 1 ? (
              <>
                <line
                  x1={point.x}
                  x2={point.x}
                  y1="26"
                  y2={plotBottom}
                  className="equity-curve-chart__focus-line"
                />
                <text
                  x={Math.min(point.x + 10, width - 160)}
                  y={Math.max(point.y - 12, 20)}
                  className="equity-curve-chart__focus-label"
                >
                  {formatInlineMeta([formatPointLabel(point.label), formatAxisValue(point.value)])}
                </text>
              </>
            ) : null}
          </g>
        ))}

        <text x="54" y={height - 8} className="equity-curve-chart__axis-label">
          {formatPointLabel(pointsWithCoords[0]?.label)}
        </text>
        <text x={width - 24} y={height - 8} textAnchor="end" className="equity-curve-chart__axis-label">
          {formatPointLabel(pointsWithCoords.at(-1)?.label)}
        </text>
      </svg>
    </div>
  )
}
