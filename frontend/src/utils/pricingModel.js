export const PUBLIC_PLAN_KEYS = ['starter', 'pro', 'professional', 'team', 'enterprise']

export const STATIC_PRICING_PLANS = {
  items: [
    {
      key: 'starter',
      name: 'Starter',
      tagline: 'Manual live trading plus paper automation for one strategy lane.',
      monthly_price: 99,
      annual_price: 990,
      seats_label: 'Up to 2 members',
      cta_label: 'Start controlled',
      public: true,
      recommended: false,
      display_order: 10,
      live_mode: 'Manual live trading',
      target_persona: 'Solo operator proving one paper-first strategy lane.',
      billing_pitch: 'Low-cost entry into the control plane without live automation.',
      support_model: 'Standard support',
      featured_capabilities: ['Manual live trading', 'Paper automation', 'One strategy lane', 'No live automation'],
      proof_points: ['Manual order flow', 'Paper automation lane', 'Basic evidence trail', 'Risk gates visible'],
    },
    {
      key: 'pro',
      name: 'Pro',
      tagline: 'Assisted live trading with approval required for every live order.',
      monthly_price: 299,
      annual_price: 2990,
      seats_label: 'Up to 5 members',
      cta_label: 'Upgrade to Pro',
      public: true,
      recommended: false,
      display_order: 20,
      live_mode: 'Assisted live trading',
      target_persona: 'Operator who wants approval-led live trading and basic risk controls.',
      billing_pitch: 'Assisted control for every order without granting live automation.',
      support_model: 'Standard support',
      featured_capabilities: ['Assisted live trading', 'Approval required', 'Basic risk limits', '3 strategies'],
      proof_points: ['Approval queue', 'Risk limits', 'Order evidence', 'Paper automation'],
    },
    {
      key: 'professional',
      name: 'Professional',
      tagline: 'Supervised live automation with readiness gates, risk, replay, and execution evidence.',
      monthly_price: 499,
      annual_price: 4990,
      seats_label: 'Up to 10 members',
      cta_label: 'Recommended',
      public: true,
      recommended: true,
      display_order: 30,
      live_mode: 'Supervised automation',
      target_persona: 'Professional trader or small desk moving from paper evidence to supervised live controls.',
      billing_pitch: 'The $499 tier sells proof, gates, and control instead of commodity connectivity.',
      support_model: 'Priority support',
      featured_capabilities: ['Supervised live automation', 'Readiness gates', 'Risk engine', 'Audit replay', 'Execution quality', 'Kill switch', 'Versioning', '10 strategies'],
      proof_points: ['Readiness gates', 'Risk engine', 'Audit replay', 'Execution quality'],
    },
    {
      key: 'team',
      name: 'Team',
      tagline: 'Multi-user approvals, multi-account live control, roles, and team audit logs.',
      monthly_price: 899,
      annual_price: 8990,
      seats_label: 'Up to 20 members',
      cta_label: 'Scale the desk',
      public: true,
      recommended: false,
      display_order: 40,
      live_mode: 'Team-controlled automation',
      target_persona: 'Small professional desk with reviewers, operators, and multiple linked accounts.',
      billing_pitch: 'Adds the operating workflow needed when more than one person can approve risk.',
      support_model: 'Priority support',
      featured_capabilities: ['Multi-user approvals', 'Multi-account live control', 'Role permissions', 'Team audit logs', 'Priority support', '25 strategies'],
      proof_points: ['Multi-user approvals', 'Multi-account controls', 'Team audit logs', 'Priority support'],
    },
    {
      key: 'enterprise',
      name: 'Enterprise',
      tagline: 'Custom policies, reporting, retention, and dedicated support for control-plane rollouts.',
      monthly_price: 2499,
      annual_price: 24990,
      seats_label: 'Custom users',
      cta_label: 'Talk to sales',
      public: true,
      recommended: false,
      display_order: 50,
      live_mode: 'Custom control plane',
      target_persona: 'Firm or partner that needs custom controls and reporting around live automation.',
      billing_pitch: 'For teams that need custom control-plane policy, retention, and support commitments.',
      support_model: 'Dedicated support',
      price_prefix: 'from',
      featured_capabilities: ['White-label control plane', 'Custom policies', 'Custom reporting', 'Custom retention', 'Dedicated support'],
      proof_points: ['Custom policies', 'Custom reporting', 'Custom retention', 'Dedicated support'],
    },
  ],
}

export const LIVE_MODE_STEPS = [
  {
    key: 'manual-live',
    label: 'Manual live',
    copy: 'The user places trades manually through the UI with account and risk context visible.',
  },
  {
    key: 'assisted-live',
    label: 'Assisted live',
    copy: 'The system can stage a live order idea, but the user must approve before submission.',
  },
  {
    key: 'supervised-automation',
    label: 'Supervised automation',
    copy: 'A signed authorization, readiness gates, risk policy, and kill switch bound every live session.',
  },
  {
    key: 'managed-automation',
    label: 'Managed automation',
    copy: 'Disabled and off-roadmap for launch because it requires heavier compliance review.',
    disabled: true,
  },
]

export const PRICING_COMPARISON_ROWS = [
  {
    label: 'Manual live trading',
    values: { starter: 'Included', pro: 'Included', professional: 'Included', team: 'Included', enterprise: 'Custom' },
  },
  {
    label: 'Paper automation',
    values: { starter: 'Included', pro: 'Included', professional: 'Included', team: 'Included', enterprise: 'Custom' },
  },
  {
    label: 'Assisted live approvals',
    values: { starter: '-', pro: 'Every order', professional: 'Policy based', team: 'Multi-user', enterprise: 'Custom' },
  },
  {
    label: 'Supervised live automation',
    values: { starter: '-', pro: '-', professional: 'Included', team: 'Included', enterprise: 'Custom' },
  },
  {
    label: 'Risk engine and kill switch',
    values: { starter: 'Basic', pro: 'Basic', professional: 'Full', team: 'Full', enterprise: 'Custom' },
  },
  {
    label: 'Audit replay and exports',
    values: { starter: '-', pro: 'Basic', professional: 'Included', team: 'Team logs', enterprise: 'Custom' },
  },
  {
    label: 'Execution quality evidence',
    values: { starter: '-', pro: 'Summary', professional: 'Full', team: 'Full', enterprise: 'Custom' },
  },
  {
    label: 'Strategy lanes',
    values: { starter: '1', pro: '3', professional: '10', team: '25', enterprise: 'Custom' },
  },
  {
    label: 'Support model',
    values: { starter: 'Standard', pro: 'Standard', professional: 'Priority', team: 'Priority', enterprise: 'Dedicated' },
  },
]

export function formatPlanMoney(amount) {
  const numeric = Number(amount)
  if (!Number.isFinite(numeric) || numeric <= 0) return 'Custom'
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(numeric)
}

export function formatPlanPrice(plan, cycle = 'monthly') {
  const amount = cycle === 'annual' ? plan?.annual_price : plan?.monthly_price
  const formatted = formatPlanMoney(amount)
  const prefix = plan?.price_prefix ? `${plan.price_prefix} ` : ''
  const suffix = formatted === 'Custom' ? '' : cycle === 'annual' ? '/yr' : '/mo'
  return `${prefix}${formatted}${suffix}`.trim()
}

export function normalizePricingPlan(plan = {}) {
  const fallback = STATIC_PRICING_PLANS.items.find((item) => item.key === plan.key) || {}
  const legacyPlanPayload = fallback.key && plan.public === undefined
  const merged = legacyPlanPayload ? { ...plan, ...fallback } : { ...fallback, ...plan }
  return {
    ...merged,
    featured_capabilities: Array.isArray(merged.featured_capabilities)
      ? merged.featured_capabilities
      : fallback.featured_capabilities || [],
    proof_points: Array.isArray(merged.proof_points) ? merged.proof_points : fallback.proof_points || [],
    public: merged.public ?? fallback.public ?? PUBLIC_PLAN_KEYS.includes(merged.key),
    recommended: Boolean(merged.recommended ?? fallback.recommended),
    display_order: Number(merged.display_order ?? fallback.display_order ?? 100),
  }
}

export function normalizePricingPlans(payload) {
  const sourceItems = Array.isArray(payload?.items) && payload.items.length ? payload.items : STATIC_PRICING_PLANS.items
  return sourceItems
    .map(normalizePricingPlan)
    .filter((plan) => plan.public && PUBLIC_PLAN_KEYS.includes(plan.key))
    .sort((left, right) => left.display_order - right.display_order)
}
