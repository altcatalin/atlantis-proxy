# atlantis-proxy

PoC proxy for Atlantis horizontal scaling with sticky routing of PR-related webhook traffic. This repository is a proof of concept focused on behavior, not deep optimization.

Problems:
- Plan/apply artifacts are node-local by default, so apply can fail if it lands on a different node than plan. Shared storage looks like a quick fix, but under high I/O and many nodes it can become a bottleneck because every plan/apply must do frequent metadata and file operations on the same backend.
- Atlantis relies on locking to prevent conflicting operations:
	- Directory/workspace locking: Atlantis locks a directory/workspace during operations. If another user attempts to plan for the same directory and workspace in a different pull request they'll get blocked and a link to the pull request that holds the lock.
	- Repo-level operation locking: Atlantis also guards against overlapping operations for the same PR, which can fail in multi-node mode if operation state is not shared.

In real multi-node workloads, lock state in Redis plus deterministic sticky routing is typically more scalable than relying on shared filesystem semantics for plan artifacts, because it minimizes cross-node file contention and reduces remote storage latency impact.

Primary goals:
- Demonstrate Atlantis horizontal scaling with a proxy in front.
- Demonstrate sticky PR routing and failover when a node is unavailable.

Out of scope for this PoC:
- Performance tuning of Terraform provider cache/cold start behavior.
- Complex repository folder conventions.
- Production-grade scheduling and autoscaling policies.

## Architecture at a glance

- `gitea`: local VCS and webhook source.
- `atlantis-a`, `atlantis-b`: 2 Atlantis nodes.
- `proxy`: FastAPI reverse proxy that does sticky routing decisions.
- `redis`: shared state for backend registration, sticky mappings, and directory/workspace operation locking.
- `prometheus`, `grafana`: observability.

## How it works

### 1) Node registration (PoC behavior)

On startup, each Atlantis node registers itself in Redis set `atlantis:backends` via `scripts/atlantis-entrypoint.sh`.

On shutdown signal, the entrypoint deregisters it from the same set.

This registration model is intentionally simple and suitable for PoC demonstration.

### 2) Routing behavior

The proxy:
- Uses sticky routing only for `POST /events` PR-related events.
- Uses key format `pr:{owner}:{repo}:{pr_number}` for sticky mapping.
- Uses Redis for both sticky mappings and round-robin index.
- Routes non-sticky traffic to a random healthy backend.

### 3) Node loss behavior

If a sticky-mapped node is gone/unhealthy:
- The proxy selects another healthy Atlantis node.
- Sticky mapping is updated to the new node.
- If no healthy backend exists, proxy returns `503`.

Proxy messaging in this PoC:
- The proxy logs one routing line per request including selected backend and sticky context.
- This acts as the observable "message" when failover/reroute occurs.

## Terraform workspace usage (why it exists)

This PoC uses one Terraform workspace per workflow (`wf-<workflow_id>`) to isolate state between concurrent runs.

Why:
- Prevents cross-workflow lock contention on a single workspace.
- Makes concurrent plan/apply behavior easier to reason about.
- Keeps benchmark automation deterministic and reproducible.

The included Terraform template is intentionally minimal and low-cost.

## Run the stack

Prerequisites: a container runtime and a Compose-compatible orchestrator are required (tested with Docker Engine + Docker Compose v2; similar tooling like Podman + podman-compose may also work).

1. Start all services:

```bash
docker compose up -d --build
```

Endpoints:
- Proxy: [http://localhost:8000](http://localhost:8000); webhook endpoint: [http://localhost:8000/events](http://localhost:8000/events)
- Atlantis: [http://localhost:4141](http://localhost:4141) & [http://localhost:4142](http://localhost:4142)
- Gitea: [http://localhost:3000](http://localhost:3000); user & password: `atlantis`; repository: [http://localhost:3000/atlantis/atlantis-benchmark](http://localhost:3000/atlantis/atlantis-benchmark)
- Prometheus: [http://localhost:9090](http://localhost:9090)
- Grafana: [http://localhost:3001](http://localhost:3001)

## Run workflow automation

Use the `repo-bootstrap` container image to run automation from inside Docker.

```bash
docker compose run --rm --no-deps repo-bootstrap \
	python3 scripts/run_workflow_automation.py --count 11 --parallel 3
```

Notes:
- The container already gets `GITEA_TOKEN_FILE=/data/gitea/atlantis_token` from Compose.
- `--pause-after-comment` can be added for interactive step-by-step runs.
- A fixed 5-second pause is applied between successful plan and apply in each workflow.

Results:

1. Gitea
[http://localhost:3000/atlantis/atlantis-benchmark/pulls][http://localhost:3000/atlantis/atlantis-benchmark/pulls]
[./artifacts/gitea.png](./artifacts/gitea.png)

2. Atlantis
[http://localhost:4141](http://localhost:4141) & [http://localhost:4142](http://localhost:4142)
[./artifacts/atlantis-a.png](./artifacts/atlantis-a.png)
[./artifacts/atlantis-b.png](./artifacts/atlantis-b.png)

3. Grafana
[http://localhost:3001/explore?schemaVersion=1&panes=%7B%22odp%22:%7B%22datasource%22:%22PBFA97CFB590B2093%22,%22queries%22:%5B%7B%22refId%22:%22C%22,%22expr%22:%22atlantis_builder_projects%22,%22range%22:true,%22instant%22:true,%22datasource%22:%7B%22type%22:%22prometheus%22,%22uid%22:%22PBFA97CFB590B2093%22%7D,%22editorMode%22:%22code%22,%22legendFormat%22:%22%7B%7Bjob%7D%7D%22,%22hide%22:false%7D,%7B%22refId%22:%22D%22,%22expr%22:%22sum%28atlantis_builder_projects%29%22,%22range%22:true,%22instant%22:true,%22datasource%22:%7B%22type%22:%22prometheus%22,%22uid%22:%22PBFA97CFB590B2093%22%7D,%22editorMode%22:%22code%22,%22legendFormat%22:%22all%22,%22hide%22:false%7D%5D,%22range%22:%7B%22from%22:%22now-5m%22,%22to%22:%22now%22%7D,%22compact%22:false%7D%7D&orgId=1](http://localhost:3001/explore?schemaVersion=1&panes=%7B%22odp%22:%7B%22datasource%22:%22PBFA97CFB590B2093%22,%22queries%22:%5B%7B%22refId%22:%22C%22,%22expr%22:%22atlantis_builder_projects%22,%22range%22:true,%22instant%22:true,%22datasource%22:%7B%22type%22:%22prometheus%22,%22uid%22:%22PBFA97CFB590B2093%22%7D,%22editorMode%22:%22code%22,%22legendFormat%22:%22%7B%7Bjob%7D%7D%22,%22hide%22:false%7D,%7B%22refId%22:%22D%22,%22expr%22:%22sum%28atlantis_builder_projects%29%22,%22range%22:true,%22instant%22:true,%22datasource%22:%7B%22type%22:%22prometheus%22,%22uid%22:%22PBFA97CFB590B2093%22%7D,%22editorMode%22:%22code%22,%22legendFormat%22:%22all%22,%22hide%22:false%7D%5D,%22range%22:%7B%22from%22:%22now-5m%22,%22to%22:%22now%22%7D,%22compact%22:false%7D%7D&orgId=1)
[./artifacts/grafana.png](./artifacts/grafana.png)


## Failover test

Run the workflow automation and stop one node, for example:

```bash
docker compose stop atlantis-b
```

Notes:
- The newly selected node adds an error comment indicating that the action was handled after failover.
- One can restart the process on the new node.

Results:

1. Gitea
[http://localhost:3000/atlantis/atlantis-benchmark/pulls][http://localhost:3000/atlantis/atlantis-benchmark/pulls]
[./artifacts/gitea_failover_list.png](./artifacts/gitea_failover_list.png)
[./artifacts/gitea_failover_pr.png](./artifacts/gitea_failover_pr.png)

## Team-oriented routing option

This PoC routes by PR identity key. A common next step is routing by PR path/team ownership so different teams can be pinned to dedicated Atlantis nodes or pools.

Example strategy:
- Team A repos or paths -> Atlantis pool A
- Team B repos or paths -> Atlantis pool B

This is not fully implemented here, but the proxy routing layer is where that policy belongs.

## Run tests

Run tests from the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. pytest -q
```

Note:
- `PYTHONPATH=.` is required so test imports like `app.*` resolve correctly.
