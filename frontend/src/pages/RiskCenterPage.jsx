import { useCallback, useEffect, useState } from 'react'
import ErrorState from '../components/ErrorState'
import { TextField } from '../components/FormFields'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import Button from '../components/Button'
import KillSwitchPanel from '../components/risk/KillSwitchPanel'
import RiskEventTable from '../components/risk/RiskEventTable'
import RiskPolicyPanel from '../components/risk/RiskPolicyPanel'
import {
  activateKillSwitch,
  clearKillSwitch,
  createRiskPolicy,
  getRiskEvents,
  getRiskPolicies,
  runRiskCheck,
} from '../api/client'

export default function RiskCenterPage() {
  const [policies, setPolicies] = useState([])
  const [events, setEvents] = useState([])
  const [check, setCheck] = useState({ symbol: 'AAPL', expected_notional: 5000 })
  const [checkResult, setCheckResult] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [policyData, eventData] = await Promise.all([getRiskPolicies(), getRiskEvents()])
      setPolicies(policyData.items || [])
      setEvents(eventData.items || [])
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load risk center.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  async function handleCreatePolicy(payload = {}) {
    try {
      await createRiskPolicy({
        scope: payload.scope || 'strategy',
        status: payload.status || 'active',
        max_daily_loss: 500,
        max_order_notional: 5000,
        max_open_positions: 5,
        allowed_symbols: [],
        blocked_symbols: [],
        allowed_instruments: ['equity', 'listed_option'],
        config: {},
      })
      await load()
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to create policy.')
    }
  }

  async function handleRiskCheck(event) {
    event.preventDefault()
    try {
      const result = await runRiskCheck({
        symbol: check.symbol,
        instrument_type: 'equity',
        side: 'buy',
        quantity: 1,
        expected_notional: Number(check.expected_notional || 0),
      })
      setCheckResult(result)
      await load()
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Risk check failed.')
    }
  }

  return (
    <div className="ui-shell__page">
      <PageIntro
        kicker="Controls"
        title="Risk Center"
        description="Policies, checks, breaches, and kill-switch state for the productized strategy surface."
        badge={`${policies.length} policies`}
      />
      {error ? <ErrorState description={error} onAction={load} /> : null}
      <div className="ui-dashboard-grid">
        <MetricCard label="Policies" value={policies.length} />
        <MetricCard label="Risk events" value={events.length} />
        <MetricCard label="Latest check" value={checkResult ? (checkResult.allowed ? 'Allowed' : 'Blocked') : 'Not run'} />
      </div>
      <SectionCard title="Kill switch" subtitle="This control records state and does not loosen live gates.">
        <KillSwitchPanel
          active={events.some((item) => item.event_type?.includes('kill'))}
          scope="tenant or strategy"
          onActivate={() => activateKillSwitch({ reason: 'operator_review' }).then(load).catch((err) => setError(err.message))}
          onClear={() => clearKillSwitch({ reason: 'operator_review' }).then(load).catch((err) => setError(err.message))}
        />
      </SectionCard>
      <SectionCard title="Risk policies" subtitle="Promotion and live requests require active policy evidence.">
        <RiskPolicyPanel policies={policies} loading={loading} onSave={handleCreatePolicy} />
      </SectionCard>
      <SectionCard title="Ad hoc risk check" subtitle="Run a deterministic policy check without placing an order.">
        <form className="ui-form-grid" onSubmit={handleRiskCheck}>
          <TextField label="Symbol" value={check.symbol} onChange={(event) => setCheck((current) => ({ ...current, symbol: event.target.value.toUpperCase() }))} />
          <TextField label="Expected notional" type="number" value={check.expected_notional} onChange={(event) => setCheck((current) => ({ ...current, expected_notional: event.target.value }))} />
          <div className="ui-action-row">
            <Button type="submit" variant="solid">Run Check</Button>
          </div>
        </form>
      </SectionCard>
      <SectionCard title="Risk events" subtitle="Breaches, checks, and kill-switch evidence.">
        <RiskEventTable items={events} loading={loading} />
      </SectionCard>
    </div>
  )
}
