import type { CreateTaskBody, Task } from "@myagent/shared";

const BASE = "/api";

export async function listTasks(): Promise<Task[]> {
  const res = await fetch(`${BASE}/tasks`);
  if (!res.ok) throw new Error(`list failed: ${res.status}`);
  const data = (await res.json()) as { tasks: Task[] };
  return data.tasks;
}

export async function createTask(body: CreateTaskBody): Promise<Task> {
  const res = await fetch(`${BASE}/tasks`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = (await res.json()) as { task?: Task; error?: string };
  if (!res.ok || !data.task) {
    throw new Error(data.error ?? `create failed: ${res.status}`);
  }
  return data.task;
}
