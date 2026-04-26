# Institutional Multi-Account Equities Trading Core

This package is a paper-safe control plane for U.S. equities automation. It is separate from the parent personal research app and separate from `hft_system`.

Default behavior does not send live broker orders. Live trading requires explicit credential setup, broker approval, legal/compliance review, operational runbooks, and a separate enablement step.

## Local Run

Enabled currently means paper-only local service operation. The service writes local runtime state and audit records, but it does not enable live IBKR order submission.

```bash
python scripts/manage_institutional_trading.py --config institutional_trading/config/example.yaml start
python scripts/manage_institutional_trading.py --config institutional_trading/config/example.yaml status
python scripts/manage_institutional_trading.py --config institutional_trading/config/example.yaml health
python scripts/manage_institutional_trading.py --config institutional_trading/config/example.yaml kill --reason manual_test
python scripts/manage_institutional_trading.py --config institutional_trading/config/example.yaml stop
python -m unittest discover -s institutional_trading/tests -t .
```

The same commands are exposed from the repo root:

```bash
make institutional-start
make institutional-status
make institutional-health
make institutional-kill
make institutional-stop
make institutional-test
```

The long-lived service entrypoint is:

```bash
python -m institutional_trading.cli --config institutional_trading/config/example.yaml run
```

It fails closed if `broker.live_trading_enabled` is `true` or `broker.paper.enabled` is not `true`.

## Implemented Controls

- Normalized minute and tick records with pre-market, regular, after-hours, and closed session labels.
- Provider failover for redundant data sources.
- Stateless strategy interface plus a versioned mean-reversion example.
- Deterministic proportional and fixed multi-account allocation.
- Explicit order state machine with idempotent paper broker submission.
- Extended-hours limit-order enforcement.
- Partial-fill handling and per-account fill reports.
- Aggregate pre-trade risk checks, daily-loss/drawdown guards, circuit breaker, and global kill switch.
- Hash-chained JSONL audit log with a SQLite query index.
- Deterministic replay readers for audit and market records.
- Backtest engine using the same strategy interface.
- CLI and local process manager for `start`, `stop`, `status`, `health`, `kill`, `backtest`, `replay`, and `reconcile`.

## Compliance And Broker References

The design is aligned around auditability, risk controls, and recordkeeping, but it is not a legal certification or a broker-dealer compliance program by itself.

- [SEC 17a-4 electronic recordkeeping amendments](https://www.sec.gov/investment/amendments-electronic-recordkeeping-requirements-broker-dealers)
- [SEC Rule 15c3-5 market access risk controls guide](https://www.sec.gov/file/small-entity-compliance-guide-27)
- [FINRA Rule 4511](https://www.finra.org/rules-guidance/rulebooks/finra-rules/4511)
- [IBKR TWS API docs](https://www.interactivebrokers.com/campus/ibkr-api-page/twsapi-doc/)
