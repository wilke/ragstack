# Architecture Decision Records

Each ADR captures **one** significant architectural decision: the context that forced
it, the options weighed, the decision, and the consequences accepted. ADRs are
immutable once accepted — to change a decision, add a new ADR that *supersedes* the
old one, leaving an audit trail of how the system's thinking evolved.

Format follows [Michael Nygard's ADR pattern](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions).

## Index

| # | Title | Status |
|---|---|---|
| [0001](0001-execution-topology.md) | Execution topology: workflow engine, Go, and Python ownership | Proposed |
| [0002](0002-collection-identity.md) | Collection identity: content-addressed stores + a durable registry | Accepted |
| [0003](0003-access-control.md) | Access control: physical tenancy, collection-level ownership, two roles | Accepted |
| [0004](0004-users-groups-shares.md) | Users, groups, and shares: Postgres ACLs with grant-option delegation | Accepted |

## Conventions

- **Filename:** `NNNN-kebab-title.md`, zero-padded, monotonic.
- **Status:** `Proposed` → `Accepted` → (`Deprecated` \| `Superseded by NNNN`).
- **Keep it short** — one screen. Link out to deep docs (`../ARCHITECTURE-DEEP-DIVE.md`) rather than restating them.
- **Never edit an accepted ADR's decision** — supersede it with a new record.
