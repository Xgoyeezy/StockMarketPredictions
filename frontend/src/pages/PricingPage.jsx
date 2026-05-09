import { useEffect, useMemo, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { getBillingPlans } from '../api/client'
import PricingComparisonTable from '../components/pricing/PricingComparisonTable'
import PricingTierCard from '../components/pricing/PricingTierCard'
import {
  LIVE_MODE_STEPS,
  STATIC_PRICING_PLANS,
  normalizePricingPlans,
} from '../utils/pricingModel'
import {
  getPublicSiteBranding,
  getPublicSitePages,
} from '../utils/publicSiteModel'

function joinClasses(...values) {
  return values.filter(Boolean).join(' ')
}

export default function PricingPage() {
  const branding = useMemo(() => getPublicSiteBranding(), [])
  const navItems = useMemo(() => getPublicSitePages(), [])
  const [billingCycle, setBillingCycle] = useState('monthly')
  const [plansPayload, setPlansPayload] = useState(STATIC_PRICING_PLANS)
  const plans = useMemo(() => normalizePricingPlans(plansPayload), [plansPayload])

  useEffect(() => {
    if (typeof document !== 'undefined') {
      document.title = `Pricing | ${branding.name}`
    }

    let cancelled = false
    getBillingPlans()
      .then((payload) => {
        if (!cancelled) setPlansPayload(payload)
      })
      .catch(() => {
        if (!cancelled) setPlansPayload(STATIC_PRICING_PLANS)
      })

    return () => {
      cancelled = true
    }
  }, [branding.name])

  return (
    <div className="pricing-page">
      <header className="pricing-public-header">
        <NavLink to="/connect" className="pricing-public-header__brand">
          <span className="pricing-public-header__mark" aria-hidden="true">
            {branding.name
              .split(/\s+/)
              .filter(Boolean)
              .slice(0, 2)
              .map((part) => part[0]?.toUpperCase() || '')
              .join('')}
          </span>
          <span>
            <strong>{branding.name}</strong>
            <small>{branding.tagline}</small>
          </span>
        </NavLink>
        <nav className="pricing-public-header__nav" aria-label="Public pages">
          {navItems.map((item) => (
            <NavLink
              key={item.key}
              to={item.path}
              className={({ isActive }) =>
                joinClasses('pricing-public-header__nav-link', isActive && 'pricing-public-header__nav-link--active')
              }
            >
              {item.navLabel}
            </NavLink>
          ))}
        </nav>
      </header>

      <main className="pricing-page__main">
        <section className="pricing-hero">
          <div className="pricing-hero__copy">
            <div className="pricing-eyebrow">Quant Evidence Operating System</div>
            <h1>Paper-validated trading decisions with proof.</h1>
            <p>
              Paper-validated live automation control for serious desks: opportunity capture,
              missed-move intelligence, AI evidence review, risk allocation, audit replay, and
              execution evidence above connected brokers.
            </p>
            <div className="pricing-hero__actions">
              <NavLink to="/login" className="pricing-hero__primary-cta">Sign in to upgrade</NavLink>
              <NavLink to="/live" className="pricing-hero__secondary-cta">Open live console</NavLink>
            </div>
          </div>

          <div className="pricing-proof-panel">
            <div className="pricing-proof-panel__headline">
              <span>Operational proof</span>
              <strong>Every decision must show why it happened or why it stood down.</strong>
            </div>
            <div className="pricing-proof-grid">
              {[
                'Candidate lifecycle ID',
                'Opportunity score and blocker',
                'AI evidence verdict',
                'Risk allocation and heat',
                'Paper receipt and reconciliation',
                'Missed-move follow-up',
              ].map((item) => (
                <div key={item}>{item}</div>
              ))}
            </div>
          </div>
        </section>

        <section className="pricing-mode-strip" aria-label="Live trading modes">
          {LIVE_MODE_STEPS.map((mode, index) => (
            <article key={mode.key} className={joinClasses('pricing-mode-card', mode.disabled && 'pricing-mode-card--disabled')}>
              <span>{String(index + 1).padStart(2, '0')}</span>
              <h2>{mode.label}</h2>
              <p>{mode.copy}</p>
            </article>
          ))}
        </section>

        <section className="pricing-section pricing-section--plans">
          <div className="pricing-section__header">
            <div>
              <div className="pricing-eyebrow">Plans</div>
              <h2>Price the control plane, not commodity connectivity.</h2>
              <p>
                Commodity developer access is cheap. The Professional tier earns the $499 price by
                packaging supervised automation, risk gates, evidence, and support.
              </p>
            </div>
            <div className="pricing-cycle-toggle" role="group" aria-label="Billing cycle">
              {['monthly', 'annual'].map((cycle) => (
                <button
                  key={cycle}
                  type="button"
                  className={joinClasses('pricing-cycle-toggle__button', billingCycle === cycle && 'pricing-cycle-toggle__button--active')}
                  onClick={() => setBillingCycle(cycle)}
                >
                  {cycle === 'monthly' ? 'Monthly' : 'Annual'}
                </button>
              ))}
            </div>
          </div>

          <div className="pricing-tier-grid">
            {plans.map((plan) => (
              <PricingTierCard
                key={plan.key}
                plan={plan}
                billingCycle={billingCycle}
                actionTo={plan.key === 'enterprise' ? '/connect' : '/login'}
                actionLabel={plan.key === 'enterprise' ? 'Talk to sales' : plan.cta_label}
              />
            ))}
          </div>
        </section>

        <section className="pricing-section">
          <div className="pricing-section__header">
            <div>
              <div className="pricing-eyebrow">Compare</div>
              <h2>Built around live-mode responsibility.</h2>
              <p>
                Manual live is the entry point. Assisted live adds approvals. Supervised automation starts at Professional.
                Managed automation stays disabled until the operating and compliance model is ready.
              </p>
            </div>
          </div>
          <PricingComparisonTable plans={plans} />
        </section>

        <section className="pricing-section pricing-section--evidence">
          <div className="pricing-proof-column">
            <div className="pricing-eyebrow">Why teams pay</div>
              <h2>Evidence, risk, workflow, and support justify premium ARPU.</h2>
              <p>
              The product is not sold as broker API access. It is sold as the operating layer that
              keeps paper validation, missed-opportunity review, AI evidence, pre-trade risk, kill
              switches, replay records, and execution-quality proof in one workflow.
              </p>
          </div>
          <div className="pricing-proof-list">
            {[
              'Opportunity Capture Graph across every scanned symbol',
              'Missed-Move Intelligence after rejected setups',
              'AI Evidence Referee in shadow mode first',
              'Institutional risk allocator before order evidence',
              'Execution packets with receipt and reconciliation proof',
              'Market-day reports for operator and customer review',
            ].map((item) => (
              <div key={item}>{item}</div>
            ))}
          </div>
        </section>

        <section className="pricing-section pricing-section--evidence">
          <div className="pricing-proof-column">
            <div className="pricing-eyebrow">Daily outputs</div>
            <h2>What a customer receives every market day.</h2>
            <p>
              The platform turns the trading day into proof artifacts: what scanned, what qualified,
              what was blocked, what AI reviewed, which desk deserved capital, and how paper orders
              reconciled. The 1-2% weekly objective is tracked as an operating target, not a guarantee.
            </p>
          </div>
          <div className="pricing-proof-list">
            {[
              'Market Session Commander readiness proof',
              'No-trade escalation and missed-move leaderboard',
              'AI referee shadow-vs-outcome report',
              'Desk SLA and capital allocation report',
              'Alpaca paper reconciliation console',
              'Execution-quality and order evidence packets',
              'Close-of-day market report and audit ledger',
            ].map((item) => (
              <div key={item}>{item}</div>
            ))}
          </div>
        </section>
      </main>
    </div>
  )
}
