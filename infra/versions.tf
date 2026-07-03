terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Remote state. Create the bucket + lock table once, out of band, then
  # `terraform init`. Workspaces (dev/staging/prod) keep state files separate
  # under the same bucket via the `workspace_key_prefix`.
  backend "s3" {
    bucket       = "pennywise-tfstate"
    key          = "pennywise/terraform.tfstate"
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
      Env       = terraform.workspace
      ManagedBy = "Terraform"
    }
  }
}
