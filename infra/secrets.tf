# Secrets Manager entries, injected into the task as env vars by ECS (never
# stored in the task definition in plaintext). JWT_SECRET is generated here;
# the rest are populated out of band after `terraform apply`:
#
#   aws secretsmanager put-secret-value --secret-id pennywise-<env>-anthropic-api-key --secret-string '...'
#
# Until populated, the service will fail readiness — by design.

resource "random_password" "jwt_secret" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "jwt_secret" {
  name = "${local.name}-jwt-secret"
}

resource "aws_secretsmanager_secret_version" "jwt_secret" {
  secret_id     = aws_secretsmanager_secret.jwt_secret.id
  secret_string = random_password.jwt_secret.result
}

resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name = "${local.name}-anthropic-api-key"
}

resource "aws_secretsmanager_secret" "google_client_id" {
  name = "${local.name}-google-client-id"
}

resource "aws_secretsmanager_secret" "google_client_secret" {
  name = "${local.name}-google-client-secret"
}

locals {
  # Secrets surfaced to the container, mapped to the env var names the app reads.
  container_secrets = {
    JWT_SECRET           = aws_secretsmanager_secret.jwt_secret.arn
    ANTHROPIC_API_KEY    = aws_secretsmanager_secret.anthropic_api_key.arn
    GOOGLE_CLIENT_ID     = aws_secretsmanager_secret.google_client_id.arn
    GOOGLE_CLIENT_SECRET = aws_secretsmanager_secret.google_client_secret.arn
  }
  secret_arns = values(local.container_secrets)
}
