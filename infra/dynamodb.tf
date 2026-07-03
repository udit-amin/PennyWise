# DynamoDB tables — mirrors the schema in pennywise/api/db.py:_table_specs().
# PAY_PER_REQUEST, encryption at rest, point-in-time recovery.

resource "aws_dynamodb_table" "users" {
  name         = "${local.table_prefix}users"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"

  attribute {
    name = "user_id"
    type = "S"
  }
  attribute {
    name = "email"
    type = "S"
  }

  global_secondary_index {
    name            = "email-index"
    hash_key        = "email"
    projection_type = "ALL"
  }

  server_side_encryption {
    enabled = true
  }
  point_in_time_recovery {
    enabled = true
  }
}

resource "aws_dynamodb_table" "sessions" {
  name         = "${local.table_prefix}sessions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"
  range_key    = "session_id"

  attribute {
    name = "user_id"
    type = "S"
  }
  attribute {
    name = "session_id"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }
  point_in_time_recovery {
    enabled = true
  }
}

resource "aws_dynamodb_table" "snapshots" {
  name         = "${local.table_prefix}snapshots"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"
  range_key    = "sk"

  attribute {
    name = "user_id"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }
  point_in_time_recovery {
    enabled = true
  }
}

resource "aws_dynamodb_table" "jobs" {
  name         = "${local.table_prefix}jobs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"
  range_key    = "job_id"

  attribute {
    name = "user_id"
    type = "S"
  }
  attribute {
    name = "job_id"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }
  point_in_time_recovery {
    enabled = true
  }
}

resource "aws_dynamodb_table" "cache" {
  name         = "${local.table_prefix}cache"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "cache_key"

  attribute {
    name = "cache_key"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  server_side_encryption {
    enabled = true
  }
}

locals {
  dynamodb_table_arns = [
    aws_dynamodb_table.users.arn,
    "${aws_dynamodb_table.users.arn}/index/*",
    aws_dynamodb_table.sessions.arn,
    aws_dynamodb_table.snapshots.arn,
    aws_dynamodb_table.jobs.arn,
    aws_dynamodb_table.cache.arn,
  ]
}
