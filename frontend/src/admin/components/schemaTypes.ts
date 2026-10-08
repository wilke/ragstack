// Local aliases over the generated contract for the G2.2 components.
//
// TEMPORARY. PR-G2.1 adds the shared aliases (`Plan`, `PlannedStep`, `Job`,
// `Step`, `JobState`, `SecretsResponse`, …) to `api/types.ts`, and PR-G2.3
// switches these components over to them and deletes this file. Until then the
// components import from here so neither piece edits the other's files.
//
// Like `api/types.ts`'s `Body<T>`, the aliases strip the `$defs` member that
// openapi-typescript keeps on each JSON-Schema file's emitted type: a response
// body never carries it. `plan.doctor` is a nested `doctor_response`, so it is
// stripped one level down as well — without that, a fixture (and a real
// response) could not satisfy the type.

import type { components } from "../api/ctlSchema";

type S = components["schemas"];
type Body<T> = Omit<T, "$defs">;

export type PlanDoctor = Body<S["doctor_response"]>;
export type PlanT = Omit<Body<S["plan"]>, "doctor"> & { doctor: PlanDoctor };
export type PlannedStepT = S["PlannedStep"];
export type JobT = Body<S["job"]>;
export type StepT = S["Step"];
export type StepStateT = StepT["state"];
export type JobStateT = S["JobState"];
export type JobsT = Omit<Body<S["jobs_response"]>, "jobs"> & { jobs: JobT[] };
export type SecretsT = Body<S["secrets_response"]>;
export type SecretRowT = SecretsT["secrets"][number];
export type SettingsT = Body<S["settings_response"]>;
export type ArtifactsT = Body<S["artifacts_response"]>;
export type StepLogT = S["StepLogResponse"];

/** Every JobState, in lifecycle order — for fixtures, tests and filters. */
export const JOB_STATES: readonly JobStateT[] = [
  "queued",
  "running",
  "awaiting_cutover",
  "succeeded",
  "failed",
  "rolled_back",
  "interrupted",
  "cancelled",
];

/** Every Step.state, in lifecycle order. */
export const STEP_STATES: readonly StepStateT[] = [
  "pending",
  "running",
  "succeeded",
  "failed",
  "skipped",
  "rolled_back",
  "interrupted",
];
