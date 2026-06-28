# myagent

A Cursor-like GitHub development agent. See `README.md` for the architecture
overview, package table, dev workflow, and LLM configuration.

## Cursor Cloud specific instructions

### Services & how to run them

Three long-running services. Standard commands live in `README.md` and each
`package.json`; the non-obvious bits:

| Service | Port | Start command | Notes |
|---|---|---|---|
| agent (Go) | 8081 | `cd services/agent && go run .` | Start this FIRST |
| api (Hono) | 3001 | `pnpm --filter @myagent/api dev` | needs `AGENT_URL` (default `http://localhost:8081`) |
| web (React) | 5173 | `pnpm --filter @myagent/web dev` | dev server proxies `/api` → `:3001` |

- **Start order matters:** the API dispatches each task to the agent worker
  synchronously, so if the agent is not running, `POST /api/tasks` marks the
  task `failed`. Start agent → api → web.
- **LLM defaults to `mock`** (deterministic, offline) so the full flow runs
  with no secrets/network. For real models set the agent's env:
  `LLM_PROVIDER=ollama|openai`, `LLM_BASE_URL`, `LLM_MODEL`, and
  `LLM_API_KEY` (openai/cloud only).
- The agent shallow-clones the target repo with `git`, so outbound network to
  the repo host is required for non-mock, real-repo runs.

### Gotchas

- **Build `@myagent/shared` before running an app's dev/check directly.**
  `apps/api` (tsx) and `apps/web`/`apps/api` typechecks resolve `@myagent/shared`
  from its built `dist/`. The root turbo tasks handle this via
  `dependsOn: ["^build"]`, but invoking a single package's `dev`/`check` with
  `pnpm --filter ...` does not — run `pnpm build` (or
  `pnpm --filter @myagent/shared build`) once first.
- **esbuild build script** is enabled via `pnpm.onlyBuiltDependencies` in the
  root `package.json`. If Vite/Vitest ever fail with an esbuild binary error,
  run `pnpm rebuild esbuild`.
- The `deploy/` docker-compose and k8s manifests describe the intended on-prem
  deployment but are not exercised in this VM (Docker is not installed here).
