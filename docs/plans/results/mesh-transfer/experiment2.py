#!/usr/bin/env python
"""Addendum to experiment.py.

(C) the decisive control: is eval B low because the journal was UNSEEN, or because
    FCIM is intrinsically harder to label? Split F in half. Predict F_test twice:
      B'  train = TRAIN                     (journal unseen)
      C   train = TRAIN + F_train           (journal seen, ~1.5k in-journal docs)
    The B'->C gap is the value of having in-journal labelled data; the A->B' gap is
    what the brief asked for. Same test rows in both, so the comparison is exact.
(D) macro-F1 over a COMMON label set (support >= 20 in both A and B), so the macro
    comparison is not confounded by which labels have support where.
(E) bootstrap CIs on every A->B delta.
(F) cost projection.
"""
import json, os, random, collections, time
import numpy as np, torch

D = os.path.dirname(os.path.abspath(__file__))
FCIM = "Frontiers in Cellular and Infection Microbiology"
SEED = 20260915
DEV = "cuda:7"
rows = [json.loads(l) for l in open(f"{D}/labelled.jsonl")]
X = np.load(f"{D}/emb.npy")

idx_f = [i for i, r in enumerate(rows) if r["journal"] == FCIM]
idx_bg = [i for i, r in enumerate(rows) if r["journal"] != FCIM]
rng = random.Random(SEED); rng.shuffle(idx_bg)
nF = len(idx_f)
idx_R = idx_bg[:nF]; idx_tr = idx_bg[nF:]
rng.shuffle(idx_tr)
ndev = max(500, int(0.15 * len(idx_tr)))
idx_dev, idx_fit = idx_tr[:ndev], idx_tr[ndev:]

cnt = collections.Counter()
for i in idx_tr:
    for m in set(x["d"] for x in rows[i]["mesh"]): cnt[m] += 1
vocab = sorted([l for l, c in cnt.items() if c >= 0.01 * len(idx_tr)], key=lambda l: -cnt[l])
L = {l: j for j, l in enumerate(vocab)}

def ymat(idxs):
    Y = np.zeros((len(idxs), len(vocab)), dtype=np.float32)
    for a, i in enumerate(idxs):
        for m in set(x["d"] for x in rows[i]["mesh"]):
            j = L.get(m)
            if j is not None: Y[a, j] = 1.0
    return Y

Xt = torch.tensor(X, device=DEV)
def sub(ix): return Xt[torch.tensor(ix, device=DEV)]

def train_probe(Xtr_, Ytr_, C=10.0, iters=500):
    Xd = Xtr_.double(); Yd = Ytr_.double()
    W = torch.zeros(Xd.shape[1], Yd.shape[1], device=DEV, dtype=torch.float64, requires_grad=True)
    b = torch.zeros(Yd.shape[1], device=DEV, dtype=torch.float64, requires_grad=True)
    prior = Yd.mean(0).clamp(1e-4, 1 - 1e-4)
    with torch.no_grad(): b.copy_(torch.log(prior / (1 - prior)))
    opt = torch.optim.LBFGS([W, b], max_iter=iters, history_size=20,
                            tolerance_grad=1e-9, tolerance_change=1e-12,
                            line_search_fn="strong_wolfe")
    lf = torch.nn.BCEWithLogitsLoss(reduction="sum")
    def cl():
        opt.zero_grad()
        loss = lf(Xd @ W + b, Yd) + (0.5 / C) * (W * W).sum(); loss.backward(); return loss
    opt.step(cl)
    return W.detach().float(), b.detach().float()

def prob(W, b, Xq): return torch.sigmoid(Xq @ W + b).cpu().numpy()

def knn_frac(Xq, Xtr, Ytr_t, k):
    out = []
    for i in range(0, Xq.shape[0], 512):
        S = Xq[i:i + 512] @ Xtr.T
        _, ix = torch.topk(S, k, dim=1)
        out.append(Ytr_t[ix].mean(1))
    return torch.cat(out).cpu().numpy()

def tune_perlabel_th(P, Y, grid=np.arange(0.05, 0.81, 0.025)):
    th = np.full(Y.shape[1], 0.5)
    for j in range(Y.shape[1]):
        y = Y[:, j].astype(bool); p = P[:, j]
        bf, bt = -1.0, 0.5
        for t in grid:
            pr = p >= t
            tp = (pr & y).sum(); fp = (pr & ~y).sum(); fn = (~pr & y).sum()
            f = 2 * tp / max(2 * tp + fp + fn, 1e-9)
            if f > bf: bf, bt = f, t
        th[j] = bt
    return th

def micro(P, Y):
    P = P.astype(bool); Y = Y.astype(bool)
    tp = (P & Y).sum(); fp = (P & ~Y).sum(); fn = (~P & Y).sum()
    p = tp / max(tp + fp, 1e-9); r = tp / max(tp + fn, 1e-9)
    return 2 * p * r / max(p + r, 1e-9)

def macro_on(P, Y, cols):
    P = P.astype(bool); Y = Y.astype(bool)
    fs = []
    for j in cols:
        tp = (P[:, j] & Y[:, j]).sum(); fp = (P[:, j] & ~Y[:, j]).sum(); fn = (~P[:, j] & Y[:, j]).sum()
        fs.append(2 * tp / max(2 * tp + fp + fn, 1e-9))
    return float(np.mean(fs))

Ytr = ymat(idx_tr); Yfit = ymat(idx_fit); Ydev = ymat(idx_dev)
YA = ymat(idx_R); YB = ymat(idx_f)
Xtr_all, Xfit, Xdev, XA, XB = sub(idx_tr), sub(idx_fit), sub(idx_dev), sub(idx_R), sub(idx_f)
Ytr_t = torch.tensor(Ytr, device=DEV); Yfit_t = torch.tensor(Yfit, device=DEV)

K, C = 50, 10.0
TH_P = tune_perlabel_th(prob(*train_probe(Xfit, Yfit_t, C=C), Xdev), Ydev)
TH_K = tune_perlabel_th(knn_frac(Xdev, Xfit, Yfit_t, K), Ydev)

W, b = train_probe(Xtr_all, Ytr_t, C=C)
pA, pB = prob(W, b, XA), prob(W, b, XB)
fA, fB = knn_frac(XA, Xtr_all, Ytr_t, K), knn_frac(XB, Xtr_all, Ytr_t, K)
PA_p, PB_p = pA >= TH_P, pB >= TH_P
PA_k, PB_k = fA >= TH_K, fB >= TH_K

# ---- (D) common-label macro ------------------------------------------------
common = [j for j in range(len(vocab)) if YA[:, j].sum() >= 20 and YB[:, j].sum() >= 20]
print(f"common labels (support>=20 in both A and B): {len(common)} of {len(vocab)}")
print(f"  probe macroF1 on common:  A={macro_on(PA_p, YA, common):.3f}  B={macro_on(PB_p, YB, common):.3f}"
      f"  delta={macro_on(PB_p, YB, common)-macro_on(PA_p, YA, common):+.3f}")
print(f"  kNN   macroF1 on common:  A={macro_on(PA_k, YA, common):.3f}  B={macro_on(PB_k, YB, common):.3f}"
      f"  delta={macro_on(PB_k, YB, common)-macro_on(PA_k, YA, common):+.3f}")

# ---- (E) bootstrap ---------------------------------------------------------
def boot(Pa, Ya, Pb, Yb, n=2000, seed=7):
    r = np.random.default_rng(seed); d = []
    na, nb = Ya.shape[0], Yb.shape[0]
    for _ in range(n):
        ia = r.integers(0, na, na); ib = r.integers(0, nb, nb)
        d.append(micro(Pb[ib], Yb[ib]) - micro(Pa[ia], Ya[ia]))
    d = np.array(d)
    return float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))

print("\n=== bootstrap 95% CI on the A->B micro-F1 delta (2000 resamples) ===")
for tag, Pa, Pb in (("probe", PA_p, PB_p), ("kNN", PA_k, PB_k)):
    lo, hi = boot(Pa, YA, Pb, YB)
    print(f"  {tag:6s} A={micro(Pa,YA):.3f} B={micro(Pb,YB):.3f} "
          f"delta={micro(Pb,YB)-micro(Pa,YA):+.3f}  CI=[{lo:+.3f},{hi:+.3f}]")

# ---- (C) unseen-journal vs seen-journal, SAME test rows --------------------
print("\n=== (C) is B low because the journal is UNSEEN, or because FCIM is harder? ===")
rf = random.Random(SEED + 1); order = idx_f[:]; rf.shuffle(order)
half = len(order) // 2
f_tr, f_te = order[:half], order[half:]
Yfte = ymat(f_te); Xfte = sub(f_te)
print(f"  F_train={len(f_tr)} F_test={len(f_te)} (same rows scored under both models)")

# B' : journal unseen -- reuse the TRAIN-only model
pB2 = prob(W, b, Xfte); fB2 = knn_frac(Xfte, Xtr_all, Ytr_t, K)
# C  : journal seen
idx_trC = idx_tr + f_tr
XtrC = sub(idx_trC); YtrC = ymat(idx_trC); YtrC_t = torch.tensor(YtrC, device=DEV)
WC, bC = train_probe(XtrC, YtrC_t, C=C)
pC = prob(WC, bC, Xfte); fC = knn_frac(Xfte, XtrC, YtrC_t, K)

for tag, unseen, seen, th in (("probe", pB2, pC, TH_P), ("kNN", fB2, fC, TH_K)):
    u = micro(unseen >= th, Yfte); s = micro(seen >= th, Yfte)
    um = macro_on(unseen >= th, Yfte, common); sm = macro_on(seen >= th, Yfte, common)
    print(f"  {tag:6s} F_test microF1: journal UNSEEN={u:.3f}  journal SEEN={s:.3f}  "
          f"gain from in-journal labels={s-u:+.3f}")
    print(f"         F_test macroF1(common): UNSEEN={um:.3f}  SEEN={sm:.3f}  gain={sm-um:+.3f}")

# ---- (F) cost --------------------------------------------------------------
print("\n=== (F) cost ===")
torch.cuda.synchronize(); t0 = time.time()
_ = knn_frac(XB, Xtr_all, Ytr_t, K); torch.cuda.synchronize()
t_knn = time.time() - t0
print(f"  kNN scoring {XB.shape[0]} queries against {Xtr_all.shape[0]} labelled: {t_knn:.3f}s")
torch.cuda.synchronize(); t0 = time.time()
_ = prob(W, b, XB); torch.cuda.synchronize()
print(f"  probe scoring {XB.shape[0]} docs: {time.time()-t0:.4f}s")
json.dump({"common_labels": len(common)}, open(f"{D}/report2.json", "w"))
