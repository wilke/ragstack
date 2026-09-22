# v3 — strict. All patterns CASE-SENSITIVE and word-bounded. Acronym beta-lactamase families
# require an explicit hyphen before the allele number (v2 allowed a space, which matched
# "KPC 69", "FOX 30", "ACT 2601"). blaACT and blaFOX are dropped: "ACT-1" and "FOX-1" collide
# with a transcription factor and with human RBFOX1. Case-sensitivity is deliberate and
# conservative: it lowers the text-hit count, and it is applied identically to the
# PubTator surface forms, so the comparison stays symmetric.
import re
H = r"[-‐‑‒–]"
FAM = ["OXA","CTX-M","KPC","NDM","TEM","SHV","VIM","IMP","CMY","GES","DHA","PER","VEB",
       "CARB","MOX","ADC","IMI","SME","CphA","OKP","LEN","SFO","TLA","BEL","GIM","SPM","AIM"]
RX={}
for f in FAM:
    esc=f.replace("-", H)
    RX["bla"+f]=re.compile(rf"\b(?:bla)?{esc}{H}\d+\b|\bbla{esc}\b")
LOWER = {
 "blaZ":   r"\bblaZ\b",
 "ampC":   r"\b(?:bla)?ampC\b|\bAmpC\b",
 "mecA/C": r"\bmec[AC]\b",
 "vanA-N": r"\bvan[ABCDEGLMN]\b",
 "tet":    r"\btet\(?[ABCDGKLMOQSWXZ]\)?\b",
 "erm":    r"\berm\(?[ABCFTX]\)?\b",
 "qnr":    r"\bqnr[ABCDES]\d*\b",
 "aac6Ib": r"aac\(6['′]\)" + H + r"?Ib(?:" + H + r"?cr)?",
 "aph":    r"\baph\(\d",
 "ant":    r"\bant\(\d",
 "aadA":   r"\baadA\d*\b",
 "sul":    r"\bsul[123]\b",
 "dfrA":   r"\bdfrA\d*\b",
 "mcr":    r"\bmcr" + H + r"?\d+(?:\.\d+)?\b",
 "cfr":    r"\bcfr\b",
 "optrA":  r"\boptrA\b",
 "poxtA":  r"\bpoxtA\b",
 "fosA":   r"\bfosA\d*\b",
 "oqxAB":  r"\boqx[AB]\b",
 "floR":   r"\bfloR\b",
 "catA/B": r"\bcat[AB]\d+\b",
 "cmlA":   r"\bcml[AB]\d*\b",
 "qacE":   r"\bqacEΔ1?\b|\bqacE\b",
 "arr":    r"\barr" + H + r"\d+\b",
 "rmt":    r"\brmt[ABCDEFG]\b",
 "armA":   r"\barmA\b",
 "vga":    r"\bvga\(?[ABCE]\)?\b",
 "lsa":    r"\blsa\(?[ABCE]\)?\b",
 "msr":    r"\bmsr\(?[ABCDE]\)?\b",
 "mph":    r"\bmph\(?[ABCE]\)?\b",
 "strA/B": r"\bstr[AB]\b",
 "gyrA":   r"\bgyrA\b",
 "parC":   r"\bparC\b",
}
for k,v in LOWER.items(): RX[k]=re.compile(v)
