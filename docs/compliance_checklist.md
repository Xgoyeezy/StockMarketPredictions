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

## Release Validation And Rollback Controls

Release validation and rollback controls are governance evidence only. They should record the release candidate, validation checks, reviewer, timestamp, result, rollback note, and affected research surfaces.

Rollback metadata must not auto-clear kill switches, bypass risk gates, change broker routes, change order behavior, enable live-money autonomy, or alter ranking weights.

## Incident Management Runbook

Incident management is a review workflow for evidence, operations, and governance events. It does not place orders, change broker routes, clear kill switches, bypass risk gates, change ranking weights, or enable live-money autonomy.

Every incident record should include:

- Incident identifier.
- Opened timestamp.
- Severity.
- Owner.
- Affected research or control-plane entity.
- Current status.
- Containment note.
- Corrective action.
- Closed timestamp when resolved.
- Post-incident review note when applicable.

Incident reports must stay sanitized. They should exclude secrets, broker records, raw logs, account identifiers, and raw local paths.

## External Security Legal And Compliance Review Plan

External review is required before any institutional-grade, compliance-approved, broker-dealer, investment-adviser, direct-market-access, or HFT capability claim. This repository can prepare review evidence, but it cannot self-certify those statuses.

Before any institutional-grade claim, obtain and retain evidence of:

- External security review scope and result.
- Qualified legal review of product claims and operating model.
- Qualified compliance review of applicable market, advice, custody, recordkeeping, privacy, and supervision obligations.
- Evidence that paper-first unattended execution remains the active boundary unless a separately approved future framework changes it.
- Evidence that reward and forecast analytics are research-only.
- Evidence that AI has no order authority.
- Evidence that risk gates remain authoritative.
- Evidence that broker routes remain unchanged.

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
