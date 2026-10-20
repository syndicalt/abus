"""Atomic additive migration of skeleton and version one databases."""

import sqlite3

VERSION = 2

TABLES = """
            CREATE TABLE IF NOT EXISTS agents (
                name TEXT PRIMARY KEY, registered_at INTEGER, capabilities TEXT
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, from_agent TEXT, to_agent TEXT, type TEXT,
                corr TEXT, ts INTEGER, payload TEXT, acked INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS traffic (minute INTEGER PRIMARY KEY, count INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS receipts (
                message_id TEXT REFERENCES messages(id) ON DELETE CASCADE,
                consumer TEXT, acked INTEGER NOT NULL DEFAULT 0,
                lease_owner TEXT, lease_until REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(message_id, consumer)
            );
            CREATE TABLE IF NOT EXISTS subscriptions (
                name TEXT REFERENCES agents(name) ON DELETE CASCADE,
                topic TEXT, PRIMARY KEY(name, topic)
            );
            CREATE INDEX IF NOT EXISTS idx_to ON messages(to_agent, ts);
            CREATE INDEX IF NOT EXISTS idx_receipt ON receipts(consumer, acked);
        """

INDEXES = """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_sequence ON messages(seq);
            CREATE INDEX IF NOT EXISTS idx_expiry ON messages(ts);
            CREATE INDEX IF NOT EXISTS idx_destination ON messages(to_agent,ts DESC,seq DESC);
            CREATE INDEX IF NOT EXISTS idx_destination_sequence ON messages(to_agent,seq);
            CREATE INDEX IF NOT EXISTS idx_queue_order ON messages(seq) WHERE state='queued';
            CREATE INDEX IF NOT EXISTS idx_queue_destination ON messages(to_agent,seq) WHERE state='queued';
        """

COUNTERS = """
            CREATE INDEX IF NOT EXISTS idx_inbox ON receipts(consumer,seq);
            CREATE INDEX IF NOT EXISTS idx_inbox_unread ON receipts(consumer,seq) WHERE acked=0;
            DROP INDEX IF EXISTS idx_to;
            DROP INDEX IF EXISTS idx_receipt;
            CREATE TABLE IF NOT EXISTS mailbox_counts (
                consumer TEXT PRIMARY KEY, total INTEGER NOT NULL, unread INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS state_counts (state TEXT PRIMARY KEY, count INTEGER NOT NULL);
            CREATE TRIGGER IF NOT EXISTS receipt_added AFTER INSERT ON receipts BEGIN
                INSERT INTO mailbox_counts VALUES (new.consumer,1,1-new.acked)
                ON CONFLICT(consumer) DO UPDATE SET total=total+1, unread=unread+1-new.acked;
            END;
            CREATE TRIGGER IF NOT EXISTS receipt_removed AFTER DELETE ON receipts BEGIN
                UPDATE mailbox_counts SET total=total-1, unread=unread-(1-old.acked) WHERE consumer=old.consumer;
                DELETE FROM mailbox_counts WHERE consumer=old.consumer AND total=0;
            END;
            CREATE TRIGGER IF NOT EXISTS receipt_acked AFTER UPDATE OF acked ON receipts
            WHEN old.acked!=new.acked BEGIN
                UPDATE mailbox_counts SET unread=unread+old.acked-new.acked WHERE consumer=new.consumer;
            END;
            CREATE TRIGGER IF NOT EXISTS message_added AFTER INSERT ON messages BEGIN
                INSERT INTO state_counts VALUES (new.state,1)
                ON CONFLICT(state) DO UPDATE SET count=count+1;
            END;
            CREATE TRIGGER IF NOT EXISTS message_removed AFTER DELETE ON messages BEGIN
                UPDATE state_counts SET count=count-1 WHERE state=old.state;
            END;
            CREATE TRIGGER IF NOT EXISTS message_state_changed AFTER UPDATE OF state ON messages
            WHEN old.state!=new.state BEGIN
                UPDATE state_counts SET count=count-1 WHERE state=old.state;
                INSERT INTO state_counts VALUES (new.state,1)
                ON CONFLICT(state) DO UPDATE SET count=count+1;
            END;
        """


def migrate(db: sqlite3.Connection):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version == VERSION:
        return False
    if version > VERSION:
        raise RuntimeError(f"Database schema {version} is newer than this ABUS supports")
    additions = {
        "agents": {"project": "TEXT", "session": "TEXT", "seen": "REAL DEFAULT 0", "endpoint": "TEXT"},
        "messages": {
            "state": "TEXT DEFAULT 'accepted'",
            "attempts": "INTEGER DEFAULT 0",
            "next_attempt": "REAL DEFAULT 0",
            "last_error": "TEXT",
            "origin": "TEXT DEFAULT 'local'",
            "seq": "INTEGER",
        },
        "receipts": {"seq": "INTEGER"},
    }
    alters = []
    for table, fields in additions.items():
        existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        for name, definition in fields.items():
            if name not in existing:
                alters.append(f"ALTER TABLE {table} ADD COLUMN {name} {definition};")
    data = """
        UPDATE messages SET state='acked' WHERE acked=1
            AND NOT EXISTS (SELECT 1 FROM meta WHERE key='schema');
        INSERT OR IGNORE INTO receipts(message_id,consumer,acked)
            SELECT id,substr(to_agent,7),acked FROM messages WHERE to_agent LIKE 'agent:%'
            AND instr(to_agent,'/')=0 AND NOT EXISTS (SELECT 1 FROM meta WHERE key='schema');
        INSERT OR IGNORE INTO meta VALUES ('schema','1');
        UPDATE messages SET seq=rowid WHERE seq IS NULL;
        INSERT INTO meta VALUES ('message_sequence',(SELECT coalesce(max(seq),0) FROM messages))
            ON CONFLICT(key) DO UPDATE SET value=max(CAST(value AS INTEGER),CAST(excluded.value AS INTEGER));
        UPDATE receipts SET seq=(SELECT seq FROM messages WHERE id=message_id);
        INSERT OR IGNORE INTO traffic SELECT ts/60,count(*) FROM messages
            WHERE NOT EXISTS (SELECT 1 FROM meta WHERE key='traffic_initialized') GROUP BY ts/60;
        INSERT OR IGNORE INTO meta VALUES ('traffic_initialized','1');
    """
    backfill = """
        DELETE FROM mailbox_counts;
        INSERT INTO mailbox_counts SELECT consumer,count(*),sum(1-acked) FROM receipts GROUP BY consumer;
        DELETE FROM state_counts;
        INSERT INTO state_counts SELECT state,count(*) FROM messages GROUP BY state;
        CREATE TRIGGER IF NOT EXISTS message_sequence_required BEFORE INSERT ON messages
        WHEN new.seq IS NULL BEGIN
            SELECT RAISE(ABORT,'ABUS message sequence is required; use a current daemon');
        END;
        CREATE TRIGGER IF NOT EXISTS receipt_sequence_required BEFORE INSERT ON receipts
        WHEN new.seq IS NULL OR new.seq!=(SELECT seq FROM messages WHERE id=new.message_id) BEGIN
            SELECT RAISE(ABORT,'Receipt sequence must match its envelope');
        END;
        CREATE TRIGGER IF NOT EXISTS message_sequence_immutable BEFORE UPDATE OF seq ON messages
        WHEN new.seq!=old.seq BEGIN
            SELECT RAISE(ABORT,'Message sequence is immutable');
        END;
    """
    try:
        db.executescript(
            "BEGIN IMMEDIATE;" + TABLES + "\n".join(alters) + data + INDEXES + COUNTERS + backfill
        )
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("Database contains orphaned receipts; migration was rolled back")
        db.execute(f"PRAGMA user_version={VERSION}")
        db.commit()
        return True
    except BaseException:
        db.rollback()
        raise
