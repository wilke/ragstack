"""Control-plane conformance suite (``conformance/ctl``).

This is a package on purpose. ``conformance/`` runs in pytest's default
"prepend" import mode, where a test module is imported by its basename; this
directory has a ``test_health.py`` and so does ``conformance/``, and without an
``__init__.py`` here ``pytest conformance/`` would refuse the second one as an
"import file mismatch". With it, these modules import as ``ctl.test_health``
and the two suites coexist under one ``pytest conformance/`` run.
"""
