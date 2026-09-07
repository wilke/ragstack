"""Grading — the study's two-independent-reader label validation, server-side.

``docs/plans/grading-ui.md`` moves the R-dev / R-conf evidence read
(``SPEC-confirmation-run.md`` §6.6) off a claude.ai artifact, where reader
independence was honour-based, into RAGStack, where it is enforced: a reader
can read and write only their own verdict row, and the per-reader order is
computed on the server from the batch's seed.

* :mod:`ragstack.grading.models` — the stored records and the normative
  order rule.
* :mod:`ragstack.grading.store` — the ``memory`` | ``sqlite`` | ``postgres``
  backend switch, mirroring the job store's.

The HTTP surface is :mod:`ragstack.api.routers.grading`; the contract is
``contracts/openapi.yaml`` (tag ``Grading``) plus ``contracts/schemas/grading_*.json``.
"""
