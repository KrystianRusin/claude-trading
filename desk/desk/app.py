"""The web app: a read-only JSON API over the bots' data, and the built frontend.

Only GET routes exist. Each route belongs to a section of the settings and answers 404 while that
section is off. Answers are cached for cache_seconds, and each client IP is held to
rate_limit_per_minute requests.

    .venv/bin/python -m desk.app      serve on the configured host and port
"""
import threading
import time
from datetime import date
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from . import config, sources, views

WEB_DIR = config.ROOT / "data" / "web" / "current"  # the build deploy.sh downloaded
Bot = Literal["quant", "sentiment"]


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

    def section(name):
        if not settings.public(name):
            raise HTTPException(404, "not found")

    def cached(request, make):
        try:
            return cache.get(str(request.url.path) + "?" + str(request.url.query), make)
        except sources.QueryTimeout:
            raise HTTPException(503, "busy, try again shortly")

    @app.get("/api/meta")
    def meta(request: Request):
        return cached(request, lambda: views.meta(settings))

    @app.get("/api/equity")
    def equity(request: Request):
        section("equity")
        return cached(request, lambda: views.equity(settings))

    @app.get("/api/benchmark")
    def benchmark(request: Request, start: date | None = None):
        section("prices")
        return cached(request, lambda: views.benchmark(settings, start))

    @app.get("/api/trades")
    def trades(request: Request, bot: Bot | None = None, symbol: str | None = Query(None, max_length=12),
               strategy: str | None = Query(None, max_length=64), exit_reason: str | None = Query(None, max_length=32),
               side: Literal["long", "short"] | None = None, config_version: str | None = Query(None, max_length=16),
               start: date | None = None, end: date | None = None, limit: int = Query(100, ge=1, le=500),
               offset: int = Query(0, ge=0, le=views.MAX_TRADES)):
        section("trades")
        return cached(request, lambda: views.trades(
            settings, bot=bot, symbol=symbol.upper() if symbol else None, strategy=strategy, exit_reason=exit_reason,
            side=side, config_version=config_version, start=start, end=end, limit=limit, offset=offset))

    @app.get("/api/trades/options")
    def trade_options(request: Request):
        section("trades")
        return cached(request, lambda: views.trade_options(settings))

    @app.get("/api/symbols")
    def symbols(request: Request, days: int = Query(30, description="7, 30, or 0 for all"),
                min_trades: int = Query(3, ge=1, le=100)):
        section("symbols")
        if days not in (0, 7, 30):
            raise HTTPException(422, "days must be 7, 30 or 0 (all)")
        return cached(request, lambda: views.symbol_results(settings, days or None, min_trades))

    @app.get("/api/hot")
    def hot(request: Request):
        section("symbols")
        return cached(request, lambda: views.hot(settings))

    @app.get("/api/compare")
    def compare(request: Request, start: date | None = None, end: date | None = None):
        section("compare")
        return cached(request, lambda: views.compare(settings, start, end))

    @app.get("/api/versions")
    def versions(request: Request):
        section("versions")
        return cached(request, lambda: views.versions(settings, with_content=settings.public("version_content")))

    @app.get("/api/positions")
    def positions(request: Request):
        section("positions")
        return cached(request, lambda: views.positions(settings))

    @app.get("/api/ops")
    def ops(request: Request):
        section("ops")
        return cached(request, lambda: views.ops(settings, with_errors=settings.public("ops_errors")))

    @app.get("/api/{rest:path}")
    def unknown_api(rest: str):
        raise HTTPException(404, "not found")

    @app.get("/{rest:path}")
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
