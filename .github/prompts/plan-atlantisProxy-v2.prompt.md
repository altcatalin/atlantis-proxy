## Plan V2: Atlantis Proxy Sticky Routing MVP

Build a Python FastAPI reverse proxy that enables horizontal Atlantis scaling by enforcing sticky routing only for POST /events webhook payloads representing pull request create, update, and comment activity. Sticky mappings are stored in Redis and keyed by repository plus PR number. The stack runs with Docker Compose using Gitea, Atlantis node A, Atlantis node B, Redis (ephemeral), and the proxy.

## Scope Contract

1. Sticky applies only when all conditions are true:
- Method is POST.
- Path is /events.
- Payload resolves to a pull request context with repo and PR number.
- Event classification is one of:
  - PR created
  - PR updated (synchronize/new commits)
  - PR comment created

2. Non-sticky passthrough applies to:
- POST /events payloads outside the sticky event set.
- Any non-POST /events request.
- Any request on paths other than /events.

3. Sticky persistence policy:
- No automatic expiry.
- Mapping changes only on backend failure or manual reset.

4. Failover policy:
- If mapped backend is unhealthy at dispatch time, immediately reassign to a healthy backend and continue forwarding.

## Event Filter Specification

The proxy should classify sticky-eligible events from incoming Gitea webhook payload and headers using a deterministic resolver.

Resolver output:
- event_type: pr_created | pr_updated | pr_comment_created | non_sticky
- sticky_key: pr:{owner}:{repo}:{pr_number} when eligible

Primary detection inputs:
- Header event type from Gitea event header.
- Payload fields that identify:
  - pull request object
  - repository owner/name
  - pull request number
  - action subtype when available

Eligibility rules:
1. PR created:
- Event indicates pull request activity and action is opened/created equivalent.

2. PR updated:
- Event indicates pull request activity and action is synchronize/updated equivalent.

3. PR comment created:
- Event indicates issue or pull request comment activity and payload confirms target is a pull request, with action created.

Fallback behavior:
- If payload cannot confidently produce repo plus PR number, classify as non_sticky.
- Non_sticky requests are routed randomly to a healthy backend.

## Architecture

Request flow:
Gitea -> Proxy -> Atlantis backends

State flow:
- Redis set stores healthy backend targets.
- Redis key-value stores sticky mapping from sticky_key to backend id.

Selection:
- First eligible event for unknown key uses round-robin among healthy backends.
- Existing mapping is reused while backend remains healthy.

## Implementation Phases

1. Runtime and topology
- Build Docker Compose for Gitea, proxy, Redis, Atlantis A, Atlantis B.
- Add health checks and dependency ordering.
- Add init script to register backend nodes into Redis set at startup.

2. Proxy core
- FastAPI app with async forwarding client.
- Router gate for sticky eligibility using resolver.
- Redis mapping service with no TTL.
- Round-robin allocator for first-seen sticky keys.
- Failover reassignment on unhealthy mapped node.

3. Observability and safety
- Structured logs with request id, event_type, sticky_key, selected backend, failover reason, upstream status.
- Health endpoint reporting process liveness and Redis connectivity.
- Input guardrails for malformed payloads and empty healthy-backend pool.

4. Validation and docs
- Unit tests for resolver classification and sticky key extraction.
- Unit tests for mapping reuse, round-robin assignment, failover reassignment.
- Integration tests in Compose for end-to-end routing behavior.
- Operator docs for startup, manual reset, and troubleshooting.

## Acceptance Criteria

1. Sticky routing criteria
- Two POST /events PR created payloads for same repo and PR go to same Atlantis backend.
- POST /events PR updated payload for that same repo and PR goes to same backend.
- POST /events PR comment created payload for that same repo and PR goes to same backend.

2. Distribution criteria
- First-seen sticky keys distribute across Atlantis A and B in round-robin order.

3. Non-sticky criteria
- POST /events payload not matching sticky filter is forwarded randomly to a healthy backend.
- Non-POST /events and non-/events requests are forwarded randomly to a healthy backend.

4. Failover criteria
- When mapped backend is unhealthy, next eligible sticky event is reassigned to a healthy backend and succeeds.

5. Operability criteria
- Health endpoint returns healthy when Redis reachable and app running.
- Logs include event_type, sticky_key when applicable, and selected backend for each request.

## Explicit Exclusions for MVP

- Webhook signature verification.
- Redis persistence and HA.
- Kubernetes deployment.
- Metrics and distributed tracing.

## Open Items for Refinement

1. Lock exact Gitea header names and payload field paths used by resolver.
2. Decide behavior if both backends unhealthy (503 versus buffered retry).
3. Decide whether to add admin endpoint for targeted sticky key reset in MVP or post-MVP.