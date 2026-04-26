const env = (typeof import.meta !== 'undefined' && import.meta.env) ? import.meta.env : {}
const personalMode = (env.VITE_PERSONAL_MODE || 'true').toLowerCase() !== 'false'

export const appConfig = {
  personalMode,
  appName: env.VITE_APP_NAME || (personalMode ? 'Personal Trading Research Desk' : 'Trading Platform Operations'),
  appTagline: env.VITE_APP_TAGLINE || (personalMode ? 'Self-directed research, risk checks, and execution control for your own account' : 'Organization operations and execution control plane'),
  publicAppName: env.VITE_PUBLIC_APP_NAME || (personalMode ? 'Personal Trading Research Desk' : 'Stock Options Signal'),
  publicAppTagline: env.VITE_PUBLIC_APP_TAGLINE || (personalMode ? 'Private own-account trading workstation for self-directed research and execution control.' : 'Private pilot trading application built on Alpaca OAuth.'),
  publicSupportEmail: env.VITE_PUBLIC_SUPPORT_EMAIL || '',
  publicSupportUrl: env.VITE_PUBLIC_SUPPORT_URL || '',
  apiBaseUrl: env.VITE_API_BASE_URL || '',
  enableMockAuth: (env.VITE_ENABLE_MOCK_AUTH || 'false').toLowerCase() === 'true',
  defaultPollingMs: Number(env.VITE_DEFAULT_POLLING_MS || 15000),
}
