"""Dựng dashboard 6 panel từ data/logs.jsonl theo contract config/dashboard.yaml.

    python scripts/build_dashboard.py                       # 60 phút gần nhất -> PNG
    python scripts/build_dashboard.py --out submission/evidence/11-dashboard-overview.png
    python scripts/build_dashboard.py --highlight 10:05-10:12   # tô vùng sự cố (giờ local)
    python scripts/build_dashboard.py --watch               # tự refresh theo refresh_seconds

Tên panel, đơn vị, phép tổng hợp, time range và threshold đều đọc từ dashboard.yaml;
SLO latency lấy từ config/slo.yaml và được vẽ thêm thành đường nét đứt.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.cli import configure_utf8_stdio  # noqa: E402

LOCAL_TZ = timezone(timedelta(hours=7), "Asia/Ho_Chi_Minh")
OK_COLOR, BAD_COLOR = "#1a7f37", "#cf222e"
SERIES = ["#2f6fdf", "#e0801f", "#7a4fd6", "#12a39a"]
THRESHOLD_COLOR, SLO_COLOR, HIGHLIGHT_COLOR = "#cf222e", "#bf8700", "#ffd8b5"


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank, cùng công thức với app/metrics.py."""
    if not values:
        return float("nan")
    items = sorted(values)
    idx = max(0, min(len(items) - 1, round((p / 100) * len(items) + 0.5) - 1))
    return float(items[idx])


@dataclass
class Window:
    start: datetime
    end: datetime
    bucket_seconds: int = 60

    def floor(self, ts: datetime) -> datetime:
        epoch = int(ts.timestamp())
        return datetime.fromtimestamp(epoch - epoch % self.bucket_seconds, tz=timezone.utc)

    def minutes(self) -> list[datetime]:
        first = self.floor(self.start)
        count = int((self.end - first).total_seconds() // self.bucket_seconds) + 1
        return [first + timedelta(seconds=i * self.bucket_seconds) for i in range(count)]


def load_events(path: Path) -> list[dict]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            record["_ts"] = datetime.fromisoformat(record["ts"].replace("Z", "+00:00"))
            events.append(record)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return events


def bucket(events: list[dict], window: Window) -> dict[datetime, list[dict]]:
    grouped: dict[datetime, list[dict]] = defaultdict(list)
    for event in events:
        grouped[window.floor(event["_ts"])].append(event)
    return grouped


def check(value: float, threshold: dict) -> bool:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return True
    return value <= threshold["value"] if threshold["operator"] == "lte" else value >= threshold["value"]


def compute(events: list[dict], window: Window) -> dict:
    in_window = [e for e in events if window.start <= e["_ts"] <= window.end]
    received = [e for e in in_window if e.get("event") == "request_received"]
    sent = [e for e in in_window if e.get("event") == "response_sent"]
    failed = [e for e in in_window if e.get("event") == "request_failed"]
    tool_events = [e for e in in_window if e.get("tool_success") is not None]
    minutes_active = max(1, len({e["_ts"].replace(second=0, microsecond=0) for e in received}))
    latency = [e["latency_ms"] for e in sent if "latency_ms" in e]
    ttft = [e["ttft_ms"] for e in sent if "ttft_ms" in e]
    return {
        "in_window": in_window,
        "received": received,
        "sent": sent,
        "failed": failed,
        "p50": percentile(latency, 50),
        "p95": percentile(latency, 95),
        "p99": percentile(latency, 99),
        "ttft_p95": percentile(ttft, 95),
        "count": len(received),
        "rate_per_minute": len(received) / minutes_active,
        "error_rate_pct": (100 * len(failed) / len(received)) if received else 0.0,
        "count_by_value": dict(Counter(e.get("error_type") or "unknown" for e in failed)),
        "tool_success_rate_pct": (
            100 * sum(1 for e in tool_events if e["tool_success"]) / len(tool_events) if tool_events else float("nan")
        ),
        "total": sum(e.get("cost_usd", 0) for e in sent),
        "tokens_in": sum(e.get("tokens_in", 0) for e in sent),
        "tokens_out": sum(e.get("tokens_out", 0) for e in sent),
        "mean": (sum(e["quality_score"] for e in sent if "quality_score" in e) / len(sent)) if sent else float("nan"),
    }


def render(events: list[dict], dashboard: dict, slo: dict, window: Window, out: Path,
           highlight: tuple[datetime, datetime] | None, source: str) -> dict:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    stats = compute(events, window)
    panels = {p["id"]: p for p in dashboard["panels"]}
    minutes = window.minutes()
    by_min = bucket(stats["in_window"], window)
    bar_w = window.bucket_seconds / 86400 * 0.8
    bucket_label = f"{window.bucket_seconds // 60}-minute" if window.bucket_seconds % 60 == 0 else f"{window.bucket_seconds}-second"
    slo_latency = slo.get("primary_slo", {}).get("latency_threshold_ms")

    fig, axes = plt.subplots(3, 2, figsize=(17, 13.5))
    local_start = window.start.astimezone(LOCAL_TZ)
    local_end = window.end.astimezone(LOCAL_TZ)
    fig.suptitle(
        f"{dashboard['title']}\n"
        f"Time range: last {int((window.end - window.start).total_seconds() // 60)} min  "
        f"({local_start:%Y-%m-%d %H:%M} → {local_end:%H:%M} Asia/Ho_Chi_Minh)  |  "
        f"refresh {dashboard['refresh_seconds']}s  |  source: {source}  |  "
        f"{stats['count']} requests, {len(stats['failed'])} failed",
        fontsize=13, fontweight="bold",
    )

    def per_min(fn):
        return [fn(by_min.get(m, [])) for m in minutes]

    def finish(ax, panel, value_text, value, extra_threshold=None):
        threshold = panel["threshold"]
        ok = check(value, threshold)
        op = "≤" if threshold["operator"] == "lte" else "≥"
        ax.set_title(
            f"{panel['title']}  [{panel['unit']}]\n{value_text}\n"
            f"threshold: {threshold['aggregation']} {op} {threshold['value']}  →  {'OK' if ok else 'BREACH'}",
            fontsize=10.5, color=OK_COLOR if ok else BAD_COLOR, loc="left",
        )
        ax.set_xlim(window.start, window.end)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=LOCAL_TZ))
        span_min = (window.end - window.start).total_seconds() / 60
        step = 10 if span_min > 30 else (2 if span_min > 10 else 1)
        ax.xaxis.set_major_locator(mdates.MinuteLocator(byminute=range(0, 60, step), tz=LOCAL_TZ))
        ax.grid(alpha=0.25)
        ax.set_xlabel(f"time (Asia/Ho_Chi_Minh), {bucket_label} buckets", fontsize=8.5)
        if highlight:
            ax.axvspan(highlight[0], highlight[1], color=HIGHLIGHT_COLOR, alpha=0.6, zorder=0, label="incident window")
        ax.legend(fontsize=8, loc="upper left")

    def lat_series(p, field="latency_ms"):
        return per_min(lambda es: percentile([e[field] for e in es if e.get("event") == "response_sent" and field in e], p))

    # 1. Latency
    ax, panel = axes[0][0], panels["latency"]
    for (label, series), color in zip(
        [("P50", lat_series(50)), ("P95", lat_series(95)), ("P99", lat_series(99)), ("TTFT P95", lat_series(95, "ttft_ms"))],
        SERIES,
    ):
        ax.plot(minutes, series, marker="o", ms=3.5, color=color, label=label)
    ax.axhline(panel["threshold"]["value"], color=THRESHOLD_COLOR, ls="-", lw=1.4,
               label=f"threshold P95 ≤ {panel['threshold']['value']} ms")
    if slo_latency:
        ax.axhline(slo_latency, color=SLO_COLOR, ls="--", lw=1.4, label=f"SLO latency ≤ {slo_latency} ms")
    ax.set_ylabel("ms")
    ax.set_ylim(bottom=0)
    finish(ax, panel, f"P50={stats['p50']:.0f}  P95={stats['p95']:.0f}  P99={stats['p99']:.0f}  TTFT P95={stats['ttft_p95']:.0f} ms",
           stats["p95"])

    # 2. Traffic
    ax, panel = axes[0][1], panels["traffic"]
    counts = per_min(lambda es: sum(1 for e in es if e.get("event") == "request_received"))
    ax.bar(minutes, counts, width=bar_w, color=SERIES[0], label="requests / bucket")
    ax.axhline(panel["threshold"]["value"], color=THRESHOLD_COLOR, lw=1.4,
               label=f"threshold ≥ {panel['threshold']['value']} req/min")
    ax.set_ylabel("requests per minute" if window.bucket_seconds == 60 else f"requests per {bucket_label} bucket")
    finish(ax, panel, f"count={stats['count']}  rate={stats['rate_per_minute']:.1f} req/min (active minutes)",
           stats["rate_per_minute"])

    # 3. Errors + retrieval success
    ax, panel = axes[1][0], panels["errors"]

    def err_rate(es):
        rec = sum(1 for e in es if e.get("event") == "request_received")
        return 100 * sum(1 for e in es if e.get("event") == "request_failed") / rec if rec else float("nan")

    def tool_rate(es):
        tools = [e for e in es if e.get("tool_success") is not None]
        return 100 * sum(1 for e in tools if e["tool_success"]) / len(tools) if tools else float("nan")

    ax.plot(minutes, per_min(err_rate), marker="o", ms=3.5, color=BAD_COLOR, label="error rate %")
    ax.plot(minutes, per_min(tool_rate), marker="s", ms=3.5, color=SERIES[3], label="retrieval success %")
    ax.axhline(panel["threshold"]["value"], color=THRESHOLD_COLOR, lw=1.4,
               label=f"threshold error rate ≤ {panel['threshold']['value']}%")
    guard = slo.get("guardrails", {}).get("retrieval_success_rate_pct_min")
    if guard:
        ax.axhline(guard, color=SLO_COLOR, ls="--", lw=1.4, label=f"guardrail retrieval ≥ {guard}%")
    ax.set_ylim(-5, 105)
    ax.set_ylabel("percent")
    breakdown = ", ".join(f"{k}={v}" for k, v in stats["count_by_value"].items()) or "none"
    finish(ax, panel, f"error rate={stats['error_rate_pct']:.1f}%  retrieval success={stats['tool_success_rate_pct']:.1f}%  "
           f"breakdown: {breakdown}", stats["error_rate_pct"])

    # 4. Cost
    ax, panel = axes[1][1], panels["cost"]
    cost_min = per_min(lambda es: sum(e.get("cost_usd", 0) for e in es if e.get("event") == "response_sent"))
    cumulative, running = [], 0.0
    for value in cost_min:
        running += value
        cumulative.append(running)
    ax.bar(minutes, [v if v > 0 else float("nan") for v in cost_min], width=bar_w, color=SERIES[1], label="cost per bucket (USD)")
    ax.plot(minutes, cumulative, color=SERIES[2], lw=2, label="cumulative total (USD)")
    ax.axhline(panel["threshold"]["value"], color=THRESHOLD_COLOR, lw=1.4,
               label=f"threshold total ≤ {panel['threshold']['value']} USD")
    ax.set_yscale("log")
    ax.set_ylim(1e-4, panel["threshold"]["value"] * 3)
    ax.set_ylabel("USD (log scale)")
    finish(ax, panel, f"total={stats['total']:.4f} USD  avg/request={stats['total'] / max(1, len(stats['sent'])):.5f} USD",
           stats["total"])

    # 5. Tokens
    ax, panel = axes[2][0], panels["tokens"]
    tin = per_min(lambda es: sum(e.get("tokens_in", 0) for e in es if e.get("event") == "response_sent"))
    tout = per_min(lambda es: sum(e.get("tokens_out", 0) for e in es if e.get("event") == "response_sent"))
    offset = timedelta(seconds=window.bucket_seconds * 0.22)
    ax.bar([m - offset for m in minutes], [v or float("nan") for v in tin], width=bar_w / 2, color=SERIES[0], label="tokens_in / bucket")
    ax.bar([m + offset for m in minutes], [v or float("nan") for v in tout], width=bar_w / 2, color=SERIES[1], label="tokens_out / bucket")
    ax.axhline(panel["threshold"]["value"], color=THRESHOLD_COLOR, lw=1.4,
               label=f"threshold sum per field ≤ {panel['threshold']['value']:,}")
    ax.set_yscale("log")
    ax.set_ylim(10, panel["threshold"]["value"] * 3)
    ax.set_ylabel("tokens (log scale)")
    finish(ax, panel, f"sum tokens_in={stats['tokens_in']:,}  sum tokens_out={stats['tokens_out']:,}",
           max(stats["tokens_in"], stats["tokens_out"]))

    # 6. Quality
    ax, panel = axes[2][1], panels["quality"]
    q = per_min(lambda es: (lambda xs: sum(xs) / len(xs) if xs else float("nan"))(
        [e["quality_score"] for e in es if e.get("event") == "response_sent" and "quality_score" in e]))
    ax.plot(minutes, q, marker="o", ms=3.5, color=SERIES[2], label="mean quality_score")
    ax.axhline(panel["threshold"]["value"], color=THRESHOLD_COLOR, lw=1.4,
               label=f"threshold mean ≥ {panel['threshold']['value']}")
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("score 0–1")
    finish(ax, panel, f"mean={stats['mean']:.3f}  (n={len(stats['sent'])})", stats["mean"])

    fig.tight_layout(rect=(0, 0, 1, 0.95))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return stats


def parse_highlight(text: str | None, reference: datetime) -> tuple[datetime, datetime] | None:
    if not text:
        return None
    start_s, end_s = text.split("-")
    day = reference.astimezone(LOCAL_TZ)

    def at(hhmm: str) -> datetime:
        hour, minute = (int(x) for x in hhmm.split(":"))
        return day.replace(hour=hour, minute=minute, second=0, microsecond=0).astimezone(timezone.utc)

    return at(start_s), at(end_s)


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--logs", type=Path, default=REPO_ROOT / "data" / "logs.jsonl")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "dashboard.yaml")
    parser.add_argument("--slo", type=Path, default=REPO_ROOT / "config" / "slo.yaml")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "dashboard.png")
    parser.add_argument("--end", help="Mốc cuối cửa sổ (ISO, mặc định: event mới nhất)")
    parser.add_argument("--highlight", help="HH:MM-HH:MM giờ Asia/Ho_Chi_Minh, tô vùng sự cố")
    parser.add_argument("--watch", action="store_true", help="Tự refresh theo refresh_seconds")
    parser.add_argument("--minutes", type=int, help="Zoom cửa sổ (mặc định time_range_minutes=60)")
    parser.add_argument("--bucket-seconds", type=int, default=60, help="Độ rộng bucket (mặc định 60s)")
    args = parser.parse_args()

    dashboard = yaml.safe_load(args.config.read_text(encoding="utf-8"))["dashboard"]
    slo = yaml.safe_load(args.slo.read_text(encoding="utf-8")) if args.slo.exists() else {}
    while True:
        events = load_events(args.logs)
        if not events:
            print(f"Không có event trong {args.logs}")
            return 1
        end = datetime.fromisoformat(args.end) if args.end else max(e["_ts"] for e in events)
        if end.tzinfo is None:
            end = end.replace(tzinfo=LOCAL_TZ)
        end = end.astimezone(timezone.utc) + timedelta(seconds=30)
        span = args.minutes or dashboard["time_range_minutes"]
        window = Window(end - timedelta(minutes=span), end, args.bucket_seconds)
        stats = render(events, dashboard, slo, window, args.out, parse_highlight(args.highlight, end),
                       args.logs.relative_to(REPO_ROOT).as_posix() if args.logs.is_relative_to(REPO_ROOT) else args.logs.name)
        print(
            f"Dashboard -> {args.out}\n"
            f"  requests={stats['count']} failed={len(stats['failed'])} error_rate={stats['error_rate_pct']:.1f}% "
            f"retrieval_success={stats['tool_success_rate_pct']:.1f}%\n"
            f"  latency P50/P95/P99={stats['p50']:.0f}/{stats['p95']:.0f}/{stats['p99']:.0f} ms  TTFT P95={stats['ttft_p95']:.0f} ms\n"
            f"  cost total={stats['total']:.4f} USD  tokens in/out={stats['tokens_in']}/{stats['tokens_out']}  quality mean={stats['mean']:.3f}"
        )
        if not args.watch:
            return 0
        time.sleep(dashboard["refresh_seconds"])


if __name__ == "__main__":
    raise SystemExit(main())
