import { useCallback, useEffect, useMemo, useState } from 'react'
import SectionCard from './SectionCard'
import StatusBadge from './StatusBadge'
import MetricCard from './MetricCard'
import Button from './Button'
import { getExecutionDiagnostics } from '../api/client'
import { useToast } from '../context/ToastContext'

function mapTone(allowed) {
  if (allowed === true) return 'positive'
  if (allowed === false) return 'negative'
  return 'neutral'
}
function formatAdapter(decision) {
  if (!decision) return '--'
  return decision.adapter_key || decision.broker_name || '--'
}

function formatDetail(decision) {
  if (!decision) return 'No route decision available.'
  if (decision.allowed === false) return decision.detail || 'Blocked'
  return decision.detail || 'Route resolved.'
}

export default function ExecutionProviderDiagnosticsSection() {
  const { pushToast } = useToast()
  const [loading, setLoading] = useState(true)
  const [snapshot, setSnapshot] = useState({
    equity: null,
    options: null,
    defaults: null,
    updatedAt: null,
  })

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [defaults, equity, options] = await Promise.all([
        getExecutionDiagnostics({ instrument_type: 'equity', execution_intent: 'default' }),
        getExecutionDiagnostics({ instrument_type: 'equity', execution_intent: 'broker_paper' }),
        getExecutionDiagnostics({ instrument_type: 'listed_option', execution_intent: 'broker_paper' }),
      ])
      setSnapshot({ defaults, equity, options, updatedAt: new Date().toISOString() })
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to load execution diagnostics.', 'error')
    } finally {
      setLoading(false)
    }
  }, [pushToast])

  useEffect(() => {
    load()
  }, [load])

  const metrics = useMemo(() => {
    const defaultsDecision = snapshot.defaults?.decision
    const equityDecision = snapshot.equity?.decision
    const optionsDecision = snapshot.options?.decision
    return [
      {
        label: 'Default adapter',
        value: formatAdapter(defaultsDecision),
        tone: mapTone(defaultsDecision?.allowed ?? true),
        helper: formatDetail(defaultsDecision),
      },
      {
        label: 'Equity paper lane',
        value: formatAdapter(equityDecision),
        tone: mapTone(equityDecision?.allowed),
        helper: formatDetail(equityDecision),
      },
      {
        label: 'Options paper lane',
        value: formatAdapter(optionsDecision),
        tone: mapTone(optionsDecision?.allowed),
        helper: formatDetail(optionsDecision),
      },
    ]
  }, [snapshot])

  const configured = snapshot.defaults?.configured || {}
  const badge = loading ? 'Loading' : configured.execution_adapter || 'default'

  return (
    <SectionCard
      title="Execution provider diagnostics"
      subtitle="Shows which adapter lane will be selected before any order submission. Safety gates still apply and live routing remains locked unless explicitly authorized."
      badge={badge}
      actions={(
        <Button type="button" variant="ghost" size="sm" onClick={load} disabled={loading}>
          Refresh
        </Button>
      )}
    >
      <div className="metrics-grid metrics-grid--compact">
        {metrics.map((item) => (
          <MetricCard key={`execution-diag:${item.label}`} {...item} />
        ))}
      </div>
      <div className="inline-meta" style={{ marginTop: 12 }}>
        <StatusBadge tone="neutral" label={`broker_mode: ${configured.broker_mode || '--'}`} />
        <StatusBadge tone="neutral" label={`paper_broker_provider: ${configured.paper_broker_provider || '--'}`} />
        <StatusBadge tone="neutral" label={`options_broker_provider: ${configured.options_broker_provider || '--'}`} />
      </div>
    </SectionCard>
  )
}
