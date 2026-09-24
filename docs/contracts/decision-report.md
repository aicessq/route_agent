# 契约：DecisionReport（decision-report.v1）

决策任务的最终产物。**结构化报告先于任何自然语言表达**；
摘要（模板或 LLM）只能改写报告内容，不能覆盖验证结论。

## 顶层字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `report_version` | string | `decision-report.v1` |
| `run_id` | string? | 关联 Run |
| `status` | DecisionStatus | 见下表 |
| `execution_mode` | ExecutionMode | `deterministic_preview / local_async / structured_fallback` |
| `intent_fingerprint` | string | 绑定的意图版本指纹 |
| `policy_version` | string | FactPolicy + Verifier 版本 |
| `generated_at` | datetime | — |
| `candidates` | CandidateReport[] | 全部候选的逐约束结果 |
| `verified_route_ids` | string[] | 仅硬约束全 PASS 的候选 |
| `warnings` | string[] | 运行提示（依赖故障、无候选等） |
| `budget` | object | 候选数/事实数/provider 调用数 |

## DecisionStatus

| 状态 | 含义 |
| --- | --- |
| `VERIFIED_MATCHES` | 存在硬约束全 PASS 的候选 |
| `NO_MATCH` | 候选存在但无合格（业务结论，非异常） |
| `INSUFFICIENT_EVIDENCE` | 硬约束证据缺失/冲突/依赖故障，无法验证 |
| `CANCELLED` / `FAILED` | 用户取消 / 系统异常 |

状态推导规则：任一候选 hard_pass → VERIFIED_MATCHES；否则任一硬约束 UNKNOWN →
INSUFFICIENT_EVIDENCE；否则 NO_MATCH。无可验证约束 → INSUFFICIENT_EVIDENCE。

## CandidateReport

| 字段 | 说明 |
| --- | --- |
| `route_id / title / source_kind` | 候选标识与来源分级 |
| `checks: ConstraintCheck[]` | 逐约束判定 |
| `hard_pass: bool` | 全部硬约束 PASS 且无 UNKNOWN |
| `soft_score: 0..1` | 软约束满足比例 |
| `tradeoffs: string[]` | 未满足项/证据不足项/陈旧证据提示 |
| `evidence: EvidenceRecord[]` | 本次使用的证据（引用来源） |

## ConstraintCheck

```json
{
  "constraint": "max_distance_km",
  "strength": "hard",
  "status": "PASS",
  "expected": 8,
  "actual": 7.6,
  "fact_type": "distance_km",
  "evidence_ids": ["bj-yanqing-001:distance_km:fixture"],
  "reason": "满足阈值",
  "stale": false
}
```

- `strength ∈ {hard, soft}`；`status ∈ {PASS, FAIL, UNKNOWN, NOT_APPLICABLE}`。
- `evidence_ids` 非空才允许 `PASS/FAIL`（引用支持）。
- `stale=true` 表示使用了宽限期内陈旧静态证据，必须在 tradeoffs 中提示。

## 完成门（Completion Gate）

进入 `VERIFIED_MATCHES` 前，由代码校验（LLM 无权绕过）：

1. 每条硬约束均有三态结论；
2. 推荐候选硬约束全 PASS 且引用存在；
3. 动态证据未过期（或按策略标记陈旧）；
4. `intent_fingerprint` 与当前意图一致。

## 摘要（懒生成）

`POST /decisions/{id}/summary` 在用户查看时才生成并绑定报告指纹；
报告变化后旧摘要不可复用。阶段 1 为确定性模板；阶段 4 可选 LLM，
且 LLM 摘要须通过"不得引入报告外结论"的校验。
