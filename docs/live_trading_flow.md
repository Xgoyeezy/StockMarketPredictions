# Live Trading Flow

Real money trading is explicit, reversible, logged, limited, and risk-gated.

```mermaid
flowchart TD
    A["Signal Generated"] --> B["TradeDecision Stored"]
    B --> C["Attach Readiness Snapshot"]
    C --> D["Create LiveOrderIntent"]
    D --> E["Run LiveRiskCheck"]
    E -->|"Blocked"| F["Record RiskEvent and AuditEvent"]
    E -->|"Approval Required"| G["Place in Approval Queue"]
    G -->|"Approved"| H["Broker Submission Gate"]
    G -->|"Rejected"| I["Mark LiveOrderIntent Rejected"]
    E -->|"Policy Auto-Approved"| H
    H --> J["Store BrokerExecutionReceipt"]
    J --> K["Store ExecutionQualitySnapshot"]
    K --> L["Update Replay Timeline"]
    L --> M["Update Exposure and Session Metrics"]
```

Current implementation notes:
- Live order intents are approval-required by default.
- Policy auto-approval is only considered when the active risk policy sets `allow_policy_auto_approval=true` and Professional+ live entitlements are present.
- Broker submission is still blocked unless `FEATURE_LIVE_TRADING=true`, a provider live flag such as `ALPACA_LIVE_TRADING_ENABLED=true`, signed authorization, readiness, risk, session, kill-switch, fresh-data, and audit gates all pass.
- The approval service records a broker receipt even when submission is not performed, so the platform can prove why no broker call occurred.

Live states:
- `draft`
- `paper`
- `validated`
- `live_candidate`
- `armed`
- `live`
- `paused`
- `blocked`
- `killed`
- `retired`

`armed` means the strategy is authorized and ready for explicit start. It does not submit orders.
