"""JSON structured logging for the API.

Production (ECS → CloudWatch) is far easier to query when logs are single-line
JSON. Locally we keep it readable. Toggle with ``PENNYWISE_LOG_FORMAT=json|text``
(defaults to ``json`` in staging/prod, ``text`` in dev).
"""
from __future__ import annotations

import json
import logging
import os
import sys


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("request_id", "user_id", "job_id"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Idempotently configure the root logger. Safe to call on every startup."""
    from pennywise import config

    default_fmt = "json" if config.load().is_prod_like else "text"
    fmt = os.getenv("PENNYWISE_LOG_FORMAT", default_fmt).lower()
    level = os.getenv("PENNYWISE_LOG_LEVEL", "INFO").upper()

    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s — %(message)s")
        )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
