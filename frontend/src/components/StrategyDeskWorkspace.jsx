import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import AiDeskManagerPanel from './AiDeskManagerPanel'
import Button from './Button'
import Chip from './Chip'
import LoadingBlock from './LoadingBlock'
import PageIntro from './PageIntro'
import SectionCard from './SectionCard'
import { useToast } from '../context/ToastContext'
import {
  closeOptionsPaper,
  executeOptionsPaper,
  executeLatestPortfolioTargets,
  getAllocatorSnapshot,
  getLatestPortfolioTargetExecution,
  getLatestPortfolioTargets,
  getOptionsAutomationSnapshot,
  getRiskSnapshot,
  getStrategyDesk,
  getStrategyDeskMetrics,
  getStrategyDesks,
  refreshOptionsAutomationPositions,
  runStrategyBacktest,
  runStrategyDesk,
  scanOptionsAutomation,
  syncOptionsAutomation,
  syncPortfolioTargetExecution,
  updateStrategyDesk,
} from '../api/client'

export const SYSTEMATIC_DESK_KEY = 'systematic-equities'
export const SYSTEMATIC_DESK_ROUTE = '/strategy-desks/systematic-equities'

function formatNumber(value, digits = 2) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric.toFixed(digits) : '--'
}

function formatPercent(value) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? `${(numeric * 100).toFixed(2)}%` : '--'
}

function formatCurrency(value) {
  const numeric = Number(value)
  return Number.isFinite(numeric)
    ? numeric.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 })
    : '--'
}

function formatDateTime(value) {
  if (!value) return '--'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString()
}

function readinessTone(value) {
  const normalized = String(value || '').trim().toLowerCase()
  if (normalized === 'ready') return 'positive'
  if (normalized === 'blocked') return 'negative'
  if (normalized === 'collecting_lifecycle_evidence') return 'warning'
  return 'neutral'
}

function executionValidation(snapshot) {
  return snapshot?.validation_artifact || snapshot?.summary?.validation_artifact || {
    readiness_state: 'collecting_lifecycle_evidence',
    readiness_label: 'collecting lifecycle evidence',
    next_step: 'Run macro/stat-arb desks, execute a personal-paper basket, then refresh execution to collect lifecycle evidence.',
    blockers: [],
    orphan_events: [],
  }
}

function DeskListItem({ item, active, onSelect, onOpenDedicated }) {
  return (
    <div style={{ marginBottom: '0.5rem' }}>
      <button
        type="button"
        className={`ui-button ui-button--ghost ui-button--md ${active ? 'ui-shell__nav-link--active' : ''}`}
        onClick={() => onSelect(item.desk_key)}
        style={{ justifyContent: 'space-between', width: '100%' }}
      >
        <span style={{ textAlign: 'left' }}>
          <strong>{item.name}</strong>
          <span style={{ display: 'block', opacity: 0.7, fontSize: '0.85rem' }}>{item.category}</span>
        </span>
        <span style={{ display: 'inline-flex', gap: '0.4rem', alignItems: 'center' }}>
          <Chip tone={item.lifecycle_stage === 'paper' ? 'warning' : 'neutral'} size="sm">{item.lifecycle_stage}</Chip>
          <Chip tone={item.paper_trading_enabled ? 'positive' : 'neutral'} size="sm">{item.paper_trading_enabled ? 'paper' : 'research'}</Chip>
        </span>
      </button>
      {onOpenDedicated ? (
        <Button
          type="button"
          variant="subtle"
          size="sm"
          onClick={() => onOpenDedicated(item.desk_key)}
          style={{ marginTop: '0.35rem', width: '100%' }}
        >
          Open dedicated tab
        </Button>
      ) : null}
    </div>
  )
}

export default function StrategyDeskWorkspace({
  focusedDeskKey = '',
  pageKicker = 'Quant control plane',
  pageTitle = 'Strategy desks',
  pageDescription = 'Internal multi-desk strategy runtime for macro, stat-arb, cross-sectional, event, and volatility workflows.',
  pageHelper = 'Macro and stat-arb are Alpaca-paper routable first. Scheduling, live routing, linked-account routing, and research-desk execution stay blocked until lifecycle evidence is clean.',
  pageBadge,
}) {
  const { pushToast } = useToast()
  const navigate = useNavigate()
  const [loading, setLoading] = useState(true)
  const [runningDeskKey, setRunningDeskKey] = useState('')
  const [selectedDeskKey, setSelectedDeskKey] = useState(focusedDeskKey || '')
  const [desks, setDesks] = useState([])
  const [deskDetail, setDeskDetail] = useState(null)
  const [deskMetrics, setDeskMetrics] = useState(null)
  const [allocator, setAllocator] = useState(null)
  const [risk, setRisk] = useState(null)
  const [latestTargets, setLatestTargets] = useState(null)
  const [latestExecution, setLatestExecution] = useState(null)
  const [optionsAutomation, setOptionsAutomation] = useState(null)

  const showDeskRegistry = !focusedDeskKey
  const selectedDesk = useMemo(
    () => desks.find((item) => item.desk_key === selectedDeskKey) || null,
    [desks, selectedDeskKey],
  )
  const lifecycleValidation = executionValidation(latestExecution)
  const optionsLifecycle = optionsAutomation?.lifecycle || {}
  const optionsValidation = optionsAutomation?.validation_artifact || optionsLifecycle?.validation_artifact || {}
  const introBadge = pageBadge ?? (showDeskRegistry ? `${desks.length} desks` : 'Dedicated desk')

  useEffect(() => {
    if (focusedDeskKey) {
      setSelectedDeskKey(focusedDeskKey)
    }
  }, [focusedDeskKey])

  async function loadSummary() {
    const [deskSnapshot, allocatorSnapshot, riskSnapshot, portfolioTargetsSnapshot, executionSnapshot, optionsSnapshot] = await Promise.all([
      getStrategyDesks(),
      getAllocatorSnapshot(),
      getRiskSnapshot(),
      getLatestPortfolioTargets(),
      getLatestPortfolioTargetExecution(),
      getOptionsAutomationSnapshot(),
    ])
    const items = Array.isArray(deskSnapshot?.items) ? deskSnapshot.items : []
    setDesks(items)
    setSelectedDeskKey((current) => focusedDeskKey || current || items[0]?.desk_key || '')
    setAllocator(allocatorSnapshot)
    setRisk(riskSnapshot)
    setLatestTargets(portfolioTargetsSnapshot)
    setLatestExecution(executionSnapshot)
    setOptionsAutomation(optionsSnapshot)
  }

  async function loadDeskDetail(deskKey) {
    if (!deskKey) return
    const [detail, metrics] = await Promise.all([
      getStrategyDesk(deskKey),
      getStrategyDeskMetrics(deskKey),
    ])
    setDeskDetail(detail)
    setDeskMetrics(metrics)
  }

  useEffect(() => {
    let cancelled = false
    async function hydrate() {
      setLoading(true)
      try {
        await loadSummary()
      } catch (error) {
        if (!cancelled) {
          pushToast(error?.message || 'Failed to load strategy desks.', 'error')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    hydrate()
    return () => {
      cancelled = true
    }
  }, [focusedDeskKey, pushToast])

  useEffect(() => {
    let cancelled = false
    async function hydrateDetail() {
      if (!selectedDeskKey) return
      try {
        const [detail, metrics] = await Promise.all([
          getStrategyDesk(selectedDeskKey),
          getStrategyDeskMetrics(selectedDeskKey),
        ])
        if (!cancelled) {
          setDeskDetail(detail)
          setDeskMetrics(metrics)
        }
      } catch (error) {
        if (!cancelled) {
          pushToast(error?.message || 'Failed to load desk detail.', 'error')
        }
      }
    }
    hydrateDetail()
    return () => {
      cancelled = true
    }
  }, [selectedDeskKey, pushToast])

  async function handleRunDesk(deskKey) {
    setRunningDeskKey(deskKey)
    try {
      await runStrategyDesk(deskKey, { run_type: 'manual' })
      await Promise.all([loadSummary(), loadDeskDetail(deskKey)])
      pushToast(`${deskKey} run completed.`, 'success')
    } catch (error) {
      pushToast(error?.message || `Failed to run ${deskKey}.`, 'error')
    } finally {
      setRunningDeskKey('')
    }
  }

  async function handleRunBacktest(deskKey) {
    setRunningDeskKey(`backtest:${deskKey}`)
    try {
      await runStrategyBacktest({ desk_key: deskKey, horizon_days: 5, warmup_bars: 60, fee_bps: 2, slippage_bps: 5 })
      await loadDeskDetail(deskKey)
      pushToast(`${deskKey} backtest completed.`, 'success')
    } catch (error) {
      pushToast(error?.message || `Failed to backtest ${deskKey}.`, 'error')
    } finally {
      setRunningDeskKey('')
    }
  }

  async function handleToggleField(deskKey, field, nextValue) {
    try {
      await updateStrategyDesk(deskKey, { [field]: nextValue })
      await Promise.all([loadSummary(), loadDeskDetail(deskKey)])
      pushToast(`${deskKey} updated.`, 'success')
    } catch (error) {
      pushToast(error?.message || `Failed to update ${deskKey}.`, 'error')
    }
  }

  async function handleExecutePaperBasket() {
    setRunningDeskKey('portfolio-execution')
    try {
      await executeLatestPortfolioTargets({ execution_intent: 'broker_paper', dry_run: false })
      await loadSummary()
      pushToast('Paper basket execution submitted.', 'success')
    } catch (error) {
      pushToast(error?.message || 'Failed to execute the paper basket.', 'error')
    } finally {
      setRunningDeskKey('')
    }
  }

  async function handleRefreshExecution() {
    const executionRunId = latestExecution?.latest_execution_run_id
    if (!executionRunId) {
      pushToast('No execution run is available to refresh yet.', 'warning')
      return
    }
    setRunningDeskKey('portfolio-execution-sync')
    try {
      const snapshot = await syncPortfolioTargetExecution(executionRunId)
      setLatestExecution(snapshot)
      pushToast('Execution lifecycle refreshed.', 'success')
    } catch (error) {
      pushToast(error?.message || 'Failed to refresh the execution lifecycle.', 'error')
    } finally {
      setRunningDeskKey('')
    }
  }

  async function handleScanOptions() {
    setRunningDeskKey('options-scan')
    try {
      const snapshot = await scanOptionsAutomation({})
      setOptionsAutomation(snapshot)
      pushToast('Options scan completed.', snapshot?.ready_candidate_count > 0 ? 'success' : 'warning')
    } catch (error) {
      pushToast(error?.message || 'Failed to scan options.', 'error')
    } finally {
      setRunningDeskKey('')
    }
  }

  async function handleExecuteOptionsPaper() {
    setRunningDeskKey('options-execute')
    try {
      await executeOptionsPaper({ scan_run_id: optionsAutomation?.latest_scan_run_id, max_candidates: 1, dry_run: false })
      await loadSummary()
      pushToast('Paper option order submitted.', 'success')
    } catch (error) {
      pushToast(error?.message || 'Failed to execute paper option candidate.', 'error')
    } finally {
      setRunningDeskKey('')
    }
  }

  async function handleRefreshOptionPositions() {
    setRunningDeskKey('options-refresh')
    try {
      await refreshOptionsAutomationPositions({})
      await loadSummary()
      pushToast('Open option quotes refreshed.', 'success')
    } catch (error) {
      pushToast(error?.message || 'Failed to refresh open option quotes.', 'error')
    } finally {
      setRunningDeskKey('')
    }
  }

  async function handleSyncOptionsLifecycle() {
    setRunningDeskKey('options-sync')
    try {
      const snapshot = await syncOptionsAutomation()
      setOptionsAutomation(snapshot)
      pushToast('Option lifecycle synced.', 'success')
    } catch (error) {
      pushToast(error?.message || 'Failed to sync option lifecycle.', 'error')
    } finally {
      setRunningDeskKey('')
    }
  }

  async function handleCloseOptionPosition(position) {
    const tradeId = position?.trade_id
    if (!tradeId) {
      pushToast('That position is missing its trade id.', 'warning')
      return
    }
    setRunningDeskKey(`options-close:${tradeId}`)
    try {
      await closeOptionsPaper({ trade_id: tradeId })
      await loadSummary()
      pushToast('Paper sell-to-close submitted.', 'success')
    } catch (error) {
      pushToast(error?.message || 'Failed to submit the paper sell-to-close order.', 'error')
    } finally {
      setRunningDeskKey('')
    }
  }

  function handleOpenDedicatedDesk(deskKey) {
    if (deskKey === SYSTEMATIC_DESK_KEY) {
      navigate(SYSTEMATIC_DESK_ROUTE)
    }
  }

  if (loading) {
    return <LoadingBlock label={showDeskRegistry ? 'Loading strategy desks...' : 'Loading systematic desk...'} />
  }

  const detailCard = (
    <SectionCard
      title={selectedDesk?.name || (focusedDeskKey === SYSTEMATIC_DESK_KEY ? 'Systematic Equities Desk' : 'Desk detail')}
      subtitle={selectedDesk?.metadata?.description || 'Select a desk to inspect runs, targets, and controls.'}
      actions={selectedDesk ? (
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
          <Button
            type="button"
            variant="solid"
            size="sm"
            onClick={() => handleRunDesk(selectedDesk.desk_key)}
            disabled={Boolean(runningDeskKey)}
          >
            {runningDeskKey === selectedDesk.desk_key ? 'Running...' : 'Run desk'}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => handleRunBacktest(selectedDesk.desk_key)}
            disabled={Boolean(runningDeskKey)}
          >
            {runningDeskKey === `backtest:${selectedDesk.desk_key}` ? 'Backtesting...' : 'Run backtest'}
          </Button>
          <Button
            type="button"
            variant="subtle"
            size="sm"
            onClick={() => handleToggleField(selectedDesk.desk_key, 'enabled', !selectedDesk.enabled)}
          >
            {selectedDesk.enabled ? 'Disable' : 'Enable'}
          </Button>
          <Button
            type="button"
            variant="subtle"
            size="sm"
            onClick={() => handleToggleField(selectedDesk.desk_key, 'paper_trading_enabled', !selectedDesk.paper_trading_enabled)}
          >
            {selectedDesk.paper_trading_enabled ? 'Paper off' : 'Paper on'}
          </Button>
          {!focusedDeskKey && selectedDesk.desk_key === SYSTEMATIC_DESK_KEY ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => handleOpenDedicatedDesk(selectedDesk.desk_key)}
            >
              Open dedicated tab
            </Button>
          ) : null}
        </div>
      ) : null}
    >
      {selectedDesk ? (
        <div className="ui-stack-md">
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
            <Chip tone={selectedDesk.enabled ? 'positive' : 'negative'} size="sm">{selectedDesk.enabled ? 'enabled' : 'disabled'}</Chip>
            <Chip tone={selectedDesk.paper_trading_enabled ? 'warning' : 'neutral'} size="sm">
              {selectedDesk.paper_trading_enabled ? 'paper-tradable' : 'research-only'}
            </Chip>
            <Chip tone="neutral" size="sm">{selectedDesk.lifecycle_stage}</Chip>
            <Chip tone="neutral" size="sm">{selectedDesk.trading_mode}</Chip>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: '0.75rem' }}>
            <div className="ui-panel ui-panel--section">
              <div className="ui-kicker">Latest publication</div>
              <strong>{deskMetrics?.latest_publication?.targets?.length || 0} targets</strong>
              <div>Confidence {formatNumber(deskMetrics?.latest_publication?.confidence_score, 3)}</div>
              <div>Risk {formatNumber(deskMetrics?.latest_publication?.risk_estimate, 3)}</div>
            </div>
            <div className="ui-panel ui-panel--section">
              <div className="ui-kicker">Desk PnL</div>
              <strong>{formatNumber(deskMetrics?.latest_pnl_snapshot?.gross_exposure, 3)} gross</strong>
              <div>Net {formatNumber(deskMetrics?.latest_pnl_snapshot?.net_exposure, 3)}</div>
              <div>Drawdown {formatNumber(deskMetrics?.latest_pnl_snapshot?.max_drawdown_pct, 2)}%</div>
            </div>
            <div className="ui-panel ui-panel--section">
              <div className="ui-kicker">Runtime</div>
              <strong>{deskDetail?.desk?.runtime?.last_status || 'idle'}</strong>
              <div>Last signal {deskDetail?.desk?.runtime?.last_signal_type || '--'}</div>
              <div>Last targets {deskDetail?.desk?.runtime?.last_target_count ?? '--'}</div>
            </div>
          </div>
          <SectionCard title="Recent runs" subtitle="Latest strategy outputs and validation states.">
            <div className="ui-stack-sm">
              {(deskDetail?.runs || []).slice(0, 6).map((run) => (
                <div key={run.id} className="ui-panel ui-panel--section">
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.5rem', flexWrap: 'wrap' }}>
                    <strong>{run.status}</strong>
                    <span>{run.created_at || '--'}</span>
                  </div>
                  <div>Signal: {run.signal?.signal_type || '--'}</div>
                  <div>Targets: {run.target_count}</div>
                  <div>Confidence: {formatNumber(run.signal?.confidence_score, 3)}</div>
                  <div>{run.validation?.detail || '--'}</div>
                </div>
              ))}
              {!deskDetail?.runs?.length ? <div>No runs yet.</div> : null}
            </div>
          </SectionCard>
          <SectionCard title="Recent backtests" subtitle="Research and promotion evidence.">
            <div className="ui-stack-sm">
              {(deskDetail?.backtests || []).slice(0, 4).map((run) => (
                <div key={run.id} className="ui-panel ui-panel--section">
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.5rem', flexWrap: 'wrap' }}>
                    <strong>{run.status}</strong>
                    <span>{run.created_at || '--'}</span>
                  </div>
                  <div>Trades: {run.summary?.trade_count ?? 0}</div>
                  <div>Net PnL: {formatNumber(run.summary?.net_pnl, 2)}</div>
                  <div>Sharpe: {formatNumber(run.summary?.sharpe_ratio, 3)}</div>
                  <div>Max DD: {formatNumber(run.summary?.max_drawdown_pct, 2)}%</div>
                </div>
              ))}
              {!deskDetail?.backtests?.length ? <div>No backtests yet.</div> : null}
            </div>
          </SectionCard>
        </div>
      ) : (
        <div>Select a strategy desk.</div>
      )}
    </SectionCard>
  )

  const optionsCard = (
    <SectionCard
      title="Options automation"
      subtitle="Paper-only long calls and puts with Alpaca quote gates. Live, linked-account, short-premium, and spread routing stay blocked."
      actions={(
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={handleScanOptions}
            disabled={Boolean(runningDeskKey)}
          >
            {runningDeskKey === 'options-scan' ? 'Scanning...' : 'Scan options'}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={handleRefreshOptionPositions}
            disabled={Boolean(runningDeskKey)}
          >
            {runningDeskKey === 'options-refresh' ? 'Refreshing...' : 'Refresh positions'}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={handleSyncOptionsLifecycle}
            disabled={Boolean(runningDeskKey)}
          >
            {runningDeskKey === 'options-sync' ? 'Syncing...' : 'Sync lifecycle'}
          </Button>
          <Button
            type="button"
            variant="solid"
            size="sm"
            onClick={handleExecuteOptionsPaper}
            disabled={Boolean(runningDeskKey) || !optionsAutomation?.ready_candidate_count}
          >
            {runningDeskKey === 'options-execute' ? 'Submitting...' : 'Execute paper option'}
          </Button>
        </div>
      )}
    >
      <div className="ui-stack-sm">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: '0.75rem' }}>
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Readiness</div>
            <strong>{optionsAutomation?.readiness_label || String(optionsAutomation?.readiness_state || optionsAutomation?.status || 'idle').replaceAll('_', ' ')}</strong>
            <div>Feed {optionsAutomation?.feed || 'opra'} | OPRA {optionsLifecycle?.opra_entitlement_status || 'unknown'}</div>
            <div>Limit orders only</div>
          </div>
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Scan rate</div>
            <strong>{optionsAutomation?.scan_interval_seconds ?? 30}s</strong>
            <div>Trading engine or faster</div>
            <div>Last scan {formatDateTime(optionsAutomation?.created_at)}</div>
          </div>
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Contracts</div>
            <strong>{optionsAutomation?.ready_candidate_count ?? 0} ready</strong>
            <div>{optionsAutomation?.candidate_count ?? 0} checked</div>
            <div>{optionsAutomation?.ticker_count ?? 0} underlyings</div>
          </div>
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Funds basis</div>
            <strong>{formatCurrency(optionsAutomation?.summary?.account_summary?.effective_funds)}</strong>
            <div>{optionsAutomation?.summary?.account_summary?.funds_source || 'unavailable'}</div>
            <div>Premium risk capped</div>
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: '0.75rem' }}>
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Scheduled runtime</div>
            <strong>{optionsAutomation?.automation_enabled ? (optionsAutomation?.automation_armed ? 'armed' : 'enabled') : 'disabled'}</strong>
            <div>Profile {optionsAutomation?.automation_profile_key || 'personal_paper'}</div>
            <div>Last cycle {formatDateTime(optionsAutomation?.last_scheduled_cycle_at)}</div>
          </div>
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Last entry</div>
            <strong>{optionsAutomation?.latest_paper_execution?.payload?.contract_symbol || '--'}</strong>
            <div>{formatDateTime(optionsAutomation?.last_scheduled_entry_at || optionsAutomation?.latest_paper_execution?.created_at)}</div>
            <div>{optionsAutomation?.latest_paper_execution?.event_type || 'No paper option entry yet.'}</div>
          </div>
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Quote refresh</div>
            <strong>{optionsLifecycle?.sell_ready_count ?? 0} sell ready</strong>
            <div>{optionsLifecycle?.open_position_count ?? 0} open option positions</div>
            <div>{formatDateTime(optionsAutomation?.latest_quote_refresh?.created_at)}</div>
          </div>
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Last exit</div>
            <strong>{optionsAutomation?.latest_paper_exit?.payload?.contract_symbol || '--'}</strong>
            <div>{formatDateTime(optionsAutomation?.last_scheduled_exit_at || optionsAutomation?.latest_paper_exit?.created_at)}</div>
            <div>{optionsAutomation?.latest_paper_exit?.event_type || 'No paper option exit yet.'}</div>
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: '0.75rem' }}>
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Validation</div>
            <strong>{optionsValidation?.readiness_label || 'collecting lifecycle evidence'}</strong>
            <div>Clean cycles {(optionsValidation?.clean_cycle_count ?? 0)}/{optionsValidation?.required_clean_cycles ?? 5}</div>
            <div>Clean entries {optionsValidation?.clean_entry_count ?? 0} | Clean exits {optionsValidation?.clean_exit_count ?? 0}</div>
          </div>
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Blocked counts</div>
            <strong>{optionsValidation?.blocked_entry_count ?? 0} entry</strong>
            <div>{optionsValidation?.blocked_exit_count ?? 0} exit</div>
            <div>{optionsValidation?.stale_quote_block_count ?? 0} stale/wide quote blocks</div>
          </div>
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Broker sync</div>
            <strong>{formatDateTime(optionsValidation?.last_broker_sync_at)}</strong>
            <div>Last clean lifecycle {formatDateTime(optionsValidation?.last_clean_lifecycle_at)}</div>
            <div>Working {optionsValidation?.working_order_count ?? 0} | Orphans {optionsValidation?.orphan_event_count ?? 0}</div>
          </div>
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Next step</div>
            <strong>{optionsValidation?.next_step || optionsAutomation?.next_step || 'Keep collecting unchanged.'}</strong>
          </div>
        </div>
        {optionsValidation?.recent_clean_cycles?.length ? (
          <div className="ui-panel ui-panel--section">
            <strong>Recent clean cycles</strong>
            {(optionsValidation.recent_clean_cycles || []).slice(0, 5).map((item, index) => (
              <div key={`${item.trade_id || item.contract_symbol || 'cycle'}-${index}`}>
                {item.contract_symbol || '--'} | Entry {formatDateTime(item.entry_at)} | Exit {formatDateTime(item.exit_at)}
              </div>
            ))}
          </div>
        ) : null}
        {optionsAutomation?.last_scheduled_blocker ? (
          <div className="ui-panel ui-panel--section">
            <strong>Last scheduled blocker</strong>
            <div>{optionsAutomation.last_scheduled_blocker}</div>
          </div>
        ) : null}
        {optionsAutomation?.blocked_reason ? (
          <div className="ui-panel ui-panel--section">
            <strong>Blocked</strong>
            <div>{optionsAutomation.blocked_reason}</div>
          </div>
        ) : null}
        {optionsAutomation?.blockers?.length ? (
          <div className="ui-panel ui-panel--section">
            <strong>Lifecycle blockers</strong>
            {(optionsAutomation.blockers || []).map((item) => (
              <div key={item}>{item}</div>
            ))}
          </div>
        ) : null}
        {optionsValidation?.orphan_events?.length ? (
          <div className="ui-panel ui-panel--section">
            <strong>Unmatched lifecycle events</strong>
            {(optionsValidation.orphan_events || []).map((item, index) => (
              <div key={`${item.event_type || 'event'}-${item.trade_id || item.contract_symbol || index}`}>
                {item.event_type || 'event'}: {item.detail || 'Unmatched option lifecycle event.'}
              </div>
            ))}
          </div>
        ) : null}
        <div className="ui-panel ui-panel--section">
          <div className="ui-kicker">Open option positions</div>
          <div className="ui-stack-sm">
            {(optionsAutomation?.open_positions || []).map((position) => (
              <div key={position.trade_id || position.contract_symbol} className="ui-panel ui-panel--section">
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
                  <strong>{position.contract_symbol}</strong>
                  <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
                    <Chip tone={position.sell_ready ? 'positive' : 'warning'} size="sm">
                      {position.sell_ready ? 'sell ready' : 'blocked'}
                    </Chip>
                    <Button
                      type="button"
                      variant="subtle"
                      size="sm"
                      onClick={() => handleCloseOptionPosition(position)}
                      disabled={Boolean(runningDeskKey) || !position.sell_ready}
                    >
                      {runningDeskKey === `options-close:${position.trade_id}` ? 'Closing...' : 'Close paper option'}
                    </Button>
                  </div>
                </div>
                <div>{position.ticker} {String(position.option_right || '').toUpperCase()} | {position.expiration || '--'} | Strike {formatNumber(position.strike, 2)} | Qty {formatNumber(position.quantity, 0)}</div>
                <div>Bid {formatNumber(position.bid, 2)} | Ask {formatNumber(position.ask, 2)} | Mid {formatNumber(position.mid, 2)} | Sell limit {formatNumber(position.sell_limit_price, 2)}</div>
                <div>Quote age {formatNumber(position.quote_age_seconds, 1)}s | Value {formatCurrency(position.current_value)} | Unrealized {formatCurrency(position.unrealized_pnl)}</div>
                <div>{position.sell_block_detail || 'Current sell-to-close price is using the refreshed bid-side quote.'}</div>
                <div>Last refreshed {formatDateTime(position.refreshed_at)} | Quote time {formatDateTime(position.quote_timestamp)}</div>
              </div>
            ))}
            {!optionsAutomation?.open_positions?.length ? <div>No open paper option positions yet.</div> : null}
          </div>
        </div>
        <div className="ui-stack-sm">
          {(optionsAutomation?.candidates || []).slice(0, 8).map((candidate) => (
            <div key={candidate.contract_symbol} className="ui-panel ui-panel--section">
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.5rem', flexWrap: 'wrap' }}>
                <strong>{candidate.contract_symbol}</strong>
                <Chip tone={candidate.ready_to_execute ? 'positive' : 'neutral'} size="sm">
                  {candidate.ready_to_execute ? 'ready' : 'filtered'}
                </Chip>
              </div>
              <div>{candidate.underlying} {String(candidate.right || '').toUpperCase()} | {candidate.expiration || '--'} | Strike {formatNumber(candidate.strike, 2)}</div>
              <div>Bid {formatNumber(candidate.bid, 2)} | Ask {formatNumber(candidate.ask, 2)} | Mid {formatNumber(candidate.mid, 2)} | Limit {formatNumber(candidate.entry_limit_price, 2)}</div>
              <div>Spread {formatPercent(candidate.spread_pct)} | Age {formatNumber(candidate.quote_age_seconds, 1)}s | Vol {candidate.volume ?? 0} | OI {candidate.open_interest ?? 0}</div>
              <div>{candidate.rejection_reasons?.length ? candidate.rejection_reasons.join(' ') : 'Current price, liquidity, freshness, and risk gates passed.'}</div>
            </div>
          ))}
          {!optionsAutomation?.candidates?.length ? <div>No option scan has been run yet.</div> : null}
        </div>
      </div>
    </SectionCard>
  )

  const allocatorRiskCard = (
    <SectionCard title="Allocator and risk" subtitle="Shared control plane across internal desks.">
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '0.75rem' }}>
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={handleRefreshExecution}
            disabled={Boolean(runningDeskKey) || !latestExecution?.latest_execution_run_id}
          >
            {runningDeskKey === 'portfolio-execution-sync' ? 'Refreshing...' : 'Refresh execution'}
          </Button>
          <Button
            type="button"
            variant="solid"
            size="sm"
            onClick={handleExecutePaperBasket}
            disabled={Boolean(runningDeskKey)}
          >
            {runningDeskKey === 'portfolio-execution' ? 'Executing...' : 'Execute paper basket'}
          </Button>
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: '0.75rem' }}>
        <div className="ui-panel ui-panel--section">
          <div className="ui-kicker">Allocator</div>
          <strong>{allocator?.status || latestTargets?.status || 'idle'}</strong>
          <div>Gross {formatNumber(allocator?.metrics?.gross_exposure ?? latestTargets?.metrics?.gross_exposure, 3)}</div>
          <div>Net {formatNumber(allocator?.metrics?.net_exposure ?? latestTargets?.metrics?.net_exposure, 3)}</div>
        </div>
        <div className="ui-panel ui-panel--section">
          <div className="ui-kicker">Risk</div>
          <strong>{risk?.allowed ? 'Allowed' : 'Blocked'}</strong>
          <div>Targets {risk?.target_count ?? 0}</div>
          <div>Symbols {risk?.symbol_count ?? 0}</div>
        </div>
        <div className="ui-panel ui-panel--section">
          <div className="ui-kicker">Portfolio targets</div>
          <strong>{latestTargets?.targets?.length || 0}</strong>
          <div>Run {latestTargets?.latest_run_id || '--'}</div>
          <div>Status {latestTargets?.status || '--'}</div>
        </div>
        <div className="ui-panel ui-panel--section">
          <div className="ui-kicker">Last execution</div>
          <strong>{latestExecution?.status || 'idle'}</strong>
          <div>
            <Chip tone={readinessTone(lifecycleValidation.readiness_state)} size="sm">
              {lifecycleValidation.readiness_label || 'collecting lifecycle evidence'}
            </Chip>
          </div>
          <div>Working {latestExecution?.working_count ?? 0} | Partial {latestExecution?.partial_fill_count ?? 0}</div>
          <div>Filled {latestExecution?.filled_count ?? 0} | Rejected {latestExecution?.rejected_count ?? 0}</div>
        </div>
        <div className="ui-panel ui-panel--section">
          <div className="ui-kicker">Lifecycle validation</div>
          <strong>{lifecycleValidation.readiness_label || 'collecting lifecycle evidence'}</strong>
          <div>Submitted {lifecycleValidation.submitted_count ?? 0} | Working {lifecycleValidation.working_count ?? 0}</div>
          <div>Partial {lifecycleValidation.partial_fill_count ?? 0} | Filled {lifecycleValidation.filled_count ?? 0}</div>
          <div>Orphans {lifecycleValidation.orphan_event_count ?? 0} | Broker-linked {lifecycleValidation.broker_linked_item_count ?? 0}</div>
        </div>
      </div>
      <div className="ui-stack-sm" style={{ marginTop: '1rem' }}>
        {(latestTargets?.targets || []).slice(0, 12).map((target) => (
          <div key={`${target.symbol}-${target.target_weight}`} className="ui-panel ui-panel--section">
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.5rem', flexWrap: 'wrap' }}>
              <strong>{target.symbol}</strong>
              <span>{formatPercent(target.target_weight)}</span>
            </div>
            <div>Notional {formatNumber(target.target_notional, 2)}</div>
            <div>Directions {(target.directions || []).join(', ') || '--'}</div>
            <div>Risk flags {(target.risk_flags || []).join(', ') || 'none'}</div>
          </div>
        ))}
        {!latestTargets?.targets?.length ? <div>No portfolio targets published yet.</div> : null}
      </div>
      <div className="ui-stack-sm" style={{ marginTop: '1rem' }}>
        <div className="ui-kicker">Execution summary</div>
        <div className="ui-panel ui-panel--section">
          <div>Status {latestExecution?.status || '--'}</div>
          <div>Execution run {latestExecution?.latest_execution_run_id || '--'}</div>
          <div>Portfolio target run {latestExecution?.portfolio_target_run_id || '--'}</div>
          <div>Executed {latestExecution?.summary?.executed_count ?? 0} | Skipped {latestExecution?.summary?.skipped_count ?? 0} | Blocked {latestExecution?.summary?.blocked_count ?? 0}</div>
          <div>Working {latestExecution?.working_count ?? 0} | Partial {latestExecution?.partial_fill_count ?? 0} | Filled {latestExecution?.filled_count ?? 0}</div>
          <div>Canceled {latestExecution?.canceled_count ?? 0} | Rejected {latestExecution?.rejected_count ?? 0} | Orphan {latestExecution?.orphan_event_count ?? 0}</div>
          <div>Last sync {latestExecution?.last_sync_at || '--'}</div>
          <div>{lifecycleValidation.next_step || latestExecution?.summary?.blocked_reason || 'Paper execution is ready when allocator and risk are accepted.'}</div>
          {Array.isArray(lifecycleValidation.blockers) && lifecycleValidation.blockers.length ? (
            <div>Blockers: {lifecycleValidation.blockers.join(' ')}</div>
          ) : null}
        </div>
        {Array.isArray(lifecycleValidation.orphan_events) && lifecycleValidation.orphan_events.length ? (
          <div className="ui-panel ui-panel--section">
            <div className="ui-kicker">Orphan event review</div>
            {lifecycleValidation.orphan_events.slice(0, 4).map((event) => (
              <div key={event.id || `${event.symbol}-${event.created_at}`}>
                {event.symbol || '--'} | {event.event_key || '--'} | {event.reason || 'Unmatched order event.'}
              </div>
            ))}
          </div>
        ) : null}
        {(latestExecution?.items || []).slice(0, 8).map((item) => (
          <div key={item.id || `${item.symbol}-${item.action}`} className="ui-panel ui-panel--section">
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.5rem', flexWrap: 'wrap' }}>
              <strong>{item.symbol}</strong>
              <span>{item.action} | {item.reconciliation_status || item.status}</span>
            </div>
            <div>Desk {item.strategy_desk_key || '--'}</div>
            <div>Delta {formatNumber(item.requested_delta_quantity, 3)}</div>
            <div>Filled {formatNumber(item.filled_quantity, 3)} | Remaining {formatNumber(item.remaining_quantity, 3)}</div>
            <div>Broker {item.broker_status || '--'} | Avg fill {formatNumber(item.average_fill_price, 4)}</div>
                    <div>{item.reason || 'Submitted through Alpaca paper routing.'}</div>
          </div>
        ))}
        {!latestExecution?.items?.length ? <div>No paper basket executions recorded yet.</div> : null}
      </div>
    </SectionCard>
  )

  const content = (
    <div className="ui-stack-lg">
      <AiDeskManagerPanel selectedDeskKey={selectedDeskKey} onChanged={loadSummary} />
      {detailCard}
      {optionsCard}
      {allocatorRiskCard}
    </div>
  )

  return (
    <div className="ui-stack-lg">
      <PageIntro
        kicker={pageKicker}
        title={pageTitle}
        description={pageDescription}
        badge={introBadge}
        helper={pageHelper}
      />

      {showDeskRegistry ? (
        <div className="ui-grid ui-grid--2" style={{ display: 'grid', gridTemplateColumns: 'minmax(260px, 320px) 1fr', gap: '1rem' }}>
          <SectionCard title="Desk registry" subtitle="Internal strategy desks under the active organization.">
            {desks.map((item) => (
              <DeskListItem
                key={item.desk_key}
                item={item}
                active={item.desk_key === selectedDeskKey}
                onSelect={setSelectedDeskKey}
                onOpenDedicated={item.desk_key === SYSTEMATIC_DESK_KEY ? handleOpenDedicatedDesk : null}
              />
            ))}
          </SectionCard>
          {content}
        </div>
      ) : (
        content
      )}
    </div>
  )
}
