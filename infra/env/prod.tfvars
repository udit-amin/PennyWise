env              = "prod"
aws_region       = "ap-south-1"
task_cpu         = 512
task_memory      = 1024
desired_count    = 1 # keep at 1 until the SQS job runner lands
use_fargate_spot = false

# Fill these in for your environment:
# domain_name         = "api.pennywise.app"
# route53_zone_id     = "Z0123456789ABCDEFGHIJ"
cors_origins       = "https://pennywise.app"
llm_model          = "claude-opus-4-8"
reasoning_effort   = "medium"
log_retention_days = 30
# alarm_email       = "alerts@pennywise.app"
