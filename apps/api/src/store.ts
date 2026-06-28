import { randomUUID } from "node:crypto";
import type { AgentResult, Task, TaskStatus } from "@myagent/shared";

/**
 * Minimal in-memory task store. This is intentionally simple for the MVP;
 * swapping it for Postgres/Redis only requires implementing this interface.
 */
export class TaskStore {
  private readonly tasks = new Map<string, Task>();

  create(repoUrl: string, prompt: string): Task {
    const now = new Date().toISOString();
    const task: Task = {
      id: randomUUID(),
      repoUrl,
      prompt,
      status: "queued",
      createdAt: now,
      updatedAt: now,
    };
    this.tasks.set(task.id, task);
    return task;
  }

  get(id: string): Task | undefined {
    return this.tasks.get(id);
  }

  list(): Task[] {
    return [...this.tasks.values()].sort((a, b) =>
      b.createdAt.localeCompare(a.createdAt),
    );
  }

  setStatus(id: string, status: TaskStatus): void {
    const task = this.tasks.get(id);
    if (!task) return;
    task.status = status;
    task.updatedAt = new Date().toISOString();
  }

  complete(id: string, result: AgentResult): void {
    const task = this.tasks.get(id);
    if (!task) return;
    task.status = "completed";
    task.result = result;
    task.updatedAt = new Date().toISOString();
  }

  fail(id: string, error: string): void {
    const task = this.tasks.get(id);
    if (!task) return;
    task.status = "failed";
    task.error = error;
    task.updatedAt = new Date().toISOString();
  }
}
