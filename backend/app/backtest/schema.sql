-- Twiq backtest DuckDB schema

CREATE TABLE IF NOT EXISTS underlying_bars (
    timestamp_ist TIMESTAMP NOT NULL,
    symbol VARCHAR NOT NULL,
    timeframe VARCHAR NOT NULL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume DOUBLE DEFAULT 0,
    source VARCHAR,
    import_batch_id VARCHAR,
    PRIMARY KEY (timestamp_ist, symbol, timeframe)
);

CREATE TABLE IF NOT EXISTS option_bars (
    timestamp_ist TIMESTAMP NOT NULL,
    underlying VARCHAR NOT NULL,
    expiry_date DATE NOT NULL,
    strike INTEGER NOT NULL,
    option_side VARCHAR NOT NULL,
    relative_strike INTEGER,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    ltp DOUBLE,
    volume DOUBLE DEFAULT 0,
    open_interest BIGINT DEFAULT 0,
    implied_volatility DOUBLE,
    bid DOUBLE,
    ask DOUBLE,
    delta DOUBLE,
    gamma DOUBLE,
    source VARCHAR,
    import_batch_id VARCHAR,
    PRIMARY KEY (timestamp_ist, expiry_date, strike, option_side)
);

CREATE TABLE IF NOT EXISTS vix_bars (
    timestamp_ist TIMESTAMP NOT NULL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    source VARCHAR,
    import_batch_id VARCHAR,
    PRIMARY KEY (timestamp_ist)
);

CREATE TABLE IF NOT EXISTS lot_size_schedule (
    effective_from DATE PRIMARY KEY,
    lot_size INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS import_batches (
    id VARCHAR PRIMARY KEY,
    dataset_type VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL,
    row_count INTEGER DEFAULT 0,
    checksum VARCHAR,
    mapping_profile JSON,
    status VARCHAR DEFAULT 'completed'
);

CREATE TABLE IF NOT EXISTS day_quality (
    trading_date DATE PRIMARY KEY,
    status VARCHAR NOT NULL,
    warnings JSON,
    import_batch_id VARCHAR
);

CREATE TABLE IF NOT EXISTS backtest_jobs (
    id VARCHAR PRIMARY KEY,
    job_type VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    payload JSON,
    progress JSON,
    error_message VARCHAR,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id VARCHAR PRIMARY KEY,
    strategy_id VARCHAR NOT NULL,
    strategy_version VARCHAR NOT NULL,
    strategy_hash VARCHAR NOT NULL,
    settings JSON NOT NULL,
    date_from DATE NOT NULL,
    date_to DATE NOT NULL,
    replay_mode VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    dataset_version VARCHAR,
    summary JSON,
    created_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS backtest_signals (
    id VARCHAR PRIMARY KEY,
    run_id VARCHAR NOT NULL,
    trading_date DATE,
    payload JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_trades (
    id VARCHAR PRIMARY KEY,
    run_id VARCHAR NOT NULL,
    trading_date DATE,
    payload JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_equity (
    run_id VARCHAR NOT NULL,
    timestamp_ist TIMESTAMP NOT NULL,
    equity DOUBLE NOT NULL,
    drawdown DOUBLE NOT NULL,
    PRIMARY KEY (run_id, timestamp_ist)
);

CREATE INDEX IF NOT EXISTS idx_option_bars_date ON option_bars (expiry_date, timestamp_ist);
CREATE INDEX IF NOT EXISTS idx_underlying_date ON underlying_bars (symbol, timestamp_ist);

CREATE TABLE IF NOT EXISTS mapping_profiles (
    id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    dataset_type VARCHAR NOT NULL,
    mapping JSON NOT NULL,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS download_manifests (
    id VARCHAR PRIMARY KEY,
    dataset_key VARCHAR NOT NULL,
    checksum VARCHAR,
    status VARCHAR NOT NULL,
    metadata JSON,
    created_at TIMESTAMP NOT NULL,
    UNIQUE(dataset_key)
);
