import { useCallback, useEffect, useState } from 'react'
import ErrorState from '../components/ErrorState'
import FinishTrackerSection from '../components/FinishTrackerSection'
import { TextField } from '../components/FormFields'
import ListTable from '../components/ListTable'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import StatusBadge from '../components/StatusBadge'
import Button from '../components/Button'
import KillSwitchPanel from '../components/risk/KillSwitchPanel'
import RiskEventTable from '../components/risk/RiskEventTable'
import RiskPolicyPanel from '../components/risk/RiskPolicyPanel'
import {
  activateKillSwitch,
  clearKillSwitch,
  createRiskPolicy,
  getKillSwitchStatus,
  getRiskAuditHardening,
  getRiskEvents,
  getRiskPolicies,
  runRiskCheck,
} from '../api/client'

function humanize(value, fallback = 'Unknown') {
  const text = String(value || '').trim()
  if (!text) return fallback
  return text.replace(/[_-]+/g, ' ').replace(/\b\w/g, (match) => match.toUpperCase())
}

function statusTone(status) {
  if (status === 'ready' || status === 'ready_for_human_review') return 'positive'
  if (status === 'no_records' || status === 'empty') return 'neutral'
  return 'warning'
}

function DataTable({ columns, rows, empty }) {
  return (
    <ListTable>
      <table className="ui-list-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column.key}>{column.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.length ? rows.map((row, index) => (
            <tr key={row.key || row.event_type || row.id || row.title || row.note || index}>
              {columns.map((column) => (
                <td key={column.key}>{column.render ? column.render(row) : row[column.key]}</td>
              ))}
            </tr>
          )) : (
            <tr><td colSpan={columns.length}>{empty}</td></tr>
          )}
        </tbody>
      </table>
    </ListTable>
  )
}

export default function RiskCenterPage() {
  const [policies, setPolicies] = useState([])
  const [events, setEvents] = useState([])
  const [killSwitch, setKillSwitch] = useState(null)
  const [hardeningReport, setHardeningReport] = useState(null)
  const [check, setCheck] = useState({ symbol: 'AAPL', expected_notional: 5000 })
  const [checkResult, setCheckResult] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [policyData, eventData, killSwitchData] = await Promise.all([getRiskPolicies(), getRiskEvents(), getKillSwitchStatus()])
      setPolicies(policyData.items || [])
      setEvents(eventData.items || [])
      setKillSwitch(killSwitchData)
      const hardeningData = await getRiskAuditHardening()
      setHardeningReport(hardeningData)
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

  const hardeningPlan = hardeningReport?.risk_audit_hardening_plan || {}
  const hardeningSummary = hardeningPlan?.summary || hardeningReport?.summary || {}
  const hardeningRows = hardeningPlan?.items || []
  const safetyNotes = hardeningReport?.safety_notes || []
  const warnings = hardeningReport?.warnings || []
  const latestKillSwitchEvent = killSwitch?.latest_event || null

  return (
    <div className="ui-shell__page">
      <PageIntro
        kicker="Controls"
        title="Risk Center"
        description="Policies, checks, breaches, kill-switch state, and read-only audit hardening for the productized strategy surface."
        badge="No proof-layer bypass"
      />
      {error ? <ErrorState description={error} onAction={load} /> : null}
      <div className="ui-action-row">
        <StatusBadge tone={statusTone(hardeningReport?.status)}>{humanize(hardeningReport?.status || 'blocked_by_evidence')}</StatusBadge>
        <StatusBadge tone="neutral">Audit only</StatusBadge>
        <StatusBadge tone="neutral">Does not place orders</StatusBadge>
        <StatusBadge tone="neutral">Does not clear kill switches</StatusBadge>
        <StatusBadge tone="warning">No live-trading readiness</StatusBadge>
        <Button type="button" variant="ghost" size="sm" onClick={load} disabled={loading}>
          Refresh
        </Button>
      </div>
      <div className="ui-dashboard-grid">
        <MetricCard label="Policies" value={policies.length} />
        <MetricCard label="Risk events" value={events.length} />
        <MetricCard label="Latest check" value={checkResult ? (checkResult.allowed ? 'Allowed' : 'Blocked') : 'Not run'} />
        <MetricCard label="Hardening status" value={humanize(hardeningSummary.risk_audit_hardening_status || hardeningPlan.status || 'blocked_by_evidence')} helper={`${hardeningSummary.risk_audit_hardening_open_items ?? hardeningSummary.open_item_count ?? 0} open items`} tone={hardeningPlan.status === 'ready_for_human_review' ? 'default' : 'warning'} />
        <MetricCard label="Critical blockers" value={hardeningSummary.risk_audit_hardening_critical_open_items ?? hardeningSummary.critical_open_items ?? 0} helper={hardeningSummary.top_hardening_item || 'No top hardening item'} />
        <MetricCard label="Internal review" value={hardeningSummary.claim_permissions?.cautious_internal_risk_audit_review ? 'Allowed' : 'Blocked'} helper="Human evidence review only" />
      </div>
      <SectionCard title="Kill switch" subtitle="This control records state and does not loosen live gates.">
        <KillSwitchPanel
          active={Boolean(killSwitch?.active)}
          scope={killSwitch?.scope || 'tenant'}
          activeCount={killSwitch?.active_strategy_count || 0}
          strategyCount={killSwitch?.strategy_count || 0}
          latestEvent={latestKillSwitchEvent}
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
      <SectionCard title="Risk And Audit Hardening Plan" subtitle="Proof-first blockers for risk authority, audit completeness, paper-to-live, compliance, and live-readiness claims.">
        <div className="ui-dashboard-grid">
          <MetricCard label="Open hardening items" value={hardeningSummary.open_item_count ?? hardeningSummary.risk_audit_hardening_open_items ?? 0} helper={`${hardeningSummary.ready_item_count ?? 0} ready items`} />
          <MetricCard label="Blocked claims" value={(hardeningSummary.blocked_claims || []).length || 0} helper={(hardeningSummary.blocked_claims || []).slice(0, 3).map(humanize).join(', ') || 'None'} />
          <MetricCard label="Policies with evidence" value={hardeningPlan.metrics?.active_policy_count ?? hardeningReport?.summary?.active_policy_count ?? 0} helper="Active scope and risk limits" />
          <MetricCard label="Kill-switch audit events" value={hardeningPlan.metrics?.kill_switch_audit_event_count ?? 0} helper="Activation and clear lineage" />
        </div>
        <DataTable
          rows={hardeningRows}
          empty={loading ? 'Loading risk and audit hardening plan...' : 'No risk and audit hardening plan is available.'}
          columns={[
            { key: 'title', label: 'Hardening item' },
            { key: 'priority', label: 'Priority', render: (row) => humanize(row.priority) },
            { key: 'status', label: 'Status', render: (row) => <StatusBadge tone={statusTone(row.status)}>{humanize(row.status)}</StatusBadge> },
            { key: 'missing', label: 'Missing evidence', render: (row) => row.missing_fields?.length ? row.missing_fields.join(', ') : 'None' },
            { key: 'blocked_claims', label: 'Blocked claims', render: (row) => row.blocked_claims?.length ? row.blocked_claims.map(humanize).join(', ') : 'None' },
            { key: 'action', label: 'Safe next action', render: (row) => row.safe_next_action },
          ]}
        />
      </SectionCard>
      <SectionCard title="Audit Boundary Notes" subtitle="The hardening report is visibility only and carries explicit false authority flags.">
        <div className="ui-dashboard-grid">
          <DataTable
            rows={warnings.map((warning, index) => ({ note: warning, index }))}
            empty="No risk and audit warnings."
            columns={[{ key: 'note', label: 'Warning' }]}
          />
          <DataTable
            rows={safetyNotes.map((note, index) => ({ note, index }))}
            empty="Safety notes unavailable."
            columns={[{ key: 'note', label: 'Safety boundary' }]}
          />
        </div>
      </SectionCard>
      <FinishTrackerSection tracker={hardeningReport?.finish_tracker} loading={loading} />
    </div>
  )
}
