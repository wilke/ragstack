# coconut-proxy bootstrap copies

The two generated includes exactly as the `coconut-proxy` repo ships them on
branch `ctl-generated-includes` (commit `84fd604`, 2026-09-12). `deploy.sh`
writes them into `/rag/config/proxy` as REGULAR files so a freshly deployed
proxy tree loads before `ragstack-ctl` has ever published, and the first
`gateway apply` has to adopt them — replace each with the symlink into
`<state>/gateway/current/` — rather than refuse them as hand edits.

They are copied in verbatim, byte for byte, on purpose. The adoption test
compares against the FILES THAT SHIP, not against a render made in the test:
the whole failure mode being guarded is "the shipped copy and today's render
differ in a line that is not routing", and a test that renders both sides
cannot see it.

Note what they carry and what they do not: the renderer's own first line
(`# … from registry generation 1. DO NOT EDIT.`) and no `ragstack-ctl gateway
generation header` block — they are the output of `ragstack-ctl render nginx`,
not of a publish.
