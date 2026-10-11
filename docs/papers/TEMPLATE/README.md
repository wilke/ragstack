# TEMPLATE — starting a paper

1. Copy this folder to `docs/papers/paper-<letter>-<slug>/`.
2. Add a roster row for it in `docs/papers/README.md`.
3. Fill `OUTLINE.md` before `DRAFT.md`: the claim, the prior-art table, then the seven sections.
4. Add claims to `claims.json` before writing the sentences that quote them (schema: see
   `paper-a-evidence-localisation/claims.json`; fields `id`, `section`, `statement`, `value`,
   `source`, `pointer`, `kind`, `verified`, `note`).
5. Run `/rag/envs/ragstack/bin/python3 docs/papers/check_claims.py`; it passes on this empty file.
