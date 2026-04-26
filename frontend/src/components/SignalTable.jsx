import Button from './Button'
import ListTable from './ListTable'
import StatusBadge from './StatusBadge'
import EmptyState from './EmptyState'
import { buildSignalTelemetry } from '../utils/signalTelemetry'

export default function SignalTable({ rows = [], onSelectTicker, caption = 'Signal results table' }) {
  if (!rows.length) {
    return <EmptyState title="No rows returned" description="Adjust the inputs and try again." />
  }

  return (
    <ListTable>
      <table className="signal-table ui-list-table">
        <caption className="ui-visually-hidden">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Ticker</th>
            <th scope="col">Verdict</th>
            <th scope="col">Score</th>
            <th scope="col">Conviction</th>
            <th scope="col">Ranking</th>
            <th scope="col">Automation</th>
            <th scope="col">Decision</th>
            <th scope="col">Target</th>
            <th scope="col">Stop</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const telemetry = buildSignalTelemetry(row)
            return (
              <tr key={`${row.ticker}-${row.contract_symbol || row.verdict}`}>
                <td>
                  {onSelectTicker ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="table-link"
                      onClick={() => onSelectTicker(row.ticker)}
                    >
                      {row.ticker}
                    </Button>
                  ) : (
                    row.ticker
                  )}
                </td>
                <td><StatusBadge value={row.verdict} /></td>
                <td>{row.setup_score ?? '--'}</td>
                <td>{row.conviction_label ?? '--'}</td>
                <td>{telemetry.rankingSummary.length ? telemetry.rankingSummary.join(' | ') : '--'}</td>
                <td>{telemetry.automationSummary.length ? telemetry.automationSummary.join(' | ') : '--'}</td>
                <td>
                  <div className="ui-list-cell__stack">
                    <StatusBadge value={row.trade_decision} />
                    <span className="ui-list-cell__meta">{telemetry.eligibilityLabel}</span>
                    {telemetry.rejectionSummary ? <span className="ui-list-cell__meta">{telemetry.rejectionSummary}</span> : null}
                  </div>
                </td>
                <td>{row.target_price ?? row.expected_underlying_target ?? '--'}</td>
                <td>{row.stop_loss ?? '--'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </ListTable>
  )
}
