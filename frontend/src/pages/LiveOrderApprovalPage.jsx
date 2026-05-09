import { useCallback, useEffect, useState } from 'react'
import ErrorState from '../components/ErrorState'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import LiveModeStrip from '../components/live/LiveModeStrip'
import LiveOrderApprovalQueue from '../components/live/LiveOrderApprovalQueue'
import {
  approveLiveOrder,
  getLiveOrders,
  rejectLiveOrder,
  runLiveRiskCheck,
} from '../api/client'

export default function LiveOrderApprovalPage() {
  const [items, setItems] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setError('')
    try {
      const data = await getLiveOrders({ limit: 100 })
      setItems(data.items || [])
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load live approvals.')
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  async function runAction(action) {
    setBusy(true)
    setError('')
    try {
      await action()
      await load()
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Live approval action failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="ui-shell__page">
      <PageIntro
        kicker="Supervised live orders"
        title="Live Approvals"
        description="Every live order intent is queued for operator review unless a Professional+ policy explicitly allows auto-approval and every gate passes."
        badge={`${items.length} intents`}
      />
      <LiveModeStrip activeKey="assisted-live" />
      {error ? <ErrorState description={error} onAction={load} /> : null}
      <SectionCard title="Approval queue" subtitle="Approving records evidence; broker submission stays gated behind live flags, risk, authorization, provider configuration, and receipts.">
        <LiveOrderApprovalQueue
          items={items}
          busy={busy}
          onRiskCheck={(item) => runAction(() => runLiveRiskCheck(item.id, { force_refresh: true }))}
          onApprove={(item) => runAction(() => approveLiveOrder(item.id, { note: 'Approved from live approval queue.' }))}
          onReject={(item) => runAction(() => rejectLiveOrder(item.id, { reason: 'Rejected from live approval queue.' }))}
        />
      </SectionCard>
    </div>
  )
}
