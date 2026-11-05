# Alias study: chunk-boundary control (exploratory)

This control is exploratory, post-freeze and judge-only. It re-judges 300 stage-A pairs against
boundary-respecting control chunks that cover the same text (sentence/512/0 with section cuts), with
the frozen judge. It changes no confirmatory number of the pre-registered study.

The plan (`CONTROL-PLAN.md`, with the owner's answers of 2026-11-05), the pre-judge counts and their
sha256 values were committed **before any judge call**. The JSON files are the pre-judge run's public
outputs, copied byte for byte. Their provenance names commit
`c9af66cc6c9b90c1cf3d6baddd477a894ccbab7e`, with `dirty: false` and `citable: true`. The private
files they hash (the sample, the control chunks, the documents and the pair seeds) are not committed.

| file | sha256 |
|---|---|
| `artifacts/control-draw.json` | `f3ef041cacebf0366f016bc5372c01780a5a4d63b4ec1135dcca7f3dc687db1d` |
| `artifacts/control-chunk.json` | `e6b3064ace4b4d57c4c7fd787bc3affa2ac01a483520943c13f1553d00fa3e20` |
| `artifacts/control-map.json` | `4f5cfaecb6d5329b3c05d4476420958f9435e3ed1b8432cfc90695ab4315d14f` |
| `artifacts/control-check.json` | `3ca213e543623a2e6ffff9bde1f82ab48f3aba0ac1ab15891ca60031c332e61c` |
