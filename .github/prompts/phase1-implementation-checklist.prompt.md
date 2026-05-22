## Phase 1 Implementation Checklist (Scope: points 2, 3, 4, 6)

### Locked decisions
1. Apply trigger: explicit comment.
2. Isolation: workspace-per-workflow.
3. Completion event: PR merged.
4. Timeout: 10 minutes per workflow (absolute from PR creation).

### 1) Gitea bootstrap (fixed repo)
1. Create or verify one benchmark repo.
2. Create or verify automation user/token and Atlantis access.
3. Configure webhook to proxy endpoint.
4. Seed baseline repo content (Terraform + Atlantis config).
5. Verify idempotency by rerunning bootstrap.

Acceptance:
1. Bootstrap can be rerun safely.
2. Repo, webhook, and permissions are correct after rerun.

### 2) Atlantis behavior (explicit apply comment)
1. Configure autoplan on PR open/update.
2. Configure apply to run only on explicit comment command.
3. Define one canonical apply comment string.
4. Enforce PR merge only after successful apply.

Acceptance:
1. PR open/update triggers plan.
2. Apply does not run without explicit comment.
3. PR can be merged only after successful apply.

### 3) Terraform workload (workspace-per-workflow)
1. Define workflow ID format.
2. Map workflow ID to workspace name (wf-<id> pattern).
3. Add deterministic delay mechanism in workload.
4. Keep workload low-cost and stable.
5. Ensure independent workflows do not share state.

Acceptance:
1. Parallel workflows use different workspaces.
2. Apply in one workflow does not lock/block another.
3. Runtime is stable enough for later comparison.

### 4) Workflow automation (branch/commit/push/PR/apply/merge)
1. Implement lifecycle:
   1. Create branch.
   2. Mutate inputs for workflow ID.
   3. Commit + push.
   4. Open PR.
   5. Wait for plan success.
   6. Post explicit apply comment.
   7. Wait for apply success.
   8. Merge PR.
   9. Store result.
2. Apply 10-minute per-workflow timeout.
3. Classify terminal states: success, timeout, failed.

Acceptance:
1. End-to-end flow completes automatically.
2. Timeout triggers at 10 minutes and records timeout-failed.
3. Non-timeout failures are separately classified.

### 5) Result schema and run logging
1. Record per workflow:
   1. workflow_id
   2. workspace
   3. branch
   4. pr_number
   5. start_timestamp
   6. apply_comment_timestamp
   7. merged_timestamp
   8. terminal_status
   9. error_category
2. Persist run artifacts for later benchmark phase.

Acceptance:
1. Every workflow has exactly one terminal record.
2. Timestamps are complete and consistent.

### 6) Readiness gate (before benchmark phase)
1. Bootstrap is repeatable.
2. Atlantis plan/apply policy behaves exactly as defined.
3. Workspace-per-workflow isolation is validated under concurrency.
4. 10-minute timeout handling is correct.
5. Full automation runs without manual intervention.
