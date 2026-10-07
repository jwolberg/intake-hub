"""Test-wide environment.

``backend.config.settings`` is read once at import, so env defaults must be set
before any ``backend`` module is imported. Auth defaults to fail-closed IAP in
the app; the suite runs with it disabled, and ``tests/integration/test_auth.py``
re-enables it explicitly.
"""

import os

os.environ.setdefault("AUTH_MODE", "disabled")
