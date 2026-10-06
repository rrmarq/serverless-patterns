"""
DynamoDB-backed session store for the Sticky Notes Board middleware.

One item per clientId holds everything needed to reconnect a user to their
MicroVM (or spin a new one up and restore their last session's state):

    clientId       (partition key)  e.g. "demo-user-ricardo"
    microvmId                       current MicroVM id (if any)
    endpoint                        current MicroVM ingress endpoint host
    authToken                       last minted X-aws-proxy-auth token
    authExpiresAt                   epoch seconds the token expires
    imageArn                        MicroVM image used to launch
    stateBucket                     S3 bucket where notes/files persist
    status                          "running" | "closed"
    updatedAt                       ISO-8601 timestamp of last write

The table is intentionally simple — the authoritative state (notes + files)
lives in S3 via the MicroVM lifecycle hooks. This table only tracks the
*session* so we can decide whether to reuse or relaunch a MicroVM.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import boto3

TABLE_NAME = os.environ.get("SESSIONS_TABLE", "microvm-sessions")

_dynamodb = boto3.resource("dynamodb")
_table = _dynamodb.Table(TABLE_NAME)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_session(client_id: str) -> dict | None:
    resp = _table.get_item(Key={"clientId": client_id})
    return resp.get("Item")


def put_session(
    client_id: str,
    microvm_id: str,
    endpoint: str,
    auth_token: str,
    auth_expires_at: int,
    image_arn: str,
    state_bucket: str,
    status: str = "running",
) -> dict:
    item = {
        "clientId": client_id,
        "microvmId": microvm_id,
        "endpoint": endpoint,
        "authToken": auth_token,
        "authExpiresAt": auth_expires_at,
        "imageArn": image_arn,
        "stateBucket": state_bucket,
        "status": status,
        "updatedAt": _now_iso(),
    }
    _table.put_item(Item=item)
    return item


def update_token(client_id: str, auth_token: str, auth_expires_at: int) -> None:
    _table.update_item(
        Key={"clientId": client_id},
        UpdateExpression="SET authToken = :t, authExpiresAt = :e, updatedAt = :u",
        ExpressionAttributeValues={
            ":t": auth_token,
            ":e": auth_expires_at,
            ":u": _now_iso(),
        },
    )


def mark_closed(client_id: str) -> None:
    _table.update_item(
        Key={"clientId": client_id},
        UpdateExpression="SET #s = :c, updatedAt = :u",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":c": "closed", ":u": _now_iso()},
    )


def token_is_valid(session: dict, skew_seconds: int = 120) -> bool:
    """A stored token is usable if it won't expire within `skew_seconds`."""
    expires_at = int(session.get("authExpiresAt", 0))
    return expires_at - skew_seconds > int(time.time())
