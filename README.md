# myagent

A Cursor-like, GitHub-integrated development agent. A user submits a task
("do X in repo Y"); the platform clones the repository, asks a language model
for a structured plan, and surfaces the result in a web dashboard. The language
model can run on-prem (Ollama / OpenAI-compatible servers such as vLLM in
docker/k8s) or in the cloud.

## Architecture

```
┌────────────┐    /api     ┌────────────┐   /run    ┌────────────────┐   LLM   ┌──────────────┐
│  web        │ ─────────▶ │  api        │ ────────▶ │  agent (Go)     │ ──────▶ │  model server │
│  React+Vite │            │  Hono/Node  │           │  clone + plan   │         │  ollama/cloud │
└────────────┘            └────────────┘           └────────────────┘         └──────────────┘
```

| Package | Path | Stack | Role |
|---|---|---|---|
| `@myagent/web` | `apps/web` | React 19 + Vite | Dashboard to submit tasks and view plans |
| `@myagent/api` | `apps/api` | Hono on Node | Orchestrator: tasks, validation, agent dispatch |
| agent worker | `services/agent` | Go | Clones repos, calls the LLM, returns a plan |
| `@myagent/shared` | `packages/shared` | TypeScript | Shared contracts between web and api |

The agent is a compiled (Go) service so the heavy lifting (repo I/O, model
calls) lives outside Node. Its LLM layer is provider-pluggable: `mock`
(deterministic, offline — the default), `ollama` (on-prem), and `openai`
(cloud / any OpenAI-compatible endpoint such as vLLM).

## Development

Requires Node 22 + pnpm 10 (via corepack) and Go 1.22.

```bash
pnpm install            # install JS deps

# Terminal 1 — Go agent worker (defaults to the offline mock provider)
cd services/agent && go run .

# Terminal 2 — Hono API
pnpm --filter @myagent/api dev

# Terminal 3 — React dashboard (proxies /api -> :3001)
pnpm --filter @myagent/web dev   # http://localhost:5173
```

Repo-wide tasks (via turbo): `pnpm build`, `pnpm lint`, `pnpm test`,
`pnpm check`. Go: `go test ./...` in `services/agent`.

### LLM configuration (agent worker env)

| Var | Values | Default |
|---|---|---|
| `LLM_PROVIDER` | `mock` \| `ollama` \| `openai` | `mock` |
| `LLM_BASE_URL` | model server URL | provider-specific |
| `LLM_MODEL` | model name | provider-specific |
| `LLM_API_KEY` | bearer token (openai) | — |

## Deployment

`deploy/docker-compose.yml` runs the whole stack plus Ollama; `deploy/k8s/`
contains Kubernetes manifests. See `deploy/.env.example` for model config.
