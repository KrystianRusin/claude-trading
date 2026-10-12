# Tests

Run from the repo root:

```bash
.venv/bin/python -m pytest
```

The suite needs no network and no credentials. Alpaca is replaced by `SimBroker` over synthetic
daily bars, and the `claude` call by a function that returns a fixed reply.

The `state` fixture in `conftest.py` points `STATE_DIR` at a temp folder initialised from the
defaults, and moves the `HALT` file there.

| File | Covers |
|---|---|
| `test_config.py` | Config bounds, state init, watchlist |
| `test_broker.py` | Paper-only endpoint, separate-account check, bar and news conversion |
| `test_news.py` | Dedupe, date bucketing, fetch cursor, status |
| `test_universe.py` | Liquidity screen, universe file, watchlist merge |
| `test_score.py` | Symbol selection, batches, output validation, retries, prompt injection, score files |
| `test_risk.py` | ATR, stops, sizing, book limits, daily loss |
| `test_rules.py` | Entry signals, hold days, exit reasons, ranking, entry planning |
| `test_trader.py` | Run and close jobs, restart reconciliation, kill switch, loss limit, replay |
| `test_analytics.py` | Paper-trader column parity, trade rows, R ranges, report, day report, holdings, comparison |
| `test_memory.py` | Score calibration, lifetime layout, review validation, memory in scoring |
