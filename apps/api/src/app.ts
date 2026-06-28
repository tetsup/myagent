import { Hono } from "hono";
import { cors } from "hono/cors";
import type { CreateTaskBody } from "@myagent/shared";
import type { AgentRunner } from "./agentClient.js";
import { TaskStore } from "./store.js";

export interface AppDeps {
  store: TaskStore;
  runAgent: AgentRunner;
}

function isValidBody(value: unknown): value is CreateTaskBody {
  if (typeof value !== "object" || value === null) return false;
  const body = value as Record<string, unknown>;
  return (
    typeof body.repoUrl === "string" &&
    body.repoUrl.trim().length > 0 &&
    typeof body.prompt === "string" &&
    body.prompt.trim().length > 0
  );
}

export function createApp({ store, runAgent }: AppDeps): Hono {
  const app = new Hono();

  app.use("/api/*", cors());

  app.get("/api/health", (c) => c.json({ status: "ok" }));

  app.get("/api/tasks", (c) => c.json({ tasks: store.list() }));

  app.get("/api/tasks/:id", (c) => {
    const task = store.get(c.req.param("id"));
    if (!task) return c.json({ error: "task not found" }, 404);
    return c.json({ task });
  });

  app.post("/api/tasks", async (c) => {
    const body = await c.req.json().catch(() => null);
    if (!isValidBody(body)) {
      return c.json({ error: "repoUrl and prompt are required" }, 400);
    }

    const task = store.create(body.repoUrl.trim(), body.prompt.trim());
    store.setStatus(task.id, "running");

    try {
      const result = await runAgent({
        taskId: task.id,
        repoUrl: task.repoUrl,
        prompt: task.prompt,
      });
      store.complete(task.id, result);
    } catch (err) {
      store.fail(task.id, err instanceof Error ? err.message : String(err));
    }

    return c.json({ task: store.get(task.id) }, 201);
  });

  return app;
}
