## Plan: Atlantis Proxy Sticky Routing MVP

Build a Python FastAPI reverse proxy that enables horizontal Atlantis scaling by enforcing sticky routing only for POST /events that are PR create/update/comment webhook events, using Redis-backed PR mappings. Run the stack with Docker Compose using Gitea, 2 Atlantis nodes, Redis (ephemeral), and the proxy. Sticky assignment is effectively forever (no TTL), reassigned only on node failure or manual reset. Non-matching events plus all other paths/methods are passthrough-routed to a random healthy node.

**Steps**
1. Phase 1 - Scaffold runtime and contracts.
2. Define proxy runtime contract: env vars, endpoint behavior, and routing matrix (sticky only for POST /events PR create/update/comment events; all else random passthrough).
3. Create container topology in Docker Compose for Gitea, Redis, proxy, Atlantis node A, Atlantis node B, with startup ordering and health checks.
4. Implement node-registration init script that writes available Atlantis backends into a Redis set on startup.
5. Phase 2 - Core sticky router implementation.
6. Implement request intake and forwarding pipeline in FastAPI with async HTTP client and transparent body/header forwarding.
7. Implement sticky key extraction from Gitea PR webhook payload as repo+PR identifier.
8. Implement mapping lifecycle in Redis: lookup mapping -> validate backend health -> select backend via round-robin for misses -> persist mapping without expiry.
9. Implement failover behavior: if mapped node unhealthy, reassign immediately to healthy node and continue forwarding.
10. Implement non-sticky passthrough: any POST /events webhook that is not PR create/update/comment, plus any non-POST /events request or non-/events path, forwards to a random healthy node.
11. Phase 3 - Reliability and observability.
12. Add structured logging for request id, sticky key, chosen node, failover events, and upstream status codes.
13. Add proxy health endpoint with checks for process health and Redis reachability.
14. Add simple safeguards for malformed payloads and unavailable backend pool (clear 4xx/5xx behavior).
15. Phase 4 - Validation and handoff.
16. Add automated tests for sticky assignment persistence on PR create/update/comment events, round-robin assignment for first-seen PRs, failover reassignment, and random passthrough behavior for non-matching events.
17. Add integration test flow in Compose: generate representative webhook calls and verify node affinity across repeated PR create/update/comment events.
18. Write operator docs for startup, backend registration, manual mapping reset, and troubleshooting failover.

**Relevant files**
- Repository root compose definition for all containers and health checks.
- Proxy application package with modules for config, routing, sticky store, backend pool, and forwarding client.
- Container init scripts for Redis backend-node registration.
- Test suite for unit and integration behavior checks.
- README/operations docs for local runbook and expected routing behavior.

**Verification**
1. Bring up stack and verify all services report healthy; confirm backend set exists in Redis.
2. Send repeated POST /events PR create/update/comment payloads for the same repo+PR and verify all requests hit the same Atlantis node.
3. Send POST /events PR create/update/comment payloads for different PRs and verify initial assignment alternates by round-robin.
4. Simulate failure of mapped Atlantis node and verify immediate reassignment on next matching PR event.
5. Send non-matching POST /events payloads plus non-POST /events and non-/events requests, and verify random healthy-node forwarding.
6. Validate logs include sticky key, selected backend, and failover reason when applicable.

**Decisions**
- Included scope: MVP sticky routing only for PR create/update/comment webhooks on POST /events, 2 Atlantis nodes, Redis-backed mapping with no expiry, Docker Compose deployment.
- Excluded scope: webhook signature verification, Redis persistence/HA, Kubernetes deployment, metrics/tracing.
- Sticky key: repo+PR identity from Gitea PR events.
- Backend source of truth: Redis set populated by container init script.

**Further Considerations**
1. Optional hardening after MVP: add webhook signature validation toggle and Redis persistence.
2. Optional scaling improvement: replace random passthrough with least-loaded routing if load metrics become available.
3. Optional operability: add admin endpoints for mapping inspection and targeted reset.