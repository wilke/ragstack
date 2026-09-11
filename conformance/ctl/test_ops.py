"""Mutation conformance (``POST /v1/tenants/{name}/ops/{verb}`` and friends).

Lands with the job engine in PR-C (idempotency keys, locks, plan re-validation,
confirm, doctor gate, delivery envelope) against ``ragstack-ctl serve
--fake-drivers``. PR-A ships the contract and the read surface only, so this
module skips as a whole rather than asserting against a surface that answers
nothing yet — a file that never asserts is indistinguishable from a wrong one,
and this skip says so by name.
"""

from __future__ import annotations

import pytest

pytest.skip("PR-C: mutation conformance lands with the job engine", allow_module_level=True)
