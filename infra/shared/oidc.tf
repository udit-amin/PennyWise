# GitHub Actions → AWS via OIDC. No long-lived IAM user access keys anywhere
# in this project: CI assumes this role for the duration of a workflow run,
# scoped to exactly the deploy actions it performs.
#
# One role, shared by deploy-staging.yml and deploy-prod.yml (both call
# _deploy.yml, which declares `environment: <staging|production>` on the
# job — GitHub's OIDC token then carries that as the `sub` claim, which the
# trust policy below checks). rollback.yml uses the same role the same way.

data "aws_caller_identity" "current" {}

locals {
  github_repo = "udit-amin/PennyWise"
  # Per-env stack uses these two names (infra/locals.tf: name = "pennywise-${var.env}").
  deploy_envs = ["staging", "prod"]
  # GitHub Environment names referenced by the `environment:` key in the
  # deploy/rollback workflow jobs — "prod" the AWS env is "production" as a
  # GitHub Environment (matches deploy-prod.yml / rollback.yml).
  github_environments = ["staging", "production"]
}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # Root CA thumbprint for token.actions.githubusercontent.com (currently
  # Let's Encrypt ISRG Root X1 — verified via a live TLS handshake; GitHub
  # has changed CAs before). Terraform's schema requires a 40-hex-char
  # value here, but AWS IAM has validated GitHub's OIDC tokens against its
  # own managed trust store (not this field) since 2023, so a future CA
  # rotation on GitHub's end will not break authentication even if this
  # value goes stale.
  thumbprint_list = ["22ff89586561fc2d52f77491e9f1eff1b80be33e"]
}

data "aws_iam_policy_document" "deploy_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [for env in local.github_environments : "repo:${local.github_repo}:environment:${env}"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name               = "pennywise-deploy"
  assume_role_policy = data.aws_iam_policy_document.deploy_assume.json
}

# ── Least-privilege deploy policy ───────────────────────────────────────
# Exactly the actions the deploy/rollback workflows perform: push images,
# roll the ECS service via a new task-definition revision, and pass the
# app's own roles to ECS. Nothing else — no broad ecs:*, no iam:*.

locals {
  account_id = data.aws_caller_identity.current.account_id

  ecs_cluster_arns = [
    for env in local.deploy_envs : "arn:aws:ecs:${var.aws_region}:${local.account_id}:cluster/pennywise-${env}"
  ]
  ecs_service_arns = [
    for env in local.deploy_envs : "arn:aws:ecs:${var.aws_region}:${local.account_id}:service/pennywise-${env}/pennywise-${env}"
  ]
  passable_role_arns = flatten([
    for env in local.deploy_envs : [
      "arn:aws:iam::${local.account_id}:role/pennywise-${env}-execution",
      "arn:aws:iam::${local.account_id}:role/pennywise-${env}-task",
    ]
  ])
  # Secrets Manager appends a random 6-char suffix to every ARN; wildcarding
  # just that suffix (not the whole ARN) is AWS's documented pattern for
  # scoping IAM to a fixed secret name. Granted so _deploy.yml can verify
  # secrets are populated before rolling — note this isn't a materially new
  # exposure: this role can already deploy arbitrary images, and a deployed
  # container receives these same secrets via the execution role, so a
  # compromised deploy role could exfiltrate them either way.
  secret_read_arns = flatten([
    for env in local.deploy_envs : [
      for name in ["jwt-secret", "anthropic-api-key", "google-client-id", "google-client-secret", "credentials-key"] :
      "arn:aws:secretsmanager:${var.aws_region}:${local.account_id}:secret:pennywise-${env}-${name}-*"
    ]
  ])
}

data "aws_iam_policy_document" "deploy" {
  # ECR: auth token is account-wide by AWS's own requirement; push/pull is
  # scoped to the one shared repository.
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
      "ecr:PutImage",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
    ]
    resources = [aws_ecr_repository.this.arn]
  }

  # DescribeServices / UpdateService support resource-level scoping —
  # restricted to exactly the two clusters/services this project owns.
  statement {
    actions   = ["ecs:DescribeServices", "ecs:UpdateService"]
    resources = concat(local.ecs_cluster_arns, local.ecs_service_arns)
  }
  # RegisterTaskDefinition/DescribeTaskDefinition act on a family named in
  # the request body, not an ARN — AWS does not support resource-level
  # scoping for them (they require Resource "*"). The PassRole condition
  # below is what actually bounds which roles a registered task definition
  # can run as.
  statement {
    actions   = ["ecs:RegisterTaskDefinition", "ecs:DescribeTaskDefinition"]
    resources = ["*"]
  }

  statement {
    actions   = ["iam:PassRole"]
    resources = local.passable_role_arns
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = local.secret_read_arns
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "deploy-scoped"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}

output "deploy_role_arn" {
  description = "Set this as the GitHub Actions repo secret AWS_DEPLOY_ROLE_ARN."
  value       = aws_iam_role.deploy.arn
}
