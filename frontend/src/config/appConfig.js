const env = (typeof import.meta !== 'undefined' && import.meta.env) ? import.meta.env : {}
const customerReadyMode = (env.VITE_CUSTOMER_READY_MODE || 'true').toLowerCase() !== 'false'
const personalMode = (env.VITE_PERSONAL_MODE || (customerReadyMode ? 'false' : 'true')).toLowerCase() !== 'false'
const showAdminSurfaces = (env.VITE_SHOW_ADMIN_SURFACES || 'false').toLowerCase() === 'true'

export const appConfig = {
  customerReadyMode,
  personalMode,
  showAdminSurfaces,
  appName: env.VITE_APP_NAME || (personalMode ? 'Personal Trading Research Desk' : 'Quant Evidence Desk'),
  appTagline: env.VITE_APP_TAGLINE || (personalMode ? 'Self-directed research, risk checks, and execution control for your own account' : 'Paper-validated live automation control for trading teams'),
  publicAppName: env.VITE_PUBLIC_APP_NAME || (personalMode ? 'Personal Trading Research Desk' : 'Quant Evidence OS'),
  publicAppTagline: env.VITE_PUBLIC_APP_TAGLINE || (personalMode ? 'Private own-account trading workstation for self-directed research and execution control.' : 'A premium control plane for risk-gated automation, evidence review, and execution proof.'),
  publicSupportEmail: env.VITE_PUBLIC_SUPPORT_EMAIL || '',
  publicSupportUrl: env.VITE_PUBLIC_SUPPORT_URL || '',
  apiBaseUrl: env.VITE_API_BASE_URL || '',
  enableMockAuth: (env.VITE_ENABLE_MOCK_AUTH || 'false').toLowerCase() === 'true',
  defaultPollingMs: Number(env.VITE_DEFAULT_POLLING_MS || 15000),
}
