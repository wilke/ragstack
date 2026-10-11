# TEMPLATE — starting a paper

1. Copy this folder to `docs/papers/paper-[letter]-[slug]/`, then delete the copied `README.md` (a README beside an artifact is reserved for the republish log).
2. Add a roster row for it in `docs/papers/README.md`.
3. Fill `OUTLINE.md` before `DRAFT.md`: the claim, the prior-art table, then the seven sections.
4. Add claims to `claims.json` before writing the sentences that quote them. Fields, `kind` values, pointer syntax and `verified: false` semantics are in the CLAIM SCHEMA and POINTER SYNTAX sections of the `docs/papers/check_claims.py` docstring; a worked file is `docs/papers/paper-a-evidence-localisation/claims.json`.
5. Run `/rag/envs/ragstack/bin/python3 docs/papers/check_claims.py`. It passes vacuously: an empty claims.json, or prose citing ids that don't exist, is not verified.
