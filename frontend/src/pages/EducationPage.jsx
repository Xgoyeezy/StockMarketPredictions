import { Link, useLocation } from 'react-router-dom'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import { UX_TEST_COUNTS, UX_TEST_PATHS } from '../data/uxTestingPlaybook'

function RouteLink({ to, children, className = 'table-link' }) {
  const location = useLocation()
  return (
    <Link className={className} to={{ pathname: to, search: location.search }}>
      {children}
    </Link>
  )
}

export default function EducationPage() {
  const location = useLocation()
  const routeSearch = location.search

  const implementationChecklist = [
    {
      id: 'check-scope',
      title: 'Scope and market lock',
      bullets: [
        'Own-account only. Keep the first live deployment inside one personally operated trading process.',
        'One liquid market, one setup family, and one broker execution path are selected for the first live deployment.',
        'The setup does not depend on latency-sensitive edge or passive queue capture unless the data and simulator support it honestly.',
      ],
      route: '/settings',
      routeLabel: 'Check desk setup',
    },
    {
      id: 'check-data',
      title: 'Data and backtest quality',
      bullets: [
        'Reference data, corporate actions, market status, halts, and auction windows are modeled correctly.',
        'Backtests use event-driven logic with explicit spread, fee, slippage, and delay assumptions where execution realism matters.',
        'Walk-forward and cost-stress passes are complete, and promotion is not based on one best backtest.',
      ],
      route: '/watchlist',
      routeLabel: 'Check liquid board inputs',
    },
    {
      id: 'check-execution',
      title: 'Execution and control path',
      bullets: [
        'Strategy logic stays separate from execution logic, and every order, fill, reject, and cancel is persisted.',
        'Manual flatten, stale-data blocks, duplicate-order suppression, and reject/disconnect halt logic are tested.',
        'The desk can reconcile local state against broker state after a live or simulated session.',
      ],
      route: '/',
      routeLabel: 'Inspect execution desk',
    },
    {
      id: 'check-shadow',
      title: 'Paper and shadow evidence',
      bullets: [
        'Paper trading has covered more than one intraday regime, and shadow mode uses the same code path as live execution.',
        'Modeled fills and observed fills have been compared, and any large slippage drift is explained.',
        'No unresolved stale-state, order-state, or routing surprises remain before live promotion.',
      ],
      route: '/journal',
      routeLabel: 'Review the paper loop',
    },
    {
      id: 'check-live',
      title: 'Tiny live promotion gate',
      bullets: [
        'First live size is intentionally tiny, with one symbol or one instrument only.',
        'Promotion criteria are defined in advance: positive expectancy after costs, acceptable live slippage, no control failures, and no unexplained drift.',
        'If live slippage breaks the model or controls start failing, promotion stops immediately instead of scaling through uncertainty.',
      ],
      route: '/trades',
      routeLabel: 'Open route controls',
    },
  ]

  const modules = [
    {
      id: 'scope-lock',
      title: 'Scope lock the desk',
      subtitle: 'Treat this workstation as an own-account intraday system with a deliberately narrow first-live scope.',
      bullets: [
        'Start with one market, one setup family, one broker path, and one same-session risk plan.',
        'Do not widen the scope into more symbols, more strategy families, or more routing paths just because the desk can automate tickets.',
        'A stable own-account process is the first milestone. Expansion comes only after the first process survives live conditions.',
      ],
      route: '/settings',
      routeLabel: 'Open desk settings',
    },
    {
      id: 'market-choice',
      title: 'Choose a market you can model honestly',
      subtitle: 'Pick the instrument and venue structure that match your data and execution assumptions.',
      bullets: [
        'Liquid ETFs and micro futures are stronger first-live candidates than queue-sensitive equity or options strategies.',
        'If the setup needs only aggressive event-driven execution, top-of-book and tick data can be enough to start.',
        'If the setup depends on passive fills or queue behavior, the desk needs deeper order-book data before the backtest is trustworthy.',
      ],
      route: '/watchlist',
      routeLabel: 'Open liquid board',
    },
    {
      id: 'execution-first',
      title: 'Build execution first',
      subtitle: 'Treat the path from signal to fill as part of the strategy, not as a thin wrapper around it.',
      bullets: [
        'A deterministic event-driven backtest, explicit slippage assumptions, and broker-side execution logging matter more than adding another indicator.',
        'Implementation shortfall is the right measuring stick when the desk needs to explain the gap between signal and realized fill.',
        'Do not assume passive orders fill just because the quoted price traded. Queue loss and missed fills are part of the economics.',
      ],
      route: '/',
      routeLabel: 'Open execution desk',
    },
    {
      id: 'controls',
      title: 'Keep controls outside the signal',
      subtitle: 'The workstation should block preventable damage before strategy logic can make it expensive.',
      bullets: [
        'Position caps, order-size limits, stale-data detection, duplicate-order suppression, and kill switches are part of the strategy design.',
        'Halts, price-band protections, rejects, and disconnects need explicit handling before the desk is promoted to live capital.',
        'An unexplained operational loss is worse than a clean signal loss because it weakens the whole process, not just one trade.',
      ],
      route: '/settings',
      routeLabel: 'Open risk controls',
    },
    {
      id: 'promotion-gate',
      title: 'Promote slowly',
      subtitle: 'The first live goal is process stability, not fast income claims.',
      bullets: [
        'Move from historical replay to paper trading to tiny live size, and compare modeled fills against realized behavior at each step.',
        'Do not promote a system that only works in one regime or only under optimistic spread assumptions.',
        'Positive expectancy after costs, stable slippage, and no control failures are stronger milestones than headline backtest returns.',
      ],
      route: '/journal',
      routeLabel: 'Open review loop',
    },
  ]

  return (
    <>
      <PageIntro
        kicker="Own-account operator guide"
        title="Run the desk like an execution-first intraday system"
        description="This guide treats the workstation as an own-account intraday operation. The focus is narrow scope, liquid markets, realistic fills, hard controls, and promotion gates that survive live trading."
        badge="Memo 1 guide"
      />

      <section className="metrics-grid">
        <MetricCard
          label="Scope first"
          value="One market, one path"
          helper="Keep the first live system narrow enough to explain every fill and failure."
        />
        <MetricCard
          label="Execution first"
          value="Signal to fill"
          helper="A usable intraday edge is defined by what the market actually lets the desk execute."
        />
        <MetricCard
          label="Controls outside strategy"
          value="Risk before route"
          helper="Position caps, kill switches, and stale-data blocks are part of the operating model."
        />
        <MetricCard
          label="Promotion gate"
          value={UX_TEST_COUNTS.walkthroughs}
          helper="Use the desk only after the workflow, fills, and control paths hold together under repeated testing."
        />
      </section>

      <SectionCard
        title="How to use this guide"
        subtitle="Start by narrowing scope, then work through market choice, execution, controls, and rollout."
        actions={
          <RouteLink to="/">Return to dashboard</RouteLink>
        }
      >
        <div className="education-grid">
          {modules.map((module) => (
            <a key={module.id} className="education-jump-card" href={`${routeSearch}#${module.id}`}>
              <span>{module.title}</span>
              <strong>{module.subtitle}</strong>
            </a>
          ))}
        </div>
      </SectionCard>

      <SectionCard
        title="Operator walkthroughs"
        subtitle="Use these scenarios as the first user-testing pass for the desk. Each one checks whether the workflow stays clear, safe, and recoverable under live intraday use."
      >
        <div className="ux-test-grid">
          {UX_TEST_PATHS.map((path) => (
            <article key={path.id} className="ux-test-card" id={path.id}>
              <div className="ux-test-card__header">
                <span>Critical path</span>
                <strong>{path.title}</strong>
              </div>
              <p className="ux-test-card__goal">{path.goal}</p>
              <div className="ux-test-card__section">
                <strong>Pass when</strong>
                <ul className="ux-test-card__list">
                  {path.passCriteria.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
              <div className="ux-test-card__section">
                <strong>Watch for</strong>
                <ul className="ux-test-card__list">
                  {path.watchFor.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
              <div className="ux-test-card__actions">
                <RouteLink to={path.startRoute} className="education-test-card__action">
                  {path.startLabel}
                </RouteLink>
                <RouteLink to="/notes" className="education-test-card__action">
                  Log finding in notes
                </RouteLink>
              </div>
            </article>
          ))}
        </div>
      </SectionCard>

      <SectionCard
        title="Own-account implementation checklist"
        subtitle="Use this as the promotion gate for Memo 1. If these checks do not hold, the desk stays in replay, paper, or tiny-size review."
        actions={<RouteLink to="/settings">Open desk setup</RouteLink>}
      >
        <div className="ux-test-grid">
          {implementationChecklist.map((item) => (
            <article key={item.id} className="ux-test-card" id={item.id}>
              <div className="ux-test-card__header">
                <span>Go-live gate</span>
                <strong>{item.title}</strong>
              </div>
              <div className="ux-test-card__section">
                <strong>Check before promotion</strong>
                <ul className="ux-test-card__list">
                  {item.bullets.map((bullet) => (
                    <li key={bullet}>{bullet}</li>
                  ))}
                </ul>
              </div>
              <div className="ux-test-card__actions">
                <RouteLink to={item.route} className="education-test-card__action">
                  {item.routeLabel}
                </RouteLink>
              </div>
            </article>
          ))}
        </div>
      </SectionCard>

      {modules.map((module) => (
        <SectionCard
          key={module.id}
          title={module.title}
          subtitle={module.subtitle}
          actions={<RouteLink to={module.route}>{module.routeLabel}</RouteLink>}
        >
          <div id={module.id} className="education-module">
            <div className="education-module__copy">
              {module.bullets.map((item) => (
                <p key={item}>{item}</p>
              ))}
            </div>
          </div>
        </SectionCard>
      ))}
    </>
  )
}
