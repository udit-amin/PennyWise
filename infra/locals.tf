locals {
  name = "pennywise-${var.env}"

  # Mirrors PENNYWISE_TABLE_PREFIX consumed by pennywise/api/db.py.
  table_prefix = "pennywise_${var.env}_"

  enable_dns = var.domain_name != "" && var.route53_zone_id != ""

  # Request a cert if a domain is given but no cert ARN was supplied.
  create_cert = var.domain_name != "" && var.acm_certificate_arn == ""

  # Resolved cert for the HTTPS listener: provided ARN, or the one we create.
  certificate_arn = var.acm_certificate_arn != "" ? var.acm_certificate_arn : (
    local.create_cert ? aws_acm_certificate.this[0].arn : ""
  )

  enable_https = local.certificate_arn != ""
}

