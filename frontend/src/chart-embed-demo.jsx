import { useState } from 'react'
import ReactDOM from 'react-dom/client'
import {
  EmbeddedChartSurface,
  buildChartDemoPayload,
} from './chart-engine/react.js'
import './styles/foundation.css'
import './styles/primitives.css'
import './styles.css'

const payload = buildChartDemoPayload({
  ticker: 'QQQ',
  interval: '15m',
  period: '3d',
  count: 240,
  intervalMinutes: 15,
})

const lastClose = payload.candles.at(-1)?.close ?? 0
const defaultSelection = Number((lastClose - 0.74).toFixed(4))

function formatJson(value) {
  return JSON.stringify(value, null, 2)
}

function ChartEmbedDemoPage() {
  const [selectedPoint, setSelectedPoint] = useState(null)
  const [viewportState, setViewportState] = useState(null)

  return (
    <main
      style={{
        minHeight: '100vh',
        background:
          'linear-gradient(180deg, rgba(6, 6, 6, 0.98) 0%, rgba(9, 9, 9, 1) 100%)',
        color: '#f4f7ff',
        padding: '28px 22px 48px',
      }}
    >
      <section
        style={{
          maxWidth: '1560px',
          margin: '0 auto',
          display: 'grid',
          gap: '18px',
        }}
      >
        <header style={{ display: 'grid', gap: '8px' }}>
          <div
            style={{
              fontSize: '12px',
              letterSpacing: '0.16em',
              textTransform: 'uppercase',
              color: '#9ec5ff',
            }}
          >
            Standalone embed contract
          </div>
          <h1 style={{ margin: 0, fontSize: '34px', lineHeight: 1.1 }}>
            Embedded chart surface demo
          </h1>
          <p style={{ margin: 0, maxWidth: '960px', color: '#b9c9e7', fontSize: '15px', lineHeight: 1.6 }}>
            This page shows the narrow public chart contract that can be reused outside the desk shell. It uses the
            exported embed surface directly and captures selection and viewport callbacks in a plain host page.
          </p>
        </header>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'minmax(0, 1fr) 320px',
            gap: '18px',
            alignItems: 'start',
          }}
        >
          <EmbeddedChartSurface
            embedId="chart-demo"
            payload={payload}
            ticker={payload.ticker}
            interval={payload.interval}
            height={760}
            livePrice={lastClose}
            selectedPrice={defaultSelection}
            label="Standalone embed demo"
            pendingGuidePoint={{ price: Number((lastClose - 0.32).toFixed(4)) }}
            positionMarkers={[
              {
                entryPrice: Number((lastClose - 1.24).toFixed(4)),
                stopPrice: Number((lastClose - 2.86).toFixed(4)),
                targetPrice: Number((lastClose + 2.18).toFixed(4)),
              },
            ]}
            guides={[
              { type: 'hline', price: Number((lastClose + 1.12).toFixed(4)), label: 'Breakout reference' },
            ]}
            workingOrder={{
              orderType: 'limit',
              timeInForce: 'day',
              executionPrice: Number((lastClose - 0.48).toFixed(4)),
              limitPrice: Number((lastClose - 0.33).toFixed(4)),
              stopPrice: Number((lastClose - 1.92).toFixed(4)),
            }}
            onSelectionChange={setSelectedPoint}
            onViewportChange={setViewportState}
          />

          <aside
            style={{
              display: 'grid',
              gap: '14px',
            }}
          >
            <section
              style={{
                border: '1px solid rgba(120, 151, 203, 0.18)',
                borderRadius: '18px',
                padding: '16px',
                background: 'rgba(11, 18, 31, 0.86)',
                boxShadow: '0 24px 48px rgba(0, 0, 0, 0.24)',
              }}
            >
              <h2 style={{ margin: 0, fontSize: '14px', letterSpacing: '0.08em', textTransform: 'uppercase', color: '#9ec5ff' }}>
                Host callbacks
              </h2>
              <p style={{ margin: '10px 0 0', color: '#b9c9e7', fontSize: '14px', lineHeight: 1.5 }}>
                The embed surface only exposes host-level callbacks. No shell routing, auth, or workspace state is required.
              </p>
            </section>

            <section
              style={{
                border: '1px solid rgba(120, 151, 203, 0.18)',
                borderRadius: '18px',
                padding: '16px',
                background: 'rgba(11, 18, 31, 0.86)',
              }}
            >
              <div style={{ fontSize: '12px', letterSpacing: '0.12em', textTransform: 'uppercase', color: '#9ec5ff' }}>
                Selection event
              </div>
              <pre
                style={{
                  margin: '10px 0 0',
                  color: '#d8e4ff',
                  fontSize: '12px',
                  lineHeight: 1.45,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {selectedPoint ? formatJson(selectedPoint) : 'Select a price level in the chart to populate the host callback.'}
              </pre>
            </section>

            <section
              style={{
                border: '1px solid rgba(120, 151, 203, 0.18)',
                borderRadius: '18px',
                padding: '16px',
                background: 'rgba(11, 18, 31, 0.86)',
              }}
            >
              <div style={{ fontSize: '12px', letterSpacing: '0.12em', textTransform: 'uppercase', color: '#9ec5ff' }}>
                Viewport contract
              </div>
              <pre
                style={{
                  margin: '10px 0 0',
                  color: '#d8e4ff',
                  fontSize: '12px',
                  lineHeight: 1.45,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {viewportState ? formatJson(viewportState) : 'Pan, zoom, or resize panes to populate the host viewport callback.'}
              </pre>
            </section>
          </aside>
        </div>
      </section>
    </main>
  )
}

ReactDOM.createRoot(document.getElementById('root')).render(<ChartEmbedDemoPage />)
