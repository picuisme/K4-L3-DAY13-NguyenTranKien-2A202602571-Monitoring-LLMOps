from __future__ import annotations

import hashlib
import re
from typing import Any

# Thứ tự quan trọng: pattern dài/đặc thù chạy trước để không bị pattern ngắn hơn
# "cắt" mất một phần (ví dụ số thẻ 16 chữ số chứa một đoạn giống số điện thoại).
PII_PATTERNS: dict[str, str] = {
    "email": r"[\w\.+-]+@[\w-]+(?:\.[\w-]+)+",
    # Thẻ thanh toán 13–19 chữ số, cho phép nhóm cách nhau bằng space hoặc '-'.
    "credit_card": r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)",
    # CCCD (căn cước công dân) 12 chữ số liền nhau.
    "cccd": r"(?<!\d)\d{12}(?!\d)",
    # Điện thoại VN: 0xxxxxxxxx, +84 / 84 / (+84) + 9 chữ số, cho phép space . -
    "phone_vn": r"(?<!\d)(?:\(?\+?84\)?|0)(?:[ .-]?\d){9}(?!\d)",
    # Hộ chiếu VN: 1 chữ cái in hoa + 7–8 chữ số (ví dụ B1234567).
    "passport": r"\b[A-Z]\d{7,8}\b",
}

_COMPILED = {name: re.compile(pattern) for name, pattern in PII_PATTERNS.items()}


def scrub_text(text: str) -> str:
    safe = text
    for name, pattern in _COMPILED.items():
        safe = pattern.sub(f"[REDACTED_{name.upper()}]", safe)
    return safe


def scrub_value(value: Any) -> Any:
    """Scrub đệ quy mọi chuỗi trong dict/list/tuple; giữ nguyên số, bool, None."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {key: scrub_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(scrub_value(item) for item in value)
    return value


def summarize_text(text: str, max_len: int = 80) -> str:
    safe = scrub_text(text).strip().replace("\n", " ")
    return safe[:max_len] + ("..." if len(safe) > max_len else "")


def hash_user_id(user_id: str) -> str:
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:12]
