# Shared, account-global resources applied ONCE (not per-env workspace):
# the ECR repository that all environments pull from.
#
#   cd infra/shared && terraform init && terraform apply
#
# Uses a distinct state key so it never collides with the per-env stack.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
  }
  backend "s3" {
    bucket       = "pennywise-tfstate"
    key          = "pennywise/shared/terraform.tfstate"
    region       = "ap-south-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project   = "PennyWise"
      Scope     = "shared"
      ManagedBy = "Terraform"
    }
  }
}

variable "aws_region" {
  type    = string
  default = "ap-south-1"
}

resource "aws_ecr_repository" "this" {
  name                 = "pennywise"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  repository = aws_ecr_repository.this.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 20 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 20
      }
      action = { type = "expire" }
    }]
  })
}

output "ecr_repository_url" {
  value = aws_ecr_repository.this.repository_url
}
