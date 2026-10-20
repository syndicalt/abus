#!/usr/bin/env python3
"""ABUS daemon — Omarchy Agent Bus.

Owns $XDG_RUNTIME_DIR/abus.sock and durable SQLite store.
"""
import asyncio
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

SOCKET = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "abus.sock"
STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "omarchy/abus"
DB = STATE / "abus.db"

STATE.mkdir(parents=True, exist_ok=True)

def init_db():
    con = sqlite3.connect(DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            name TEXT PRIMARY KEY,
            registered_at INTEGER,
            capabilities TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            from_agent TEXT,
            to_agent TEXT,
            type TEXT,
            corr TEXT,
            ts INTEGER,
            payload TEXT,
            acked INTEGER DEFAULT 0
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_to ON messages(to_agent, ts)")
    con.commit()
    return con

def prune(con):
    cutoff = int(time.time()) - 7 * 86400
    con.execute("DELETE FROM messages WHERE ts < ?", (cutoff,))
    # per-inbox count limit
    rows = con.execute("SELECT to_agent, COUNT(*) c FROM messages GROUP BY to_agent HAVING c > 1000").fetchall()
    for name, _ in rows:
        con.execute("""
            DELETE FROM messages WHERE to_agent = ? AND id NOT IN (
                SELECT id FROM messages WHERE to_agent = ? ORDER BY ts DESC LIMIT 1000
            )
        """, (name, name))
    con.commit()

async def handle(reader, writer, con):
    try:
        line = await reader.readline()
        if not line:
            return
        req = json.loads(line.decode())
        method = req.get("method")
        params = req.get("params", {})
        rid = req.get("id")

        if method == "register":
            name = params["name"]
            caps = json.dumps(params.get("capabilities", []))
            con.execute("INSERT OR REPLACE INTO agents VALUES (?, ?, ?)",
                        (name, int(time.time()), caps))
            con.commit()
            resp = {"id": rid, "result": {"ok": True, "name": name}}

        elif method == "send":
            to = params["to"]
            # require registered for agent: targets
            if to.startswith("agent:"):
                short = to.split(":", 1)[1]
                if not con.execute("SELECT 1 FROM agents WHERE name=?", (short,)).fetchone():
                    resp = {"id": rid, "error": "unregistered"}
                    writer.write((json.dumps(resp) + "\n").encode())
                    await writer.drain()
                    return
            mid = str(uuid.uuid4())
            con.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                (mid, params.get("from", "unknown"), to, params["type"],
                 params.get("corr"), int(time.time()), json.dumps(params.get("payload", {})))
            )
            con.commit()
            prune(con)
            resp = {"id": rid, "result": {"id": mid}}

        elif method == "inbox":
            name = params.get("name")
            to = f"agent:{name}" if name and not name.startswith(("agent:", "herdr:", "broadcast:")) else name
            rows = con.execute(
                "SELECT id, from_agent, type, corr, ts, payload FROM messages WHERE to_agent=? AND acked=0 ORDER BY ts",
                (to,)
            ).fetchall()
            msgs = [{"id": r[0], "from": r[1], "type": r[2], "corr": r[3], "ts": r[4],
                     "payload": json.loads(r[5])} for r in rows]
            resp = {"id": rid, "result": {"messages": msgs}}

        elif method == "ack":
            con.execute("UPDATE messages SET acked=1 WHERE id=?", (params["id"],))
            con.commit()
            resp = {"id": rid, "result": {"ok": True}}

        elif method == "list":
            agents = [r[0] for r in con.execute("SELECT name FROM agents").fetchall()]
            # Herdr stub: try CLI
            herdr = []
            try:
                import subprocess
                out = subprocess.check_output(["herdr", "agent", "list", "--json"], timeout=2, stderr=subprocess.DEVNULL)
                data = json.loads(out)
                herdr = [f"herdr:{a.get('id', a.get('pane_id', '?'))}" for a in data if isinstance(data, list)]
            except Exception:
                pass
            resp = {"id": rid, "result": {"agents": agents, "herdr": herdr}}

        else:
            resp = {"id": rid, "error": "unknown method"}

        writer.write((json.dumps(resp) + "\n").encode())
        await writer.drain()
    except Exception as e:
        writer.write((json.dumps({"error": str(e)}) + "\n").encode())
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()

async def main():
    if SOCKET.exists():
        SOCKET.unlink()
    con = init_db()
    server = await asyncio.start_unix_server(lambda r, w: handle(r, w, con), path=str(SOCKET))
    os.chmod(SOCKET, 0o600)
    print(f"ABUS listening on {SOCKET}", flush=True)
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
