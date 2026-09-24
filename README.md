# sixfoot_route_agent

约束验证型户外路线决策 Agent（公开数据原型）。

面向"带 8 岁孩子、8 公里以内、爬升不超过 300 米、有补给点、避开悬崖"这类复合出行需求，
把自然语言/表单约束编译为结构化 `RouteIntent`，经过候选召回、证据收集、
**确定性约束验证（PASS / FAIL / UNKNOWN）**，输出带证据引用的结构化 `DecisionReport`。

> 定位：公司正式安排的实验性任务，在本机使用公开/合成数据完成可复现原型与测评。
> 未接入生产数据、生产流量或真实用户；所有结果均为本机实验结论。

## 核心设计原则

1. **硬约束判定全部由确定性代码完成**，LLM 不参与本阶段，未来也只能做意图编译与表达。
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

# 4) 试一次同步预览
curl -s -X POST http://127.0.0.1:18230/api/v1/decisions/preview \
  -H 'Content-Type: application/json' \
  -d '{"intent":{"location":"北京·延庆","max_distance_km":8,"max_elevation_gain_m":300,"avoid_risk_types":["risk_cliff_exposure"]}}'

# 5) Golden Query 测评
.venv/bin/python scripts/run_eval.py
```

可选本地依赖（MySQL/Redis/ES，用于阶段 2/3）：`docker compose up -d`。

## 接口一览

| 端点 | 说明 |
| --- | --- |
| `GET /health` | 健康检查 |
| `POST /api/v1/decisions/preview` | 同步确定性验证，直接返回结构化报告 |
| `POST /api/v1/decisions` | 创建异步决策任务（202 + decisionId，需 Idempotency-Key） |
| `GET /api/v1/decisions/{id}` | 轮询任务状态与报告 |
| `DELETE /api/v1/decisions/{id}` | 协作式取消 |
| `POST /api/v1/decisions/{id}/summary` | 按需生成模板摘要 |

详见 [docs/api/decisions-api.md](docs/api/decisions-api.md)。

## 目录结构

```
app/
  domain/        # 领域契约：RouteIntent / Evidence / Verifier / Report / FactPolicy
  providers/     # SearchProvider / EvidenceProvider 协议 + Fake 与 ES 适配器
  services/      # DecisionService（检索→取证→验证→报告）与模板摘要
  api/           # FastAPI 路由与响应信封
  runtime/       # Run 状态机、内存任务存储（阶段 3 替换为 MySQL+Outbox+Worker）
  repositories/  # SQLAlchemy 模型与仓储（决策/运行/步骤/证据/调用账本/Outbox）
  schemas/       # API 严格 DTO
migrations/      # Alembic 首迁移
tests/           # 57 项测试：领域层 / 服务 / API 契约 / 仓储 / 取消语义
eval/            # Golden Query 测评集与结果
scripts/         # run_eval.py 测评入口
docs/            # 架构 / 设计冻结 / 契约 / API / 基准 / 路线图
```

## 当前状态（阶段 1 已完成）

- 确定性验证闭环：Fake Provider → 决策服务 → 三态报告 → API 全链路跑通。
- 57 项 pytest 全绿；ruff 零告警；Alembic 迁移在 SQLite 上验证升降级。
- Golden Query 测评 10/10 通过，硬约束误通过数为 **0**（详见 [eval/results/latest.json](eval/results/latest.json)）。
- 异步任务骨架：202 + 轮询 + 幂等键 + 协作式取消（内存实现，MySQL 持久化为阶段 3）。

阶段 2–4（ES 10 万级索引、批量 Evidence Gateway、两级缓存、Outbox/Worker/故障注入、
LLM 意图编译与双层评测）见 [docs/roadmap.md](docs/roadmap.md)。

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

- 本项目不接入 sixfoot 生产服务与数据库；跨服务访问一律经版本化 Adapter。
- 公开数据与合成数据严格标记来源，合成样本不冒充真实轨迹。
- 性能与质量数字只来自本机实测（见 eval/results 与 docs/benchmark），不外推为生产指标。
