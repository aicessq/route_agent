# 学习指南（LEARNING.md）

> 面向把本仓库当作**学习/简历项目**的读者：为什么这样设计、按什么顺序读、
> 动手跑什么实验、能对面试官讲出什么。所有数字与结论均来自本机实测
> （`eval/results/`、`works_log.md`），不外推为生产指标。

## 1. 这个项目解决什么问题

一句话：**LLM Agent 的"幻觉治理"问题**——让大模型在一个有安全边界的检索-验证
闭环里干活，而不是直接回答"去哪徒步"。

直觉上让 LLM 直接推荐路线会有四类失败：编造路线（无证据）、把"没查到悬崖"
当成"没有悬崖"（缺失当满足）、悄悄放宽用户的硬约束（越权）、跑挂了也不告诉你
（不可观测）。本项目的答案是把 LLM 的职责**降级为三件事**：

| LLM 可以做 | 确定性代码独占 |
| --- | --- |
| 意图编译（自然语言 → RouteIntent） | 三态判定 PASS/FAIL/UNKNOWN（`app/domain/verifier.py`） |
| 修复提议（五个枚举动作之一） | 证据有效性/过期/冲突裁决（FactPolicy） |
| 摘要表达（不许引入报告外结论） | 幂等、取消、状态机、降级梯的推进 |

判定权不外借——这是全项目最重要的一句话，也解释了之后几乎所有设计。

## 2. 三分钟核心思想

- **三态验证**：每条约束（距离/爬升/风险…）对每条候选路线给出 `PASS / FAIL /
  UNKNOWN`。硬约束出现 UNKNOWN 就不通过——"没查到悬崖记录"≠"没有悬崖"。
- **证据先行**：每个判定引用具体证据（来源/观测时间/有效期/实体版本），动态
  事实过期后不支持硬约束通过；报告里每条推荐可回溯到证据 ID。
- **降级梯**：LLM 编译失败 → 升级大模型一次 → 规则提取。模型全挂时服务仍
  产出结构化报告（实测：0 硬约束误通过保持）。
- **HITL 追问**：缺信息不猜——完备性门拦截并追问（≤2 轮），放宽必须来自用户
  原话或 answers 端点的显式授权；风险约束永不可放宽。
- **有界修复**：修复循环最多 2 轮、五个动作、无进展即停——防止 Agent 无限
  自说自话烧 token。

## 3. 推荐阅读路径

按依赖顺序读，每步带着括号里的问题去读：

1. **设计冻结**：[docs/design/2026-09-25-route-decision-agent-design.md](design/2026-09-25-route-decision-agent-design.md)——
   先看"为什么不做自由 Agent"。（哪些东西被刻意排除了？为什么？）
2. **契约三件套**：`docs/contracts/route-intent.md` / `evidence.md` /
   `decision-report.md`。（为什么 IR 先冻结、为什么证据要带有效期？）
3. **领域四件套**（本仓库的心脏，共几百行）：
   - `app/domain/intent.py`——RouteIntent 为什么 `extra=forbid`？
     `relaxation` 为什么挂在意图上而不是修复层？
   - `app/domain/policies.py`——为什么过期策略按事实类型分（风险宽限期为 0）？
   - `app/domain/verifier.py`——三态怎么合并成 `hard_pass`？STALE + 宽限期
     怎么处理？
   - `app/domain/evidence.py`——citation_id 为什么是内容哈希？
4. **服务层闭环**：`app/services/decision_service.py`（检索→取证→验证→报告，
   一百来行的核心）→ `decision_orchestrator.py`（异步状态机 + 修复循环）→
   `repair.py`（五动作 + 服务端前置校验 + 进展门）→ `completeness_gate.py`。
5. **Provider 层**：`app/providers/contracts.py`（两个 Protocol）→
   `fake.py`（fixture + 故障注入：为什么故障注入要是一等公民？）→
   `elasticsearch.py`（真实通道：查询模板为什么必须是服务端生成、缺字段候选
   为什么保留）→ `location.py`（写法容错为什么只归一化一层）。
6. **LLM 层**：`app/services/llm/gateway.py`（传输/失败分类/重试一次——业务
   为什么不在这里）→ `prompts.py` → `intent_compiler.py`（Strict JSON Schema +
   语义双检：为什么要双检，Schema 不够吗？）。
7. **API 与运行时**：`app/api/decisions.py`（幂等/信封/状态码）→
   `app/runtime/local_store.py`（内存存储 + 协作式取消；MySQL 版语义在
   `app/repositories/` 已冻结）。
8. **数据与评测**：`eval/synthetic.py`（确定性生成器）→ `eval/golden_queries.json`
   （期望值冻结）→ `eval/baselines.py`（三组基线怎么公平对照）。

## 4. 动手实验清单

每个实验给出命令与"应该看到什么"（前提：`.venv` 就绪，测试见 README 快速开始）：

1. **四分支观察（UI）**：开 `http://127.0.0.1:18230/ui/`，依次点四个预置例句：
   通过（全绿）、缺地点（追问卡片 → 补答 → 恢复）、全城避悬崖（多候选 +
   FAIL 淘汰）、密云露营（风险 UNKNOWN 不进推荐）。
2. **三态语义（preview）**：
   `curl -s -X POST .../decisions/preview -d '{"intent":{"location":"北京·延庆","max_distance_km":8,...}}'`
   ——对照 `checks` 里 PASS/FAIL/UNKNOWN 的 expected→actual 与 `evidence` 引用链。
3. **幂等与冲突**：同一 `Idempotency-Key` 重放同一请求 → 返回原 decision_id（200）；
   同 key 换请求体 → 409/10002。
4. **降级梯**：把 `secrets/llm.env` 里的 key 改错、重启服务，再跑同一 NL 查询
   ——`compile_source` 应回落 `rule_extraction`，报告照常产出（成本记账里出现
   失败记录）。改回来后恢复。
5. **规模与 UNKNOWN 漏斗（ES）**：`.venv/bin/python scripts/es_seed_synthetic.py
   --count 10000`，然后 `SEARCH_PROVIDER=elasticsearch` 启动：候选池从 4 条变
   万条；带距离约束的查询里能看到"数据不全"（UNKNOWN）候选出现在报告而不是消失。
6. **确定性生成器**：跑两次 `generate_routes(10000, seed="synthetic-1")` 比较
   ——逐字段一致；换 seed 则不同（`tests/test_synthetic.py` 已断言）。
7. **黄金测评复现**：`.venv/bin/python scripts/run_eval.py` → 10/10、0 硬约束
   误通过。这个 0 是"UNKNOWN 不通过"语义的直接结果，删掉三态再看会变。

## 5. 分阶段工程知识点（学到什么）

- **阶段 1（确定性闭环）**：三态验证与证据生命周期；幂等键 + 请求指纹冲突检测；
  协作式取消（迟到结果不落报告）；"业务无结果 ≠ 系统故障"（NO_MATCH vs FAILED）。
- **阶段 2（检索与规模，部分完成）**：检索召回语义（超阈值淘汰 vs 缺数据保留）；
  批量取证消除 N+1；"索引即证据源"的简化代价；确定性合成数据的打标与合规。
- **阶段 3（持久化运行时，设计冻结未实现——在 `app/repositories/` 读设计）**：
  事务性 Outbox、多 Worker 租约、阶段级 checkpoint、调用账本对账（PREPARED/UNKNOWN）。
- **阶段 4（模型接入）**：LLM 降级梯；Strict JSON Schema + 语义双检（放宽授权
  必须溯源用户原话）；Run 级成本记账；摘要门禁（不许引入报告外结论）。

## 6. 主张与证据对照

写简历/答辩时的每条主张都要能指到证据：

| 主张 | 证据来源 |
| --- | --- |
| 硬约束 0 误通过（优于基线 A/B 的 4/4） | `eval/results/phase4-baselines-*.json`；`docs/benchmark/benchmark-spec.md` §9–10 |
| 真实 LLM 模式编译字段准确率 21/21 | 同上 §10 + `works_log.md` |
| 限流全挂时 0 误通过保持（降级梯有效） | 同上 §10 限流实测 |
| 万级候选可复现检索测评 | `eval/results/`；`eval/synthetic.py`（seed 确定性） |
| 153 项测试 + ruff 零告警 | CI 本地跑 `.venv/bin/python -m pytest -q` |

## 7. 面试自测题（先想再看括号提示）

1. 为什么 UNKNOWN 的硬约束不通过，而不进人工兜底？（安全边界：兜底=把
   不确定性悄悄变成"算通过"）
2. 为什么放宽要"字段级授权 + 溯源用户原话"？风险字段为什么一票否决？
   （LLM 的"好心"放宽是越权；语义双检在 `intent_compiler._semantic_check`）
3. 幂等键为什么要配合请求指纹？（同 key 异请求必须 409，否则静默错配）
4. 为什么证据过期策略按事实类型分？（距离过期有宽限期可用；风险过期即作废）
5. 基线 B 为什么是"模拟 ReAct"而不是真跑一个 ReAct？（要确定性对照：刻画
   结构性缺陷而非模型质量）
6. 缺索引字段的候选为什么要在检索层保留？（"没查到 ≠ 不满足"；见 D-020）
7. 哪些地方永远不该出现 LLM？（验证判定、证据裁决、幂等/取消/状态机）
8. 如果换成生产 ES+真实数据，第一件事做什么？（证据通道与索引解耦——批量
   Evidence Gateway + 逐事实来源注册）

## 8. 学习项目 vs 生产的诚实边界

- 存储：内存任务存储，进程重启即失（MySQL 模型已冻结、未接线）；
- 数据：全合成/fixture，无真实轨迹与生产数据；
- 鉴权：无登录（`x-user-id` 占位），密钥仅本地；
- 可观测：报告内 budget，无 RED 指标与 Trace；
- ES 证据：索引即证据源的原型简化，动态过期/冲突场景在 Fake 通道覆盖。

这些边界**本身是学习材料**：每一条都对应 roadmap 里一个明确的、带验收标准的待办。
