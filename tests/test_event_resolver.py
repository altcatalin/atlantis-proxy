from app.services.event_resolver import EventResolver


def test_resolves_pr_created() -> None:
    resolver = EventResolver()
    headers = {"X-Gitea-Event": "pull_request"}
    payload = {
        "action": "opened",
        "repository": {"name": "infra", "owner": {"username": "acme"}},
        "pull_request": {"number": 17},
    }

    result = resolver.resolve(headers, payload)

    assert result.event_type == "pr_created"
    assert result.sticky_key == "pr:acme:infra:17"


def test_resolves_pr_updated() -> None:
    resolver = EventResolver()
    headers = {"X-Gitea-Event": "pull_request"}
    payload = {
        "action": "synchronize",
        "repository": {"name": "infra", "owner": {"username": "acme"}},
        "pull_request": {"number": 17},
    }

    result = resolver.resolve(headers, payload)

    assert result.event_type == "pr_updated"
    assert result.sticky_key == "pr:acme:infra:17"


def test_resolves_pr_closed_as_sticky() -> None:
    resolver = EventResolver()
    headers = {"X-Gitea-Event": "pull_request"}
    payload = {
        "action": "closed",
        "repository": {"name": "infra", "owner": {"username": "acme"}},
        "pull_request": {"number": 17, "merged": False},
    }

    result = resolver.resolve(headers, payload)

    assert result.event_type == "pr_closed"
    assert result.sticky_key == "pr:acme:infra:17"


def test_resolves_pr_merged_as_sticky() -> None:
    resolver = EventResolver()
    headers = {"X-Gitea-Event": "pull_request"}
    payload = {
        "action": "closed",
        "repository": {"name": "infra", "owner": {"username": "acme"}},
        "pull_request": {"number": 17, "merged": True},
    }

    result = resolver.resolve(headers, payload)

    assert result.event_type == "pr_merged"
    assert result.sticky_key == "pr:acme:infra:17"


def test_resolves_pr_comment_created() -> None:
    resolver = EventResolver()
    headers = {"X-Gitea-Event": "issue_comment"}
    payload = {
        "action": "created",
        "repository": {"name": "infra", "owner": {"username": "acme"}},
        "issue": {"number": 17, "pull_request": {"url": "https://gitea/pr/17"}},
    }

    result = resolver.resolve(headers, payload)

    assert result.event_type == "pr_comment_created"
    assert result.sticky_key == "pr:acme:infra:17"


def test_non_matching_event_is_non_sticky() -> None:
    resolver = EventResolver()
    headers = {"X-Gitea-Event": "push"}
    payload = {"repository": {"name": "infra", "owner": {"username": "acme"}}}

    result = resolver.resolve(headers, payload)

    assert result.event_type == "non_sticky"
    assert result.sticky_key is None
