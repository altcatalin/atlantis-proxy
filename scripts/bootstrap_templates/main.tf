terraform {
  required_version = ">= 1.5.0"

  required_providers {
    time = {
      source  = "hashicorp/time"
      version = "~> 0.11"
    }
  }
}

variable "workflow_id" {
  description = "Unique workflow identifier used for workspace isolation"
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{2,62}$", var.workflow_id))
    error_message = "workflow_id must be 3-63 chars and contain only lowercase letters, numbers, and dashes."
  }
}

variable "delay_seconds_override" {
  description = "Optional fixed delay override for debugging. Null uses deterministic delay from workflow_id."
  type        = number
  default     = null
  nullable    = true

  validation {
    condition     = var.delay_seconds_override == null || (var.delay_seconds_override >= 1 && var.delay_seconds_override <= 600)
    error_message = "delay_seconds_override must be null or between 1 and 600."
  }
}

locals {
  workspace_name = "wf-${var.workflow_id}"

  # Deterministic runtime spread (15-30s) based only on workflow_id.
  workflow_hash_byte = parseint(substr(md5(var.workflow_id), 0, 2), 16)
  delay_seconds      = 15 + (local.workflow_hash_byte % 16)

  effective_delay_seconds = var.delay_seconds_override == null ? local.delay_seconds : var.delay_seconds_override
}

resource "time_sleep" "workflow_delay" {
  create_duration = "${local.effective_delay_seconds}s"
}

output "workflow_id" {
  value = var.workflow_id
}

output "workspace_name" {
  value = local.workspace_name
}

output "current_workspace" {
  value = terraform.workspace
}

output "workspace_matches_expected" {
  value = terraform.workspace == local.workspace_name
}

output "deterministic_delay_seconds" {
  value = local.delay_seconds
}

output "effective_delay_seconds" {
  value = local.effective_delay_seconds
}
