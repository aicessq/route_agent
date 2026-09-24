# 路线图（4–6 周，四个纵向阶段）

每阶段交付一条可运行的纵切；削减范围时同步削减对应简历主张。

## 阶段 1 ✅ 确定性决策闭环（本周完成）

- [x] 领域契约：RouteIntent / Evidence / FactPolicy / DecisionReport
- [x] 确定性 Verifier（三态 + 宽限期 + 冲突）
- [x] Fake Provider（4 条 fixture + 故障注入）
- [x] API：preview / 202+轮询 / 幂等 / 取消 / 摘要
- [x] MySQL 表模型 + 仓储 + Alembic 首迁移（SQLite 验证）
- [x] 57 项测试；Golden Query 测评 10/10；ruff 零告警

## 阶段 2 检索链路与数据规模（2 周）

- [ ] 本地 ES 10 万级索引（真实种子 + 合成扩增，许可与生成版本入仓）
- [ ] ElasticsearchRouteProvider.search 接入 + 契约测试
- [ ] 统一批量 Evidence Gateway（逐事实状态、批上限、响应体上限）
- [ ] 两级缓存（版本化 key + validUntil）+ singleflight
- [ ] Top-K 漏斗 + 早停；离线全量上界校准最小 K
- [ ] 分层限流背压（用户配额、Provider semaphore、快速拒绝）
- [ ] 压测脚本（1/10/25/50/100 阶梯；冷/热缓存分离报告）

## 阶段 3 持久化运行时（1–1.5 周）

- [ ] API 从内存存储切换到 MySQL 仓储（契约不变）
- [ ] Outbox + Reconciler；多 asyncio Worker 租约领取
- [ ] 调用账本（PREPARED/UNKNOWN 对账、有界重试）
- [ ] 协作式取消 fencing（run_epoch）持久化版
- [ ] 七类故障注入测试进 CI
- [ ] docker compose 全链路（MySQL/Redis/ES + Worker 进程）

## 阶段 4 模型接入与完整评测（1–1.5 周）

- [ ] Intent Compiler（小模型 + Strict JSON Schema + 语义双检；升级大模型一次）
- [ ] 规则驱动的 Completeness Gate 追问（≤2 轮）
- [ ] Bounded Repair（补证据/换候选/已授权放宽/追问/停止；进展度量）
- [ ] 摘要 LLM 可选通道（模板兜底）
- [ ] Golden Set + 三组基线对照（固定筛选 / 普通 ReAct / 本方案）
- [ ] 预注册性能消融实测 + CostReport；结果填入 docs 与简历（只写实测）

## 明确不做

- 多 Agent 协商、动态 MCP、自由 SQL/URL 工具
- 生产数据接入与线上部署
- 前端 UI（仅 curl/HTTP 契约）
