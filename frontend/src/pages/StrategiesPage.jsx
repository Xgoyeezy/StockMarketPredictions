import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Button from '../components/Button'
import ErrorState from '../components/ErrorState'
import { TextField } from '../components/FormFields'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import StrategyTable from '../components/strategy/StrategyTable'
import {
  createStrategy,
  evaluateStrategyReadiness,
  getStrategies,
} from '../api/client'

const defaultDraft = {
  name: 'Systematic Equities v1',
  desk_key: 'systematic-equities-v1',
  symbols: 'SPY, QQQ, AAPL',
  allocation_cap: 25000,
}

function splitSymbols(value) {
  return String(value || '')
    .split(',')
    .map((item) => item.trim().toUpperCase())
    .filter(Boolean)
}

export default function StrategiesPage() {
  const navigate = useNavigate()
  const [items, setItems] = useState([])
  const [deskReadiness, setDeskReadiness] = useState(null)
  const [draft, setDraft] = useState(defaultDraft)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const strategyData = await getStrategies()
      const nextItems = strategyData.items || []
      const scores = nextItems
        .map((item) => Number(item.readiness?.score || 0))
        .filter((score) => Number.isFinite(score) && score > 0)

      setItems(nextItems)
      setDeskReadiness({
        strategy_count: nextItems.length,
        average_score: scores.length ? scores.reduce((total, score) => total + score, 0) / scores.length : 0,
        items: nextItems,
      })
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load strategies.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const stats = useMemo(() => {
    const running = items.filter((item) => item.latest_deployment?.status === 'running').length
    const ready = items.filter((item) => Number(item.readiness?.score || 0) >= 60).length
    return { running, ready, total: items.length }
  }, [items])

  async function handleCreate(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const result = await createStrategy({
        name: draft.name,
        desk_key: draft.desk_key,
        description: 'Paper-first productized strategy',
        allocation_cap: Number(draft.allocation_cap || 0),
        symbols: splitSymbols(draft.symbols),
        mode: 'paper',
        risk_profile: { max_daily_loss: 500, max_order_notional: 5000 },
      })
      const strategy = result.strategy
      await load()
      if (strategy?.id) navigate(`/strategies/${strategy.id}`)
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to create strategy.')
    } finally {
      setBusy(false)
    }
  }

  async function handleEvaluate(strategy) {
    setBusy(true)
    try {
      await evaluateStrategyReadiness(strategy.id, { force_refresh: true })
      await load()
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to evaluate readiness.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="ui-shell__page">
      <PageIntro
        kicker="Productized control plane"
        title="Strategies"
        description="Lifecycle, readiness, promotion, and audit surfaces for paper-first strategy productization."
        badge={`${stats.total} strategies`}
      />
      {error ? <ErrorState description={error} onAction={load} /> : null}
      <div className="ui-dashboard-grid">
        <MetricCard label="Desk readiness" value={`${Math.round(Number(deskReadiness?.average_score || 0))}%`} />
        <MetricCard label="Ready for paper" value={stats.ready} />
        <MetricCard label="Running paper deployments" value={stats.running} />
      </div>
      <SectionCard title="Strategy inventory" subtitle="Open a strategy to inspect versions, readiness gates, runs, and audit replay.">
        <StrategyTable
          items={items}
          loading={loading}
          onSelect={(strategy) => navigate(`/strategies/${strategy.id}`)}
          onEvaluateReadiness={handleEvaluate}
          onOpenAudit={(strategy) => navigate(`/audit?strategy=${encodeURIComponent(strategy.id)}`)}
        />
      </SectionCard>
      <SectionCard title="Create paper strategy" subtitle="New strategies default to paper mode and do not enable live submission.">
        <form className="ui-form-grid" onSubmit={handleCreate}>
          <TextField label="Name" value={draft.name} onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))} required />
          <TextField label="Desk key" value={draft.desk_key} onChange={(event) => setDraft((current) => ({ ...current, desk_key: event.target.value }))} required />
          <TextField label="Symbols" value={draft.symbols} onChange={(event) => setDraft((current) => ({ ...current, symbols: event.target.value }))} />
          <TextField label="Allocation cap" type="number" min="0" value={draft.allocation_cap} onChange={(event) => setDraft((current) => ({ ...current, allocation_cap: event.target.value }))} />
          <div className="ui-action-row">
            <Button variant="solid" type="submit" disabled={busy}>Create Strategy</Button>
          </div>
        </form>
      </SectionCard>
    </div>
  )
}
