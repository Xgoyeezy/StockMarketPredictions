import { useCallback, useEffect, useMemo, useState } from 'react'
import ErrorState from '../components/ErrorState'
import LoadingBlock from '../components/LoadingBlock'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import {
  getForecastValidationModels,
  getForecastValidationPredictions,
  getForecastValidationRegimes,
  getForecastValidationSummary,
} from '../api/client'

function formatNumber(value, digits = 2) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return 'n/a'
  return numeric.toFixed(digits)
}

function formatPercent(value, digits = 1) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return 'n/a'
  return `${(numeric * 100).toFixed(digits)}%`
}

function formatPrice(value) {
  return `$${formatNumber(value, 2)}`
}

function resolvePredictionRows(prediction) {
  return prediction?.evaluation?.alignment || []
}

function buildPolyline(rows, key, width, height, padding) {
  const usableWidth = width - padding * 2
  const usableHeight = height - padding * 2
  const validRows = rows.filter((row) => Number.isFinite(Number(row?.[key])))
  const offsets = rows.map((row) => Number(row.timestamp_offset)).filter(Number.isFinite)
  const prices = rows
    .flatMap((row) => [Number(row.predicted_price), Number(row.actual_price)])
    .filter(Number.isFinite)
  if (validRows.length < 2 || offsets.length < 2 || prices.length < 2) return ''
  const minOffset = Math.min(...offsets)
  const maxOffset = Math.max(...offsets)
  const minPrice = Math.min(...prices)
  const maxPrice = Math.max(...prices)
  const offsetSpan = Math.max(maxOffset - minOffset, 1)
  const priceSpan = Math.max(maxPrice - minPrice, 0.01)
  return validRows
    .map((row) => {
      const x = padding + ((Number(row.timestamp_offset) - minOffset) / offsetSpan) * usableWidth
      const y = height - padding - ((Number(row[key]) - minPrice) / priceSpan) * usableHeight
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
}

function ForecastPathChart({ prediction }) {
  const rows = resolvePredictionRows(prediction)
  const predictedPoints = buildPolyline(rows, 'predicted_price', 640, 260, 26)
  const actualPoints = buildPolyline(rows, 'actual_price', 640, 260, 26)
  const lastRow = rows[rows.length - 1] || {}

  if (!prediction || rows.length === 0) {
    return <div className="forecast-validation-chart forecast-validation-chart--empty">No evaluated path available.</div>
  }

  return (
    <div className="forecast-validation-chart">
      <div className="forecast-validation-chart__header">
        <div>
          <span>{prediction.symbol}</span>
          <strong>{prediction.prediction_id}</strong>
        </div>
        <div className="forecast-validation-chart__legend" aria-label="Chart legend">
          <span><i className="forecast-validation-chart__swatch forecast-validation-chart__swatch--forecast" /> Forecast</span>
          <span><i className="forecast-validation-chart__swatch forecast-validation-chart__swatch--actual" /> Actual</span>
        </div>
      </div>
      <svg className="forecast-validation-chart__svg" viewBox="0 0 640 260" role="img" aria-label="Predicted path compared with actual prices">
        <line x1="26" y1="224" x2="614" y2="224" className="forecast-validation-chart__axis" />
        <line x1="26" y1="26" x2="26" y2="224" className="forecast-validation-chart__axis" />
        {predictedPoints ? <polyline points={predictedPoints} className="forecast-validation-chart__line forecast-validation-chart__line--forecast" /> : null}
        {actualPoints ? <polyline points={actualPoints} className="forecast-validation-chart__line forecast-validation-chart__line--actual" /> : null}
      </svg>
      <div className="forecast-validation-chart__footer">
        <span>Horizon {prediction.horizon_minutes}m</span>
        <span>Forecast end {formatPrice(lastRow.predicted_price)}</span>
        <span>Actual end {lastRow.actual_price == null ? 'missing' : formatPrice(lastRow.actual_price)}</span>
      </div>
    </div>
  )
}

function PredictionSummary({ title, prediction }) {
  const evaluation = prediction?.evaluation || {}
  if (!prediction) {
    return (
      <div className="forecast-validation-card">
        <span>{title}</span>
        <strong>No evaluated prediction</strong>
      </div>
    )
  }

  return (
    <div className="forecast-validation-card">
      <div className="forecast-validation-card__header">
        <span>{title}</span>
        <strong>{prediction.prediction_id}</strong>
      </div>
      <dl className="forecast-validation-metric-list">
        <div><dt>Reward</dt><dd>{formatNumber(evaluation.reward, 3)}</dd></div>
        <div><dt>Direction</dt><dd>{evaluation.direction_correct ? 'Correct' : 'Wrong'}</dd></div>
        <div><dt>Target hit</dt><dd>{evaluation.target_hit ? 'Yes' : 'No'}</dd></div>
        <div><dt>Invalidation</dt><dd>{evaluation.invalidation_hit ? 'Hit' : 'Clear'}</dd></div>
        <div><dt>MAE</dt><dd>{formatNumber(evaluation.mae, 3)}</dd></div>
        <div><dt>RMSE</dt><dd>{formatNumber(evaluation.rmse, 3)}</dd></div>
        <div><dt>Timing error</dt><dd>{evaluation.timing_error == null ? 'n/a' : `${evaluation.timing_error}m`}</dd></div>
        <div><dt>Confidence error</dt><dd>{formatNumber(evaluation.confidence_calibration, 3)}</dd></div>
        <div><dt>Max adverse</dt><dd>{formatPercent(evaluation.max_adverse_excursion, 2)}</dd></div>
      </dl>
    </div>
  )
}

function ConfidenceChart({ models }) {
  const buckets = (models?.by_engine || [])
    .flatMap((model) => model.calibration_vs_confidence || [])
    .filter((row) => row.bucket !== 'unknown')
  if (!buckets.length) {
    return <div className="forecast-validation-empty">No confidence buckets available.</div>
  }
  return (
    <div className="forecast-validation-confidence">
      {buckets.map((bucket) => {
        const accuracy = Number(bucket.direction_accuracy)
        const height = Number.isFinite(accuracy) ? Math.max(6, accuracy * 100) : 6
        return (
          <div className="forecast-validation-confidence__bucket" key={`${bucket.bucket}-${bucket.count}`}>
            <div className="forecast-validation-confidence__bar" style={{ height: `${height}%` }} />
            <strong>{formatPercent(bucket.direction_accuracy, 0)}</strong>
            <span>{bucket.bucket}</span>
            <em>{bucket.count} paths</em>
          </div>
        )
      })}
    </div>
  )
}

export default function ForecastValidationPage() {
  const [summary, setSummary] = useState(null)
  const [predictions, setPredictions] = useState([])
  const [models, setModels] = useState(null)
  const [regimes, setRegimes] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [summaryData, predictionData, modelData, regimeData] = await Promise.all([
        getForecastValidationSummary(),
        getForecastValidationPredictions(),
        getForecastValidationModels(),
        getForecastValidationRegimes(),
      ])
      setSummary(summaryData || {})
      setPredictions(predictionData?.items || [])
      setModels(modelData || {})
      setRegimes(regimeData?.items || [])
    } catch (err) {
      setError(err?.display_detail || err?.message || 'Failed to load forecast validation.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const bestPrediction = useMemo(() => {
    const bestId = summary?.best_prediction?.prediction_id
    return predictions.find((item) => item.prediction_id === bestId) || predictions.find((item) => item.evaluation?.reward != null) || null
  }, [predictions, summary])

  const missingRows = useMemo(() => (
    Object.entries(summary?.missing_field_counts || summary?.missing_fields || {})
      .sort((left, right) => Number(right[1]) - Number(left[1]))
      .map(([field, count]) => ({ field, count }))
  ), [summary])

  const safetyNotes = summary?.safety_notes || []
  const warnings = summary?.warnings || []

  const worstPrediction = useMemo(() => {
    const worstId = summary?.worst_prediction?.prediction_id
    return predictions.find((item) => item.prediction_id === worstId) || null
  }, [predictions, summary])

  if (loading) {
    return (
      <div className="ui-shell__page">
        <LoadingBlock label="Loading forecast validation" detail="Reading immutable forecast evaluations and forward-only actual price comparisons." />
      </div>
    )
  }

  return (
    <div className="ui-shell__page forecast-validation-page">
      <PageIntro
        kicker="Research validation"
        title="Forecast Validation"
        description="Prediction lines are evaluated after creation against forward-only market data."
        helper="Research only. Forecasts do not trigger trades, change ranking weights, change broker routes, or bypass risk gates."
        badge="read-only"
        actions={<button type="button" className="desk-action" onClick={load}>Refresh</button>}
      />

      {error ? <ErrorState description={error} onAction={load} /> : null}

      <div className="forecast-validation-warning">
        Research only. Predictions are evaluated after the fact and cannot trigger trades. No guaranteed returns.
      </div>

      <div className="metrics-grid">
        <MetricCard label="Validated forecasts" value={summary?.validated_forecasts ?? summary?.evaluated_count ?? 0} helper={`${summary?.non_rewardable_forecasts || summary?.missing_data_count || 0} non-rewardable`} />
        <MetricCard label="Direction accuracy" value={formatPercent(summary?.direction_accuracy)} helper="Endpoint direction only" />
        <MetricCard label="Avg forecast reward" value={formatNumber(summary?.avg_forecast_reward ?? summary?.avg_reward, 3)} helper="Transparent component score" />
        <MetricCard label="Avg path RMSE" value={formatNumber(summary?.avg_path_rmse ?? summary?.avg_rmse, 3)} helper="Forecast path error" />
      </div>

      <SectionCard title="Safety And Missing Data" subtitle="Forecast validation is analytics only and incomplete forecasts are visible but not rewarded.">
        <div className="forecast-validation-grid">
          <table className="signal-table">
            <thead>
              <tr><th>Safety boundary</th></tr>
            </thead>
            <tbody>
              {(safetyNotes.length ? safetyNotes : ['Research only. Does not affect trading.']).map((note) => (
                <tr key={note}><td>{note}</td></tr>
              ))}
            </tbody>
          </table>
          <table className="signal-table">
            <thead>
              <tr><th>Missing field</th><th>Count</th></tr>
            </thead>
            <tbody>
              {missingRows.length ? missingRows.map((row) => (
                <tr key={row.field}><td>{row.field}</td><td>{row.count}</td></tr>
              )) : (
                <tr><td colSpan="2">No missing forecast validation fields.</td></tr>
              )}
              {warnings.map((warning) => (
                <tr key={warning}><td colSpan="2">{warning}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </SectionCard>

      <div className="forecast-validation-grid">
        <PredictionSummary title="Best prediction line" prediction={bestPrediction} />
        <PredictionSummary title="Worst prediction line" prediction={worstPrediction} />
      </div>

      <SectionCard title="Path comparison" subtitle="Static comparison of the stored prediction line against aligned forward actual prices.">
        <div className="forecast-validation-grid forecast-validation-grid--charts">
          <ForecastPathChart prediction={bestPrediction} />
          <ForecastPathChart prediction={worstPrediction} />
        </div>
      </SectionCard>

      <div className="forecast-validation-grid">
        <SectionCard title="Model and source quality" subtitle="Grouped by forecast engine, with confidence buckets.">
          <table className="signal-table">
            <thead>
              <tr>
                <th>Engine</th>
                <th>Count</th>
                <th>Reward</th>
                <th>Direction</th>
                <th>Timing</th>
                <th>MAE</th>
                <th>RMSE</th>
              </tr>
            </thead>
            <tbody>
              {(models?.by_engine || []).map((row) => (
                <tr key={row.engine}>
                  <td>{row.engine}</td>
                  <td>{row.evaluated_count}/{row.count}</td>
                  <td>{formatNumber(row.avg_reward, 3)}</td>
                  <td>{formatPercent(row.direction_accuracy)}</td>
                  <td>{row.avg_timing_error == null ? 'n/a' : `${formatNumber(row.avg_timing_error, 1)}m`}</td>
                  <td>{formatNumber(row.avg_mae, 3)}</td>
                  <td>{formatNumber(row.avg_rmse, 3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </SectionCard>

        <SectionCard title="Confidence vs accuracy" subtitle="Calibration check by reported forecast confidence bucket.">
          <ConfidenceChart models={models} />
        </SectionCard>
      </div>

      <SectionCard title="Regime attribution" subtitle="Forward-only forecast quality grouped by deterministic regime labels.">
        <table className="signal-table">
          <thead>
            <tr>
              <th>Regime</th>
              <th>Evaluated</th>
              <th>Missing</th>
              <th>Reward</th>
              <th>Direction</th>
              <th>MAE</th>
              <th>RMSE</th>
            </tr>
          </thead>
          <tbody>
            {regimes.map((row) => (
              <tr key={row.regime}>
                <td>{row.regime}</td>
                <td>{row.evaluated_count}</td>
                <td>{row.missing_data_count}</td>
                <td>{formatNumber(row.avg_reward, 3)}</td>
                <td>{formatPercent(row.direction_accuracy)}</td>
                <td>{formatNumber(row.avg_mae, 3)}</td>
                <td>{formatNumber(row.avg_rmse, 3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </SectionCard>
    </div>
  )
}
