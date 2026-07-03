# ECR is shared across all environments (build once, promote the same image
# staging -> prod), so it is provisioned by infra/shared/ (its own state) and
# only referenced here.

data "aws_ecr_repository" "this" {
  name = "pennywise"
}
