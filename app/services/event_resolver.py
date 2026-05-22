from dataclasses import dataclass
from typing import Any


PR_CREATED_ACTIONS = {"opened", "open", "created", "create"}
PR_UPDATED_ACTIONS = {"synchronize", "synchronized", "updated", "update", "edited", "edit"}
PR_CLOSED_ACTIONS = {"closed", "close"}
PR_MERGED_ACTIONS = {"merged", "merge"}
COMMENT_CREATED_ACTIONS = {"created", "create"}


@dataclass(frozen=True)
class EventResolution:
    event_type: str
    sticky_key: str | None


class EventResolver:
    def resolve(self, headers: dict[str, str], payload: dict[str, Any]) -> EventResolution:
        event_name = self._event_name(headers)
        action = str(payload.get("action", "")).lower()

        if event_name == "pull_request":
            sticky_key = self._sticky_key_from_pull_request(payload)
            if not sticky_key:
                return EventResolution(event_type="non_sticky", sticky_key=None)
            if action in PR_CREATED_ACTIONS:
                return EventResolution(event_type="pr_created", sticky_key=sticky_key)
            if action in PR_UPDATED_ACTIONS:
                return EventResolution(event_type="pr_updated", sticky_key=sticky_key)
            if action in PR_MERGED_ACTIONS:
                return EventResolution(event_type="pr_merged", sticky_key=sticky_key)
            if action in PR_CLOSED_ACTIONS:
                merged = bool((payload.get("pull_request") or {}).get("merged"))
                event_type = "pr_merged" if merged else "pr_closed"
                return EventResolution(event_type=event_type, sticky_key=sticky_key)
            return EventResolution(event_type="non_sticky", sticky_key=None)

        if event_name in {"issue_comment", "pull_request_comment"}:
            sticky_key = self._sticky_key_from_comment(payload)
            if sticky_key and action in COMMENT_CREATED_ACTIONS:
                return EventResolution(event_type="pr_comment_created", sticky_key=sticky_key)

        return EventResolution(event_type="non_sticky", sticky_key=None)

    def _event_name(self, headers: dict[str, str]) -> str:
        header_keys = ["x-gitea-event", "x_gitea_event", "x-github-event"]
        lowered = {k.lower(): v for k, v in headers.items()}
        for key in header_keys:
            value = lowered.get(key)
            if value:
                return str(value).strip().lower()
        return ""

    def _sticky_key_from_pull_request(self, payload: dict[str, Any]) -> str | None:
        repo = payload.get("repository") or {}
        pr = payload.get("pull_request") or {}

        owner = self._repo_owner(repo)
        repo_name = repo.get("name")
        pr_number = pr.get("number")

        if not (owner and repo_name and pr_number):
            return None

        return f"pr:{owner}:{repo_name}:{pr_number}"

    def _sticky_key_from_comment(self, payload: dict[str, Any]) -> str | None:
        repo = payload.get("repository") or {}
        owner = self._repo_owner(repo)
        repo_name = repo.get("name")

        issue = payload.get("issue") or {}
        pull_request_marker = issue.get("pull_request")
        pr_number = issue.get("number")

        # Some providers include pull_request directly in comment payloads.
        if not pull_request_marker and payload.get("pull_request"):
            pull_request_marker = payload.get("pull_request")
            pr_number = payload.get("pull_request", {}).get("number") or pr_number

        if not (owner and repo_name and pull_request_marker and pr_number):
            return None

        return f"pr:{owner}:{repo_name}:{pr_number}"

    def _repo_owner(self, repo: dict[str, Any]) -> str | None:
        owner = repo.get("owner") or {}
        return owner.get("username") or owner.get("login") or owner.get("name")
