"""Request-scoped context, logging configuration, and the request-id middleware.

Why this package exists (#427). A user's query failed with a 503 on a production
tenant and we could not say *which* request it was, *whose* it was, or *how long*
it had run — the access-log line and the error line were unrelated rows. This is
the foundation that makes the next occurrence explainable: one id, generated per
request, carried on every log line the request produces and echoed to the caller.

It also fixes two things nobody asked for, both found while building it:

* **Nothing configured the root logger.** No ``basicConfig``/``dictConfig``
  anywhere for the API (the one ``basicConfig`` in the tree is a CLI). Uvicorn
  configures only its own loggers. So every ``log.info()`` under ``ragstack.*``
  was **silently discarded**, and ``log.warning()``/``log.error()`` fell through
  to ``logging.lastResort`` — a bare stderr ``StreamHandler`` with **no
  formatter**: no timestamp, no level, no logger name. The incident's
  ``qdrant unavailable: …`` line had no timestamp of its own.
* **``LOG_LEVEL`` configured nothing.** ``config.py`` defines it,
  ``GET /v1/config`` echoes it and tenant provisioning writes it — and nothing
  called ``setLevel``. The API advertised a knob it did not honour.

Two rules for anyone extending this package:

* **Never log ``principal.token`` or an ``api_key``.**
  ``security.Principal.__repr__`` redacts ``token``, but that guard covers
  ``repr()`` only — it does nothing for someone interpolating the attribute
  directly (``log.info("%s", principal.token)``). The context object here
  deliberately holds no credential material at all; keep it that way.
* **Never log the query text.** #114 mandates redaction by default. Log a short
  hash of it instead if correlation is needed.
"""

from ragstack.observability.context import (
    RequestContext,
    RequestContextFilter,
    clear_context,
    current_context,
    set_context,
)
from ragstack.observability.logging_config import (
    DEFAULT_DAMPEN_LOGGERS,
    LOG_LEVEL_NAMES,
    apply_dampening,
    apply_log_level,
    configure_logging,
    resolve_log_level,
)
from ragstack.observability.middleware import RequestContextMiddleware, new_request_id

__all__ = [
    "DEFAULT_DAMPEN_LOGGERS",
    "LOG_LEVEL_NAMES",
    "RequestContext",
    "RequestContextFilter",
    "RequestContextMiddleware",
    "apply_dampening",
    "apply_log_level",
    "clear_context",
    "configure_logging",
    "current_context",
    "new_request_id",
    "resolve_log_level",
    "set_context",
]
