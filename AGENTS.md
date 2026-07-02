# myagent

A mobile GitHub development agent on AWS. See `README.md` for architecture,
getting started, and development workflow.

## Cursor Cloud specific instructions

### What runs where

| Component | Where | Notes |
|---|---|---|
| AWS stack | `infra/` (Terraform) | API Gateway, Lambda, Cognito, DynamoDB, S3, SSM — copy `terraform.tfvars.example` → `terraform.tfvars`, then `terraform apply` |
| agent Lambda | `packages/agent-lambda` | Python; redeploy via `terraform apply` after edits |
| mobile UI | `packages/web-ui` | Local Vite dev server; talks to deployed API Gateway |

There is no local backend. The mobile UI connects to the Cognito + API Gateway
endpoints produced by Terraform.

### Local dev (mobile UI only)

```bash
pnpm install
pnpm --filter @myagent/web-ui dev   # http://0.0.0.0:5174
```

`vite.config.ts` sets `host: true` so phones on the same LAN can reach the dev
server. Cognito login and API calls still hit the real AWS endpoints configured
in the UI.

### Gotchas

- **Terraform must be applied before the UI is useful.** Without `api_endpoint`
  and Cognito IDs from `terraform apply`, login and task submission will fail.
- **GitHub PAT lives in SSM**, not in env files. Overwrite
  `/myagent/github-token` via the AWS CLI after the first apply.
- **Lambda code changes need redeploy.** Edit `packages/agent-lambda/index.py`,
  then `cd infra && terraform apply` to rebuild the zip.
- **esbuild build script** is enabled via `pnpm.onlyBuiltDependencies` in the
  root `package.json`. If Vite fails with an esbuild binary error, run
  `pnpm rebuild esbuild`.
