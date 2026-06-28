import { afterEach, describe, expect, it, vi } from "vitest";
import type { Task } from "@myagent/shared";
import { createTask, listTasks } from "./api.js";

const sampleTask: Task = {
  id: "1",
  repoUrl: "https://github.com/octocat/Hello-World",
  prompt: "do a thing",
  status: "completed",
  createdAt: "2024-01-01T00:00:00.000Z",
  updatedAt: "2024-01-01T00:00:00.000Z",
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("api client", () => {
  it("lists tasks", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ tasks: [sampleTask] }), { status: 200 })),
    );
    const tasks = await listTasks();
    expect(tasks).toHaveLength(1);
    expect(tasks[0].id).toBe("1");
  });

  it("throws the server error message on failed create", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ error: "bad" }), { status: 400 })),
    );
    await expect(createTask({ repoUrl: "x", prompt: "y" })).rejects.toThrow("bad");
  });
});
