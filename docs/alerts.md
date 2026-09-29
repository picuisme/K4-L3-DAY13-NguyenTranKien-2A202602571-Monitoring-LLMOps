# Alert và Runbook — K4-L3A Day 13

Mỗi alert đo **triệu chứng người dùng/nghiệp vụ** (chậm, lỗi, tốn tiền), không đo tên implementation nội bộ. Rule nằm trong [`config/alert_rules.yaml`](../config/alert_rules.yaml), SLO và error budget trong [`config/slo.yaml`](../config/slo.yaml). Nguồn dữ liệu chung là `data/logs.jsonl`. Luồng điều tra luôn là **Metrics → Logs → Traces**:

- Dashboard: `python scripts/build_dashboard.py --minutes 15 --bucket-seconds 30`
- Logs: lọc `data/logs.jsonl` theo khoảng thời gian, lấy `correlation_id` và `trace_id`
- Traces: Langfuse project `day13-k4-l3a-2A202602571` → Traces → filter metadata `correlation_id` (hoặc mở trực tiếp `trace_id`)

Các lệnh lọc log mẫu (PowerShell):

```powershell
# 10 request chậm nhất trong log
Get-Content data/logs.jsonl | ConvertFrom-Json | Where-Object event -eq "response_sent" |
  Sort-Object latency_ms -Descending | Select-Object -First 10 ts, correlation_id, trace_id, latency_ms, retrieval_ms, llm_ms, feature
# Các request lỗi
Get-Content data/logs.jsonl | ConvertFrom-Json | Where-Object event -eq "request_failed" |
  Select-Object ts, correlation_id, error_type, tool_name, tool_success
```

## Alert 1 — ChatLatencyP95High

- **Tên:** `ChatLatencyP95High`
- **Severity:** P2-high
- **Duration:** 5m
- **Kênh thông báo:** Slack `#day13-k4l3a-oncall`
- **SLI/SLO liên quan:** `fast_successful_requests` — 99.5% request thành công và `latency_ms ≤ 2000` trong 28 ngày.
- **Điều kiện và thời gian duy trì:** P95 của `response_sent.latency_ms` trong cửa sổ trượt 5 phút > 2000 ms, duy trì liên tục 5 phút.
- **Ảnh hưởng tới người dùng:** người dùng chờ > 2 giây cho một câu trả lời; mỗi request chậm tiêu error budget của SLO.
- **Ba bước kiểm tra đầu tiên:**
  1. Dashboard panel *Latency*: P50 có tăng cùng P95 không (toàn hệ thống chậm) hay chỉ tail; TTFT P95 có đổi không (TTFT không đổi → chậm nằm trước LLM).
  2. Logs: lấy các `response_sent` chậm nhất, so `retrieval_ms` với `llm_ms`, xem có tập trung vào một `feature`/`prompt_version` không.
  3. Traces: mở `trace_id` của request chậm, so duration span `retrieval` với `llm-generation` trong waterfall.
- **Mitigation tạm thời:** nếu retrieval chậm: bật cache/giảm top-k, đặt timeout retrieval + fallback trả lời không context; nếu LLM chậm: chuyển model nhỏ hơn hoặc giảm `max_tokens`; nếu do prompt mới: rollback label `production` về version trước (`python scripts/prompt_versions.py rollback`).
- **Owner:** llmops-oncall (Nguyen Tran Kien)

## Alert 2 — ChatErrorRateHigh

- **Tên:** `ChatErrorRateHigh`
- **Severity:** P1-critical
- **Duration:** 5m
- **Kênh thông báo:** Slack `#day13-k4l3a-oncall` (page on-call)
- **SLI/SLO liên quan:** `fast_successful_requests` (request lỗi là bad event) và guardrail `error_rate_pct_max = 2`.
- **Điều kiện và thời gian duy trì:** `count(request_failed) / count(request_received) × 100 > 2%` trong cửa sổ 5 phút, với ít nhất 10 request (tránh báo động giả khi traffic thấp), duy trì 5 phút.
- **Ảnh hưởng tới người dùng:** người dùng nhận HTTP 500, không có câu trả lời. Với target 99.5%, error rate 2% đốt budget nhanh gấp 4 lần mức cho phép.
- **Ba bước kiểm tra đầu tiên:**
  1. Dashboard panel *Errors*: error rate và `error_type` breakdown; nếu *retrieval success* giảm cùng lúc thì lỗi nằm ở bước retrieval.
  2. Logs: lọc `request_failed`, xem `error_type`, `tool_name`, `tool_success`, `payload.detail`; lấy `correlation_id`.
  3. Traces: filter Langfuse theo `correlation_id`, tìm observation có level `ERROR` và `status_message`.
- **Mitigation tạm thời:** nếu vector store lỗi: bật fallback trả lời không context hoặc trả thông báo "tạm thời không tra cứu được" thay vì 500; retry có giới hạn + circuit breaker; nếu lỗi do deploy/prompt mới: rollback.
- **Owner:** llmops-oncall (Nguyen Tran Kien)

## Alert 3 — CostPerRequestSpike

- **Tên:** `CostPerRequestSpike`
- **Severity:** P3-medium
- **Duration:** 15m
- **Kênh thông báo:** Slack `#day13-k4l3a-finops`
- **SLI/SLO liên quan:** guardrail `avg_cost_per_request_usd_max = 0.004` (≈ 2× baseline 0.002 USD) và `daily_cost_usd_max = 2.5`.
- **Điều kiện và thời gian duy trì:** `sum(cost_usd) / count(response_sent)` trong cửa sổ 15 phút > 0.004 USD, duy trì 15 phút. Dùng cost **trên mỗi request** để không báo nhầm khi traffic tăng hợp lệ.
- **Ảnh hưởng tới người dùng:** chưa ảnh hưởng trực tiếp nhưng vượt ngân sách; output dài bất thường thường kèm câu trả lời dài dòng, TTFT/latency tăng.
- **Ba bước kiểm tra đầu tiên:**
  1. Dashboard panel *Cost* và *Tokens*: cost tăng do `tokens_in` (prompt/context phình to) hay `tokens_out` (output dài); traffic có đổi không.
  2. Logs: so `tokens_out`, `cost_usd`, `prompt_version`, `model` của request đắt nhất với baseline.
  3. Traces: mở generation của request đắt, kiểm tra `usage_details`, `cost_details`, prompt version được link.
- **Mitigation tạm thời:** giới hạn `max_tokens` output, rollback prompt nếu version mới làm output dài, chuyển feature ít quan trọng sang model rẻ hơn, cache câu hỏi lặp.
- **Owner:** llmops-finops (Nguyen Tran Kien)
