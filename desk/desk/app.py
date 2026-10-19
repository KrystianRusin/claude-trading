"""The web app: a read-only JSON API over the bots' data, and the built frontend.

Only GET routes exist. Each route belongs to a section of the settings and answers 404 while that
section is off. Answers are cached for cache_seconds, and each client IP is held to
rate_limit_per_minute requests.

    .venv/bin/python -m desk.app      serve on the configured host and port
"""
import re
import threading
import time
from datetime import date
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from . import config, export, models, notebook, research, sources, views

WEB_DIR = config.ROOT / "data" / "web" / "current"  # the build deploy.sh downloaded
Bot = Literal["quant", "sentiment"]
DOWNLOAD_NAME = re.compile(r"^[a-z]+\.(csv|parquet)$")


class TTLCache:
    def __init__(self, seconds):
        self.seconds, self.items, self.lock = seconds, {}, threading.Lock()

    def get(self, key, make):
        now = time.monotonic()
        with self.lock:
            hit = self.items.get(key)
            if hit and now - hit[0] < self.seconds:
                return hit[1]
        value = make()
        with self.lock:
            if len(self.items) > 2000:  # many distinct queries: start over rather than grow
                self.items.clear()
            self.items[key] = (now, value)
        return value


class RateLimit:
    """At most `per_minute` requests per client in any rolling minute (a token bucket per IP)."""

    def __init__(self, per_minute):
        self.rate, self.buckets, self.lock = per_minute / 60.0, {}, threading.Lock()
        self.capacity = per_minute

    def allow(self, client):
        if self.capacity == 0:
            return True
        now = time.monotonic()
        with self.lock:
            tokens, then = self.buckets.get(client, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - then) * self.rate)
            if len(self.buckets) > 10_000:
                self.buckets.clear()
            if tokens < 1:
                self.buckets[client] = (tokens, now)
                return False
            self.buckets[client] = (tokens - 1, now)
            return True


def client_ip(request):
    """The visitor's address. Tailscale Funnel proxies from localhost and appends the visitor to
    X-Forwarded-For; the header is trusted only from a local proxy."""
    peer = request.client.host if request.client else ""
    forwarded = request.headers.get("x-forwarded-for", "")
    if peer in ("127.0.0.1", "::1") and forwarded:
        return forwarded.split(",")[-1].strip()
    return peer


def create_app(settings=None):
    settings = settings or config.load()
    cache, limit = TTLCache(settings.cache_seconds), RateLimit(settings.rate_limit_per_minute)
    app = FastAPI(title="claude-desk", docs_url=None, redoc_url=None, openapi_url="/api/openapi.json")

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.method not in ("GET", "HEAD"):
            return JSONResponse({"detail": "read-only"}, status_code=405)
        if request.url.path.startswith("/api/") and not limit.allow(client_ip(request)):
            return JSONResponse({"detail": "too many requests, slow down"}, status_code=429,
                                headers={"Retry-After": "10"})
        response = await call_next(request)
        if request.url.path.startswith("/api/") and response.status_code == 200:
            response.headers["Cache-Control"] = f"public, max-age={min(settings.cache_seconds, 30)}"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def get(path, model):
        """A GET route whose answer is checked against (and limited to) `model`."""
        return app.get(path, response_model=model, response_model_exclude_unset=True)

    def section(name):
        if not settings.public(name):
            raise HTTPException(404, "not found")

    def cached(request, make):
        try:
            return cache.get(str(request.url.path) + "?" + str(request.url.query), make)
        except sources.QueryTimeout:
            raise HTTPException(503, "busy, try again shortly")

    @get("/api/meta", models.Meta)
    def meta(request: Request):
        return cached(request, lambda: views.meta(settings))

    @get("/api/equity", models.Equity)
    def equity(request: Request):
        section("equity")
        return cached(request, lambda: views.equity(settings))

    @get("/api/benchmark", models.Benchmark)
    def benchmark(request: Request, start: date | None = None):
        section("prices")
        return cached(request, lambda: views.benchmark(settings, start))

    @get("/api/trades", models.Trades)
    def trades(request: Request, bot: Bot | None = None, symbol: str | None = Query(None, max_length=12),
               strategy: str | None = Query(None, max_length=64), exit_reason: str | None = Query(None, max_length=32),
               side: Literal["long", "short"] | None = None, config_version: str | None = Query(None, max_length=16),
               start: date | None = None, end: date | None = None, limit: int = Query(100, ge=1, le=500),
               offset: int = Query(0, ge=0, le=views.MAX_TRADES)):
        section("trades")
        return cached(request, lambda: views.trades(
            settings, bot=bot, symbol=symbol.upper() if symbol else None, strategy=strategy, exit_reason=exit_reason,
            side=side, config_version=config_version, start=start, end=end, limit=limit, offset=offset))

    @get("/api/trades/options", models.TradeOptions)
    def trade_options(request: Request):
        section("trades")
        return cached(request, lambda: views.trade_options(settings))

    @get("/api/symbols", models.Symbols)
    def symbols(request: Request, days: int = Query(30, description="7, 30, or 0 for all"),
                min_trades: int = Query(3, ge=1, le=100)):
        section("symbols")
        if days not in (0, 7, 30):
            raise HTTPException(422, "days must be 7, 30 or 0 (all)")
        return cached(request, lambda: views.symbol_results(settings, days or None, min_trades))

    @get("/api/hot", models.Hot)
    def hot(request: Request):
        section("symbols")
        return cached(request, lambda: views.hot(settings))

    @get("/api/compare", models.Compare)
    def compare(request: Request, start: date | None = None, end: date | None = None):
        section("compare")
        return cached(request, lambda: views.compare(settings, start, end))

    @get("/api/versions", dict[str, models.BotVersions])
    def versions(request: Request):
        section("versions")
        return cached(request, lambda: views.versions(settings, with_content=settings.public("version_content")))

    @get("/api/positions", models.Holdings)
    def positions(request: Request):
        section("positions")
        return cached(request, lambda: views.holdings(settings))

    def symbol_param(symbol):
        symbol = symbol.upper()
        if not research.valid_symbol(symbol):
            raise HTTPException(422, "not a symbol")
        return symbol

    @get("/api/symbols/known", models.KnownSymbols)
    def known_symbols(request: Request):
        section("symbols")
        return cached(request, lambda: research.known_symbols(settings))

    @get("/api/symbol", models.SymbolPage)
    def symbol(request: Request, symbol: str = Query(..., max_length=10)):
        section("symbols")
        sym = symbol_param(symbol)
        return cached(request, lambda: research.symbol(settings, sym))

    @get("/api/bars", models.Bars)
    def bars(request: Request, symbol: str = Query(..., max_length=10), timeframe: Literal["1Day", "1Min"] = "1Day",
             start: date = Query(...), end: date = Query(...)):
        section("prices")
        sym = symbol_param(symbol)
        if end < start or (end - start).days > (1100 if timeframe == "1Day" else 4):
            raise HTTPException(422, "date range too long: 3 years of daily bars or 5 days of minute bars at most")
        return cached(request, lambda: research.bars(settings, sym, timeframe, start, end))

    @get("/api/trade", models.TradeDetail)
    def trade(request: Request, bot: Bot, id: int = Query(..., ge=1)):
        section("trades")
        answer = cached(request, lambda: research.trade_detail(settings, bot, id))
        if answer is None:
            raise HTTPException(404, "not found")
        return answer

    @get("/api/headlines", models.Headlines)
    def headlines(request: Request, symbol: str = Query(..., max_length=10), days: int = Query(30, ge=1, le=90)):
        section("headlines")
        sym = symbol_param(symbol)
        return cached(request, lambda: research.headlines(settings, sym, days))

    @get("/api/research", models.Research)
    def research_view(request: Request):
        section("research")
        if "sentiment" not in settings.bots:
            raise HTTPException(404, "not found")
        return cached(request, lambda: research.research(settings))

    @get("/api/agent", models.Agent)
    def agent(request: Request, bot: Bot):
        section("agent")
        return cached(request, lambda: research.agent_activity(settings, bot))

    @get("/api/replay/days", models.ReplayDays)
    def replay_days(request: Request, bot: Bot):
        section("replay")
        return cached(request, lambda: views.replay_days(settings, bot))

    @get("/api/replay", models.Replay)
    def replay(request: Request, bot: Bot, day: date):
        section("replay")
        return cached(request, lambda: views.replay(settings, bot, day))

    @get("/api/versions/impact", models.Impact)
    def version_impact(request: Request, bot: Bot):
        section("versions")
        return cached(request, lambda: views.version_impact(settings, bot))

    @get("/api/versions/diff", models.Diff)
    def version_diff(request: Request, bot: Bot, id: int = Query(..., ge=1)):
        section("versions")
        section("version_content")
        answer = cached(request, lambda: views.version_diff(settings, bot, id))
        if answer is None:
            raise HTTPException(404, "not found")
        return answer

    @get("/api/disagreements", models.Disagreements)
    def disagreements(request: Request, days: int = Query(30, ge=1, le=3650)):
        section("compare")
        section("scores")
        return cached(request, lambda: views.disagreement_feed(settings, days))

    @get("/api/journal", models.Journal)
    def journal(request: Request, bot: Bot, day: date | None = None):
        section("journal")
        return cached(request, lambda: notebook.journal(settings, bot, day.isoformat() if day else None))

    @get("/api/memory", models.Memory)
    def memory(request: Request, bot: Bot):
        section("memory")
        return cached(request, lambda: notebook.memory(settings, bot))

    @get("/api/downloads", models.Downloads)
    def downloads(request: Request):
        section("downloads")
        m = export.read_manifest() or {"generated_at": None, "files": []}
        return m | {"files": [f for f in m["files"] if settings.public(f["section"])]}

    @app.get("/api/downloads/{name}")
    def download(name: str):
        section("downloads")
        m = export.read_manifest() or {"files": []}
        f = next((f for f in m["files"] if f["name"] == name and settings.public(f["section"])), None)
        path = export.EXPORT_DIR / name
        if f is None or not DOWNLOAD_NAME.match(name) or not path.is_file():
            raise HTTPException(404, "not found")
        media = "text/csv" if name.endswith(".csv") else "application/vnd.apache.parquet"
        return FileResponse(path, media_type=media, filename=f"claude-desk-{name}",
                            headers={"Cache-Control": "public, max-age=300"})

    @get("/api/ops", dict[str, models.BotOps])
    def ops(request: Request):
        section("ops")
        return cached(request, lambda: views.ops(settings, with_errors=settings.public("ops_errors")))

    @app.get("/api/{rest:path}", include_in_schema=False)
    def unknown_api(rest: str):
        raise HTTPException(404, "not found")

    @app.get("/{rest:path}", include_in_schema=False)
    def web(rest: str):
        """The frontend: a file of the build if it exists, else index.html (the app routes in the browser)."""
        root = WEB_DIR.resolve() if WEB_DIR.exists() else None
        if root is None:
            return JSONResponse({"detail": "the web build is not installed; see README"}, status_code=503)
        target = (root / rest).resolve()
        if rest and target.is_file() and root in target.parents:
            immutable = rest.startswith("assets/")  # Vite gives these hashed names
            return FileResponse(target, headers={"Cache-Control": "public, max-age=31536000, immutable"
                                                 if immutable else "no-cache"})
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def main():
    import uvicorn

    settings = config.load()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, proxy_headers=False,
                server_header=False, access_log=False)


if __name__ == "__main__":
    main()
