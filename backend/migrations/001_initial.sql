-- Twiq forward-test history schema (Supabase Postgres / compatible SQLite via SQLAlchemy)

CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    session_date DATE NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    time TEXT NOT NULL,
    signal TEXT NOT NULL,
    side TEXT,
    ema_gap DOUBLE PRECISION NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    strike INTEGER NOT NULL,
    option_ltp DOUBLE PRECISION,
    market_regime TEXT,
    nifty_spot DOUBLE PRECISION,
    pcr DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_signals_session_date ON signals (session_date DESC);
CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON signals (timestamp DESC);

CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    session_date DATE NOT NULL,
    entry_time TEXT NOT NULL,
    exit_time TEXT,
    contract TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    lots INTEGER,
    entry_price DOUBLE PRECISION NOT NULL,
    exit_price DOUBLE PRECISION,
    result TEXT NOT NULL,
    pnl DOUBLE PRECISION NOT NULL,
    target_price DOUBLE PRECISION,
    stop_price DOUBLE PRECISION,
    trail_stop_price DOUBLE PRECISION,
    regime_at_entry TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trades_session_date ON trades (session_date DESC);
CREATE INDEX IF NOT EXISTS idx_trades_entry_time ON trades (entry_time);
