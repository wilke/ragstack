# BV-BRC signed-token verifier vectors

Replay fixtures for the two BV-BRC token verifiers — Python
(`python/ragstack/identity/bvbrc.py`, replayed by
`python/tests/unit/test_identity_bvbrc_vectors.py`) and the Go control plane
(`go/internal/ctl/auth/bvbrc.go`, ADR-0007). One file, two implementations, so
the cases that matter (tampering, wrong key, the signed-region boundary,
first-wins parsing, expiry) cannot drift between them.

| File | What it is |
|---|---|
| `test-signing-key.pem` | **TEST-ONLY** 1024-bit RSA private key, PKCS#8, unencrypted. Generated once by the script below and committed on purpose. It has never signed a real token and is not trusted by any BV-BRC deployment; its public half is served only by the fake key servers in the tests. Do not rotate it casually — every vector is signed with it. |
| `test-wrong-key.pem` | **TEST-ONLY** second 1024-bit RSA key, same shape, whose only property is *not being* `test-signing-key.pem`. The `signed_by_another_key` vector is signed with it. Committed rather than generated per run, so regenerating the table is byte-stable. |
| `public_key.json` | `{"pubkey": "<PEM>"}` — the exact body the BV-BRC key server (`https://user.patricbrc.org/public_key`) answers with, for this test key. Only the *signing* key's public half is ever served; the wrong key's public half is never published, which is what makes its signature unverifiable. |
| `vectors.json` | The replay table (`schema_version: 1`). Top level: `signing_subject_url`, `allowlist` (the canonical four subjects from `P3AuthConstants.pm`), a pinned `now` (2025-09-11T00:00:00Z), `vectors[]` and `key_server_variants[]`. |

Each vector is `{name, token, expect}` with optional `allowlist` / `now`
overrides. `expect` is either `{ok: true, subject, token_id, expires_at}` or
`{ok: false, error_kind: invalid|unavailable, reason_substring}`. A replay
harness serves `public_key.json` at every allowlisted URL, pins the clock to
`now`, and asserts the verifier's outcome; `reason_substring` is the Python
error text and is advisory for Go (match it or map it, but assert the kind).

`key_server_variants[]` are raw HTTP bodies (`{name, body, expect_parse_ok}`) a
key server might return — JSON with `pubkey`, a bare PEM, and both with literal
`\n` where newlines belong — and whether the verifier must extract a usable key
from each.

The clock is fixed so the file never rots: valid tokens expire 2100-01-01,
expired ones expired in 2023. Regeneration is **byte-identical** as long as both
committed `.pem` files are in place — two runs produce the same file, and
`python/tests/unit/test_identity_bvbrc_vectors.py::
test_regenerating_the_fixture_is_byte_identical` asserts it, so a diff in this
file always means a deliberate change:

```bash
cd python && PYTHONPATH=$PWD python scripts/gen_bvbrc_vectors.py
```

`--regenerate-key` replaces **both** keys and therefore rewrites every token;
`--out DIR` writes elsewhere. Do not rotate the keys casually — every vector is
signed with them.
