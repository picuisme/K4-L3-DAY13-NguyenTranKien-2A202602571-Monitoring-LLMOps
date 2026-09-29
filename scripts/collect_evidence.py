"""Thu evidence từ output THẬT của repo vào submission/evidence/ (.txt + .png).

    python scripts/collect_evidence.py checks      # 01 pytest, 02 log validator, 03 dashboard validator
    python scripts/collect_evidence.py logs        # 04 structured log, 05 PII redaction
    python scripts/collect_evidence.py dashboard   # 11 dashboard 60 phút
    python scripts/collect_evidence.py incident --start 16:45 --end 16:55 [--challenge-id ...]
                                                   # 12 metric (dashboard zoom), 13 log bất thường
    python scripts/collect_evidence.py scan        # quét secret/PII trong file sẽ commit

Ảnh Langfuse (06–10, 14) phải tự chụp trên project cá nhân; script này không tạo chúng.
PNG chỉ là bản render của chính output text đi kèm (không chỉnh sửa số liệu).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.cli import configure_utf8_stdio  # noqa: E402

EVIDENCE = REPO_ROOT / "submission" / "evidence"
LOGS = REPO_ROOT / "data" / "logs.jsonl"
LOCAL_TZ = timezone(timedelta(hours=7), "Asia/Ho_Chi_Minh")


def git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True,
                              text=True, check=False).stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def header(title: str) -> str:
    now = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    return f"# {title}\n# repo HEAD: {git_sha()} | captured: {now} | python {sys.version.split()[0]}\n\n"


def run(cmd: list[str]) -> str:
    completed = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", check=False)
    shown = "python " + " ".join(cmd[1:]) if cmd[0] == sys.executable else " ".join(cmd)
    shown = shown.replace(str(REPO_ROOT) + "/", "").replace(str(REPO_ROOT) + "\\", "")
    return f"$ {shown}\n{completed.stdout}{completed.stderr}"


def to_png(text: str, path: Path, width_chars: int = 150) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import logging
    import warnings

    import matplotlib.pyplot as plt

    warnings.filterwarnings("ignore", category=UserWarning)
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    lines: list[str] = []
    for raw in text.splitlines():
        while len(raw) > width_chars:
            lines.append(raw[:width_chars])
            raw = "    " + raw[width_chars:]
        lines.append(raw)
    height = max(2.0, 0.19 * len(lines) + 0.6)
    fig = plt.figure(figsize=(width_chars * 0.085, height))
    fig.patch.set_facecolor("#0d1117")
    # Consolas/Courier New (Windows) hiển thị đủ dấu tiếng Việt; DejaVu là fallback.
    fig.text(0.01, 1 - 0.3 / height, "\n".join(lines), family=["Consolas", "Courier New", "DejaVu Sans Mono"], fontsize=9.5, color="#e6edf3",
             va="top", ha="left", linespacing=1.35)
    fig.savefig(path, dpi=110, facecolor=fig.get_facecolor())
    plt.close(fig)


def save(name: str, text: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / f"{name}.txt").write_text(text, encoding="utf-8")
    to_png(text, EVIDENCE / f"{name}.png")
    print(f"  -> submission/evidence/{name}.txt + .png")


def load_logs() -> list[dict]:
    if not LOGS.exists():
        raise SystemExit("Chưa có data/logs.jsonl — chạy API và load test trước.")
    records = []
    for line in LOGS.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def cmd_checks(_: argparse.Namespace) -> None:
    py = sys.executable
    save("01-pytest", header("Pytest trên commit hiện tại") + run([py, "-m", "pytest", "-q", "-p", "no:cacheprovider"]))
    save("02-log-validator", header("Log validator") + run([py, "scripts/validate_logs.py"]))
    save("03-dashboard-validator", header("Dashboard validator") + run([py, "scripts/validate_dashboard.py"]))


def cmd_logs(_: argparse.Namespace) -> None:
    records = load_logs()
    sent = [r for r in records if r.get("event") == "response_sent"]
    if not sent:
        raise SystemExit("Chưa có response_sent trong log")
    sample = sent[-1]
    cid = sample["correlation_id"]
    same_request = [r for r in records if r.get("correlation_id") == cid]
    text = header("Structured log (data/logs.jsonl) — một request, nối bằng correlation_id")
    text += f"Request correlation_id={cid}  ({len(same_request)} log lines)\n\n"
    for record in same_request:
        text += json.dumps(record, ensure_ascii=False, indent=2) + "\n"
    save("04-structured-log", text)

    queries = [json.loads(l) for l in (REPO_ROOT / "data" / "sample_queries.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    detectors = {
        "email": re.compile(r"[\w.-]+@[\w.-]+\.\w+"),
        "phone_vn": re.compile(r"(?<!\d)(?:\+84|0)(?:[ .-]?\d){9}(?!\d)"),
        "cccd": re.compile(r"\b\d{12}\b"),
        "credit_card": re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"),
    }
    pii_queries = [q for q in queries if any(d.search(q["message"]) for d in detectors.values())]
    received = [r for r in records if r.get("event") == "request_received"]
    text = header("PII redaction — input giả (data/sample_queries.jsonl) vs log thực tế đã ghi")
    for query in pii_queries:
        kinds = [k for k, d in detectors.items() if d.search(query["message"])]
        match = next((r for r in reversed(received) if r.get("session_id") == query["session_id"]), None)
        text += f"[{', '.join(kinds)}] session_id={query['session_id']}\n"
        text += f"  INPUT (test data) : {query['message']}\n"
        if match:
            text += f"  LOG  request_received correlation_id={match['correlation_id']}\n"
            text += f"       payload.message_preview = {match['payload']['message_preview']}\n\n"
        else:
            text += "  LOG  (chưa có request cho session này trong log)\n\n"
    raw = LOGS.read_text(encoding="utf-8")
    leaks = {k: len(d.findall(raw)) for k, d in detectors.items()}
    text += f"Quét toàn bộ data/logs.jsonl ({len(records)} dòng) bằng regex của validator: {leaks}\n"
    save("05-pii-redaction", text)


def cmd_dashboard(args: argparse.Namespace) -> None:
    print(run([sys.executable, "scripts/build_dashboard.py", "--out", str(EVIDENCE / "11-dashboard-overview.png")]))


def cmd_incident(args: argparse.Namespace) -> None:
    records = load_logs()
    day = datetime.now(LOCAL_TZ) if not args.date else datetime.fromisoformat(args.date).replace(tzinfo=LOCAL_TZ)

    def at(hhmm: str) -> datetime:
        hour, minute = (int(x) for x in hhmm.split(":"))
        return day.replace(hour=hour, minute=minute, second=0, microsecond=0)

    start, end = at(args.start), at(args.end)
    zoom = int((end - start).total_seconds() // 60) + 10
    end_arg = (end + timedelta(minutes=5)).isoformat()
    print(run([sys.executable, "scripts/build_dashboard.py", "--out", str(EVIDENCE / "12-incident-metric.png"),
               "--minutes", str(zoom), "--bucket-seconds", "30", "--end", end_arg,
               "--highlight", f"{args.start}-{args.end}"]))

    def ts(record: dict) -> datetime:
        return datetime.fromisoformat(record["ts"].replace("Z", "+00:00"))

    window = [r for r in records if start <= ts(r) <= end]
    failed = [r for r in window if r.get("event") == "request_failed"]
    slow = sorted((r for r in window if r.get("event") == "response_sent"), key=lambda r: -r.get("latency_ms", 0))
    costly = sorted((r for r in window if r.get("event") == "response_sent"), key=lambda r: -r.get("cost_usd", 0))
    text = header(f"Incident log — challenge {args.challenge_id or '(điền challenge_id)'}")
    text += f"Window: {start:%Y-%m-%d %H:%M} → {end:%H:%M} Asia/Ho_Chi_Minh | {len(window)} log lines trong khoảng\n"
    text += (f"request_received={sum(1 for r in window if r.get('event') == 'request_received')}  "
             f"response_sent={len(slow)}  request_failed={len(failed)}\n\n")
    fields = ["ts", "event", "level", "correlation_id", "trace_id", "feature", "latency_ms", "retrieval_ms", "prompt_ms", "llm_ms",
              "ttft_ms", "tokens_out", "cost_usd", "error_type", "tool_name", "tool_success", "prompt_version"]

    def compact(record: dict) -> str:
        return json.dumps({k: record[k] for k in fields if k in record}, ensure_ascii=False)

    if failed:
        text += "## request_failed (mới nhất trước)\n" + "\n".join(compact(r) for r in failed[-8:][::-1]) + "\n\n"
        detail = failed[-1]
        text += "## Full log line của request được chọn\n" + json.dumps(detail, ensure_ascii=False, indent=2) + "\n\n"
    text += "## response_sent chậm nhất\n" + "\n".join(compact(r) for r in slow[:6]) + "\n\n"
    text += "## response_sent đắt nhất\n" + "\n".join(compact(r) for r in costly[:4]) + "\n"
    if not failed and slow:
        text += "\n## Full log line của request được chọn (chậm nhất)\n" + json.dumps(slow[0], ensure_ascii=False, indent=2) + "\n"
    save("13-incident-log", text)


SECRET_PATTERNS = {
    "langfuse_secret": re.compile(r"sk-lf-[0-9a-f-]{8,}"),
    "langfuse_public": re.compile(r"pk-lf-[0-9a-f-]{8,}"),
    "generic_key": re.compile(r"(?i)(api[_-]?key|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}"),
}


def cmd_scan(_: argparse.Namespace) -> None:
    listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=REPO_ROOT,
                            capture_output=True, text=True, check=False).stdout.split()
    problems = []
    for rel in listed:
        if rel in {".env", "config/challenge.json"} or rel.startswith(".venv/"):
            problems.append(f"{rel}: KHÔNG được commit")
            continue
        path = REPO_ROOT / rel
        if path.suffix.lower() in {".png", ".jpg", ".jpeg"} or not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(content):
                problems.append(f"{rel}: nghi có {name}")
    print("\n".join(problems) if problems else f"OK: {len(listed)} file sẽ commit, không thấy secret/.env/challenge.json")
    if problems:
        raise SystemExit(1)


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("checks")
    sub.add_parser("logs")
    sub.add_parser("dashboard")
    sub.add_parser("scan")
    incident = sub.add_parser("incident")
    incident.add_argument("--start", required=True, help="HH:MM giờ Asia/Ho_Chi_Minh")
    incident.add_argument("--end", required=True, help="HH:MM giờ Asia/Ho_Chi_Minh")
    incident.add_argument("--date", help="YYYY-MM-DD (mặc định hôm nay)")
    incident.add_argument("--challenge-id")
    args = parser.parse_args()
    {"checks": cmd_checks, "logs": cmd_logs, "dashboard": cmd_dashboard, "incident": cmd_incident,
     "scan": cmd_scan}[args.command](args)


if __name__ == "__main__":
    main()
