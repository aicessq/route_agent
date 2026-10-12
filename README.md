# route_agent

约束验证型户外路线决策 Agent（公开数据原型）。

面向"带 8 岁孩子、8 公里以内、爬升不超过 300 米、有补给点、避开悬崖"这类复合出行需求，
把自然语言编译为结构化 `RouteIntent`（LLM 编译 + 规则降级梯），经过候选召回、证据收集、
**确定性约束验证（PASS / FAIL / UNKNOWN）**、有界修复与按需追问，
输出带证据引用与成本账本的结构化 `DecisionReport`。

> 定位：公司正式安排的实验性任务，在本机使用公开/合成数据完成可复现原型与测评。
> 未接入生产数据、生产流量或真实用户；所有结果均为本机实验结论。

## 核心设计原则

1. **硬约束判定全部由确定性代码完成**；LLM 只做意图编译、修复提议与摘要表达，且输出必须通过 Schema + 语义双检。
2. **证据缺失 ≠ 满足**：缺少风险证据的路线标记 `UNKNOWN`，不进入已验证推荐。
3. **放宽必须授权**：软约束放宽需要用户在 Intent 中显式授权字段与上限；风险约束永不可放宽。
4. **"没有符合路线"是正常业务结论**（`NO_MATCH`），不是系统异常。
5. 依赖故障（取证不可用）返回 `INSUFFICIENT_EVIDENCE`，不伪造验证结果。

## 快速开始

```bash
# 1) 创建虚拟环境并安装依赖
python3 -m venv .venv
.venv/bin/pip install "fastapi>=0.115" "pydantic>=2.8" "pydantic-settings>=2.4" \
  "pyyaml>=6.0" "sqlalchemy[asyncio]>=2.0.35" "alembic>=1.13" "uvicorn>=0.30" \
  "redis>=5.0" "httpx>=0.27"
.venv/bin/pip install "pytest>=8.3" "pytest-asyncio>=0.24" "ruff>=0.6" "aiosqlite>=0.20"

# 2) 跑测试
.venv/bin/python -m pytest -q

# 3) 启动服务（Fake Provider 驱动，无需外部依赖）
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 18230

# 4) 试一次自然语言决策（未配置模型时走规则编译，配置后走真实 LLM）
curl -s -X POST http://127.0.0.1:18230/api/v1/decisions \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: demo-key-000001' \
  -d '{"query":"周六带8岁孩子去北京延庆徒步，8公里以内，爬升不超过300米，避开悬崖"}'

# 5) 真实模型接入（Agnes AI，OpenAI 兼容）：密钥只放本地 secrets/，不入 git
cp -n secrets/README.md . 2>/dev/null; mkdir -p secrets
cat > secrets/llm.env <<'EOF2'
LLM_BASE_URL=<从官方文档确认>
LLM_API_KEY=<你的密钥>
LLM_MODEL=agnes-2.5-flash
EOF2
.venv/bin/python scripts/llm_smoke.py --intent   # 真实服务冒烟

# 6) 测评
.venv/bin/python scripts/run_eval.py               # 阶段1：确定性黄金查询
.venv/bin/python scripts/run_eval_phase4.py        # 阶段4：NL 编译 + 三组基线（FakeLLM）
.venv/bin/python scripts/run_eval_phase4.py --real # 同上，真实 Agnes 编译
```

可选本地依赖（MySQL/Redis/ES，用于阶段 2/3）：`docker compose up -d`。

## 接口一览

| 端点 | 说明 |
| --- | --- |
| `GET /health` | 健康检查 |
| `POST /api/v1/decisions/preview` | 同步确定性验证，直接返回结构化报告 |
| `POST /api/v1/decisions` | 创建异步决策任务（`query` 自然语言 / `intent` 结构化二选一；202 + decisionId，需 Idempotency-Key） |
| `GET /api/v1/decisions/{id}` | 轮询任务状态、当前步骤、追问与报告 |
| `POST /api/v1/decisions/{id}/answers` | 回答追问 / 授权放宽后以新意图版本继续 |
| `DELETE /api/v1/decisions/{id}` | 协作式取消 |
| `POST /api/v1/decisions/{id}/summary` | 按需摘要（LLM 优先，模板兜底，报告外结论门禁） |

详见 [docs/api/decisions-api.md](docs/api/decisions-api.md)。

## 目录结构

```
app/
  domain/        # 领域契约：RouteIntent / Evidence / Verifier / Report / FactPolicy
  providers/     # SearchProvider / EvidenceProvider 协议 + Fake 与 ES 适配器
  services/      # DecisionService / IntentCompiler / CompletenessGate / Repair / Orchestrator / 摘要
  services/llm/  # LLM 网关（OpenAI 兼容）+ Fake 客户端 + Prompt v1 + Run 成本记账
  api/           # FastAPI 路由与响应信封
  runtime/       # Run 状态机、内存任务存储（阶段 3 替换为 MySQL+Outbox+Worker）
  repositories/  # SQLAlchemy 模型与仓储（决策/运行/步骤/证据/调用账本/Outbox）
  schemas/       # API 严格 DTO
migrations/      # Alembic 首迁移
tests/           # 137 项测试：领域层 / 编译器 / 门禁 / 修复 / 编排 / API / 仓储
eval/            # 黄金查询集 + NL 集 + 三组基线实现与结果
scripts/         # run_eval.py / run_eval_phase4.py / llm_smoke.py
secrets/         # 本地密钥（gitignore；含真实 Agnes API Key，绝不提交）
docs/            # 架构 / 设计冻结 / 契约 / API / 基准 / 路线图
```

## 当前状态（阶段 1 + 阶段 4 已完成）

- 确定性验证闭环：Provider → 决策服务 → 三态报告 → API 全链路跑通。
- **阶段 4：真实 LLM 全链路**（Agnes AI `agnes-2.5-flash`，OpenAI 兼容）：
  - Intent Compiler：小模型编译 + Schema/语义双检 + 升级大模型一次 + 规则提取降级梯；
  - Completeness Gate：规则追问（缺地点/缺约束，≤2 轮）+ `answers` 端点以新意图版本恢复；
  - 有界 Repair：五个枚举动作（补证据/已授权放宽/换候选/追问/停止）+ 服务端前置校验 +
    进展门 + 确定性策略兜底；LLM 越权提议一律拒绝；
  - LLM 摘要通道：报告外结论门禁 + 模板兜底；Run 级 CostReport（token/调用/延迟）进报告 budget。
- 137 项 pytest 全绿；ruff 零告警。
- 测评（均含"硬约束误通过必须为 0"门槛）：
  - 阶段 1 黄金查询：10/10，0 误通过，确定性复现。
  - 阶段 4 三组基线（FakeLLM 确定性）：固定筛选误通过 4、模拟 ReAct 误通过 4、**本方案 0**，8/8。
  - 阶段 4 真实 Agnes 模式：**0 误通过**，状态 8/8，编译字段准确率 21/21（含 1 条规则降级仍走通追问闭环）。
  - 限流观察：真实服务连跑会触发限流，全失败时降级梯保住 0 误通过与 6/8 覆盖（见 works_log）。
- 异步任务骨架：202 + 轮询 + 幂等键 + 协作式取消（内存实现，MySQL 持久化为阶段 3）。

阶段 2/3（ES 10 万级索引、批量 Evidence Gateway、两级缓存、Outbox/Worker/故障注入）见 [docs/roadmap.md](docs/roadmap.md)。

## 文档索引

- 架构：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 设计冻结：[docs/design/2026-09-25-route-decision-agent-design.md](docs/design/2026-09-25-route-decision-agent-design.md)
- 契约：[docs/contracts/](docs/contracts/)（route-intent / evidence / decision-report / tool-provider）
- API：[docs/api/decisions-api.md](docs/api/decisions-api.md)
- 基准规范：[docs/benchmark/benchmark-spec.md](docs/benchmark/benchmark-spec.md)
- 技术决策记录：[docs/decisions.md](docs/decisions.md)
- 路线图：[docs/roadmap.md](docs/roadmap.md)
- 数据与许可：[data/README.md](data/README.md)

## 边界声明

- 本项目不接入任何生产服务与业务数据库；跨服务访问一律经版本化 Adapter。
- 公开数据与合成数据严格标记来源，合成样本不冒充真实轨迹。
- 性能与质量数字只来自本机实测（见 eval/results 与 docs/benchmark），不外推为生产指标。
