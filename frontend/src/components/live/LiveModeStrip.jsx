import { LIVE_MODE_STEPS } from '../../utils/pricingModel'

export default function LiveModeStrip({ activeKey = 'assisted-live' }) {
  return (
    <section className="pricing-mode-strip live-mode-strip" aria-label="Live trading modes">
      {LIVE_MODE_STEPS.map((item) => (
        <article
          key={item.key}
          className={`pricing-mode-card live-mode-card${item.disabled ? ' pricing-mode-card--disabled live-mode-card--disabled' : ''}${item.key === activeKey ? ' live-mode-card--active' : ''}`}
        >
          <span>{item.key === activeKey ? 'Current path' : item.disabled ? 'Disabled' : 'Available'}</span>
          <h2>{item.label}</h2>
          <p>{item.copy}</p>
        </article>
      ))}
    </section>
  )
}
