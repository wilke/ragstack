# Hackathon — start here

Everything you need to use the RAGStack hackathon deployment. You do not need to
clone this repository, install anything, or run a server. If a guide in `docs/`
tells you to run `make` or `uvicorn`, you are in the wrong document — that is for
people building RAGStack, not using it.

**Running code:** `v1.6.2`.

## Your two URLs

| | |
|---|---|
| **UI** | <https://www.bv-brc.org/ragstack/hackathon/ui/> |
| **API** | `https://www.bv-brc.org/ragstack/hackathon/api` |

The trailing slash on the UI is canonical; without it you get a 301 to the
slashed form, so either will work. Deep links fall back to the app.

The tenant you are on is determined by that URL — there is no tenant picker
inside the app. `…/ragstack/hackathon/ui/` talks to `…/ragstack/hackathon/api`
automatically.

## Signing in

**Use your existing BV-BRC account.** Open the UI, use the sign-in control in the
header, and enter your BV-BRC username and password. The password goes from your
browser straight to BV-BRC — it is never sent to RAGStack, which only ever sees
the returned token.

There is no event-specific signup and no pre-provisioned account. Any BV-BRC
account works, gets the `user` role, and can create its own collections on first
login.

If you would rather script against the API, you can paste an existing token
instead, or use `p3-login` from the [BV-BRC CLI](https://www.bv-brc.org/docs/cli_tutorial/),
which writes one to `~/.patric_token`:

```bash
p3-login <your-bvbrc-username>
export BASE=https://www.bv-brc.org/ragstack/hackathon/api
export AUTH="Authorization: $(cat ~/.patric_token)"
```

Note the wire format: the token goes in `Authorization` with **no `Bearer `
prefix**. Never send an API key and a token together — that is a 400, not a
precedence rule.

## What is already here

**A corpus you can query the moment you sign in**, plus whatever you add yourself.

| Collection | What it is |
|---|---|
| `asm-semantic` | American Society for Microbiology journals, semantically chunked — **6,718,269 passages**. |
| `open-access` | The PubMed Central open-access corpus — **47,625,155 passages**, roughly 1.4 million articles. |

Both are shared read-only with everyone, so any BV-BRC login can search them. You
cannot add to them or change them.

> **On retractions in `open-access`:** retraction *notices* and the articles they
> link to were excluded when the corpus was built — 183 articles. Retractions
> marked only by a `RETRACTED ARTICLE:` prefix in the title were **not** caught,
> and those papers are in the index. Check the title and the DOI before you rely
> on a passage. `asm-semantic` had no retraction screening at all.

`asm-semantic` is also this tenant's **default collection**: ask a question
without choosing one and that is what gets searched, in the UI and over the API
alike. You only need to name a collection when you want a different one.

So you can skip straight to asking questions — steps 2 and 3 below are only needed
when you want to search **your own** documents. Anything you create is private to
you until you share it, and lives alongside the shared corpus rather than in it.

## This tenant's limits

These are **overrides for this deployment**. The product defaults documented in
[USER-GUIDE.md](USER-GUIDE.md) and [COOKBOOK.md](COOKBOOK.md) are different
numbers; where they disagree, the table below is what this server enforces.

| Limit | Here | Product default |
|---|---|---|
| Collections you may own at once | **10** | 5 |
| Collections on the whole tenant | **300** | 100 |
| Can ordinary users create collections? | **yes** | yes |
| New collections per hour | **30** | 5 |
| Ingest jobs per hour | **60** | 10 |
| Ingest jobs in flight at once | **1** | 1 |
| Max size of one document | **50 MB** | 50 MB |
| Files per upload | 50 | 50 |
| Total bytes per upload request | 500 MB | 500 MB |

You cannot read these from the API yourself — `GET /v1/config` is admin-only. If
you hit a limit, the server's refusal tells you which one.

## The six things you came to do

Steps 2 and 3 are optional — `asm-semantic` is already there to query.

| | In the browser | From the API |
|---|---|---|
| 1. Sign in | [UI guide § Signing in](UI-GUIDE.md#signing-in) | [cookbook-users.md](cookbook-users.md) |
| 2. Create your own collection | [UI guide § Collections](UI-GUIDE.md#creating-a-collection) | [cookbook-users.md recipe 3](cookbook-users.md) |
| 3. Get documents in | [UI guide § Uploading](UI-GUIDE.md#uploading-documents) | [cookbook-users.md recipe 4](cookbook-users.md) |
| 4. Ask questions | [UI guide § Explore](UI-GUIDE.md#asking-a-question) | [cookbook-users.md recipe 6](cookbook-users.md) |
| 5. Read around a hit | [UI guide § Evidence](UI-GUIDE.md#reading-around-a-hit) | [USER-GUIDE.md § Chunks](USER-GUIDE.md) |
| 6. Share with a teammate | [UI guide § Sharing](UI-GUIDE.md#sharing-a-collection) | [cookbook-users.md recipe 5](cookbook-users.md) |

## Rough edges worth knowing before you hit them

These are real and current. None of them will lose your data.

- **If you are at the 10-collection limit, creating another is refused**, and the
  refusal tells you so with the count. The two ways out are to **delete** a
  collection you no longer need, or to **transfer one away** — and transfer has no
  button, so it is an API call ([cookbook-users.md](cookbook-users.md)). Renaming
  does not help; the limit is on collections you own, not on the name.
  **Prefer deleting**: a transferred collection currently arrives unsearchable for
  its new owner ([#558](https://github.com/wilke/ragstack/issues/558)).
- **The upload box takes PDFs only**, even though the API accepts plain text,
  Markdown and XML. For anything that is not a PDF, use `POST /v1/ingest/upload`
  directly — and **name the content type in the request**, because the server
  checks the declared type, not the file extension. `curl -F files=@notes.md`
  sends `application/octet-stream` and is refused; `-F "files=@notes.md;type=text/markdown"`
  works.
- **`.xml` is accepted and then fails** during processing with "no loader for
  .xml". Convert to text or PDF.
- **One ingest job at a time.** Starting a second while one is running returns
  429. Wait for the first to finish.
- **A failed ingest will not tell you why.** The status goes to `failed` with no
  reason attached; the detail is only visible to an operator. Ask one.
- **Collections cannot be renamed.** The name you give at creation is permanent.
  Deleting and recreating is the only way to change it.
- **Do not let someone else upload into your collection on your behalf.**
  Documents are attributed to whoever uploaded them, and your own searches will
  not match documents an organiser loaded for you. Upload them yourself.

## Where your files go

Uploads on this tenant do **not** land on the RAGStack server. They are written
into **your own BV-BRC Workspace** under `.ragstack/collections/<id>/sources/`
and processed by a workflow running as you. Two consequences:

- You must be signed in with a BV-BRC token. An API key alone gets a 401 on
  upload, however well-formed the request.
- `POST /v1/ingest` here expects a Workspace reference (`ws:///<user>/home/…`),
  not a path on the server.

## Also running

- **Literature console** — <https://www.bv-brc.org/ragstack/litdemo/> — a
  separate BV-BRC-specific app for structured extraction from papers
  (organism/gene/assertion tables). It is not this app and does not read your
  collections. Under active development during the event.

## If something breaks

Note the `Reference:` id shown on the error and give it to an organiser — that is
the one thing that lets them find your request in the logs.

## Other documents

- [UI-GUIDE.md](UI-GUIDE.md) — click-by-click walkthrough of the browser app.
- [cookbook-users.md](cookbook-users.md) — copy-paste API recipes.
- [USER-GUIDE.md](USER-GUIDE.md) — the fuller reference, API-first.
- [API.md](API.md) — complete endpoint reference.

`demo-quickstart.md` and `LOCAL-DEMO.md` are for standing up your own server.
You have one. Do not start there.
