"""Poll the (mock) inbox so the hub *receives* invoices (P6-T5).

The received-email counterpart to ``seed_hub``: instead of POSTing each sample,
it calls ``POST /api/inbox/fetch`` so the API pulls messages from its configured
``InboxClient`` (the offline ``MockInbox`` by default), processes each through the
pipeline, and skips already-seen messages — so re-running is idempotent and the
demo shows invoices arriving "from email" rather than being seeded.

    python -m backend.tools.inbox_poller                      # -> http://127.0.0.1:8000
    python -m backend.tools.inbox_poller http://127.0.0.1:8000

Point it at the API that actually serves the inbox route (a local ``uvicorn`` or
the Compose ``api`` service). On macOS ``localhost`` may resolve to IPv6 first —
use ``127.0.0.1`` to force the local uvicorn if a container also publishes the port.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request


def run(api: str) -> int:
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
    print("Open the hub to review the received invoices.")
    return 0


def main(argv: list[str]) -> int:
    api = argv[1] if len(argv) > 1 else "http://127.0.0.1:8000"
    return run(api)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
