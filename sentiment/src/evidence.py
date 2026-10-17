"""Hypothesis queries and the evidence test that decides a memory entry's status.

A query names a table and conditions joined with "and":

    scores: score >= 1 and reason contains "guidance"
    scores: confidence < 0.6
    trades: side == short and hold_days >= 3

`scores` has one row per non-zero score whose forward window has completed; its metric is the signed
forward return in % (the move in the score's direction). `trades` has one row per closed trade; its
metric is pnl_r. A query passes when, over enough rows and days, the metric's 95% range (grouped by
day, since same-day rows share market conditions) excludes zero, and the rows after the hypothesis
was registered point the same way. Only rows after registration can make it pass, so a hypothesis
cannot be fitted to the data it was drawn from.
"""
import math
import re

import numpy as np
import pandas as pd

T_95 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36, 8: 2.31, 9: 2.26, 10: 2.23,
        12: 2.18, 15: 2.13, 20: 2.09, 30: 2.04, 60: 2.00, 120: 1.98}
NUM, TEXT = "num", "text"
TABLES = {
    "scores": {"metric": "signed_return_pct", "min_n": 30, "min_days": 10, "min_since": 15,
               "columns": {"symbol": TEXT, "score": NUM, "confidence": NUM, "articles": NUM, "reason": TEXT,
                           "weekday": NUM}},
    "trades": {"metric": "pnl_r", "min_n": 20, "min_days": 10, "min_since": 10,
               "columns": {"symbol": TEXT, "side": TEXT, "score": NUM, "confidence": NUM, "hold_days": NUM,
                           "exit_reason": TEXT, "strategy": TEXT, "pair": TEXT, "weekday": NUM}},
}
CONFIRM_FACTOR = 2  # confirmed needs twice the minimum rows overall and since registration
CONDITION = re.compile(r"^\s*(\w+)\s*(==|!=|>=|<=|>|<|in|contains)\s*(.+?)\s*$")


class QueryError(ValueError):
    pass


def parse(text):
    """'<table>: col op value [and ...]' -> (table, [(col, op, value)]). Raises QueryError."""
    table, sep, rest = str(text).partition(":")
    table = table.strip()
    if not sep or table not in TABLES:
        raise QueryError(f"query must start with a table, one of {sorted(TABLES)}, then ':'")
    columns = TABLES[table]["columns"]
    conditions = []
    for part in re.split(r"\s+and\s+", rest.strip()):
        m = CONDITION.match(part)
        if not m or m.group(1) not in columns:
            raise QueryError(f"bad condition {part!r}; {table} columns: {sorted(columns)}")
        col, op, raw = m.groups()
        kind = columns[col]
        if op == "contains" and kind != TEXT:
            raise QueryError(f"'contains' needs a text column: {part!r}")
        try:
            if op == "in":
                if not (raw.startswith("[") and raw.endswith("]")):
                    raise QueryError(f"'in' needs a [list]: {part!r}")
                value = [_cast(v, kind) for v in raw[1:-1].split(",") if v.strip()]
            else:
                value = _cast(raw, kind)
        except ValueError as e:
            raise QueryError(f"bad value in {part!r}: {e}") from None
        conditions.append((col, op, value))
    return table, conditions


def _cast(raw, kind):
    raw = raw.strip().strip("'\"")
    return float(raw) if kind == NUM else raw


def apply(df, table, conditions):
    columns = TABLES[table]["columns"]
    df = df.assign(weekday=pd.to_datetime(df["date"]).dt.weekday)
    mask = pd.Series(True, index=df.index)
    for col, op, value in conditions:
        if columns[col] == NUM:
            s = pd.to_numeric(df[col], errors="coerce")
        else:
            s = df[col].fillna("").astype(str)
        if op == "in":
            mask &= s.isin(value)
        elif op == "contains":
            mask &= s.str.lower().str.contains(value.lower(), regex=False)
        elif columns[col] == TEXT and op not in ("==", "!="):
            raise QueryError(f"{op} needs a number column, {col} is text")
        else:
            mask &= {"==": s.eq, "!=": s.ne, ">=": s.ge, "<=": s.le, ">": s.gt, "<": s.lt}[op](value)
    return df[mask]


def t_95(dof):
    return T_95[max(k for k in T_95 if k <= dof)] if dof < 120 else 1.96


def mean_ci(values, days):
    """Mean with a 95% range that treats each day as one cluster."""
    keep = values.notna()
    values, days = values[keep], days[keep]
    n, n_days = len(values), days.nunique()
    out = {"n": n, "days": n_days, "mean": float(values.mean()) if n else float("nan"),
           "lo": float("nan"), "hi": float("nan")}
    if n_days >= 2:
        resid = (values - out["mean"]).groupby(days).sum()
        half = t_95(n_days - 1) * math.sqrt(n_days / (n_days - 1) * float((resid ** 2).sum()) / n ** 2)
        out |= {"lo": out["mean"] - half, "hi": out["mean"] + half}
    return out


def score_rows(outcomes):
    """The scores table: non-zero scored outcomes with their signed forward return."""
    if outcomes is None or len(outcomes) == 0:
        return pd.DataFrame(columns=["date", *TABLES["scores"]["columns"], "signed_return_pct"])
    df = outcomes[outcomes["score"] != 0].copy()
    df["signed_return_pct"] = 100 * df["fwd_return"] * np.sign(df["score"])
    for col in ("articles", "reason"):
        if col not in df:
            df[col] = np.nan if col == "articles" else ""
    return df


def evaluate(query, tables, registered):
    """Test one hypothesis. `tables` maps table name to its rows; `registered` is the date it was written."""
    table, conditions = parse(query)
    spec = TABLES[table]
    subset = apply(tables[table], table, conditions)
    metric = pd.to_numeric(subset[spec["metric"]], errors="coerce")
    days = pd.to_datetime(subset["date"]).dt.date
    overall = mean_ci(metric, days)
    after = days > registered
    since = mean_ci(metric[after], days[after])
    reasons = []
    if overall["n"] < spec["min_n"] or overall["days"] < spec["min_days"]:
        reasons.append(f"{overall['n']} rows over {overall['days']} days; needs {spec['min_n']}+ over "
                       f"{spec['min_days']}+")
    if math.isnan(overall["lo"]):
        reasons.append("no 95% range yet")
    elif overall["lo"] <= 0 <= overall["hi"]:
        reasons.append(f"95% range [{overall['lo']:+.2f}, {overall['hi']:+.2f}] includes zero")
    if since["n"] < spec["min_since"]:
        reasons.append(f"{since['n']} rows since {registered}; needs {spec['min_since']}+")
    elif (since["mean"] > 0) != (overall["mean"] > 0):
        reasons.append(f"rows since {registered} average {since['mean']:+.2f}, the other direction")
    passed = not reasons
    confirmed = (passed and overall["n"] >= CONFIRM_FACTOR * spec["min_n"]
                 and since["n"] >= CONFIRM_FACTOR * spec["min_since"])
    return {"table": table, "metric": spec["metric"], "all": _rounded(overall), "since_registered": _rounded(since),
            "passed": passed, "status": "confirmed" if confirmed else "supported" if passed else "hypothesis",
            "reasons": reasons}


def _rounded(ci):
    return {k: (round(v, 3) if isinstance(v, float) and not math.isnan(v) else (None if isinstance(v, float) else v))
            for k, v in ci.items()}


def summary(result):
    a, s = result["all"], result["since_registered"]
    rng = "n/a" if a["lo"] is None else f"[{a['lo']:+.2f}, {a['hi']:+.2f}]"
    mean = "n/a" if a["mean"] is None else f"{a['mean']:+.2f}"
    since = "n/a" if s["mean"] is None else f"{s['mean']:+.2f}"
    verdict = "passes" if result["passed"] else "not yet: " + "; ".join(result["reasons"])
    return (f"{a['n']} rows over {a['days']} days, mean {result['metric']} {mean}, 95% range {rng}; "
            f"{s['n']} since registration (mean {since}); {verdict}")
