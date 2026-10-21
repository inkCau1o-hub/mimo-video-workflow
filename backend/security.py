"""Redact credentials before returning or persisting diagnostic data."""

import os
import re

import config


def redact(value):
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if str(key).lower() in {
            "api_key", "api-key", "authorization", "token", "password"
        } else redact(item)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value
    secrets = [getattr(config, "API_KEY", "")]
    secrets.extend(item for name, item in os.environ.items()
                   if any(word in name.upper() for word in ("API_KEY", "TOKEN", "SECRET")))
    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret and len(secret) >= 6:
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"(?i)\bBearer\s+[^\s'\"},]+", "Bearer [REDACTED]", value)
    return re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", value)
