"""The shape of every API answer. The routes declare these as response models, so:

- the OpenAPI schema (/api/openapi.json) describes each answer, and web/src/schema.d.ts is generated from it
  (scripts/gen_types.sh; a test fails when it is out of date);
- a field a view returns but no model names is dropped, a second guard behind the views' own field lists.

Optional fields with no default that a view leaves out stay out of the answer (the routes use
response_model_exclude_unset), so "missing" and null keep their meaning.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Num = float | None
BotName = Literal["quant", "sentiment"]


class Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ---- meta, equity, benchmark

class MetaBot(Model):
    name: BotName
    label: str
    books: dict[str, Literal["ok", "missing", "unreadable"]]


class Meta(Model):
    bots: list[MetaBot]
    books: list[str]
    sections: list[str]


class DailyPoint(Model):
    date: str
    equity: Num
    pnl: Num
    trades: int | None
    halted: bool


class IntradayPoint(Model):
    ts: str
    equity: Num


class Intraday(Model):
    date: str
    points: list[IntradayPoint]


class EquitySummary(Model):
    as_of: str
    equity: Num
    start_equity: Num
    total_return_pct: Num
    today_pnl: Num
    today_return_pct: Num
    max_drawdown_pct: float
    days: int
    halted_today: bool


class EquityBook(Model):
    bot: BotName
    book: str
    daily: list[DailyPoint]
    intraday: Intraday
    summary: EquitySummary


class Equity(Model):
    books: list[EquityBook]


class BenchmarkPoint(Model):
    date: str
    close: Num


class Benchmark(Model):
    symbol: str
    daily: list[BenchmarkPoint]


# ---- trades

class Trade(Model):
    bot: BotName
    book: str
    id: int
    date: str
    symbol: str
    side: str
    qty: Num
    entry_date: str | None
    entry_time: str | None
    entry_price: Num
    exit_time: str | None
    exit_price: Num
    exit_reason: str | None
    stop_price: Num
    pnl_usd: Num
    pnl_r: Num
    pnl_pct: Num
    hold_days: Num
    strategy: str | None
    strategy_version: str | None
    config_version: str | None
    score: Num = None
    confidence: Num = None
    pair: str | None = None
    entry_minute_after_open: Num = None
    range_pct: Num = None


class HistogramBin(Model):
    from_: float = Field(alias="from")
    to: float
    count: int
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class TradeStats(Model):
    trades: int
    wins: int
    losses: int
    win_rate: Num
    total_pnl: float
    expectancy_usd: Num
    avg_r: Num
    r_histogram: list[HistogramBin]


class Trades(Model):
    total: int
    offset: int
    trades: list[Trade]
    stats: TradeStats


class TradeOptions(Model):
    symbol: list[str]
    strategy: list[str]
    exit_reason: list[str]
    side: list[str]
    config_version: list[str]
    bots: list[str]


class TradeWindow(Model):
    timeframe: Literal["1Day", "1Min"]
    start: str
    end: str


class TradeDetail(Model):
    trade: Trade
    target_price: Num
    window: TradeWindow


# ---- symbols

class SymbolRow(Model):
    bot: BotName
    symbol: str
    trades: int
    pnl: float
    win_rate: Num
    avg_r: Num


class Symbols(Model):
    days: int | None
    min_trades: int
    symbols: list[SymbolRow]


class HotQuant(Model):
    symbol: str
    signals: int
    entries: int
    side: str | None
    new_on_watchlist: bool


class HotSentiment(Model):
    symbol: str
    heat: float
    articles: int
    desk_ideas: int
    direction: str


class Disagree(Model):
    symbol: str
    quant: str
    sentiment: str


class Hot(Model):
    quant: list[HotQuant]
    sentiment: list[HotSentiment] | None = None
    disagree: list[Disagree] | None = None


class KnownSymbols(Model):
    symbols: list[str]


class SymbolStats(Model):
    trades: int
    wins: int
    losses: int
    win_rate: Num
    total_pnl: float
    avg_r: Num
    first: str
    last: str


class Signal(Model):
    date: str
    side: str | None
    entered: bool


class ScorePoint(Model):
    date: str
    score: float
    confidence: float
    articles: int


class SymbolIdea(Model):
    date: str
    kind: str
    side: str
    conviction: Num
    legs: int


class AttentionPoint(Model):
    date: str
    volume_z: Num
    news_z: Num
    wiki_z: Num
    reddit_z: Num
    mentions: int | None
    breadth: int


class SymbolPage(Model):
    symbol: str
    bots: dict[str, SymbolStats]
    trades: list[Trade]
    signals: list[Signal]
    scores: list[ScorePoint]
    ideas: list[SymbolIdea]
    attention: list[AttentionPoint]


class Bar(Model):
    ts: str
    open: Num
    high: Num
    low: Num
    close: Num
    volume: Num


class Bars(Model):
    symbol: str
    timeframe: Literal["1Day", "1Min"]
    bars: list[Bar]


class Headline(Model):
    published_at: str
    headline: str
    source: str
    url: str | None


class Headlines(Model):
    symbol: str
    headlines: list[Headline]


# ---- compare

class ReturnStats(Model):
    days: int
    total_return_pct: Num = None
    mean_daily_pct: Num = None
    volatility_annual_pct: Num = None
    sharpe: Num = None
    max_drawdown_pct: Num = None
    worst_day_pct: Num = None
    best_day_pct: Num = None


class CurvePoint(Model):
    date: str
    return_pct: float


class Difference(Model):
    days: int
    first: str | None
    last: str | None
    mean_diff_pct: Num = None
    low_pct: Num = None
    high_pct: Num = None
    correlation: Num = None
    verdict: str


class Compare(Model):
    bots: dict[str, ReturnStats]
    curves: dict[str, list[CurvePoint]]
    difference: Difference | None = None


class DisagreementItem(Model):
    date: str | None
    symbol: str
    quant: str
    quant_entered: bool
    sentiment: str
    score: float
    confidence: float
    quant_r: Num
    sentiment_r: Num


class Tally(Model):
    traded: int
    won: int
    avg_r: Num


class Disagreements(Model):
    days: int
    items: list[DisagreementItem]
    quant: Tally
    sentiment: Tally


# ---- versions

class VersionChange(Model):
    kind: str
    name: str
    version: str | None
    effective_from: str
    reason: str | None
    removed: bool
    content: str | None = None


class VersionResult(Model):
    strategy: str | None
    strategy_version: str | None
    config_version: str | None
    first: str
    last: str
    trades: int
    pnl: float
    win_rate: Num
    avg_r: Num


class BotVersions(Model):
    changes: list[VersionChange]
    results: list[VersionResult]


class WindowStats(Model):
    trades: int
    win_rate: Num
    avg_r: Num
    pnl: float
    days: int
    return_pct: Num


class Change(Model):
    id: int
    kind: str
    name: str
    version: str | None
    effective_from: str
    reason: str | None
    removed: bool
    first: bool
    current: bool
    verdict: str
    after: WindowStats
    before: WindowStats | None
    has_diff: bool | None = None


class Impact(Model):
    bot: BotName
    min_trades: int
    changes: list[Change]


class Diff(Model):
    id: int
    kind: str
    name: str
    version: str | None
    previous_version: str | None
    truncated: bool
    diff: str


# ---- holdings and replay

class Holding(Model):
    symbol: str
    side: Literal["long", "short"]
    qty: float
    avg_entry_price: Num
    price: Num
    market_value: Num
    unrealized_pnl: Num
    unrealized_pct: Num
    weight_pct: Num
    today_pnl: Num
    entry: str | None
    days_held: int | None
    stop_price: Num
    target_price: Num
    stop_distance_pct: Num
    strategy: str | None
    pair: str | None
    score: Num = None
    confidence: Num = None


class HoldingTotals(Model):
    positions: int
    equity: Num
    long_value: Num
    short_value: Num
    unrealized_pnl: Num
    today_pnl: Num
    gross_exposure_pct: Num


class HoldingsPoint(Model):
    ts: str
    unrealized_pnl: float
    positions: int | None


class HoldingsBook(Model):
    bot: BotName
    book: str
    as_of: str
    totals: HoldingTotals
    positions: list[Holding]
    history: list[HoldingsPoint]


class Holdings(Model):
    books: list[HoldingsBook]


class ReplayDays(Model):
    bot: BotName
    days: list[str]


class ReplayEquity(Model):
    ts: str
    equity: Num
    positions: int | None


class FramePosition(Model):
    symbol: str
    qty: float
    price: Num
    market_value: Num
    unrealized_pnl: Num


class Frame(Model):
    ts: str
    positions: list[FramePosition]


class Fill(Model):
    ts: str
    symbol: str
    side: str | None
    qty: Num
    price: Num
    order_type: str | None
    leg: bool


class ReplayTrade(Model):
    symbol: str
    side: str
    qty: Num
    entry_time: str | None
    exit_time: str | None
    entry_price: Num
    exit_price: Num
    exit_reason: str | None
    pnl_usd: Num
    pnl_r: Num


class Replay(Model):
    bot: BotName
    date: str
    over: bool
    positions_shown: bool
    equity_start: Num
    equity_end: Num
    equity: list[ReplayEquity]
    frames: list[Frame]
    fills: list[Fill]
    trades: list[ReplayTrade]


# ---- notebook, research, agent

MemoryStatus = Literal["hypothesis", "supported", "confirmed", "retired"]


class StatusChange(Model):
    date: str
    status: MemoryStatus


class MemoryEntry(Model):
    id: str
    title: str | None
    status: MemoryStatus
    scope: str | None
    since: str | None
    last_reviewed: str | None
    query: str | None
    evidence: str | None
    implication: str | None
    retired_reason: str | None
    history: list[StatusChange]


class MemoryCount(Model):
    date: str
    hypothesis: int
    supported: int
    confirmed: int
    retired: int


class Memory(Model):
    bot: BotName
    as_of: str | None
    entries: list[MemoryEntry]
    counts: list[MemoryCount]


class JournalEntry(Model):
    date: str
    text: str | None


class Journal(Model):
    bot: BotName
    dates: list[str]
    entry: JournalEntry | None


class Calibration(Model):
    score: int
    n: int
    mean_fwd_return_pct: Num
    hit_rate: Num


class Group(Model):
    n: Num
    win_rate: Num
    pnl_usd: Num
    expectancy_r: Num


class NewsDay(Model):
    date: str
    articles: int


class Leaning(Model):
    date: str
    bullish: int
    bearish: int
    neutral: int


class Leg(Model):
    symbol: str
    side: str


class Idea(Model):
    date: str
    kind: str
    legs: list[Leg]
    conviction: Num
    confidence: Num
    outcome: Literal["closed", "open", "not traded"]
    pnl_r: Num


class IdeaKind(Model):
    kind: str
    ideas: int
    traded: int
    closed: int
    avg_r: Num


class BreadthReturns(Model):
    breadth: str
    n: int
    d1: Num
    d3: Num
    d5: Num
    up_rate_1d: Num


class DiscoveredName(Model):
    symbol: str
    reasons: list[str]
    close: Num
    ret_1d: Num
    rvol: Num
    breadth: int


class DiscoveryOutcome(Model):
    date: str
    names: int
    d1: Num
    d3: Num
    d5: Num


class Attention(Model):
    breadth: list[BreadthReturns]
    discovery_date: str | None
    discovery: list[DiscoveredName]
    collect_only: bool
    discovery_history: list[DiscoveryOutcome]


class Research(Model):
    attention: Attention
    as_of: str | None
    calibration: list[Calibration]
    scored_outcomes: int
    by_score: dict[str, Group]
    by_confidence: dict[str, Group]
    news: list[NewsDay]
    leaning: list[Leaning]
    ideas: list[Idea]
    idea_kinds: list[IdeaKind]


class Review(Model):
    job: str
    status: str
    started_at: str
    seconds: int | None


class Guard(Model):
    passed: bool
    problems: int


class AgentChange(Model):
    kind: str
    name: str
    version: str | None
    reason: str | None
    removed: bool


class MemoryMove(Model):
    id: str
    title: str | None
    status: MemoryStatus
    previous: MemoryStatus | None


class AgentDay(Model):
    date: str
    reviews: list[Review]
    guard: Guard | None
    changes: list[AgentChange]
    memory: list[MemoryMove]
    journal: bool
    changelog: str | None


class Agent(Model):
    bot: BotName
    days: list[AgentDay]


# ---- ops and downloads

class Run(Model):
    job: str
    started_at: str
    ended_at: str | None
    status: str
    exit_code: int | None
    seconds: int | None
    git_sha: str | None
    error: str | None = None


class Job(Model):
    job: str
    runs: int
    failures_7d: int
    last_ok: str | None
    last_status: str
    last_started: str


class BotOps(Model):
    runs: list[Run]
    jobs: list[Job]


class DownloadFile(Model):
    name: str
    section: str
    format: str
    rows: int
    bytes: int
    columns: list[str]
    about: str


class Downloads(Model):
    generated_at: str | None
    files: list[DownloadFile]
