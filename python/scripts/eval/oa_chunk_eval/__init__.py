"""OA chunking eval harness (docs/plans/results/oa-chunking-eval-2026-10/HARNESS-SPEC.md).

Implements the pass rule of PLAN-AND-PASS-RULE.md with Addendum A. One CLI::

    cd python/scripts/eval
    PYTHONPATH=../.. python -m oa_chunk_eval <cmd> --config /private/oa-chunk-eval.json

Subcommands, in pipeline order: ``sample``, ``passages``, ``questions``, ``audit``,
``retrieve``, ``score``, ``noise``, ``decide``. Everything with text (samples, passages,
questions, quotes, ledgers, retrieved chunks) is written under the config's PRIVATE
``out_dir``; only ``decision.json`` / ``decision.md`` (aggregates, no text) are meant to
be committed.

Exit codes: 0 done, 75 stopped early and resumable (pause file or failure stop),
2 configuration or input error.

No hostnames, ports, keys or paths are defaults in this code: all of them come from the
JSON config (``examples/oa-chunk-eval.config.example.json`` shows the shape with
placeholders). API keys are read from a file at call time, sent only as a header and
never logged or written.
"""

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_STOPPED = 75
