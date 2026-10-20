"""SQLite owns durable identities, envelopes, per-consumer receipts, and delivery retries."""

import json
import re
import sqlite3
import time
import uuid

from .config import BusError
from .schema import migrate

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$")
TYPES = {"prompt", "reply", "status", "event", "request", "ack"}
TOPICS = {"broadcast:status", "broadcast:events"}
MAX_PAYLOAD = 65536
RETENTION_SECONDS = 7 * 86400
DESTINATION_LIMIT = 1000
PAGE_BYTES = 1024 * 1024
SWEEP_BATCH = 64


def text(value, field, limit=256):
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(c) < 32 for c in value):
        raise BusError("invalid_params", f"{field} must be a nonempty string of at most {limit} characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise BusError("invalid_params", f"{field} must contain valid Unicode") from error
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
    if not NAME.fullmatch(target) or (slash and (not machine or not NAME.fullmatch(machine))):
        raise BusError("invalid_params", "Invalid destination")
    return kind, machine, target


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA busy_timeout=5000")
            if migrate(self.db):
                self.prune(expired=False)
        except BaseException:
            self.db.close()
            raise

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
        try:
            for capability in caps:
                capability.encode("utf-8")
        except UnicodeEncodeError as error:
            raise BusError("invalid_params", "capabilities must contain valid Unicode") from error
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
        now = time.time()
        expired = self._expired_receipts()
        rows = self.db.execute("""SELECT a.*,coalesce(c.unread,0) unread FROM agents a
            LEFT JOIN mailbox_counts c ON c.consumer=a.name ORDER BY a.name""")
        return [
            {
                **dict(r),
                "unread": r["unread"] - expired.get(r["name"], 0),
                "address": f"agent:{r['name']}",
                "capabilities": json.loads(r["capabilities"]),
                "online": r["seen"] > now - 90,
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
        try:
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
            payload_size = len(encoded.encode("utf-8"))
        except (ValueError, TypeError, UnicodeError, RecursionError) as error:
            raise BusError("invalid_params", "payload must be finite JSON with valid Unicode") from error
        if payload_size > MAX_PAYLOAD:
            raise BusError("invalid_params", "payload exceeds 64 KiB")
        sender = text(p.get("from", "cli"), "from")
        corr = p.get("corr")
        if corr is not None:
            text(corr, "corr")
        mid = text(p.get("id", str(uuid.uuid4())), "id", 128)
        now = int(time.time())
        old = self.db.execute(
            "SELECT * FROM messages WHERE id=? AND ts>=?", (mid, now - RETENTION_SECONDS)
        ).fetchone()
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
            self.db.execute("DELETE FROM messages WHERE id=? AND ts<?", (mid, now - RETENTION_SECONDS))
            seq = self.db.execute("""UPDATE meta SET value=CAST(value AS INTEGER)+1
                WHERE key='message_sequence' RETURNING value""").fetchone()[0]
            self.db.execute(
                """INSERT INTO messages
                (id,from_agent,to_agent,type,corr,ts,payload,state,origin,seq) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (mid, sender, to, p["type"], corr, now, encoded, state, origin, seq),
            )
            consumers = [target] if agent else []
            if kind == "broadcast":
                consumers = [
                    r[0] for r in self.db.execute("SELECT name FROM subscriptions WHERE topic=?", (to,))
                ]
            self.db.executemany(
                "INSERT INTO receipts(message_id,consumer,seq) VALUES (?,?,?)",
                [(mid, name, seq) for name in consumers],
            )
            self.db.execute(
                "INSERT INTO traffic VALUES (?,1) ON CONFLICT(minute) DO UPDATE SET count=count+1",
                (int(time.time()) // 60,),
            )
            self._prune_destination(to)
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
        for field in ("observer", "include_acked"):
            if field in p and type(p[field]) is not bool:
                raise BusError("invalid_params", f"{field} must be a boolean")
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
        now = time.time()
        if not p.get("observer") and self.registered(name)["seen"] < now - 20:
            self.heartbeat({"name": name})
        rows = self.db.execute(
            """SELECT r.seq cursor,m.*,r.acked receipt_acked,r.lease_until FROM receipts r JOIN messages m
            ON r.message_id=m.id WHERE r.consumer=? AND r.seq>? AND m.ts>=?"""
            + (" AND r.acked=0" if not p.get("include_acked") else "")
            + (" AND r.lease_until<=?" if not p.get("observer") else "")
            + " ORDER BY r.seq LIMIT ?",
            [name, after, int(now) - RETENTION_SECONDS, *([now] if not p.get("observer") else []), limit + 1],
        )

        def render(row):
            return {
                **self.envelope(row),
                "cursor": row["cursor"],
                "acknowledged": bool(row["receipt_acked"]),
                "claimed": claim or row["lease_until"] > now,
            }

        rows, messages, more = self._page(rows, limit, p, render)
        if claim:
            with self.db:
                self.db.executemany(
                    "UPDATE receipts SET lease_owner=?,lease_until=? WHERE message_id=? AND consumer=?",
                    [(owner, time.time() + lease, r["id"], name) for r in rows],
                )
        return {
            "messages": messages,
            "next": rows[-1]["cursor"] if rows else after,
            "more": more,
            "owner": owner if claim else None,
            "total": self._inbox_total(name, bool(p.get("include_acked"))),
        }

    def ack(self, p, release=False, renew=False):
        mid = text(p.get("id"), "id", 128)
        now = time.time()
        msg = self.db.execute(
            "SELECT * FROM messages WHERE id=? AND ts>=?", (mid, int(now) - RETENTION_SECONDS)
        ).fetchone()
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
        if receipt["acked"] and not release and not renew:
            return {"ok": True}
        if release and receipt["acked"]:
            raise BusError("invalid_state", "An acknowledged delivery cannot be released")
        owner = p.get("owner")
        if receipt["lease_until"] > now and owner != receipt["lease_owner"]:
            raise BusError("lease_conflict", "Delivery is claimed by another receiver")
        if owner and (owner != receipt["lease_owner"] or receipt["lease_until"] <= now):
            raise BusError("lease_conflict", "No matching active claim")
        with self.db:
            if renew:
                if receipt["acked"] or not owner:
                    raise BusError("lease_conflict", "No matching active claim")
                lease = p.get("lease", 60)
                if type(lease) is not int or not 5 <= lease <= 3600:
                    raise BusError("invalid_params", "lease must be 5..3600 seconds")
                self.db.execute(
                    "UPDATE receipts SET lease_until=? WHERE message_id=? AND consumer=?",
                    (max(receipt["lease_until"], now + lease), mid, name),
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
        to = p.get("to")
        if to:
            address(to)
        rows = self.db.execute(
            "SELECT seq cursor,* FROM messages WHERE ts>=?"
            + (" AND to_agent=?" if to else "")
            + (" AND seq<?" if before else "")
            + " ORDER BY seq DESC LIMIT ?",
            [
                int(time.time()) - RETENTION_SECONDS,
                *([to] if to else []),
                *([before] if before else []),
                limit + 1,
            ],
        )
        rows, messages, more = self._page(
            rows, limit, p, lambda row: {**self.envelope(row), "cursor": row["cursor"]}
        )
        return {
            "messages": messages,
            "next": rows[-1]["cursor"] if rows else before,
            "more": more,
        }

    @staticmethod
    def _page(cursor, limit, params, render):
        try:
            budget = params.get("max_bytes", PAGE_BYTES)
            if type(budget) is not int or not 128 * 1024 <= budget <= 8 * PAGE_BYTES:
                raise BusError("invalid_params", "max_bytes must be 128 KiB..8 MiB")
            rows, messages, size = (
                [],
                [],
                4096,
            )  # Reserve the result and protocol wrapper, including owner/id.
            for row in cursor:
                if len(rows) == limit:
                    return rows, messages, True
                message = render(row)
                needed = (
                    len(json.dumps(message, ensure_ascii=True, separators=(",", ":")).encode("ascii")) + 1
                )
                if size + needed > budget:
                    if not rows:
                        raise BusError(
                            "invalid_params", f"max_bytes must be at least {size + needed} for this envelope"
                        )
                    return rows, messages, True
                rows.append(row)
                messages.append(message)
                size += needed
            return rows, messages, False
        finally:
            cursor.close()

    def message(self, p):
        mid = text(p.get("id"), "id", 128)
        row = self.db.execute(
            "SELECT * FROM messages WHERE id=? AND ts>=?", (mid, int(time.time()) - RETENTION_SECONDS)
        ).fetchone()
        if not row:
            raise BusError("not_found", "Unknown or expired message")
        return self.envelope(row)

    def consumers(self, mid):
        return [row[0] for row in self.db.execute("SELECT consumer FROM receipts WHERE message_id=?", (mid,))]

    def claim_delay(self, name):
        now = time.time()
        row = self.db.execute(
            """SELECT min(r.lease_until) FROM receipts r JOIN messages m ON m.id=r.message_id
            WHERE r.consumer=? AND r.acked=0 AND r.lease_until>? AND m.ts>=?""",
            (name, now, int(now) - RETENTION_SECONDS),
        ).fetchone()
        return max(0.01, row[0] - now) if row[0] is not None else None

    def _expired_receipts(self, name=None, include_acked=False):
        # Start at indexed expiry, not each consumer's entire retained inbox.
        cutoff = int(time.time()) - RETENTION_SECONDS
        if not self.db.execute("SELECT 1 FROM messages WHERE ts<? LIMIT 1", (cutoff,)).fetchone():
            return {}
        if name:
            return dict(
                self.db.execute(
                    """SELECT r.consumer,count(*) FROM receipts r JOIN messages m
                ON r.message_id=m.id WHERE r.consumer=? AND m.ts<?"""
                    + (" AND r.acked=0" if not include_acked else "")
                    + " GROUP BY r.consumer",
                    (name, cutoff),
                )
            )
        return dict(
            self.db.execute(
                """SELECT r.consumer,count(*) FROM messages m CROSS JOIN receipts r
            ON r.message_id=m.id WHERE m.ts<?"""
                + (" AND r.acked=0" if not include_acked else "")
                + " GROUP BY r.consumer",
                (cutoff,),
            )
        )

    def _inbox_total(self, name, include_acked):
        column = "total" if include_acked else "unread"
        row = self.db.execute(f"SELECT {column} FROM mailbox_counts WHERE consumer=?", (name,)).fetchone()
        return (row[0] if row else 0) - self._expired_receipts(name, include_acked).get(name, 0)

    def pending(self, limit=25, exclude=()):
        now = time.time()
        cutoff = int(now) - RETENTION_SECONDS
        return self.db.execute(
            """SELECT m.* FROM messages m WHERE m.state='queued' AND m.next_attempt<=?
            AND NOT EXISTS (SELECT 1 FROM messages older WHERE older.to_agent=m.to_agent
                AND older.state='queued' AND older.seq<m.seq AND older.ts>=?)
            AND m.ts>=?"""
            + (" AND m.to_agent NOT IN (" + ",".join("?" for _ in exclude) + ")" if exclude else "")
            + " ORDER BY m.seq LIMIT ?",
            [
                now,
                cutoff,
                cutoff,
                *exclude,
                limit,
            ],
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
                "UPDATE messages SET state='queued',next_attempt=0,last_error=NULL WHERE id=? AND ts>=? AND state IN ('queued','failed')",
                (mid, int(time.time()) - RETENTION_SECONDS),
            ).rowcount
        if not n:
            raise BusError("invalid_state", "Only queued or failed deliveries can be retried")
        return {"ok": True}

    def _prune_destination(self, destination):
        self.db.execute(
            """DELETE FROM messages WHERE id IN (
            SELECT id FROM messages WHERE to_agent=? ORDER BY ts DESC,seq DESC LIMIT -1 OFFSET ?)""",
            (destination, DESTINATION_LIMIT),
        )

    def sweep(self, limit=SWEEP_BATCH):
        with self.db:
            deleted = self.db.execute(
                """DELETE FROM messages WHERE id IN (
                SELECT id FROM messages WHERE ts<? LIMIT ?)""",
                (int(time.time()) - RETENTION_SECONDS, limit),
            ).rowcount
            self.db.execute("DELETE FROM traffic WHERE minute<?", (int(time.time()) // 60 - 7 * 1440,))
        return deleted

    def prune(self, *, expired=True):
        """Full startup/manual cleanup. Routine maintenance uses bounded age sweeps."""
        with self.db:
            destinations = [row[0] for row in self.db.execute("SELECT DISTINCT to_agent FROM messages")]
            for destination in destinations:
                self._prune_destination(destination)
        if expired:
            while self.sweep():
                pass

    def counts(self):
        counts = dict(self.db.execute("SELECT state,count FROM state_counts WHERE count>0"))
        for state, count in self.db.execute(
            "SELECT state,count(*) FROM messages WHERE ts<? GROUP BY state",
            (int(time.time()) - RETENTION_SECONDS,),
        ):
            counts[state] = counts.get(state, 0) - count
        return {state: count for state, count in counts.items() if count}

    def traffic(self):
        minute = int(time.time()) // 60
        counts = dict(self.db.execute("SELECT minute,count FROM traffic WHERE minute>=?", (minute - 59,)))
        return [counts.get(t, 0) for t in range(minute - 59, minute + 1)]

    def routes(self):
        rows = self.db.execute(
            """SELECT from_agent,to_agent,count(*) count FROM (
            SELECT from_agent,to_agent FROM messages WHERE ts>=? ORDER BY seq DESC LIMIT 100)
            GROUP BY from_agent,to_agent ORDER BY count DESC LIMIT 30""",
            (int(time.time()) - RETENTION_SECONDS,),
        )
        return [{"from": r[0], "to": r[1], "count": r[2]} for r in rows]

    def close(self):
        self.db.close()
