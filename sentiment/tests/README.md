# Tests

Run from the repo root:

```bash
.venv/bin/python -m pytest
```

The suite needs no network and no credentials. Alpaca is replaced by `SimBroker` over synthetic
daily bars, and the `claude` call by a function that returns a fixed reply.

The `state` fixture in `conftest.py` points `DATA_DIR` at a temp folder initialised from the
defaults, and moves the `HALT` file next to it.

| File | Covers |
|---|---|
| `test_config.py` | Config bounds, data folder init, config upgrade, watchlist |
| `test_broker.py` | Paper-only endpoint, `ALLOW_LIVE` gate, separate-account check, Alpaca status and error translation, backend selection (incl. the IBKR stub), bar and news conversion |
| `test_db.py` | `trader.db`: migrations, the pinned shared core schema, the CSV mirror for every book, import, check, backup and restore |
| `test_broker_contract.py` | The contract every execution backend must pass: SimBroker, and Alpaca on the in-memory client in `fake_alpaca.py` |
| `test_news.py` | Dedupe, date bucketing, fetch cursor, status |
| `test_universe.py` | Liquidity screen, universe file, watchlist merge |
| `test_score.py` | Symbol selection, batches, output validation, retries, prompt injection, score files, desk in the score file |
| `test_desk.py` | Desk input, idea validation, pair sides, evidence, retries, prompt injection, market moves |
| `test_pairs.py` | Pair discovery, half-life, z-scores, divergences, pairs file |
| `test_risk.py` | ATR, stops, sizing, book limits, daily loss |
| `test_rules.py` | Entry signals, hold days, exit reasons, candidates from scores and ideas, ranking, entry planning, pairs both-or-neither |
| `test_trader.py` | Run and close jobs, restart reconciliation, kill switch, loss limit, replay, pair entries and exits |
| `test_analytics.py` | Paper-trader column parity, trade rows, R ranges, report, day report, holdings, comparison |
| `test_memory.py` | Score calibration, lifetime layout, evidence queries and the registration cutoff, memory ops, status from evidence, prompt filtering, old-memory conversion, daily journal |
