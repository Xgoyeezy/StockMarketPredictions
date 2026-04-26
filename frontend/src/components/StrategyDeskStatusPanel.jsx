import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  getAllocatorSnapshot,
  getLatestPortfolioTargetExecution,
  getLatestPortfolioTargets,
  getRiskSnapshot,
  getStrategyDesks,
} from '../api/client'
import Button from './Button'
import Chip from './Chip'
import MetricCard from './MetricCard'
import SectionCard from './SectionCard'

function formatNumber(value, digits = 2) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric.toFixed(digits) : '--'
}

function formatPercent(value) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? `${(numeric * 100).toFixed(1)}%` : '--'
}

function toneForStatus(value) {
  const normalized = String(value || '').trim().toLowerCase()
  if (['accepted', 'completed', 'healthy', 'ready'].includes(normalized)) return 'positive'
  if (['filled'].includes(normalized)) return 'positive'
  if (['blocked', 'failed', 'error', 'rejected'].includes(normalized)) return 'negative'
  if (['research', 'collecting', 'pending', 'warning', 'idle', 'insufficient_history', 'working', 'partially_filled', 'reconciliation_warning'].includes(normalized)) return 'warning'
  return 'neutral'
}

function executionValidation(snapshot) {
  return snapshot?.validation_artifact || snapshot?.summary?.validation_artifact || {
    readiness_state: 'collecting_lifecycle_evidence',
    readiness_label: 'collecting lifecycle evidence',
    blockers: [],
    next_step: 'Run macro/stat-arb desks, execute a personal-paper basket, then refresh execution to collect lifecycle evidence.',
  }
}

function buildDeskStatusLabel(item) {
  if (item?.latest_run?.status) return item.latest_run.status
  if (item?.latest_backtest?.status) return item.latest_backtest.status
  return item?.enabled ? 'idle' : 'disabled'
}

export default function StrategyDeskStatusPanel({
  eyebrow = 'Quant desks',
  title = 'Strategy desk status',
  subtitle = 'Allocator, risk, and validation readiness across the internal multi-desk runtime.',
}) {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [payload, setPayload] = useState({
    desks: null,
    allocator: null,
    risk: null,
    latestTargets: null,
    latestExecution: null,
  })

  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        if (!cancelled) setError('')
        const [desks, allocator, risk, latestTargets, latestExecution] = await Promise.all([
          getStrategyDesks(),
          getAllocatorSnapshot(),
          getRiskSnapshot(),
          getLatestPortfolioTargets(),
          getLatestPortfolioTargetExecution(),
        ])
        if (!cancelled) {
          setPayload({ desks, allocator, risk, latestTargets, latestExecution })
          setLoading(false)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err?.response?.data?.detail || err?.message || 'Failed to load strategy desk status.')
          setLoading(false)
        }
      }
    }

    load()
    const timer = window.setInterval(load, 60000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  const desks = Array.isArray(payload.desks?.items) ? payload.desks.items : []
  const enabledCount = desks.filter((item) => item.enabled).length
  const paperCount = desks.filter((item) => item.paper_trading_enabled).length
  const researchCount = desks.filter((item) => !item.paper_trading_enabled).length
  const acceptedRunCount = desks.filter((item) => item.latest_run?.status === 'accepted').length
  const completedBacktestCount = desks.filter((item) => item.latest_backtest?.status === 'completed').length
  const targetRows = Array.isArray(payload.latestTargets?.targets) ? payload.latestTargets.targets.slice(0, 5) : []
  const latestExecutionSummary = payload.latestExecution?.summary || {}
  const lifecycleValidation = executionValidation(payload.latestExecution)
  const highlightedDesks = useMemo(
    () =>
      desks
        .slice()
        .sort((left, right) => {
          const leftPaper = left.paper_trading_enabled ? 0 : 1
          const rightPaper = right.paper_trading_enabled ? 0 : 1
          if (leftPaper !== rightPaper) return leftPaper - rightPaper
          return String(left.name || '').localeCompare(String(right.name || ''))
        })
        .slice(0, 5),
    [desks],
  )

  return (
    <SectionCard
      eyebrow={eyebrow}
      title={title}
      subtitle={subtitle}
      actions={(
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
          <Chip tone={toneForStatus(payload.allocator?.status)} size="sm">
            {payload.allocator?.status || 'idle'}
          </Chip>
          <Button type="button" variant="ghost" size="sm" onClick={() => navigate('/strategy-desks')}>
            Open desks
          </Button>
        </div>
      )}
    >
      {error ? <p className="ui-note">{error}</p> : null}
      <div className="metric-grid">
        <MetricCard label="Enabled desks" value={`${enabledCount}/${desks.length || 0}`} helper={`${paperCount} paper | ${researchCount} research`} />
        <MetricCard
          label="Allocator"
          value={payload.allocator?.status || (loading ? 'Loading...' : '--')}
          helper={`${payload.allocator?.metrics?.target_count ?? 0} targets | ${payload.allocator?.metrics?.desk_count ?? 0} desks`}
          tone={toneForStatus(payload.allocator?.status)}
        />
        <MetricCard
          label="Risk gate"
          value={payload.risk?.allowed ? 'pass' : loading ? 'Loading...' : 'block'}
          helper={`Gross ${formatPercent(payload.risk?.gross_exposure)} | Net ${formatPercent(payload.risk?.net_exposure)}`}
          tone={payload.risk?.allowed ? 'positive' : payload.risk ? 'negative' : 'default'}
        />
        <MetricCard label="Validated runs" value={acceptedRunCount} helper="Accepted strategy runs" />
        <MetricCard label="Backtests" value={completedBacktestCount} helper="Completed desk backtests" />
        <MetricCard
          label="Portfolio targets"
          value={targetRows.length}
          helper={payload.latestTargets?.created_at ? `Latest ${new Date(payload.latestTargets.created_at).toLocaleString()}` : 'No target publication yet'}
        />
        <MetricCard
          label="Last execution"
          value={lifecycleValidation.readiness_label || payload.latestExecution?.status || (loading ? 'Loading...' : '--')}
          helper={`${payload.latestExecution?.working_count ?? 0} working | ${payload.latestExecution?.filled_count ?? 0} filled | ${payload.latestExecution?.orphan_event_count ?? 0} orphan`}
          tone={toneForStatus(lifecycleValidation.readiness_state || payload.latestExecution?.status)}
        />
      </div>

      <div className="grid-two" style={{ marginTop: '1rem' }}>
        <div>
          <div className="ui-kicker">Desk readiness</div>
          <ul className="simple-list">
            {highlightedDesks.map((item) => (
              <li key={item.desk_key}>
                {item.name} | {item.lifecycle_stage} | {buildDeskStatusLabel(item)}
              </li>
            ))}
            {!highlightedDesks.length ? <li>{loading ? 'Loading strategy desk registry...' : 'No strategy desks are seeded yet.'}</li> : null}
          </ul>
        </div>
        <div>
          <div className="ui-kicker">Exposure breakdown</div>
          <ul className="simple-list">
            {targetRows.map((item) => (
              <li key={`${item.symbol}-${item.target_weight}`}>
                {item.symbol} | {formatPercent(item.target_weight)} | {(item.desk_contributions || []).map((row) => row.desk_key).join(', ') || 'Allocator'}
              </li>
            ))}
            {!targetRows.length ? <li>{loading ? 'Building allocator snapshot...' : 'No aggregated desk targets are active yet.'}</li> : null}
          </ul>
        </div>
        <div>
          <div className="ui-kicker">Last execution</div>
          <ul className="simple-list">
            <li>Status | {payload.latestExecution?.status || (loading ? 'Loading...' : '--')}</li>
            <li>Working | {payload.latestExecution?.working_count ?? 0}</li>
            <li>Partial fills | {payload.latestExecution?.partial_fill_count ?? 0}</li>
            <li>Filled | {payload.latestExecution?.filled_count ?? 0}</li>
            <li>Rejected | {payload.latestExecution?.rejected_count ?? 0}</li>
            <li>Orphans | {payload.latestExecution?.orphan_event_count ?? 0}</li>
            <li>Last sync | {payload.latestExecution?.last_sync_at || '--'}</li>
            <li>Executed | {latestExecutionSummary.executed_count ?? 0}</li>
            <li>Skipped | {latestExecutionSummary.skipped_count ?? 0}</li>
            <li>Blocked | {latestExecutionSummary.blocked_count ?? 0}</li>
            <li>Readiness | {lifecycleValidation.readiness_label || '--'}</li>
            <li>Next | {lifecycleValidation.next_step || '--'}</li>
            {latestExecutionSummary.blocked_reason ? <li>{latestExecutionSummary.blocked_reason}</li> : null}
            {Array.isArray(lifecycleValidation.blockers) && lifecycleValidation.blockers.length ? (
              <li>Blockers | {lifecycleValidation.blockers.join(' ')}</li>
            ) : null}
          </ul>
        </div>
      </div>
    </SectionCard>
  )
}
