import ReactDOM from 'react-dom/client'
import CandlestickChart from './components/CandlestickChart'
import { buildChartDemoPayload } from './chart-engine/demoPayload'
import './styles/foundation.css'
import './styles/primitives.css'
import './styles.css'

const payload = buildChartDemoPayload({
  ticker: 'SPY',
  interval: '5m',
  period: '5d',
  count: 360,
  intervalMinutes: 5,
})

const lastClose = payload.candles.at(-1)?.close ?? 0

const workingOrder = {
  orderType: 'limit',
  timeInForce: 'day',
  executionPrice: Number((lastClose - 0.38).toFixed(4)),
  limitPrice: Number((lastClose - 0.26).toFixed(4)),
  stopPrice: Number((lastClose - 1.74).toFixed(4)),
}

const positionMarkers = [
  {
    entryPrice: Number((lastClose - 2.16).toFixed(4)),
    stopPrice: Number((lastClose - 3.48).toFixed(4)),
    targetPrice: Number((lastClose + 2.84).toFixed(4)),
  },
]

const customGuides = [
  { type: 'hline', price: Number((lastClose + 1.42).toFixed(4)), label: 'Liquidity sweep' },
  { type: 'hline', price: Number((lastClose - 1.18).toFixed(4)), label: 'Session VWAP reclaim' },
]

function ChartDemoPage() {
  return (
    <main
      data-chart-demo-page
      style={{
        minHeight: '100vh',
        background:
          'radial-gradient(circle at top, rgba(58,58,58,0.24), rgba(8,8,8,0.96) 42%), linear-gradient(180deg, #080808 0%, #060606 100%)',
        color: '#f4f7ff',
        padding: '32px 24px 48px',
      }}
    >
      <section
        data-chart-demo-root
        style={{
          maxWidth: '1480px',
          margin: '0 auto',
          display: 'grid',
          gap: '18px',
        }}
      >
        <header
          style={{
            display: 'grid',
            gap: '8px',
          }}
        >
          <div
            style={{
              fontSize: '12px',
              letterSpacing: '0.16em',
              textTransform: 'uppercase',
              color: '#9ec5ff',
            }}
          >
            Browser regression fixture
          </div>
          <h1 style={{ margin: 0, fontSize: '34px', lineHeight: 1.1 }}>
            Chart engine render proof
          </h1>
          <p style={{ margin: 0, maxWidth: '920px', color: '#b9c9e7', fontSize: '15px', lineHeight: 1.6 }}>
            Deterministic synthetic payload with overlays, order markers, and lower-pane indicators. This page exists only
            to prove browser rendering stability for the sale pack.
          </p>
        </header>
        <CandlestickChart
          payload={payload}
          ticker={payload.ticker}
          interval={payload.interval}
          height={780}
          livePrice={lastClose}
          selectedPrice={Number((lastClose - 0.92).toFixed(4))}
          autoRefreshLabel="Browser render fixture"
          workingOrder={workingOrder}
          pendingGuidePoint={{ price: Number((lastClose - 0.58).toFixed(4)) }}
          positionMarkers={positionMarkers}
          customGuides={customGuides}
        />
      </section>
    </main>
  )
}

ReactDOM.createRoot(document.getElementById('root')).render(<ChartDemoPage />)
