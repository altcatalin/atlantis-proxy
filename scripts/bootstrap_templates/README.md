# Atlantis Benchmark Repository Template

This repository is seeded automatically by the local bootstrap utility.

It provides a fixed baseline for PR workflow automation and Atlantis tests.

## Point 3 behavior

- Workflow ID format: lowercase letters, digits, and dashes (3-63 chars).
- Workspace mapping rule: `wf-<workflow_id>`.
- Delay mechanism: deterministic from `workflow_id` (15-30 seconds).
- Optional override: set `delay_seconds_override` for debugging only.

The Terraform outputs expose `workflow_id`, expected workspace name,
current workspace, and delay values to support run logging and validation.
