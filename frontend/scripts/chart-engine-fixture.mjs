import { buildChartDemoPayload } from '../src/chart-engine/demoPayload.js'

export function buildChartPayload({ count = 240, intervalMinutes = 5, ticker = 'SPY', interval = '5m', period = '5d' } = {}) {
  return buildChartDemoPayload({
    ticker,
    interval,
    period,
    count,
    intervalMinutes,
  })
}
