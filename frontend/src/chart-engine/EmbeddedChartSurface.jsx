import CustomMarketChart from '../components/CustomMarketChart'

/**
 * Stable embeddable chart surface for buyer evaluation and external reuse.
 *
 * This wraps the workstation chart component while constraining the prop names
 * to a narrower, externally-documented contract.
 */
export default function EmbeddedChartSurface({
  payload,
  ticker = '',
  interval = '5m',
  height = 620,
  livePrice = null,
  selectedPrice = null,
  accent = '#7a7a7a',
  chartStyle = 'candles',
  label = 'Embedded chart',
  workingOrder = null,
  pendingGuidePoint = null,
  positionMarkers = [],
  guides = [],
  hiddenOverlays = {},
  savedViewport = null,
  onSelectionChange,
  onChartAction,
  onPayloadRecovered,
  onViewportChange,
  onLayoutReset,
  className = '',
  embedId = '',
}) {
  const nextTicker = String(ticker || payload?.ticker || '').trim().toUpperCase()
  const nextInterval = String(interval || payload?.interval || '5m').trim()
  const rootClassName = ['chart-embed-surface', className].filter(Boolean).join(' ')

  return (
    <div
      className={rootClassName}
      data-chart-embed-root
      data-chart-embed-id={embedId || undefined}
    >
      <CustomMarketChart
        payload={payload}
        ticker={nextTicker}
        interval={nextInterval}
        livePrice={livePrice}
        selectedPrice={selectedPrice}
        onPriceSelect={onSelectionChange}
        onChartAction={onChartAction}
        onPayloadRecovered={onPayloadRecovered}
        height={height}
        tickerAccent={accent}
        chartStyle={chartStyle}
        autoRefreshLabel={label}
        workingOrder={workingOrder}
        pendingGuidePoint={pendingGuidePoint}
        positionMarkers={Array.isArray(positionMarkers) ? positionMarkers : []}
        customGuides={Array.isArray(guides) ? guides : []}
        hiddenOverlays={hiddenOverlays && typeof hiddenOverlays === 'object' ? hiddenOverlays : {}}
        savedViewport={savedViewport}
        onViewportChange={onViewportChange}
        onResetLayout={onLayoutReset}
      />
    </div>
  )
}
