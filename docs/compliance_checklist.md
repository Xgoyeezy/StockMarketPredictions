# Compliance Checklist

This is an engineering and operating checklist, not legal advice.

## Compliance Readiness Checklist

Compliance readiness means the repository has sanitized evidence, documented boundaries, and review prompts for qualified external reviewers. It does not mean the system is compliance-approved, institutional-grade, an investment adviser, a broker-dealer, or a live autonomous money manager.

- Keep ratings framed as current estimated readiness, not proof of alpha.
- Keep benchmark proof required before edge claims.
- Keep walk-forward proof required before repeatability claims.
- Keep paper-first safety as the active unattended execution boundary.
- Keep reward, forecast, benchmark, and shadow-mode analytics research-only.
- Keep AI without order authority.
- Keep risk gates authoritative.
- Keep broker routes unchanged unless a separate explicit broker-routing project approves a change.
- Keep support and firm-grade reports sanitized of secrets, account identifiers, raw broker records, raw logs, and raw local paths.

## Support Export Sanitization Evidence Checklist

Support and firm-review exports are proof artifacts only. They do not prove alpha, approve live trading, change broker routes, change order behavior, bypass risk gates, clear kill switches, grant AI order authority, or mutate ranking weights.

Every support export review should record:

- Export type and schema version.
- Generated timestamp.
- Source report name.
- Sanitized flag.
- Redaction policy version.
- Whether secret-like keys were redacted.
- Whether account identifiers were redacted.
- Whether raw broker payloads were excluded.
- Whether raw logs were excluded.
- Whether raw local paths were excluded.
- Whether credentials and environment values were excluded.
- Whether exported fields are metadata, summaries, or sanitized evidence only.
- Reviewer or automation check that confirmed the boundary.

Stop the export or mark it unsafe if any artifact includes:

- `.env` values.
- API keys, tokens, passwords, or authorization headers.
- Broker account identifiers.
- Raw broker records or raw broker payloads.
- Raw runtime logs.
- Raw local paths.
- Database files or local storage files.
- Unsanitized personal data.

Allowed export contents:

- Sanitized summary metrics.
- Read-only proof status.
- Blocked claims.
- Safe next actions.
- Source document names.
- Test/build/probe status summaries.
- Redaction status and warnings.

## Firm-Grade Report Specification

Firm-grade reports are sanitized review artifacts. They are not proof of alpha, compliance approval, institutional-grade readiness, broker-dealer status, investment-adviser status, HFT capability, or live-trading readiness.

Every firm-grade report specification should include:

- Report identifier.
- Schema version.
- Generated timestamp.
- Source evidence snapshot identifiers.
- Data lineage summary.
- Model lineage summary.
- Feature lineage summary.
- Risk-control status.
- Approval and promotion metadata.
- Forecast and reward summary.
- Incident and release summary.
- Audit evidence summary.
- Verification summary.
- Sanitization summary.
- Claim-boundary section.
- External-review status.

Firm-grade reports must exclude:

- Secrets, API keys, tokens, passwords, and authorization headers.
- Broker account identifiers.
- Raw broker records or raw broker payloads.
- Raw runtime logs.
- Raw local paths.
- Database files or local storage files.
- Credentials and environment values.
- Unsanitized personal data.

If a report cannot prove sanitization, reproducibility, source evidence references, and claim boundaries, it should remain a draft review artifact and must not support institutional-grade or compliance-approved claims.

## Environment Separation Verification

Environment separation is proof and audit evidence. It does not enable live trading, loosen broker routes, change order behavior, bypass risk gates, clear kill switches, grant AI order authority, or mutate ranking weights.

Every environment-separation review should record:

- Environment name.
- Execution lane.
- Data store.
- Runtime storage scope.
- Configuration namespace.
- Secrets scope.
- Broker route scope.
- Audit scope.
- Whether live autonomy is enabled.
- Whether broker-route mutation is allowed.
- Whether risk-gate bypass is allowed.
- Whether ranking mutation is allowed.
- Whether simulation evidence can mix with market-observed evidence.

Block institutional-readiness claims if any reviewed environment allows live autonomy, broker-route mutation, risk-gate bypass, ranking mutation, or simulation and observed evidence mixing without a separate approved future project.

## Permission Enforcement Coverage

Permission enforcement coverage is proof and audit evidence for research and governance metadata. It does not enable live trading, submit orders, loosen broker routes, bypass risk gates, clear kill switches, grant AI order authority, mutate ranking weights, or change risk limits.

Every permission-enforcement review should record:

- Role.
- Action.
- Resource.
- Whether the action was allowed.
- Whether the decision was enforced.
- Audited timestamp.
- Evidence snapshot identifier.
- Audit event identifier.
- Permission source or policy version.
- Decision boundary, such as research metadata only.
- Whether the permission record carries any order, live-order, broker-route, risk-gate, kill-switch, AI-order-authority, ranking-weight, or risk-limit authority.

Block small-fund or institutional-readiness claims if any reviewed permission record lacks evidence traceability, was not enforced, or carries authority to submit orders, change broker routes, bypass risk gates, clear kill switches, grant AI order authority, mutate ranking weights, or change risk limits.

## Approval Trace Completeness

Approval traces are proof and audit evidence for research and governance decisions. They do not approve live trading, submit orders, loosen broker routes, bypass risk gates, clear kill switches, grant AI order authority, mutate ranking weights, change risk limits, mutate immutable forecast records, or edit reward inputs after outcomes.

Every approval-trace review should record:

- Approval identifier.
- Actor.
- Reviewer role.
- Action.
- Affected research or governance entity.
- Strategy identifier.
- Strategy version.
- Promotion rule version.
- Timestamp.
- Evidence snapshot identifier.
- Audit event identifier.
- Previous status.
- New status.
- Approval scope.
- Decision reason.
- Claim boundary.
- Whether the approval record carries any live-trading, order-submission, broker-route, risk-gate, kill-switch, AI-order-authority, ranking-weight, risk-limit, forecast-record-mutation, or reward-input-edit authority.

Block small-fund or institutional-readiness claims if approval traces do not prove who changed what, when, with which evidence snapshot, under which approval scope, and without unsafe execution or control authority.

## Audit Event Completeness

Audit event completeness is proof and audit evidence for reviewability and tamper resistance. It does not submit orders, change execution behavior, loosen broker routes, bypass risk gates, clear kill switches, grant AI order authority, mutate ranking weights, change risk limits, or approve incomplete evidence.

Every audit event review should record:

- Event identifier.
- Event type.
- Actor.
- Affected research or governance entity.
- Timestamp.
- Evidence snapshot identifier.
- Source report.
- Event hash.
- Previous event hash.
- Whether the event is append-only.
- Whether the event is tamper-evident.
- Sanitization status.
- Safety boundary.
- Whether the audit event contains any secret, account identifier, raw log, raw local path, or authority to change execution, broker routes, risk gates, kill switches, AI order authority, ranking weights, or risk limits.

Block small-fund or institutional-readiness claims if audit events are incomplete, editable without trace, not tamper-evident, not sanitized, or carry unsafe execution or control authority.

## Model Version Traceability

Model version traceability is proof evidence for research review. It does not change ranking weights, change execution behavior, approve live trading, submit orders, loosen broker routes, or bypass risk gates.

Every model lineage review should record:

- Model identifier.
- Model version.
- Training data version.
- Feature version.
- Created timestamp.
- Approval identifier.
- Model artifact digest.
- Training window start.
- Training window end.
- Validation report identifier.
- Approval scope.

Block small-fund or institutional-readiness claims if model records cannot prove the exact version, training data, feature version, validation evidence, approval scope, and immutable artifact reference used by a forecast or benchmark result.

## Feature Lineage Completeness

Feature lineage completeness is proof evidence for reproducible research. It does not change ranking weights, change execution behavior, approve live trading, submit orders, loosen broker routes, or bypass risk gates.

Every feature lineage review should record:

- Feature identifier.
- Feature version.
- Source data version.
- Generated timestamp.
- Transformation version.
- Owner.
- Input snapshot identifier.
- Output schema version.
- No-lookahead flag.

Block small-fund or institutional-readiness claims if feature records cannot prove source version, transformation version, input snapshot, output schema, ownership, generation time, and no-lookahead behavior.

## Release Validation And Rollback Controls

Release validation and rollback controls are governance evidence only. They should record the release candidate, validation checks, reviewer, timestamp, result, rollback note, and affected research surfaces.

Rollback metadata must not auto-clear kill switches, bypass risk gates, change broker routes, change order behavior, enable live-money autonomy, or alter ranking weights.

Every release validation record should include:

- Release identifier.
- Branch, commit, pull request, or tagged build reference.
- Reason for the release.
- Scope of changed source, docs, tests, and operational surfaces.
- Affected proof surfaces, such as Data Completeness, Professional Benchmark, Walk-Forward, Execution Quality, Score Calibration, Risk Gate and Audit Trail, Portfolio Risk, Human vs System, Research Promotion, Evidence Reward, or Forecast Validation.
- Pre-release safety invariant result.
- Test, build, and route-probe summary where applicable.
- Current known blocker summary.
- Reviewer or automation check.
- Release decision: approve, hold, reject, or rollback required.
- Rollback target, rollback owner, and rollback verification plan.
- Sanitization check confirming no secrets, broker records, account identifiers, raw logs, raw local paths, credentials, database files, or environment values are present in the release evidence.

Stop release approval or mark the release unsafe if validation finds:

- Live trading was enabled without a separate approved future project.
- Broker routes, order submission logic, risk gates, kill-switch behavior, AI authority, or ranking weights changed unexpectedly.
- Simulation evidence was merged into real-time market-observed evidence.
- Local verification failed or required checks are missing.
- The rollback target is unknown.
- Release evidence exposes secrets, broker records, account identifiers, raw logs, raw local paths, credentials, database files, or environment values.

Every rollback record should include:

- Rollback trigger.
- Release identifier being rolled back.
- Rollback target.
- Whether runtime data changed during the failed release.
- Backup or restore requirement.
- Safety invariant result after rollback.
- Test, build, and route-probe summary after rollback where applicable.
- Incident record reference if the rollback responds to an incident.
- Follow-up owner and blocked redeploy condition.

Rollback records are review metadata only. They must not approve live trading, loosen broker routes, clear kill switches, bypass risk gates, change order behavior, mutate ranking weights, or treat failed proof evidence as passing evidence.

## Incident Management Runbook

Incident management is a review workflow for evidence, operations, and governance events. It does not place orders, change broker routes, clear kill switches, bypass risk gates, change ranking weights, or enable live-money autonomy.

Every incident record should include:

- Incident identifier.
- Opened timestamp.
- Severity.
- Detection source.
- First visible symptom.
- Owner.
- Affected research or control-plane entity.
- Affected proof surfaces, such as Data Completeness, Professional Benchmark, Walk-Forward, Execution Quality, Score Calibration, Risk Gate and Audit Trail, Portfolio Risk, Human vs System, Research Promotion, Evidence Reward, or Forecast Validation.
- Current safety state.
- Whether execution, broker routes, order logic, risk gates, kill switches, AI authority, ranking weights, or simulation separation were affected.
- Current status.
- Containment note.
- Corrective action.
- Verification performed before closure.
- Closed timestamp when resolved.
- Post-incident review note when applicable.

Incident reports must stay sanitized. They should exclude secrets, broker records, raw logs, account identifiers, and raw local paths.

Treat any of the following as a release-blocking or safety-boundary incident until reviewed:

- Unexpected live-trading enablement.
- Broker-route, order-submission, risk-gate, kill-switch, AI-authority, or ranking-weight changes.
- Simulation evidence merged into real-time market-observed evidence.
- Secrets, broker records, account identifiers, raw logs, raw local paths, database files, credentials, or environment values in an export, report, or support artifact.
- Failed local verification that affects proof surfaces or safety claims.

Incident records are review metadata only. They must not clear kill switches, bypass risk gates, change broker routes, change order behavior, enable live-money autonomy, mutate ranking weights, approve a release, or convert incomplete proof into passing proof.

## External Security Legal And Compliance Review Plan

External review is required before any institutional-grade, compliance-approved, broker-dealer, investment-adviser, direct-market-access, or HFT capability claim. This repository can prepare review evidence, but it cannot self-certify those statuses.

Before any institutional-grade claim, obtain and retain evidence of:

- External security review scope and result.
- Qualified legal review of product claims and operating model.
- Qualified compliance review of applicable market, advice, custody, recordkeeping, privacy, and supervision obligations.
- Vendor and infrastructure assumptions that the reviewers were asked to inspect.
- Sanitized firm-grade report sample or report specification.
- Environment-separation and permission-enforcement evidence.
- Claim review note that lists exactly which claims remain blocked.
- Evidence that paper-first unattended execution remains the active boundary unless a separately approved future framework changes it.
- Evidence that reward and forecast analytics are research-only.
- Evidence that AI has no order authority.
- Evidence that risk gates remain authoritative.
- Evidence that broker routes remain unchanged.

Every external review evidence packet should include:

- Review packet identifier.
- Security review scope.
- Legal review scope.
- Compliance review scope.
- Vendor or infrastructure dependency scope.
- Sanitized firm-grade report reference.
- Environment-separation evidence reference.
- Permission-enforcement evidence reference.
- Safety-boundary evidence reference.
- Claim boundaries to review.
- Reviewer type or qualification note.
- Review status: planned, in review, passed, failed, or blocked.
- Sanitization check confirming no secrets, broker records, account identifiers, raw logs, raw local paths, credentials, database files, or environment values are present.

Stop any institutional-grade, compliance-approved, investment-adviser, broker-dealer, direct-market-access, or HFT claim if the packet is missing any required scope, reviewer qualification, sanitized report evidence, safety-boundary evidence, or sanitization check.

External review packets are review evidence only. They do not certify compliance, approve live trading, change broker routes, change order behavior, bypass risk gates, clear kill switches, grant AI order authority, mutate ranking weights, or prove alpha.

If any external review is missing, the allowed positioning remains paper-first trading research platform, trading evidence operating system, forecast validation platform, decision audit system, research-to-risk workflow, paper execution quality analysis, structured strategy improvement system, and benchmark or walk-forward research layer.

## Connected Broker Boundary

- Keep the product positioned as live trading control-plane software layered over connected brokers.
- Connected brokers handle custody, account statements, execution, and regulatory brokerage functions.
- If the business model changes so the company may be acting as a broker or dealer under SEC guidance, stop launch work for that scope until qualified legal and compliance reviewers approve the path.
- Broker-dealer registration, SRO membership, SIPC membership where applicable, state requirements, associated-person qualification, supervisory procedures, books and records, financial responsibility, AML, BCP, privacy, and electronic signature controls are external operating obligations, not features this repository can satisfy by itself.

## Reg BI and Advice Boundary

- Keep the product self-directed and supervised by default.
- Avoid copy that implies promised performance, managed money, or discretionary advice.
- If recommendations are made to retail customers, review SEC Regulation Best Interest obligations before launch.
- Keep `FEATURE_MANAGED_ADVISORY=false` unless investment adviser obligations have been reviewed and approved.

## Market Access and Best Execution

- Maintain pre-trade controls for notional, size, duplicate orders, stale data, authorization, symbols, instruments, loss limits, drawdown, kill switches, and audit logging.
- Review SEC Rule 15c3-5 market access materials before any direct market-access path is activated.
- Review FINRA best-execution guidance before routing customer orders or assessing third-party routing outcomes.

## Automated Investment Advice

- Automated or discretionary investment advice can trigger investment-adviser issues.
- Keep automated strategy execution as user-authorized, risk-gated, and approval-recorded software unless qualified reviewers approve a different operating model.

## Evidence Requirements

- Store who authorized live trading.
- Store readiness snapshot and active risk policy at time of arm/start/order.
- Store every `TradeDecision`, `LiveOrderIntent`, `LiveRiskCheck`, `BrokerExecutionReceipt`, risk event, kill-switch event, replay event, and audit export.
- Preserve full replay ordering and evidence payloads.

Official references:
- SEC broker-dealer guide: https://www.sec.gov/about/divisions-offices/division-trading-markets/division-trading-markets-compliance-guides/guide-broker-dealer-registration
- SEC Reg BI FAQ: https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/faq-regulation-best
- SEC Market Access Rule FAQ: https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/divisionsmarketregfaq-0
- FINRA best execution: https://www.finra.org/rules-guidance/guidance/reports/2022-finras-examination-and-risk-monitoring-program/best-execution
- SEC automated investment advice: https://www.sec.gov/about/divisions-offices/office-strategic-hub-innovation-financial-technology-finhub/automated-investment-advice
- SIPC member firms: https://www.sipc.org/for-members/
