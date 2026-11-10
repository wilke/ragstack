"""The GATEWAY SIZE-NOTICE's ``~N tokens in``, re-based per model (``glossbench_alias_judge.
gateway_est_input_tokens``): the figure is the characters the batch's calls will actually send
(the judge version's system message plus each pair's user message) divided by a calibrated
characters-per-token rate of the model's family, instead of the flat 3.4 applied to the
alias-judge/1 system for every gateway judge.

Checked against the pre-change modules (``git show`` of :data:`PRE_REBASE_COMMIT`, as
``test_alias_control_chunks.py``'s byte-identity test does): on a fixture batch the notice
reports the calibrated figure per model, nothing else in the preflight output changes, and the
request bodies are byte-identical. Fake judges, no gateway, no model."""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE.parents[1] / "scripts" / "eval", _HERE.parents[1] / "scripts" / "eval" / "glossbench"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import alias_study_dryrun as dry  # noqa: E402
import alias_study_fixture as fx  # noqa: E402
import alias_study_judge as aj  # noqa: E402
import glossbench_alias_judge as jd  # noqa: E402
import test_glossbench_alias_judge as tgj  # noqa: E402

#: main before the re-base (PR #988's merge): its harness prints the flat-3.4 estimate. Pinned to
#: a commit on main so it survives a rebase or squash of the feature branch.
PRE_REBASE_COMMIT = "8afb4cab6d967c5a1d9373febcc781ca64a42576"
FIXED_T = "2026-11-10T00:00:00+00:00"
PROV = {"commit": "c" * 40, "dirty": False}
GW = "http://gw.invalid:18799/v1"
#: the calibrated rates, pinned here so a change of a constant is a visible test change
ARGON_CPT, OPUS_CPT = 4.26, 2.81
TOKENS_IN = re.compile(r"~\d+ tokens in")


# ------------------------------------------------------------------ the rate

def test_rate_per_model_family() -> None:
    assert jd.gateway_chars_per_token("Gemini 4 Argon") == ARGON_CPT
    assert jd.gateway_chars_per_token("Claude Opus 5.5") == OPUS_CPT
    assert jd.gateway_chars_per_token("claude-opus-5-5") == OPUS_CPT
    for other in ("qwen-x", "Some Other Model", "", None):
        assert jd.gateway_chars_per_token(other) == jd.dr.CHARS_PER_TOKEN_GATEWAY == 3.4


def test_rate_reproduces_the_audit_calibration_run() -> None:
    """The audit's first run: the flat notice read N per call for both judges; Argon measured
    0.831 N (888) and Opus 1.211 N (1,290). Re-estimated from the text actually sent (Argon's
    alias-judge/2 system is longer than the alias-judge/1 one the flat notice counted), the new
    figure lands on the measurement."""
    v1, v2 = len(jd.JUDGE_SYSTEMS["alias-judge/1"]), len(jd.JUDGE_SYSTEMS["alias-judge/2"])
    assert v2 - v1 == 153
    for measured, err, version, model in ((888, -0.169, "alias-judge/2", "Gemini 4 Argon"),
                                          (1290, 0.211, "alias-judge/1", "Claude Opus 5.5")):
        flat_chars = round(measured / (1 + err) * jd.dr.CHARS_PER_TOKEN_GATEWAY)  # v1 system + user message
        user = "u" * (flat_chars - v1)
        est = jd.gateway_est_input_tokens(model, jd.JUDGE_SYSTEMS[version], [user], [1])
        assert abs(est - measured) / measured < 0.005, (model, est, measured)


def test_estimate_weights_each_prompt_by_its_calls() -> None:
    sys_ = "s" * 10
    assert jd.gateway_est_input_tokens("Claude Opus 5.5", sys_, ["a" * 18, "b" * 46], [1, 3]) == int(
        (28 + 3 * 56) / OPUS_CPT)
    with pytest.raises(ValueError):
        jd.gateway_est_input_tokens("Claude Opus 5.5", sys_, ["a"], [1, 1])


# ------------------------------------------------------------------ the calibration harness

def test_harness_gateway_counts_the_judge_version_system_and_the_subset_reps(tmp_path: Path) -> None:
    cfgp = tgj.make_world(tmp_path)
    assert jd.main(["--config", str(cfgp), "build-calib"], prov=PROV) == 0
    _, cfg = jd.load_config(cfgp)
    w = jd.World(cfg)
    pairs = w.pairs()
    sub = [p["pair_id"] for p in pairs[:2]]
    sp = tmp_path / "subset.txt"
    sp.write_text("".join(x + "\n" for x in sub))
    raw = json.loads(cfgp.read_text())
    raw["judge_gw"] = {"base_url": GW, "model": "Gemini 4 Argon", "tag": "argon", "judge_version": "alias-judge/2",
                       "subset_file": str(sp), "subset_reps": 3}
    cfgp.write_text(json.dumps(raw))
    _, cfg = jd.load_config(cfgp)
    lab = jd.Labeler(cfg, jd.World(cfg), provenance=PROV)
    plan = lab.plan("gateway", 1)
    v2 = jd.JUDGE_SYSTEMS["alias-judge/2"]
    chars = sum((len(v2) + len(w.prompt(p)[0])) * (3 if p["pair_id"] in sub else 1) for p in pairs)
    assert plan["calls"] == len(pairs) + 2 * 2
    assert plan["est_input_tokens"] == int(chars / ARGON_CPT)


def test_harness_opus_backend_uses_the_model_rate_and_unknown_models_keep_the_flat_figure(tmp_path: Path) -> None:
    cfgp = tgj.make_world(tmp_path)
    assert jd.main(["--config", str(cfgp), "build-calib"], prov=PROV) == 0
    _, cfg = jd.load_config(cfgp)
    w = jd.World(cfg)
    one_rep = sum(len(jd.JUDGE_SYSTEM) + len(w.prompt(p)[0]) for p in w.pairs())
    # the fixture's gateway model ("Claude Opus X") is a Claude model
    assert jd.Labeler(cfg, w).plan("opus", 3)["est_input_tokens"] == int(one_rep / OPUS_CPT) * 3
    cfg["gateway"] = {**cfg["gateway"], "model": "some-other-model"}
    assert jd.Labeler(cfg, w).plan("opus", 3)["est_input_tokens"] == int(one_rep / 3.4) * 3
    # the non-gateway backends' estimates are not touched
    assert jd.Labeler(cfg, w).plan("qwen", 1)["est_input_tokens"] == int(one_rep / 3.4)


# ------------------------------------------------------------------ the study, against the pre-change code

def _git_show(path: str) -> bytes:
    repo = _HERE.parents[2]
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    # with git present a missing commit is a failure, never a skip: a shallow or foreign checkout
    # would otherwise pass this byte-identity check by not running it
    try:
        return subprocess.run(["git", "-C", str(repo), "show", f"{PRE_REBASE_COMMIT}:{path}"],
                              check=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        stderr = getattr(e, "stderr", b"") or b""
        pytest.fail(f"git is available but commit {PRE_REBASE_COMMIT[:8]} or its {path} cannot be read: "
                    f"{stderr.decode(errors='replace').strip() or e}")


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def pre(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The pre-change ``alias_study_judge`` on top of the pre-change harness."""
    jd_src = _git_show("python/scripts/eval/glossbench_alias_judge.py")
    aj_src = _git_show("python/scripts/eval/alias_study_judge.py")
    assert b"gateway_est_input_tokens" not in jd_src
    # deep enough that the module's out_dir-inside-the-repository check (its directory's
    # parents[2]) is pre/, which holds no out_dir
    d = tmp_path / "pre" / "a" / "b" / "c"
    d.mkdir(parents=True)
    (d / "glossbench_alias_judge_pre.py").write_bytes(jd_src)
    (d / "alias_study_judge_pre.py").write_bytes(aj_src)
    monkeypatch.setattr(sys, "path", list(sys.path))
    old_jd = _load("glossbench_alias_judge_pre", d / "glossbench_alias_judge_pre.py")
    # the old study module imports ``glossbench_alias_judge``: give it the old harness, then
    # put the current one back at once (the current study module is bound to it already)
    cur = sys.modules["glossbench_alias_judge"]
    sys.modules["glossbench_alias_judge"] = old_jd
    try:
        old_aj = _load("alias_study_judge_pre", d / "alias_study_judge_pre.py")
    finally:
        sys.modules["glossbench_alias_judge"] = cur
    assert old_aj.jd is old_jd and aj.jd is jd and old_jd is not jd
    assert old_jd.g is jd.g     # one clock module: the frozen clock holds for both
    monkeypatch.setattr(jd.g, "now_iso", lambda: FIXED_T)
    return old_aj


class Recorder:
    """The fake judges of the dry run, deterministic per (model, user message, call number of that
    message), so the re-issue pattern does not depend on scheduling; records every request body as
    the bytes an HTTP client would send."""

    def __init__(self) -> None:
        self.bodies: list[bytes] = []
        self.n: Counter[tuple[str, str]] = Counter()

    async def post(self, url: str, json: dict[str, Any]) -> dry.Resp:  # noqa: A002
        import json as _json
        self.bodies.append(url.encode() + b"\n" + _json.dumps(json, ensure_ascii=False,
                                                              separators=(",", ":")).encode())
        model, user = json["model"], json["messages"][-1]["content"]
        self.n[(model, user)] += 1
        k = self.n[(model, user)]
        n = int(json.get("n") or 1)
        truth = dry.h("truth", user) % 4 == 0
        choices = []
        for i in range(n):
            v = truth
            if model.startswith("Qwen3.6") and dry.h("c", user, str(i)) % 9 == 0:
                v = not v
            if model.startswith("Qwen3.8") and dry.h("e", user) % 15 == 0:
                v = not v
            text = dry.json_verdict(v)
            if model == "Gemini 4 Argon" and k == 1:      # every Argon pair is re-issued once
                text = "I cannot decide."
            if model == "Claude Opus 5.5" and dry.h("opus", user) % 10 == 0:
                text = ""
            choices.append({"index": i, "message": {"content": text}, "finish_reason": "stop"})
        return dry.Resp(200, {"model": model, "choices": choices,
                              "usage": {"prompt_tokens": 300, "completion_tokens": 30 * n}})

    async def aclose(self) -> None:
        pass


def _study_world(tmp: Path) -> tuple[Path, Path]:
    tmp.mkdir()
    w = fx.World(tmp)
    w.write_staged_runs("A")
    cfg = {"out_dir": str(tmp / "judge"), "runs_dir": str(w.runs), "records": str(w.records_path),
           "chunk_exports": {k: str(v) for k, v in w.exports.items()}, "glossary": str(w.glossary),
           "batch_size": 40, "caps": {"argon_share": 1.0, "opus_share": 1.0}, "dry_run": True,
           "drift": {"share": 0.5},
           "judge_c": {"base_url": "http://c.invalid/v1", **{k: v for k, v in aj.FIXED["C"].items() if k != "variant"}},
           "judge_e": {"base_url": "http://e.invalid/v1", **{k: v for k, v in aj.FIXED["E"].items() if k != "variant"}},
           "tiebreak": {"base_url": GW, "empty_retry_s": 0.0, **{k: v for k, v in aj.FIXED["argon"].items() if k != "tag"}},
           "drift_ref": {"base_url": GW, "empty_retry_s": 0.0, **{k: v for k, v in aj.FIXED["opus"].items() if k != "tag"}}}
    cp = tmp / "jcfg.json"
    cp.write_text(json.dumps(cfg))
    return cp, tmp / "judge"


def _run_batch(mod: Any, cp: Path, capsys: pytest.CaptureFixture[str]) -> tuple[Recorder, dict[str, str]]:
    """build-pairs A, then preflight and label batch 1 on every backend; the preflight stdout per backend."""
    rec = Recorder()

    def main(*a: str) -> int:
        return int(mod.main(["--config", str(cp), *a], http_factory=lambda: rec, prov=PROV))

    assert main("build-pairs", "--stage", "A") == 0
    printed = {}
    for be in mod.BACKENDS:
        capsys.readouterr()
        assert main("preflight", "--batch", "1", "--backend", be) == 0
        printed[be] = capsys.readouterr().out
        assert main("label", "--batch", "1", "--backend", be, "--notice-acknowledged") == 0, be
    capsys.readouterr()
    return rec, printed


def test_study_notice_is_rebased_and_nothing_else_changes(pre: Any, tmp_path: Path,
                                                          capsys: pytest.CaptureFixture[str]) -> None:
    cp_old, out_old = _study_world(tmp_path / "old")
    cp_new, out_new = _study_world(tmp_path / "new")
    rec_old, printed_old = _run_batch(pre, cp_old, capsys)
    rec_new, printed_new = _run_batch(aj, cp_new, capsys)

    # ---- the request bodies are byte-identical (every backend, re-issues included)
    assert sorted(rec_new.bodies) == sorted(rec_old.bodies)
    sent = Counter(json.loads(b.split(b"\n", 1)[1])["model"] for b in rec_new.bodies)
    assert sent["Gemini 4 Argon"] > 0 and sent["Claude Opus 5.5"] > 0
    assert all(n > 1 for (m, _), n in rec_new.n.items() if m == "Gemini 4 Argon")   # re-issues exercised

    # ---- the labels written are the same rows (only the clock and the run's own paths could differ)
    for name in ("labels-qwen-C.jsonl", "labels-qwen-E.jsonl", "labels-gw-argon.jsonl", "labels-gw-opus.jsonl"):
        old_rows = (out_old / name).read_text().replace(str(tmp_path / "old"), "<W>")
        assert (out_new / name).read_text().replace(str(tmp_path / "new"), "<W>") == old_rows, name

    # ---- preflight: only the gateway estimate differs, and it is the calibrated figure
    pl = aj.Pipeline(aj.load_config(cp_new), provenance=PROV)
    report = {}
    for be, key, version, cpt in (("argon", "gw-argon", "alias-judge/2", ARGON_CPT),
                                  ("opus", "gw-opus", "alias-judge/1", OPUS_CPT)):
        lab = pl.labeler(1, be)
        system = jd.JUDGE_SYSTEMS[version]
        chars = sum(len(system) + len(lab.w.prompt(p)[0]) for p in lab.w.pairs())
        old_pf = json.loads((out_old / f"preflight-{key}.json").read_text())
        new_pf = json.loads((out_new / f"preflight-{key}.json").read_text())
        assert new_pf["plan"]["est_input_tokens"] == int(chars / cpt)
        report[be] = (old_pf["plan"]["est_input_tokens"], new_pf["plan"]["est_input_tokens"], new_pf["plan"]["calls"])
        assert report[be][0] != report[be][1]
        assert {k: v for k, v in new_pf["plan"].items() if k != "est_input_tokens"} == \
            {k: v for k, v in old_pf["plan"].items() if k != "est_input_tokens"}
        assert set(new_pf) == set(old_pf) == {"plan", "plan_sha256", "t", "provenance"}
        assert new_pf["t"] == old_pf["t"] and new_pf["provenance"] == old_pf["provenance"]
        assert new_pf["plan_sha256"] == jd.Labeler.plan_sha(lab, new_pf["plan"])
        # the notice: the same line but the number, which is the calibrated one
        line_new = next(x for x in printed_new[be].splitlines() if x.startswith("GATEWAY SIZE-NOTICE"))
        line_old = next(x for x in printed_old[be].splitlines() if x.startswith("GATEWAY SIZE-NOTICE"))
        assert f"~{int(chars / cpt)} tokens in" in line_new and f"~{report[be][0]} tokens in" in line_old
        assert TOKENS_IN.sub("~N tokens in", line_new) == TOKENS_IN.sub("~N tokens in", line_old)
        assert printed_new[be].splitlines()[-1] == line_new     # printed by the preflight, before any call
        assert TOKENS_IN.sub("~N", printed_new[be]) == TOKENS_IN.sub("~N", printed_old[be])
    # the direction of the re-base: Argon's figure falls, Opus's rises
    assert report["argon"][1] < report["argon"][0] and report["opus"][1] > report["opus"][0]
    print(f"\nfixture batch 1 (old -> new est_input_tokens, calls): {report}", file=sys.__stderr__)

    # the local judges' preflights are byte-identical; the LOG's notices (the preflights' lines;
    # its label totals carry wall-clock rates) differ only in the two numbers
    for key in ("qwen-C", "qwen-E"):
        assert (out_new / f"preflight-{key}.json").read_bytes() == (out_old / f"preflight-{key}.json").read_bytes()

    def notices(out: Path) -> list[str]:
        return [x for x in (out / "LOG").read_text().splitlines() if "-NOTICE alias-judge:" in x and TOKENS_IN.search(x)]

    assert len(notices(out_new)) == len(aj.BACKENDS)
    assert [TOKENS_IN.sub("~N tokens in", x) for x in notices(out_new)] == \
        [TOKENS_IN.sub("~N tokens in", x) for x in notices(out_old)]
    changed = [i for i, (a, b) in enumerate(zip(notices(out_new), notices(out_old), strict=True)) if a != b]
    assert len(changed) == 2 and all(" GATEWAY SIZE-NOTICE alias-judge: " in notices(out_new)[i] for i in changed)
