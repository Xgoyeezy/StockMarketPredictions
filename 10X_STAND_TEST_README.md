# 10x Stand Test

This is the test you asked for: whether the current strategy result can stand as a 10x account-value result from a $100,000 start.

It is not the conservative live-risk overlay.

The conservative overlay is for survival and controlled deployment. This 10x stand test is for validation. It keeps the current strategy logic and checks whether the 10x result survives costs, delayed fills, drawdowns, and notional caps.

## Run the report

Use your local database:

```bash
python scripts/run_10x_stand_test.py --tenant-slug alpha-desk --starting-capital 100000 --current-equity 1900000 --known-peak-equity 1900000 --known-drawdown-peak 1800000 --known-drawdown-trough 1300000 --known-max-drawdown-pct 27.8
```

Or use an exported ledger:

```bash
python scripts/run_10x_stand_test.py --ledger-csv runtime-exports/strategy-validation/latest/trade_validation_ledger.csv
```

The output goes to:

```text
runtime-exports/ten-x-stand-test/latest/TEN_X_STAND_TEST_REPORT.md
```

## Optional paper-only 10x profile

This applies an aggressive validation profile to broker-paper mode only. It is not for live trading.

Dry run first:

```bash
python scripts/apply_10x_paper_validation_profile.py --tenant-slug alpha-desk --dry-run
```

Save it unarmed:

```bash
python scripts/apply_10x_paper_validation_profile.py --tenant-slug alpha-desk
```

Save it armed for broker-paper orders:

```bash
python scripts/apply_10x_paper_validation_profile.py --tenant-slug alpha-desk --arm-paper-orders
```

## Pass criteria

The result is strong only if the report shows:

- Reference equity is at least 10x the starting capital.
- Local ledger has enough closed trades to test.
- 2x slippage still reaches 10x.
- Next-bar 10 bps penalty still reaches 10x.
- Drawdown remains under 30% in the aggressive paper validation profile.
- Lower leverage tests still show edge, even if they do not reach 10x.

If only the original unrestricted run reaches 10x, the strategy is probably leverage-dependent or execution-sensitive.
