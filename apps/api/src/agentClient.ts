import type { AgentResult, AgentRunRequest } from "@myagent/shared";

/** Drives the compiled (Go) agent worker that actually inspects repos. */
export type AgentRunner = (req: AgentRunRequest) => Promise<AgentResult>;

/**
 * Creates an {@link AgentRunner} backed by the Go agent worker over HTTP.
 */
export function createHttpAgentRunner(agentUrl: string): AgentRunner {
  return async (req: AgentRunRequest): Promise<AgentResult> => {
    const res = await fetch(`${agentUrl.replace(/\/$/, "")}/run`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(req),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`agent worker returned ${res.status}: ${text}`);
    }
    return (await res.json()) as AgentResult;
  };
}
