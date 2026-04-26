import { appConfig } from '../config/appConfig.js'

const PERSONAL_PUBLIC_PAGE_DEFINITIONS = {
  connect: {
    key: 'connect',
    path: '/connect',
    navLabel: 'Connect',
    title: 'Personal Connection Notes',
    eyebrow: 'Own-account desk',
    headline: 'Personal Trading Research Desk',
    subhead: 'Private trading workstation for self-directed research, paper rehearsal, risk checks, and tightly controlled own-account execution.',
    body: [
      'This application is configured for one operator using their own account context, not for sale as an advisory service.',
      'The workflow keeps market research, staged trade plans, paper rehearsal, and live-capital controls visible before any order route is promoted.',
      'Brokerage integrations, when enabled, are account-routing tools. They do not turn the workstation into a registered investment adviser or client money-management platform.',
      'Live-capital use should stay paper-first, risk-limited, and manually reviewed until the broker adapter, risk locks, and readiness gates are verified.',
    ],
  },
  personalUse: {
    key: 'personalUse',
    path: '/personal-use',
    navLabel: 'Personal Use',
    title: 'Personal Use',
    eyebrow: 'Self-directed use',
    headline: 'Own-account use only',
    subhead: 'The desk is framed around your decisions, your risk budget, and your brokerage credentials.',
    sections: [
      {
        title: 'Operating boundary',
        items: [
          'Use the app as research and decision support for your own account.',
          'Do not market it as investment advice, client advisory software, or a managed-account service.',
          'Do not place trades for another person through this setup without legal and compliance review.',
        ],
      },
      {
        title: 'Research workflow',
        items: [
          'Treat entries, targets, stops, invalidation levels, and model rankings as prompts for review.',
          'Keep a journal note for every real-money decision so the trade has a reason, risk limit, and exit plan.',
          'Prefer paper execution until fills, slippage, and route behavior match expectations.',
        ],
      },
      {
        title: 'Live funds',
        items: [
          'Keep live execution behind explicit broker-live readiness and frontend confirmation.',
          'Use small notional caps, one-position limits, long-only rules, and regular-hours-only defaults for early real-money use.',
          'Do not enable options, margin, shorting, or unattended live routing before backend risk locks are enforced.',
        ],
      },
    ],
  },
  terms: {
    key: 'terms',
    path: '/terms',
    navLabel: 'Terms',
    title: 'Personal Use Terms',
    eyebrow: 'Local terms',
    headline: 'Personal Use Terms',
    subhead: 'Short-form operating terms for a private own-account workstation.',
    sections: [
      {
        title: 'Personal use only',
        items: [
          'This workstation is configured for private use by the operator of this repo.',
          'It is not packaged, offered, or represented as a product for customers or clients.',
        ],
      },
      {
        title: 'Self-directed decisions',
        items: [
          'The application can surface research, rankings, trade plans, and risk checks, but final trading decisions remain your responsibility.',
          'No output guarantees performance, suitability, availability, or loss avoidance.',
        ],
      },
      {
        title: 'Real-money risk',
        items: [
          'Securities trading can lose some or all invested capital.',
          'Live routing should remain disabled unless broker credentials, risk limits, and readiness gates are intentionally configured.',
        ],
      },
      {
        title: 'No client advisory service',
        items: [
          'Do not use this configuration to advise others for compensation or manage other people\'s accounts.',
          'Before giving investment advice to others, get legal and compliance guidance on registration, licensing, custody, and conflicts.',
        ],
      },
    ],
  },
  privacy: {
    key: 'privacy',
    path: '/privacy',
    navLabel: 'Privacy',
    title: 'Local Privacy Notes',
    eyebrow: 'Local data',
    headline: 'Local Privacy Notes',
    subhead: 'How the personal workstation treats local trading and account-routing data.',
    sections: [
      {
        title: 'What the app stores',
        items: [
          'Local preferences, watchlists, notes, alerts, trade tickets, automation records, and runtime diagnostics.',
          'Brokerage metadata only when you configure a supported connection.',
        ],
      },
      {
        title: 'How it is used',
        items: [
          'Load the trading workspace, size tickets, preserve review context, and enforce the selected risk posture.',
          'Separate paper rehearsal, env-backed personal routes, and OAuth-linked brokerage routes.',
        ],
      },
      {
        title: 'Credential handling',
        items: [
          'Keep API keys and broker secrets out of the frontend.',
          'Use environment files and backend adapters for broker credentials.',
        ],
      },
    ],
  },
}

const COMMERCIAL_PUBLIC_PAGE_DEFINITIONS = {
  connect: {
    key: 'connect',
    path: '/connect',
    navLabel: 'Connect',
    title: 'Application Website',
    eyebrow: 'Private pilot',
    headline: 'Stock Options Signal',
    subhead: 'Private pilot trading application built on Alpaca OAuth.',
    body: [
      'This application connects Alpaca accounts through OAuth for a controlled trading workflow.',
      'It keeps personal trading and brokerage-linked trading in separate account contexts to reduce accidental cross-routing.',
      'Current pilot behavior is paper-first, with approval and account-binding controls visible inside the app.',
      'Linked accounts are routed through Alpaca OAuth tokens rather than personal API keys.',
    ],
  },
  tradingService: {
    key: 'tradingService',
    path: '/trading-service',
    navLabel: 'Trading Service',
    title: 'Trading Service',
    eyebrow: 'Trading workflow',
    headline: 'Trading Service',
    subhead: 'A trading workstation built around live analysis, staged trades, decision review, scenario replay, and paper-to-live controls.',
    body: [
      'The product starts from live market analysis and keeps the staged trade visible through entry, target, stop, invalidation, risk size, and execution lane.',
      'Recommendations are reviewed as cases, with rationale quality, challenge notes, accepted-risk ownership, evidence gaps, and release basis kept together.',
      'Saved scenarios help compare current setups against prior wins, losses, approved trades, rejected trades, and different market regimes.',
      'The service story is disciplined trading operations, not just prediction output.',
    ],
    sections: [
      {
        title: 'Trade workstation',
        items: [
          'Live chart context, staged recommendations, and account routing stay in the same operator flow.',
          'Decision quality checks keep weak rationale, unresolved conditions, and missing evidence visible before final action.',
          'Post-trade review turns outcomes into saved scenarios for later comparison.',
        ],
      },
      {
        title: 'Service story',
        items: [
          'Prospects see a clear process: live analysis, human review, risk controls, scenario comparison, and recorded outcomes.',
          'The product can support self-directed use, paper validation, broker-assisted review, or a controlled client-facing service path.',
        ],
      },
    ],
  },
  riskControls: {
    key: 'riskControls',
    path: '/risk-controls',
    navLabel: 'Risk Controls',
    title: 'Risk Controls',
    eyebrow: 'Risk-first operation',
    headline: 'Risk Controls',
    subhead: 'Controls for trade staging, account routing, automation, live-mode activation, and sensitive settings before risk moves.',
    body: [
      'Risk controls cover both trade decisions and account changes, so high-impact changes can be reviewed before they become active.',
      'The workflow can flag stale recommendations, unresolved conditions, route blockers, evidence gaps, failed syncs, and strategy release blockers.',
      'Sensitive changes such as linked-account edits, automation enablement, live-mode activation, risk-limit edits, payment changes, and API credential changes create review cases.',
    ],
    sections: [
      {
        title: 'Before trade action',
        items: [
          'Review rationale, accepted-risk ownership, evidence readiness, strategy release basis, and route eligibility.',
          'Keep conditional approvals separate from final approval until conditions are cleared.',
        ],
      },
      {
        title: 'Before control action',
        items: [
          'Route account, payment, automation, credential, and live-mode changes through a control-change inbox.',
          'Preserve risk score, verification checklist, request rationale, reviewer decision, and audit fingerprint.',
        ],
      },
    ],
  },
  auditReadyRecords: {
    key: 'auditReadyRecords',
    path: '/audit-ready-records',
    navLabel: 'Audit Records',
    title: 'Audit-Ready Records',
    eyebrow: 'Record quality',
    headline: 'Audit-Ready Records',
    subhead: 'Exportable recommendation packets with evidence, release basis, decision review, risk settings, broker response, and audit timeline.',
    body: [
      'Every recommendation can carry a frozen evidence register, model and release basis, decision rationale, risk state, linked account context, and event timeline.',
      'Packet fingerprints help show that exported records are stable snapshots of what the system knew at the time.',
      'The records are meant for operational review, client conversations, support diagnostics, and dispute research.',
    ],
    sections: [
      {
        title: 'Packet contents',
        items: [
          'Ticker, setup, market snapshot, chart levels, option quote context, liquidity checks, risk settings, strategy release basis, decision review, and audit timeline.',
          'Approval, rejection, conditional approval, submission, and failure events remain attached to the case.',
        ],
      },
      {
        title: 'Operational value',
        items: [
          'Records make recommendations easier to explain and easier to improve after outcomes are known.',
          'Exported packets support trust without replacing registration, legal advice, or firm compliance obligations.',
        ],
      },
    ],
  },
  reviewProcess: {
    key: 'reviewProcess',
    path: '/review-process',
    navLabel: 'Review Process',
    title: 'How Recommendations Are Reviewed',
    eyebrow: 'Decision process',
    headline: 'How Recommendations Are Reviewed',
    subhead: 'A recommendation moves from live signal to reviewed decision through evidence, challenge, scenario, and control checks.',
    body: [
      'The review process checks whether the recommendation follows the standard path, whether any deviation is justified, and whether the thesis is strong enough to act on.',
      'Evidence grounding attaches market snapshot, chart levels, model basis, option quote context, liquidity checks, event context, and strategy release basis.',
      'Scenario replay compares the setup against prior outcomes before the same idea is repeated.',
      'Sensitive account and automation changes are reviewed separately from trade approval so service operations stay controlled.',
    ],
    sections: [
      {
        title: 'Decision checks',
        items: [
          'Rationale quality, accepted-risk owner, challenge notes, unresolved conditions, evidence gaps, and release basis.',
          'Approve, reject, expire, conditionally approve, or save for scenario comparison.',
        ],
      },
      {
        title: 'Learning loop',
        items: [
          'Outcomes are saved as trade scenarios so wins, losses, approved trades, rejected trades, and regime shifts can be compared later.',
          'Repeated setup failures and weak rationales become product feedback for the next strategy release.',
        ],
      },
    ],
  },
  terms: {
    key: 'terms',
    path: '/terms',
    navLabel: 'Terms',
    title: 'Terms of Use',
    eyebrow: 'Pilot terms',
    headline: 'Terms of Use',
    subhead: 'Short-form terms for a private pilot trading workflow.',
    sections: [
      {
        title: 'Pilot use only',
        items: [
          'This application is provided as a limited private pilot and may change without notice.',
          'Access may be restricted, suspended, or revoked during the pilot at any time.',
        ],
      },
      {
        title: 'Operational support only',
        items: [
          'The application is provided for informational and operational trading support.',
          'Nothing on these pages should be interpreted as a guarantee of trading results or uninterrupted routing.',
        ],
      },
    ],
  },
  privacy: {
    key: 'privacy',
    path: '/privacy',
    navLabel: 'Privacy',
    title: 'Privacy Policy',
    eyebrow: 'Pilot privacy',
    headline: 'Privacy Policy',
    subhead: 'How the pilot handles account-link, routing, and operational data.',
    sections: [
      {
        title: 'What we collect',
        items: [
          'Alpaca-linked account metadata.',
          'OAuth authorization results and token-backed connection status.',
          'Trade intents, approvals, submissions, and audit history.',
        ],
      },
    ],
  },
}

function getPublicPageDefinitions() {
  if (!appConfig.personalMode) {
    return COMMERCIAL_PUBLIC_PAGE_DEFINITIONS
  }
  return {
    ...PERSONAL_PUBLIC_PAGE_DEFINITIONS,
    tradingService: COMMERCIAL_PUBLIC_PAGE_DEFINITIONS.tradingService,
    riskControls: COMMERCIAL_PUBLIC_PAGE_DEFINITIONS.riskControls,
    auditReadyRecords: COMMERCIAL_PUBLIC_PAGE_DEFINITIONS.auditReadyRecords,
    reviewProcess: COMMERCIAL_PUBLIC_PAGE_DEFINITIONS.reviewProcess,
  }
}

function normalizePathname(pathname) {
  const cleaned = String(pathname || '').trim().toLowerCase()
  if (!cleaned) return '/'
  if (cleaned.length > 1 && cleaned.endsWith('/')) {
    return cleaned.slice(0, -1)
  }
  return cleaned
}

export function getPublicSiteBranding() {
  const name = String(appConfig.publicAppName || '').trim() || (appConfig.personalMode ? 'Personal Trading Research Desk' : 'Stock Options Signal')
  const tagline =
    String(appConfig.publicAppTagline || '').trim() ||
    (appConfig.personalMode
      ? 'Private own-account trading workstation for self-directed research and execution control.'
      : 'Private pilot trading application built on Alpaca OAuth.')
  return { name, tagline }
}

export function getPublicSiteContact() {
  const supportEmail = String(appConfig.publicSupportEmail || '').trim()
  const supportUrl = String(appConfig.publicSupportUrl || '').trim()

  if (supportEmail) {
    return {
      type: 'email',
      label: supportEmail,
      href: `mailto:${supportEmail}`,
      description: appConfig.personalMode ? 'Local operator contact' : 'Pilot support contact',
    }
  }

  if (supportUrl) {
    return {
      type: 'url',
      label: supportUrl,
      href: supportUrl,
      description: appConfig.personalMode ? 'Local operator link' : 'Pilot support link',
    }
  }

  return {
    type: 'placeholder',
    label: appConfig.personalMode
      ? 'No public support contact is configured for this personal workstation.'
      : 'Support contact available on request during the private pilot.',
    href: '',
    description: appConfig.personalMode ? 'Local operator' : 'Pilot support',
  }
}

export function getPublicSitePages() {
  return Object.values(getPublicPageDefinitions())
}

export function getPublicSitePage(pathname) {
  const normalizedPath = normalizePathname(pathname)
  const definitions = getPublicPageDefinitions()
  if (appConfig.personalMode && normalizedPath === '/broker-services') {
    return definitions.personalUse
  }
  return Object.values(definitions).find((page) => page.path === normalizedPath) || null
}
