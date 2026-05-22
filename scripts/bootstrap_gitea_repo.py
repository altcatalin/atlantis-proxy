from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request


def _env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value or ""


def _read_token() -> str:
    token = _env("GITEA_TOKEN", "")
    if token:
        return token

    token_file = _env("GITEA_TOKEN_FILE", "")
    if token_file:
        path = Path(token_file)
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                return content

    raise RuntimeError("Missing Gitea token. Set GITEA_TOKEN or GITEA_TOKEN_FILE")


def _api_request(
    base_url: str,
    token: str,
    method: str,
    path: str,
    payload: dict | None = None,
) -> tuple[int, dict | list | str | None]:
    url = f"{base_url.rstrip('/')}{path}"
    data = None
    headers = {
        "Accept": "application/json",
        "Authorization": f"token {token}",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url=url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as response:
            raw = response.read().decode("utf-8")
            if not raw:
                return response.status, None
            return response.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        parsed = None
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = raw
        return exc.code, parsed


def _ensure_repo(base_url: str, token: str, owner: str, repo: str, private: bool) -> None:
    status, _ = _api_request(base_url, token, "GET", f"/api/v1/repos/{owner}/{repo}")
    if status == 200:
        print(f"Repository already exists: {owner}/{repo}")
        return
    if status != 404:
        raise RuntimeError(f"Unexpected repo lookup status {status} for {owner}/{repo}")

    create_payload = {
        "name": repo,
        "private": private,
        "auto_init": False,
        "default_branch": "main",
    }
    status, body = _api_request(base_url, token, "POST", "/api/v1/user/repos", create_payload)
    if status not in {200, 201}:
        raise RuntimeError(f"Failed to create repo {owner}/{repo}: status={status}, body={body}")
    print(f"Created repository: {owner}/{repo}")


def _ensure_webhook(base_url: str, token: str, owner: str, repo: str, webhook_url: str, secret: str) -> None:
    status, body = _api_request(base_url, token, "GET", f"/api/v1/repos/{owner}/{repo}/hooks")
    if status != 200 or not isinstance(body, list):
        raise RuntimeError(f"Failed to list hooks: status={status}, body={body}")

    webhook_name = "atlantis-proxy"

    desired_events = [
        "create",
        "delete",
        "fork",
        "push",
        "issues",
        "issue_assign",
        "issue_label",
        "issue_milestone",
        "issue_comment",
        "pull_request",
        "pull_request_assign",
        "pull_request_label",
        "pull_request_milestone",
        "pull_request_comment",
        "pull_request_review_approved",
        "pull_request_review_rejected",
        "pull_request_review_comment",
        "pull_request_sync",
        "pull_request_review_request",
        "wiki",
        "repository",
        "release",
        "package",
        "status",
        "workflow_run",
        "workflow_job",
    ]

    for hook in body:
        config = hook.get("config") or {}
        if config.get("url") != webhook_url:
            continue

        hook_id = hook.get("id")
        existing_name = hook.get("name") or ""
        existing_events = hook.get("events") or []
        existing_secret = config.get("secret") or ""

        events_match = existing_events == desired_events
        name_match = existing_name == webhook_name
        secret_match = (existing_secret == secret) if secret else True

        if events_match and name_match and secret_match:
            print(f"Webhook already configured: {webhook_url}")
            return

        update_payload = {
            "name": webhook_name,
            "active": True,
            "events": desired_events,
            "config": {
                "url": webhook_url,
                "content_type": "json",
            },
        }
        if secret:
            update_payload["config"]["secret"] = secret

        status, update_body = _api_request(
            base_url,
            token,
            "PATCH",
            f"/api/v1/repos/{owner}/{repo}/hooks/{hook_id}",
            update_payload,
        )
        if status not in {200, 201}:
            raise RuntimeError(f"Failed to update webhook: status={status}, body={update_body}")
        print(f"Updated webhook: {webhook_url}")
        return

    payload = {
        "name": webhook_name,
        "type": "gitea",
        "active": True,
        "events": desired_events,
        "config": {
            "url": webhook_url,
            "content_type": "json",
        },
    }
    if secret:
        payload["config"]["secret"] = secret

    status, create_body = _api_request(base_url, token, "POST", f"/api/v1/repos/{owner}/{repo}/hooks", payload)
    if status not in {200, 201}:
        raise RuntimeError(f"Failed to create webhook: status={status}, body={create_body}")
    print(f"Created webhook: {webhook_url}")


def _repo_has_main_branch(base_url: str, token: str, owner: str, repo: str) -> bool:
    status, _ = _api_request(base_url, token, "GET", f"/api/v1/repos/{owner}/{repo}/branches/main")
    return status == 200


def _render_files(template_dir: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in template_dir.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(template_dir).as_posix()
        # Atlantis repo config is mounted at server level and should not be committed to benchmark repos.
        if rel == "atlantis.yaml":
            continue
        files[rel] = path.read_text(encoding="utf-8")
    return files


def _git_remote_with_token(base_url: str, owner: str, repo: str, username: str, token: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    netloc = parsed.netloc
    token_part = urllib.parse.quote(token, safe="")
    username_part = urllib.parse.quote(username, safe="")
    auth_netloc = f"{username_part}:{token_part}@{netloc}"
    return urllib.parse.urlunsplit((parsed.scheme, auth_netloc, f"/{owner}/{repo}.git", "", ""))


def _run(cmd: list[str], cwd: str | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def _write_template_files(dest_dir: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        out_path = dest_dir / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")


def _has_changes(cwd: str) -> bool:
    proc = subprocess.run(["git", "status", "--porcelain"], cwd=cwd, check=True, capture_output=True, text=True)
    return bool(proc.stdout.strip())


def _seed_repo(base_url: str, token: str, owner: str, repo: str, username: str, template_dir: Path) -> None:
    files = _render_files(template_dir)
    remote = _git_remote_with_token(base_url, owner, repo, username, token)

    with tempfile.TemporaryDirectory(prefix="gitea-bootstrap-") as tmp:
        has_main = _repo_has_main_branch(base_url, token, owner, repo)

        if has_main:
            _run(["git", "clone", "--branch", "main", remote, tmp])
        else:
            _run(["git", "init", "-b", "main"], cwd=tmp)
            _run(["git", "remote", "add", "origin", remote], cwd=tmp)

        _run(["git", "config", "user.email", "bootstrap@local"], cwd=tmp)
        _run(["git", "config", "user.name", "gitea-bootstrap"], cwd=tmp)

        _write_template_files(Path(tmp), files)

        _run(["git", "add", "."], cwd=tmp)
        if _has_changes(tmp):
            message = "chore: seed benchmark repository" if not has_main else "chore: sync benchmark repository template"
            _run(["git", "commit", "-m", message], cwd=tmp)
            _run(["git", "push", "-u", "origin", "main"], cwd=tmp)
            print("Seeded/synced repository content on main")
        else:
            print("Repository content already up to date")


def main() -> None:
    base_url = _env("GITEA_URL", "http://localhost:3000")
    token = _read_token()
    owner = _env("GITEA_OWNER", "atlantis")
    repo = _env("GITEA_REPO", "atlantis-benchmark")
    username = _env("GITEA_USERNAME", owner)
    private_repo = _env("GITEA_REPO_PRIVATE", "false").lower() == "true"
    webhook_url = _env("GITEA_WEBHOOK_URL", "http://proxy:8000/events")
    webhook_secret = _env("GITEA_WEBHOOK_SECRET", "")

    template_dir = Path(__file__).resolve().parent / "bootstrap_templates"
    if not template_dir.exists():
        raise RuntimeError(f"Template directory not found: {template_dir}")

    _ensure_repo(base_url, token, owner, repo, private_repo)
    _ensure_webhook(base_url, token, owner, repo, webhook_url, webhook_secret)
    _seed_repo(base_url, token, owner, repo, username, template_dir)

    print("Gitea bootstrap complete")


if __name__ == "__main__":
    main()
