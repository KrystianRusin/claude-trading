-- Attention signals (ticket 009), this repo only. One row per symbol and session: how unusual each source's
-- attention was against the symbol's own baseline (z, in log space), and the raw numbers behind it.
-- `date` is the session the data describes; features are computed the next morning, before scoring.
CREATE TABLE IF NOT EXISTS attention (
    date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    in_universe INTEGER NOT NULL,   -- 0 or 1: in the core universe or the watchlist that day
    volume_z REAL,                  -- today's volume against the previous 20 sessions
    rvol REAL,                      -- today's volume / the previous 20 sessions' average
    news_z REAL,                    -- articles that day against the previous 20 days
    articles INTEGER,
    wiki_z REAL,                    -- Wikipedia page views against the previous 30 days
    page_views INTEGER,
    reddit_z REAL,                  -- Reddit mentions (ApeWisdom) against the previous 20 snapshots
    mentions INTEGER,
    breadth INTEGER NOT NULL,       -- sources with z >= the config's elevated level
    acceleration REAL,              -- the highest z today minus yesterday's
    close REAL,                     -- the session's close, for forward returns
    ret_1d REAL,                    -- the session's move, in %
    PRIMARY KEY (date, symbol)
);
CREATE INDEX IF NOT EXISTS attention_symbol ON attention (symbol, date);
