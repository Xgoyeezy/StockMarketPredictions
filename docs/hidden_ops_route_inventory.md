# Hidden Ops Route Inventory

Customer navigation should stay focused on the Trader Operator workflow: Desk, Research, Trades, Portfolio, Strategies, Live Console, Live Approvals, Risk, Audit Replay, Execution Quality, Pricing, and simplified Settings.

The routes below can remain direct-linkable for owners, admins, and support, but they should not appear in standard customer navigation:

- organization/workspace management;
- tenant branding and org creation;
- delivery providers and routing;
- API tokens and partner webhooks;
- feature rollout controls;
- billing sync and recovery internals;
- support timelines and security event internals;
- release controls;
- legacy strategy desk pages when not part of the main operator lane.

Customer-safe labels:

- use `Linked Accounts` or `Account Setup` for connected account setup;
- use `Alpaca paper execution` for paper routing;
- use `Paper execution router` for simulator/router views;
- use `Risk gates`, `Audit trail`, `Execution evidence`, and `Live readiness` for decision evidence.

Avoid customer-facing copy that implies in-house custody, clearing, SIPC membership, regulatory brokerage operation, or promised performance.
