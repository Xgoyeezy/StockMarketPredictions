import { useEffect, useState } from 'react'
import { getOrganizationExecutionDiagnostics } from '../../api/client'
import Button from '../Button'
import SectionCard from '../SectionCard'

function formatBool(value) {
  return value ? 'Yes' : 'No'
}
export default function ExecutionProviderDiagnostics() {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [data, setData] = useState(null)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoading(true)
    setError('')
    getOrganizationExecutionDiagnostics()
      .then((payload) => {
        if (!cancelled) setData(payload)
      })
      .catch((err) => {
        if (!cancelled) setError(err?.response?.data?.detail || err?.message || 'Unable to load diagnostics.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [open])

  const configured = data?.configured || {}
  const providers = data?.providers || {}

  return (
    <SectionCard
      title="Execution provider diagnostics"
      subtitle="Configured routing, credentials presence, and provider readiness signals."
      actions={(
        <Button type="button" variant="ghost" onClick={() => setOpen((current) => !current)}>
          {open ? 'Hide diagnostics' : 'Show diagnostics'}
        </Button>
      )}
    >
      {!open ? null : (
        <>
          {error ? <div className="inline-error">{error}</div> : null}
          {loading ? <div className="inline-muted">Loading execution diagnostics...</div> : null}
          {!loading && data ? (
            <div className="table-shell">
              <table className="list-table">
                <caption>Configured routing</caption>
                <tbody>
                  <tr><th scope="row">EXECUTION_ADAPTER</th><td>{configured.execution_adapter || '--'}</td></tr>
                  <tr><th scope="row">BROKER_MODE</th><td>{configured.broker_mode || '--'}</td></tr>
                  <tr><th scope="row">PAPER_BROKER_PROVIDER</th><td>{configured.paper_broker_provider || '--'}</td></tr>
                  <tr><th scope="row">OPTIONS_BROKER_PROVIDER</th><td>{configured.options_broker_provider || '--'}</td></tr>
                </tbody>
              </table>
            </div>
          ) : null}

          {!loading && data ? (
            <div className="table-shell">
              <table className="list-table">
                <caption>Provider readiness</caption>
                <thead>
                  <tr>
                    <th scope="col">Provider</th>
                    <th scope="col">Credentials</th>
                    <th scope="col">Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(providers).map(([key, value]) => (
                    <tr key={key}>
                      <td>{key.replace(/_/g, ' ')}</td>
                      <td>{formatBool(Boolean(value?.credentials_present))}</td>
                      <td>{value?.detail || '--'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </>
      )}
    </SectionCard>
  )
}
