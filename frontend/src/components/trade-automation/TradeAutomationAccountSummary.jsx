import MetricCard from '../MetricCard'

function formatMoney(value) {
  const numberValue = Number(value)
  if (!Number.isFinite(numberValue)) return '--'
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(numberValue)
}

function humanizeValue(value, fallback = '--') {
  const text = String(value || '').trim()
  return text ? text.replaceAll('_', ' ') : fallback
}

export default function TradeAutomationAccountSummary({ summary = null, profileKey = '' }) {
  if (!summary) return null
  return (
    <section className="metrics-grid metrics-grid--compact">
      <MetricCard label="Equity" value={formatMoney(summary.equity ?? summary.portfolio_value)} />
      <MetricCard label="Cash" value={formatMoney(summary.cash)} />
      <MetricCard label="Buying power" value={formatMoney(summary.buying_power)} />
      <MetricCard label="Profile" value={humanizeValue(profileKey)} />
    </section>
  )
}
