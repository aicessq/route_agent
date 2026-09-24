# 技术决策记录（ADR 索引）

按时间倒序。每条：背景 → 决策 → 理由 → 影响。

## D-001 采用 Python FastAPI + asyncio，而非 Rust/Temporal

- **背景**：参考项目为 Rust/Tauri 通用 Agent Runtime；本项目瓶颈在远程数据获取等待。
- **决策**：Python 3.11+ / FastAPI / SQLAlchemy async / asyncio Worker；CPU 热点压测确认后才下沉原生扩展。
- **理由**：交付速度、sixfoot 生态一致、可测试性；语言本身不作为性能证据。
- **影响**：性能主张必须来自漏斗/批量/缓存/早停的消融实测。

## D-002 领域契约先行（domain 层为唯一语义来源）

- **决策**：`app/domain/` 的 Intent/Evidence/Policy/Verifier 是全链路唯一语义；
  Provider、API、仓储都依赖它而不是互相依赖。
- **理由**：换 Provider / 换存储不改判定语义；测试面小。
- **影响**：阶段 2–4 的所有扩展只能在外围加模块。

## D-003 硬约束 UNKNOWN 不通过（三态而非布尔）

- **决策**：`PASS/FAIL/UNKNOWN`；`hard_pass` 要求全 PASS 且无 UNKNOWN。
- **理由**："没查到悬崖记录"≠"没有悬崖"；依赖故障不伪造满足。
- **影响**：密云类证据缺失候选永远进不了已验证推荐；这是可辩护的安全边界。

## D-004 证据 freshness 按事实分类，而非全局 TTL

- **决策**：静态事实（距离/爬升）宽限 3600s；动态风险宽限 0；STALE 必须在 check 与 tradeoffs 标记。
- **理由**：可用性与安全语义不同；不能让过期天气支持硬约束。

## D-005 内存任务存储先行，MySQL 契约同步冻结

- **决策**：阶段 1 API 用 `InMemoryDecisionStore`；表模型/仓储/Alembic 已实现并用 SQLite 测试。
- **理由**：先跑通语义闭环，持久化接线时不改对外契约。
- **风险声明**：当前无进程级恢复能力，不宣称"可恢复 Runtime"（阶段 3 交付）。

## D-006 幂等键显式化 + 请求指纹冲突检测

- **决策**：`user_key + Idempotency-Key`，指纹不一致返回 409；不做服务端自然语言自动去重。
- **理由**：避免网络重试重复计费；自动去重会误合并相似但不同的需求。

## D-007 摘要懒生成 + 模板先行

- **决策**：报告先完成；摘要按需生成并绑定报告指纹；阶段 1 用确定性模板。
- **理由**：正常 Run 固定模型成本压到一次 Intent 编译；模板在 LLM 不可用时兜底。

## D-008 无可验证约束 → 不做"已验证推荐"

- **决策**：`required_fact_fields()` 为空时返回 `INSUFFICIENT_EVIDENCE` + warning。
- **理由**：空约束下 "hard_pass=true" 是空洞真值，会误导用户以为经过验证；这类需求应走普通搜索入口。

## D-009 Fake Provider 支持故障注入而非 mock 框架

- **决策**：`fail_search / fail_evidence / delay` + 证据覆盖（CONFLICT/过期）直接进 Fake。
- **理由**：故障场景是第一等测试需求；场景可进 Golden Query 与 CI。

## D-010 配置三层：默认值 < config/app.yaml < 环境变量/.env

- **决策**：pydantic-settings + 显式 yaml 拍平合并；密钥只进环境变量。
- **理由**：结构配置可评审、密钥不入库；与 sixfoot_upload_service 模式一致。
