"""Attention: how unusual the interest in each stock is, and the discovery list built from it (ticket 009).

    python src/attention.py collect volume       full-market daily bars of every listed stock (19:00)
    python src/attention.py collect apewisdom    a snapshot of Reddit mention counts (06:00 and 18:00)
    python src/attention.py morning              Reddit and Wikipedia, then the features of the last session
                                                 and today's discovery list (06:00, before scoring)
    python src/attention.py features [DATE]      (re)compute one session's features into trader.db
    python src/attention.py discover             write today's discovery list again
    python src/attention.py show [SYMBOL]        the latest features, or one symbol's history

Four sources, each measured against the symbol's own baseline in log space (a z-score), so a big name's
normal attention never looks like a spike:
- volume: today's volume against the previous 20 sessions (Alpaca's free full-market history)
- news: articles that day against the previous 20 days, counted from the news archive (data/news/)
- wikipedia: page views against the previous 30 days (the Wikimedia API; Wikidata maps tickers to articles)
- reddit: mentions across stock subreddits against the previous 20 days of snapshots (ApeWisdom's API)

Raw data is archived under data/attention/<source>/ as it is fetched and never rewritten, because two of
the sources keep no history of their own. A source that fails is logged and skipped; the others carry on.

The discovery list is collect-only for now: names outside the core universe with a genuine surge in
attention, written to data/discovery/<date>.json and measured, never traded.
"""
import argparse
import gzip
import json
import logging
import math
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

import db
import paths
from broker import ET

LOG = logging.getLogger("attention")
USER_AGENT = "claude-sentiment-trading/1.0 (personal research; https://github.com/KrystianRusin/claude-sentiment-trading)"
SOURCES = ("volume", "news", "wikipedia", "reddit")
Z_FIELDS = {"volume": "volume_z", "news": "news_z", "wikipedia": "wiki_z", "reddit": "reddit_z"}
BAR_LOOKBACK_DAYS = 45  # calendar days of bars for a 20-session baseline
MIN_PRICE, MIN_DOLLAR_VOLUME = 1.0, 1_000_000  # symbols kept in the volume archive
APEWISDOM = "https://apewisdom.io/api/v1.0/filter/all-stocks/page/{page}"
WIKIDATA = "https://query.wikidata.org/sparql"
PAGEVIEWS = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
             "{article}/daily/{start}/{end}")
WIKI_MAP_DAYS = 7  # refresh the ticker -> article map weekly
WIKI_QUERY = """SELECT ?ticker ?article WHERE {
  VALUES ?exchange { wd:Q13677 wd:Q82059 wd:Q846626 }
  ?company p:P414 ?st . ?st ps:P414 ?exchange ; pq:P249 ?ticker .
  ?article schema:about ?company ; schema:isPartOf <https://en.wikipedia.org/> .
}"""  # tickers on NYSE, NASDAQ and NYSE American with an English Wikipedia article
KEEP_Z = 1.5  # outside the universe, a symbol gets a features row when some source is at least this unusual
CALIBRATION_DAYS = 180
HORIZONS = (1, 3, 5)


# ---- the raw archive

def _raw_dir(source):
    return paths.attention_dir() / source


def write_raw(source, day, data, stamp=None):
    """Archive one fetch as data/attention/<source>/<day>[_<stamp>].json.gz. Returns the path."""
    folder = _raw_dir(source)
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / f"{day.isoformat()}{'_' + stamp if stamp else ''}.json.gz"
    tmp = f.with_name(f.name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump({"source": source, "date": day.isoformat(), "fetched_at": datetime.now(ET).isoformat(timespec="seconds"),
                   "data": data}, fh, separators=(",", ":"))
    tmp.replace(f)
    return f


def _read(f):
    try:
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        LOG.warning("unreadable attention file %s skipped", f)
        return None


def raw_files(source):
    """[(day, stamp, path)] of a source's archive, oldest first."""
    out = []
    for f in _raw_dir(source).glob("*.json.gz") if _raw_dir(source).is_dir() else []:
        name = f.name[:-len(".json.gz")]
        day, _, stamp = name.partition("_")
        try:
            out.append((date.fromisoformat(day), stamp, f))
        except ValueError:
            continue
    return sorted(out)


def read_raw(source, day):
    """The data of a source's last fetch dated `day`, or None."""
    files = [f for d, _, f in raw_files(source) if d == day]
    rec = _read(files[-1]) if files else None
    return rec["data"] if rec else None


# ---- statistics

def zscore(value, baseline, floor):
    """How unusual `value` is against `baseline`, in log space: (log1p(value) - mean) / max(std, floor).
    None when the baseline is too short to mean anything."""
    if value is None or len(baseline) < 5:
        return None
    logs = [math.log1p(max(v, 0)) for v in baseline]
    sd = statistics.pstdev(logs)
    return round((math.log1p(max(value, 0)) - statistics.fmean(logs)) / max(sd, floor), 3)


# ---- HTTP

def get_json(url, timeout=30, retries=2, data=None, headers=None):
    """GET (or POST `data`) a JSON document with a descriptive User-Agent, retrying with a pause."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
                                         | (headers or {}))
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except Exception:
            if attempt == retries:
                raise
            time.sleep(2 * (attempt + 1))


# ---- volume: full-market daily bars

def volume_metrics(bars, acfg):
    """{symbol: metrics} for the latest session in `bars` ({symbol: daily DataFrame}): its open and close, the
    move that day and over 5 sessions, average dollar volume, and volume against the previous sessions.
    Keeps symbols whose last bar is that session, priced and traded above the archive's minimums."""
    last = {s: df.index[-1] for s, df in bars.items() if df is not None and len(df)}
    if not last:
        return None, {}
    session = max(set(last.values()), key=list(last.values()).count)
    n = acfg["baseline_days"]
    out = {}
    for s, df in bars.items():
        if last.get(s) != session or len(df) < 2:
            continue
        close, vol = df["close"].astype(float), df["volume"].astype(float)
        prev = vol.iloc[-n - 1:-1]
        dollar_volume = float((close.iloc[-n:] * vol.iloc[-n:]).mean())
        if float(close.iloc[-1]) < MIN_PRICE or dollar_volume < MIN_DOLLAR_VOLUME:
            continue
        mean_prev = float(prev.mean()) if len(prev) else 0.0
        out[s] = {"open": round(float(df["open"].iloc[-1]), 4), "close": round(float(close.iloc[-1]), 4),
                  "ret_1d": round((float(close.iloc[-1]) / float(close.iloc[-2]) - 1) * 100, 3),
                  "ret_5d": round((float(close.iloc[-1]) / float(close.iloc[max(-6, -len(close))]) - 1) * 100, 3),
                  "dollar_volume": round(dollar_volume), "volume": float(vol.iloc[-1]),
                  "rvol": round(float(vol.iloc[-1]) / mean_prev, 3) if mean_prev else None,
                  "volume_z": zscore(float(vol.iloc[-1]), [float(v) for v in prev], acfg["z_floor"]),
                  "history": int(len(df))}
    return session, out


def collect_volume(cfg, now):
    from broker import make_market_data

    md = make_market_data(cfg, feed="sip")
    symbols = [a["symbol"] for a in md.list_equities()]
    bars = md.daily_bars(symbols, now - timedelta(days=BAR_LOOKBACK_DAYS), now)
    session, metrics = volume_metrics(bars, cfg["attention"])
    if session is None:
        raise RuntimeError("no daily bars came back")
    write_raw("volume", session, metrics)
    return f"volume: {len(metrics)} symbols for {session}"


# ---- reddit: ApeWisdom mention counts

def collect_apewisdom(now, max_pages=20):
    out, page, pages = {}, 1, 1
    while page <= min(pages, max_pages):
        d = get_json(APEWISDOM.format(page=page))
        pages = int(d.get("pages") or 1)
        for r in d.get("results", []):
            t = str(r.get("ticker") or "").upper()
            if t:
                out[t] = {k: r.get(k) for k in ("mentions", "upvotes", "rank", "mentions_24h_ago", "rank_24h_ago")}
        page += 1
        time.sleep(1)  # polite: a handful of requests twice a day
    write_raw("reddit", now.date(), out, stamp=now.strftime("%H%M"))
    return f"reddit: {len(out)} tickers"


def reddit_series(until):
    """{symbol: [mentions per day]} for the 20 days before `until`'s snapshot day, and the current snapshot:
    each day is that day's last snapshot taken at or before `until`."""
    by_day = {}
    for d, stamp, f in raw_files("reddit"):
        when = datetime.combine(d, datetime.strptime(stamp or "2359", "%H%M").time(), ET)
        if when <= until:
            by_day[d] = f  # the last one of the day wins (files sort by stamp)
    if not by_day:
        return None, {}
    days = sorted(by_day)
    current = _read(by_day[days[-1]])
    history = [(_read(by_day[d]) or {}).get("data", {}) for d in days[:-1][-20:]]
    return (current or {}).get("data", {}), history


# ---- wikipedia: page views

def wiki_map(now, refresh=False):
    """{ticker: article}, refreshed from Wikidata weekly into data/attention/wiki_map.json."""
    f = paths.attention_dir() / "wiki_map.json"
    if f.exists() and not refresh:
        rec = json.loads(f.read_text(encoding="utf-8"))
        if (now.date() - date.fromisoformat(rec["fetched"])).days < WIKI_MAP_DAYS:
            return rec["map"]
    try:
        url = WIKIDATA + "?" + urllib.parse.urlencode({"query": WIKI_QUERY, "format": "json"})
        rows = get_json(url, timeout=120, headers={"Accept": "application/sparql-results+json"})["results"]["bindings"]
    except Exception:
        if f.exists():
            LOG.warning("Wikidata unreachable; keeping the previous ticker map", exc_info=True)
            return json.loads(f.read_text(encoding="utf-8"))["map"]
        raise
    mapping = {}
    for r in rows:
        mapping.setdefault(r["ticker"]["value"].upper(), urllib.parse.unquote(r["article"]["value"].rsplit("/", 1)[1]))
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"fetched": now.date().isoformat(), "map": mapping}), encoding="utf-8")
    return mapping


def collect_wikipedia(symbols, cfg, now, pause=0.05):
    """Page views of each symbol's article: the latest complete day and its z against the previous days."""
    mapping = wiki_map(now)
    acfg = cfg["attention"]
    n = acfg["wiki_baseline_days"]
    end = now.date() - timedelta(days=1)
    start = end - timedelta(days=n + 3)
    out, failures = {}, 0
    for s in sorted(set(symbols)):
        article = mapping.get(s)
        if not article:
            continue
        url = PAGEVIEWS.format(article=urllib.parse.quote(article.replace(" ", "_"), safe=""),
                               start=start.strftime("%Y%m%d"), end=end.strftime("%Y%m%d"))
        try:
            items = get_json(url, timeout=20, retries=1).get("items", [])
        except Exception:
            failures += 1
            if failures >= 20:
                LOG.warning("Wikipedia: stopping after 20 failed requests")
                break
            continue
        if not items:
            continue
        days = [date(int(i["timestamp"][:4]), int(i["timestamp"][4:6]), int(i["timestamp"][6:8])) for i in items]
        views = [int(i.get("views", 0)) for i in items]
        weekend = days[-1].weekday() >= 5  # traffic dips at weekends: compare weekdays with weekdays
        baseline = [v for d, v in zip(days[:-1], views[:-1]) if (d.weekday() >= 5) == weekend][-n:]
        out[s] = {"date": days[-1].isoformat(), "views": views[-1], "wiki_z": zscore(views[-1], baseline, acfg["z_floor"])}
        time.sleep(pause)
    if out:
        write_raw("wikipedia", end, out)
    return f"wikipedia: {len(out)} articles"


# ---- news: counted from the news archive

def news_counts(day, symbols):
    import news

    counts = {}
    for a in news.read_day(day):
        for s in a.get("symbols") or []:
            if s in symbols:
                counts[s] = counts.get(s, 0) + 1
    return counts


# ---- features

def _session_before(day):
    """The latest archived volume session strictly before `day`, or None."""
    days = [d for d, _, _ in raw_files("volume") if d < day]
    return days[-1] if days else None


def features(session, cfg, core, now=None, denylist=()):
    """One row per symbol for `session`: each source's z, breadth and acceleration. Rows go to symbols in the
    core universe (`core`), and to listed symbols outside it when a source is unusual or Reddit names them."""
    now = now or datetime.now(ET)
    acfg = cfg["attention"]
    volume = read_raw("volume", session)
    if not volume:
        return []
    elevated, floor = acfg["elevated_z"], acfg["z_floor"]
    wiki = {}
    for d, _, f in raw_files("wikipedia"):
        if d >= session:  # the first fetch on or after the session covers it
            rec = _read(f)
            wiki = {s: v for s, v in (rec or {}).get("data", {}).items() if v.get("date") == session.isoformat()}
            if wiki:
                break
    reddit_now, reddit_history = reddit_series(max(now, datetime.combine(session, datetime.max.time(), ET)))
    reddit_now = reddit_now or {}
    news_days = [session - timedelta(days=i) for i in range(acfg["baseline_days"], -1, -1)]
    candidates = set(core) | {s for s, m in volume.items() if (m.get("volume_z") or 0) >= KEEP_Z} \
        | (set(reddit_now) & set(volume)) | {s for s, w in wiki.items() if (w.get("wiki_z") or 0) >= KEEP_Z}
    candidates &= set(volume)
    counts = {d: news_counts(d, candidates) for d in news_days}
    previous = {r["symbol"]: r for r in db.read_attention(since=(_session_before(session) or session).isoformat())
                if r["date"] == (_session_before(session) or date.min).isoformat()}
    rows = []
    for s in sorted(candidates):
        v = volume[s]
        articles = counts[session].get(s, 0)
        news_z = zscore(articles, [counts[d].get(s, 0) for d in news_days[:-1]], floor)
        mentions = (reddit_now.get(s) or {}).get("mentions")
        reddit_z = None
        if len(reddit_history) >= acfg["min_reddit_days"]:
            reddit_z = zscore(mentions or 0, [(h.get(s) or {}).get("mentions") or 0 for h in reddit_history], floor)
        if s in denylist and not max(v.get("volume_z") or 0, news_z or 0) >= elevated:
            reddit_z = None  # a word that is also a ticker: only trusted with a volume or news spike
        w = wiki.get(s) or {}
        zs = {"volume_z": v.get("volume_z"), "news_z": news_z, "wiki_z": w.get("wiki_z"), "reddit_z": reddit_z}
        if s not in core and not any((z or 0) >= KEEP_Z for z in zs.values()) and not mentions:
            continue
        top = max((z for z in zs.values() if z is not None), default=None)
        before = previous.get(s)
        top_before = max((before[k] for k in Z_FIELDS.values() if before and before[k] is not None), default=None)
        rows.append({"date": session.isoformat(), "symbol": s, "in_universe": int(s in core), **zs, "rvol": v.get("rvol"),
                     "articles": articles, "page_views": w.get("views"), "mentions": mentions,
                     "breadth": sum((z or 0) >= elevated for z in zs.values()),
                     "acceleration": round(top - top_before, 3) if None not in (top, top_before) else None,
                     "close": v.get("close"), "ret_1d": v.get("ret_1d")})
    return rows


# ---- discovery

def discover(rows, session, cfg, today):
    """Today's discovery list: names outside the core universe that are tradeable, with attention from at
    least `min_breadth` sources including a volume or news surge, not already run up, and not fading."""
    dcfg, elevated = cfg["discovery"], cfg["attention"]["elevated_z"]
    volume = read_raw("volume", session) or {}
    picked, rejected = [], {}

    def reject(why):
        rejected[why] = rejected.get(why, 0) + 1

    for r in rows:
        if r["in_universe"]:
            continue
        v = volume.get(r["symbol"], {})
        if (v.get("close") or 0) < dcfg["min_price"]:
            reject("price")
        elif (v.get("dollar_volume") or 0) < dcfg["min_dollar_volume"]:
            reject("dollar volume")
        elif (v.get("history") or 0) < dcfg["min_history_days"]:
            reject("too new")
        elif r["breadth"] < dcfg["min_breadth"]:
            reject("too few sources")
        elif max(r["volume_z"] or 0, r["news_z"] or 0) < elevated:
            reject("no volume or news surge")
        elif (v.get("ret_5d") or 0) > dcfg["max_run_5d_pct"]:
            reject("already ran up")
        elif r["acceleration"] is not None and r["acceleration"] < 0:
            reject("fading")
        else:
            reasons = [f"{name} {r[field]:+.1f}σ" for name, field in Z_FIELDS.items() if (r[field] or 0) >= elevated]
            picked.append({"symbol": r["symbol"], "score": round(sum(max(r[f] or 0, 0) for f in Z_FIELDS.values()), 3),
                           "breadth": r["breadth"], "acceleration": r["acceleration"], "close": v.get("close"),
                           "dollar_volume": v.get("dollar_volume"), "ret_1d": v.get("ret_1d"), "ret_5d": v.get("ret_5d"),
                           "rvol": v.get("rvol"), "mentions": r["mentions"], "reasons": reasons})
    picked.sort(key=lambda p: (-p["score"], p["symbol"]))
    record = {"date": today.isoformat(), "session": session.isoformat(), "collect_only": not dcfg["enabled"],
              "criteria": dcfg | {"elevated_z": elevated}, "names": picked[:dcfg["max_names"]], "rejected": rejected}
    paths.discovery_dir().mkdir(parents=True, exist_ok=True)
    f = paths.discovery_dir() / f"{today.isoformat()}.json"
    f.write_text(json.dumps(record, indent=1), encoding="utf-8")
    return record


def latest_discovery(today=None):
    """Symbols on the newest discovery list dated today or earlier (news.py fetches their news too)."""
    folder = paths.discovery_dir()
    files = sorted(f for f in folder.glob("*.json") if not today or f.stem <= today.isoformat()) if folder.is_dir() else []
    if not files:
        return []
    try:
        return [n["symbol"] for n in json.loads(files[-1].read_text(encoding="utf-8"))["names"]]
    except (OSError, ValueError, KeyError):
        return []


# ---- calibration: does attention predict the next sessions?

def forward_returns(days=CALIBRATION_DAYS, today=None):
    """{(session, symbol): {h: % return}}: from the next session's open to the close h sessions later, from
    the volume archive's opens and closes."""
    today = today or datetime.now(ET).date()
    sessions = [(d, f) for d, _, f in raw_files("volume") if d >= today - timedelta(days=days + 15)]
    prices = {}
    for d, f in sessions:
        rec = _read(f)
        if rec:
            prices[d] = {s: (m["open"], m["close"]) for s, m in rec["data"].items()}
    order = sorted(prices)
    out = {}
    for i, d in enumerate(order):
        if i + 1 >= len(order):
            break
        entry = prices[order[i + 1]]
        for s, (o, _) in entry.items():
            if not o:
                continue
            got = {}
            for h in HORIZONS:
                if i + h < len(order) and s in prices[order[i + h]]:
                    got[h] = round((prices[order[i + h]][s][1] / o - 1) * 100, 4)
            if got:
                out[(d.isoformat(), s)] = got
    return out


def _summary(values):
    if not values:
        return {"n": 0}
    return {"n": len(values), "mean_pct": round(statistics.fmean(values), 4),
            "mean_abs_pct": round(statistics.fmean(abs(x) for x in values), 4),
            "up_rate": round(sum(x > 0 for x in values) / len(values), 3)}


def calibration(days=CALIBRATION_DAYS, today=None):
    """Forward returns grouped by breadth and by each source's z, split by whether the day's move was up or
    down (to tell continuation from reversal), plus how past discovery lists did. For lifetime.json."""
    today = today or datetime.now(ET).date()
    fwd = forward_returns(days, today)
    rows = db.read_attention(since=(today - timedelta(days=days)).isoformat())

    def bucket_z(z):
        return None if z is None else "below 1" if z < 1 else "1 to 2" if z < 2 else "2 to 3" if z < 3 else "3 and up"

    groups = {}
    for r in rows:
        f = fwd.get((r["date"], r["symbol"]))
        if not f:
            continue
        keys = [("breadth", str(min(r["breadth"], 3)) + ("+" if r["breadth"] >= 3 else ""))]
        keys += [(field, bucket_z(r[field])) for field in Z_FIELDS.values() if r[field] is not None]
        direction = "after an up day" if (r["ret_1d"] or 0) > 0 else "after a down day"
        for k, b in keys:
            for split in ("all", direction):
                g = groups.setdefault(k, {}).setdefault(b, {}).setdefault(split, {h: [] for h in HORIZONS})
                for h, v in f.items():
                    g[h].append(v)
    out = {"days": days, "rows": len(rows), "by": {}}
    for k, buckets in groups.items():
        out["by"][k] = {b: {split: {f"{h}d": _summary(vs) for h, vs in hs.items()} for split, hs in splits.items()}
                        for b, splits in sorted(buckets.items())}
    lists = []
    folder = paths.discovery_dir()
    for f in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rets = [fwd.get((rec["session"], n["symbol"]), {}) for n in rec["names"]]
        lists.append({"date": rec["date"], "names": len(rec["names"]),
                      **{f"{h}d": _summary([r[h] for r in rets if h in r]) for h in HORIZONS}})
    out["discovery"] = lists[-60:]
    return out


# ---- jobs

def load_denylist():
    try:
        return set(json.loads((paths.ROOT / "defaults" / "attention_denylist.json").read_text())["symbols"])
    except (OSError, ValueError, KeyError):
        return set()


def run_features(cfg, session, now):
    import config
    import universe

    core = set(universe.load(config.load_watchlist()))
    rows = features(session, cfg, core, now, load_denylist())
    db.note_attention(rows)
    return rows


def morning(cfg, now):
    """Reddit and Wikipedia, then the last session's features and today's discovery list. Each step that
    fails is logged; the rest still run. Returns (messages, failures)."""
    import config
    import universe

    messages, failures = [], 0
    try:
        messages.append(collect_apewisdom(now))
    except Exception:
        LOG.warning("ApeWisdom fetch failed", exc_info=True)
        failures += 1
    session = _session_before(now.date() + timedelta(days=1))
    core = set(universe.load(config.load_watchlist()))
    volume = read_raw("volume", session) if session else None
    if volume and (now.date() - session).days > 4:
        messages.append(f"the latest volume session is {session}: too old, features and discovery skipped")
        volume = None
    done = [r for r in db.read_attention(since=session.isoformat()) if r["date"] == session.isoformat()] if volume else []
    if done:  # a session's features are computed once, the morning after it; weekend mornings keep Friday's
        messages.append(f"features of {session} already computed ({len(done)} symbols)")
        if now.weekday() < 5:
            rec = discover(done, session, cfg, now.date())
            messages.append(f"discovery: {len(rec['names'])} names ({', '.join(n['symbol'] for n in rec['names'][:8])})")
        return messages, failures
    if volume:
        reddit_now, _ = reddit_series(now)
        wanted = core | {s for s, m in volume.items() if (m.get("volume_z") or 0) >= KEEP_Z} \
            | (set(reddit_now or {}) & set(volume))
        try:
            messages.append(collect_wikipedia(wanted, cfg, now))
        except Exception:
            LOG.warning("Wikipedia fetch failed", exc_info=True)
            failures += 1
        rows = run_features(cfg, session, now)
        messages.append(f"features: {len(rows)} symbols for {session}")
        if now.weekday() < 5:
            rec = discover(rows, session, cfg, now.date())
            messages.append(f"discovery: {len(rec['names'])} names ({', '.join(n['symbol'] for n in rec['names'][:8])})")
    else:
        messages.append("no volume session archived yet: features and discovery skipped")
    return messages, failures


def main(argv=None):
    import config
    from logger import notify, setup_logging

    ap = argparse.ArgumentParser(description="attention signals and the discovery list")
    ap.add_argument("command", choices=["collect", "morning", "features", "discover", "show"])
    ap.add_argument("args", nargs="*")
    args = ap.parse_args(argv)
    config.init_data()
    log = setup_logging("attention")
    cfg = config.load_config()
    now = datetime.now(ET)
    try:
        if args.command == "collect":
            for source in args.args or ["apewisdom"]:
                if source == "volume":
                    log.info(collect_volume(cfg, now))
                elif source == "apewisdom":
                    log.info(collect_apewisdom(now))
                else:
                    print(f"unknown source {source}; use volume or apewisdom")
                    return 2
        elif args.command == "morning":
            messages, failures = morning(cfg, now)
            for m in messages:
                log.info(m)
            if failures:
                notify(f"attention: {failures} source(s) failed this morning; see the log")
        elif args.command == "features":
            session = date.fromisoformat(args.args[0]) if args.args else _session_before(now.date() + timedelta(days=1))
            log.info("features: %d symbols for %s", len(run_features(cfg, session, now)), session)
        elif args.command == "discover":
            session = _session_before(now.date())
            rows = [r for r in db.read_attention(since=session.isoformat()) if r["date"] == session.isoformat()]
            rec = discover(rows, session, cfg, now.date())
            print(json.dumps(rec["names"], indent=1))
        else:
            rows = db.read_attention()
            if args.args:
                for r in [r for r in rows if r["symbol"] == args.args[0].upper()][-30:]:
                    print(r)
            else:
                last = rows[-1]["date"] if rows else None
                top = sorted((r for r in rows if r["date"] == last), key=lambda r: -r["breadth"])[:25]
                for r in top:
                    print(r)
    except Exception as e:
        log.exception("attention %s failed", args.command)
        notify(f"attention {args.command} failed: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
