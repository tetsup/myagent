# myagent

> **English:** [README.md](./README.md)

Cursor ライクな GitHub 連携開発エージェントです。ユーザーがタスク（「リポジトリ Y で X をやって」）を送信すると、プラットフォームがリポジトリをクローンし、言語モデルに構造化プランを依頼して、結果を Web ダッシュボードに表示します。言語モデルはオンプレ（Ollama / docker・k8s 上の vLLM など OpenAI 互換サーバー）でもクラウドでも動かせます。

## アーキテクチャ

```
┌────────────┐    /api     ┌────────────┐   /run    ┌────────────────┐   LLM   ┌──────────────┐
│  web        │ ─────────▶ │  api        │ ────────▶ │  agent (Go)     │ ──────▶ │  model server │
│  React+Vite │            │  Hono/Node  │           │  clone + plan   │         │  ollama/cloud │
└────────────┘            └────────────┘           └────────────────┘         └──────────────┘
```

| パッケージ | パス | スタック | 役割 |
|---|---|---|---|
| `@myagent/web` | `apps/web` | React 19 + Vite | タスク送信・プラン閲覧用ダッシュボード |
| `@myagent/api` | `apps/api` | Hono on Node | オーケストレーター（タスク管理・検証・エージェント起動） |
| agent worker | `services/agent` | Go | リポジトリのクローン、LLM 呼び出し、プラン返却 |
| `@myagent/shared` | `packages/shared` | TypeScript | web と api 間の共有コントラクト |

エージェントは Go でコンパイルされたサービスなので、リポジトリ I/O やモデル呼び出しなどの重い処理は Node の外で実行されます。LLM レイヤーはプロバイダー差し替え可能で、`mock`（決定的・オフライン — デフォルト）、`ollama`（オンプレ）、`openai`（クラウド / vLLM など OpenAI 互換エンドポイント）に対応しています。

## 開発

Node 22 + pnpm 10（corepack 経由）と Go 1.22 が必要です。

```bash
pnpm install            # JS 依存関係のインストール

# ターミナル 1 — Go エージェントワーカー（デフォルトはオフラインモック）
cd services/agent && go run .

# ターミナル 2 — Hono API
pnpm --filter @myagent/api dev

# ターミナル 3 — React ダッシュボード（/api を :3001 にプロキシ）
pnpm --filter @myagent/web dev   # http://localhost:5173
```

リポジトリ全体のタスク（turbo 経由）: `pnpm build`、`pnpm lint`、`pnpm test`、`pnpm check`。Go: `services/agent` で `go test ./...`。

### LLM 設定（エージェントワーカーの環境変数）

| 変数 | 値 | デフォルト |
|---|---|---|
| `LLM_PROVIDER` | `mock` \| `ollama` \| `openai` | `mock` |
| `LLM_BASE_URL` | モデルサーバー URL | プロバイダー依存 |
| `LLM_MODEL` | モデル名 | プロバイダー依存 |
| `LLM_API_KEY` | ベアラートークン（openai） | — |

## デプロイ

`deploy/docker-compose.yml` でスタック全体と Ollama を起動できます。`deploy/k8s/` に Kubernetes マニフェストがあります。モデル設定は `deploy/.env.example` を参照してください。

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
