import { useCallback, useEffect, useMemo, useState } from 'react'
import ErrorState from '../components/ErrorState'
import ListTable from '../components/ListTable'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import StatusBadge from '../components/StatusBadge'
import Button from '../components/Button'
import { getEvidenceOutcomesSummary, getProfessionalBenchmarkSummary } from '../api/client'

function formatNumber(value, digits = 2) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '--'
  return numeric.toFixed(digits)
}

function formatRatio(value) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '--'
  return `${(numeric * 100).toFixed(1)}%`
}

function humanize(value, fallback = 'Unknown') {
  const text = String(value || '').trim()
  if (!text) return fallback
  return text.replace(/[_-]+/g, ' ').replace(/\b\w/g, (match) => match.toUpperCase())
}

function verdictTone(verdict) {
  if (verdict === 'edge_detected') return 'positive'
  if (verdict === 'weak_edge_detected') return 'warning'
  if (verdict === 'no_edge_detected') return 'negative'
  if (verdict === 'data_quality_too_weak') return 'warning'
  return 'neutral'
}

function DataTable({ columns, rows, empty }) {
  return (
    <ListTable>
      <table className="ui-list-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key}>{column.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length ? rows.map((row, index) => (
            <tr key={row.key || row.score_bucket || row.blocker || row.ai_verdict || row.setup_type || row.engine || row.regime || row.prediction_id || index}>
              {columns.map((column) => (
                <td key={column.key}>{column.render ? column.render(row) : row[column.key]}</td>
              ))}
            </tr>
          )) : (
            <tr>
              <td colSpan={columns.length}>{empty}</td>
            </tr>
          )}
        </tbody>
      </table>
    </ListTable>
  )
}

function SectionStatus({ section }) {
  return (
    <StatusBadge tone={section?.available ? 'positive' : 'warning'}>
      {section?.available ? 'Available' : 'Needs data'}
    </StatusBadge>
  )
}

export default function ProfessionalBenchmarkPage() {
  const [report, setReport] = useState(null)
  const [outcomeReport, setOutcomeReport] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [benchmarkPayload, outcomePayload] = await Promise.all([
        getProfessionalBenchmarkSummary(),
        getEvidenceOutcomesSummary(),
      ])
      setReport(benchmarkPayload)
      setOutcomeReport(outcomePayload)
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load Professional Benchmark analytics.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const summary = report?.summary || {}
  const outcomeSummary = outcomeReport?.summary || {}
  const sections = report?.sections || {}
  const aggregations = report?.aggregations || {}
  const baselineRows = report?.baselines?.items || []
  const scoreBucketSection = sections.score_bucket_separation || aggregations.score_bucket_separation || {}
  const blockerSection = sections.blocker_value || aggregations.blocker_value || {}
  const aiSection = sections.ai_verdict_accuracy || aggregations.ai_verdict_accuracy || {}
  const forecastSection = sections.forecast_accuracy || aggregations.forecast_accuracy || {}
  const executionSection = sections.execution_quality || aggregations.execution_quality || {}
  const rewardBySetup = sections.reward_by_setup?.items || aggregations.reward_by_setup || []
  const rewardByEngine = sections.reward_by_engine?.items || aggregations.reward_by_engine || []
  const rewardByRegime = sections.reward_by_regime?.items || aggregations.reward_by_regime || []
  const warnings = report?.warnings || []
  const safetyNotes = report?.safety_notes || []
  const missingFields = useMemo(() => (
    Object.entries(report?.missing_fields || {})
      .sort((left, right) => Number(right[1]) - Number(left[1]))
      .map(([field, count]) => ({ field, count }))
  ), [report?.missing_fields])

  return (
    <div className="ui-shell__page">
      <PageIntro
        kicker="Research only"
        title="Professional Benchmark"
        description="Answers whether Quant Evidence OS beat simple baselines after costs. Benchmark outputs are proof reports only and do not affect execution."
        badge="No trading authority"
      />
      {error ? <ErrorState description={error} onAction={load} /> : null}

      <div className="ui-action-row">
        <StatusBadge tone={verdictTone(summary.benchmark_verdict)}>{humanize(summary.benchmark_verdict || 'insufficient_evidence')}</StatusBadge>
        <StatusBadge tone="neutral">Research only</StatusBadge>
        <StatusBadge tone="neutral">Does not place orders</StatusBadge>
        <StatusBadge tone="neutral">Does not change ranking weights</StatusBadge>
        <StatusBadge tone="warning">No guaranteed returns</StatusBadge>
        <Button type="button" variant="ghost" size="sm" onClick={load} disabled={loading}>
          Refresh
        </Button>
      </div>

      <SectionCard title="Benchmark Verdict" subtitle={summary.verdict_reason || 'Waiting for rewardable evidence and explicit baselines.'}>
        <div className="ui-dashboard-grid">
          <MetricCard label="Verdict" value={humanize(summary.benchmark_verdict || report?.status || 'insufficient_evidence')} helper={summary.sample_size_warning ? 'Sample-size warning active' : 'Sample size passed v1 threshold'} />
          <MetricCard label="Rewardable rows" value={summary.rewardable_count ?? 0} helper={`${summary.candidate_count ?? 0} candidate evidence rows`} />
          <MetricCard label="Average reward" value={formatNumber(summary.average_reward)} helper="Rewardable prediction contracts only" />
          <MetricCard label="Baseline edge" value={formatNumber(summary.baseline_relative_edge)} helper="System expected value minus explicit baseline average" />
          <MetricCard label="Score bucket lift" value={formatNumber(summary.score_bucket_lift)} helper="80+ score buckets minus 0-59 buckets" />
          <MetricCard label="Data quality" value={`${formatNumber(summary.data_quality_score, 0)}%`} helper={summary.out_of_sample_status || 'Out-of-sample labels pending'} />
        </div>
      </SectionCard>

      <SectionCard title="Evidence Outcome Readiness" subtitle="Benchmark blockers are usually missing outcomes, baselines, execution costs, or regime labels. Outcomes are append-only research evidence.">
        <div className="ui-dashboard-grid">
          <MetricCard label="Due horizons" value={outcomeSummary.due_count ?? 0} helper="Matured candidate windows not yet stamped" />
          <MetricCard label="Stamped outcomes" value={outcomeSummary.stamped_outcome_rows ?? 0} helper={`${outcomeSummary.available_outcome_count ?? 0} available outcome rows`} />
          <MetricCard label="Baseline coverage" value={formatRatio(outcomeSummary.baseline_coverage_rate)} helper="Primary baseline present" />
          <MetricCard label="Execution-cost coverage" value={formatRatio(outcomeSummary.execution_cost_coverage_rate)} helper="Spread, slippage, or paper fill cost evidence" />
        </div>
      </SectionCard>

      <SectionCard title="Safety Boundary" subtitle="Benchmark reports are analytics only. They cannot route, approve, or force trades.">
        <div className="ui-dashboard-grid">
          <DataTable
            rows={safetyNotes.map((note, index) => ({ note, index }))}
            empty="Safety notes unavailable."
            columns={[{ key: 'note', label: 'Boundary' }]}
          />
          <DataTable
            rows={warnings.map((warning, index) => ({ warning, index }))}
            empty="No benchmark warnings."
            columns={[{ key: 'warning', label: 'Warning' }]}
          />
        </div>
      </SectionCard>

      <SectionCard title="Baseline Comparison" subtitle="Baselines are only marked available when explicit forward-only baseline fields exist.">
        <DataTable
          rows={baselineRows}
          empty={loading ? 'Loading baselines...' : 'No explicit baseline evidence is available yet.'}
          columns={[
            { key: 'label', label: 'Baseline' },
            { key: 'available', label: 'Status', render: (row) => <StatusBadge tone={row.available ? 'positive' : 'warning'}>{row.available ? 'Available' : 'Missing'}</StatusBadge> },
            { key: 'sample_size', label: 'Rows' },
            { key: 'system_expected_value', label: 'System EV', render: (row) => formatNumber(row.system_expected_value) },
            { key: 'baseline_expected_value', label: 'Baseline EV', render: (row) => formatNumber(row.baseline_expected_value) },
            { key: 'baseline_relative_edge', label: 'Edge', render: (row) => formatNumber(row.baseline_relative_edge) },
            { key: 'reason', label: 'Reason' },
          ]}
        />
      </SectionCard>

      <SectionCard title="Score Bucket Separation" subtitle="A usable edge should show higher buckets outperforming lower buckets after costs.">
        <div className="ui-action-row"><SectionStatus section={scoreBucketSection} /></div>
        <DataTable
          rows={scoreBucketSection.items || []}
          empty={loading ? 'Loading score buckets...' : 'Need rewardable high- and low-score bucket rows.'}
          columns={[
            { key: 'score_bucket', label: 'Bucket', render: (row) => humanize(row.score_bucket) },
            { key: 'candidate_count', label: 'Rows' },
            { key: 'rewardable_count', label: 'Rewardable' },
            { key: 'avg_reward', label: 'Avg reward', render: (row) => formatNumber(row.avg_reward) },
            { key: 'win_rate', label: 'Hit rate', render: (row) => formatRatio(row.win_rate) },
          ]}
        />
      </SectionCard>

      <SectionCard title="Reward By Setup, Engine, And Regime" subtitle="These sections expose where reward was concentrated without changing ranking weights.">
        <div className="ui-dashboard-grid">
          <DataTable
            rows={rewardBySetup.slice(0, 8)}
            empty={loading ? 'Loading setup rewards...' : 'No setup benchmark data yet.'}
            columns={[
              { key: 'setup_type', label: 'Setup', render: (row) => humanize(row.setup_type) },
              { key: 'rewardable_count', label: 'Rewardable' },
              { key: 'avg_reward', label: 'Avg reward', render: (row) => formatNumber(row.avg_reward) },
              { key: 'win_rate', label: 'Hit rate', render: (row) => formatRatio(row.win_rate) },
            ]}
          />
          <DataTable
            rows={rewardByEngine.slice(0, 8)}
            empty={loading ? 'Loading engine rewards...' : 'No engine benchmark data yet.'}
            columns={[
              { key: 'engine', label: 'Engine', render: (row) => humanize(row.engine) },
              { key: 'rewardable_count', label: 'Rewardable' },
              { key: 'avg_reward', label: 'Avg reward', render: (row) => formatNumber(row.avg_reward) },
              { key: 'win_rate', label: 'Hit rate', render: (row) => formatRatio(row.win_rate) },
            ]}
          />
          <DataTable
            rows={rewardByRegime.slice(0, 8)}
            empty={loading ? 'Loading regime rewards...' : 'No regime benchmark data yet.'}
            columns={[
              { key: 'regime', label: 'Regime', render: (row) => humanize(row.regime) },
              { key: 'rewardable_count', label: 'Rewardable' },
              { key: 'avg_reward', label: 'Avg reward', render: (row) => formatNumber(row.avg_reward) },
              { key: 'reward_dispersion', label: 'Dispersion', render: (row) => formatNumber(row.reward_dispersion) },
            ]}
          />
        </div>
      </SectionCard>

      <SectionCard title="Blockers, AI, Forecast, And Execution" subtitle="The suite keeps proof separate from order flow and shows missing data instead of overclaiming.">
        <div className="ui-dashboard-grid">
          <DataTable
            rows={(blockerSection.items || []).slice(0, 8)}
            empty={loading ? 'Loading blocker value...' : 'No blocker outcome data yet.'}
            columns={[
              { key: 'blocker', label: 'Blocker', render: (row) => humanize(row.blocker) },
              { key: 'times_seen', label: 'Seen' },
              { key: 'estimated_blocker_value', label: 'Value', render: (row) => formatNumber(row.estimated_blocker_value) },
              { key: 'false_block_rate', label: 'False block', render: (row) => formatRatio(row.false_block_rate) },
            ]}
          />
          <DataTable
            rows={(aiSection.items || []).slice(0, 8)}
            empty={loading ? 'Loading AI verdict quality...' : 'No AI verdict benchmark data yet.'}
            columns={[
              { key: 'ai_verdict', label: 'AI verdict', render: (row) => humanize(row.ai_verdict) },
              { key: 'rewardable_count', label: 'Rewardable' },
              { key: 'avg_reward', label: 'Avg reward', render: (row) => formatNumber(row.avg_reward) },
              { key: 'win_rate', label: 'Hit rate', render: (row) => formatRatio(row.win_rate) },
            ]}
          />
          <DataTable
            rows={[forecastSection]}
            empty="Forecast section unavailable."
            columns={[
              { key: 'available', label: 'Forecasts', render: (row) => <SectionStatus section={row} /> },
              { key: 'validated_forecasts', label: 'Validated' },
              { key: 'direction_accuracy', label: 'Direction', render: (row) => formatRatio(row.direction_accuracy) },
              { key: 'avg_forecast_reward', label: 'Reward', render: (row) => formatNumber(row.avg_forecast_reward) },
            ]}
          />
          <DataTable
            rows={[executionSection]}
            empty="Execution section unavailable."
            columns={[
              { key: 'available', label: 'Execution', render: (row) => <SectionStatus section={row} /> },
              { key: 'sample_size', label: 'Rows' },
              { key: 'avg_slippage_bps', label: 'Slippage bps', render: (row) => formatNumber(row.avg_slippage_bps) },
              { key: 'slippage_adjusted_reward', label: 'Adj reward', render: (row) => formatNumber(row.slippage_adjusted_reward) },
            ]}
          />
        </div>
      </SectionCard>

      <SectionCard title="Missing Data" subtitle="Missing fields keep benchmark claims honest and prevent fabricated baseline results.">
        <DataTable
          rows={missingFields}
          empty={loading ? 'Loading missing data...' : 'No missing fields reported.'}
          columns={[
            { key: 'field', label: 'Field', render: (row) => humanize(row.field) },
            { key: 'count', label: 'Count' },
          ]}
        />
      </SectionCard>
    </div>
  )
}
