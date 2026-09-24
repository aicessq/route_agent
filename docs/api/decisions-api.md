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

请求体同 preview（`intent` + `candidate_limit`）。

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
  "report": { "...DecisionReport...": true },
  "error": null,
  "execution_mode": "local_async"
}
```

状态取值：`QUEUED / RUNNING / SUCCEEDED / NO_MATCH / INSUFFICIENT_EVIDENCE /
CANCEL_REQUESTED / CANCELLED / FAILED`。
终态任务 `report` 保留（CANCELLED 除外）；`FAILED` 时 `error` 非空。

## DELETE /api/v1/decisions/{decision_id}

协作式取消：

- 运行中：置 `CANCEL_REQUESTED`，在途请求可完成但迟到结果不产出报告，终态 `CANCELLED`。
- 已终态：返回原终态（幂等取消）。
- 不存在：`404 / 10003`。

## POST /api/v1/decisions/{decision_id}/summary

按需生成摘要（懒生成，同一报告只生成一次，绑定报告指纹）。

- `200`：`{"summary": "...", "report_fingerprint": "...", "metadata": {}}`。
- `409 / 10004`：任务尚未产出报告（QUEUED/RUNNING/CANCELLED）。

## 幂等语义

- 键 = `user_key + Idempotency-Key`；请求指纹 = `intent.fingerprint()`。
- 同键同指纹 → 返回原任务；同键异指纹 → 409。
- 幂等键只解决网络重试/重复点击；**不跨用户共享任何结果**。

## 示例（curl）

```bash
curl -s -X POST http://127.0.0.1:18230/api/v1/decisions \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: my-idem-key-000001' \
  -d '{"intent":{"location":"北京","max_distance_km":10,"max_elevation_gain_m":400,"avoid_risk_types":["risk_cliff_exposure"]}}'
```
