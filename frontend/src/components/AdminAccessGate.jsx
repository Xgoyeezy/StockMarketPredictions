import { NavLink } from 'react-router-dom'
import { appConfig } from '../config/appConfig'
import { useAuth } from '../context/useAuth'
import { hasAdminSurfaceAccess } from '../utils/navigationModel'

export default function AdminAccessGate({ children }) {
  const { session } = useAuth()
  const permissionMap = session?.active_tenant?.permission_map || {}
  const allowed = appConfig.showAdminSurfaces && hasAdminSurfaceAccess(permissionMap)

  if (allowed) return children

  return (
    <section className="ui-panel ui-panel--section page-section">
      <div className="page-section__header">
        <div>
          <div className="ui-kicker">Managed in the background</div>
          <h1>This area is reserved for admin and support workflows.</h1>
          <p>
            Standard trader workspaces keep internal setup, rollout, delivery, security audit,
            and support controls out of the main product flow. Use live control, account setup,
            risk, audit replay, and execution evidence for customer-facing decisions.
          </p>
        </div>
      </div>
      <div className="ui-actions ui-actions--wrap">
        <NavLink className="ui-button ui-button--solid ui-button--md" to="/live">
          Open Live Console
        </NavLink>
        <NavLink className="ui-button ui-button--ghost ui-button--md" to="/settings">
          Open Settings
        </NavLink>
      </div>
    </section>
  )
}
