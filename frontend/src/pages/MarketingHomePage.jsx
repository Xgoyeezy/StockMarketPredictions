import { useEffect, useMemo, useRef, useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import Button from '../components/Button'
import Chip from '../components/Chip'
import MarketingTopNav from '../components/MarketingTopNav'
import { appConfig } from '../config/appConfig'
import { getPublicSiteBranding, getPublicSitePages } from '../utils/publicSiteModel'

function joinClasses(...values) {
  return values.filter(Boolean).join(' ')
}

function prefersReducedMotion() {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return true
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function clamp01(value) {
  if (!Number.isFinite(value)) return 0
  return Math.max(0, Math.min(value, 1))
}

function easeOutCubic(t) {
  const clamped = clamp01(t)
  return 1 - Math.pow(1 - clamped, 3)
}

function computePageProgress() {
  const scrollTop = window.scrollY || window.pageYOffset || 0
  const doc = document.documentElement
  const scrollHeight = Math.max(doc.scrollHeight, doc.offsetHeight, doc.clientHeight, window.innerHeight)
  const total = Math.max(scrollHeight - window.innerHeight, 1)
  return clamp01(scrollTop / total)
}

function computeScrollProgress(element) {
  const rect = element.getBoundingClientRect()
  const viewport = Math.max(window.innerHeight, 1)
  const start = viewport * 0.85
  const end = viewport * 0.2
  const raw = (start - rect.top) / Math.max(start - end, 1)
  return clamp01(raw)
}

function computeSectionProgress(section) {
  const rect = section.getBoundingClientRect()
  const viewport = Math.max(window.innerHeight, 1)
  const total = Math.max(rect.height + viewport * 0.25, 1)
  const traveled = viewport - rect.top
  return clamp01(traveled / total)
}

export default function MarketingHomePage() {
  const branding = useMemo(() => getPublicSiteBranding(), [])
  const pages = useMemo(() => getPublicSitePages(), [])
  const reducedMotion = useMemo(() => prefersReducedMotion(), [])
  const navigate = useNavigate()
  const rootRef = useRef(null)
  const heroRef = useRef(null)
  const storyRef = useRef(null)
  const rafRef = useRef(0)
  const [activeStep, setActiveStep] = useState('control')

  function go(to) {
    if (typeof document !== 'undefined' && typeof document.startViewTransition === 'function' && !reducedMotion) {
      document.startViewTransition(() => {
        navigate(to)
      })
      return
    }
    navigate(to)
  }

  useEffect(() => {
    if (typeof document !== 'undefined') {
      document.title = `${branding.name} | Trading control plane`
    }
  }, [branding.name])

  useEffect(() => {
    const root = rootRef.current
    if (!root || typeof window === 'undefined') return undefined

    const steps = Array.from(root.querySelectorAll('[data-story-step]'))
    if (!steps.length) return undefined
    const hero = heroRef.current
    const story = storyRef.current

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => (a.boundingClientRect.top || 0) - (b.boundingClientRect.top || 0))[0]
        if (!visible?.target) return
        const key = String(visible.target.getAttribute('data-story-step') || '').trim()
        if (key) setActiveStep(key)
      },
      { root: null, threshold: 0.28 },
    )

    steps.forEach((step) => observer.observe(step))

    if (reducedMotion) return () => observer.disconnect()

    let lastScrollY = window.scrollY
    let velocity = 0

    function tick() {
      const now = typeof performance !== 'undefined' ? performance.now() : Date.now()
      const currentScrollY = window.scrollY
      const delta = currentScrollY - lastScrollY
      lastScrollY = currentScrollY
      velocity = velocity * 0.9 + delta * 0.1

      const speed = Math.min(1, Math.abs(velocity) / 60)
      root.style.setProperty('--story-velocity', String(speed))
      root.style.setProperty('--page-progress', String(easeOutCubic(computePageProgress())))
      root.style.setProperty('--time', String(now / 1000))

      if (hero) {
        hero.style.setProperty('--hero-progress', String(easeOutCubic(computeScrollProgress(hero))))
      }

      if (story) {
        root.style.setProperty('--story-progress', String(easeOutCubic(computeSectionProgress(story))))
      }

      for (const step of steps) {
        const progress = computeScrollProgress(step)
        step.style.setProperty('--step-progress', String(easeOutCubic(progress)))
      }

      rafRef.current = window.requestAnimationFrame(tick)
    }

    rafRef.current = window.requestAnimationFrame(tick)

    return () => {
      observer.disconnect()
      if (rafRef.current) window.cancelAnimationFrame(rafRef.current)
    }
  }, [reducedMotion])

  return (
    <div className="marketing-home" ref={rootRef}>
      <MarketingTopNav branding={branding} pages={pages} mode="home" />

      <section className="marketing-hero" ref={heroRef}>
        <div className="marketing-hero__grid" aria-hidden="true" />
        <div className="marketing-hero__beam" aria-hidden="true" />
        <div className="marketing-hero__noise" aria-hidden="true" />
        <div className="marketing-hero__layout">
          <div className="marketing-hero__content">
            <div className="marketing-hero__kicker">
              <Chip tone="neutral" size="sm">Paper-first</Chip>
              <Chip tone="neutral" size="sm">Audit-ready</Chip>
              <Chip tone="neutral" size="sm" className="marketing-hero__chip marketing-hero__chip--late">
                Provider-agnostic
              </Chip>
            </div>
            <h1 className="marketing-hero__title">
              <span className="marketing-hero__title-line" style={{ '--line': 0 }}>
                Evidence-led <span className="marketing-hero__title-accent">trading operations</span>
              </span>
              <span className="marketing-hero__title-line" style={{ '--line': 1 }}>
                that still feel <span className="marketing-hero__title-accent">like a desk</span>.
              </span>
            </h1>
            <p className="marketing-hero__subtitle">
              Sell the control plane: readiness, risk gates, approvals, execution proof, and operator workflow.
              Keep brokers as pluggable adapters.
            </p>
            <div className="marketing-hero__actions">
              <Button type="button" variant="solid" size="md" onClick={() => go('/login')}>
                Open the desk
              </Button>
              <Button type="button" variant="subtle" size="md" onClick={() => go('/trading-service')}>
                How it works
              </Button>
            </div>
          </div>

          <div className="marketing-hero__scene" aria-hidden="true">
            <div className="marketing-scene">
              <svg className="marketing-scene__paths" viewBox="0 0 560 420">
                <defs>
                  <linearGradient id="routeGlow" x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0%" stopColor="rgb(var(--accent-2-rgb) / 0.92)" />
                    <stop offset="55%" stopColor="rgb(var(--accent-1-rgb) / 0.92)" />
                    <stop offset="100%" stopColor="rgb(216 163 92 / 0.9)" />
                  </linearGradient>
                </defs>
                <path className="marketing-scene__ring" d="M 280 62 C 390 78 468 144 486 224 C 504 304 444 372 350 392 C 256 412 154 382 102 304 C 50 226 72 120 168 84 C 212 66 246 58 280 62 Z" />
                <path className="marketing-scene__ring marketing-scene__ring--inner" d="M 280 118 C 358 128 410 176 420 232 C 430 288 392 338 322 352 C 252 366 180 342 148 290 C 116 238 126 170 190 138 C 222 122 248 114 280 118 Z" />
                <path className="marketing-scene__route" d="M 130 276 C 188 312 240 312 280 252 C 320 192 372 178 430 204" />
                <path className="marketing-scene__route marketing-scene__route--pulse" d="M 130 276 C 188 312 240 312 280 252 C 320 192 372 178 430 204" />
              </svg>

              <div className="marketing-scene__core">
                <div className="marketing-scene__core-kicker">router</div>
                <div className="marketing-scene__core-title">execution lane</div>
                <div className="marketing-scene__core-meta">gates → receipts → sync</div>
              </div>

              <div className="marketing-scene__node marketing-scene__node--alpaca">
                <div className="marketing-scene__node-title">alpaca</div>
                <div className="marketing-scene__node-sub">equities paper</div>
              </div>
              <div className="marketing-scene__node marketing-scene__node--tradier">
                <div className="marketing-scene__node-title">tradier</div>
                <div className="marketing-scene__node-sub">options paper</div>
              </div>
              <div className="marketing-scene__node marketing-scene__node--internal">
                <div className="marketing-scene__node-title">internal</div>
                <div className="marketing-scene__node-sub">offline sim</div>
              </div>
              <div className="marketing-scene__node marketing-scene__node--audit">
                <div className="marketing-scene__node-title">audit</div>
                <div className="marketing-scene__node-sub">export packet</div>
              </div>
            </div>
          </div>
        </div>

        <div className="marketing-hero__tape" aria-hidden="true">
          <div className="marketing-hero__tape-track">
            {[
              'route: alpaca_paper',
              'gate: broker_execution entitlement',
              'state: paper_ready',
              'audit: hash-chained receipts',
              'session: regular / pre / after',
              'risk: max loss, notional, cooldown',
              'evidence: replay comparisons',
            ].map((item) => (
              <span key={item} className="marketing-hero__tape-item">
                {item}
              </span>
            ))}
          </div>
        </div>
      </section>

      <section className="story" aria-label="Product story" ref={storyRef}>
        <aside className="story-rail" aria-hidden="true">
          <div className="story-rail__line" />
          <div className="story-rail__fill" />
          {[
            ['control', 'Control'],
            ['routing', 'Routing'],
            ['proof', 'Proof'],
            ['safety', 'Safety'],
          ].map(([key, label]) => (
            <div
              key={key}
              className={joinClasses('story-rail__node', activeStep === key && 'story-rail__node--active')}
            >
              <span className="story-rail__dot" />
              <span className="story-rail__label">{label}</span>
            </div>
          ))}
        </aside>

        <div className="story-body">
          <article className="story-step" data-story-step="control">
            <div className="story-step__panel">
              <div className="ui-kicker">01 / Control plane</div>
              <h2>Decisions become cases, not screenshots.</h2>
              <p>
                Keep entry, target, stop, sizing, session mode, and evidence in one place. Make the
                operator workflow the product.
              </p>
              <ul className="story-step__list">
                <li>Evidence register + rationale quality checks</li>
                <li>Release basis, run id, and replay outcomes</li>
                <li>Runbook notes instead of hidden defaults</li>
              </ul>
            </div>
            <div className="story-step__visual">
              <div className="story-card story-card--glow">
                <div className="story-card__kicker">Desk signal</div>
                <div className="story-card__title">Staged trade packet</div>
                <div className="story-card__meta">
                  <span>case: client_trade_case_v1</span>
                  <span>route_correlation_id: ...</span>
                </div>
              </div>
              <div className="story-card story-card--stack story-card--shift">
                <div className="story-card__kicker">Evidence</div>
                <div className="story-card__title">Replay comparisons</div>
                <div className="story-card__meta">
                  <span>win rate: tracked</span>
                  <span>drift: monitored</span>
                </div>
              </div>
            </div>
          </article>

          <article className="story-step" data-story-step="routing">
            <div className="story-step__panel">
              <div className="ui-kicker">02 / Provider routing</div>
              <h2>Brokers are dumb pipes. Your product stays portable.</h2>
              <p>
                Central routing chooses the adapter only after gates pass. Swap paper lanes without rewriting UI
                flows or trade logic.
              </p>
              <div className="story-step__chips">
                <Chip tone="neutral" size="sm">alpaca_paper equities</Chip>
                <Chip tone="neutral" size="sm">tradier_paper options</Chip>
                <Chip tone="neutral" size="sm">internal_paper offline</Chip>
              </div>
              <p className="ui-muted">
                Live adapters exist, but are still locked by policy and readiness basis.
              </p>
            </div>
            <div className="story-step__visual">
              <div className="route-orbit" aria-hidden="true">
                <div className="route-orbit__ring" />
                <div className="route-orbit__node route-orbit__node--a">alpaca</div>
                <div className="route-orbit__node route-orbit__node--b">tradier</div>
                <div className="route-orbit__node route-orbit__node--c">internal</div>
                <div className="route-orbit__core">router</div>
              </div>
            </div>
          </article>

          <article className="story-step" data-story-step="proof">
            <div className="story-step__panel">
              <div className="ui-kicker">03 / Proof</div>
              <h2>Execution quality is a surface, not a spreadsheet.</h2>
              <p>
                Slippage, fills, rejection reasons, and route states are captured with every order event.
                Export support packets when you need to explain a decision.
              </p>
              <ul className="story-step__list">
                <li>Typed order lifecycle states + normalized broker statuses</li>
                <li>Retryable vs terminal error envelopes</li>
                <li>Execution analytics: routes, fills, costs, and drift</li>
              </ul>
            </div>
            <div className="story-step__visual">
              <svg className="proof-path" viewBox="0 0 560 240" role="img" aria-label="Execution proof path">
                <defs>
                  <linearGradient id="proofGradient" x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0%" stopColor="var(--accent-2)" stopOpacity="0.9" />
                    <stop offset="50%" stopColor="var(--accent-1)" stopOpacity="0.9" />
                    <stop offset="100%" stopColor="var(--accent-3)" stopOpacity="0.9" />
                  </linearGradient>
                </defs>
                <path
                  className="proof-path__track"
                  d="M 24 188 C 120 110, 190 212, 282 128 S 460 46, 536 88"
                  fill="none"
                  stroke="rgba(255,255,255,0.10)"
                  strokeWidth="14"
                  strokeLinecap="round"
                />
                <path
                  className="proof-path__stroke"
                  d="M 24 188 C 120 110, 190 212, 282 128 S 460 46, 536 88"
                  fill="none"
                  stroke="url(#proofGradient)"
                  strokeWidth="14"
                  strokeLinecap="round"
                />
              </svg>
              <div className="proof-badges" aria-hidden="true">
                <div className="proof-badge">route_state: accepted</div>
                <div className="proof-badge">route_state: filled</div>
                <div className="proof-badge">slippage_bps: tracked</div>
              </div>
            </div>
          </article>

          <article className="story-step" data-story-step="safety">
            <div className="story-step__panel">
              <div className="ui-kicker">04 / Safety</div>
              <h2>Trades stay blocked until the system can explain “why”.</h2>
              <p>
                The point is not to trade more. It is to trade with a readable chain of custody:
                readiness, permissions, entitlements, session policy, and kill switches.
              </p>
              <div className="safety-grid">
                {[
                  { label: 'Readiness gates', value: 'readyz + lane checks' },
                  { label: 'Entitlements', value: 'plan_key -> broker_execution' },
                  { label: 'Kill switch', value: 'operator proof required' },
                  { label: 'Live lock', value: 'paper evidence basis first' },
                ].map((item) => (
                  <div key={item.label} className="safety-item">
                    <div className="safety-item__label">{item.label}</div>
                    <div className="safety-item__value">{item.value}</div>
                  </div>
                ))}
              </div>
            </div>
            <div className="story-step__visual">
              <div className="gate-wall" aria-hidden="true">
                <div className="gate-wall__gate gate-wall__gate--1">risk</div>
                <div className="gate-wall__gate gate-wall__gate--2">readiness</div>
                <div className="gate-wall__gate gate-wall__gate--3">approval</div>
                <div className="gate-wall__gate gate-wall__gate--4">broker</div>
              </div>
            </div>
          </article>
        </div>
      </section>

      <footer className="marketing-footer">
        <div className="marketing-footer__grid">
          <div>
            <strong>{branding.name}</strong>
            <div className="ui-muted">{branding.tagline}</div>
          </div>
          <div className="marketing-footer__links">
            <NavLink to="/about" className="marketing-footer__link">About</NavLink>
            <NavLink to="/integrations" className="marketing-footer__link">Integrations</NavLink>
            <NavLink to="/security" className="marketing-footer__link">Security</NavLink>
            <NavLink to="/docs" className="marketing-footer__link">Docs</NavLink>
            <NavLink to="/contact" className="marketing-footer__link">Contact</NavLink>
            <NavLink to="/pricing" className="marketing-footer__link">Pricing</NavLink>
            <NavLink to="/terms" className="marketing-footer__link">Terms</NavLink>
            <NavLink to="/privacy" className="marketing-footer__link">Privacy</NavLink>
            <NavLink to="/connect" className="marketing-footer__link">Connect</NavLink>
          </div>
          <div className="marketing-footer__cta">
            <Button type="button" variant="solid" size="sm" onClick={() => go('/login')}>
              Sign in
            </Button>
          </div>
        </div>
        <div className="marketing-footer__fineprint">
          {appConfig.personalMode ? 'Own-account workflow only.' : 'Customer control-plane workflow.'}
        </div>
      </footer>
    </div>
  )
}
