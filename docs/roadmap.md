# 路线图（4–6 周，四个纵向阶段）

每阶段交付一条可运行的纵切；削减范围时同步削减对应简历主张。

## 阶段 1 ✅ 确定性决策闭环（本周完成）

- [x] 领域契约：RouteIntent / Evidence / FactPolicy / DecisionReport
- [x] 确定性 Verifier（三态 + 宽限期 + 冲突）
- [x] Fake Provider（4 条 fixture + 故障注入）
- [x] API：preview / 202+轮询 / 幂等 / 取消 / 摘要
- [x] MySQL 表模型 + 仓储 + Alembic 首迁移（SQLite 验证）
- [x] 57 项测试；Golden Query 测评 10/10；ruff 零告警

## 阶段 2 检索链路与数据规模（2 周；部分完成 2026-10-12）

- [ ] 本地 ES 10 万级索引（真实种子 + 合成扩增，许可与生成版本入仓）
      → 进展：10,000 条确定性合成已灌入本地索引（`eval/synthetic.py` + `scripts/es_seed_synthetic.py`）；
        10 万级与真实种子提取未做
- [x] ElasticsearchRouteProvider 接入 + 契约测试
      → search（归一化 + 分词 AND 地点匹配、缺索引字段候选保留进验证层）与
        collect（分批 `_mget`，缺失 → UNAVAILABLE）均已实现；语义抽为纯函数离线可测
- [ ] 统一批量 Evidence Gateway（逐事实状态、批上限、响应体上限）
      → 进展：collect 通道可用，但为"索引即证据源"原型简化；独立网关与响应体上限未做
- [ ] 两级缓存（版本化 key + validUntil）+ singleflight
- [ ] Top-K 漏斗 + 早停；离线全量上界校准最小 K
- [ ] 分层限流背压（用户配额、Provider semaphore、快速拒绝）
- [ ] 压测脚本（1/10/25/50/100 阶梯；冷/热缓存分离报告）
      → 附注：性能消融的数据基建（ES 1 万条）已就绪，缓存/压测基建仍缺

## 阶段 3 持久化运行时（1–1.5 周）

- [ ] API 从内存存储切换到 MySQL 仓储（契约不变）
- [ ] Outbox + Reconciler；多 asyncio Worker 租约领取
- [ ] 调用账本（PREPARED/UNKNOWN 对账、有界重试）
- [ ] 协作式取消 fencing（run_epoch）持久化版
- [ ] 七类故障注入测试进 CI
- [ ] docker compose 全链路（MySQL/Redis/ES + Worker 进程）

## 阶段 4 模型接入与完整评测 ✅（2026-10-09 完成）

- [x] LLM 网关：OpenAI 兼容（Agnes AI `agnes-2.5-flash`）+ 失败分类 + 可重试一次 + Run 级成本记账
- [x] Intent Compiler：小模型 + Strict JSON Schema + 语义双检（放宽授权必须来自用户原话）；
      升级大模型一次 → 规则提取降级梯；编译层丢弃模型夹带的无害额外字段（领域模型仍 extra=forbid）
- [x] 规则驱动 Completeness Gate 追问（缺地点/缺约束，≤2 轮）+ answers 端点以新 intent_version 恢复
- [x] Bounded Repair：五动作（补证据/已授权放宽/换候选/追问/停止）+ 服务端前置校验 +
      进展门（无进展立即停止）+ 确定性策略兜底；LLM 越权提议一律拒绝
- [x] 摘要 LLM 通道（报告外结论门禁 + 模板兜底）
- [x] Golden NL Set（8 条）+ 三组基线对照（固定筛选 / 模拟 ReAct / 本方案）
- [x] CostReport 进报告 budget（token/调用/延迟）
- [x] 真实模式测评：0 硬约束误通过、8/8 状态、21/21 编译字段；端到端 HTTP 验证（编译/追问/摘要）
- [ ] 预注册性能消融实测（依赖阶段 2 的 ES 数据与缓存基建，暂缓）

## 明确不做

- 多 Agent 协商、动态 MCP、自由 SQL/URL 工具
- 生产数据接入与线上部署
- 前端 UI（仅 curl/HTTP 契约）
