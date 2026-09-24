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
