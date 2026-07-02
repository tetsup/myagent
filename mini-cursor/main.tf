terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  description = "AWS region (Bedrock model availability depends on region)"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Resource name prefix"
  type        = string
  default     = "mini-cursor"
}

variable "bedrock_model_id" {
  description = "Amazon Bedrock model ID for Claude 3.5 Sonnet"
  type        = string
  default     = "anthropic.claude-3-5-sonnet-20241022-v2:0"
}

variable "lambda_timeout" {
  description = "Lambda timeout in seconds"
  type        = number
  default     = 300
}

# ---------------------------------------------------------------------------
# Unique suffix for globally-unique S3 bucket name
# ---------------------------------------------------------------------------
resource "random_id" "suffix" {
  byte_length = 4
}

# ---------------------------------------------------------------------------
# 1. Workspace S3 bucket (optional artifact / log storage)
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "workspace" {
  bucket = "${var.project_name}-workspace-${random_id.suffix.hex}"
}

resource "aws_s3_bucket_versioning" "workspace" {
  bucket = aws_s3_bucket.workspace.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "workspace" {
  bucket = aws_s3_bucket.workspace.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "workspace" {
  bucket = aws_s3_bucket.workspace.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------------------------------------------------------------------------
# 2. SSM Parameter Store — GitHub token (SecureString)
#    After first apply, update the value in AWS Console or CLI:
#    aws ssm put-parameter --name /mini-cursor/github-token \
#      --value "ghp_xxxx" --type SecureString --overwrite
# ---------------------------------------------------------------------------
resource "aws_ssm_parameter" "github_token" {
  name  = "/${var.project_name}/github-token"
  type  = "SecureString"
  value = "REPLACE_WITH_YOUR_GITHUB_PAT"

  lifecycle {
    ignore_changes = [value]
  }
}

# ---------------------------------------------------------------------------
# 3. IAM role for Lambda
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "lambda_role" {
  name               = "${var.project_name}-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

data "aws_iam_policy_document" "lambda_policy" {
  # CloudWatch Logs
  statement {
    sid    = "CloudWatchLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:aws:logs:${var.aws_region}:*:*"]
  }

  # SSM Parameter Store (read GitHub token)
  statement {
    sid    = "SSMReadGitHubToken"
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
    ]
    resources = [aws_ssm_parameter.github_token.arn]
  }

  # S3 workspace bucket
  statement {
    sid    = "S3WorkspaceAccess"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:ListBucket",
    ]
    resources = [
      aws_s3_bucket.workspace.arn,
      "${aws_s3_bucket.workspace.arn}/*",
    ]
  }

  # Amazon Bedrock — InvokeModel only (scoped)
  statement {
    sid    = "BedrockInvokeModel"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
    ]
    resources = [
      "arn:aws:bedrock:${var.aws_region}::foundation-model/${var.bedrock_model_id}",
    ]
  }
}

resource "aws_iam_role_policy" "lambda_policy" {
  name   = "${var.project_name}-lambda-policy"
  role   = aws_iam_role.lambda_role.id
  policy = data.aws_iam_policy_document.lambda_policy.json
}

# ---------------------------------------------------------------------------
# 4. Lambda deployment package
# ---------------------------------------------------------------------------
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/index.py"
  output_path = "${path.module}/lambda_function.zip"
}

# ---------------------------------------------------------------------------
# 5. Orchestrator Lambda function
# ---------------------------------------------------------------------------
resource "aws_lambda_function" "agent" {
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  function_name    = "${var.project_name}-agent"
  role             = aws_iam_role.lambda_role.arn
  handler          = "index.handler"
  runtime          = "python3.11"
  timeout          = var.lambda_timeout
  memory_size      = 512

  environment {
    variables = {
      WORKSPACE_BUCKET  = aws_s3_bucket.workspace.id
      GITHUB_TOKEN_SSM  = aws_ssm_parameter.github_token.name
      BEDROCK_MODEL_ID  = var.bedrock_model_id
      BEDROCK_REGION    = var.aws_region
      DEFAULT_REPO      = "your-user/your-repo"
      DEFAULT_FILE_PATH = "src/main.py"
    }
  }

  depends_on = [
    aws_iam_role_policy.lambda_policy,
    aws_cloudwatch_log_group.lambda_logs,
  ]
}

resource "aws_cloudwatch_log_group" "lambda_logs" {
  name              = "/aws/lambda/${var.project_name}-agent"
  retention_in_days = 14
}

# ---------------------------------------------------------------------------
# 6. API Gateway HTTP API (for mobile / external HTTP clients)
# ---------------------------------------------------------------------------
resource "aws_apigatewayv2_api" "http_api" {
  name          = "${var.project_name}-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["POST", "OPTIONS"]
    allow_headers = ["content-type", "authorization"]
  }
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.http_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.agent.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "agent_post" {
  api_id    = aws_apigatewayv2_api.http_api.id
  route_key = "POST /agent"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http_api.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.agent.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http_api.execution_arn}/*/*"
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
output "api_endpoint" {
  description = "POST instructions to this URL from your phone"
  value       = "${aws_apigatewayv2_api.http_api.api_endpoint}/agent"
}

output "lambda_function_name" {
  value = aws_lambda_function.agent.function_name
}

output "workspace_bucket" {
  value = aws_s3_bucket.workspace.id
}

output "github_token_ssm_parameter" {
  description = "Set your GitHub PAT here after terraform apply"
  value       = aws_ssm_parameter.github_token.name
}

output "example_curl" {
  description = "Example request"
  value       = <<-EOT
    curl -X POST '${aws_apigatewayv2_api.http_api.api_endpoint}/agent' \
      -H 'Content-Type: application/json' \
      -d '{"instruction":"Add error handling","repo":"octocat/Hello-World","file_path":"src/main.py"}'
  EOT
}
