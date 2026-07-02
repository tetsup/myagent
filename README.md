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

## mini-cursor（AWS）使い方

スマホから Cognito 認証付き API Gateway 経由でエージェントを操作するための手順です。

### 1. インフラをデプロイする

`infra/` で Terraform を実行し、出力される値をメモします。

```bash
cd infra
terraform init
terraform apply
```

`apply` 完了後、画面に表示される次の値を控えておきます。

| 出力名 | 用途 |
|---|---|
| `cognito_user_pool_id` | Web UI の **User Pool ID** |
| `cognito_user_pool_client_id` | Web UI の **App Client ID** |
| `api_endpoint` | Web UI の **API Gateway URL**（`POST /agent`） |
| `status_endpoint` | タスク進捗のポーリング先（`GET /status`） |
| `github_token_ssm_parameter` | 次のステップで上書きする SSM パラメータ名 |

### 2. 秘密の鍵とユーザーを AWS CLI でセットする

GitHub トークンは SSM Parameter Store に置き、Cognito ユーザーは CLI から作成します。トークンをシェル履歴やチャットに貼らないよう、以下の 3 コマンドだけで済ませます。

`terraform apply` の出力値を環境変数に入れてから実行してください（例は `us-east-1`）。

```bash
export AWS_REGION=us-east-1
export USER_POOL_ID=<cognito_user_pool_id の値>
```

**① GitHub PAT を SSM に上書きする**

```bash
aws ssm put-parameter \
  --name /mini-cursor/github-token \
  --value "ghp_xxxxxxxxxxxxxxxxxxxx" \
  --type SecureString \
  --overwrite \
  --region "$AWS_REGION"
```

**② スマホ用 Cognito ユーザーを作成する（`tetsup-phone`）**

```bash
aws cognito-idp admin-create-user \
  --user-pool-id "$USER_POOL_ID" \
  --username tetsup-phone \
  --user-attributes Name=email,Value=tetsup-phone@example.com Name=email_verified,Value=true \
  --message-action SUPPRESS \
  --region "$AWS_REGION"
```

**③ 永続パスワードを設定する**

Cognito のパスワードポリシー（8 文字以上・大文字・小文字・数字）に合わせてください。

```bash
aws cognito-idp admin-set-user-password \
  --user-pool-id "$USER_POOL_ID" \
  --username tetsup-phone \
  --password "YourSecurePass1" \
  --permanent \
  --region "$AWS_REGION"
```

### 3. フロントエンドを起動してスマホからログインする

リポジトリ直下でモバイル UI の開発サーバーを起動します（`host: true` のため同一 LAN 内のスマホからアクセス可能）。

```bash
pnpm install
pnpm --filter @mini-cursor/web-ui dev
```

ターミナルに表示される URL（例: `http://192.168.x.x:5174`）をスマホのブラウザで開き、ステップ 1 でメモした値を入力してログインします。

| フィールド | 入力する値 |
|---|---|
| User Pool ID | `cognito_user_pool_id` |
| App Client ID | `cognito_user_pool_client_id` |
| Username | `tetsup-phone` |
| Password | ステップ 2 で設定したパスワード |
| API Gateway URL | `api_endpoint` |

ログイン後、自然言語の指示を送信すると API Gateway 経由で Lambda エージェントが実行されます。
