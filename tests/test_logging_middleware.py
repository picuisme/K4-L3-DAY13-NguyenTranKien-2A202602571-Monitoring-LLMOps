from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest
from structlog.contextvars import bind_contextvars

from app import logging_config
from app.logging_config import scrub_event
from app.main import app
from app.middleware import resolve_correlation_id

CID_RE = re.compile(r"^req-[0-9a-f]{8}$")
PII_INPUTS = {
    "email": "student@vinuni.edu.vn",
    "phone": "0987654321",
    "cccd": "001203004567",
    "card": "4111 1111 1111 1111",
}


def _post_chat(payload: dict, headers: dict | None = None) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/chat", json=payload, headers=headers or {})

    return asyncio.run(send())


@pytest.fixture()
def log_path(monkeypatch, tmp_path: Path) -> Path:
    path = tmp_path / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", path)
    return path


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_generated_correlation_id_format() -> None:
    assert CID_RE.match(resolve_correlation_id(None))
    assert CID_RE.match(resolve_correlation_id("   "))
    # Header không an toàn (khoảng trắng / quá dài) bị thay bằng ID mới.
    assert CID_RE.match(resolve_correlation_id("bad id\nINJECT"))
    assert CID_RE.match(resolve_correlation_id("x" * 200))
    assert resolve_correlation_id("req-deadbeef") == "req-deadbeef"


def test_response_headers_and_logs_share_correlation_id(log_path: Path) -> None:
    response = _post_chat(
        {"user_id": "u01", "session_id": "s01", "feature": "qa", "message": "Explain monitoring"}
    )
    assert response.status_code == 200
    cid = response.headers["x-request-id"]
    assert CID_RE.match(cid)
    assert float(response.headers["x-response-time-ms"]) > 0
    assert response.json()["correlation_id"] == cid

    api_events = [e for e in _events(log_path) if e.get("service") == "api"]
    assert {e["event"] for e in api_events} == {"request_received", "response_sent"}
    for event in api_events:
        assert event["correlation_id"] == cid
        for field in ("user_id_hash", "session_id", "feature", "model", "env", "ts", "level"):
            assert event.get(field), field
        assert event["user_id_hash"] != "u01"


def test_incoming_request_id_is_propagated(log_path: Path) -> None:
    response = _post_chat(
        {"user_id": "u02", "session_id": "s02", "feature": "qa", "message": "hi"},
        headers={"x-request-id": "req-0badc0de"},
    )
    assert response.headers["x-request-id"] == "req-0badc0de"
    assert all(e["correlation_id"] == "req-0badc0de" for e in _events(log_path) if e.get("service") == "api")


def test_context_does_not_leak_between_requests(log_path: Path) -> None:
    bind_contextvars(user_id_hash="stale", correlation_id="req-stale000")
    first = _post_chat({"user_id": "u03", "session_id": "s03", "feature": "qa", "message": "a"})
    second = _post_chat({"user_id": "u04", "session_id": "s04", "feature": "summary", "message": "b"})
    ids = {first.headers["x-request-id"], second.headers["x-request-id"]}
    assert len(ids) == 2
    for event in _events(log_path):
        if event.get("service") != "api":
            continue
        assert event["correlation_id"] in ids
        assert event["user_id_hash"] != "stale"
        expected_session = "s03" if event["correlation_id"] == first.headers["x-request-id"] else "s04"
        assert event["session_id"] == expected_session


def test_pii_is_scrubbed_before_writing_file(log_path: Path) -> None:
    tags = {
        "email": "REDACTED_EMAIL",
        "phone": "REDACTED_PHONE_VN",
        "cccd": "REDACTED_CCCD",
        "card": "REDACTED_CREDIT_CARD",
    }
    for kind, value in PII_INPUTS.items():
        response = _post_chat(
            {"user_id": "u05", "session_id": "s05", "feature": "qa", "message": f"my {kind} is {value}"}
        )
        assert response.status_code == 200
    raw = log_path.read_text(encoding="utf-8")
    for value in PII_INPUTS.values():
        assert value not in raw
    for tag in tags.values():
        assert tag in raw


def test_scrub_event_processor_handles_nested_fields() -> None:
    out = scrub_event(None, "info", {"event": "x 0901234567", "payload": {"detail": ["a@b.co"]}, "n": 1})
    assert out == {"event": "x [REDACTED_PHONE_VN]", "payload": {"detail": ["[REDACTED_EMAIL]"]}, "n": 1}


def test_failed_request_is_logged_with_error_and_retrieval_flag(log_path: Path) -> None:
    from app.incidents import disable, enable

    enable("tool_fail")
    try:
        response = _post_chat({"user_id": "u06", "session_id": "s06", "feature": "qa", "message": "x"})
    finally:
        disable("tool_fail")
    assert response.status_code == 500
    cid = response.headers["x-request-id"]
    failed = [e for e in _events(log_path) if e["event"] == "request_failed"]
    assert failed and failed[0]["correlation_id"] == cid
    assert failed[0]["error_type"] == "RuntimeError"
    assert failed[0]["tool_success"] is False


def test_system_ids_are_not_mangled_by_pii_scrubber() -> None:
    # trace_id hex bắt đầu bằng 13+ chữ số từng bị nhận nhầm là số thẻ.
    trace_id = "3017410596284c1dfd0c24bd4fed12d5"
    out = scrub_event(None, "info", {"event": "response_sent", "trace_id": trace_id, "user_id_hash": "123456789012"})
    assert out["trace_id"] == trace_id
    assert out["user_id_hash"] == "123456789012"
