/**
 * Shared contracts for the myagent platform.
 *
 * These types are the single source of truth for the data exchanged between
 * the React web app, the Hono API, and the Go agent worker.
 */

export type TaskStatus = "queued" | "running" | "completed" | "failed";

/** A unit of work: "do X in repo Y" submitted by a user, just like a Cursor task. */
export interface Task {
  id: string;
  repoUrl: string;
  prompt: string;
  status: TaskStatus;
  createdAt: string;
  updatedAt: string;
  result?: AgentResult;
  error?: string;
}

/** Request payload the API sends to the Go agent worker. */
export interface AgentRunRequest {
  taskId: string;
  repoUrl: string;
  prompt: string;
}

/** A single proposed step the agent intends to take to fulfil the prompt. */
export interface PlanStep {
  title: string;
  detail: string;
}

/** Structured output produced by the agent worker for a task. */
export interface AgentResult {
  summary: string;
  plan: PlanStep[];
  /** Files the agent inspected in the cloned repository. */
  inspectedFiles: string[];
  provider: string;
  model: string;
}

/** Body accepted by `POST /api/tasks`. */
export interface CreateTaskBody {
  repoUrl: string;
  prompt: string;
}

export const TASK_STATUSES: readonly TaskStatus[] = [
  "queued",
  "running",
  "completed",
  "failed",
] as const;

export function isTaskStatus(value: unknown): value is TaskStatus {
  return typeof value === "string" && (TASK_STATUSES as readonly string[]).includes(value);
}
