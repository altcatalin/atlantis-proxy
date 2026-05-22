from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import random
import re
import string
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(ts: datetime | None) -> str | None:
    if ts is None:
        return None
    return ts.isoformat()


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


def _run(cmd: list[str], cwd: str | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def _git_remote_with_token(base_url: str, owner: str, repo: str, username: str, token: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    token_part = urllib.parse.quote(token, safe="")
    username_part = urllib.parse.quote(username, safe="")
    auth_netloc = f"{username_part}:{token_part}@{parsed.netloc}"
    return urllib.parse.urlunsplit((parsed.scheme, auth_netloc, f"/{owner}/{repo}.git", "", ""))


def _write_workflow_marker(repo_dir: Path, workflow_id: str) -> Path:
    marker_dir = repo_dir / ".benchmarks"
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker_path = marker_dir / f"{workflow_id}.txt"
    marker_path.write_text(f"workflow_id={workflow_id}\n", encoding="utf-8")
    return marker_path


def _random_suffix(length: int = 4) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def _new_workflow_id() -> str:
    return f"{_utc_now().strftime('%Y%m%d%H%M%S')}-{_random_suffix()}"


@dataclass
class WorkflowResult:
    workflow_id: str
    workspace: str
    branch: str
    pr_number: int | None
    start_timestamp: str
    apply_comment_timestamp: str | None
    merged_timestamp: str | None
    terminal_status: str
    error_category: str | None


class WorkflowRunner:
    def __init__(
        self,
        base_url: str,
        token: str,
        owner: str,
        repo: str,
        username: str,
        plan_comment: str,
        apply_comment: str,
        timeout_minutes: int,
        pause_after_comment: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.owner = owner
        self.repo = repo
        self.username = username
        self.plan_comment = plan_comment
        self.apply_comment = apply_comment
        self.timeout_minutes = timeout_minutes
        self.pause_after_comment = pause_after_comment

    def _check_deadline(self, deadline: datetime, stage: str) -> None:
        if _utc_now() > deadline:
            raise TimeoutError(f"timeout while waiting for {stage}")

    def _pause(self, stage: str) -> None:
        print(f"\n[PAUSED] {stage} comment posted. Press Enter to continue...", flush=True)
        try:
            input()
        except (KeyboardInterrupt, EOFError):
            print("\n[RESUMED] Continuing automation...")

    def _pause_before_apply(self) -> None:
        print("[PAUSE] waiting 5s before apply", flush=True)
        time.sleep(5)

    def _open_pr(self, branch: str, workflow_id: str) -> int:
        payload = {
            "title": f"benchmark: {workflow_id}",
            "head": branch,
            "base": "main",
            "body": f"Automated benchmark workflow for {workflow_id}.",
        }
        status, body = _api_request(
            self.base_url,
            self.token,
            "POST",
            f"/api/v1/repos/{self.owner}/{self.repo}/pulls",
            payload,
        )
        if status not in {200, 201} or not isinstance(body, dict) or "number" not in body:
            raise RuntimeError(f"failed to open PR: status={status}, body={body}")
        return int(body["number"])

    def _list_comments(self, pr_number: int) -> list[dict]:
        status, body = _api_request(
            self.base_url,
            self.token,
            "GET",
            f"/api/v1/repos/{self.owner}/{self.repo}/issues/{pr_number}/comments",
        )
        if status != 200 or not isinstance(body, list):
            raise RuntimeError(f"failed to list PR comments: status={status}, body={body}")
        return [c for c in body if isinstance(c, dict)]

    def _get_pr(self, pr_number: int) -> dict:
        status, body = _api_request(
            self.base_url,
            self.token,
            "GET",
            f"/api/v1/repos/{self.owner}/{self.repo}/pulls/{pr_number}",
        )
        if status != 200 or not isinstance(body, dict):
            raise RuntimeError(f"failed to get PR: status={status}, body={body}")
        return body

    def _update_pr_branch(self, pr_number: int) -> None:
        status, body = _api_request(
            self.base_url,
            self.token,
            "POST",
            f"/api/v1/repos/{self.owner}/{self.repo}/pulls/{pr_number}/update?style=rebase",
        )
        # 409 means no update could be applied right now; caller will retry/poll.
        if status in {200, 201, 202, 204, 409}:
            return
        raise RuntimeError(f"failed to update PR branch: status={status}, body={body}")

    def _wait_until_mergeable(self, pr_number: int, deadline: datetime) -> None:
        while True:
            self._check_deadline(deadline, "mergeable")
            pr = self._get_pr(pr_number)
            mergeable = pr.get("mergeable")
            if mergeable is True:
                return

            self._update_pr_branch(pr_number)
            time.sleep(3)

    def _wait_for_comment_state(self, pr_number: int, deadline: datetime, success_pattern: re.Pattern[str], failure_pattern: re.Pattern[str], stage: str) -> None:
        seen_comment_ids: set[int] = set()
        while True:
            self._check_deadline(deadline, stage)
            comments = self._list_comments(pr_number)
            for comment in comments:
                comment_id = int(comment.get("id", 0))
                if comment_id in seen_comment_ids:
                    continue
                seen_comment_ids.add(comment_id)
                body = str(comment.get("body") or "")
                if failure_pattern.search(body):
                    raise RuntimeError(f"{stage} failed: {body[:200]}")
                if success_pattern.search(body):
                    return
            time.sleep(5)

    def _post_apply_comment(self, pr_number: int, workspace: str) -> None:
        payload = {"body": f"{self.apply_comment} -w {workspace}"}
        status, body = _api_request(
            self.base_url,
            self.token,
            "POST",
            f"/api/v1/repos/{self.owner}/{self.repo}/issues/{pr_number}/comments",
            payload,
        )
        if status not in {200, 201}:
            raise RuntimeError(f"failed to post apply comment: status={status}, body={body}")
        if self.pause_after_comment:
            self._pause("apply")

    def _post_plan_comment(self, pr_number: int, workspace: str, workflow_id: str) -> None:
        payload = {"body": f"{self.plan_comment} -w {workspace} -- -var=workflow_id={workflow_id}"}
        status, body = _api_request(
            self.base_url,
            self.token,
            "POST",
            f"/api/v1/repos/{self.owner}/{self.repo}/issues/{pr_number}/comments",
            payload,
        )
        if status not in {200, 201}:
            raise RuntimeError(f"failed to post plan comment: status={status}, body={body}")
        if self.pause_after_comment:
            self._pause("plan")

    def _merge_pr(self, pr_number: int) -> None:
        payload = {
            "Do": "merge",
            "force_merge": False,
            "delete_branch_after_merge": True,
        }

        last_status = 0
        last_body: dict | list | str | None = None
        for attempt in range(6):
            status, body = _api_request(
                self.base_url,
                self.token,
                "POST",
                f"/api/v1/repos/{self.owner}/{self.repo}/pulls/{pr_number}/merge",
                payload,
            )
            if status in {200, 201}:
                return

            last_status = status
            last_body = body

            # Gitea can return transient 405/409 during concurrent merge races.
            if status in {405, 409}:
                payload["force_merge"] = True
                time.sleep(2)
                continue

            break

        raise RuntimeError(f"failed to merge PR {pr_number}: status={last_status}, body={last_body}")

    def _create_commit_and_pr(self, workflow_id: str, branch: str) -> int:
        remote = _git_remote_with_token(self.base_url, self.owner, self.repo, self.username, self.token)
        with tempfile.TemporaryDirectory(prefix="workflow-automation-") as tmp:
            _run(["git", "clone", "--branch", "main", remote, tmp])
            _run(["git", "checkout", "-b", branch], cwd=tmp)
            _run(["git", "config", "user.email", "automation@local"], cwd=tmp)
            _run(["git", "config", "user.name", "workflow-automation"], cwd=tmp)

            marker_path = _write_workflow_marker(Path(tmp), workflow_id)

            _run(["git", "add", marker_path.relative_to(Path(tmp)).as_posix()], cwd=tmp)
            _run(["git", "commit", "-m", f"chore: workflow {workflow_id}"], cwd=tmp)
            _run(["git", "push", "-u", "origin", branch], cwd=tmp)

        return self._open_pr(branch, workflow_id)

    def run_one(self, workflow_id: str | None = None) -> WorkflowResult:
        start_ts = _utc_now()
        deadline = start_ts + timedelta(minutes=self.timeout_minutes)

        wf_id = workflow_id or _new_workflow_id()
        branch = f"bench/{wf_id}"
        workspace = f"wf-{wf_id}"
        pr_number: int | None = None
        apply_comment_ts: datetime | None = None
        merged_ts: datetime | None = None

        try:
            pr_number = self._create_commit_and_pr(wf_id, branch)

            self._post_plan_comment(pr_number, workspace, wf_id)

            plan_success = re.compile(r"(plan successful|ran plan|no changes)", re.IGNORECASE)
            plan_failure = re.compile(r"(plan failed|error .*plan|failed plan)", re.IGNORECASE)
            self._wait_for_comment_state(pr_number, deadline, plan_success, plan_failure, "plan")

            self._pause_before_apply()

            self._wait_until_mergeable(pr_number, deadline)

            self._post_apply_comment(pr_number, workspace)
            apply_comment_ts = _utc_now()

            apply_success = re.compile(r"(apply complete|apply successful|ran apply)", re.IGNORECASE)
            apply_failure = re.compile(r"(apply failed|error .*apply|failed apply)", re.IGNORECASE)
            self._wait_for_comment_state(pr_number, deadline, apply_success, apply_failure, "apply")

            self._merge_pr(pr_number)
            merged_ts = _utc_now()

            return WorkflowResult(
                workflow_id=wf_id,
                workspace=workspace,
                branch=branch,
                pr_number=pr_number,
                start_timestamp=_iso(start_ts) or "",
                apply_comment_timestamp=_iso(apply_comment_ts),
                merged_timestamp=_iso(merged_ts),
                terminal_status="success",
                error_category=None,
            )
        except TimeoutError:
            return WorkflowResult(
                workflow_id=wf_id,
                workspace=workspace,
                branch=branch,
                pr_number=pr_number,
                start_timestamp=_iso(start_ts) or "",
                apply_comment_timestamp=_iso(apply_comment_ts),
                merged_timestamp=_iso(merged_ts),
                terminal_status="timeout",
                error_category="timeout",
            )
        except Exception as exc:  # pylint: disable=broad-except
            return WorkflowResult(
                workflow_id=wf_id,
                workspace=workspace,
                branch=branch,
                pr_number=pr_number,
                start_timestamp=_iso(start_ts) or "",
                apply_comment_timestamp=_iso(apply_comment_ts),
                merged_timestamp=_iso(merged_ts),
                terminal_status="failed",
                error_category=str(exc),
            )


def _write_results(results: list[WorkflowResult], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as f:
        for result in results:
            record = {
                "workflow_id": result.workflow_id,
                "workspace": result.workspace,
                "branch": result.branch,
                "pr_number": result.pr_number,
                "start_timestamp": result.start_timestamp,
                "apply_comment_timestamp": result.apply_comment_timestamp,
                "merged_timestamp": result.merged_timestamp,
                "terminal_status": result.terminal_status,
                "error_category": result.error_category,
            }
            f.write(json.dumps(record, separators=(",", ":")) + "\n")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run automated Atlantis benchmark workflows")
    parser.add_argument("--count", type=int, default=1, help="How many workflows to run")
    parser.add_argument("--parallel", type=int, default=1, help="Max parallel workflows")
    parser.add_argument("--timeout-minutes", type=int, default=10, help="Absolute timeout per workflow")
    parser.add_argument(
        "--results-file",
        default="artifacts/workflow-runs.jsonl",
        help="Path for JSONL workflow run records",
    )
    parser.add_argument(
        "--write-results",
        action="store_true",
        help="Write workflow results to --results-file (disabled by default)",
    )
    parser.add_argument(
        "--plan-comment",
        default=os.getenv("ATLANTIS_PLAN_COMMENT", "atlantis plan"),
        help="Comment command used to trigger plan",
    )
    parser.add_argument(
        "--apply-comment",
        default=os.getenv("ATLANTIS_APPLY_COMMENT", "atlantis apply"),
        help="Explicit apply comment command",
    )
    parser.add_argument(
        "--pause-after-comment",
        action="store_true",
        help="Pause after each PR comment and wait for keystroke to continue",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.count < 1:
        raise RuntimeError("--count must be >= 1")
    if args.parallel < 1:
        raise RuntimeError("--parallel must be >= 1")

    base_url = _env("GITEA_URL", "http://localhost:3000")
    token = _read_token()
    owner = _env("GITEA_OWNER", "atlantis")
    repo = _env("GITEA_REPO", "atlantis-benchmark")
    username = _env("GITEA_USERNAME", owner)

    runner = WorkflowRunner(
        base_url=base_url,
        token=token,
        owner=owner,
        repo=repo,
        username=username,
        plan_comment=args.plan_comment,
        apply_comment=args.apply_comment,
        timeout_minutes=args.timeout_minutes,
        pause_after_comment=args.pause_after_comment,
    )

    results: list[WorkflowResult] = []
    with ThreadPoolExecutor(max_workers=min(args.parallel, args.count)) as pool:
        futures = [pool.submit(runner.run_one) for _ in range(args.count)]
        for future in as_completed(futures):
            results.append(future.result())

    out_path = Path(args.results_file)
    if args.write_results:
        _write_results(results, out_path)

    success_count = sum(1 for r in results if r.terminal_status == "success")
    timeout_count = sum(1 for r in results if r.terminal_status == "timeout")
    failed_count = sum(1 for r in results if r.terminal_status == "failed")
    print(f"Completed workflows: success={success_count}, timeout={timeout_count}, failed={failed_count}")
    if args.write_results:
        print(f"Results written to {out_path}")
    else:
        print("Results file writing is disabled. Use --write-results to enable it.")

    return 0 if failed_count == 0 and timeout_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
