"""
myagent — AWS Lambda handler

Flow:
  POST /agent (API Gateway):
    1. Issue a unique task_id and return 202 Accepted immediately
    2. Async self-invoke to continue processing in the background
    3. Append progress logs to DynamoDB as each step completes

  GET /status?task_id=... (API Gateway):
    Return current status (processing / success / failed) and log lines from DynamoDB

  Background invocation:
    1. Call Amazon Bedrock (Claude 3.5 Sonnet) to generate code
    2. Create GitHub branch → commit file → open Pull Request
       (GitHub REST API via urllib.request only — no third-party HTTP libs)
"""

import base64
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

import boto3

# ---------------------------------------------------------------------------
# Logging (CloudWatch)
# ---------------------------------------------------------------------------
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# AWS clients (boto3 is available in the Lambda runtime)
# ---------------------------------------------------------------------------
_bedrock_region = os.environ.get("BEDROCK_REGION", "us-east-1")
bedrock = boto3.client(service_name="bedrock-runtime", region_name=_bedrock_region)
ssm = boto3.client("ssm")
s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
lambda_client = boto3.client("lambda")

LOGS_TABLE = os.environ.get("LOGS_TABLE", "myagent-logs")

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"

TASK_STATUS_PROCESSING = "processing"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_FAILED = "failed"


def _resolve_repo_file_path(body_or_event: dict) -> tuple[str, str, str | None]:
    repo = (body_or_event.get("repo") or os.environ.get("DEFAULT_REPO") or "").strip()
    file_path = (
        body_or_event.get("file_path") or os.environ.get("DEFAULT_FILE_PATH") or "src/main.py"
    ).strip()
    if not repo or "/" not in repo:
        return (
            "",
            file_path,
            "Set 'repo' to a valid 'owner/repo' in the request body or DEFAULT_REPO env var",
        )
    if not file_path:
        return repo, "", "Set 'file_path' in the request body or DEFAULT_FILE_PATH env var"
    return repo, file_path, None


# ---------------------------------------------------------------------------
# DynamoDB task log cache
# ---------------------------------------------------------------------------
def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _logs_table():
    return dynamodb.Table(LOGS_TABLE)


def init_task_record(task_id: str, cognito_sub: str | None, instruction: str, repo: str, file_path: str) -> None:
    """Create the initial DynamoDB record for a new background task."""
    table = _logs_table()
    table.put_item(
        Item={
            "task_id": task_id,
            "status": TASK_STATUS_PROCESSING,
            "logs": [f"[{_utc_now()}] タスクを受け付けました"],
            "instruction": instruction,
            "repo": repo,
            "file_path": file_path,
            "cognito_sub": cognito_sub or "",
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }
    )


def append_task_log(task_id: str, message: str) -> None:
    """Append a progress line to the task's log array in DynamoDB."""
    timestamp = _utc_now()
    entry = f"[{timestamp}] {message}"
    table = _logs_table()
    table.update_item(
        Key={"task_id": task_id},
        UpdateExpression=(
            "SET logs = list_append(if_not_exists(logs, :empty), :entry), "
            "updated_at = :updated_at"
        ),
        ExpressionAttributeValues={
            ":empty": [],
            ":entry": [entry],
            ":updated_at": timestamp,
        },
    )
    logger.info("task_id=%s log: %s", task_id, message)


def set_task_status(task_id: str, status: str, result: dict | None = None, error: str | None = None) -> None:
    """Update the terminal status (success / failed) and optional result payload."""
    timestamp = _utc_now()
    update_expr = "SET #status = :status, updated_at = :updated_at"
    expr_names = {"#status": "status"}
    expr_values: dict = {
        ":status": status,
        ":updated_at": timestamp,
    }

    if result is not None:
        update_expr += ", #result = :result"
        expr_names["#result"] = "result"
        expr_values[":result"] = result

    if error is not None:
        update_expr += ", #error = :error"
        expr_names["#error"] = "error"
        expr_values[":error"] = error

    table = _logs_table()
    table.update_item(
        Key={"task_id": task_id},
        UpdateExpression=update_expr,
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
    )


def get_task_record(task_id: str) -> dict | None:
    """Fetch a task record from DynamoDB. Returns None if not found."""
    table = _logs_table()
    response = table.get_item(Key={"task_id": task_id})
    return response.get("Item")


def invoke_background_task(task_id: str, payload: dict) -> None:
    """Fire-and-forget async self-invocation so POST /agent can return 202 immediately."""
    function_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "")
    if not function_name:
        raise RuntimeError("AWS_LAMBDA_FUNCTION_NAME is not set")

    event = {"background": True, "task_id": task_id, **payload}
    lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="Event",
        Payload=json.dumps(event, ensure_ascii=False).encode("utf-8"),
    )
    logger.info("Dispatched background task: task_id=%s", task_id)


# ---------------------------------------------------------------------------
# Cognito identity extraction from API Gateway JWT authorizer
# ---------------------------------------------------------------------------
def get_cognito_user_sub(event: dict) -> str | None:
    """
    Extract the Cognito user ID (sub) from the API Gateway JWT authorizer context.
    Path: event['requestContext']['authorizer']['jwt']['claims']['sub']
    """
    try:
        return (
            event.get("requestContext", {})
            .get("authorizer", {})
            .get("jwt", {})
            .get("claims", {})
            .get("sub")
        )
    except (AttributeError, TypeError):
        return None


def log_authenticated_request(event: dict, context) -> str | None:
    """
    Emit a structured JSON log entry identifying the authenticated caller.
    Returns the cognito sub if present.
    """
    cognito_sub = get_cognito_user_sub(event)
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )

    log_entry = {
        "event": "authenticated_request",
        "cognito_sub": cognito_sub,
        "cognito_username": claims.get("username") if isinstance(claims, dict) else None,
        "cognito_email": claims.get("email") if isinstance(claims, dict) else None,
        "request_id": context.aws_request_id if context else None,
        "http_method": event.get("requestContext", {}).get("http", {}).get("method"),
        "path": event.get("requestContext", {}).get("http", {}).get("path"),
        "source_ip": event.get("requestContext", {}).get("http", {}).get("sourceIp"),
    }
    logger.info(json.dumps(log_entry, ensure_ascii=False))

    return cognito_sub


# ---------------------------------------------------------------------------
# Secret resolution: env var first, then SSM Parameter Store
# ---------------------------------------------------------------------------
def get_secret(env_key: str, ssm_env_key: str | None = None) -> str:
    """
    Resolve a secret value.
    Priority:
      1. Direct environment variable (e.g. GITHUB_TOKEN)
      2. SSM parameter whose name is in {env_key}_SSM or GITHUB_TOKEN_SSM
    """
    direct = os.environ.get(env_key, "").strip()
    if direct and direct not in ("REPLACE_WITH_YOUR_GITHUB_PAT", "your_github_token_here"):
        logger.info("Using %s from environment variable", env_key)
        return direct

    param_name = os.environ.get(ssm_env_key or f"{env_key}_SSM", "").strip()
    if not param_name:
        raise ValueError(
            f"Secret '{env_key}' not found. Set env var {env_key} or {ssm_env_key or env_key + '_SSM'}"
        )

    logger.info("Fetching %s from SSM parameter: %s", env_key, param_name)
    try:
        response = ssm.get_parameter(Name=param_name, WithDecryption=True)
        value = response["Parameter"]["Value"].strip()
        if not value or value == "REPLACE_WITH_YOUR_GITHUB_PAT":
            raise ValueError(f"SSM parameter {param_name} has a placeholder value")
        return value
    except Exception as exc:
        logger.error("Failed to read SSM parameter %s: %s", param_name, exc)
        raise


# ---------------------------------------------------------------------------
# GitHub REST API helper (urllib.request only)
# ---------------------------------------------------------------------------
class GitHubAPIError(Exception):
    def __init__(self, status_code: int, message: str, url: str = ""):
        self.status_code = status_code
        self.message = message
        self.url = url
        super().__init__(f"GitHub API {status_code} on {url}: {message}")


def github_request(
    method: str,
    path: str,
    token: str,
    payload: dict | None = None,
) -> tuple[dict | list | None, int]:
    """
    Execute a GitHub REST API request.
    `path` is relative, e.g. '/repos/octocat/Hello-World'
    Returns (parsed_json_body, status_code).
    """
    url = f"{GITHUB_API_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "myagent/1.0",
    }

    body_bytes = None
    if payload is not None:
        body_bytes = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            status = response.status
            if not raw:
                return None, status
            return json.loads(raw), status

    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        logger.error(
            "GitHub HTTP error: method=%s url=%s status=%s body=%s",
            method,
            url,
            exc.code,
            error_body,
        )
        try:
            parsed = json.loads(error_body)
            message = parsed.get("message", error_body)
        except json.JSONDecodeError:
            message = error_body
        raise GitHubAPIError(exc.code, message, url) from exc

    except urllib.error.URLError as exc:
        logger.error("GitHub network error: method=%s url=%s error=%s", method, url, exc)
        raise GitHubAPIError(0, str(exc.reason), url) from exc


def parse_repo(repo: str) -> tuple[str, str]:
    """Split 'owner/repo' into (owner, repo_name)."""
    parts = repo.strip().split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Invalid repo format '{repo}'. Expected 'owner/repo'.")
    return parts[0], parts[1]


# ---------------------------------------------------------------------------
# Bedrock — Claude 3.5 Sonnet code generation
# ---------------------------------------------------------------------------
def invoke_bedrock(instruction: str, file_path: str, existing_content: str | None) -> str:
    model_id = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-3-5-sonnet-20241022-v2:0")

    if existing_content:
        user_content = (
            f"You are a coding assistant. Modify the file `{file_path}` according to "
            f"the following instruction.\n\n"
            f"## Instruction\n{instruction}\n\n"
            f"## Current file content\n```\n{existing_content}\n```\n\n"
            f"Return ONLY the complete updated file content. "
            f"No markdown fences, no explanation."
        )
    else:
        user_content = (
            f"You are a coding assistant. Create the file `{file_path}` according to "
            f"the following instruction.\n\n"
            f"## Instruction\n{instruction}\n\n"
            f"Return ONLY the complete file content. "
            f"No markdown fences, no explanation."
        )

    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": user_content}],
    }

    logger.info("Invoking Bedrock model: %s", model_id)
    try:
        response = bedrock.invoke_model(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(request_body),
        )
        response_body = json.loads(response["body"].read())
        generated = response_body["content"][0]["text"]
        logger.info("Bedrock generated %d characters", len(generated))
        return generated.strip()
    except Exception as exc:
        logger.error("Bedrock invocation failed: %s", exc)
        raise


# ---------------------------------------------------------------------------
# GitHub PR workflow
# ---------------------------------------------------------------------------
def get_default_branch(token: str, owner: str, repo_name: str) -> str:
    data, _ = github_request("GET", f"/repos/{owner}/{repo_name}", token)
    default_branch = data.get("default_branch", "main")
    logger.info("Default branch for %s/%s: %s", owner, repo_name, default_branch)
    return default_branch


def get_branch_commit_sha(token: str, owner: str, repo_name: str, branch: str) -> str:
    data, _ = github_request(
        "GET",
        f"/repos/{owner}/{repo_name}/git/ref/heads/{branch}",
        token,
    )
    sha = data["object"]["sha"]
    logger.info("Branch %s tip SHA: %s", branch, sha)
    return sha


def get_file_content_and_sha(
    token: str, owner: str, repo_name: str, file_path: str, ref: str
) -> tuple[str | None, str | None]:
    """Return (decoded_content, file_sha) or (None, None) if file does not exist."""
    encoded_path = "/".join(urllib.request.quote(segment, safe="") for segment in file_path.split("/"))
    try:
        data, _ = github_request(
            "GET",
            f"/repos/{owner}/{repo_name}/contents/{encoded_path}?ref={ref}",
            token,
        )
        if isinstance(data, list):
            logger.warning("Path %s is a directory, not a file", file_path)
            return None, None
        content_b64 = data.get("content", "")
        content = base64.b64decode(content_b64).decode("utf-8")
        file_sha = data.get("sha")
        logger.info("Fetched existing file %s (sha=%s)", file_path, file_sha)
        return content, file_sha
    except GitHubAPIError as exc:
        if exc.status_code == 404:
            logger.info("File %s does not exist on branch %s — will create new file", file_path, ref)
            return None, None
        raise


def create_branch(
    token: str, owner: str, repo_name: str, branch_name: str, from_sha: str
) -> None:
    payload = {"ref": f"refs/heads/{branch_name}", "sha": from_sha}
    github_request("POST", f"/repos/{owner}/{repo_name}/git/refs", token, payload)
    logger.info("Created branch: %s", branch_name)


def commit_file(
    token: str,
    owner: str,
    repo_name: str,
    file_path: str,
    branch_name: str,
    content: str,
    commit_message: str,
    file_sha: str | None = None,
) -> dict:
    encoded_path = "/".join(urllib.request.quote(segment, safe="") for segment in file_path.split("/"))
    payload = {
        "message": commit_message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch_name,
    }
    if file_sha:
        payload["sha"] = file_sha

    data, _ = github_request(
        "PUT",
        f"/repos/{owner}/{repo_name}/contents/{encoded_path}",
        token,
        payload,
    )
    commit_sha = data.get("commit", {}).get("sha", "unknown")
    logger.info("Committed file %s on branch %s (commit=%s)", file_path, branch_name, commit_sha)
    return data


def create_pull_request(
    token: str,
    owner: str,
    repo_name: str,
    title: str,
    head_branch: str,
    base_branch: str,
    body: str,
) -> dict:
    payload = {
        "title": title,
        "head": head_branch,
        "base": base_branch,
        "body": body,
    }
    data, _ = github_request("POST", f"/repos/{owner}/{repo_name}/pulls", token, payload)
    pr_number = data.get("number")
    pr_url = data.get("html_url")
    logger.info("Created PR #%s: %s", pr_number, pr_url)
    return data


def generate_branch_name() -> str:
    timestamp = int(time.time())
    short_id = uuid.uuid4().hex[:8]
    return f"myagent-patch-{timestamp}-{short_id}"


def create_github_pr(
    repo: str,
    file_path: str,
    code: str,
    token: str,
    instruction: str = "",
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    """
    GitHub PR creation workflow (3 API steps):

      Step 1 — Create a new branch from the default branch tip
      Step 2 — Commit (create or update) the target file on that branch
      Step 3 — Open a Pull Request from the new branch into the default branch

    Returns a dict with branch_name, pr_number, pr_url, commit_sha.
    """
    owner, repo_name = parse_repo(repo)
    default_branch = get_default_branch(token, owner, repo_name)
    base_sha = get_branch_commit_sha(token, owner, repo_name, default_branch)

    # Fetch existing file on default branch for logging / commit message context
    _, existing_file_sha_on_default = get_file_content_and_sha(
        token, owner, repo_name, file_path, default_branch
    )
    action = "Update" if existing_file_sha_on_default else "Create"

    # --- Step 1: Create branch ---
    branch_name = generate_branch_name()
    if on_progress:
        on_progress(f"ブランチ作成中: {branch_name}")
    logger.info("Step 1/3: Creating branch %s from %s (%s)", branch_name, default_branch, base_sha)
    create_branch(token, owner, repo_name, branch_name, base_sha)

    # --- Step 2: Commit file ---
    _, file_sha_on_branch = get_file_content_and_sha(token, owner, repo_name, file_path, branch_name)
    commit_message = f"myagent: {action} {file_path}"
    if instruction:
        truncated = instruction[:72] + ("..." if len(instruction) > 72 else "")
        commit_message = f"myagent: {truncated}"

    if on_progress:
        on_progress(f"ファイルをコミット中: {file_path}")
    logger.info("Step 2/3: Committing %s to branch %s", file_path, branch_name)
    commit_result = commit_file(
        token=token,
        owner=owner,
        repo_name=repo_name,
        file_path=file_path,
        branch_name=branch_name,
        content=code,
        commit_message=commit_message,
        file_sha=file_sha_on_branch,
    )
    commit_sha = commit_result.get("commit", {}).get("sha", "")

    # --- Step 3: Create Pull Request ---
    pr_title = f"myagent: {action} `{file_path}`"
    pr_body = (
        f"## Summary\n\n"
        f"Automated change by **myagent** agent.\n\n"
        f"**Instruction:** {instruction or '(no instruction provided)'}\n\n"
        f"**File:** `{file_path}`\n"
        f"**Branch:** `{branch_name}` → `{default_branch}`\n"
    )
    if on_progress:
        on_progress("PR作成中")
    logger.info("Step 3/3: Creating pull request %s → %s", branch_name, default_branch)
    pr = create_pull_request(
        token=token,
        owner=owner,
        repo_name=repo_name,
        title=pr_title,
        head_branch=branch_name,
        base_branch=default_branch,
        body=pr_body,
    )

    return {
        "branch_name": branch_name,
        "default_branch": default_branch,
        "commit_sha": commit_sha,
        "pr_number": pr.get("number"),
        "pr_url": pr.get("html_url"),
        "pr_title": pr.get("title"),
    }


# ---------------------------------------------------------------------------
# Optional: persist run metadata to S3 workspace bucket
# ---------------------------------------------------------------------------
def save_run_metadata(bucket: str, key: str, metadata: dict) -> None:
    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        logger.info("Saved run metadata to s3://%s/%s", bucket, key)
    except Exception as exc:
        logger.warning("Failed to save metadata to S3: %s", exc)


# ---------------------------------------------------------------------------
# HTTP route handlers
# ---------------------------------------------------------------------------
def _parse_json_body(event: dict) -> dict:
    raw_body = event.get("body", "{}")
    if isinstance(raw_body, str):
        return json.loads(raw_body or "{}")
    if isinstance(raw_body, dict):
        return raw_body
    return {}


def handle_post_agent(event: dict, context) -> dict:
    """
    Accept a new instruction, persist task metadata to DynamoDB, dispatch
    background processing, and return 202 immediately.
    """
    cognito_sub = log_authenticated_request(event, context)

    try:
        body = _parse_json_body(event)

        user_instruction = body.get("instruction", "").strip()
        if not user_instruction:
            return _response(400, {"error": "Missing required field: instruction"})

        repo_name, file_path, repo_error = _resolve_repo_file_path(body)
        if repo_error:
            return _response(400, {"error": repo_error})

        task_id = str(uuid.uuid4())
        init_task_record(task_id, cognito_sub, user_instruction, repo_name, file_path)

        invoke_background_task(
            task_id,
            {
                "instruction": user_instruction,
                "repo": repo_name,
                "file_path": file_path,
                "cognito_sub": cognito_sub,
            },
        )

        append_task_log(task_id, "バックグラウンド処理を開始しました")

        return _response(
            202,
            {
                "task_id": task_id,
                "message": "Accepted",
            },
        )

    except ValueError as exc:
        logger.exception("Validation error on POST /agent")
        return _response(400, {"error": str(exc)})
    except Exception as exc:
        logger.exception("Unhandled error on POST /agent")
        return _response(500, {"error": "Internal server error", "detail": str(exc)})


def handle_get_status(event: dict, context) -> dict:
    """Return task status and accumulated logs from DynamoDB."""
    log_authenticated_request(event, context)

    query_params = event.get("queryStringParameters") or {}
    task_id = (query_params.get("task_id") or "").strip()

    if not task_id:
        return _response(400, {"error": "Missing required query parameter: task_id"})

    record = get_task_record(task_id)
    if not record:
        return _response(404, {"error": "Task not found", "task_id": task_id})

    logs = record.get("logs", [])
    if not isinstance(logs, list):
        logs = [str(logs)]

    response_body: dict = {
        "task_id": task_id,
        "status": record.get("status", TASK_STATUS_PROCESSING),
        "logs": logs,
        "updated_at": record.get("updated_at"),
    }

    if "result" in record:
        response_body["result"] = record["result"]
    if "error" in record:
        response_body["error"] = record["error"]

    return _response(200, response_body)


def process_background_task(event: dict, context) -> dict:
    """Run the full agent workflow and stream progress into DynamoDB."""
    task_id = event.get("task_id", "")
    user_instruction = event.get("instruction", "").strip()
    repo_name, file_path, repo_error = _resolve_repo_file_path(event)
    if repo_error:
        logger.error("Background invocation invalid repo/file_path: %s", repo_error)
        return {"statusCode": 400, "body": repo_error}

    cognito_sub = event.get("cognito_sub")

    if not task_id:
        logger.error("Background invocation missing task_id")
        return {"statusCode": 500, "body": "missing task_id"}

    def log_progress(message: str) -> None:
        append_task_log(task_id, message)

    try:
        log_progress("GitHubトークンを取得中")
        github_token = get_secret("GITHUB_TOKEN", "GITHUB_TOKEN_SSM")
        owner, repo_part = parse_repo(repo_name)

        log_progress(f"リポジトリ情報を取得中: {repo_name}")
        default_branch = get_default_branch(github_token, owner, repo_part)
        existing_content, _ = get_file_content_and_sha(
            github_token, owner, repo_part, file_path, default_branch
        )

        log_progress("Bedrock呼び出し中")
        generated_code = invoke_bedrock(user_instruction, file_path, existing_content)
        generated_code = _strip_markdown_fences(generated_code)
        log_progress(f"コード生成完了 ({len(generated_code)} 文字)")

        pr_result = create_github_pr(
            repo=repo_name,
            file_path=file_path,
            code=generated_code,
            token=github_token,
            instruction=user_instruction,
            on_progress=log_progress,
        )

        workspace_bucket = os.environ.get("WORKSPACE_BUCKET", "")
        if workspace_bucket:
            save_run_metadata(
                workspace_bucket,
                f"runs/{task_id}.json",
                {
                    "task_id": task_id,
                    "cognito_sub": cognito_sub,
                    "instruction": user_instruction,
                    "repo": repo_name,
                    "file_path": file_path,
                    **pr_result,
                },
            )

        log_progress(f"PR作成完了: {pr_result.get('pr_url', '(no url)')}")
        set_task_status(task_id, TASK_STATUS_SUCCESS, result=pr_result)

        return {"statusCode": 200, "body": json.dumps({"task_id": task_id, "status": TASK_STATUS_SUCCESS})}

    except GitHubAPIError as exc:
        logger.exception("GitHub API failure for task_id=%s", task_id)
        error_message = f"GitHub API error: {exc}"
        log_progress(error_message)
        set_task_status(task_id, TASK_STATUS_FAILED, error=error_message)
        return {"statusCode": 500, "body": error_message}

    except ValueError as exc:
        logger.exception("Validation error for task_id=%s", task_id)
        error_message = str(exc)
        log_progress(f"バリデーションエラー: {error_message}")
        set_task_status(task_id, TASK_STATUS_FAILED, error=error_message)
        return {"statusCode": 400, "body": error_message}

    except Exception as exc:
        logger.exception("Unhandled error for task_id=%s", task_id)
        error_message = str(exc)
        log_progress(f"エラー: {error_message}")
        set_task_status(task_id, TASK_STATUS_FAILED, error=error_message)
        return {"statusCode": 500, "body": error_message}


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------
def handler(event, context):
    """
    Routes:
      POST /agent  — accept instruction, return 202 + task_id, process in background
      GET  /status — poll task_id for status and logs (DynamoDB)
      background   — async self-invocation payload (event['background'] == True)
    """
    if event.get("background"):
        return process_background_task(event, context)

    http = event.get("requestContext", {}).get("http", {})
    method = (http.get("method") or "").upper()
    path = http.get("path") or ""

    if method == "GET" and path == "/status":
        return handle_get_status(event, context)

    if method == "POST" and path == "/agent":
        return handle_post_agent(event, context)

    return _response(404, {"error": "Not found", "method": method, "path": path})


def _strip_markdown_fences(text: str) -> str:
    """Remove ```lang ... ``` wrappers if the model returns fenced code."""
    pattern = r"^```[\w]*\n([\s\S]*?)\n```$"
    match = re.match(pattern, text.strip())
    if match:
        return match.group(1)
    return text


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False),
    }
