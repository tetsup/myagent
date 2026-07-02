"""
mini-cursor-agent — AWS Lambda handler

Flow:
  1. Receive user instruction via API Gateway (Cognito JWT authenticated)
  2. Log the authenticated Cognito user (sub) as structured JSON to CloudWatch
  3. Call Amazon Bedrock (Claude 3.5 Sonnet) to generate code
  4. Create GitHub branch → commit file → open Pull Request
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

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"


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
        "User-Agent": "mini-cursor-agent/1.0",
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
    return f"mini-cursor-patch-{timestamp}-{short_id}"


def create_github_pr(
    repo: str,
    file_path: str,
    code: str,
    token: str,
    instruction: str = "",
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
    logger.info("Step 1/3: Creating branch %s from %s (%s)", branch_name, default_branch, base_sha)
    create_branch(token, owner, repo_name, branch_name, base_sha)

    # --- Step 2: Commit file ---
    # On a fresh branch the file blob matches default branch; re-fetch sha on new branch if needed
    _, file_sha_on_branch = get_file_content_and_sha(token, owner, repo_name, file_path, branch_name)
    commit_message = f"mini-cursor: {action} {file_path}"
    if instruction:
        truncated = instruction[:72] + ("..." if len(instruction) > 72 else "")
        commit_message = f"mini-cursor: {truncated}"

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
    pr_title = f"mini-cursor: {action} `{file_path}`"
    pr_body = (
        f"## Summary\n\n"
        f"Automated change by **mini-cursor** agent.\n\n"
        f"**Instruction:** {instruction or '(no instruction provided)'}\n\n"
        f"**File:** `{file_path}`\n"
        f"**Branch:** `{branch_name}` → `{default_branch}`\n"
    )
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
# Lambda entry point
# ---------------------------------------------------------------------------
def handler(event, context):
    """
    Expected JSON body:
      {
        "instruction": "Add logging to main function",
        "repo": "owner/repo",          // optional
        "file_path": "src/main.py"     // optional
      }

    Authentication: Cognito JWT via API Gateway authorizer.
    The caller's user ID is available at:
      event['requestContext']['authorizer']['jwt']['claims']['sub']
    """
    cognito_sub = log_authenticated_request(event, context)

    try:
        raw_body = event.get("body", "{}")
        if isinstance(raw_body, str):
            body = json.loads(raw_body or "{}")
        elif isinstance(raw_body, dict):
            body = raw_body
        else:
            body = {}

        user_instruction = body.get("instruction", "").strip()
        if not user_instruction:
            return _response(400, {"error": "Missing required field: instruction"})

        repo_name = body.get("repo") or os.environ.get("DEFAULT_REPO", "your-user/your-repo")
        file_path = body.get("file_path") or os.environ.get("DEFAULT_FILE_PATH", "src/main.py")

        if repo_name == "your-user/your-repo":
            return _response(400, {"error": "Set 'repo' to a valid 'owner/repo' in the request body or DEFAULT_REPO env var"})

        github_token = get_secret("GITHUB_TOKEN", "GITHUB_TOKEN_SSM")
        owner, repo_part = parse_repo(repo_name)

        default_branch = get_default_branch(github_token, owner, repo_part)
        existing_content, _ = get_file_content_and_sha(
            github_token, owner, repo_part, file_path, default_branch
        )

        generated_code = invoke_bedrock(user_instruction, file_path, existing_content)

        # Strip accidental markdown fences if the model adds them
        generated_code = _strip_markdown_fences(generated_code)

        pr_result = create_github_pr(
            repo=repo_name,
            file_path=file_path,
            code=generated_code,
            token=github_token,
            instruction=user_instruction,
        )

        workspace_bucket = os.environ.get("WORKSPACE_BUCKET", "")
        if workspace_bucket:
            run_id = context.aws_request_id if context else str(uuid.uuid4())
            save_run_metadata(
                workspace_bucket,
                f"runs/{run_id}.json",
                {
                    "cognito_sub": cognito_sub,
                    "instruction": user_instruction,
                    "repo": repo_name,
                    "file_path": file_path,
                    **pr_result,
                },
            )

        return _response(
            200,
            {
                "message": "PR created successfully.",
                "cognito_sub": cognito_sub,
                "instruction": user_instruction,
                "repo": repo_name,
                "file_path": file_path,
                **pr_result,
            },
        )

    except GitHubAPIError as exc:
        logger.exception("GitHub API failure")
        return _response(
            exc.status_code if 400 <= exc.status_code < 600 else 502,
            {"error": "GitHub API error", "detail": str(exc)},
        )
    except ValueError as exc:
        logger.exception("Validation error")
        return _response(400, {"error": str(exc)})
    except Exception as exc:
        logger.exception("Unhandled error")
        return _response(500, {"error": "Internal server error", "detail": str(exc)})


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
