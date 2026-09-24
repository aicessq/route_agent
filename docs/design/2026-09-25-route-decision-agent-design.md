# 设计冻结：约束验证型户外路线决策 Agent V1

> 2026-09-25 冻结。变更需先改本文并记入 [decisions.md](../decisions.md)。
> 本阶段为"公司正式安排的实验性任务"：本机 + 公开/合成数据，不接生产。

## 1. 产品边界

- **严格只读**：不写业务数据、不收藏、不改路线/POI/行程。
- **有界决策任务**：一次需求形成一个 Decision Run；最多 1–2 轮追问；完成后只读归档。
- **候选池**：CMS 精选路线 + 公开轨迹（来源分级）；不含私有行程。
- **核心六类约束**：
  - 硬约束：距离、累计爬升、指定结构化风险类型（`risk_cliff_exposure` 等 5 类枚举）。
  - 软约束：预计时长、补给设施（water_source/store/restroom/camp）、评分。
  - 天气仅触发重新验证，不宣称"雨天安全"。
- **单 Agent**：不派生子 Agent；并发只发生在事实取证层。

## 2. 决策语义

1. 三态验证 `PASS/FAIL/UNKNOWN`；硬约束 `UNKNOWN` 不通过（证据缺失≠满足）。
2. 字段级放宽授权：`authorized + max_value + order` 进入 Intent；无上限先追问；风险约束永不可放宽。
   Verifier 始终按原阈值判定，放宽由 Repair 显式执行并记录（阶段 4）。
3. `NO_MATCH`（没有符合路线）是正常业务结论；`INSUFFICIENT_EVIDENCE` 表示证据不足；
   `FAILED` 仅用于系统/依赖异常。
4. 无可验证约束时不做"已验证推荐"（返回 INSUFFICIENT_EVIDENCE + warning）。
5. 用户评价（未来数据源）只作软排序与风险线索，不可成为安全事实。

## 3. 运行时协议（目标态；阶段 1 为内存子集）

- MySQL 是真相源：`Decision → RunAttempt → Step` + 证据账本 + 调用账本 + Outbox。
- Redis 只做通知/缓存/限流；Redis 丢失由 Reconciler 从 MySQL 修复。
- 阶段级 checkpoint：`COMPILE_INTENT → RETRIEVE_CANDIDATES → COLLECT_EVIDENCE → VERIFY → REPAIR → FINALIZE`。
- 调用语义：`PREPARED/RUNNING/SUCCEEDED/FAILED/UNKNOWN` 账本 + 有界重试；at-least-once 调度 + 去重。
- 条件变更生成新 `intent_version`，按依赖图增量失效；重试创建新 RunAttempt；协作式取消用 `run_epoch` fencing。
- Worker：多实例 asyncio 池，MySQL 租约领取 Step；CPU 密集计算进进程池。

## 4. 降级矩阵

| 故障 | 行为 |
| --- | --- |
| 取证 Provider 故障 | 该批事实缺失 → UNKNOWN → 不得 VERIFIED |
| 搜索 Provider 故障 | Run FAILED（依赖失败） |
| 主模型不可用（阶段4） | 备用模型一次；全不可用时已编译 Intent 继续检索/验证，模板输出 |
| 关键工具超时 | 按事实 criticality：硬约束证据缺失 → INSUFFICIENT_EVIDENCE |
| 过载 | 分层限流背压：快速拒绝 + retryAfter；不静默转普通搜索 |

## 5. 性能与成本（阶段 2/4 落地，目标值非实测值）

- 双独立入口；创建接口 P95 < 200ms；正常决策 P95 20–30s；硬截止 60s。
- 分层漏斗：宽召回 → 索引字段预筛 → Top-K 批量取证 → 动态取证 → 早停。
- 每 Run 最多 2 次主模型调用（小模型 Intent，按需升级大模型一次）；摘要懒生成。
- 两级缓存 + singleflight（事实层合并，不跨用户合并完整报告）。
- 全链路 CostReport；逐项消融（单查→批量→缓存→Top-K）。

## 6. 评测

- **双层**：确定性 Golden Query（阶段 1 已实现，10 条）+ 真实模型 Golden Set（阶段 4）。
- **门槛**：硬约束误通过数必须为 0；三组基线对照（固定筛选/普通 ReAct/本方案）。
- 预注册 BenchmarkSpec；所有百分比实测后写入，禁止预写。

## 7. 非目标（Non-goals）

- 不做多 Agent 协商、动态 MCP 工具发现、自由 SQL/URL 工具。
- 不承诺户外绝对安全；输出必须提示"基于当前证据有效期"。
- 不接生产数据/流量；不把本机压测表述为生产指标。
- 前端 UI 不在本阶段范围。

## 8. 归属与表述边界

- 表述为"公司正式安排的实验性任务/测试环境原型（公开数据）"。
- 不写：生产上线、服务真实用户、复用生产海量数据、线上 QPS/成本改善。
