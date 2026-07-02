# myagent

> **日本語:** [README.ja.md](./README.ja.md)

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

## mini-cursor (AWS) — Getting started

Use this flow to operate the agent from a phone over Cognito-authenticated API Gateway.

### 1. Deploy the infrastructure

Run Terraform in `infra/` and save the output values.

```bash
cd infra
terraform init
terraform apply
```

After `apply` completes, note these outputs:

| Output | Use |
|---|---|
| `cognito_user_pool_id` | Web UI **User Pool ID** |
| `cognito_user_pool_client_id` | Web UI **App Client ID** |
| `api_endpoint` | Web UI **API Gateway URL** (`POST /agent`) |
| `status_endpoint` | Task progress polling (`GET /status`) |
| `github_token_ssm_parameter` | SSM parameter name to overwrite in the next step |

### 2. Set secrets and users with the AWS CLI

Store the GitHub token in SSM Parameter Store and create the Cognito user from the CLI. Use only the three commands below so the token never ends up in shell history or chat logs.

Export values from `terraform apply` first (example region: `us-east-1`):

```bash
export AWS_REGION=us-east-1
export USER_POOL_ID=<value of cognito_user_pool_id>
```

**① Overwrite the GitHub PAT in SSM**

```bash
aws ssm put-parameter \
  --name /mini-cursor/github-token \
  --value "ghp_xxxxxxxxxxxxxxxxxxxx" \
  --type SecureString \
  --overwrite \
  --region "$AWS_REGION"
```

**② Create the mobile Cognito user (`tetsup-phone`)**

```bash
aws cognito-idp admin-create-user \
  --user-pool-id "$USER_POOL_ID" \
  --username tetsup-phone \
  --user-attributes Name=email,Value=tetsup-phone@example.com Name=email_verified,Value=true \
  --message-action SUPPRESS \
  --region "$AWS_REGION"
```

**③ Set a permanent password**

Must satisfy the Cognito password policy (8+ chars, upper, lower, number).

```bash
aws cognito-idp admin-set-user-password \
  --user-pool-id "$USER_POOL_ID" \
  --username tetsup-phone \
  --password "YourSecurePass1" \
  --permanent \
  --region "$AWS_REGION"
```

### 3. Start the frontend and log in from your phone

From the repo root, start the mobile UI dev server (`host: true` allows access from phones on the same LAN):

```bash
pnpm install
pnpm --filter @mini-cursor/web-ui dev
```

Open the URL shown in the terminal (e.g. `http://192.168.x.x:5174`) on your phone and log in with the values from step 1:

| Field | Value |
|---|---|
| User Pool ID | `cognito_user_pool_id` |
| App Client ID | `cognito_user_pool_client_id` |
| Username | `tetsup-phone` |
| Password | Password set in step 2 |
| API Gateway URL | `api_endpoint` |

After login, send a natural-language instruction and the Lambda agent runs via API Gateway.
