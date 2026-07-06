"""Poll the inbox so the hub *receives* invoices (P6-T5).

The received-email counterpart to ``seed_hub``: instead of POSTing each sample,
it calls ``POST /api/inbox/fetch`` so the API pulls messages from its configured
``InboxClient`` (the offline ``MockInbox`` by default), processes each through the
pipeline, and skips already-seen messages — so re-running is idempotent and the
demo shows invoices arriving "from email" rather than being seeded.

Provider-agnostic: the poller only calls the fetch route, so it drives the
``DriveInbox`` provider (``INBOX_PROVIDER=drive``) with no change — the API pulls
from the watched Drive folder instead of the mock set.

    python -m backend.tools.inbox_poller                      # one-shot -> http://127.0.0.1:8000
    python -m backend.tools.inbox_poller http://127.0.0.1:8000
    python -m backend.tools.inbox_poller --interval 60        # monitor: poll every 60s

With ``--interval N`` (or the ``INBOX_POLL_INTERVAL`` env var) the poller loops,
fetching every ``N`` seconds until interrupted — this is what turns a watched
Drive folder into a continuously *monitored* inbox. A transient fetch failure in
loop mode is logged and retried on the next tick rather than exiting, so a brief
API restart or network blip never stops the monitor. One-shot mode (no interval)
keeps the original behavior: a single fetch whose exit code reflects success.

Point it at the API that actually serves the inbox route (a local ``uvicorn`` or
the Compose ``api`` service). On macOS ``localhost`` may resolve to IPv6 first —
use ``127.0.0.1`` to force the local uvicorn if a container also publishes the port.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_API = "http://127.0.0.1:8000"


def run(api: str) -> int:
    """Fetch once, printing a per-message summary. Returns a shell exit code."""
    api = api.rstrip("/")
    req = urllib.request.Request(f"{api}/api/inbox/fetch", data=b"", method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            out = json.load(resp)
    except urllib.error.URLError as exc:
        print(f"  inbox fetch failed ({exc}) — is the API at {api}?", file=sys.stderr)
        return 1
    for row in out.get("received", []):
        print(f"  {row['message_id']}: {row['id']}  status={row['status']}  "
              f"decision={row.get('decision')}")
    print(f"\nReceived {out.get('count', 0)} new invoice(s); skipped "
          f"{out.get('skipped', 0)} already-seen. ({api})")
    return 0


def run_loop(api: str, interval: int) -> int:
    """Poll forever every ``interval`` seconds. Survives transient fetch errors."""
    api = api.rstrip("/")
    print(f"Monitoring {api} every {interval}s — Ctrl-C to stop.")
    try:
        while True:
            try:
                run(api)
            except Exception as exc:  # never let one bad tick kill the monitor
                print(f"  poll cycle errored ({exc}); retrying in {interval}s",
                      file=sys.stderr)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")
        return 0


def _parse_interval(argv: list[str]) -> tuple[int, list[str]]:
    """Pull ``--interval N`` / ``--interval=N`` out of argv; env is the fallback."""
    rest: list[str] = []
    interval = int(os.environ.get("INBOX_POLL_INTERVAL", "0") or "0")
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--interval":
            interval = int(argv[i + 1])
            i += 2
            continue
        if arg.startswith("--interval="):
            interval = int(arg.split("=", 1)[1])
            i += 1
            continue
        rest.append(arg)
        i += 1
    return interval, rest


def main(argv: list[str]) -> int:
    interval, rest = _parse_interval(argv[1:])
    api = rest[0] if rest else DEFAULT_API
    if interval > 0:
        return run_loop(api, interval)
    return run(api)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
