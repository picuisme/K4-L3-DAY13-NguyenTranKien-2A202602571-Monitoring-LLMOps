"""Quản lý prompt `day13-chat` trên project Langfuse cá nhân (docs/PROMPT_VERSIONING.md).

Các bước (mỗi bước là một lệnh riêng để kịp chụp ảnh evidence giữa các bước):

    python scripts/prompt_versions.py create     # v1 [baseline, production] + v2 [candidate]
    python scripts/prompt_versions.py status     # in version/label hiện tại
    python scripts/prompt_versions.py run --label baseline
    python scripts/prompt_versions.py run --label candidate
    python scripts/prompt_versions.py promote    # production -> v2
    python scripts/prompt_versions.py run --label production
    python scripts/prompt_versions.py rollback   # production -> v1
    python scripts/prompt_versions.py run --label production

Lệnh `run` gọi đúng LabAgent của app với cùng một input cố định, in ra correlation_id,
trace_id và prompt version thật do Langfuse trả về, đồng thời ghi dòng tóm tắt vào
submission/evidence/10-prompt-rollout.txt. Script đọc key từ `.env`, không in key ra.
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(REPO_ROOT / ".env")

from app.cli import configure_utf8_stdio  # noqa: E402

PROMPT_V1 = "Feature={{feature}}\nDocs={{docs}}\nQuestion={{message}}"
# v2: thay đổi nhỏ về format/độ dài câu trả lời, vẫn giữ đủ ba biến.
PROMPT_V2 = (
    "Feature={{feature}}\nDocs={{docs}}\nQuestion={{message}}\n"
    "Answer in at most 3 short bullet points and cite the doc you used."
)
FIXED_INPUT = {
    "user_id": "prompt-rollout-tester",
    "session_id": "prompt-rollout",
    "feature": "qa",
    "message": "Explain why metrics traces and logs work together",
}
ROLLOUT_LOG = REPO_ROOT / "submission" / "evidence" / "10-prompt-rollout.txt"


def _client():
    from langfuse import get_client

    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        raise SystemExit("Thiếu LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY trong .env")
    return get_client()


def _name() -> str:
    return os.getenv("LANGFUSE_PROMPT_NAME", "day13-chat")


def _fetch(client, version: int):
    try:
        return client.get_prompt(
            _name(), version=version, type="text", cache_ttl_seconds=0, max_retries=0
        )
    except Exception:
        return None


def cmd_status(client) -> None:
    print(f"Prompt: {_name()}  (host: {os.getenv('LANGFUSE_BASE_URL')})")
    for version in (1, 2, 3, 4):
        prompt = _fetch(client, version)
        if prompt is None:
            break
        last_line = prompt.prompt.splitlines()[-1][:70]
        print(f"  v{prompt.version}: labels={sorted(prompt.labels)}  last line: {last_line!r}")


def cmd_create(client) -> None:
    if _fetch(client, 1) is None:
        client.create_prompt(
            name=_name(), prompt=PROMPT_V1, labels=["baseline", "production"], type="text",
            commit_message="v1 baseline template",
        )
        print("Đã tạo v1 [baseline, production]")
    else:
        print("v1 đã tồn tại, bỏ qua")
    if _fetch(client, 2) is None:
        client.create_prompt(
            name=_name(), prompt=PROMPT_V2, labels=["candidate"], type="text",
            commit_message="v2 candidate: bullet-point answers",
        )
        print("Đã tạo v2 [candidate]")
    else:
        print("v2 đã tồn tại, bỏ qua")
    cmd_status(client)


def cmd_promote(client) -> None:
    # Label là duy nhất giữa các version: gắn production cho v2 sẽ gỡ khỏi v1.
    client.update_prompt(name=_name(), version=2, new_labels=["candidate", "production"])
    print("PROMOTE: production -> v2")
    cmd_status(client)


def cmd_rollback(client) -> None:
    client.update_prompt(name=_name(), version=1, new_labels=["baseline", "production"])
    print("ROLLBACK: production -> v1")
    cmd_status(client)


def cmd_run(client, label: str) -> None:
    os.environ["LANGFUSE_PROMPT_LABEL"] = label
    from app.agent import LabAgent
    from app.tracing import tracing_enabled

    if not tracing_enabled():
        raise SystemExit("Tracing chưa bật; kiểm tra .env")
    correlation_id = f"req-{uuid.uuid4().hex[:8]}"
    result = LabAgent().run(correlation_id=correlation_id, **FIXED_INPUT)
    client.flush()
    prompt = client.get_prompt(_name(), label=label, type="text", cache_ttl_seconds=0)
    line = (
        f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} label={label} "
        f"prompt_version={result.prompt_version} (label now on v{prompt.version}) "
        f"correlation_id={correlation_id} trace_id={result.trace_id} "
        f"tokens_in={result.tokens_in} cost_usd={result.cost_usd}"
    )
    print(line)
    ROLLOUT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with ROLLOUT_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["create", "status", "promote", "rollback", "run"])
    parser.add_argument("--label", default="production")
    args = parser.parse_args()
    client = _client()
    if args.command == "run":
        cmd_run(client, args.label)
    else:
        {"create": cmd_create, "status": cmd_status, "promote": cmd_promote, "rollback": cmd_rollback}[
            args.command
        ](client)
    client.flush()


if __name__ == "__main__":
    main()
