# 项目状态速览（STATUS）

> 面向接手本项目的 AI / 新工程师：一页纸掌握实现进度、运行方式与后续方向。
> 基准：main @ `ea60a9c` + 工作区未提交的"阶段 2 部分"改动（见 §5）。
> 更新：2026-10-13，当日复跑 pytest 153 项全绿。深入阅读路径见 [docs/LEARNING.md](LEARNING.md) §3，
> 本文不重复其内容。

## 1. 一句话定位与最高原则

约束验证型户外路线决策 Agent 原型（公开/合成数据、本机实验、不上线）：
自然语言 → `RouteIntent`（LLM 编译 + 规则降级梯）→ 候选召回 → 证据收集 →
**确定性三态验证 PASS / FAIL / UNKNOWN** → 有界修复 → 带证据引用与成本账本的 `DecisionReport`。

最高原则（解释了几乎所有设计）：**判定权不外借**——三态判定、证据裁决、状态机推进
只属于确定性代码；LLM 只做意图编译、修复提议、摘要表达，且输出必须过 Schema + 语义双检。
推论：缺失证据 ≠ 满足（UNKNOWN 的硬约束不通过）；放宽必须用户显式授权且只能落在可放宽字段；
风险约束永不可放宽；"没有符合路线"（NO_MATCH）与"取证不可用"（INSUFFICIENT_EVIDENCE）
是正常业务结论，不是系统异常。

## 2. 阶段进度

| 阶段 | 状态 | 内容 |
| --- | --- | --- |
| 1 确定性决策闭环 | ✅ 已提交 | 领域契约 / 三态验证器 / Fake Provider（4 fixture + 故障注入）/ API（幂等/取消/preview）/ MySQL 表模型 |
| 4 模型接入与完整评测 | ✅ 已提交（2026-10-09） | Agnes LLM 全链路（编译/追问/修复/摘要）+ Golden NL 8 条 + 三组基线对照 |
| 2 检索链路与数据规模 | ◐ 部分完成（**未提交**，2026-10-12） | ES 1 万确定性合成数据 + `ElasticsearchRouteProvider` + 地点归一化 + `/ui` 测试页；缺缓存 / Top-K 漏斗 / 背压 / 压测 |
| 3 持久化运行时 | ○ 设计冻结未实现 | 表模型与仓储在 `app/repositories/`（读设计），Outbox / Worker 未接线 |

关键实测数字（可复现，`eval/results/`）：

- pytest 153 项全绿（2026-10-13 复跑验证），ruff 零告警。
- 阶段 1 黄金查询：10/10，硬约束误通过 0，两次运行结果一致（确定性）。
- 阶段 4（8 条 NL）：基线 A 固定筛选误通过 4、基线 B 模拟 ReAct 误通过 4、本方案 **0**；
  状态一致 8/8；编译字段准确率 21/21；真实 Agnes 模式 12 调用 / 9325 token。
- 限流观察：真实服务连跑触发限流（编译调用全失败）时，降级梯保住 0 误通过与 6/8 状态覆盖
  ——安全底线不依赖模型可用性。

## 3. 代码地图

一次自然语言决策的数据流（改代码前先对着走一遍）：

```
POST /api/v1/decisions (query, Idempotency-Key)
 → app/services/decision_orchestrator.py    编译→门禁→决策→修复→终态 状态机（WAITING_USER 暂停/恢复）
   → app/services/intent_compiler.py         LLM 编译 + Schema/语义双检 + 升级大模型一次 + 规则降级
   → app/services/completeness_gate.py       缺地点/缺约束追问（≤2 轮，answers 端点恢复）
   → app/services/decision_service.py        检索→取证→验证→报告（核心闭环，~100 行）
     → app/providers/{contracts,fake,elasticsearch,location}.py
     → app/domain/verifier.py                三态判定（LLM 禁入）
   → app/services/repair.py                  五动作 + 服务端前置校验 + 进展门 + 确定性兜底
 → app/runtime/local_store.py                内存任务存储（阶段 3 换 MySQL 仓储，契约不变）
 → app/services/summary.py                  LLM 摘要（报告外结论门禁 + 模板兜底）
```

模块速查：

| 位置 | 职责 / 关键点 |
| --- | --- |
| `app/domain/` | `intent`（extra=forbid + 字段级放宽授权挂意图上）、`evidence`（citation=内容哈希 + 有效期）、`policies`（过期策略按事实类型分，风险宽限期 0）、`verifier`（三态合并成 hard_pass）、`report` |
| `app/providers/contracts.py` | `SearchProvider` / `EvidenceProvider` 两个 Protocol，一切检索/取证的接缝 |
| `app/providers/fake.py` | 4 条 fixture + 故障注入（测试一等公民，CI 不依赖网络） |
| `app/providers/elasticsearch.py` | `build_search_body` / `evidence_from_docs` 纯函数（离线可测）+ `ElasticsearchRouteProvider`（search：分词 AND 地点匹配、缺索引字段候选保留进验证层，见 D-020；collect：分批 `_mget`，缺失 → UNAVAILABLE） |
| `app/providers/location.py` | `normalize_location`：城市/区县写法归一化（只归一层，不做多级推断） |
| `app/services/llm/` | `gateway`（OpenAI 兼容 + 失败分类 + 可重试一次重试）、`prompts`、`fake`（FakeLLMClient 脚本化确定性替身） |
| `app/api/decisions.py` | 幂等键 + 请求指纹 409、202+轮询、answers、协作式取消、summary |
| `app/repositories/` + `migrations/` | MySQL 6 表模型 / 仓储 / Outbox（设计冻结未接线） |
| `eval/` | `golden_queries.json`（10 条结构化）、`golden_nl_queries.json`（8 条 NL）、`baselines.py`（A 固定筛选 / B 模拟 ReAct / 独立真值 Oracle）、`synthetic.py`（确定性合成生成器） |
| `scripts/` | `run_eval.py`（阶段 1）、`run_eval_phase4.py`（`--real` 走真实 Agnes）、`es_seed_synthetic.py`（幂等灌库）、`llm_smoke.py` |
| `static/index.html` | `/ui` 本地测试页（自包含，与 API 同源免 CORS；非产品 UI） |

## 4. 本机环境与命令

环境事实（均为本机实测，详见 README 快速开始）：

- Python：`.venv` 必须用 `/Users/zed/anaconda3/bin/python3.13` 创建（系统默认 3.9 缺 `StrEnum`），
  依赖已装齐。
- Docker daemon 平时不运行：`open -a Docker` 后 `docker compose up -d elasticsearch`。
- LLM：Agnes AI `agnes-2.5-flash`，OpenAI 兼容，凭据在 `secrets/llm.env`（gitignore，绝不提交）。
- ES 索引：本地已灌 10,000 条合成路线（`es_seed_synthetic.py --count 10000` 幂等重建，
  生成器 `eval/synthetic.py`，seed `synthetic-1`，10 城 × 10 区 = 100 地点池，
  索引字段缺失率 8%/8%/6%，风险/设施证据缺失率 25%/20%——刻意用于测 UNKNOWN 漏斗）。

```bash
# 测试（无网络依赖）
.venv/bin/python -m pytest -q
# 启动：Fake（默认，无外部依赖）/ ES 两档
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 18230
SEARCH_PROVIDER=elasticsearch .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 18230
# 测评
.venv/bin/python scripts/run_eval.py                # 阶段 1：10 条黄金（确定性）
.venv/bin/python scripts/run_eval_phase4.py         # 阶段 4：8 NL + 三基线（FakeLLM，CI 安全）
.venv/bin/python scripts/run_eval_phase4.py --real  # 真实 Agnes（1.5s/条间隔防限流）
```

测试页：`http://127.0.0.1:18230/ui/`（四个预置例句覆盖通过/追问/FAIL 淘汰/UNKNOWN 四分支）。

## 5. 工作区未提交改动（接手第一件事）

main @ `ea60a9c` 之后的工作区内容 = "阶段 2 部分"（2026-10-12），文档已同步、测试已绿，
**尚未提交**：

- 新增：`app/providers/location.py`、`eval/synthetic.py`、`scripts/es_seed_synthetic.py`、
  `static/`、`tests/test_elasticsearch_provider.py`、`tests/test_synthetic.py`、`docs/LEARNING.md`。
- 修改：README / ARCHITECTURE / roadmap / decisions（新增 D-020~D-022）/ data/README /
  API 与契约文档、`app/main.py`、`app/settings.py`、`app/api/health.py`、
  `app/providers/elasticsearch.py`、`app/providers/fake.py`、`config/app.yaml`。
- **`works_log.md` 尚缺 2026-10-12 条目**——提交时建议补记（可对照 README"阶段 2 部分"小节与本文件）。
- 提交规范：`<type>:<中文描述>`（冒号后无空格）；不得提交 `secrets/`、本地索引、未确认许可数据。

## 6. 后续方向：增大测评规模（已定方向）

现状规模盘点（瓶颈在哪）：

| 资产 | 当前规模 | 备注 |
| --- | --- | --- |
| NL 黄金集 `golden_nl_queries.json` | 8 条 | 每条三件套：`scripted_intent`（FakeLLM 确定性）/ `expected_intent` / `expected_final` |
| 结构化黄金集 `golden_queries.json` | 10 条 | 阶段 1，期望值已冻结 |
| 编译字段核对点 | 21 个 | 8 条 × 平均约 2.6 个字段 |
| 候选数据 | **评测只用 Fake 4 条 fixture** | ES 10k 已灌库但未接入任何测评脚本 |
| 真实 LLM 模式 | 8 条全量 | 1.5s/条间隔；连续重跑会触发限流（已实证） |

放大路径（按优先级，1/2 与 3/4 可并行）：

1. **NL 黄金集扩容（8 → 50–100 条）**。多样性来源：`synthetic.py` 的 100 地点池 +
   约束组合（距离/爬升/时长/风险/设施/年龄）+ 口语变体（"公里 vs km"、口语数字、语序）。
   场景维度至少覆盖：单约束、组合约束、缺地点追问、授权放宽、NO_MATCH、全 UNKNOWN、
   冷字段（时长/设施/年龄，当前 8 条几乎未覆盖）。结构沿用三件套，spec_version 升
   `golden-nl.v2`。注意：Fake fixture 只有 4 条北京路线，地点多样性要靠第 3 步的 ES 通道才真正体现。
2. **期望值半自动生成（降人工成本的关键）**。`expected_intent` 人工写（这是真正的黄金）；
   `expected_final` 用**独立 Oracle 从数据直推**——仿 `eval/baselines.py` 的
   `fixture_ground_truth`（从证据记录直接构造真值，而不是跑被测系统本身），人工抽核约 20% 后冻结。
   预注册原则：期望冻结后才能跑 `--real`；禁止用系统输出反推期望（会把缺陷合法化）。
3. **把测评接到 ES 10k 候选池**。`run_eval_phase4.py` 目前硬编码 `FakeRouteProvider`；
   加 Provider 档位（ES 通道需 Docker + 已灌库，CI 保留 Fake 档）。新增检索层指标：
   候选召回率、UNKNOWN 漏斗占比、缺字段候选保留行为（D-020 的回归测试）。
   合成数据确定性使 Oracle 可由 `synthetic.py` 生成结果直接计算期望。
4. **真实 LLM 模式的规模化策略**。全量确定性门槛用 FakeLLM 跑（零成本、可进 CI）；
   真实模式分层抽样（如 20–30%）+ 限流退避；编译结果缓存避免重复烧 token。
   指标从点值升为分布：编译字段准确率按字段细分、`compile_source` 分布、追问轮数、
   修复动作分布、单条 token/成本。
5. **基线 A/B 同规模重跑**。确定性模拟零成本，只需新数据集的 `query` +
   `scripted_intent`（基线自身走规则提取），直接吃扩容后的黄金集。

约束提醒（扩规模时不放松的底线）：

- 硬约束误通过 = 0 是绝对门槛；`run_eval_phase4.py` 的 gate 结构（summary.gate +
  逐条 FAIL 输出 + JSON 落盘）沿用，保证规模化后单条失败可定位到 `query_id`。
- 合成数据不冒充真实轨迹（AGENTS.md / data/README.md）；新数据集记录生成器版本与 seed，
  产物不入 git、生成器入仓。
- 性能消融（A0–A3，benchmark-spec §4）是另一条线，依赖缓存/压测基建（roadmap 阶段 2 未完项），
  与本节"决策质量规模测评"分开推进，不要混在同一份报告里。

## 7. 诚实边界（学习项目 vs 生产）

存储为内存态（进程重启即失；MySQL 模型已冻结未接线）、数据全合成/fixture、无鉴权
（`x-user-id` 占位）、无 RED 指标与 Trace、ES 通道为"索引即证据源"的原型简化。
逐条边界及对应待办见 [docs/LEARNING.md](LEARNING.md) §8。
