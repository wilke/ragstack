"""Alias benchmark: the wave planner keyed on ACCEPTED records (pure half: no I/O, no
model, no network). The runner is ``glossbench_alias_draft.py run-waves``.

**Why.** The paid pilot (2026-10-30) stopped drafting a paper once ``goal`` jobs of a kind
passed LAYER 1; the verifier then rejected most of them, and only 1 of 12 papers reached
2 G + 1 C. Waves count only ACCEPTED records (layers 1 and 2).

**A paper is satisfied** when it has ``goal.G`` accepted G records on DISTINCT aliases
(:func:`glossbench_alias_checks.alias_key`, the shared matcher's normalisation) and
``goal.C`` accepted C records.

**One wave.** For every ACTIVE paper that is neither satisfied nor exhausted, the next
jobs still needed, in the pool's seeded order (``rank``): G — ``goal.G`` minus the
distinct aliases already accepted, at most one job per alias in a wave, and never a job
on an alias that is already accepted (those are skipped for good); C — ``goal.C`` minus
the accepted C. With ``c_first`` (the default) a paper drafts only its C jobs until its
C goal is met, and its G jobs after: a paper has at most two C candidates (the pool's
``c_per_paper``) against up to six G, so C is the cheap gate — a paper whose C jobs all
fail is retired before any G job is spent on it. On the real pool (set O, 796 reachable
papers, the pilot's rates) this drafts ~23% fewer jobs for the same target (review of
#889: an independent Monte Carlo, 1,446 -> 1,114 jobs for 100 papers), at the price of
up to ``goal.C x 2`` extra waves. The satisfaction rule is unchanged, so the set of
papers that can be satisfied is the same. A paper is EXHAUSTED (nothing more is drafted for it, either kind) when
its remaining jobs can no longer satisfy it: fewer distinct unaccepted aliases than the
G still needed, or fewer C jobs than the C still needed, or the per-paper cap
(``max_jobs_per_paper``) is spent.

**Activation.** Papers are activated in a seeded order (``<pool seed>|waves`` over the
REACHABLE papers: at least ``goal.G`` distinct aliases among their G jobs and ``goal.C``
C jobs), only as needed: before each wave, papers are added until the expected number
of satisfied papers (satisfied ones count 1; an open paper counts its probability of
being satisfied by its remaining jobs, :func:`p_satisfy`) reaches the target, at most
``max_new_papers_per_wave`` in one wave. The rates are the run's measured per-kind
acceptance per drafted job, blended with the prior (``accept_rate``, weight
``prior_weight`` jobs): :func:`blend`. Conservative by construction: activation never
exceeds what the measured rate says the target needs; a shortfall is topped up in the
next wave, never pre-paid.

**Stops** (the planner's): ``target_met`` (satisfied papers >= ``target_papers``),
``pool_exhausted`` (no job left to plan), ``max_waves``. The runner adds the budget stop,
PAUSE, ``max_calls`` and every other existing pause.
"""
from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

KINDS = ("G", "C")

WAVE_DEFAULTS: dict[str, Any] = {
    "target_papers": 100,
    #: the prior per DRAFTED job (layers 1-2): the 2026-10-30 pilot, G 10/28, C 6/16
    "accept_rate": {"G": 0.36, "C": 0.38},
    "prior_weight": 20,
    "max_new_papers_per_wave": 150,
    "max_waves": 40,
    "max_jobs_per_paper": {"G": None, "C": None},
    #: preflight cost model (the pilot's realised numbers)
    "calls_per_job": 1.32,
    "prompt_tokens_per_call": 700,
    "completion_tokens_per_call": 1780,
    #: layer-1 passes per drafted job (each is one verifier CLI call): pilot G 25/28, C 11/16
    "l1_pass_per_job": {"G": 0.89, "C": 0.69},
    #: the pessimistic preflight scenario: the accept rates times this factor
    "pessimistic_factor": 0.67,
    "preflight_sims": 400,
    #: draft a paper's C jobs first and its G jobs only once its C goal is met (module docstring)
    "c_first": True,
}


def waves_config(given: Mapping[str, Any] | None) -> dict[str, Any]:
    g = dict(given or {})
    unknown = set(g) - set(WAVE_DEFAULTS)
    if unknown:
        raise SystemExit(f"config block 'waves': unknown keys {sorted(unknown)}")
    c = {**WAVE_DEFAULTS, **g}
    for k in ("accept_rate", "max_jobs_per_paper", "l1_pass_per_job"):
        c[k] = {**WAVE_DEFAULTS[k], **(g.get(k) or {})}
    for kind in KINDS:
        for blk in ("accept_rate", "l1_pass_per_job"):
            v = c[blk][kind]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < float(v) <= 1:
                raise SystemExit(f"waves.{blk}.{kind} {v!r}: a number in (0, 1]")
        cap = c["max_jobs_per_paper"][kind]
        if cap is not None and (isinstance(cap, bool) or int(cap) < 1):
            raise SystemExit(f"waves.max_jobs_per_paper.{kind} {cap!r}: null or at least 1")
    for k in ("target_papers", "max_new_papers_per_wave", "max_waves", "preflight_sims"):
        if isinstance(c[k], bool) or int(c[k]) < 1:
            raise SystemExit(f"waves.{k} {c[k]!r}: at least 1")
    if not isinstance(c["c_first"], bool):
        raise SystemExit(f"waves.c_first {c['c_first']!r}: true or false")
    if not 0 < float(c["pessimistic_factor"]) <= 1:
        raise SystemExit(f"waves.pessimistic_factor {c['pessimistic_factor']!r}: in (0, 1]")
    return c


@dataclass
class PaperState:
    """One paper's jobs (pool order) and what is known of them."""

    paper: str
    g: list[tuple[str, str]] = field(default_factory=list)     # (job_id, alias key), rank order
    c: list[str] = field(default_factory=list)                 # job_id, rank order
    drafted: set[str] = field(default_factory=set)             # drafted jobs (any outcome)
    acc_keys: set[str] = field(default_factory=set)            # aliases with an accepted G
    acc_c: int = 0

    def copy(self) -> PaperState:
        return PaperState(self.paper, self.g, self.c, set(self.drafted), set(self.acc_keys), self.acc_c)


def need(ps: PaperState, goal: Mapping[str, int]) -> tuple[int, int]:
    return max(0, int(goal["G"]) - len(ps.acc_keys)), max(0, int(goal["C"]) - ps.acc_c)


def satisfied(ps: PaperState, goal: Mapping[str, int]) -> bool:
    return need(ps, goal) == (0, 0)


def remaining(ps: PaperState, caps: Mapping[str, int | None]) -> tuple[list[tuple[str, str]], list[str]]:
    """The jobs still draftable: not drafted, G not on an accepted alias, within the caps."""
    rg = [(j, k) for j, k in ps.g if j not in ps.drafted and k not in ps.acc_keys]
    rc = [j for j in ps.c if j not in ps.drafted]
    if caps.get("G") is not None:
        rg = rg[:max(0, int(caps["G"]) - sum(j in ps.drafted for j, _ in ps.g))]  # type: ignore[arg-type]
    if caps.get("C") is not None:
        rc = rc[:max(0, int(caps["C"]) - sum(j in ps.drafted for j in ps.c))]  # type: ignore[arg-type]
    return rg, rc


def reachable(ps: PaperState, goal: Mapping[str, int]) -> bool:
    """A fresh paper can be satisfied at all (distinct aliases, enough C jobs)."""
    return len({k for _, k in ps.g}) >= int(goal["G"]) and len(ps.c) >= int(goal["C"])


def plan_paper(ps: PaperState, goal: Mapping[str, int], caps: Mapping[str, int | None],
               c_first: bool = False) -> list[str] | None:
    """The paper's jobs for the next wave; ``[]`` when satisfied, None when exhausted.
    With ``c_first`` only C jobs while the C goal is unmet (feasibility is judged on both)."""
    ng, nc = need(ps, goal)
    if (ng, nc) == (0, 0):
        return []
    rg, rc = remaining(ps, caps)
    if len({k for _, k in rg}) < ng or len(rc) < nc:
        return None
    if c_first and nc:
        return rc[:nc]
    picks: list[str] = []
    keys: set[str] = set()
    for j, k in rg:
        if len(keys) == ng:
            break
        if k not in keys:
            keys.add(k)
            picks.append(j)
    return picks + rc[:nc]


def prob_at_least(qs: Sequence[float], k: int) -> float:
    """P(at least ``k`` of independent events with probabilities ``qs``)."""
    if k <= 0:
        return 1.0
    dist = [1.0] + [0.0] * k                       # dist[i] = P(i successes), i == k means >= k
    for q in qs:
        new = [0.0] * (k + 1)
        for i, pr in enumerate(dist):
            if not pr:
                continue
            new[i] += pr * (1 - q)
            new[min(k, i + 1)] += pr * q
        dist = new
    return dist[k]


def p_satisfy(ps: PaperState, goal: Mapping[str, int], rates: Mapping[str, float],
              caps: Mapping[str, int | None]) -> float:
    """The probability that the paper's remaining jobs satisfy it, each job accepted
    independently at its kind's rate (an alias succeeds when any of its jobs does)."""
    ng, nc = need(ps, goal)
    if (ng, nc) == (0, 0):
        return 1.0
    rg, rc = remaining(ps, caps)
    per_key = Counter(k for _, k in rg)
    pg = prob_at_least([1 - (1 - float(rates["G"])) ** n for n in per_key.values()], ng)
    pc = prob_at_least([float(rates["C"])] * len(rc), nc)
    return pg * pc


def blend(accepted: int, drafted: int, prior: float, weight: float) -> float:
    """The measured acceptance per drafted job, shrunk toward the prior by ``weight`` jobs."""
    return (accepted + weight * prior) / (drafted + weight)


def activate(states: Mapping[str, PaperState], order: Sequence[str], n_active: int, target: int,
             rates: Mapping[str, float], goal: Mapping[str, int], caps: Mapping[str, int | None],
             max_new: int) -> int:
    """The new number of active papers (a prefix of ``order``): add papers until the
    expected satisfied papers reach ``target``, at most ``max_new`` in one wave."""
    exp = sum(p_satisfy(states[p], goal, rates, caps) for p in order[:n_active])
    n = n_active
    while exp < target and n < len(order) and n - n_active < max_new:
        exp += p_satisfy(states[order[n]], goal, rates, caps)
        n += 1
    return n


def plan_wave(states: Mapping[str, PaperState], order: Sequence[str], n_active: int, target: int,
              rates: Mapping[str, float], goal: Mapping[str, int], caps: Mapping[str, int | None],
              max_new: int, c_first: bool = False) -> tuple[int, list[str], str | None]:
    """(new active count, the wave's job ids, a stop reason or None)."""
    if sum(satisfied(states[p], goal) for p in order[:n_active]) >= target:
        return n_active, [], "target_met"
    n = activate(states, order, n_active, target, rates, goal, caps, max_new)
    jobs: list[str] = []
    for p in order[:n]:
        jobs += plan_paper(states[p], goal, caps, c_first) or []
    if not jobs:
        return n, [], "pool_exhausted"
    return n, jobs, None


def simulate(states: Mapping[str, PaperState], order: Sequence[str], n_active: int, job_info: Mapping[str, tuple[str, str, str]],
             rates: Mapping[str, float], goal: Mapping[str, int], caps: Mapping[str, int | None], target: int,
             max_new: int, max_waves: int, wave0: int, rng: random.Random, c_first: bool = False) -> dict[str, Any]:
    """One simulated run of the wave policy from the current state; ``job_info`` maps a job
    id to (paper, kind, alias key); each job is accepted at its kind's rate (the planner
    uses the same rates). Papers are copied only when touched."""
    view = _View(states)
    jobs: Counter[str] = Counter()
    wave, stop = wave0, None
    while True:
        if wave >= max_waves:
            stop = "max_waves"
            break
        n_active, todo, stop = plan_wave(view, order, n_active, target, rates, goal, caps, max_new, c_first)
        if stop:
            break
        wave += 1
        for j in todo:
            paper, kind, key = job_info[j]
            jobs[kind] += 1
            ps = view[paper]
            ps.drafted.add(j)
            if rng.random() < float(rates[kind]):
                if kind == "G":
                    ps.acc_keys.add(key)
                else:
                    ps.acc_c += 1
    sat = sum(satisfied(view[p], goal) for p in order[:n_active])
    return {"jobs": dict(jobs), "waves": wave - wave0, "satisfied": sat, "activated": n_active, "stop": stop}


class _View(dict):  # type: ignore[type-arg]
    """The simulation's papers: the real states, each copied on first access."""

    def __init__(self, base: Mapping[str, PaperState]) -> None:
        super().__init__()
        self.base = base

    def __missing__(self, p: str) -> PaperState:
        ps = self.base[p].copy()
        self[p] = ps
        return ps


def forecast(states: Mapping[str, PaperState], order: Sequence[str], n_active: int,
             job_info: Mapping[str, tuple[str, str, str]], rates: Mapping[str, float], goal: Mapping[str, int],
             caps: Mapping[str, int | None], target: int, max_new: int, max_waves: int, wave0: int, sims: int,
             seed: str, c_first: bool = False) -> dict[str, Any]:
    """``sims`` seeded runs of :func:`simulate`: the mean and the 95th percentile of the
    jobs drafted (per kind and total), waves, papers activated, and P(target met)."""
    rng = random.Random(seed)
    runs = [simulate(states, order, n_active, job_info, rates, goal, caps, target, max_new, max_waves, wave0, rng,
                     c_first)
            for _ in range(int(sims))]

    def q95(xs: list[float]) -> float:
        xs = sorted(xs)
        return xs[min(len(xs) - 1, math.ceil(0.95 * len(xs)) - 1)] if xs else 0.0

    tot = [float(sum(r["jobs"].values())) for r in runs]
    return {"sims": len(runs), "rates": {k: round(float(rates[k]), 4) for k in KINDS},
            "jobs_mean": round(sum(tot) / len(tot), 2) if tot else 0.0, "jobs_p95": q95(tot),
            "jobs_mean_by_kind": {k: round(sum(r["jobs"].get(k, 0) for r in runs) / len(runs), 2) if runs else 0.0
                                  for k in KINDS},
            "waves_mean": round(sum(r["waves"] for r in runs) / len(runs), 2) if runs else 0.0,
            "waves_p95": q95([float(r["waves"]) for r in runs]),
            "papers_activated_mean": round(sum(r["activated"] for r in runs) / len(runs), 1) if runs else 0.0,
            "papers_activated_p95": q95([float(r["activated"]) for r in runs]),
            "p_target_met": round(sum(r["satisfied"] >= target for r in runs) / len(runs), 3) if runs else 0.0,
            "stops": dict(Counter(str(r["stop"]) for r in runs))}
