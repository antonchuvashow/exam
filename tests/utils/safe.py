import os
import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer
from django.conf import settings
import secrets as _secrets
from typing import List
import json

# Helpers 
def _safe_json_loads(body_bytes):
    try:
        return json.loads(body_bytes.decode() if isinstance(body_bytes, (bytes, bytearray)) else body_bytes)
    except (ValueError, json.JSONDecodeError):
        return None


def _token_equal(a, b):
    # constant-time comparison for tokens
    try:
        return _secrets.compare_digest(str(a), str(b))
    except Exception:
        return False


def _to_int_list(values: List[str], limit: int = 100):
    """
    Convert list of strings to ints with basic validation and size limit.
    Returns list[int] or raises ValueError.
    """
    if not isinstance(values, (list, tuple)):
        raise ValueError("expected list")
    if len(values) > limit:
        raise ValueError("list too long")
    out = []
    for v in values:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            raise ValueError("invalid id")
    return out
