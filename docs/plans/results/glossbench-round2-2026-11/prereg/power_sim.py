#!/usr/bin/env python3
"""Round-2 planning: power of the paper-clustered sign-flip test (part-4's test) for
paired found@10 contrasts. Offline, stdlib only. Not a result; a planning aid.

Model: P papers x R records. A record is discordant with prob d; a discordant record is a
gain with prob s_paper, where s_paper ~ Beta around s with between-paper spread (ICC-ish).
H1 (superiority): one-sided sign-flip on paper sums at alpha_h (Holm worst case alpha/4).
NI: shift each difference by +delta, one-sided that the mean > 0, true net 0 (s = 0.5).
"""
import random, statistics, sys, json

def paper_sums(P, R, d, s, spread, rng):
    sums = []
    for _ in range(P):
        sp = s if spread == 0 else min(1, max(0, rng.gauss(s, spread)))
        tot = 0
        for _ in range(R):
            if rng.random() < d:
                tot += 1 if rng.random() < sp else -1
        sums.append(tot)
    return sums

def signflip_p(sums, flips, rng, shift=0.0):
    obs = sum(sums) + shift
    ge = 0
    for _ in range(flips):
        v = sum(x if rng.random() < .5 else -x for x in sums) + shift
        if v >= obs:
            ge += 1
    return (1 + ge) / (1 + flips)

def power(P, R, d, s, alpha, sims, flips, spread, delta=0.0, seed=20261029):
    rng = random.Random(seed)
    rej = 0
    for _ in range(sims):
        sums = paper_sums(P, R, d, s, spread, rng)
        # NI: shift every record difference by +delta => shift the total by delta*N
        # (the test is on the total; sign-flip of the unshifted sums, shifted threshold)
        if delta:
            N = P * R
            obs = sum(sums) + delta * N
            ge = 0
            for _ in range(flips):
                v = sum(x if rng.random() < .5 else -x for x in sums)
                if v >= obs:
                    ge += 1
            p = (1 + ge) / (1 + flips)
        else:
            p = signflip_p(sums, flips, rng)
        rej += p <= alpha
    return rej / sims

if __name__ == "__main__":
    P, R = 50, 10          # 25 papers x 2 corpora, 10 accepted records each (500 records)
    alpha = 0.05 / 4       # Holm worst case over a 4-hypothesis family
    sims, flips = 400, 600
    out = {"P": P, "R": R, "alpha_worst_case": alpha, "sims": sims, "flips": flips,
           "spread_between_papers_sd_of_share": 0.15, "H1": {}, "NI": {}}
    for d in (0.05, 0.10, 0.17, 0.25):
        for s in (0.60, 0.70, 0.85):
            out["H1"][f"d{d}_s{s}"] = power(P, R, d, s, alpha, sims, flips, 0.15)
    for d in (0.05, 0.10, 0.17):
        for delta in (0.03, 0.05):
            out["NI"][f"d{d}_delta{delta}_truenet0"] = power(P, R, d, 0.5, alpha, sims, flips, 0.15, delta)
        out["NI"][f"d{d}_delta0.05_truenet-0.02"] = power(P, R, d, 0.5 - 0.01 / d, alpha, sims, flips, 0.15, 0.05)
    # smaller design: 25 papers x 10 (one corpus alone)
    out["H1_one_corpus_P25"] = {f"d{d}_s{s}": power(25, R, d, s, alpha, sims, flips, 0.15)
                                for d in (0.10, 0.17) for s in (0.70, 0.85)}
    print(json.dumps(out, indent=1))
