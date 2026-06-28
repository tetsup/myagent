import { describe, expect, it } from "vitest";
import type { AgentResult } from "@myagent/shared";
import { createApp } from "./app.js";
import { TaskStore } from "./store.js";

const mockResult: AgentResult = {
  summary: "mock summary",
  plan: [{ title: "step", detail: "do the thing" }],
  inspectedFiles: ["README.md"],
  provider: "mock",
  model: "mock-1",
};

function makeApp(runAgent = async () => mockResult) {
  return createApp({ store: new TaskStore(), runAgent });
}

describe("api", () => {
  it("reports health", async () => {
    const res = await makeApp().request("/api/health");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ status: "ok" });
  });

  it("rejects invalid task bodies", async () => {
    const res = await makeApp().request("/api/tasks", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ repoUrl: "" }),
    });
    expect(res.status).toBe(400);
  });

  it("creates a task and stores the agent result", async () => {
    const app = makeApp();
    const res = await app.request("/api/tasks", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        repoUrl: "https://github.com/octocat/Hello-World",
        prompt: "Add a greeting",
      }),
    });
    expect(res.status).toBe(201);
    const { task } = (await res.json()) as { task: { id: string; status: string; result: AgentResult } };
    expect(task.status).toBe("completed");
    expect(task.result.summary).toBe("mock summary");

    const listed = await (await app.request("/api/tasks")).json();
    expect((listed as { tasks: unknown[] }).tasks).toHaveLength(1);
  });

  it("marks a task failed when the agent throws", async () => {
    const app = makeApp(async () => {
      throw new Error("agent down");
    });
    const res = await app.request("/api/tasks", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ repoUrl: "x", prompt: "y" }),
    });
    const { task } = (await res.json()) as { task: { status: string; error: string } };
    expect(task.status).toBe("failed");
    expect(task.error).toContain("agent down");
  });
});
