#!/usr/bin/env python
"""Topic-label transfer experiment.

Identical training set, two test sets:
  A  random in-distribution holdout R drawn from the background pool
  B  every labelled doc of Frontiers in Cellular and Infection Microbiology (FCIM)
TRAIN = BG \\ R in both cases, so the ONLY thing that differs is the test distribution.

Predictors: kNN label propagation (control) and a one-vs-rest linear probe (torch).
Hyperparameters are tuned on an in-distribution dev slice of TRAIN and then applied
unchanged to A and B; the full sweep on both is also printed.
"""
import json, sys, os, math, random, collections
import numpy as np, torch

D = os.path.dirname(os.path.abspath(__file__))
FCIM = "Frontiers in Cellular and Infection Microbiology"
SEED = 20260915
MIN_LABEL_FRAC = 0.01
DEV = "cuda:7"

rows = [json.loads(l) for l in open(f"{D}/labelled.jsonl")]
X = np.load(f"{D}/emb.npy")
assert len(rows) == X.shape[0]

# ---- pools -----------------------------------------------------------------
idx_f = [i for i, r in enumerate(rows) if r["journal"] == FCIM]
idx_bg = [i for i, r in enumerate(rows) if r["journal"] != FCIM]
rng = random.Random(SEED)
rng.shuffle(idx_bg)
nF = len(idx_f)
idx_R = idx_bg[:nF]                 # eval-A test
idx_tr = idx_bg[nF:]                # TRAIN, identical for both evals
rng.shuffle(idx_tr)
ndev = max(500, int(0.15 * len(idx_tr)))
idx_dev = idx_tr[:ndev]
idx_fit = idx_tr[ndev:]

# ---- label vocabulary (from TRAIN only) ------------------------------------
cnt = collections.Counter()
for i in idx_tr:
    for m in set(x["d"] for x in rows[i]["mesh"]):
        cnt[m] += 1
thresh = MIN_LABEL_FRAC * len(idx_tr)
vocab = sorted([l for l, c in cnt.items() if c >= thresh], key=lambda l: -cnt[l])
L = {l: j for j, l in enumerate(vocab)}
print(f"pool={len(rows)} TRAIN={len(idx_tr)} (fit={len(idx_fit)} dev={len(idx_dev)}) "
      f"A|R|={len(idx_R)} B|F|={nF} labels={len(vocab)}")

def ymat(idxs):
    Y = np.zeros((len(idxs), len(vocab)), dtype=np.float32)
    for a, i in enumerate(idxs):
        for m in set(x["d"] for x in rows[i]["mesh"]):
            j = L.get(m)
            if j is not None:
                Y[a, j] = 1.0
    return Y

def coverage(idxs):
    """fraction of true MeSH heading instances that the vocab can even express"""
    tot = inv = 0
    for i in idxs:
        s = set(x["d"] for x in rows[i]["mesh"])
        tot += len(s); inv += sum(1 for m in s if m in L)
    return inv / max(tot, 1), tot / max(len(idxs), 1)

Yfit, Ydev, YA, YB = ymat(idx_fit), ymat(idx_dev), ymat(idx_R), ymat(idx_f)
Ytr = ymat(idx_tr)
covA, densA = coverage(idx_R)
covB, densB = coverage(idx_f)
print(f"vocab coverage of true headings: A={covA:.3f} ({densA:.1f} headings/doc)  "
      f"B={covB:.3f} ({densB:.1f}/doc)")

Xt = torch.tensor(X, device=DEV)
def sub(idxs): return Xt[torch.tensor(idxs, device=DEV)]

# ---- metrics ---------------------------------------------------------------
def metrics(P, Y):
    P = P.astype(bool); Y = Y.astype(bool)
    tp = (P & Y).sum(0).astype(float); fp = (P & ~Y).sum(0).astype(float)
    fn = (~P & Y).sum(0).astype(float)
    miP = tp.sum() / max(tp.sum() + fp.sum(), 1e-9)
    miR = tp.sum() / max(tp.sum() + fn.sum(), 1e-9)
    miF = 2 * miP * miR / max(miP + miR, 1e-9)
    sup = Y.sum(0) > 0
    p = tp / np.maximum(tp + fp, 1e-9); r = tp / np.maximum(tp + fn, 1e-9)
    f = 2 * p * r / np.maximum(p + r, 1e-9)
    return dict(microP=miP, microR=miR, microF1=miF,
                macroP=float(p[sup].mean()), macroR=float(r[sup].mean()),
                macroF1=float(f[sup].mean()), n_lab_sup=int(sup.sum()),
                perlabel_f1=f, perlabel_sup=Y.sum(0))

def tune_perlabel_th(P, Y, grid=np.arange(0.05, 0.81, 0.025)):
    """per-label threshold maximising that label's F1 on the dev slice"""
    th = np.full(Y.shape[1], 0.5)
    for j in range(Y.shape[1]):
        y = Y[:, j].astype(bool); p = P[:, j]
        bestf, bt = -1.0, 0.5
        for t in grid:
            pr = p >= t
            tp = (pr & y).sum(); fp = (pr & ~y).sum(); fn = (~pr & y).sum()
            f = 2 * tp / max(2 * tp + fp + fn, 1e-9)
            if f > bestf: bestf, bt = f, t
        th[j] = bt
    return th

def full_recall(P, idxs):
    """recall against ALL true MeSH headings (incl. ones outside the vocab)"""
    tp = 0; tot = 0
    for a, i in enumerate(idxs):
        truth = set(x["d"] for x in rows[i]["mesh"])
        pred = set(vocab[j] for j in np.nonzero(P[a])[0])
        tp += len(truth & pred); tot += len(truth)
    return tp / max(tot, 1)

# ---- kNN label propagation -------------------------------------------------
def knn_scores(Xq, Xtr, Ytr_t, kmax):
    out = []
    Bq = 512
    for i in range(0, Xq.shape[0], Bq):
        S = Xq[i:i + Bq] @ Xtr.T                      # cosine (rows are unit norm)
        v, ix = torch.topk(S, kmax, dim=1)
        out.append(Ytr_t[ix].mean(1))                 # fraction of k neighbours w/ label
    return torch.cat(out).cpu().numpy()

KS = [10, 25, 50, 100]
THS = [0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5]

def knn_frac(Xq, Xtr, Ytr_t, k):
    out = []
    for i in range(0, Xq.shape[0], 512):
        S = Xq[i:i + 512] @ Xtr.T
        v, ix = torch.topk(S, k, dim=1)
        out.append(Ytr_t[ix].mean(1))
    return torch.cat(out).cpu().numpy()

Xfit, Xdev, XA, XB, Xtr_all = sub(idx_fit), sub(idx_dev), sub(idx_R), sub(idx_f), sub(idx_tr)
Yfit_t = torch.tensor(Yfit, device=DEV)
Ytr_t = torch.tensor(Ytr, device=DEV)

print("\n=== kNN hyperparameter sweep (dev = in-distribution slice of TRAIN) ===")
best = None
dev_frac = {k: knn_frac(Xdev, Xfit, Yfit_t, k) for k in KS}
for k in KS:
    for th in THS:
        m = metrics(dev_frac[k] >= th, Ydev)
        print(f"  k={k:3d} th={th:.2f}  microF1={m['microF1']:.3f} "
              f"P={m['microP']:.3f} R={m['microR']:.3f} macroF1={m['macroF1']:.3f}")
        if best is None or m["microF1"] > best[0]:
            best = (m["microF1"], k, th)
_, K_STAR, TH_STAR = best
print(f"  -> chosen k={K_STAR} th={TH_STAR}")

fracA = {k: knn_frac(XA, Xtr_all, Ytr_t, k) for k in KS}
fracB = {k: knn_frac(XB, Xtr_all, Ytr_t, k) for k in KS}

print("\n=== kNN full sweep on A and B (transparency; selection was on dev) ===")
for k in KS:
    for th in THS:
        a = metrics(fracA[k] >= th, YA); b = metrics(fracB[k] >= th, YB)
        print(f"  k={k:3d} th={th:.2f}  A microF1={a['microF1']:.3f} macroF1={a['macroF1']:.3f}"
              f" | B microF1={b['microF1']:.3f} macroF1={b['macroF1']:.3f}"
              f" | delta={b['microF1']-a['microF1']:+.3f}")

KTH_VEC = tune_perlabel_th(dev_frac[K_STAR], Ydev)
mk = metrics(dev_frac[K_STAR] >= KTH_VEC, Ydev)
print(f"  kNN per-label thresholds on dev: microF1={mk['microF1']:.3f} macroF1={mk['macroF1']:.3f}")
kplA = metrics(fracA[K_STAR] >= KTH_VEC, YA); kplB = metrics(fracB[K_STAR] >= KTH_VEC, YB)
print(f"  kNN per-label th: A microF1={kplA['microF1']:.3f} macroF1={kplA['macroF1']:.3f}"
      f" | B microF1={kplB['microF1']:.3f} macroF1={kplB['macroF1']:.3f}"
      f" | delta={kplB['microF1']-kplA['microF1']:+.3f}")
if mk["microF1"] > best[0]:
    print("  -> kNN per-label thresholds win on dev; using them")
    knnA, knnB = kplA, kplB
    PRED_A_KNN, PRED_B_KNN = fracA[K_STAR] >= KTH_VEC, fracB[K_STAR] >= KTH_VEC
    TH_STAR = "per-label"
else:
    knnA = metrics(fracA[K_STAR] >= TH_STAR, YA)
    knnB = metrics(fracB[K_STAR] >= TH_STAR, YB)
    PRED_A_KNN, PRED_B_KNN = fracA[K_STAR] >= TH_STAR, fracB[K_STAR] >= TH_STAR

# ---- linear probe ----------------------------------------------------------
def train_probe(Xtr_, Ytr_, C=1.0, iters=500):
    """OvR L2-regularised logistic regression, LBFGS to convergence.
    Objective matches sklearn's: sum_i BCE_i + (1/(2C))||W||^2 , in float64."""
    Xd = Xtr_.double(); Yd = Ytr_.double()
    n, d = Xd.shape; l = Yd.shape[1]
    W = torch.zeros(d, l, device=DEV, dtype=torch.float64, requires_grad=True)
    b = torch.zeros(l, device=DEV, dtype=torch.float64, requires_grad=True)
    prior = Yd.mean(0).clamp(1e-4, 1 - 1e-4)
    with torch.no_grad():
        b.copy_(torch.log(prior / (1 - prior)))
    opt = torch.optim.LBFGS([W, b], max_iter=iters, history_size=20,
                            tolerance_grad=1e-9, tolerance_change=1e-12,
                            line_search_fn="strong_wolfe")
    lossf = torch.nn.BCEWithLogitsLoss(reduction="sum")
    def closure():
        opt.zero_grad()
        loss = lossf(Xd @ W + b, Yd) + (0.5 / C) * (W * W).sum()
        loss.backward()
        return loss
    opt.step(closure)
    return W.detach().float(), b.detach().float()


def probe_prob(W, b, Xq):
    return torch.sigmoid(Xq @ W + b).cpu().numpy()

print("\n=== linear probe: C + global threshold tuned on dev (LBFGS, fp64) ===")
bestp = None
devprob = {}
for C in [0.1, 1.0, 10.0, 100.0]:
    W, b = train_probe(Xfit, Yfit_t, C=C)
    pd_ = probe_prob(W, b, Xdev); devprob[C] = pd_
    for th in [0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5]:
        m = metrics(pd_ >= th, Ydev)
        print(f"  C={C:g} th={th:.2f}  microF1={m['microF1']:.3f} "
              f"P={m['microP']:.3f} R={m['microR']:.3f} macroF1={m['macroF1']:.3f}")
        if bestp is None or m["microF1"] > bestp[0]:
            bestp = (m["microF1"], C, th)
_, WD_STAR, PTH_STAR = bestp
print(f"  -> chosen C={WD_STAR:g} th={PTH_STAR}")
TH_VEC = tune_perlabel_th(devprob[WD_STAR], Ydev)
mpl = metrics(devprob[WD_STAR] >= TH_VEC, Ydev)
print(f"  per-label thresholds on dev: microF1={mpl['microF1']:.3f} macroF1={mpl['macroF1']:.3f}")

W, b = train_probe(Xtr_all, Ytr_t, C=WD_STAR)
pA, pB = probe_prob(W, b, XA), probe_prob(W, b, XB)
print("\n=== probe threshold sweep on A and B (transparency) ===")
for th in [0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5]:
    a = metrics(pA >= th, YA); bb = metrics(pB >= th, YB)
    print(f"  th={th:.2f}  A microF1={a['microF1']:.3f} macroF1={a['macroF1']:.3f}"
          f" | B microF1={bb['microF1']:.3f} macroF1={bb['macroF1']:.3f}"
          f" | delta={bb['microF1']-a['microF1']:+.3f}")
plA = metrics(pA >= TH_VEC, YA); plB = metrics(pB >= TH_VEC, YB)
print(f"  per-label th: A microF1={plA['microF1']:.3f} macroF1={plA['macroF1']:.3f}"
      f" | B microF1={plB['microF1']:.3f} macroF1={plB['macroF1']:.3f}"
      f" | delta={plB['microF1']-plA['microF1']:+.3f}")
# keep whichever dev-selected variant was better on dev
if mpl["microF1"] > bestp[0]:
    print("  -> per-label thresholds win on dev; using them")
    prA, prB = plA, plB
    PRED_A_PR, PRED_B_PR = pA >= TH_VEC, pB >= TH_VEC
    PTH_STAR = "per-label"
else:
    prA = metrics(pA >= PTH_STAR, YA)
    prB = metrics(pB >= PTH_STAR, YB)
    PRED_A_PR, PRED_B_PR = pA >= PTH_STAR, pB >= PTH_STAR

print("\n=== recall against ALL true headings (vocab ceiling included) ===")
for tag, P, idxs, cov in (("A knn", PRED_A_KNN, idx_R, covA), ("B knn", PRED_B_KNN, idx_f, covB),
                          ("A probe", PRED_A_PR, idx_R, covA), ("B probe", PRED_B_PR, idx_f, covB)):
    print(f"  {tag:8s} full-MeSH recall={full_recall(P, idxs):.3f}  (vocab ceiling {cov:.3f})")

# ---- prior baseline: always predict the n most frequent TRAIN labels --------
print("\n=== prior baseline (always predict top-n TRAIN labels) ===")
order = np.argsort(-Ytr.sum(0))
base = {}
for n_ in [1, 3, 5, 10]:
    PA = np.zeros_like(YA); PB = np.zeros_like(YB)
    PA[:, order[:n_]] = 1; PB[:, order[:n_]] = 1
    a = metrics(PA, YA); bb = metrics(PB, YB)
    print(f"  top{n_:2d}  A microF1={a['microF1']:.3f} | B microF1={bb['microF1']:.3f}")
    base[n_] = (a, bb)

# ---- report ----------------------------------------------------------------
def line(name, m):
    return (f"{name:28s} microF1={m['microF1']:.3f} (P={m['microP']:.3f} R={m['microR']:.3f}) "
            f"macroF1={m['macroF1']:.3f} (P={m['macroP']:.3f} R={m['macroR']:.3f}) "
            f"labels_with_support={m['n_lab_sup']}")

print("\n================ HEADLINE ================")
print(line(f"kNN k={K_STAR} th={TH_STAR}  A", knnA))
print(line(f"kNN k={K_STAR} th={TH_STAR}  B", knnB))
print(f"  kNN   A->B delta microF1 = {knnB['microF1']-knnA['microF1']:+.3f} "
      f"({100*(knnB['microF1']-knnA['microF1'])/knnA['microF1']:+.1f}%)  "
      f"macroF1 = {knnB['macroF1']-knnA['macroF1']:+.3f}")
print(line(f"probe wd={WD_STAR:g} th={PTH_STAR}  A", prA))
print(line(f"probe wd={WD_STAR:g} th={PTH_STAR}  B", prB))
print(f"  probe A->B delta microF1 = {prB['microF1']-prA['microF1']:+.3f} "
      f"({100*(prB['microF1']-prA['microF1'])/prA['microF1']:+.1f}%)  "
      f"macroF1 = {prB['macroF1']-prA['macroF1']:+.3f}")
print(f"  probe - kNN on A = {prA['microF1']-knnA['microF1']:+.3f}; "
      f"on B = {prB['microF1']-knnB['microF1']:+.3f}")

# ---- per-label diagnostics -------------------------------------------------
print("\n=== per-label transfer (labels with >=20 true positives in BOTH tests) ===")
fa, fb = prA["perlabel_f1"], prB["perlabel_f1"]
sa, sb = prA["perlabel_sup"], prB["perlabel_sup"]
kfa, kfb = knnA["perlabel_f1"], knnB["perlabel_f1"]
rowsd = [(vocab[j], sa[j], sb[j], fa[j], fb[j], fb[j] - fa[j], kfa[j], kfb[j])
         for j in range(len(vocab)) if sa[j] >= 20 and sb[j] >= 20]
rowsd.sort(key=lambda t: t[5])
print(f"{'label':45s} {'supA':>6s} {'supB':>6s} {'probeA':>7s} {'probeB':>7s} {'d':>7s} {'knnA':>6s} {'knnB':>6s}")
for t in rowsd[:20]:
    print(f"{t[0][:45]:45s} {t[1]:6.0f} {t[2]:6.0f} {t[3]:7.3f} {t[4]:7.3f} {t[5]:+7.3f} {t[6]:6.3f} {t[7]:6.3f}")
print("  ... best-transferring:")
for t in rowsd[-10:]:
    print(f"{t[0][:45]:45s} {t[1]:6.0f} {t[2]:6.0f} {t[3]:7.3f} {t[4]:7.3f} {t[5]:+7.3f} {t[6]:6.3f} {t[7]:6.3f}")

print("\n=== labels predictable at all (probe, B) ===")
for tag, m in (("A", prA), ("B", prB)):
    f = m["perlabel_f1"]; s = m["perlabel_sup"]
    for cut in (0.3, 0.5):
        print(f"  {tag}: labels with support>0 and F1>={cut}: "
              f"{int(((f >= cut) & (s > 0)).sum())}/{int((s > 0).sum())}")

# ---- prevalence shift ------------------------------------------------------
print("\n=== biggest prevalence shift TRAIN -> B ===")
ptr = Ytr.mean(0); pb = YB.mean(0)
sh = sorted(range(len(vocab)), key=lambda j: -(pb[j] - ptr[j]))
print("  over-represented in FCIM:")
for j in sh[:10]:
    print(f"    {vocab[j][:45]:45s} train={ptr[j]:.3f} B={pb[j]:.3f}")
print("  under-represented in FCIM:")
for j in sh[-10:]:
    print(f"    {vocab[j][:45]:45s} train={ptr[j]:.3f} B={pb[j]:.3f}")

json.dump({"K_STAR": K_STAR, "TH_STAR": TH_STAR, "WD_STAR": WD_STAR, "PTH_STAR": PTH_STAR,
           "n_pool": len(rows), "n_train": len(idx_tr), "n_A": len(idx_R), "n_B": nF,
           "n_labels": len(vocab), "covA": covA, "covB": covB,
           "densA": densA, "densB": densB,
           "knnA": {k: v for k, v in knnA.items() if not k.startswith("perlabel")},
           "knnB": {k: v for k, v in knnB.items() if not k.startswith("perlabel")},
           "probeA": {k: v for k, v in prA.items() if not k.startswith("perlabel")},
           "probeB": {k: v for k, v in prB.items() if not k.startswith("perlabel")},
           "vocab": vocab},
          open(f"{D}/report.json", "w"), indent=2, default=float)
print("\nwrote report.json")
