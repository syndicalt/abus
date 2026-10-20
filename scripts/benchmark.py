#!/usr/bin/env python3
"""Compare isolated ABUS checkouts with identical retained messages and receipts."""

import argparse
import asyncio
import hashlib
import json
import resource
import statistics
import sys
import tempfile
import time
from pathlib import Path


def measure(operation, count=25):
    samples = []
    for _ in range(count):
        started = time.perf_counter()
        operation()
        samples.append((time.perf_counter() - started) * 1000)
    ordered = sorted(samples)
    return {
        "median_ms": round(statistics.median(samples), 3),
        "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3),
    }


def seed(store, rows, receivers, payload_bytes):
    for index in range(receivers):
        store.register({"name": f"worker-{index}"})
    has_sequence = "seq" in {row[1] for row in store.db.execute("PRAGMA table_info(messages)")}
    payload, now = json.dumps({"text": "x" * payload_bytes}), int(time.time())
    with store.db:
        sql = (
            "INSERT INTO messages(id,from_agent,to_agent,type,ts,payload"
            + (",seq" if has_sequence else "")
            + ") VALUES (?,?,?,?,?,?"
            + (",?" if has_sequence else "")
            + ")"
        )
        store.db.executemany(
            sql,
            [
                (
                    f"seed-{i}",
                    "benchmark",
                    f"agent:worker-{i % receivers}",
                    "event",
                    now,
                    payload,
                    *([i + 1] if has_sequence else []),
                )
                for i in range(rows)
            ],
        )
        store.db.executemany(
            "INSERT INTO receipts(message_id,consumer"
            + (",seq" if has_sequence else "")
            + ") VALUES (?,?"
            + (",?" if has_sequence else "")
            + ")",
            [
                (f"seed-{i}", f"worker-{i % receivers}", *([i + 1] if has_sequence else []))
                for i in range(rows)
            ],
        )
    if has_sequence:
        store.set_meta("message_sequence", str(rows))


async def api_benchmark(bus, client_call, clients):
    async def no_refresh():
        return []

    async def accepted(_envelope):
        return {"result": {}}

    bus.herdr.refresh = no_refresh
    bus.network.refresh = no_refresh
    bus.network.forward = accepted
    await bus.start()
    lag = []

    async def tick():
        previous = time.perf_counter()
        while True:
            await asyncio.sleep(0.005)
            current = time.perf_counter()
            lag.append(max(0, (current - previous - 0.005) * 1000))
            previous = current

    ticker = asyncio.create_task(tick())

    async def read(index):
        started = time.perf_counter()
        await asyncio.to_thread(
            client_call,
            "inbox",
            {"name": f"worker-{index}", "observer": True, "limit": 10},
            config=bus.config,
        )
        return (time.perf_counter() - started) * 1000

    try:
        started = time.perf_counter()
        latencies = sorted(await asyncio.gather(*(read(index) for index in range(clients))))
        return {
            "clients": clients,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "p95_ms": round(latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))], 3),
            "max_loop_delay_ms": round(max(lag, default=0), 3),
        }
    finally:
        ticker.cancel()
        await asyncio.gather(ticker, return_exceptions=True)
        await bus.close()


async def delivery_benchmark(Bus, Config, root):
    bus = Bus(Config(root / "delivery-run", root / "delivery-state"))
    healthy = asyncio.Event()

    async def refresh():
        return []

    async def forward(envelope):
        if envelope["id"] == "healthy":
            healthy.set()
        else:
            await asyncio.sleep(0.05)

    bus.herdr.refresh = refresh
    bus.network.refresh = refresh
    bus.network.forward = forward
    for index in range(25):
        bus.store.send({"to": f"agent:slow-{index}/worker", "type": "event"})
    bus.store.send({"id": "healthy", "to": "agent:healthy/worker", "type": "event"})
    started = time.perf_counter()
    await bus.start()
    try:
        await asyncio.wait_for(healthy.wait(), 5)
        return {
            "slow_destinations": 25,
            "each_delay_ms": 50,
            "healthy_latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    finally:
        await bus.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--rows", type=int, default=100000)
    parser.add_argument("--receivers", type=int, default=100)
    parser.add_argument("--payload-bytes", type=int, default=256, help="ASCII text bytes per seeded payload")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.receivers <= 128 or not 1 <= args.rows <= args.receivers * 1000:
        parser.error("Use 1..128 receivers and 1..1000 retained rows per receiver")
    if not 0 <= args.payload_bytes <= 65000:
        parser.error("payload-bytes must be 0..65000")
    sys.path.insert(0, str(args.checkout))
    from abus.client import call
    from abus.config import Config
    from abus.daemon import Bus
    from abus.store import Store

    source = hashlib.sha256()
    for file in sorted((args.checkout / "abus").glob("*.py")):
        source.update(file.name.encode() + file.read_bytes())
    result = {
        "rows": args.rows,
        "receivers": args.receivers,
        "payload_text_bytes": args.payload_bytes,
        "source_sha256": source.hexdigest(),
        "python": sys.version.split()[0],
    }
    with tempfile.TemporaryDirectory(prefix="abus-benchmark-") as directory:
        root = Path(directory)
        store = Store(root / "benchmark.db")
        seed(store, args.rows, args.receivers, args.payload_bytes)
        counter = iter(range(1000000))

        def send():
            return store.send({"id": f"new-{next(counter)}", "to": "agent:worker-0", "type": "event"})

        result["send"] = measure(send)
        result["inbox"] = measure(lambda: store.inbox({"name": "worker-0", "observer": True}))
        result["history"] = measure(lambda: store.history({}))
        for index in range(10):
            store.send({"to": f"agent:peer-{index}/worker", "type": "event"})
        result["pending"] = measure(store.pending)
        result["cleanup"] = measure(store.sweep if hasattr(store, "sweep") else store.prune)
        bus = Bus(Config(root / "api-run", root / "api-state"))
        bus.store.close()
        bus.store = store
        result["dashboard"] = measure(lambda: asyncio.run(bus.dispatch("dashboard", {"name": "worker-0"})))
        result["storage"] = {
            "database_bytes": (root / "benchmark.db").stat().st_size,
            "wal_bytes": (root / "benchmark.db-wal").stat().st_size,
            "inbox_page_bytes": len(json.dumps(store.inbox({"name": "worker-0", "observer": True})).encode()),
        }
        result["api"] = asyncio.run(api_benchmark(bus, call, args.receivers))
        result["delivery"] = asyncio.run(delivery_benchmark(Bus, Config, root))
        result["peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
