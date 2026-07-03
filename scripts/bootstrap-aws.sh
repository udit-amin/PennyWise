#!/usr/bin/env bash
# One-time, idempotent setup of the Terraform state backend that every
# infra/ and infra/shared/ config expects to already exist (see the
# `backend "s3"` blocks in infra/versions.tf and infra/shared/main.tf).
#
# Run this ONCE, after IAM Identity Center / SSO admin access is set up
# (see docs/AWS_SETUP.md) and BEFORE the first `terraform init` anywhere
# in this repo. Safe to re-run — every step checks current state first.
#
#   AWS_REGION=ap-south-1 scripts/bootstrap-aws.sh
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
BUCKET="pennywise-tfstate"

command -v aws >/dev/null || { echo "aws CLI not found — install it first." >&2; exit 1; }

echo "Using AWS identity:"
aws sts get-caller-identity --query '[Account, Arn]' --output text

if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  echo "Bucket $BUCKET already exists — skipping creation."
else
  echo "Creating $BUCKET in $REGION..."
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION"
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration "LocationConstraint=$REGION"
  fi
fi

echo "Enabling versioning (state history / recovery from bad applies)..."
aws s3api put-bucket-versioning --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled

echo "Enabling default encryption (SSE-S3)..."
aws s3api put-bucket-encryption --bucket "$BUCKET" \
  --server-side-encryption-configuration '{
    "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
  }'

echo "Blocking all public access..."
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

echo "Enforcing TLS-only access..."
aws s3api put-bucket-policy --bucket "$BUCKET" --policy "$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "DenyInsecureTransport",
    "Effect": "Deny",
    "Principal": "*",
    "Action": "s3:*",
    "Resource": ["arn:aws:s3:::$BUCKET", "arn:aws:s3:::$BUCKET/*"],
    "Condition": {"Bool": {"aws:SecureTransport": "false"}}
  }]
}
JSON
)"

echo "Cleaning up abandoned multipart uploads after 7 days..."
aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration '{
  "Rules": [{
    "ID": "abort-incomplete-multipart",
    "Status": "Enabled",
    "Filter": {},
    "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7}
  }]
}'

cat <<EOF

State bucket ready: s3://$BUCKET ($REGION)

Next steps (see docs/AWS_SETUP.md for the full walkthrough):
  1. cd infra/shared && terraform init && terraform apply
  2. Set the GitHub repo secret AWS_DEPLOY_ROLE_ARN to the 'deploy_role_arn'
     output from step 1, and create the 'production' GitHub Environment
     with a required reviewer.
  3. cd infra && terraform init
     terraform workspace new staging
     terraform apply -var-file=env/staging.tfvars
  4. Populate the Secrets Manager secrets for staging (see infra/README.md).
EOF
