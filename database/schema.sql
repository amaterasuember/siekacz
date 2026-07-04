CREATE TABLE IF NOT EXISTS project_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    client_name TEXT,
    order_number TEXT,
    material TEXT,
    saved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leftovers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    material TEXT NOT NULL,
    thickness REAL,
    profile TEXT,
    width REAL,
    height REAL,
    length REAL,
    source TEXT,
    status TEXT NOT NULL DEFAULT 'available',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

