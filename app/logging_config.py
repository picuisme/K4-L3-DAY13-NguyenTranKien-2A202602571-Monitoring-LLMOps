from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import structlog
from structlog.contextvars import merge_contextvars

from .pii import scrub_value

LOG_PATH = Path(os.getenv("LOG_PATH", "data/logs.jsonl"))


class JsonlFileProcessor:
    def __call__(self, logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        rendered = structlog.processors.JSONRenderer()(logger, method_name, event_dict)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(rendered + "\n")
        return event_dict



# ID do hệ thống sinh (hex), không bao giờ chứa dữ liệu người dùng. Không scrub để tránh
# false positive: trace_id hex có thể mở đầu bằng >=13 chữ số và bị nhận nhầm là số thẻ,
# làm đứt liên kết log -> trace.
SYSTEM_ID_FIELDS = frozenset({"trace_id", "user_id_hash"})


def scrub_event(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Processor chạy TRƯỚC file writer và JSON renderer.

    Scrub đệ quy mọi giá trị chuỗi (event, payload lồng nhau, error detail, ...),
    không chỉ riêng payload, để PII không lọt qua một field mới nào đó.
    """
    return {
        key: value if key in SYSTEM_ID_FIELDS else scrub_value(value)
        for key, value in event_dict.items()
    }



def configure_logging() -> None:
    logging.basicConfig(format="%(message)s", level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")))
    structlog.configure(
        processors=[
            merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            # PII scrubber đứng sau format_exc_info (để scrub cả traceback) và
            # trước JsonlFileProcessor/JSONRenderer: dữ liệu được làm sạch trước
            # khi serialize hoặc ghi xuống file.
            scrub_event,
            JsonlFileProcessor(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )



def get_logger() -> structlog.typing.FilteringBoundLogger:
    return structlog.get_logger()
