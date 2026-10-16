# 架构文档

> 版本：2026-10-12（阶段 1+4 完成，阶段 2 部分）。本文描述目标架构与当前实现状态的对应关系；
> 标注【阶段 N】的模块尚未实现，属于 [roadmap](roadmap.md) 范围。

## 1. 系统定位

约束验证型户外路线决策 Agent：把复合出行需求编译为结构化约束，
经候选召回与证据收集后由**确定性代码**验证，输出可追溯的结构化决策报告。

双入口原则：

- **普通搜索**：关键词/明确筛选，同步、零 LLM、零 Agent 状态开销（现有搜索服务，不在本仓库）。
- **智能决策**：复合约束、条件分支、授权放宽，走异步 Decision Run（本仓库）。

## 2. 目标架构（全量）

```text
客户端
  │ POST /api/v1/decisions（Idempotency-Key）
  ▼
Agent API（FastAPI）
  │ 202 + decisionId        ┌──────────── MySQL（真相源）────────────┐
  ▼                         │ agent_decisions / agent_runs /         │
Transactional Outbox        │ agent_run_steps / agent_evidence /     │
  │ Redis 通知（可丢失）     │ agent_tool_calls / agent_outbox        │
  ▼                         └────────────────────────────────────────┘
asyncio Worker 池【阶段3】
  ├─ Intent Compiler（LLM，阶段4）→ RouteIntent
  ├─ Completeness Gate（规则追问，阶段4）
  ├─ Fact Planner（面向"缺什么事实"，不面向内部工具名）
  ├─ Evidence Gateway（批量 routeIds+factTypes，阶段2）
  │    ├─ SearchProvider（ES 结构化查询模板）
  │    ├─ 路线/风险/设施 FactProvider
  │    └─ WeatherProvider（仅触发重新验证）
  ├─ Deterministic Verifier（本仓库已实现）
  ├─ Bounded Repair（补证据/换候选/已授权放宽/追问/停止，阶段4）
  ├─ Completion Gate（本仓库部分实现）
  └─ Template/LLM Renderer（模板已实现，LLM 阶段4）
  ▼
DecisionReport（结构化）→ 状态查询 / 按需摘要
```

## 3. 当前实现（阶段 1 + 阶段 4）

```text
客户端
  │ POST /decisions/preview            POST /decisions（query | intent）
  ▼                                        ▼
DecisionService（同步验证）          DecisionOrchestrator（asyncio 任务）
  ├─ Provider 检索/取证                  ├─ COMPILE_INTENT：LLM 编译（降级梯）
  ├─ RouteVerifier（三态判定）           │    └─ Agnes AI 网关 / Fake / 规则提取
  └─ DecisionReport                      ├─ COMPLETENESS_GATE：规则追问（≤2 轮）
                                         │    └─ WAITING_USER → answers 恢复
                                         ├─ RETRIEVE_VERIFY：decide()
                                         ├─ REPAIR：五动作 + 前置校验 + 进展门
                                         │    └─ LLM 提议（越权即拒）/ 确定性兜底
                                         └─ FINALIZED：报告 + CostReport（budget.llm）
```

- 领域层 `app/domain/` 是全链路的唯一语义来源：Intent、Evidence、Verifier、FactPolicy、Report。
- `app/providers/` 以 Protocol 定义 SearchProvider/EvidenceProvider；Fake 与 ES 适配器同构，
  未来替换为内部服务 Adapter 时不改上层。地点归一化为共享模块 `app/providers/location.py`
  （Fake 与 ES 通道用同一套写法容错）。
- **检索通道装配开关**：`search_provider: fake | elasticsearch`（`config/app.yaml`，环境变量
  `SEARCH_PROVIDER` 可覆盖）。ES 通道检索 10,000 条确定性合成路线（`eval/synthetic.py` 生成、
  `scripts/es_seed_synthetic.py` 灌入本地索引）；`/health` 暴露当前装配，供测试页展示。
- **检索召回语义**（ES 通道）：意图地点经归一化后以分词 `match + operator=and` 召回，
  城市级（"北京"）与区县级（"延庆"/"北京延庆"）均可命中；距离/爬升阈值过滤用
  `should(range, 缺字段)` 形态——**超阈值候选在检索层淘汰，缺数据候选保留进入验证层判
  UNKNOWN**，不在检索层无声丢弃。
- **证据通道现状**：`ElasticsearchRouteProvider.collect` 已实现（分批 `_mget`，字段缺失 →
  `UNAVAILABLE`；当前为"索引即证据源"的原型简化，动态过期/冲突由 Fake 注入场景覆盖，
  阶段 2 由批量 Evidence Gateway 替换）。`app/repositories/` 已定义 MySQL 表模型与仓储语义
  （租约领取、Outbox 领取、证据批量 upsert），测试用 SQLite 内存库驱动同一套 SQL 语义；
  **API 当前仍使用内存存储**，持久化接线属于阶段 3。
- 本地测试页 `static/index.html`（挂载 `/ui/`）：开发自测工具，覆盖自然语言/结构化表单、
  追问应答、三态报告渲染、摘要与取消；不是产品 UI。

## 4. 模块边界与职责

| 模块 | 职责 | 不做什么 |
| --- | --- | --- |
| `app/domain/intent.py` | RouteIntent v1：硬/软约束、字段级放宽授权、指纹 | 不做自然语言解析（阶段 4 由 LLM 编译） |
| `app/domain/evidence.py` | EvidenceRecord：raw/normalized、来源、有效期、citation | 不做跨源融合（FactPolicy 裁决） |
| `app/domain/policies.py` | FactPolicy 注册表：单位、criticality、宽限期、来源优先级 | — |
| `app/domain/verifier.py` | 三态确定性判定；硬约束 UNKNOWN 不通过 | 不调模型、不发网络请求 |
| `app/services/decision_service.py` | 检索→取证→验证→报告编排；依赖隔离 | 不做 Repair 循环（阶段 4） |
| `app/providers/*` | 类型化数据适配；Fake 支持故障注入；ES 适配器 search/collect 已实现 | 不暴露 SQL/DSL/任意 URL 给模型 |
| `app/providers/location.py` | 地点归一化（共享，检索层写法容错） | 不改变意图原文与验证语义 |
| `app/api/*` | 严格 DTO、幂等键、统一信封 | 不在请求线程跑完整决策 |
| `app/runtime/*` | Run 状态机 + 内存任务存储 | 不伪装持久化恢复能力（阶段 3） |
| `app/repositories/*` | MySQL 真相源表模型与仓储 | API 尚未接线（阶段 3） |

## 5. 关键运行语义

### 5.1 三态验证

| 状态 | 含义 | 对硬约束 |
| --- | --- | --- |
| `PASS` | 有足够证据支持满足 | 可通过 |
| `FAIL` | 有证据证明违反 | 排除 |
| `UNKNOWN` | 缺失/冲突/过期/依赖故障 | **不通过**，候选不进已验证推荐 |

### 5.2 证据生命周期

- 每条事实携带 `source / observed_at / valid_until / entity_revision`。
- 静态事实（距离/爬升）过期后在 `stale_grace_seconds` 宽限期内可用，check 标记 `stale=true` 并写入 tradeoffs。
- 动态风险事实过期即 `UNKNOWN`，宽限期为 0。
- 依赖故障不写入伪造证据，Run 转为 `INSUFFICIENT_EVIDENCE` + warning。

### 5.3 任务状态机（当前内存版）

```text
QUEUED → RUNNING → SUCCEEDED / NO_MATCH / INSUFFICIENT_EVIDENCE / FAILED
              ↘ CANCEL_REQUESTED → CANCELLED（终态幂等取消）
```

- 幂等：同 user + Idempotency-Key 同请求返回原任务；同键不同请求 409。
- 取消协作式：终态后取消返回原状态；运行中取消后迟到结果不产出报告。
- `FAILED` 仅表示系统/依赖异常；`NO_MATCH` 是业务结论。

## 6. 数据模型（MySQL 真相源）

| 表 | 用途 |
| --- | --- |
| `agent_decisions` | 用户决策（幂等键约束），历史不可变 |
| `agent_runs` | 执行尝试 + run_epoch/intent_version + 租约字段 |
| `agent_run_steps` | 阶段级 checkpoint |
| `agent_evidence` | 证据账本（批量 upsert，按 run/route/fact 唯一） |
| `agent_tool_calls` | 调用账本 PREPARED/RUNNING/SUCCEEDED/FAILED/UNKNOWN |
| `agent_outbox` | 事务性 Outbox（Redis 丢失可由 Reconciler 修复） |

迁移：`alembic -x db_url=<URL> upgrade head`。

## 7. 降级与不变量（设计约束，逐步落地）

| 场景 | 行为 | 状态 |
| --- | --- | --- |
| 取证 Provider 故障 | `INSUFFICIENT_EVIDENCE` + warning，不伪造通过 | ✅ 已实现 |
| 搜索 Provider 故障 | Run `FAILED`（依赖失败，非业务无结果） | ✅ 已实现 |
| 无可验证约束 | 不做"已验证"声明 | ✅ 已实现 |
| LLM 不可用 | 规则/表单降级，结构化报告照常产出 | ✅ 已实现（限流实测：0 误通过保持，覆盖 6/8） |
| 关键工具超时 | 按事实 criticality 降级；硬约束证据缺失不得 VERIFIED | 阶段 3 |
| Worker 崩溃 | Outbox+租约+checkpoint 阶段级续跑 | 阶段 3 |

系统不变量（任何降级不得违反）：

1. 硬约束违规路线绝不推荐。
2. 硬约束 `UNKNOWN` 不得冒充 `PASS`。
3. 无授权不放宽；风险约束永不可放宽。
4. 每条推荐结论可追溯到具体证据。

## 8. 可观测性

阶段 1：结构化 warning + 报告内 budget（候选数、事实数、provider 调用数）。
阶段 2/3：RED 指标 + decisionId/runId/toolCallId 关联 Trace（低基数标签，routeId/userId 不进时序标签）。

## 9. 部署形态

- 本机默认：`uvicorn app.main:app`（零外部依赖，Fake Provider）。
- 本机 ES 通道：`scripts/es_seed_synthetic.py` 灌数后 `SEARCH_PROVIDER=elasticsearch` 启动
  （docker compose 提供 MySQL 3307 / Redis 6380 / ES 9201；ES 检索+取证通道已可用）。
- 阶段 3：MySQL 持久化接线 + Outbox + 独立 Worker 进程。
- 不接入生产；公开版与内部版仅通过 Provider Adapter 差异区分。
