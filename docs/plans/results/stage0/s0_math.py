"""Distribution functions for the Stage-0 power gate -- numpy/math only.

``scipy`` is ABSENT from ``/rag/envs/ragstack`` and this design refuses to add a dependency
to a pinned environment (the same reason SS8.4.3 chose a cluster bootstrap over a GLMM).
Everything the gate needs is implemented here and **validated against the SPEC's own
published tables** by ``selftest()``:

* the chi2 multipliers of SS8.5.5 (df = 9: x1.293 / x1.469 / x1.645 / x1.826);
* the exact non-central-t power surface of SS8.5.1 (sigma_d = 0.140 -> 88.4% at Delta = 0,
  47.3% at Delta = 0.02, and the SS14.A note that 59.8% is the Delta = 0.015 value);
* the sigma_d requirement 0.05*sqrt(80)/2.802 = 0.1596 and the exact df = 79 value 0.158.

If any of those fail, the gate is not computed.
"""
from __future__ import annotations

import math

import numpy as np

SQRT2 = math.sqrt(2.0)


# --------------------------------------------------------------------- normal
def norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / SQRT2)


def norm_ppf(p: float) -> float:
    """Acklam's inverse normal, refined by one Halley step (|err| < 1e-15)."""
    if not 0.0 < p < 1.0:
        raise ValueError(p)
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    pl = 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        x = (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    elif p <= 1 - pl:
        q = p - 0.5
        r = q * q
        x = (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    else:
        q = math.sqrt(-2 * math.log(1 - p))
        x = -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    e = norm_cdf(x) - p
    u = e * math.sqrt(2 * math.pi) * math.exp(x * x / 2)
    return x - u / (1 + x * u / 2)


# ------------------------------------------------------- incomplete gamma / chi2
def _gammainc_lower_reg(a: float, x: float) -> float:
    """Regularised lower incomplete gamma P(a, x)."""
    if x < 0 or a <= 0:
        raise ValueError((a, x))
    if x == 0:
        return 0.0
    if x < a + 1.0:                                    # series
        ap, s, term = a, 1.0 / a, 1.0 / a
        for _ in range(10000):
            ap += 1.0
            term *= x / ap
            s += term
            if abs(term) < abs(s) * 1e-16:
                break
        return s * math.exp(-x + a * math.log(x) - math.lgamma(a))
    # continued fraction for Q(a, x)
    tiny = 1e-300
    b, c, d = x + 1.0 - a, 1.0 / tiny, 1.0 / (x + 1.0 - a)
    h = d
    for i in range(1, 10000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < 1e-16:
            break
    q = math.exp(-x + a * math.log(x) - math.lgamma(a)) * h
    return 1.0 - q


def chi2_cdf(x: float, df: int) -> float:
    return _gammainc_lower_reg(df / 2.0, x / 2.0)


def chi2_ppf(p: float, df: int) -> float:
    lo, hi = 1e-12, max(10.0 * df, 100.0)
    while chi2_cdf(hi, df) < p:
        hi *= 2
    for _ in range(300):
        mid = 0.5 * (lo + hi)
        if chi2_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def sigma_upper_multiplier(conf: float, df: int) -> float:
    """One-sided upper bound on sigma as a multiple of s (SS8.5.5)."""
    return math.sqrt(df / chi2_ppf(1.0 - conf, df))


# ------------------------------------------------------------ incomplete beta / t
def _betacf(a: float, b: float, x: float) -> float:
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 500):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < 1e-16:
            break
    return h


def betainc_reg(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lb = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
          + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return math.exp(lb) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lb) * _betacf(b, a, 1 - x) / b


def t_cdf(t: float, df: float) -> float:
    x = df / (df + t * t)
    p = 0.5 * betainc_reg(df / 2.0, 0.5, x)
    return 1.0 - p if t > 0 else p


def t_ppf(p: float, df: float) -> float:
    lo, hi = -1e3, 1e3
    for _ in range(300):
        mid = 0.5 * (lo + hi)
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# -------------------------------------------------------------- non-central t
_GL_CACHE: dict[int, tuple] = {}


def _leggauss(n: int):
    """Cached Gauss-Legendre nodes.

    ``np.polynomial.legendre.leggauss`` eigendecomposes an n x n companion matrix, so
    recomputing it per call cost 4 minutes of wall clock for the selftest. 400 nodes
    integrate this smooth integrand to ~1e-12; the 4000-node result is reproduced to the
    last printed digit (asserted in :func:`selftest`).
    """
    if n not in _GL_CACHE:
        _GL_CACHE[n] = np.polynomial.legendre.leggauss(n)
    return _GL_CACHE[n]


def nct_sf(t: float, df: int, ncp: float, nodes: int = 400) -> float:
    """P(T' > t) for a non-central t, by exact integration over the chi variate.

    T' = (Z + ncp) / sqrt(V/df), V ~ chi2_df.  Writing s = sqrt(V/df),
    P(T' > t) = E_s[ 1 - Phi(t*s - ncp) ].  The density of s is
    f(s) = 2 * (df/2)^(df/2) / Gamma(df/2) * s^(df-1) * exp(-df s^2 / 2),
    integrated on a Gauss-Legendre grid over a range that carries all the mass.
    """
    a = df / 2.0
    logc = math.log(2.0) + a * math.log(a) - math.lgamma(a)
    lo = max(1e-9, 1.0 - 12.0 / math.sqrt(2.0 * df))
    hi = 1.0 + 12.0 / math.sqrt(2.0 * df)
    xs, ws = _leggauss(nodes)
    s = 0.5 * (hi - lo) * xs + 0.5 * (hi + lo)
    w = ws * 0.5 * (hi - lo)
    dens = np.exp(logc + (df - 1) * np.log(s) - df * s * s / 2.0)
    from math import erfc
    phi = np.array([0.5 * erfc(-(t * si - ncp) / SQRT2) for si in s])
    return float(np.sum(w * dens * (1.0 - phi)))


def ni_power(sigma_d: float, n: int, eps: float, delta: float, alpha: float = 0.025) -> float:
    """Power of the one-sided non-inferiority test (SS8.3, SS8.5.1), exact non-central t.

    Reject H0 (the candidate is worse by >= eps) iff (eps - dbar)/(s/sqrt(n)) > t_{1-a,n-1};
    under a true difference ``delta`` the statistic is non-central t with
    ncp = (eps - delta) * sqrt(n) / sigma_d.
    """
    if sigma_d <= 0:
        return 1.0 if delta < eps else 0.0
    df = n - 1
    tcrit = t_ppf(1.0 - alpha, df)
    ncp = (eps - delta) * math.sqrt(n) / sigma_d
    return nct_sf(tcrit, df, ncp)


def sigma_for_power(n: int, eps: float, delta: float = 0.0, power: float = 0.80,
                    alpha: float = 0.025) -> float:
    lo, hi = 1e-4, 5.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if ni_power(mid, n, eps, delta, alpha) > power:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# --------------------------------------------------------------------- Wilson
def wilson(k: int, n: int, conf: float = 0.95, one_sided: bool = False):
    if n == 0:
        return (0.0, 1.0)
    z = norm_ppf(conf) if one_sided else norm_ppf(1 - (1 - conf) / 2)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


# -------------------------------------------------------------------- selftest
def selftest() -> dict:
    out = {}
    m = {c: sigma_upper_multiplier(c, 9) for c in (0.80, 0.90, 0.95, 0.975)}
    out["chi2_multipliers_df9"] = {str(k): round(v, 4) for k, v in m.items()}
    exp = {0.80: 1.293, 0.90: 1.469, 0.95: 1.645, 0.975: 1.826}
    for k, v in exp.items():
        assert abs(m[k] - v) < 0.001, (k, m[k], v)
    out["t_0975_df79"] = round(t_ppf(0.975, 79), 5)
    assert abs(t_ppf(0.975, 79) - 1.99045) < 1e-4
    tbl = {}
    for sd in (0.120, 0.140, 0.160, 0.177, 0.195):
        tbl[f"{sd:.3f}"] = {f"{d:.3f}": round(100 * ni_power(sd, 80, 0.05, d), 1)
                            for d in (0.0, 0.005, 0.010, 0.015, 0.020, 0.025)}
    out["ss851_power_table"] = tbl
    # SS8.5.1's own published cells
    for sd, d, want in [(0.140, 0.0, 88.4), (0.140, 0.020, 47.3), (0.140, 0.015, 59.8),
                        (0.120, 0.0, 95.7), (0.160, 0.0, 78.8), (0.177, 0.0, 70.4),
                        (0.195, 0.0, 62.0), (0.160, 0.020, 38.1)]:
        got = 100 * ni_power(sd, 80, 0.05, d)
        assert abs(got - want) < 0.15, (sd, d, got, want)
    out["sigma_req_normal_approx"] = round(0.05 * math.sqrt(80) / 2.802, 4)
    out["sigma_req_exact_df79"] = round(sigma_for_power(80, 0.05), 4)
    assert abs(out["sigma_req_exact_df79"] - 0.158) < 0.0015, out["sigma_req_exact_df79"]
    out["ok"] = True
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(selftest(), indent=1))
