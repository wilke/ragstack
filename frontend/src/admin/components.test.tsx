import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { CtlError } from "./api/http";
import type { CtlErrorCode } from "./api/types";
import { ErrorBanner, extraJobId, messageFor } from "./components/ErrorBanner";
import { duration, jobActions, jobInFlight, JobView } from "./components/JobView";
import { KeyPrompt, KeyPromptView, spendKey } from "./components/KeyPrompt";
import { PlanView, shortHash } from "./components/PlanView";
import { isSecretPath, redactArgv, redactText } from "./components/redact";
import { ownSecrets, RevealOnce, wipeSecrets } from "./components/RevealOnce";
import { JOB_STATES, STEP_STATES, type JobState } from "./api/types";
import { chipTone } from "./components/StateChip";
import { TypedConfirm, TypedConfirmView } from "./components/TypedConfirm";
import { typedConfirmed } from "./lib/confirm";
import {
  artifactsFixture,
  jobFixture,
  jobsFixture,
  planFixture,
  secretsFixture,
  settingsFixture,
  stepLogsFixture,
} from "./fixtures";

// Static-render smoke tests for the PR-G2.2 mutation components, the same
// device as render.test.tsx: no DOM, no fetch. Every component is pure (or has
// a pure seam), so each state is reached through props alone.

function render(node: ReactElement): string {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, retryOnMount: false, staleTime: Infinity } },
  });
  return renderToStaticMarkup(createElement(QueryClientProvider, { client: qc }, node));
}

const noop = () => {};

/** What React's server renderer makes of a text child. */
const esc = (t: string) =>
  t
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/'/g, "&#x27;")
    .replace(/"/g, "&quot;");

function ctlError(code: CtlErrorCode | null, extra?: Record<string, unknown>, status = 409): CtlError {
  return new CtlError({
    status,
    code,
    detail: "DETAIL-PROSE /rag/data/tenants/dev unit ragstack-dev-api.service",
    requestId: "0123456789abcdef",
    extra,
  });
}

// ---------------------------------------------------------------------------

describe("KeyPrompt", () => {
  it("renders a password input that no browser will remember", () => {
    const html = render(createElement(KeyPrompt, { onSubmit: noop }));
    expect(html).toContain('type="password"');
    expect(html).toContain('name="ctl-api-key"');
    expect(html).toContain('autoComplete="off"');
    expect(html).toMatch(/spellcheck="false"/i);
    expect(html).toContain('value=""');
    // Nothing to submit yet.
    expect(html).toMatch(/<button type="submit" disabled=""/);
  });

  it("enables submit once a key is typed, and shows busy and error states", () => {
    const typed = render(
      createElement(KeyPromptView, { value: "k", onChange: noop, onFormSubmit: noop }),
    );
    expect(typed).not.toMatch(/<button type="submit" disabled=""/);

    const busy = render(
      createElement(KeyPromptView, { value: "k", onChange: noop, onFormSubmit: noop, busy: true }),
    );
    expect(busy).toContain("Submitting…");

    const failed = render(
      createElement(KeyPrompt, { onSubmit: noop, error: ctlError("forbidden", undefined, 403) }),
    );
    expect(failed).toContain("operator credential");
    expect(failed).toContain("Reference: 0123456789abcdef");
    expect(failed).not.toContain("DETAIL-PROSE");
  });

  it("renders the caller's context and title", () => {
    const html = render(
      createElement(KeyPrompt, { onSubmit: noop, title: "Key for decommission" }, "About to run 5 steps"),
    );
    expect(html).toContain("Key for decommission");
    expect(html).toContain("About to run 5 steps");
  });

  it("clears the key BEFORE the callback is invoked, and hands it over trimmed", async () => {
    const order: string[] = [];
    const onSubmit = vi.fn((k: string) => {
      order.push(`submit:${k}`);
    });
    await spendKey("  ctl-key-123  ", () => order.push("clear"), onSubmit);
    expect(order).toEqual(["clear", "submit:ctl-key-123"]);
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("clears even when there is nothing to send, and never calls back with an empty key", () => {
    const clear = vi.fn();
    const onSubmit = vi.fn();
    expect(spendKey("   ", clear, onSubmit)).toBeNull();
    expect(clear).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("has already cleared when an async submit rejects, and swallows the rejection", async () => {
    let cleared = false;
    await expect(
      spendKey(
        "k",
        () => {
          cleared = true;
        },
        async () => {
          expect(cleared).toBe(true);
          throw new Error("boom");
        },
      ),
    ).resolves.toBeUndefined();
    expect(cleared).toBe(true);
    // A synchronous throw is contained too.
    await expect(
      spendKey("k", noop, () => {
        throw new Error("sync");
      }),
    ).resolves.toBeUndefined();
  });
});

// ---------------------------------------------------------------------------

describe("TypedConfirm", () => {
  it("confirms only on an exact match, forgiving surrounding whitespace", () => {
    expect(typedConfirmed("dev", "dev")).toBe(true);
    expect(typedConfirmed("  dev\n", "dev")).toBe(true);
    expect(typedConfirmed("Dev", "dev")).toBe(false);
    expect(typedConfirmed("de", "dev")).toBe(false);
    expect(typedConfirmed("dev2", "dev")).toBe(false);
    expect(typedConfirmed("", "")).toBe(false);
  });

  const view = (typed: string, destructive = true) =>
    render(
      createElement(TypedConfirmView, {
        expected: "dev",
        typed,
        onTypedChange: noop,
        onConfirm: noop,
        onCancel: noop,
        destructive,
        warnings: ["open sessions end", "API_KEYS=leaked-api-key-value-0900"],
      }),
    );

  it("keeps the button disabled until the name is typed", () => {
    expect(view("")).toMatch(/<button type="button" disabled=""[^>]*>Confirm destructive run/);
    expect(view("de")).toMatch(/<button type="button" disabled=""[^>]*>Confirm destructive run/);
    expect(view("dev")).not.toMatch(/disabled=""[^>]*>Confirm destructive run/);
  });

  it("is the red box for a destructive plan, with the input browsers leave alone", () => {
    const html = view("");
    expect(html).toContain("border-l-4 border-rust");
    expect(html).toContain("Type <code");
    expect(html).toContain('autoComplete="off"');
    expect(html).toMatch(/spellcheck="false"/i);
    expect(html).toContain("open sessions end");
    expect(html).not.toContain("leaked-api-key-value-0900");
  });

  it("is not red for a non-destructive confirm", () => {
    const html = view("", false);
    expect(html).not.toContain("border-rust");
    expect(html).toContain(">Confirm<");
  });

  it("the stateful wrapper starts empty and locked", () => {
    const html = render(
      createElement(TypedConfirm, { expected: "gateway", onConfirm: noop, onCancel: noop }),
    );
    expect(html).toContain('value=""');
    expect(html).toMatch(/disabled=""[^>]*>Confirm</);
  });
});

// ---------------------------------------------------------------------------

describe("PlanView", () => {
  const html = render(createElement(PlanView, { plan: planFixture }));

  it("renders the header facts the execute call pins", () => {
    expect(html).toContain("decommission");
    expect(html).toContain(">dev<");
    expect(html).toContain(">3<"); // registry generation
    expect(html).toContain(shortHash(planFixture.plan_hash));
    expect(shortHash(planFixture.plan_hash)).toBe("sha256:3f9a3f9a3f9a");
    expect(html).toContain(">yellow<");
    expect(html).toContain("1 finding");
    expect(html).toContain("required: <strong>dev</strong>");
  });

  it("renders every step, destructive ones in red with their warnings", () => {
    for (const s of planFixture.steps) expect(html).toContain(esc(s.title));
    expect(html).toContain("2 destructive");
    expect(html.match(/>destructive</g)?.length).toBe(2);
    expect(html).toContain("open sessions on dev end when the API stops");
    expect(html).toContain("dev has 1 drift row");
  });

  it("shows would_write as path + mode with a collapsible text preview, and argv as code", () => {
    expect(html).toContain("/rag/backups/dev/2026-10-08T120000Z.manifest.json");
    expect(html).toContain("0640");
    expect(html).toMatch(/<details[^>]*><summary[^>]*>preview<\/summary><pre[^>]*>\{\n {2}&quot;tenant&quot;/);
    expect(html).toContain("<code");
    expect(html).toContain("/usr/bin/apptainer instance stop qdrant-dev");
    // A null preview renders no <pre>, just the reason.
    expect(html).toContain("content withheld (credential file)");
  });

  it("escapes a hostile preview rather than interpreting it", () => {
    const plan = {
      ...planFixture,
      steps: [
        {
          ...planFixture.steps[1],
          would_write: [{ path: "/x/y.conf", mode: "0644", preview: "<script>alert(1)</script>" }],
          would_run: [{ argv: ["<img src=x onerror=alert(1)>"] }],
        },
      ],
    };
    const out = render(createElement(PlanView, { plan }));
    expect(out).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(out).not.toContain("<script>");
    expect(out).not.toContain("<img");
  });

  it("says a red doctor will refuse the run", () => {
    const red = render(
      createElement(PlanView, {
        plan: {
          ...planFixture,
          requires_confirm: false,
          confirm_value: null,
          doctor: { ...planFixture.doctor, status: "red" },
        },
      }),
    );
    expect(red).toContain("Doctor is red for this scope");
    expect(red).toContain("not required");
  });
});

// ---------------------------------------------------------------------------

describe("JobView", () => {
  const BUTTONS = ["Resume", "Continue to cutover", "Cancel job"];
  const expected: Record<JobState, string[]> = {
    queued: ["Cancel job"],
    running: ["Cancel job"],
    awaiting_cutover: ["Continue to cutover", "Cancel job"],
    succeeded: [],
    failed: [],
    rolled_back: [],
    interrupted: ["Resume"],
    cancelled: [],
  };

  it.each(JOB_STATES)("renders a %s job with exactly the operator controls that apply", (state) => {
    const html = render(
      createElement(JobView, {
        job: jobFixture(state),
        role: "operator",
        onResume: noop,
        onContinue: noop,
        onCancel: noop,
      }),
    );
    expect(html).toContain(`>${state}<`);
    expect(html).toContain(jobFixture(state).id);
    for (const b of BUTTONS) {
      if (expected[state].includes(b)) expect(html).toContain(`>${b}</button>`);
      else expect(html).not.toContain(`>${b}</button>`);
    }
    // Every step renders with its own chip.
    for (const s of jobFixture(state).steps) expect(html).toContain(esc(s.title));
  });

  it.each(JOB_STATES)("shows a viewer a %s job with no controls", (state) => {
    const html = render(
      createElement(JobView, {
        job: jobFixture(state, { viewer: true }),
        role: "viewer",
        onResume: noop,
        onContinue: noop,
        onCancel: noop,
      }),
    );
    for (const b of BUTTONS) expect(html).not.toContain(`>${b}</button>`);
  });

  it("derives the controls from the contract's state table", () => {
    expect(jobActions("interrupted", "operator")).toEqual(["resume"]);
    expect(jobActions("awaiting_cutover", "operator")).toEqual(["continue", "cancel"]);
    expect(jobActions("running", "operator")).toEqual(["cancel"]);
    expect(jobActions("queued", "operator")).toEqual(["cancel"]);
    for (const s of JOB_STATES) expect(jobActions(s, "viewer")).toEqual([]);
    expect(JOB_STATES.filter(jobInFlight)).toEqual(["queued", "running"]);
  });

  it("renders the error and rollback blocks of a failed job", () => {
    const html = render(createElement(JobView, { job: jobFixture("failed"), role: "operator" }));
    expect(html).toContain("driver_failed");
    expect(html).toContain("step 3");
    expect(html).toContain("apptainer instance stop es-dev: exit status 255");
    expect(html).toContain("rollback");
    expect(html).toContain(">attempted<");
    expect(html).toContain(">partial<");
    expect(html).toContain("the archive bundle is kept");
    expect(html).toContain(">failed<"); // the failed step's chip
    expect(html).toContain("attempts <span");
  });

  it("shows step logs and external ids to an operator only", () => {
    const op = render(
      createElement(JobView, { job: jobFixture("failed"), role: "operator", logs: stepLogsFixture }),
    );
    expect(op).toContain("FATAL: exit status 255");
    expect(op).toContain("bundle:2026-10-08T120000Z");
    expect(op).toMatch(/<pre[^>]*>apptainer: stopping qdrant-dev\napptainer/);

    // Even handed the operator body and the lines, a viewer render prints neither.
    const viewer = render(
      createElement(JobView, { job: jobFixture("failed"), role: "viewer", logs: stepLogsFixture }),
    );
    expect(viewer).not.toContain("FATAL: exit status 255");
    expect(viewer).not.toContain("bundle:2026-10-08T120000Z");
    expect(viewer).not.toContain("steps/3.log");
  });

  it("disables a control whose callback the parent did not wire", () => {
    const html = render(createElement(JobView, { job: jobFixture("interrupted"), role: "operator" }));
    expect(html).toMatch(/disabled=""[^>]*>Resume</);
  });

  it("formats durations", () => {
    expect(duration(null, "2026-10-08T12:00:00Z")).toBe("—");
    expect(duration("2026-10-08T12:00:00Z", "2026-10-08T12:00:42Z")).toBe("42 s");
    expect(duration("2026-10-08T12:00:00Z", "2026-10-08T12:01:05Z")).toBe("1 min 05 s");
    expect(duration("2026-10-08T12:00:00Z", "2026-10-08T14:03:00Z")).toBe("2 h 03 min");
    expect(duration("2026-10-08T12:00:01Z", "2026-10-08T12:00:00Z")).toBe("—");
  });

  it("the jobs fixture covers every state", () => {
    expect(jobsFixture.jobs.map((j) => j.state)).toEqual([...JOB_STATES]);
  });
});

// ---------------------------------------------------------------------------

describe("RevealOnce", () => {
  it("before the click: explains the window and shows no value", () => {
    const html = render(createElement(RevealOnce, { secrets: null, onReveal: noop }));
    expect(html).toContain(">Reveal once</button>");
    expect(html).toContain("15");
    expect(html).toContain("exactly once");
    expect(html).not.toContain("leaked-secret-value");
    expect(html).not.toContain("Shown once");
  });

  it("shows the revealing state and the caller's expiry", () => {
    const html = render(
      createElement(RevealOnce, {
        secrets: null,
        onReveal: noop,
        revealing: true,
        expiresAt: "2026-10-08T12:16:00Z",
      }),
    );
    expect(html).toContain("Revealing…");
    expect(html).toContain("expires 2026-10-08T12:16:00Z");
  });

  it("says what a 410 means here, without the server's detail", () => {
    const html = render(
      createElement(RevealOnce, {
        secrets: null,
        onReveal: noop,
        error: ctlError("not_found", undefined, 410),
      }),
    );
    expect(html).toContain("already delivered");
    expect(html).toContain("Reference: 0123456789abcdef");
    expect(html).not.toContain("DETAIL-PROSE");
  });

  it("after the click: every value with a copy button under the shown-once banner", () => {
    const html = render(createElement(RevealOnce, { secrets: secretsFixture, onReveal: noop }));
    expect(html).toContain("Shown once — store it now");
    expect(html).toContain("bootstrap admin");
    expect(html).toContain("ingest worker");
    expect(html.match(/>Copy<\/button>/g)?.length).toBe(2);
    expect(html).not.toContain(">Reveal once</button>");
  });

  it("wipes its own copy and never the caller's", () => {
    const mine = ownSecrets(secretsFixture);
    wipeSecrets(mine);
    expect(mine.secrets).toEqual([]);
    expect(secretsFixture.secrets.map((s) => s.value)).toEqual([
      "leaked-secret-value-0001",
      "leaked-secret-value-0002",
    ]);
    // The row objects the copy held are blanked too, not just dropped.
    const rows = ownSecrets(secretsFixture);
    const held = rows.secrets[0];
    wipeSecrets(rows);
    expect(held.value).toBe("");
    expect(() => wipeSecrets(null)).not.toThrow();
  });
});

// ---------------------------------------------------------------------------

describe("StateChip job and step kinds", () => {
  it("maps every JobState to its tone", () => {
    const tones = Object.fromEntries(JOB_STATES.map((s) => [s, chipTone("job", s)]));
    expect(tones).toEqual({
      queued: "info",
      running: "info",
      awaiting_cutover: "info",
      succeeded: "ok",
      failed: "bad",
      rolled_back: "bad",
      interrupted: "warn",
      cancelled: "warn",
    });
  });

  it("maps every Step.state to a tone", () => {
    expect(Object.fromEntries(STEP_STATES.map((s) => [s, chipTone("step", s)]))).toEqual({
      pending: "neutral",
      running: "info",
      succeeded: "ok",
      failed: "bad",
      skipped: "neutral",
      rolled_back: "warn",
      interrupted: "warn",
    });
    expect(chipTone("rollback", "partial")).toBe("warn");
  });
});

// ---------------------------------------------------------------------------

describe("ErrorBanner mutation codes", () => {
  const ULID = "01J9Z3K7Q8M4N5P6R7S8T9V0W3";

  it.each([
    ["plan_stale", "The plan changed after you reviewed it"],
    ["duplicate", "already submitted"],
    ["confirm_required", "typed confirmation"],
    ["validation", "rejected the request"],
    ["doctor_red", "Doctor is red"],
    ["locked", "holds a lock this operation needs"],
  ] as [CtlErrorCode, string][])("%s has its own copy and never prints detail", (code, copy) => {
    const html = render(createElement(ErrorBanner, { error: ctlError(code) }));
    expect(html).toContain(copy);
    expect(html).toContain("Reference: 0123456789abcdef");
    expect(html).not.toContain("DETAIL-PROSE");
  });

  it("names the lock holder from extra, in checked shapes only", () => {
    expect(messageFor(ctlError("locked", { job_id: ULID, lock: "tenant", since: "x" }))).toBe(
      `Job ${ULID} holds the tenant lock. Try again when it finishes.`,
    );
    expect(messageFor(ctlError("locked", { principal: "wilke@patricbrc.org" }))).toContain(
      "wilke@patricbrc.org holds a lock",
    );
    // Free text in extra is dropped, not printed.
    const hostile = messageFor(
      ctlError("locked", { job_id: "not a ulid <b>", principal: "two words", lock: "everything" }),
    );
    expect(hostile).toBe("Another job holds a lock this operation needs. Try again when it finishes.");
  });

  it("links a duplicate to the earlier job", () => {
    const err = ctlError("duplicate", { job_id: ULID });
    expect(extraJobId(err)).toBe(ULID);
    const html = render(createElement(ErrorBanner, { error: err, onOpenJob: noop }));
    expect(html).toContain(`job ${ULID}`);
    expect(html).toContain(">Open job</button>");
    // No link without a checkable id.
    const bare = render(createElement(ErrorBanner, { error: ctlError("duplicate"), onOpenJob: noop }));
    expect(bare).not.toContain("Open job");
  });

  it("lists validation field names, never their values", () => {
    const msg = messageFor(ctlError("validation", { fields: ["name", "args.scope", "has space", 42] }));
    expect(msg).toBe("The control plane rejected these arguments: name, args.scope.");
  });
});

// ---------------------------------------------------------------------------

describe("redact", () => {
  it("withholds values next to secret names and keeps the rest", () => {
    expect(redactText("API_KEYS=abc LOG_LEVEL=info")).toBe("API_KEYS=<redacted> LOG_LEVEL=info");
    expect(redactText('{"client_secret": "abc", "port": 1}')).toBe(
      '{"client_secret": <redacted>, "port": 1}',
    );
    expect(redactText("Environment=GOWE_TOKEN=abc")).toBe("Environment=GOWE_TOKEN=<redacted>");
    expect(redactText("password: hunter2")).toBe("password: <redacted>");
    expect(redactText("--api-key=abc")).toBe("--api-key=<redacted>");
    expect(redactText("curl --api-key abc failed")).toBe("curl --api-key <redacted> failed");
    expect(redactText("run --token -v")).toBe("run --token -v");
    expect(redactText("postgresql://u:pw@h:5432/db")).toBe("postgresql://u:<redacted>@h:5432/db");
    // The derived-value allowlist still applies.
    expect(redactText("api_key_fingerprint=sha256:1a2b")).toBe("api_key_fingerprint=sha256:1a2b");
    expect(redactText("plain prose with no assignment")).toBe("plain prose with no assignment");
  });

  it("blanks the argument after a secret-named flag", () => {
    expect(redactArgv(["/usr/bin/x", "--token", "abc", "--name", "dev"])).toEqual([
      "/usr/bin/x",
      "--token",
      "<redacted>",
      "--name",
      "dev",
    ]);
    expect(redactArgv(["--api-key"])).toEqual(["--api-key"]);
  });

  it("knows a credential file by its name", () => {
    expect(isSecretPath("/rag/data/tenants/dev/secrets.env")).toBe(true);
    expect(isSecretPath("/x/api_keys.json")).toBe(true);
    expect(isSecretPath("/x/tenant.env")).toBe(false);
    expect(isSecretPath("/x/RECOVERY.json")).toBe(false);
  });
});

describe("fixtures", () => {
  it("settings and artifacts are present for the later views", () => {
    expect(settingsFixture.recipients.read_only).toBe(true);
    expect(artifactsFixture.artifacts.length).toBeGreaterThan(0);
  });
});
