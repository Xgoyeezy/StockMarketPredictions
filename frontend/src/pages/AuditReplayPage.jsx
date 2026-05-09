import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import AuditEventTimeline from '../components/audit/AuditEventTimeline'
import AuditExportPanel from '../components/audit/AuditExportPanel'
import DecisionReplayPanel from '../components/audit/DecisionReplayPanel'
import Button from '../components/Button'
import ErrorState from '../components/ErrorState'
import { TextField } from '../components/FormFields'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import {
  exportAuditBundle,
  getAuditEvents,
  getStrategyAudit,
  getTradeReplay,
} from '../api/client'

export default function AuditReplayPage() {
  const [params] = useSearchParams()
  const strategyId = params.get('strategy') || ''
  const [events, setEvents] = useState([])
  const [selectedEvent, setSelectedEvent] = useState(null)
  const [tradeId, setTradeId] = useState('')
  const [decision, setDecision] = useState(null)
  const [replay, setReplay] = useState([])
  const [exports, setExports] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setError('')
    try {
      const eventData = await getAuditEvents()
      setEvents(eventData.items || [])
      if (strategyId) {
        const strategyAudit = await getStrategyAudit(strategyId)
        const firstDecision = (strategyAudit.decisions || [])[0]
        if (firstDecision) {
          setDecision(firstDecision)
          setTradeId(firstDecision.id)
        }
      }
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load audit evidence.')
    }
  }, [strategyId])

  useEffect(() => {
    load()
  }, [load])

  const exportFilters = useMemo(() => ({ export_type: 'audit_bundle', strategy_id: strategyId || undefined, trade_id: tradeId || undefined }), [strategyId, tradeId])

  async function handleReplay(event) {
    event.preventDefault()
    if (!tradeId) return
    setBusy(true)
    setError('')
    try {
      const result = await getTradeReplay(tradeId)
      setDecision(result.decision)
      setReplay(result.replay || [])
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load replay.')
    } finally {
      setBusy(false)
    }
  }

  async function handleExport(filters = exportFilters) {
    setBusy(true)
    try {
      const result = await exportAuditBundle(filters)
      setExports((current) => [result.export, ...current].filter(Boolean))
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to queue export.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="ui-shell__page">
      <PageIntro
        kicker="Evidence"
        title="Audit Replay"
        description="Ordered decision evidence, replay events, and export jobs for operator review."
        badge={strategyId ? 'strategy scoped' : 'tenant scoped'}
      />
      {error ? <ErrorState description={error} onAction={load} /> : null}
      <SectionCard title="Replay lookup" subtitle="Enter a trade decision, order event, or decision hash.">
        <form className="ui-form-grid" onSubmit={handleReplay}>
          <TextField label="Trade id" value={tradeId} onChange={(event) => setTradeId(event.target.value)} />
          <div className="ui-action-row">
            <Button variant="solid" type="submit" disabled={busy}>Load Replay</Button>
          </div>
        </form>
      </SectionCard>
      <SectionCard title="Decision replay" subtitle="Sequence-ordered replay output from recorded decision events.">
        <DecisionReplayPanel trade={decision} replay={replay} onExport={() => handleExport(exportFilters)} />
      </SectionCard>
      <SectionCard title="Audit events" subtitle="Recent productized control-plane events.">
        <AuditEventTimeline items={events} selectedId={selectedEvent?.id} onSelect={setSelectedEvent} />
      </SectionCard>
      <SectionCard title="Exports" subtitle="Evidence bundles are queued and tracked without changing strategy state.">
        <AuditExportPanel filters={exportFilters} jobs={exports} busy={busy} onExport={handleExport} />
      </SectionCard>
    </div>
  )
}
