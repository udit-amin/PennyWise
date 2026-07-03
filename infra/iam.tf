# Two roles:
#  - execution role: ECS agent pulls the image + injects secrets + writes logs
#  - task role:      the app's own AWS identity (DynamoDB access only)

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# ── Execution role ────────────────────────────────────────────────────
resource "aws_iam_role" "execution" {
  name               = "${local.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Allow the execution role to read exactly our secrets for injection.
data "aws_iam_policy_document" "execution_secrets" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = local.secret_arns
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "secrets-read"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

# ── Task role (the application's identity) ─────────────────────────────
resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "task_dynamodb" {
  statement {
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
      "dynamodb:DescribeTable",
    ]
    resources = local.dynamodb_table_arns
  }
}

resource "aws_iam_role_policy" "task_dynamodb" {
  name   = "dynamodb-access"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task_dynamodb.json
}
