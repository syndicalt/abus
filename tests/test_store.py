import json
import sqlite3
import time

import pytest

from abus.config import BusError
from abus.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "abus.db")
    s.register({"name": "alpha", "capabilities": ["review"], "project": "/project"})
    s.register({"name": "beta"})
    yield s
    s.close()


def send(s, **overrides):
    return s.send({"to": "agent:alpha", "type": "prompt", "payload": {"text": "Hello"}, **overrides})


def test_registration_is_explicit_and_capabilities_are_durable(store):
    with pytest.raises(BusError, match="not registered"):
        send(store, to="agent:missing")
    assert store.agents()[0]["capabilities"] == ["review"]
    assert store.agents()[0]["project"] == "/project"
    assert store.agents()[0]["online"]
    store.db.execute("UPDATE agents SET seen=0")
    assert not store.agents()[0]["online"]
    assert store.registered("alpha")


def test_envelope_round_trip_and_independent_ack(store):
    mid = send(store, **{"from": "agent:beta", "corr": "task-1"})["id"]
    m = store.inbox({"name": "alpha"})["messages"][0]
    assert (m["v"], m["from"], m["to"], m["corr"], m["payload"]) == (
        1,
        "agent:beta",
        "agent:alpha",
        "task-1",
        {"text": "Hello"},
    )
    assert store.inbox({"name": "beta"})["messages"] == []
    with pytest.raises(BusError):
        store.ack({"id": mid, "name": "beta"})
    store.ack({"id": mid})
    assert store.inbox({"name": "alpha"})["messages"] == []
    assert store.history({})["messages"][0]["state"] == "acked"


def test_unregister_preserves_inbox_and_rejects_new_messages(store):
    mid = send(store)["id"]
    store.unregister({"name": "alpha"})
    with pytest.raises(BusError):
        send(store)
    store.register({"name": "alpha"})
    assert store.inbox({"name": "alpha"})["messages"][0]["id"] == mid


def test_retries_are_idempotent_but_conflicting_ids_rejected(store):
    first = send(store, id="stable-id")
    second = send(store, id="stable-id")
    assert first["id"] == second["id"]
    assert second["duplicate"]
    assert len(store.inbox({"name": "alpha"})["messages"]) == 1
    with pytest.raises(BusError, match="different envelope"):
        send(store, id="stable-id", payload={"text": "changed"})


def test_broadcast_ack_is_per_subscriber_and_no_retroactive_delivery(store):
    for name in ("alpha", "beta"):
        store.subscribe({"name": name, "topic": "broadcast:events"})
    mid = send(store, to="broadcast:events", type="event")["id"]
    with pytest.raises(BusError, match="name is required"):
        store.ack({"id": mid})
    store.ack({"id": mid, "name": "alpha"})
    assert not store.inbox({"name": "alpha"})["messages"]
    assert store.inbox({"name": "beta"})["messages"][0]["id"] == mid
    store.subscribe({"name": "beta", "topic": "broadcast:events"}, remove=True)
    send(store, to="broadcast:events", type="event")
    assert len(store.inbox({"name": "beta"})["messages"]) == 1


def test_exclusive_claim_release_expiry_and_renewal(store):
    mid = send(store)["id"]
    params = {"name": "alpha", "owner": "receiver-1"}
    assert store.inbox(params, claim=True)["messages"][0]["id"] == mid
    assert store.inbox({"name": "alpha", "owner": "receiver-2"}, claim=True)["messages"] == []
    with pytest.raises(BusError, match="another receiver"):
        store.ack({"id": mid, "name": "alpha", "owner": "receiver-2"})
    store.ack({"id": mid, **params}, renew=True)
    store.ack({"id": mid, **params}, release=True)
    assert store.inbox({"name": "alpha", "owner": "receiver-2"}, claim=True)["messages"]
    store.db.execute("UPDATE receipts SET lease_until=0")
    assert store.inbox(params, claim=True)["messages"]


def test_expired_messages_are_hidden_on_read_and_collected_by_maintenance(store):
    mid = send(store)["id"]
    store.db.execute("UPDATE messages SET ts=? WHERE id=?", (int(time.time()) - 8 * 86400, mid))
    store.db.commit()
    before = store.db.total_changes
    assert store.inbox({"name": "alpha", "observer": True})["messages"] == []
    assert store.history({})["messages"] == []
    assert store.agents()[0]["unread"] == 0
    assert store.db.total_changes == before
    assert store.sweep() == 1
    assert not store.db.execute("SELECT * FROM receipts").fetchall()


def test_retention_keeps_newest_at_equal_timestamps_and_paginates_large_inbox(store):
    now = int(time.time())
    with store.db:
        store.db.executemany(
            """INSERT INTO messages(seq,id,from_agent,to_agent,type,ts,payload)
            VALUES (?,?,'test','agent:alpha','event',?,?)""",
            [(i + 1, f"bulk-{i}", now, json.dumps({"text": "x" * 2000})) for i in range(1005)],
        )
        store.db.executemany(
            "INSERT INTO receipts(message_id,consumer,seq) VALUES (?,'alpha',?)",
            [(f"bulk-{i}", i + 1) for i in range(1005)],
        )
    store.prune()
    messages, after = [], 0
    while True:
        page = store.inbox({"name": "alpha", "after": after})
        messages.extend(page["messages"])
        after = page["next"]
        if not page["more"]:
            break
    assert len(messages) == 1000
    assert messages[0]["id"] == "bulk-5"
    assert messages[-1]["id"] == "bulk-1004"


def test_queue_does_not_overtake_an_earlier_retry(store):
    first = send(store, to="agent:other/alpha")["id"]
    send(store, to="agent:other/alpha")
    assert [r["id"] for r in store.pending()] == [first]
    store.failed(first, "offline")
    assert not store.pending()
    store.db.execute("UPDATE messages SET next_attempt=0")
    store.delivered(first, "forwarded")
    assert len(store.pending()) == 1


@pytest.mark.parametrize(
    "params",
    [
        {"to": "nonsense"},
        {"to": "agent:"},
        {"to": "agent:../../x"},
        {"type": "unsupported"},
        {"payload": []},
        {"payload": {"text": "x" * 70000}},
        {"corr": 1},
        {"id": ""},
        {"from": "bad\nname"},
    ],
)
def test_invalid_envelopes_are_rejected(store, params):
    with pytest.raises(BusError):
        send(store, **params)


def test_skeleton_database_migrates_without_losing_messages(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""CREATE TABLE agents(name TEXT PRIMARY KEY, registered_at INTEGER, capabilities TEXT);
        CREATE TABLE messages(id TEXT PRIMARY KEY,from_agent TEXT,to_agent TEXT,type TEXT,corr TEXT,ts INTEGER,payload TEXT,acked INTEGER DEFAULT 0);
        INSERT INTO agents VALUES ('old',1,'[]');""")
    con.execute(
        "INSERT INTO messages VALUES ('old-message','cli','agent:old','prompt',NULL,?,'{}',0)",
        (int(time.time()),),
    )
    con.commit()
    con.close()
    s = Store(path)
    assert s.inbox({"name": "old"})["messages"][0]["id"] == "old-message"
    s.close()
    s = Store(path)
    assert len(s.inbox({"name": "old"})["messages"]) == 1
    s.close()


def test_activity_pagination_and_traffic_count_survive_inbox_count_pruning(store):
    for i in range(1005):
        send(store, id=f"traffic-{i}")
    assert sum(store.traffic()) == 1005
    send(store, id="traffic-1004")
    assert sum(store.traffic()) == 1005
    ids, before = [], 0
    while True:
        page = store.history({"before": before})
        ids.extend(m["id"] for m in page["messages"])
        if not page["more"]:
            break
        before = page["next"]
    assert len(set(ids)) == len(ids) == 1000
    assert ids[0] == "traffic-1004"
    assert ids[-1] == "traffic-5"


def test_observer_cannot_take_over_an_active_claim(store):
    mid = send(store)["id"]
    store.inbox({"name": "alpha", "owner": "receiver"}, claim=True)
    with pytest.raises(BusError, match="Observation"):
        store.inbox({"name": "alpha", "observer": True, "owner": "other"}, claim=True)
    store.ack({"id": mid, "name": "alpha", "owner": "receiver"})
    assert store.history({})["messages"][0]["state"] == "acked"
    with pytest.raises(BusError, match="cannot be released"):
        store.ack({"id": mid, "name": "alpha"}, release=True)
    assert not store.inbox({"name": "alpha"})["messages"]
