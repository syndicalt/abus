"""SQLite owns durable identities, envelopes, per-consumer receipts, and delivery retries."""

import json
import re
import sqlite3
import time
import uuid

from .config import BusError

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$")
TYPES = {"prompt", "reply", "status", "event", "request", "ack"}
TOPICS = {"broadcast:status", "broadcast:events"}
MAX_PAYLOAD = 65536


def text(value, field, limit=256):
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(c) < 32 for c in value):
        raise BusError("invalid_params", f"{field} must be a nonempty string of at most {limit} characters")
    return value


def agent_name(value):
    value = text(value, "name", 128)
    value = value.removeprefix("agent:")
    if not NAME.fullmatch(value):
        raise BusError("invalid_params", "Invalid agent name")
    return value


def address(value):
    value = text(value, "to")
    kind, sep, name = value.partition(":")
    if not sep or kind not in {"agent", "herdr", "broadcast"}:
        raise BusError("invalid_params", "Use agent:<name>, herdr:<pane>, or broadcast:status|events")
    if kind == "broadcast":
        if value not in TOPICS:
            raise BusError("invalid_params", "Unknown broadcast topic")
        return kind, None, name
    machine, slash, target = name.partition("/")
    if not slash:
        target, machine = machine, None
    if not NAME.fullmatch(target) or (machine and not NAME.fullmatch(machine)):
        raise BusError("invalid_params", "Invalid destination")
    return kind, machine, target


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
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
        """)
        # Additive migration preserves inboxes created by the original skeleton.
        self._columns(
            "agents", {"project": "TEXT", "session": "TEXT", "seen": "REAL DEFAULT 0", "endpoint": "TEXT"}
        )
        self._columns(
            "messages",
            {
                "state": "TEXT DEFAULT 'accepted'",
                "attempts": "INTEGER DEFAULT 0",
                "next_attempt": "REAL DEFAULT 0",
                "last_error": "TEXT",
                "origin": "TEXT DEFAULT 'local'",
            },
        )
        if not self.get_meta("schema"):
            self.db.execute("""INSERT OR IGNORE INTO receipts(message_id,consumer,acked)
                SELECT id,substr(to_agent,7),acked FROM messages WHERE to_agent LIKE 'agent:%'
                AND instr(to_agent,'/')=0""")
            self.set_meta("schema", "1")
        self.db.commit()
        if not self.get_meta("traffic_initialized"):
            with self.db:
                self.db.execute(
                    "INSERT OR IGNORE INTO traffic SELECT ts/60,count(*) FROM messages GROUP BY ts/60"
                )
            self.set_meta("traffic_initialized", "1")
        self.prune()

    def _columns(self, table, columns):
        existing = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns.items():
            if name not in existing:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def get_meta(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))

    def registered(self, name):
        return self.db.execute("SELECT * FROM agents WHERE name=?", (name,)).fetchone()

    def register(self, p):
        name = agent_name(p.get("name"))
        caps = p.get("capabilities", [])
        if (
            not isinstance(caps, list)
            or len(caps) > 64
            or any(not isinstance(c, str) or len(c) > 128 for c in caps)
        ):
            raise BusError("invalid_params", "capabilities must be an array of short strings")
        project = p.get("project")
        if project is not None:
            text(project, "project", 1024)
        session = p.get("session")
        if session is not None:
            text(session, "session", 128)
        endpoint = p.get("endpoint")
        if endpoint is not None:
            kind, machine, _ = address(endpoint)
            if kind != "herdr" or machine:
                raise BusError("invalid_params", "endpoint must be a local herdr:<pane-id>")
        now = time.time()
        with self.db:
            self.db.execute(
                """INSERT INTO agents(name,registered_at,capabilities,project,session,seen,endpoint)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET
                capabilities=excluded.capabilities, project=excluded.project,
                session=excluded.session, seen=excluded.seen, endpoint=excluded.endpoint""",
                (name, int(now), json.dumps(caps), project, session, now, endpoint),
            )
        return {"ok": True, "name": name, "address": f"agent:{name}"}

    def heartbeat(self, p):
        name = agent_name(p.get("name"))
        if not self.registered(name):
            raise BusError("unregistered", f"Agent {name} is not registered")
        with self.db:
            self.db.execute("UPDATE agents SET seen=? WHERE name=?", (time.time(), name))
        return {"ok": True}

    def unregister(self, p):
        name = agent_name(p.get("name"))
        with self.db:
            changed = self.db.execute("DELETE FROM agents WHERE name=?", (name,)).rowcount
        return {"ok": True, "removed": bool(changed), "inbox_retained": True}

    def agents(self):
        rows = self.db.execute("""SELECT a.*, (SELECT count(*) FROM receipts r
            WHERE r.consumer=a.name AND r.acked=0) unread FROM agents a ORDER BY name""")
        return [
            {
                **dict(r),
                "address": f"agent:{r['name']}",
                "capabilities": json.loads(r["capabilities"]),
                "online": r["seen"] > time.time() - 90,
            }
            for r in rows
        ]

    def subscribe(self, p, remove=False):
        name = agent_name(p.get("name"))
        if not self.registered(name):
            raise BusError("unregistered", f"Agent {name} is not registered")
        topic = p.get("topic")
        if topic not in TOPICS:
            raise BusError("invalid_params", "Unknown broadcast topic")
        with self.db:
            if remove:
                self.db.execute("DELETE FROM subscriptions WHERE name=? AND topic=?", (name, topic))
            else:
                self.db.execute("INSERT OR IGNORE INTO subscriptions VALUES (?,?)", (name, topic))
        return {"ok": True, "name": name, "topic": topic}

    def send(self, p, origin="local"):
        to = p.get("to")
        kind, machine, target = address(to)
        if p.get("type") not in TYPES:
            raise BusError("invalid_params", "Unknown message type")
        payload = p.get("payload", {})
        if not isinstance(payload, dict):
            raise BusError("invalid_params", "payload must be a JSON object")
        encoded = json.dumps(payload, allow_nan=False)
        if len(encoded.encode()) > MAX_PAYLOAD:
            raise BusError("invalid_params", "payload exceeds 64 KiB")
        sender = text(p.get("from", "cli"), "from")
        corr = p.get("corr")
        if corr is not None:
            text(corr, "corr")
        mid = text(p.get("id", str(uuid.uuid4())), "id", 128)
        old = self.db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
        if old:
            if (
                old["origin"],
                old["to_agent"],
                old["from_agent"],
                old["type"],
                old["corr"],
                json.loads(old["payload"]),
            ) != (origin, to, sender, p["type"], corr, payload):
                raise BusError("id_conflict", "Message id already belongs to a different envelope")
            return {"id": mid, "state": old["state"], "duplicate": True}
        agent = self.registered(target) if kind == "agent" and not machine else None
        if kind == "agent" and not machine and not agent:
            raise BusError("unregistered", f"Recipient {to} is not registered")
        state = "queued" if machine or kind == "herdr" or (agent and agent["endpoint"]) else "accepted"
        with self.db:
            self.db.execute(
                """INSERT INTO messages
                (id,from_agent,to_agent,type,corr,ts,payload,state,origin) VALUES (?,?,?,?,?,?,?,?,?)""",
                (mid, sender, to, p["type"], corr, int(time.time()), encoded, state, origin),
            )
            consumers = [target] if agent else []
            if kind == "broadcast":
                consumers = [
                    r[0] for r in self.db.execute("SELECT name FROM subscriptions WHERE topic=?", (to,))
                ]
            self.db.executemany(
                "INSERT INTO receipts(message_id,consumer) VALUES (?,?)", [(mid, name) for name in consumers]
            )
            self.db.execute(
                "INSERT INTO traffic VALUES (?,1) ON CONFLICT(minute) DO UPDATE SET count=count+1",
                (int(time.time()) // 60,),
            )
        self.prune()
        return {"id": mid, "state": state, "duplicate": False}

    @staticmethod
    def envelope(row):
        return {
            "v": 1,
            "id": row["id"],
            "from": row["from_agent"],
            "to": row["to_agent"],
            "type": row["type"],
            "corr": row["corr"],
            "ts": row["ts"],
            "payload": json.loads(row["payload"]),
            "state": row["state"],
            "attempts": row["attempts"],
            "last_error": row["last_error"],
        }

    def inbox(self, p, claim=False):
        if claim and (p.get("observer") or p.get("include_acked")):
            raise BusError("invalid_params", "Observation cannot acquire delivery claims")
        name = agent_name(p.get("name"))
        if not self.registered(name):
            raise BusError("unregistered", f"Agent {name} is not registered")
        limit = p.get("limit", 100)
        after = p.get("after", 0)
        if type(limit) is not int or not 1 <= limit <= 100 or type(after) is not int or after < 0:
            raise BusError("invalid_params", "limit must be 1..100 and after a nonnegative cursor")
        owner = text(p.get("owner", name), "owner", 128)
        lease = p.get("lease", 60)
        if type(lease) is not int or not 5 <= lease <= 3600:
            raise BusError("invalid_params", "lease must be 5..3600 seconds")
        self.prune()
        if not p.get("observer"):
            self.heartbeat({"name": name})
        rows = self.db.execute(
            """SELECT m.rowid cursor,m.*,r.acked receipt_acked,r.lease_until FROM messages m JOIN receipts r
            ON r.message_id=m.id WHERE r.consumer=? AND (? OR r.acked=0) AND m.rowid>?
            AND (? OR r.lease_until<=?) ORDER BY m.rowid LIMIT ?""",
            (name, bool(p.get("include_acked")), after, bool(p.get("observer")), time.time(), limit + 1),
        ).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        if claim:
            with self.db:
                self.db.executemany(
                    "UPDATE receipts SET lease_owner=?,lease_until=? WHERE message_id=? AND consumer=?",
                    [(owner, time.time() + lease, r["id"], name) for r in rows],
                )
        return {
            "messages": [
                {
                    **self.envelope(r),
                    "cursor": r["cursor"],
                    "acknowledged": bool(r["receipt_acked"]),
                    "claimed": claim or r["lease_until"] > time.time(),
                }
                for r in rows
            ],
            "next": rows[-1]["cursor"] if rows else after,
            "more": more,
            "owner": owner if claim else None,
            "total": self.db.execute(
                "SELECT count(*) FROM receipts WHERE consumer=? AND (? OR acked=0)",
                (name, bool(p.get("include_acked"))),
            ).fetchone()[0],
        }

    def ack(self, p, release=False, renew=False):
        mid = text(p.get("id"), "id", 128)
        msg = self.db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
        if not msg:
            raise BusError("not_found", "Unknown or expired message")
        name = p.get("name")
        if not name and msg["to_agent"].startswith("agent:") and "/" not in msg["to_agent"]:
            name = msg["to_agent"][6:]
        if not name:
            raise BusError("invalid_params", "name is required for broadcast acknowledgments")
        name = agent_name(name)
        receipt = self.db.execute(
            "SELECT * FROM receipts WHERE message_id=? AND consumer=?", (mid, name)
        ).fetchone()
        if not receipt:
            raise BusError("not_found", "No delivery for this consumer")
        if release and receipt["acked"]:
            raise BusError("invalid_state", "An acknowledged delivery cannot be released")
        owner = p.get("owner")
        if receipt["lease_until"] > time.time() and owner != receipt["lease_owner"]:
            raise BusError("lease_conflict", "Delivery is claimed by another receiver")
        with self.db:
            if renew:
                if receipt["acked"] or not owner or owner != receipt["lease_owner"]:
                    raise BusError("lease_conflict", "No matching active claim")
                self.db.execute(
                    "UPDATE receipts SET lease_until=? WHERE message_id=? AND consumer=?",
                    (time.time() + 60, mid, name),
                )
            else:
                self.db.execute(
                    "UPDATE receipts SET acked=?,lease_until=0,lease_owner=NULL WHERE message_id=? AND consumer=?",
                    (0 if release else 1, mid, name),
                )
                if not release and not msg["to_agent"].startswith("broadcast:"):
                    self.db.execute("UPDATE messages SET acked=1,state='acked' WHERE id=?", (mid,))
        return {"ok": True}

    def history(self, p):
        limit, before = p.get("limit", 100), p.get("before", 0)
        if type(limit) is not int or not 1 <= limit <= 100 or type(before) is not int or before < 0:
            raise BusError("invalid_params", "limit must be 1..100 and before a nonnegative cursor")
        self.prune()
        to = p.get("to")
        if to:
            address(to)
        rows = self.db.execute(
            """SELECT rowid cursor,* FROM messages
            WHERE (? IS NULL OR to_agent=?) AND (?=0 OR rowid<?) ORDER BY rowid DESC LIMIT ?""",
            (to, to, before, before, limit + 1),
        ).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        return {
            "messages": [{**self.envelope(r), "cursor": r["cursor"]} for r in rows],
            "next": rows[-1]["cursor"] if rows else before,
            "more": more,
        }

    def pending(self):
        return self.db.execute(
            """SELECT m.* FROM messages m WHERE m.state='queued' AND m.next_attempt<=?
            AND NOT EXISTS (SELECT 1 FROM messages older WHERE older.to_agent=m.to_agent
                AND older.state='queued' AND older.rowid<m.rowid)
            ORDER BY m.rowid LIMIT 25""",
            (time.time(),),
        ).fetchall()

    def delivered(self, mid, state):
        with self.db:
            self.db.execute(
                "UPDATE messages SET state=?,attempts=attempts+1,last_error=NULL WHERE id=? AND state='queued'",
                (state, mid),
            )

    def failed(self, mid, error, permanent=False):
        row = self.db.execute("SELECT attempts FROM messages WHERE id=?", (mid,)).fetchone()
        if row:
            with self.db:
                self.db.execute(
                    "UPDATE messages SET state=?,attempts=attempts+1,last_error=?,next_attempt=? WHERE id=? AND state='queued'",
                    (
                        "failed" if permanent else "queued",
                        str(error)[:1000],
                        time.time() + min(300, 2 ** min(row[0] + 1, 8)),
                        mid,
                    ),
                )

    def retry(self, p):
        mid = text(p.get("id"), "id", 128)
        with self.db:
            n = self.db.execute(
                "UPDATE messages SET state='queued',next_attempt=0,last_error=NULL WHERE id=? AND state IN ('queued','failed')",
                (mid,),
            ).rowcount
        if not n:
            raise BusError("invalid_state", "Only queued or failed deliveries can be retried")
        return {"ok": True}

    def prune(self):
        with self.db:
            self.db.execute("DELETE FROM messages WHERE ts<?", (int(time.time()) - 7 * 86400,))
            self.db.execute("DELETE FROM traffic WHERE minute<?", (int(time.time()) // 60 - 7 * 1440,))
            self.db.execute("""DELETE FROM messages WHERE rowid IN (
                SELECT rid FROM (SELECT rowid rid,row_number() OVER (
                    PARTITION BY to_agent ORDER BY ts DESC,rowid DESC) n FROM messages) WHERE n>1000)""")

    def counts(self):
        return {r[0]: r[1] for r in self.db.execute("SELECT state,count(*) FROM messages GROUP BY state")}

    def traffic(self):
        minute = int(time.time()) // 60
        counts = dict(self.db.execute("SELECT minute,count FROM traffic WHERE minute>=?", (minute - 59,)))
        return [counts.get(t, 0) for t in range(minute - 59, minute + 1)]

    def routes(self):
        rows = self.db.execute("""SELECT from_agent,to_agent,count(*) count FROM (
            SELECT from_agent,to_agent FROM messages ORDER BY rowid DESC LIMIT 100)
            GROUP BY from_agent,to_agent ORDER BY count DESC LIMIT 30""")
        return [{"from": r[0], "to": r[1], "count": r[2]} for r in rows]

    def close(self):
        self.db.close()
