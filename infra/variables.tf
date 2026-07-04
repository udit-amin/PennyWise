variable "aws_region" {
  type    = string
  default = "ap-south-1"
}

variable "env" {
  type        = string
  description = "Deployment environment. Should match the Terraform workspace."
  validation {
    condition     = contains(["dev", "staging", "prod"], var.env)
    error_message = "env must be one of: dev, staging, prod."
  }
}

variable "domain_name" {
  type        = string
  description = "Public hostname for the API, e.g. api.pennywise.app. Empty disables Route53 + ACM (use the ALB DNS name directly)."
  default     = ""
}

variable "route53_zone_id" {
  type        = string
  description = "Existing Route53 hosted zone id for domain_name. Required if domain_name is set."
  default     = ""
}

variable "acm_certificate_arn" {
  type        = string
  description = "ACM cert ARN for the HTTPS listener. If empty and domain_name is set, one is requested + DNS-validated."
  default     = ""
}

# ── Compute sizing (per env via tfvars) ───────────────────────────────

variable "task_cpu" {
  type    = number
  default = 512 # 0.5 vCPU
}

variable "task_memory" {
  type    = number
  default = 1024 # 1 GB
}

variable "desired_count" {
  type        = number
  default     = 1
  description = "Keep at 1 until the SQS-backed job runner lands (in-memory jobs don't survive >1 task)."
}

variable "use_fargate_spot" {
  type        = bool
  default     = false
  description = "Run tasks on Fargate Spot. Recommended for staging to cut cost."
}

variable "image_tag" {
  type = string
  # No default, deliberately. CI always passes this explicitly (the git
  # SHA it just built). A human running `terraform apply` by hand who
  # forgets -var="image_tag=..." would otherwise silently regress the
  # running service onto a "latest" tag that doesn't exist in ECR (CI only
  # ever pushes SHA-tagged images) — the new task then fails to pull its
  # image, invisibly to application logs, and the deployment circuit
  # breaker rolls back. Better to fail the apply loudly and immediately.
  description = "ECR image tag to deploy. CI sets this to the git SHA; always pass explicitly for manual applies."
}

# ── App config (non-secret) ───────────────────────────────────────────

variable "cors_origins" {
  type        = string
  description = "Comma-separated allowed CORS origins (frontend URLs)."
  default     = ""
}

variable "llm_model" {
  type    = string
  default = "claude-opus-4-8"
}

variable "reasoning_effort" {
  type    = string
  default = "medium"
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "alarm_email" {
  type        = string
  description = "Email subscribed to the CloudWatch alarm SNS topic. Empty disables notifications."
  default     = ""
}
