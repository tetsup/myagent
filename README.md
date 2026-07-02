# myagent

> **日本語:** [README.ja.md](./README.ja.md)

A mobile-first GitHub development agent on AWS. Send a natural-language instruction
from your phone; the platform calls Amazon Bedrock (Claude), edits a target file,
and opens a pull request — all behind Cognito-authenticated API Gateway.

## Architecture

```
┌──────────────┐  Cognito JWT   ┌──────────────┐   invoke   ┌─────────────────┐
│  web-ui       │ ─────────────▶ │  API Gateway  │ ─────────▶ │  agent (Lambda)  │
│  React+Vite   │  POST /agent   │  HTTP API     │            │  Python 3.11     │
│  (phone)      │  GET /status   │  + JWT auth   │            └────────┬────────┘
└──────────────┘                └──────────────┘                     │
       ▲                                                              │
       │ poll logs                                                    ▼
       │                                                         ┌─────────────┐
       └──────────────── DynamoDB log cache ────────────────────│  Bedrock     │
                                                                 │  GitHub API  │
                                                                 └─────────────┘
```

| Package | Path | Stack | Role |
|---|---|---|---|
| `@myagent/web-ui` | `packages/web-ui` | React 19 + Vite | Mobile console — Cognito login, send instructions, poll task logs |
| `@myagent/infra` | `infra/` | Terraform | AWS resources (API Gateway, Lambda, Cognito, DynamoDB, S3, SSM) |
| agent Lambda | `packages/agent-lambda` | Python 3.11 | Bedrock code generation, GitHub branch/commit/PR automation |

**Request flow**

1. The mobile UI authenticates with Cognito and obtains an ID token.
2. `POST /agent` returns `202 Accepted` with a `task_id` immediately; Lambda continues asynchronously.
3. The UI polls `GET /status?task_id=…` for progress logs stored in DynamoDB.
4. In the background, Lambda calls Bedrock, then uses the GitHub REST API to open a PR.

## Getting started

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
  --name /myagent/github-token \
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
pnpm --filter @myagent/web-ui dev
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

## Development

Requires Node 22 + pnpm 10 (via corepack). Terraform >= 1.5 for infrastructure changes.

```bash
pnpm install
pnpm dev          # alias for @myagent/web-ui dev (port 5174)
pnpm check        # TypeScript check on web-ui
```

After editing `packages/agent-lambda/index.py`, redeploy with `terraform apply` in `infra/` so the Lambda zip is rebuilt.

### Infrastructure variables (`infra/`)

| Variable | Default | Description |
|---|---|---|
| `aws_region` | `us-east-1` | AWS region (Bedrock model availability varies) |
| `project_name` | `myagent` | Resource name prefix |
| `bedrock_model_id` | Claude 3.5 Sonnet v2 | Bedrock model for code generation |
| `lambda_timeout` | `300` | Lambda timeout in seconds |

### Lambda environment (set by Terraform)

| Variable | Description |
|---|---|
| `GITHUB_TOKEN_SSM` | SSM parameter name for the GitHub PAT |
| `BEDROCK_MODEL_ID` | Bedrock model ID |
| `LOGS_TABLE` | DynamoDB table for task log cache |
| `DEFAULT_REPO` | Fallback `owner/repo` when the request omits `repo` |
| `DEFAULT_FILE_PATH` | Fallback file path when the request omits `file_path` |
