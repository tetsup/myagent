# myagent

> **English:** [README.md](./README.md)

スマホ向けの GitHub 開発エージェント（AWS 上で動作）です。スマホから自然言語の指示を送ると、Amazon Bedrock（Claude）でコードを生成し、対象ファイルを編集してプルリクエストを開きます。API Gateway の背後で Cognito 認証がかかっています。

## アーキテクチャ

```
┌──────────────┐  Cognito JWT   ┌──────────────┐   invoke   ┌─────────────────┐
│  web-ui       │ ─────────────▶ │  API Gateway  │ ─────────▶ │  agent (Lambda)  │
│  React+Vite   │  POST /agent   │  HTTP API     │            │  Python 3.11     │
│  (スマホ)      │  GET /status   │  + JWT 認証   │            └────────┬────────┘
└──────────────┘                └──────────────┘                     │
       ▲                                                              │
       │ ログをポーリング                                               ▼
       │                                                         ┌─────────────┐
       └──────────────── DynamoDB ログキャッシュ ────────────────│  Bedrock     │
                                                                 │  GitHub API  │
                                                                 └─────────────┘
```

| パッケージ | パス | スタック | 役割 |
|---|---|---|---|
| `@myagent/web-ui` | `packages/web-ui` | React 19 + Vite | モバイルコンソール（Cognito ログイン・指示送信・タスクログのポーリング） |
| agent Lambda | `packages/agent-lambda` | Python 3.11 | Bedrock によるコード生成、GitHub ブランチ/コミット/PR 自動化 |
| Terraform | `infra/` | Terraform | AWS リソース（API Gateway、Lambda、Cognito、DynamoDB、S3、SSM） |

**リクエストの流れ**

1. モバイル UI が Cognito で認証し、ID トークンを取得する。
2. `POST /agent` は即座に `202 Accepted` と `task_id` を返し、Lambda は非同期で処理を続ける。
3. UI は `GET /status?task_id=…` をポーリングし、DynamoDB に蓄積された進捗ログを表示する。
4. バックグラウンドで Lambda が Bedrock を呼び出し、GitHub REST API で PR を開く。

## 使い方

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
  --name /myagent/github-token \
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
pnpm --filter @myagent/web-ui dev
```

ターミナルに表示される URL（例: `http://192.168.x.x:5174`）をスマホのブラウザで開き、ステップ 1 でメモした値を入力してログインします。

| フィールド | 入力する値 |
|---|---|
| User Pool ID | `cognito_user_pool_id` |
| App Client ID | `cognito_user_pool_client_id` |
| Username | `tetsup-phone` |
| Password | ステップ 2 で設定したパスワード |
| API Gateway URL | `api_endpoint` |
| リポジトリ | `owner/repo`（例: `octocat/Hello-World`） |
| ファイルパス | 編集対象ファイル（例: `src/main.py`） |

ログイン後、自然言語の指示を送信すると API Gateway 経由で Lambda エージェントが実行されます。

## 開発

Node 22 + pnpm 10（corepack 経由）が必要です。インフラ変更には Terraform >= 1.5 を使います。

```bash
pnpm install
pnpm dev          # @myagent/web-ui dev のエイリアス（ポート 5174）
pnpm check        # web-ui の TypeScript チェック
```

`packages/agent-lambda/index.py` を編集したら、`infra/` で `terraform apply` を再実行して Lambda の zip を更新してください。

### インフラ変数（`infra/`）

| 変数 | デフォルト | 説明 |
|---|---|---|
| `aws_region` | `us-east-1` | AWS リージョン（Bedrock のモデル提供はリージョン依存） |
| `project_name` | `myagent` | リソース名のプレフィックス |
| `bedrock_model_id` | Claude 3.5 Sonnet v2 | コード生成用 Bedrock モデル |
| `lambda_timeout` | `300` | Lambda タイムアウト（秒） |
| `default_repo` | `""` | 任意のフォールバック `owner/repo`（`terraform.tfvars.example` 参照） |
| `default_file_path` | `src/main.py` | 任意のフォールバックファイルパス |

### Lambda 環境変数（Terraform が設定）

| 変数 | 説明 |
|---|---|
| `GITHUB_TOKEN_SSM` | GitHub PAT の SSM パラメータ名 |
| `BEDROCK_MODEL_ID` | Bedrock モデル ID |
| `LOGS_TABLE` | タスクログキャッシュ用 DynamoDB テーブル |
| `DEFAULT_REPO` | リクエストに `repo` がない場合の `owner/repo` |
| `DEFAULT_FILE_PATH` | リクエストに `file_path` がない場合のファイルパス |
