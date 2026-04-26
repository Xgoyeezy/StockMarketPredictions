export default function WorkspaceSummary({ analysis }) {
  const plan = analysis?.report?.option_plan || {}
  const items = [
    ['Direction', analysis?.report?.verdict || '—'],
    ['Decision', analysis?.report?.trade_decision || '—'],
    ['Entry', plan.entry_price || analysis?.live_price || '—'],
    ['Target', plan.expected_underlying_target || '—'],
    ['Stop', plan.stop_loss || '—'],
    ['Confidence', analysis?.report?.conviction_label || '—'],
  ]

  return (
    <div className="workspace-summary-grid">
      {items.map(([label, value]) => (
        <div className="workspace-summary-card" key={label}>
          <span>{label}</span>
          <strong>{String(value)}</strong>
        </div>
      ))}
    </div>
  )
}
