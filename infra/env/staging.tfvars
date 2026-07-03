env              = "staging"
aws_region       = "ap-south-1"
task_cpu         = 512
task_memory      = 1024
desired_count    = 1
use_fargate_spot = true # cost-optimized; staging tolerates interruptions

# Fill these in for your environment:
# domain_name         = "staging-api.pennywise.app"
# route53_zone_id     = "Z0123456789ABCDEFGHIJ"
cors_origins       = "https://staging.pennywise.app"
llm_model          = "claude-opus-4-8"
reasoning_effort   = "medium"
log_retention_days = 14
# alarm_email       = "alerts@pennywise.app"
