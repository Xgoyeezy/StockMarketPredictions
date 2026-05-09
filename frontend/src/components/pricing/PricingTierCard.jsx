import { NavLink } from 'react-router-dom'
import Button from '../Button'
import StatusBadge from '../StatusBadge'
import { formatPlanPrice } from '../../utils/pricingModel'

function joinClasses(...values) {
  return values.filter(Boolean).join(' ')
}

export default function PricingTierCard({
  plan,
  billingCycle = 'monthly',
  isActive = false,
  busy = false,
  disabled = false,
  actionLabel = '',
  actionTo = '',
  onAction,
}) {
  if (!plan) return null

  const ctaLabel = actionLabel || (isActive ? 'Current plan' : plan.cta_label || 'Choose plan')
  const actionDisabled = disabled || busy || isActive

  return (
    <article
      className={joinClasses(
        'pricing-tier-card',
        plan.recommended && 'pricing-tier-card--featured',
        isActive && 'pricing-tier-card--active',
      )}
    >
      <div className="pricing-tier-card__top">
        <div>
          <div className="pricing-tier-card__eyebrow">{plan.live_mode || 'Control plane'}</div>
          <h3>{plan.name}</h3>
        </div>
        {plan.recommended ? <StatusBadge tone="warning">Recommended</StatusBadge> : null}
        {isActive ? <StatusBadge tone="positive">Current</StatusBadge> : null}
      </div>

      <p className="pricing-tier-card__tagline">{plan.tagline}</p>

      <div className="pricing-tier-card__price">
        <strong>{formatPlanPrice(plan, billingCycle)}</strong>
        <span>{billingCycle === 'annual' ? 'Annual pricing is 10x monthly.' : plan.seats_label}</span>
      </div>

      <p className="pricing-tier-card__pitch">{plan.billing_pitch}</p>

      <ul className="pricing-tier-card__features">
        {(plan.featured_capabilities || []).slice(0, 8).map((feature) => (
          <li key={feature}>{feature}</li>
        ))}
      </ul>

      <div className="pricing-tier-card__proof">
        {(plan.proof_points || []).slice(0, 4).map((point) => (
          <span key={point}>{point}</span>
        ))}
      </div>

      {onAction ? (
        <Button type="button" variant={plan.recommended ? 'solid' : 'ghost'} disabled={actionDisabled} onClick={() => onAction(plan.key)}>
          {busy ? 'Starting checkout...' : ctaLabel}
        </Button>
      ) : (
        <NavLink className={joinClasses('pricing-tier-card__cta', plan.recommended && 'pricing-tier-card__cta--featured')} to={actionTo || '/login'}>
          {ctaLabel}
        </NavLink>
      )}
    </article>
  )
}
