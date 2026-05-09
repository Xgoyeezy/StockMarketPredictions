import { useCallback, useEffect, useMemo, useState } from 'react'
import Button from '../components/Button'
import ErrorState from '../components/ErrorState'
import ListTable from '../components/ListTable'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import StatusBadge from '../components/StatusBadge'
import { getScoreCalibrationSummary } from '../api/client'

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

function statusTone(status) {
  if (status === 'ready') return 'positive'
  if (status === 'empty') return 'neutral'
  return 'warning'
}

function liftTone(value) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return 'neutral'
  if (numeric > 0.1) return 'positive'
  if (numeric < -0.1) return 'negative'
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
            <tr key={row.feature || row.score_bucket || row.setup_type || row.engine || row.regime || row.recommendation || row.field || index}>
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

function missingRows(missingFields) {
  return Object.entries(missingFields || {})
    .sort((left, right) => Number(right[1]) - Number(left[1]))
    .map(([field, count]) => ({ field, count }))
}

export default function ScoreCalibrationPage() {
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setReport(await getScoreCalibrationSummary())
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load Score Calibration analytics.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const summary = report?.summary || {}
  const aggregations = report?.aggregations || {}
  const bucketSection = aggregations.score_bucket_separation || {}
  const featureSection = aggregations.feature_attribution || {}
  const recommendations = aggregations.recommendations || []
  const setupRows = aggregations.setup_specific_lift || []
  const engineRows = aggregations.engine_specific_lift || []
  const regimeRows = aggregations.regime_specific_lift || []
  const warnings = report?.warnings || []
  const safetyNotes = report?.safety_notes || []
  const missingFieldRows = useMemo(() => missingRows(report?.missing_fields), [report?.missing_fields])

  return (
    <div className="ui-shell__page">
      <PageIntro
        kicker="Research only"
        title="Score Calibration"
        description="Measures whether candidate scores and score components predict reward, forecast quality, and execution-adjusted outcomes. Calibration never changes ranking weights automatically."
        badge="No trading authority"
      />
      {error ? <ErrorState description={error} onAction={load} /> : null}

      <div className="ui-action-row">
        <StatusBadge tone={statusTone(report?.status)}>{humanize(report?.status || 'empty')}</StatusBadge>
        <StatusBadge tone="neutral">Research only</StatusBadge>
        <StatusBadge tone="neutral">Does not change ranking weights</StatusBadge>
        <StatusBadge tone="neutral">Does not place orders</StatusBadge>
        <StatusBadge tone="neutral">Does not change broker routes</StatusBadge>
        <StatusBadge tone="warning">No guaranteed returns</StatusBadge>
        <Button type="button" variant="ghost" size="sm" onClick={load} disabled={loading}>
          Refresh
        </Button>
      </div>

      <SectionCard title="Calibration Summary" subtitle={summary.calibration_warning || 'Waiting for rewardable score records.'}>
        <div className="ui-dashboard-grid">
          <MetricCard label="Rewardable rows" value={summary.rewardable_count ?? 0} helper={`${summary.candidate_count ?? 0} candidate rows`} />
          <MetricCard label="Bucket lift" value={formatNumber(summary.bucket_lift)} helper="80-100 bucket minus 0-40 buckets" />
          <MetricCard label="Monotonicity" value={formatRatio(summary.monotonicity_score)} helper="Share of adjacent buckets improving with score" />
          <MetricCard label="Score scale" value={humanize(summary.score_scale?.scale || 'missing')} helper={summary.score_scale?.description || 'No score scale detected'} />
          <MetricCard label="Score to reward" value={formatNumber(summary.score_to_reward_correlation)} helper="Simple correlation" />
          <MetricCard label="Score to execution adj." value={formatNumber(summary.score_to_execution_adjusted_reward_correlation)} helper="Simple correlation" />
        </div>
      </SectionCard>

      <SectionCard title="Score Bucket Separation" subtitle="Buckets are transparent 0-20, 20-40, 40-60, 60-80, and 80-100 ranges.">
        <DataTable
          rows={bucketSection.items || []}
          empty={loading ? 'Loading bucket analysis...' : 'No score bucket data available.'}
          columns={[
            { key: 'score_bucket', label: 'Bucket', render: (row) => humanize(row.score_bucket) },
            { key: 'candidate_count', label: 'Rows' },
            { key: 'rewardable_count', label: 'Rewardable' },
            { key: 'average_reward', label: 'Avg reward', render: (row) => formatNumber(row.average_reward) },
            { key: 'median_reward', label: 'Median reward', render: (row) => formatNumber(row.median_reward) },
            { key: 'hit_rate', label: 'Hit rate', render: (row) => formatRatio(row.hit_rate) },
            { key: 'baseline_relative_edge', label: 'Baseline edge', render: (row) => formatNumber(row.baseline_relative_edge) },
            { key: 'forecast_accuracy', label: 'Forecast', render: (row) => formatRatio(row.forecast_accuracy) },
            { key: 'execution_adjusted_reward', label: 'Execution adj.', render: (row) => formatNumber(row.execution_adjusted_reward) },
            { key: 'missing_data_rate', label: 'Missing', render: (row) => formatRatio(row.missing_data_rate) },
          ]}
        />
      </SectionCard>

      <SectionCard title="Feature Attribution" subtitle="Attribution uses grouped averages, difference in means, univariate lift, and segment lift only. No black-box model is trained.">
        <div className="ui-dashboard-grid">
          <DataTable
            rows={(featureSection.top_positive_features || []).slice(0, 8)}
            empty={loading ? 'Loading positive feature lift...' : 'No positive feature lift found.'}
            columns={[
              { key: 'feature', label: 'Best feature' },
              { key: 'times_seen', label: 'Seen' },
              { key: 'lift', label: 'Lift', render: (row) => <StatusBadge tone={liftTone(row.lift)}>{formatNumber(row.lift)}</StatusBadge> },
              { key: 'confidence_bucket', label: 'Confidence', render: (row) => humanize(row.confidence_bucket) },
            ]}
          />
          <DataTable
            rows={(featureSection.top_negative_features || []).slice(0, 8)}
            empty={loading ? 'Loading negative feature lift...' : 'No negative feature lift found.'}
            columns={[
              { key: 'feature', label: 'Worst feature' },
              { key: 'times_seen', label: 'Seen' },
              { key: 'lift', label: 'Lift', render: (row) => <StatusBadge tone={liftTone(row.lift)}>{formatNumber(row.lift)}</StatusBadge> },
              { key: 'confidence_bucket', label: 'Confidence', render: (row) => humanize(row.confidence_bucket) },
            ]}
          />
          <DataTable
            rows={(featureSection.false_positive_drivers || []).slice(0, 8)}
            empty={loading ? 'Loading false-positive drivers...' : 'No false-positive drivers found.'}
            columns={[
              { key: 'feature', label: 'False-positive driver' },
              { key: 'false_positive_rate', label: 'Rate', render: (row) => formatRatio(row.false_positive_rate) },
              { key: 'false_positive_count', label: 'Count' },
              { key: 'warnings', label: 'Warnings', render: (row) => (row.warnings || []).join(', ') || 'None' },
            ]}
          />
          <DataTable
            rows={(featureSection.false_negative_drivers || []).slice(0, 8)}
            empty={loading ? 'Loading false-negative drivers...' : 'No false-negative drivers found.'}
            columns={[
              { key: 'feature', label: 'False-negative driver' },
              { key: 'false_negative_rate', label: 'Rate', render: (row) => formatRatio(row.false_negative_rate) },
              { key: 'false_negative_count', label: 'Count' },
              { key: 'warnings', label: 'Warnings', render: (row) => (row.warnings || []).join(', ') || 'None' },
            ]}
          />
        </div>
      </SectionCard>

      <SectionCard title="Segment Lift" subtitle="Setup, engine, and regime lift are research segments only and cannot update ranking automatically.">
        <div className="ui-dashboard-grid">
          <DataTable
            rows={setupRows.slice(0, 8)}
            empty={loading ? 'Loading setup lift...' : 'No setup lift data.'}
            columns={[
              { key: 'setup_type', label: 'Setup', render: (row) => humanize(row.setup_type) },
              { key: 'rewardable_count', label: 'Rewardable' },
              { key: 'average_reward', label: 'Avg reward', render: (row) => formatNumber(row.average_reward) },
              { key: 'lift', label: 'Lift', render: (row) => formatNumber(row.lift) },
            ]}
          />
          <DataTable
            rows={engineRows.slice(0, 8)}
            empty={loading ? 'Loading engine lift...' : 'No engine lift data.'}
            columns={[
              { key: 'engine', label: 'Engine', render: (row) => humanize(row.engine) },
              { key: 'rewardable_count', label: 'Rewardable' },
              { key: 'average_reward', label: 'Avg reward', render: (row) => formatNumber(row.average_reward) },
              { key: 'lift', label: 'Lift', render: (row) => formatNumber(row.lift) },
            ]}
          />
          <DataTable
            rows={regimeRows.slice(0, 8)}
            empty={loading ? 'Loading regime lift...' : 'No regime lift data.'}
            columns={[
              { key: 'regime', label: 'Regime', render: (row) => humanize(row.regime) },
              { key: 'rewardable_count', label: 'Rewardable' },
              { key: 'average_reward', label: 'Avg reward', render: (row) => formatNumber(row.average_reward) },
              { key: 'lift', label: 'Lift', render: (row) => formatNumber(row.lift) },
            ]}
          />
        </div>
      </SectionCard>

      <SectionCard title="Safe Recommendations" subtitle="Recommendations are manual research review only. They do not mutate ranking weights, risk gates, broker routes, or execution settings.">
        <DataTable
          rows={recommendations}
          empty={loading ? 'Loading recommendations...' : 'No score calibration recommendations yet.'}
          columns={[
            { key: 'type', label: 'Type', render: (row) => humanize(row.type) },
            { key: 'feature', label: 'Feature', render: (row) => row.feature || '--' },
            { key: 'recommendation', label: 'Recommendation' },
            { key: 'manual_review_only', label: 'Authority', render: (row) => <StatusBadge tone="neutral">{row.manual_review_only ? 'Manual review only' : 'Review'}</StatusBadge> },
          ]}
        />
      </SectionCard>

      <SectionCard title="Warnings And Missing Data" subtitle="Missing fields keep calibration claims honest. No missing-data fix changes trading automatically.">
        <div className="ui-dashboard-grid">
          <DataTable
            rows={warnings.map((warning, index) => ({ warning, index }))}
            empty="No calibration warnings."
            columns={[{ key: 'warning', label: 'Warning' }]}
          />
          <DataTable
            rows={missingFieldRows}
            empty="No missing fields reported."
            columns={[
              { key: 'field', label: 'Missing field', render: (row) => humanize(row.field) },
              { key: 'count', label: 'Count' },
            ]}
          />
          <DataTable
            rows={safetyNotes.map((note, index) => ({ note, index }))}
            empty="Safety notes unavailable."
            columns={[{ key: 'note', label: 'Safety boundary' }]}
          />
        </div>
      </SectionCard>
    </div>
  )
}
