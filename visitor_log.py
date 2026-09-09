import sqlite3
import datetime
import os
import config

def get_connection():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes the database schema if not already present."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS visits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                name TEXT NOT NULL,
                confidence REAL,
                trigger_source TEXT NOT NULL,
                photo_path TEXT NOT NULL
            );
        """)
        conn.commit()

def log_visit(name: str, photo_path: str, trigger_source: str = "PIR", confidence: float = None) -> int:
    """Logs a visitor entry to the SQLite database."""
    init_db()
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    relative_path = os.path.relpath(photo_path, config.BASE_DIR) if os.path.isabs(photo_path) else photo_path

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO visits (timestamp, name, confidence, trigger_source, photo_path)
            VALUES (?, ?, ?, ?, ?)
        """, (timestamp, name, confidence, trigger_source, relative_path))
        conn.commit()
        return cursor.lastrowid

def get_recent_visits(limit: int = 25):
    """Retrieves recent visitor logs."""
    init_db()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, timestamp, name, confidence, trigger_source, photo_path
            FROM visits
            ORDER BY id DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]

def print_log_table(limit: int = 25):
    """CLI viewer helper to inspect logs."""
    visits = get_recent_visits(limit)
    if not visits:
        print("No visitor logs found.")
        return

    header = f"{'ID':<4} | {'Timestamp':<19} | {'Trigger':<7} | {'Visitor / Name':<20} | {'Photo Path'}"
    print("-" * len(header))
    print(header)
    print("-" * len(header))

    for v in visits:
        name_display = v['name']
        if v['confidence'] is not None:
            name_display += f" ({v['confidence']:.2f})"
        print(f"{v['id']:<4} | {v['timestamp']:<19} | {v['trigger_source']:<7} | {name_display:<20} | {v['photo_path']}")
    print("-" * len(header))

if __name__ == "__main__":
    init_db()
    print_log_table()
