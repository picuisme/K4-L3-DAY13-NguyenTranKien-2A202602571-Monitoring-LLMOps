# Báo cáo cá nhân — K4-L3A Day 13 Monitoring & LLMOps

## 1. Thông tin học viên

- **Họ và tên:** Nguyễn Trần Kiên
- **MSSV:** 2A202602571
- **Lớp:** K4-L3A
- **Repository URL:** https://github.com/picuisme/K4-L3-DAY13-NguyenTranKien-2A202602571-Monitoring-LLMOps
- **Commit SHA cuối:** xem mục cuối — SHA nộp trên LMS là commit cuối cùng của nhánh `main` (commit code + evidence: {{SHA_CODE}})
- **Challenge ID:** `day13-k4-l3a-monitoring-llmops-v1`
- **Tên project Langfuse cá nhân:** `day13-k4-l3a-2A202602571` (Langfuse Cloud EU, `https://cloud.langfuse.com`)

## 2. Evidence index

| Evidence | Đường dẫn |
|---|---|
| Baseline CP0 (starter, trước khi sửa) | [evidence/00-baseline.txt](evidence/00-baseline.txt) |
| Pytest cuối | [evidence/01-pytest.png](evidence/01-pytest.png) · [.txt](evidence/01-pytest.txt) |
| Log validator | [evidence/02-log-validator.png](evidence/02-log-validator.png) · [.txt](evidence/02-log-validator.txt) |
| Dashboard validator | [evidence/03-dashboard-validator.png](evidence/03-dashboard-validator.png) · [.txt](evidence/03-dashboard-validator.txt) |
| Structured log | [evidence/04-structured-log.png](evidence/04-structured-log.png) · [.txt](evidence/04-structured-log.txt) |
| PII redaction | [evidence/05-pii-redaction.png](evidence/05-pii-redaction.png) · [.txt](evidence/05-pii-redaction.txt) |
| Trace list | [evidence/06-trace-list.png](evidence/06-trace-list.png) |
| Trace waterfall | [evidence/07-trace-waterfall.png](evidence/07-trace-waterfall.png) |
| Trace metadata | [08a generation: model/token/cost/prompt](evidence/08a-trace-generation.png) · [08b cột correlation_id/cost/TTFT/prompt](evidence/08b-trace-metadata-columns.png) · [08c metadata correlation_id + prompt label/version](evidence/08c-trace-metadata.png) |
| Prompt versions | [evidence/09-prompt-versions.png](evidence/09-prompt-versions.png) |
| Prompt rollback | [10a trước](evidence/10a-prompt-before.png) · [10b promote](evidence/10b-prompt-promoted.png) · [10c rollback](evidence/10c-prompt-rollback.png) · [10-prompt-rollout.txt](evidence/10-prompt-rollout.txt) |
| Dashboard runtime | [evidence/11-dashboard-overview.png](evidence/11-dashboard-overview.png) |
| Incident metric | [evidence/12-incident-metric.png](evidence/12-incident-metric.png) |
| Incident log | [evidence/13-incident-log.png](evidence/13-incident-log.png) · [.txt](evidence/13-incident-log.txt) |
| Incident trace | [evidence/14-incident-trace.png](evidence/14-incident-trace.png) |

Evidence 01–05, 11–13 được sinh bởi [`scripts/collect_evidence.py`](../scripts/collect_evidence.py) từ output thật (file `.txt` là output gốc, `.png` là bản render của chính text đó). Evidence 06–10, 14 là ảnh chụp trực tiếp trên project Langfuse cá nhân.

## 3. Kết quả kỹ thuật

| Nội dung | Baseline (starter `13b6066`) | Kết quả cuối | Nhận xét |
|---|---|---|---|
| `validate_logs.py` | 30/100 | 100/100 | Baseline thiếu `correlation_id` (`MISSING`) và enrichment ở 20/21 dòng |
| `validate_dashboard.py` | 6/6 | 6/6 | Contract YAML giữ nguyên; dashboard runtime là phần mới |
| `pytest` | 22 passed | 37 passed | Thêm 14 test: PII (CCCD, thẻ, passport, recursive), middleware, context leak, scrub trước khi ghi file, child observations |
| Số traces hợp lệ | 0 (chưa cấu hình key) | 124 trace (root observation) / ~430 observation trong project cá nhân tại thời điểm nộp (≥ 10 yêu cầu); ảnh 06 chụp lúc 65 trace | Mỗi trace có root + retrieval + generation |
| Số PII leak | 0 | 0 | Baseline 0 là vì `summarize_text` đã scrub; nhưng scrubber chưa được đăng ký trong pipeline log nên field mới nào chứa PII cũng lọt |
| Latency P95 / TTFT P95 | ~155 ms / 50 ms (starter, tracing tắt) | P50 154 ms, P95 2650 ms, P99 2965 ms / TTFT P95 50 ms | P95/P99 cao do 5 request đồng thời đầu tiên cùng fetch prompt từ Langfuse (cache stampede, `prompt_ms` 1.1–2.8 s); 17/22 request còn lại ~152 ms — xem mục 8 |
| Retrieval success rate | 100% | 100% | Từ `tool_success` |

## 4. Logging và PII

- **Cách tạo/nhận và truyền correlation ID:** [`app/middleware.py`](../app/middleware.py) gọi `clear_contextvars()` đầu mỗi request, rồi nhận `x-request-id` nếu header hợp lệ (`^[A-Za-z0-9._:-]{1,64}$`, chống log injection/header quá dài). Nếu không hợp lệ thì sinh `req-<8-hex>`. ID được `bind_contextvars(correlation_id=...)`, gán vào `request.state` và trả lại trong header `x-request-id` cùng `x-response-time-ms`. `agent.run` đưa correlation ID vào trace metadata qua `propagate_attributes`.
- **Các metadata được ghi vào structured log:** [`app/main.py`](../app/main.py) bind `user_id_hash` (SHA-256 rút gọn, không ghi user_id gốc), `session_id`, `feature`, `model`, `env` trước `request_received`, nên mọi log sau của request đều có cùng context. `response_sent` có thêm `latency_ms`, `ttft_ms`, `retrieval_ms`, `llm_ms`, `tokens_in/out`, `cost_usd`, `quality_score`, `doc_count`, `prompt_version`, `tool_success` và **`trace_id`** (link trực tiếp sang Langfuse).
- **Cách bảo đảm PII được scrub trước khi ghi:** [`app/logging_config.py`](../app/logging_config.py) đăng ký `scrub_event` sau `format_exc_info` (để scrub cả traceback) và **trước** `JsonlFileProcessor`/`JSONRenderer`. Processor scrub đệ quy mọi chuỗi trong event (kể cả payload lồng nhau và `error detail`), không chỉ riêng `payload`. [`app/pii.py`](../app/pii.py) có pattern cho email, thẻ thanh toán 13–19 số (có space/dash), CCCD 12 số, điện thoại VN (`0`, `+84`, `84`, `(+84)`, phân tách bằng space/`.`/`-`) và hộ chiếu VN. Các pattern chạy theo thứ tự dài → ngắn để số thẻ không bị pattern điện thoại cắt mất một phần.
- **Cách kiểm chứng kết quả:** test [`tests/test_pii.py`](../tests/test_pii.py) và [`tests/test_logging_middleware.py`](../tests/test_logging_middleware.py): gửi request chứa email/điện thoại/CCCD/thẻ giả rồi assert file log không chứa giá trị gốc; kiểm tra context không rò giữa hai request liên tiếp; kiểm tra header được propagate. Chạy runtime: `validate_logs.py` và [05-pii-redaction](evidence/05-pii-redaction.txt) (thêm một query CCCD giả vào `data/sample_queries.jsonl` để có đủ 4 loại PII).

## 5. Tracing và prompt versioning

- **Cách xác nhận traces do chính tôi tạo trong project cá nhân:** key trong `.env` (không commit) thuộc project `day13-k4-l3a-2A202602571`. `/health` trả `tracing_enabled: true`. Mọi trace được sinh bằng `scripts/load_test.py` và `scripts/prompt_versions.py run` trên máy tôi. Ảnh 06 thấy tên project và danh sách trace.
- **Cấu trúc root/retrieval/generation observations:** [`app/agent.py`](../app/agent.py). Root là `lab-agent-run` (type `agent`, trace name `day13-agent-request`), gồm ba con (ảnh [07](evidence/07-trace-waterfall.png)): `retrieval`, `prompt-resolve` (span lấy prompt từ Langfuse — thêm sau khi phát hiện bước này có thể mất 1–3 s) và `llm-generation`:
  - `retrieval` (type `retriever`): input là `query_preview` đã scrub, output là `doc_count` và `docs_preview`. Khi lỗi, SDK tự đánh dấu level `ERROR` kèm `status_message`.
  - `llm-generation` (type `generation`): `model`, `input` = prompt đã scrub PII, `output` rút gọn, `usage_details` {input, output, total}, `cost_details` {input, output, total} (3 USD / 15 USD mỗi 1M token), `completion_start_time` (= thời điểm có token đầu, suy ra TTFT) và `prompt` = đối tượng prompt từ Langfuse nên generation được link tới đúng prompt version.

  Không dùng `capture_input/output` mặc định để tránh gửi raw message. Tôi đã kiểm tra quan hệ cha–con bằng một OTLP endpoint giả lập trước khi chạy thật.
- **Cách nối trace với log:** `correlation_id` nằm trong trace metadata (propagate xuống mọi observation), còn `trace_id` được ghi vào log `response_sent`. Từ một dòng log có thể mở trace bằng `trace_id`, hoặc filter Traces theo metadata `correlation_id`.
- **Prompt name:** `day13-chat` (text prompt, giữ ba biến `{{feature}}`, `{{docs}}`, `{{message}}`).
- **Version/label baseline:** v1, labels `baseline` + `production`, template gốc.
- **Version/label candidate:** v2, label `candidate`, thêm dòng *"Answer in at most 3 short bullet points and cite the doc you used."*
- **Trace ID của mỗi version:** trích từ [10-prompt-rollout.txt](evidence/10-prompt-rollout.txt) (cùng input *"Explain why metrics traces and logs work together"*):

  | Bước | Label | Version app nhận | correlation_id | trace_id |
  |---|---|---|---|---|
  | chạy baseline | `baseline` | v1 | `req-8d847af8` | `6abc5ef98e0c9f70f87c48c66262c26e` |
  | chạy candidate | `candidate` | v2 | `req-4081b153` | `aa11cc654cf56666f3ce6ff597f01a71` |
  | sau promote | `production` | v2 | `req-197cddc2` | `85ba41993e981c36c8b43747f9bf3fb7` |
  | sau rollback | `production` | v1 | `req-ad35875a` | `a305daf450f551ac9bff251d8d92e03a` |
  | lặp lại promote → rollback | `production` | v2 → v1 | `req-2c680f36` → `req-bea7ab6b` | `3e8cd4e4…` → `90b7344be387a17d495c60969189f5ef` |

  v2 dài hơn nên `tokens_in` tăng 32 → 49 với cùng input — ví dụ prompt version ảnh hưởng trực tiếp tới token/cost.
- **Cách promote và rollback `production`:** dùng [`scripts/prompt_versions.py`](../scripts/prompt_versions.py). `promote` gọi `update_prompt(version=2, new_labels=["candidate","production"])`; label trong Langfuse là duy nhất giữa các version nên v1 mất `production`. `rollback` gắn lại `["baseline","production"]` cho v1. Sau mỗi bước tôi chạy lại cùng một input (`run --label production`) để chứng minh app lấy đúng version (xem [10-prompt-rollout.txt](evidence/10-prompt-rollout.txt) và ảnh 10a/10b/10c). Rollback không cần deploy lại code; client cache prompt tối đa 60 giây (`cache_ttl_seconds=60`).

## 6. Dashboard, SLO và alerts

- **Dashboard và sáu panel:** [`scripts/build_dashboard.py`](../scripts/build_dashboard.py) đọc `data/logs.jsonl` (nguồn chuẩn) và dựng đúng 6 panel theo [`config/dashboard.yaml`](../config/dashboard.yaml): (1) latency P50/P95/P99 + TTFT P95; (2) traffic request/phút; (3) error rate + breakdown `error_type` + retrieval success; (4) cost theo phút + tổng tích lũy; (5) tokens in/out; (6) mean quality. Tên panel, đơn vị, phép tổng hợp, time range 60 phút, refresh 30s và threshold đều đọc từ YAML. Title panel đổi màu `OK`/`BREACH` theo threshold. Panel latency vẽ thêm đường SLO 2000 ms nét đứt, panel errors vẽ thêm guardrail retrieval ≥ 90%. Có `--watch` để tự refresh và `--minutes/--bucket-seconds/--highlight` để zoom vào sự cố.
- **SLO và lý do chọn:** [`config/slo.yaml`](../config/slo.yaml): `fast_successful_requests`, 99.5% request `/chat` thành công **và** `latency_ms ≤ 2000` trong 28 ngày. Baseline đo được P50 ≈ 155 ms, P95 ≈ 165 ms, P99 ≈ 330 ms. Ngưỡng 3000 ms của starter quá lỏng: practice `rag_slow` (retrieval +2.5 s → khoảng 2.65 s/request, chậm gấp ~17 lần) vẫn được tính là "tốt". Vì vậy tôi hạ SLI xuống 2000 ms (khoảng 6× P99 baseline). Đường 3000 ms trong dashboard contract giữ nguyên làm trần cứng.
- **Cách tính error budget:** budget = (1 − 0.995) × tổng request trong 28 ngày = 0.5%. Ví dụ 10 000 request/ngày → 280 000 request/28 ngày → được phép tối đa 1 400 request chậm/lỗi, tương đương khoảng 201.6 phút sập hoàn toàn. Burn rate = (tỉ lệ bad trong cửa sổ ngắn) / 0.5%: burn rate 14.4 trong 1h (tiêu 2% budget) thì page, 6 trong 6h thì mở ticket. Policy: còn dưới 50% budget thì đổi prompt/model bắt buộc đi qua `candidate`; hết budget thì chỉ được rollback/fix.
- **Ba alert và runbook tương ứng:** [`config/alert_rules.yaml`](../config/alert_rules.yaml) + [`docs/alerts.md`](../docs/alerts.md):
  1. `ChatLatencyP95High`: P95 > 2000 ms, kéo dài 5m, P2, `#day13-k4l3a-oncall`.
  2. `ChatErrorRateHigh`: error rate > 2% (≥ 10 request), kéo dài 5m, P1, `#day13-k4l3a-oncall`.
  3. `CostPerRequestSpike`: cost trung bình/request > 0.004 USD (≈ 2× baseline), kéo dài 15m, P3, `#day13-k4l3a-finops`.

  Cả ba đều đo triệu chứng người dùng/chi phí; runbook ghi 3 bước kiểm tra theo Metrics → Logs → Traces và mitigation.

## 7. Điều tra challenge

- **Challenge ID:** `day13-k4-l3a-monitoring-llmops-v1` (cohort K4, file do Lab Coach gửi, lưu tại `config/challenge.json` — không commit). Chạy `python scripts/inject_incident.py` rồi `python scripts/load_test.py --challenge --concurrency 5` (2 lượt), kẹp giữa một đợt baseline và một đợt recovery để có mốc so sánh.
- **Khoảng thời gian điều tra:** 2026-09-29 **18:37:13 → 18:37:51** (Asia/Ho_Chi_Minh) — từ log `incident_enabled` tới `incident_disabled`; baseline 18:36:35–18:36:42, recovery 18:38:21–18:38:23.
- **Triệu chứng từ metrics** ([12-incident-metric](evidence/12-incident-metric.png)): panel *Latency* — P95 `latency_ms` nhảy từ ~154 ms (baseline/recovery) lên **~2 656 ms**, vượt SLO 2000 ms (và `latency_threshold_ms` 2000 của challenge) → alert `ChatLatencyP95High`. Đồng thời **TTFT P95 giữ nguyên 50 ms**, error rate 0%, retrieval success 100%, cost/token không đổi → hệ thống *chậm nhưng không lỗi*, và phần chậm nằm **trước** LLM. Toàn bộ 10 request chậm đều thuộc `feature=monitoring` (đúng `affected_feature` của challenge).
- **Log line và correlation ID liên quan** ([13-incident-log](evidence/13-incident-log.txt)): 10/10 `response_sent` trong khoảng sự cố có `latency_ms` 2652–2657 với **`retrieval_ms` 2500–2502** trong khi `llm_ms` 151–153 (baseline: `retrieval_ms` 0–2). Request được chọn:

  ```json
  {"event": "response_sent", "correlation_id": "req-4ccc9959", "feature": "monitoring", "latency_ms": 2657,
   "retrieval_ms": 2501, "llm_ms": 153, "ttft_ms": 50, "tool_name": "retrieval", "tool_success": true,
   "trace_id": "8d2ae58ff4555623f11d1120c6105b2b", "ts": "2026-09-29T11:37:50.382Z"}
  ```
- **Trace ID và span gây ảnh hưởng** ([14-incident-trace](evidence/14-incident-trace.png)): trace `8d2ae58ff4555623f11d1120c6105b2b` (metadata `correlation_id = req-4ccc9959` — cùng request với log). Waterfall: root `lab-agent-run` ≈ 2.65 s, trong đó **`retrieval` (retriever) ≈ 2.50 s** chiếm ~94%, `prompt-resolve` ≈ 0 s (cache hit), `llm-generation` ≈ 0.15 s với TTFT 0.05 s — giống hệt baseline. Span retrieval không có level ERROR → chậm chứ không lỗi.
- **Root cause:** bước retrieval (vector store / `app/mock_rag.retrieve`) bị chậm thêm ~2.5 s mỗi request (incident `rag_slow` được bật lúc 18:37:13 và tắt lúc 18:37:51 — latency trở về ~155 ms ngay sau đó ở đợt recovery). LLM, prompt management và mạng tới Langfuse không liên quan: `llm_ms`, TTFT và `prompt_ms` không đổi.
- **Loại trừ nhiễu:** dashboard còn một đỉnh nhỏ lúc 18:36:38 (5 request 1.4–1.9 s) *trước* khi bật incident. Log cho thấy các request đó có `retrieval_ms` 1–2 ms → không phải retrieval; trace của chúng (ví dụ `eeaec7639df872d7027ad4f281e88549`) có span `prompt-resolve` dài — đó là cold-start fetch prompt từ Langfuse (xem blocker (3) ở mục 8; do lúc chạy challenge `app/main.py` đang là bản cũ thiếu đoạn warm-up). Nhờ tách `retrieval_ms`/`prompt_ms`/`llm_ms` và span riêng, hai nguyên nhân khác nhau không bị gộp làm một.
- **Fix action:** (ngắn hạn) tắt/khắc phục nguồn gây chậm của vector store (`python scripts/inject_incident.py --disable` trong lab); đặt **timeout cho retrieval** (ví dụ 800 ms ≈ nhiều lần P99 bình thường) và **fallback** trả lời không có context (hoặc câu trả lời an toàn) thay vì bắt người dùng chờ; theo dõi P95 quay về < 2000 ms trên dashboard.
- **Preventive measure:** (1) alert `ChatLatencyP95High` (P95 > 2000 ms trong 5m) + runbook đã chỉ rõ bước so `retrieval_ms` vs `llm_ms`; (2) thêm SLI riêng cho retrieval (P95 `retrieval_ms` < 300 ms) để bắt sớm trước khi ảnh hưởng SLO tổng; (3) cache kết quả retrieval cho câu hỏi lặp, circuit breaker khi vector store chậm liên tục; (4) giữ `retrieval_ms`/`prompt_ms`/`llm_ms` trong log và span riêng trong trace để lần sau khoanh vùng trong vài phút.

## 8. Giải thích và tự đánh giá

- **Một quyết định kỹ thuật quan trọng và lý do:** tách retrieval và LLM thành child observation **và** ghi `retrieval_ms`/`llm_ms`/`trace_id` vào log `response_sent`. Nhờ vậy khi metric xấu, chỉ nhìn log đã biết bước nào chậm, và có `trace_id` để nhảy sang waterfall mà không phải tìm thủ công. Cùng tinh thần đó, tôi chỉ gửi lên Langfuse prompt đã scrub và câu trả lời rút gọn, thay vì bật auto-capture input/output của `@observe`.
- **Một lỗi/blocker đã gặp:** (1) Chạy `load_test.py --concurrency 5` thấy latency phía client 770–930 ms dù `latency_ms` của agent chỉ ~150 ms. (2) Log thật có dòng `"trace_id": "[REDACTED_CREDIT_CARD]c1dfd0c24bd4fed12d5"` — PII scrubber che nhầm trace ID. (3) Trên máy cá nhân, 5 request đầu có `prompt_ms` 1.1–2.8 s, một request (`req-1057a0d4`) rơi về `local-v1`; dashboard P95 = 2650 ms. (4) Cổng 8000 bị một app khác chiếm, API lab không bind được và load test nhận 404.
- **Cách tìm nguyên nhân và xử lý:** (1) So `x-response-time-ms` với `latency_ms` → thời gian mất trước khi agent chạy: endpoint `async def chat` gọi `agent.run` đồng bộ (`time.sleep`) nên chặn event loop, request phải xếp hàng. Sửa bằng `await run_in_threadpool(agent.run, ...)` (contextvars của structlog/OTel được copy sang thread) → ~160 ms. (2) Trace ID hex 32 ký tự có thể mở đầu bằng ≥13 chữ số nên khớp regex thẻ 13–19 số → loại `trace_id`, `user_id_hash` (ID do hệ thống sinh, không bao giờ chứa dữ liệu người dùng) khỏi scrubber, thêm test hồi quy `test_system_ids_are_not_mangled_by_pii_scrubber`. Bài học: redaction quá tay cũng là bug observability vì làm đứt liên kết log→trace. (3) Log có `prompt_ms` tách riêng + span `prompt-resolve` trong waterfall cho thấy thời gian nằm ở bước lấy prompt: warm-up lúc khởi động bị timeout 2 s (mạng qua VPN tới Langfuse EU chậm, có lần TLS handshake timeout), nên 5 request đồng thời đầu tiên cùng fetch (cache stampede). Sửa: warm-up gọi `get_prompt` với timeout 10 s + 2 retry trước khi nhận traffic; request vẫn giữ timeout 2 s + fallback local để Langfuse không bao giờ làm sập request. (4) `/health` trả `{"status":"ok","env":"development"}` — không phải app lab → chạy API ở cổng 8001 và thêm biến `LAB_BASE_URL` cho load test/inject incident. (5) Khi chạy CP3, log không có `prompt_ms` và không có `prompt_warmup` → so md5 thấy `app/main.py` trên máy đã bị ghi đè bằng một bản cũ trong lúc đồng bộ file giữa môi trường phát triển và máy chạy (lọt cả vào commit `95834b4`); khôi phục bản đã test và từ đó đối chiếu checksum của mọi file đã sửa sau mỗi lần đồng bộ. Bài học: kiểm tra log khởi động (event `prompt_warmup`) ngay sau mỗi lần restart để biết đúng code đang chạy.
- **Cách hiểu luồng Metrics → Logs → Traces:** metrics (dashboard) cho biết *có vấn đề gì, từ lúc nào, mức độ ra sao* nhưng chỉ ở dạng tổng hợp. Logs trong khoảng thời gian đó cho biết *request nào* bị ảnh hưởng (`correlation_id`, feature, error_type, retrieval_ms). Trace của đúng request đó (qua `trace_id`/`correlation_id`) cho biết *span nào* gây ra: so duration/level của `retrieval` với `llm-generation`. Chỉ kết luận root cause khi cả ba lớp cùng chỉ về một chỗ.
- **Vai trò của prompt version, token/cost, SLO hoặc rollback trong vận hành LLM:** prompt là "code" của LLM nhưng thay đổi được mà không cần deploy. Vì vậy mỗi trace phải ghi prompt version để biết thay đổi hành vi, cost hay latency đến từ version nào; label `production` + rollback giúp gỡ một prompt xấu trong vài giây. Token/cost là tín hiệu riêng của LLM: output dài bất thường làm tăng cả chi phí lẫn latency mà error rate vẫn bình thường. SLO + error budget biến chuyện "hệ thống ổn không" thành con số, quyết định lúc nào được thử prompt mới và lúc nào phải đóng băng.
- **Điều quan trọng nhất đã học:** observability phải thiết kế từ đầu để *nối* được ba lớp tín hiệu (correlation_id/trace_id), và redaction phải nằm trong pipeline trước khi serialize chứ không phụ thuộc vào việc từng dòng log nhớ gọi scrub.
- **Hạn chế hoặc phần chưa hoàn thành, nếu có:** (1) LLM là mock nên quality proxy là heuristic, không phản ánh chất lượng câu trả lời thật; (2) dashboard là ảnh tĩnh sinh bằng matplotlib (có chế độ `--watch` refresh 30 s) chứ chưa phải Grafana có alert engine thật — alert rules mới ở mức định nghĩa + runbook; (3) chạy `pytest` trên máy có `.env` cũng gửi vài trace test lên project (ví dụ trace `12e03823…` với `query_preview` đã che `[REDACTED_CREDIT_CARD]`) — vô hại và còn cho thấy trace không chứa PII thô, nhưng nên tách project test riêng; (4) prompt cache TTL 60 s nên sau khi đổi label, process đang chạy có thể dùng version cũ tối đa ~60 s.

## 9. Checklist trước khi nộp

- [x] Kết quả và evidence thuộc commit SHA cuối.
- [x] Tất cả ảnh/output mở được bằng đường dẫn tương đối.
- [x] Incident evidence nối đúng metric → log → trace.
- [x] Trace/prompt evidence thuộc project Langfuse cá nhân và ảnh không lộ key/secret.
- [x] Repository chạy lại được theo README.
- [x] Không có secret, API key, PII thô hoặc evidence của người khác/lớp khác (`python scripts/collect_evidence.py scan`).
- [ ] URL repo và commit SHA cuối đã được nộp trên LMS/Codelabs.

## 10. Bonus (automation)

- [`scripts/collect_evidence.py`](../scripts/collect_evidence.py): tự sinh evidence tests/validators/log/PII/dashboard/incident từ output thật, kèm lệnh `scan` quét secret/`.env`/`challenge.json` trước khi commit.
- [`scripts/build_dashboard.py`](../scripts/build_dashboard.py): dashboard generation từ contract YAML (có chế độ `--watch`).
- [`scripts/prompt_versions.py`](../scripts/prompt_versions.py): tạo/promote/rollback prompt và chạy cùng input để ghi lại trace ID theo từng label.
