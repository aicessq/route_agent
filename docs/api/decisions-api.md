# API 契约：decisions

统一响应信封：`{"code": 0, "message": "ok", "data": {...}}`；
错误时 `code` 为业务码、`data` 为 null。

业务码：

| code | HTTP | 含义 |
| --- | --- | --- |
| 0 | 200/202 | 成功 |
| 10001 | 400 | Idempotency-Key 缺失或格式非法（8–128 可打印 ASCII） |
| 10002 | 409 | Idempotency-Key 已被不同请求使用 |
| 10003 | 404 | 决策任务不存在 |
| 10004 | 409 | 报告未产出，无法生成摘要 |
| 10005 | 409 | 应答非法（字段不支持/合并后意图非法/任务不在等待状态） |

## POST /api/v1/decisions/preview

同步确定性验证（Fake Provider 驱动，用于开发联调与快速验证）。

请求：

```json
{
  "intent": { "...RouteIntent v1...": true },
  "candidate_limit": 10
}
```

响应 200：`data` 为完整 [DecisionReport](../contracts/decision-report.md)。

## POST /api/v1/decisions

创建异步决策任务。**必须携带 `Idempotency-Key` 请求头。**

请求体（`query` 与 `intent` 二选一，同时给出或都不给返回 422）：

```json
{"query": "周六带8岁孩子去北京延庆徒步，8公里以内，爬升不超过300米，避开悬崖", "candidate_limit": 10}
```

- `query`：自然语言，先经 LLM 编译（未配置模型服务时自动降级为规则提取）。
- `intent`：结构化 [RouteIntent](../contracts/route-intent.md)（跳过编译）。

请求指纹：`query` 模式取 `query:<原文>`，`intent` 模式取意图 SHA-256。

响应：

- `202`：新任务，`data = {"decision_id": "...", "status": "QUEUED", "poll_after_ms": 500}`。
- `200`：幂等重放（同 user + 同 key + 同请求指纹），返回原任务。
- `409 / 10002`：同 key 不同请求。
- `400 / 10001`：key 缺失或非法。

## GET /api/v1/decisions/{decision_id}

轮询任务状态。

响应 200：

```json
{
  "decision_id": "...",
  "status": "SUCCEEDED",
  "created_at": "...", "updated_at": "...",
  "current_step": "FINALIZED",
  "question": null,
  "intent": { "...当前 RouteIntent...": true },
  "intent_version": 2,
  "compile_source": "llm_primary",
  "report": { "...DecisionReport...": true },
  "error": null,
  "execution_mode": "local_async"
}
```

状态取值：`QUEUED / RUNNING / WAITING_USER / SUCCEEDED / NO_MATCH / INSUFFICIENT_EVIDENCE /
CANCEL_REQUESTED / CANCELLED / FAILED`。

- `WAITING_USER` 时 `question` 非空：`{field, text, kind}`（`missing_field / missing_constraints /
  relaxation_consent`），等待 `answers` 端点。
- `current_step`：`COMPILE_INTENT / COMPLETENESS_GATE / RETRIEVE_VERIFY / REPAIR / FINALIZED`。
- `compile_source`：`llm_primary / llm_fallback / rule_extraction`（结构化创建为空）。
- `intent_version`：应答合并或授权放宽后递增；报告的 `intent_fingerprint` 与当前意图一致。
- 终态任务 `report` 保留（CANCELLED 除外）；`FAILED` 时 `error` 非空。
- 报告 `budget.llm`：该 Run 的模型调用数、token 与延迟（CostReport）。

## POST /api/v1/decisions/{decision_id}/answers

回答追问或授权放宽；任务必须在 `WAITING_USER` 状态。

请求：

```json
{
  "answers": {"location": "北京·延庆", "max_distance_km": 10},
  "authorize_relaxation": {"max_distance_km": 10}
}
```

- `answers` 支持字段：`location / travel_date / max_distance_km / max_elevation_gain_m /
  max_duration_minutes / min_rating`（未知字段 409）。
- `authorize_relaxation`：为软字段授权放宽上限（风险字段拒绝）；
  合并进 `intent.relaxation` 后由 Repair 在授权范围内显式执行。
- 合并后的意图必须通过 RouteIntent 校验，否则 409。

响应 200：`{"decision_id": "...", "status": "QUEUED", "intent_version": 2, "poll_after_ms": 500}`，
任务以新意图版本重新驱动。

## DELETE /api/v1/decisions/{decision_id}

协作式取消：

- 运行中：置 `CANCEL_REQUESTED`，在途请求可完成但迟到结果不产出报告，终态 `CANCELLED`。
- 已终态：返回原终态（幂等取消）。
- 不存在：`404 / 10003`。

## POST /api/v1/decisions/{decision_id}/summary

按需生成摘要（懒生成，绑定报告指纹）。

- `200`：`{"summary": "...", "report_fingerprint": "...", "channel": "llm|template", "metadata": {}}`。
  LLM 通道失败、超长或引入报告外路线时自动回落 `template`，永不阻塞报告交付。
- `409 / 10004`：任务尚未产出报告（QUEUED/RUNNING/CANCELLED）。

## 幂等语义

- 键 = `user_key + Idempotency-Key`；请求指纹 = 意图 SHA-256（或 `query:<原文>`）。
- 同键同指纹 → 返回原任务；同键异指纹 → 409。
- 幂等键只解决网络重试/重复点击；**不跨用户共享任何结果**。

## 示例（curl）

```bash
# 自然语言创建（LLM 编译，未配置模型时规则降级）
curl -s -X POST http://127.0.0.1:18230/api/v1/decisions \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: my-idem-key-000001' \
  -d '{"query":"周六带8岁孩子去北京延庆徒步，8公里以内，爬升不超过300米，避开悬崖"}'

# 结构化创建
curl -s -X POST http://127.0.0.1:18230/api/v1/decisions \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: my-idem-key-000002' \
  -d '{"intent":{"location":"北京","max_distance_km":10,"max_elevation_gain_m":400,"avoid_risk_types":["risk_cliff_exposure"]}}'

# 追问应答（status=WAITING_USER 时）
curl -s -X POST http://127.0.0.1:18230/api/v1/decisions/<id>/answers \
  -H 'Content-Type: application/json' \
  -d '{"answers":{"location":"北京·延庆"}}'
```
