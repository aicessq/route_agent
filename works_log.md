# works_log

开发日志（按日期，倒序）。

## 2026-09-25 阶段 1 完成

- 初始化工程基线：pyproject / docker-compose / config/app.yaml / AGENTS.md / .env.example。
- 领域层：RouteIntent（字段级放宽授权 + 指纹）、EvidenceRecord（五状态 + citation）、
  FactPolicy 注册表、RouteVerifier（三态 + 宽限期 + 冲突）、DecisionReport。
- Provider 层：Protocol 契约、FakeRouteProvider（4 条 fixture + 故障注入）、ES 适配器骨架。
- 服务与 API：DecisionService 闭环、preview/202+轮询/取消/摘要端点、幂等键冲突检测。
- 持久化契约：6 张表 SQLAlchemy 模型 + 仓储（租约/Outbox/证据批量 upsert）+ Alembic 首迁移。
- 测试：57 项全绿（领域/服务/API 契约/仓储/取消语义）。
- 修复记录：
  - FastAPI 忘传 lifespan 导致 state 未初始化 → 补 `lifespan=`。
  - Alembic `**dict` 把 Column 传成关键字参数产生 SAWarning → 改位置展开。
  - 迁移文件 `**` 解包后跟位置参数语法错误 → 调整顺序。
  - verifier 宽限期从布尔改为按 `now - valid_until` 时长判定。
  - 无可验证约束改为 INSUFFICIENT_EVIDENCE（空洞 hard_pass 会误导）。
- Golden Query 测评：10 条，全过；硬约束误通过数 0；两次运行 diff 一致（确定性）。
  - 期间修正了 2 个我方期望数据的 offset 错误（未基于 +6h 基准计算）。
- 验证：pytest 57 绿 / ruff 零告警 / compileall 通过 / uvicorn 启动 + curl 全端点 /
  alembic 升降级 / 幂等 202/200/409 / 协作式取消。

## 2026-10-09 阶段 4 完成（模型接入与完整评测）

- LLM 网关（`app/services/llm/`）：OpenAI 兼容 chat/completions + Bearer 鉴权；
  失败分类（auth/rate_limit/server/network/schema/empty）+ 可重试失败重试一次；
  FakeLLMClient 脚本化测试；RunCostRecorder 按 Run 包装共享客户端记账。
- 真实服务接入：Agnes AI `agnes-2.5-flash`（Base URL 从官方文档确认：
  `https://apihub.agnes-ai.com/v1`）；密钥只存 `secrets/llm.env`（gitignore，
  优先级 高于 .env），业务代码零硬编码；`scripts/llm_smoke.py` 冒烟验证。
- Intent Compiler：主模型 → JSON 容错提取 + null 剥离 + 未知字段丢弃 → RouteIntent 校验
  → 语义校验（放宽授权必须有用户原话依据，只能落在可放宽字段）→ 失败升级大模型一次
  → 规则提取降级（正则：距离/爬升/时长/风险/设施/年龄/城市）。编译失败原因进 detail 观测。
- Completeness Gate：规则追问（缺 location / 无可验证约束），每 Run ≤2 轮；
  `POST /decisions/{id}/answers` 合并应答（含 `authorize_relaxation` 授权放宽）→
  新 intent_version 重新驱动。
- Bounded Repair：五动作枚举 + 服务端前置校验（LLM 提议同样过校验，越权即拒）
  + 进展门（verified↑/unknown↓/candidates↑，无进展立即停止）+ 确定性策略兜底。
- 摘要：LLM 优先（报告外路线标题门禁 + 600 字上限 + 长度校验），失败回落模板；
  `channel` 字段透出实际通道。
- 编排器重写：编译→门禁→决策→修复→终态；WAITING_USER 暂停/恢复；
  修复取消竞态（run() 仅从 QUEUED 进入 RUNNING，排队期取消不被覆盖）。
- 测评：
  - Golden NL（8 条）+ 三组基线（`scripts/run_eval_phase4.py`）：
    fake 模式 8/8、21/21 字段、A/B 基线各 4 次硬约束误通过、本方案 0；
    real 模式（加 1.5s 间隔防限流）8/8、21/21、0 误通过、12 调用/9325 token。
  - 限流观察：连续重跑触发真实服务限流（10 调用全失败、0 token）时，
    降级梯保住 0 误通过与 6/8 状态覆盖——安全底线不依赖模型可用性。
- 端到端真实 HTTP 验证：query 创建（真实编译）→ SUCCEEDED 三态报告 →
  LLM 摘要（channel=llm，数值转述准确）→ 无地点追问 → answers → SUCCEEDED。
- 修复记录（阶段 4 期间）：
  - `_semantic_check` 重复定义（旧版覆盖新版）→ 删除；
  - RouteIntent.location 必填导致"待追问中间态"无法表达 → 允许空串，门禁强制补齐；
  - Run 成本记账初始为 0（共享 client 账本非 Run 级）→ RunCostRecorder 包装；
  - Fake Provider 地点严格子串匹配导致真实编译地点（"北京延庆"）匹配不到
    "北京·延庆" → 归一化匹配（去分隔符 + 行政后缀）；
  - lint 修复误合并 RELAX 校验外层 if 破坏授权检查 → 恢复结构（由测试拦截）。
- 回归：137 测试全绿、ruff 零告警、阶段 1/4 评测全过、alembic 零告警、uvicorn 启动验证。
