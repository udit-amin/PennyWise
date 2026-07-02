"""DynamoDB abstractions for PennyWise.

Thin wrappers over boto3 that mirror the shapes of the existing filesystem
persistence (Snapshot, ChatSession) so the rest of the codebase barely
changes.

For local development: run ``docker-compose up dynamodb`` and set
``DYNAMODB_ENDPOINT=http://localhost:8042``.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

TABLE_PREFIX = os.getenv("PENNYWISE_TABLE_PREFIX", "pennywise_")


def _client():
    endpoint = os.getenv("DYNAMODB_ENDPOINT")
    kwargs: dict[str, Any] = {"region_name": os.getenv("AWS_REGION", "ap-south-1")}
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    return boto3.resource("dynamodb", **kwargs)


def _table(name: str):
    return _client().Table(f"{TABLE_PREFIX}{name}")


# ── Users ─────────────────────────────────────────────────────────────


def create_user(email: str, name: str | None = None, picture: str | None = None) -> dict:
    """Create (or update on re-login) a user by email. Returns user dict."""
    table = _table("users")
    # Try to find existing user by email
    try:
        resp = table.query(
            IndexName="email-index",
            KeyConditionExpression="email = :e",
            ExpressionAttributeValues={":e": email},
        )
        items = resp.get("Items", [])
        if items:
            user = items[0]
            # Update name/picture on re-login
            table.update_item(
                Key={"user_id": user["user_id"]},
                UpdateExpression="SET #n = :n, picture = :p, updated_at = :u",
                ExpressionAttributeNames={"#n": "name"},
                ExpressionAttributeValues={
                    ":n": name, ":p": picture,
                    ":u": datetime.now(timezone.utc).isoformat(),
                },
            )
            user["name"] = name
            user["picture"] = picture
            return user
    except ClientError:
        pass

    user_id = str(uuid.uuid4())
    item = {
        "user_id": user_id,
        "email": email,
        "name": name,
        "picture": picture,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "settings": {},
    }
    table.put_item(Item=item)
    return item


def get_user(user_id: str) -> dict | None:
    table = _table("users")
    try:
        resp = table.get_item(Key={"user_id": user_id})
        return resp.get("Item")
    except ClientError:
        return None


# ── Sessions ──────────────────────────────────────────────────────────


def save_session(user_id: str, session_id: str, data: dict) -> None:
    table = _table("sessions")
    item = {
        "user_id": user_id,
        "session_id": session_id,
        "history": json.dumps(data.get("history", []), default=str),
        "model": data.get("model", ""),
        "started_at": data.get("started_at", ""),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "last_user_message": data.get("last_user_message", ""),
    }
    table.put_item(Item=item)


def load_session(user_id: str, session_id: str) -> dict | None:
    table = _table("sessions")
    try:
        resp = table.get_item(Key={"user_id": user_id, "session_id": session_id})
        item = resp.get("Item")
        if item and isinstance(item.get("history"), str):
            item["history"] = json.loads(item["history"])
        return item
    except ClientError:
        return None


def list_sessions(user_id: str, limit: int = 20) -> list[dict]:
    table = _table("sessions")
    try:
        resp = table.query(
            KeyConditionExpression="user_id = :u",
            ExpressionAttributeValues={":u": user_id},
            ScanIndexForward=False,  # newest first
            Limit=limit,
            ProjectionExpression="session_id, started_at, updated_at, last_user_message",
        )
        return resp.get("Items", [])
    except ClientError:
        return []


def delete_session(user_id: str, session_id: str) -> None:
    table = _table("sessions")
    table.delete_item(Key={"user_id": user_id, "session_id": session_id})


# ── Snapshots ─────────────────────────────────────────────────────────


def save_snapshot(user_id: str, snapshot_dict: dict) -> None:
    table = _table("snapshots")
    item = {
        "user_id": user_id,
        "sk": "LATEST",
        "fetched_at": snapshot_dict.get("fetched_at", ""),
        "holdings": json.dumps(snapshot_dict.get("holdings", []), default=str),
        "positions": json.dumps(snapshot_dict.get("positions", []), default=str),
    }
    table.put_item(Item=item)


def load_snapshot(user_id: str) -> dict | None:
    table = _table("snapshots")
    try:
        resp = table.get_item(Key={"user_id": user_id, "sk": "LATEST"})
        item = resp.get("Item")
        if item:
            if isinstance(item.get("holdings"), str):
                item["holdings"] = json.loads(item["holdings"])
            if isinstance(item.get("positions"), str):
                item["positions"] = json.loads(item["positions"])
        return item
    except ClientError:
        return None


# ── Jobs ──────────────────────────────────────────────────────────────


def create_job(user_id: str, job_type: str, params: dict | None = None) -> str:
    table = _table("jobs")
    job_id = str(uuid.uuid4())
    table.put_item(Item={
        "user_id": user_id,
        "job_id": job_id,
        "job_type": job_type,
        "params": json.dumps(params or {}, default=str),
        "status": "pending",
        "result": None,
        "error": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return job_id


def update_job(user_id: str, job_id: str, *, status: str, result: dict | None = None, error: str | None = None) -> None:
    table = _table("jobs")
    table.update_item(
        Key={"user_id": user_id, "job_id": job_id},
        UpdateExpression="SET #s = :s, #r = :r, #e = :e, updated_at = :u",
        ExpressionAttributeNames={"#s": "status", "#r": "result", "#e": "error"},
        ExpressionAttributeValues={
            ":s": status,
            ":r": json.dumps(result, default=str) if result else None,
            ":e": error,
            ":u": datetime.now(timezone.utc).isoformat(),
        },
    )


def get_job(user_id: str, job_id: str) -> dict | None:
    table = _table("jobs")
    try:
        resp = table.get_item(Key={"user_id": user_id, "job_id": job_id})
        item = resp.get("Item")
        if item and isinstance(item.get("result"), str):
            item["result"] = json.loads(item["result"])
        if item and isinstance(item.get("params"), str):
            item["params"] = json.loads(item["params"])
        return item
    except ClientError:
        return None


# ── Cache ─────────────────────────────────────────────────────────────


def cache_get(key: str) -> dict | None:
    """Retrieve cached data (technicals / fundamentals). Returns None if missing or expired."""
    table = _table("cache")
    try:
        resp = table.get_item(Key={"cache_key": key})
        item = resp.get("Item")
        if item and isinstance(item.get("data"), str):
            item["data"] = json.loads(item["data"])
        return item
    except ClientError:
        return None


def cache_put(key: str, data: dict, ttl_seconds: int = 3600) -> None:
    table = _table("cache")
    now = datetime.now(timezone.utc)
    table.put_item(Item={
        "cache_key": key,
        "data": json.dumps(data, default=str),
        "fetched_at": now.isoformat(),
        "ttl": int(now.timestamp()) + ttl_seconds,
    })


# ── Health ────────────────────────────────────────────────────────────


def ping() -> None:
    """Cheap reachability check for the readiness probe. Raises on failure."""
    _client().meta.client.describe_table(TableName=f"{TABLE_PREFIX}users")


# ── Table provisioning ────────────────────────────────────────────────


def _table_specs() -> list[dict]:
    """The canonical DynamoDB schema. Single source of truth shared by the
    local creator and the prod provisioning entrypoint; Terraform mirrors it."""
    return [
        {
            "TableName": f"{TABLE_PREFIX}users",
            "KeySchema": [{"AttributeName": "user_id", "KeyType": "HASH"}],
            "AttributeDefinitions": [
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "email", "AttributeType": "S"},
            ],
            "GlobalSecondaryIndexes": [{
                "IndexName": "email-index",
                "KeySchema": [{"AttributeName": "email", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }],
            "BillingMode": "PAY_PER_REQUEST",
        },
        {
            "TableName": f"{TABLE_PREFIX}sessions",
            "KeySchema": [
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "session_id", "KeyType": "RANGE"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "session_id", "AttributeType": "S"},
            ],
            "BillingMode": "PAY_PER_REQUEST",
        },
        {
            "TableName": f"{TABLE_PREFIX}snapshots",
            "KeySchema": [
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            "BillingMode": "PAY_PER_REQUEST",
        },
        {
            "TableName": f"{TABLE_PREFIX}jobs",
            "KeySchema": [
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "job_id", "KeyType": "RANGE"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "job_id", "AttributeType": "S"},
            ],
            "BillingMode": "PAY_PER_REQUEST",
        },
        {
            "TableName": f"{TABLE_PREFIX}cache",
            "KeySchema": [{"AttributeName": "cache_key", "KeyType": "HASH"}],
            "AttributeDefinitions": [
                {"AttributeName": "cache_key", "AttributeType": "S"},
            ],
            "BillingMode": "PAY_PER_REQUEST",
        },
    ]


def ensure_tables(*, enable_ttl: bool = True) -> None:
    """Create all DynamoDB tables and enable TTL on the cache table.

    Idempotent — skips existing tables. This is the canonical provisioning
    entrypoint, run as a one-shot deploy step
    (``python -m pennywise.api.db --create``) against real AWS, and on startup
    against dynamodb-local.
    """
    db = _client()
    client = db.meta.client
    existing = [t.name for t in db.tables.all()]

    created = []
    for spec in _table_specs():
        if spec["TableName"] not in existing:
            db.create_table(**spec)
            created.append(spec["TableName"])

    # Wait for newly-created tables to become ACTIVE before touching them.
    for name in created:
        client.get_waiter("table_exists").wait(TableName=name)

    if enable_ttl:
        cache_table = f"{TABLE_PREFIX}cache"
        try:
            desc = client.describe_time_to_live(TableName=cache_table)
            status = desc["TimeToLiveDescription"]["TimeToLiveStatus"]
            if status in ("DISABLED", "DISABLING"):
                client.update_time_to_live(
                    TableName=cache_table,
                    TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
                )
        except ClientError:
            # dynamodb-local / partial IAM: TTL is best-effort, not fatal.
            pass


def create_tables_if_not_exist() -> None:
    """Backwards-compatible alias used by local dev / tests."""
    ensure_tables()


if __name__ == "__main__":  # pragma: no cover - operational entrypoint
    import argparse

    parser = argparse.ArgumentParser(description="PennyWise DynamoDB admin")
    parser.add_argument("--create", action="store_true", help="Create tables + enable TTL")
    args = parser.parse_args()
    if args.create:
        ensure_tables()
        print(f"Tables ensured (prefix={TABLE_PREFIX!r}).")
    else:
        parser.print_help()
