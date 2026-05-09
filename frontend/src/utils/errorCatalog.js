export const ERROR_CATALOG = {
  validation_error: {
    title: 'Invalid request',
    description: 'The request data was rejected by the service.',
  },
  unauthorized: {
    title: 'Sign-in required',
    description: 'Your session is missing or expired.',
  },
  forbidden: {
    title: 'Not permitted',
    description: 'Your current role does not have access to that action.',
  },
  not_found: {
    title: 'Not found',
    description: 'The requested resource does not exist in this workspace.',
  },
  conflict: {
    title: 'Conflict',
    description: 'The request conflicts with the current state of the resource.',
  },
  rate_limited: {
    title: 'Rate limited',
    description: 'Too many requests were sent too quickly. Wait briefly and try again.',
  },
  service_error: {
    title: 'Service error',
    description: 'The service failed while processing the request.',
  },
  network_unreachable: {
    title: 'Service unreachable',
    description: 'The backend is starting or unavailable. Retry once the health check is green.',
  },
  timeout: {
    title: 'Request timed out',
    description: 'The service took too long to respond. Try again.',
  },
}
export function getErrorCatalogEntry(errorCode) {
  const normalized = String(errorCode || '').trim()
  if (!normalized) return null
  return ERROR_CATALOG[normalized] || null
}
