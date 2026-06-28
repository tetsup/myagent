import { serve } from "@hono/node-server";
import { createApp } from "./app.js";
import { createHttpAgentRunner } from "./agentClient.js";
import { TaskStore } from "./store.js";

const port = Number(process.env.PORT ?? 3001);
const agentUrl = process.env.AGENT_URL ?? "http://localhost:8081";

const app = createApp({
  store: new TaskStore(),
  runAgent: createHttpAgentRunner(agentUrl),
});

serve({ fetch: app.fetch, port }, (info) => {
  console.log(`[api] listening on http://localhost:${info.port}`);
  console.log(`[api] agent worker: ${agentUrl}`);
});
