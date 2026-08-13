"""Every operator CLI must be able to render `--help` (#323).

argparse runs `%`-formatting over help strings, so a bare `%` in prose (`~99% of
the wall clock`) raises `TypeError: %o format: an integer is required` the moment
anything formats the help. Nothing else touches it, so the parser builds fine,
normal runs work, and the breakage only appears when a human — or a deployment
check — asks for help.

That is exactly how it was found: the bulk-load runbook mandates
`--help | grep <flag>` against the rebuilt worker image before submitting a
batch, and the grep came back empty because the process was crashing rather than
because the flag was absent.
"""
import importlib.util
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_CLIS = [
    "load_embeddings.py",
    "ingest_shard.py",
    "embed_shard.py",
    "gowe_batch_ingest.py",
    "plan_shards.py",
    "scan_notices.py",
    "jats_extract.py",
    "merge_receipts.py",
]


@pytest.mark.parametrize("name", _CLIS)
def test_help_renders(name, capsys):
    path = _SCRIPTS / name
    if not path.exists():
        pytest.skip(f"{name} not present")
    spec = importlib.util.spec_from_file_location(f"_cli_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "parse_args"):
        pytest.skip(f"{name} has no parse_args")
    # SystemExit(0) is argparse's normal --help exit. Any other exception —
    # notably TypeError from an unescaped '%' — is the failure we are guarding.
    with pytest.raises(SystemExit) as exc:
        mod.parse_args(["--help"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip(), f"{name} --help printed nothing"
