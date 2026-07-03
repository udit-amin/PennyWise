output "alb_dns_name" {
  description = "Public DNS of the load balancer."
  value       = aws_lb.this.dns_name
}

output "api_url" {
  description = "Base URL for the API."
  value       = local.enable_dns ? "https://${var.domain_name}" : "http://${aws_lb.this.dns_name}"
}

output "ecr_repository_url" {
  description = "Push images here; CI tags with the git SHA."
  value       = data.aws_ecr_repository.this.repository_url
}

output "ecs_cluster" {
  value = aws_ecs_cluster.this.name
}

output "ecs_service" {
  value = aws_ecs_service.app.name
}

output "table_prefix" {
  value = local.table_prefix
}

output "secret_names" {
  description = "Populate these with `aws secretsmanager put-secret-value` before first deploy."
  value = {
    anthropic_api_key    = aws_secretsmanager_secret.anthropic_api_key.name
    google_client_id     = aws_secretsmanager_secret.google_client_id.name
    google_client_secret = aws_secretsmanager_secret.google_client_secret.name
    credentials_key      = aws_secretsmanager_secret.credentials_key.name
  }
}
