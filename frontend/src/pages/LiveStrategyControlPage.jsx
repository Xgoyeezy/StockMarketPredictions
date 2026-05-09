import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import Button from '../components/Button'
import ErrorState from '../components/ErrorState'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import LiveAuthorizationPanel from '../components/live/LiveAuthorizationPanel'
import LiveAuditTimelinePanel from '../components/live/LiveAuditTimelinePanel'
import LiveBrokerReceiptPanel from '../components/live/LiveBrokerReceiptPanel'
import LiveModeStrip from '../components/live/LiveModeStrip'
import LiveRiskGatePanel from '../components/live/LiveRiskGatePanel'
import LiveSessionStatusPanel from '../components/live/LiveSessionStatusPanel'
import {
  armLiveStrategy,
  createLiveAuthorization,
  getLiveAuthorizations,
  getLiveKillSwitch,
  getLiveStatus,
  getStrategy,
  getStrategyReadiness,
  killLiveStrategy,
  pauseLiveStrategy,
  requestLiveStart,
  resumeLiveStrategy,
  revokeLiveAuthorization,
  startLiveStrategy,
  stopLiveStrategy,
} from '../api/client'

export default function LiveStrategyControlPage() {
  const { strategyId } = useParams()
  const [strategy, setStrategy] = useState(null)
  const [readiness, setReadiness] = useState(null)
  const [authorizations, setAuthorizations] = useState([])
  const [liveStatus, setLiveStatus] = useState(null)
  const [killSwitch, setKillSwitch] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const selectedAuthorization = useMemo(
    () => authorizations.find((item) => item.status === 'signed') || authorizations[0] || null,
    [authorizations],
  )

  const load = useCallback(async () => {
    setError('')
    try {
      const [strategyData, readinessData, authData, statusData, killData] = await Promise.all([
        getStrategy(strategyId),
        getStrategyReadiness(strategyId),
        getLiveAuthorizations({ strategy_id: strategyId }),
        getLiveStatus(),
        getLiveKillSwitch({ strategy_id: strategyId }),
      ])
      setStrategy(strategyData.strategy)
      setReadiness(readinessData)
      setAuthorizations(authData.items || [])
      setLiveStatus(statusData)
      setKillSwitch(killData)
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load live strategy controls.')
    }
  }, [strategyId])

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
      setError(err?.response?.data?.detail || err.message || 'Live action failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="ui-shell__page">
      <PageIntro
        kicker="Live strategy control"
        title={strategy?.name || 'Live strategy'}
        description="Authorization, arming, start/stop controls, and live gates for one strategy."
        badge={strategy?.lifecycle_stage || 'strategy'}
      />
      <LiveModeStrip activeKey="supervised-automation" />
      {error ? <ErrorState description={error} onAction={load} /> : null}
      <SectionCard title="Live gates" subtitle="Readiness, kill switch, risk, and audit gates are evaluated before live activation.">
        <LiveRiskGatePanel readiness={readiness} killSwitch={killSwitch} />
      </SectionCard>
      <SectionCard title="Authorization" subtitle="Live automation requires a signed authorization tied to an Alpaca account.">
        <LiveAuthorizationPanel
          strategyId={strategyId}
          authorizations={authorizations}
          busy={busy}
          onCreate={(payload) => runAction(() => createLiveAuthorization(payload))}
          onRevoke={(item) => runAction(() => revokeLiveAuthorization(item.id, { reason: 'Operator revoked from live strategy page.' }))}
        />
      </SectionCard>
      <SectionCard title="Session controls" subtitle="Armed means ready for explicit start; it does not submit orders.">
        <div className="ui-action-row">
          <Button disabled={busy} onClick={() => runAction(() => requestLiveStart(strategyId, { authorization_id: selectedAuthorization?.id }))}>Request Live</Button>
          <Button variant="solid" disabled={busy || !selectedAuthorization} onClick={() => runAction(() => armLiveStrategy(strategyId, { authorization_id: selectedAuthorization.id, expected_min_readiness_score: 85 }))}>Arm</Button>
          <Button disabled={busy} onClick={() => runAction(() => startLiveStrategy(strategyId, { authorization_id: selectedAuthorization?.id }))}>Start</Button>
          <Button disabled={busy} onClick={() => runAction(() => pauseLiveStrategy(strategyId))}>Pause</Button>
          <Button disabled={busy} onClick={() => runAction(() => resumeLiveStrategy(strategyId))}>Resume</Button>
          <Button disabled={busy} onClick={() => runAction(() => stopLiveStrategy(strategyId))}>Stop</Button>
          <Button variant="subtle" disabled={busy} onClick={() => runAction(() => killLiveStrategy(strategyId, { reason: 'Operator killed live strategy from detail control.' }))}>Kill</Button>
        </div>
        <LiveSessionStatusPanel status={liveStatus} sessions={(liveStatus?.sessions || []).filter((item) => item.strategy_id === strategyId)} />
      </SectionCard>
      <SectionCard title="Recent execution evidence" subtitle="Approved order receipts and replay events stay quiet until this strategy creates live evidence.">
        <LiveBrokerReceiptPanel receipts={liveStatus?.receipts || []} />
        <LiveAuditTimelinePanel events={liveStatus?.events || []} />
      </SectionCard>
    </div>
  )
}
