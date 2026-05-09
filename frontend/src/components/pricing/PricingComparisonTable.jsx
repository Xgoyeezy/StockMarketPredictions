import { PRICING_COMPARISON_ROWS } from '../../utils/pricingModel'

export default function PricingComparisonTable({ plans = [] }) {
  const visiblePlans = plans.length ? plans : []

  return (
    <div className="pricing-comparison" aria-label="Plan comparison">
      <div className="pricing-comparison__row pricing-comparison__row--head">
        <div>Capability</div>
        {visiblePlans.map((plan) => (
          <div key={plan.key}>{plan.name}</div>
        ))}
      </div>

      {PRICING_COMPARISON_ROWS.map((row) => (
        <div key={row.label} className="pricing-comparison__row">
          <div>{row.label}</div>
          {visiblePlans.map((plan) => (
            <div key={`${row.label}:${plan.key}`}>{row.values?.[plan.key] || '-'}</div>
          ))}
        </div>
      ))}
    </div>
  )
}
