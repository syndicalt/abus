import sqlite3
import time

import pytest

from abus.store import Store

LEGACY = """
CREATE TABLE agents(name TEXT PRIMARY KEY,registered_at INTEGER,capabilities TEXT);
CREATE TABLE messages(id TEXT PRIMARY KEY,from_agent TEXT,to_agent TEXT,type TEXT,
    corr TEXT,ts INTEGER,payload TEXT,acked INTEGER DEFAULT 0);
"""


def test_migration_preserves_legacy_acknowledgment_and_rebuilds_counters(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript(LEGACY + "INSERT INTO agents VALUES ('worker',1,'[]');")
    connection.executemany(
        "INSERT INTO messages VALUES (?,?,?,'event',NULL,?,'{}',?)",
        [
            ("handled", "cli", "agent:worker", int(time.time()), 1),
            ("waiting", "cli", "agent:worker", int(time.time()), 0),
        ],
    )
    connection.commit()
    connection.close()
    store = Store(path)
    try:
        assert store.counts() == {"acked": 1, "accepted": 1}
        assert store.agents()[0]["unread"] == 1
        assert store.inbox({"name": "worker", "observer": True})["total"] == 1
        retained = store.inbox({"name": "worker", "observer": True, "include_acked": True})
        assert retained["total"] == 2
        assert [message["acknowledged"] for message in retained["messages"]] == [True, False]
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert store.db.execute("PRAGMA synchronous").fetchone()[0] == 2
    finally:
        store.close()


def test_failed_migration_is_atomic_and_preserves_original_database(tmp_path):
    path = tmp_path / "broken.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        LEGACY
        + """
        CREATE TABLE receipts(message_id TEXT REFERENCES messages(id),consumer TEXT,acked INTEGER,
            lease_owner TEXT,lease_until REAL,PRIMARY KEY(message_id,consumer));
        INSERT INTO receipts VALUES ('missing','worker',0,NULL,0);
    """
    )
    before = connection.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    connection.close()
    with pytest.raises(RuntimeError, match="orphaned receipts"):
        Store(path)
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall() == before
    assert connection.execute("SELECT message_id FROM receipts").fetchone()[0] == "missing"
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    connection.close()


def test_counters_follow_claim_ack_broadcast_retention_and_expiry(tmp_path):
    store = Store(tmp_path / "bus.db")
    try:
        for name in ("one", "two"):
            store.register({"name": name})
            store.subscribe({"name": name, "topic": "broadcast:events"})
        mid = store.send({"to": "broadcast:events", "type": "event"})["id"]
        store.inbox({"name": "one", "owner": "receiver"}, claim=True)
        receipt = {"id": mid, "name": "one", "owner": "receiver"}
        store.ack(receipt)
        assert store.ack(receipt) == {"ok": True}
        assert [agent["unread"] for agent in store.agents()] == [0, 1]
        with store.db:
            store.db.execute("UPDATE messages SET ts=?", (int(time.time()) - 8 * 86400,))
        assert [agent["unread"] for agent in store.agents()] == [0, 0]
        assert store.counts() == {}
        assert store.inbox({"name": "two", "observer": True})["total"] == 0
        store.sweep()
        assert store.counts() == {}
        assert all(
            row[0] == row[1] == 0 for row in store.db.execute("SELECT total,unread FROM mailbox_counts")
        )
    finally:
        store.close()


def test_upgrade_never_resets_a_sequence_when_retained_messages_are_empty(tmp_path):
    path = tmp_path / "bus.db"
    store = Store(path)
    store.set_meta("message_sequence", "1000")
    store.db.execute("PRAGMA user_version=1")
    store.close()
    store = Store(path)
    try:
        store.register({"name": "worker"})
        store.send({"to": "agent:worker", "type": "event"})
        assert store.inbox({"name": "worker"})["next"] == 1001
    finally:
        store.close()
