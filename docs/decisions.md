# 技术决策记录（ADR 索引）

按时间倒序。每条：背景 → 决策 → 理由 → 影响。

## D-019 真实 LLM 密钥只存 secrets/ 目录（不入 git）

- **背景**：阶段 4 接入 Agnes AI（OpenAI 兼容），拿到真实 API Key。
- **决策**：密钥放 `secrets/llm.env`（.gitignore 排除整个目录）；`Settings` 以
  env_file 元组加载（secrets 优先于 .env，进程环境变量最优先）；`secrets/README.md`
  说明轮换方式；`.env.example` 只留占位。
- **理由**：密钥不入版本控制/日志/报错/对外文档是硬边界；分层优先级让本地密钥
  覆盖占位配置而不影响 CI（CI 无 secrets → 自动禁用 LLM）。
- **影响**：Base URL（`https://apihub.agnes-ai.com/v1`）从官方文档确认后写入
  secrets；业务代码与文档只出现占位符。

## D-018 编译层丢弃模型夹带的无害额外字段，领域模型保持 extra=forbid

- **背景**：真实模型输出偶发夹带额外字段（如解释性键），严格 extra=forbid 导致
  整单编译失败 → 频繁落入规则降级，覆盖率下降。
- **决策**：`IntentCompiler._validate` 在校验前把 payload 过滤为
  `RouteIntent.model_fields` 已知键；领域模型对 API 输入仍 extra=forbid。
- **理由**：丢弃的键不可能进入意图，安全性等价于拒绝；可用性显著提升。
  越权语义（relaxation 无授权依据）仍由 `_semantic_check` 拒绝。
- **影响**：真实模式状态一致率从 6/8 提升到 8/8。

## D-017 放宽授权的语义校验：授权必须可溯源到用户原话

- **背景**：模型可能自行"好心"填 relaxation。
- **决策**：`_semantic_check` 要求 payload 中出现 relaxation 时，query 必须包含
  放宽语言标记（"放宽/可以到/接受/没有合适的/适当"），且字段属于可放宽集合；
  风险字段一票否决。应答端点的 `authorize_relaxation` 是第二条授权入口。
- **理由**：未授权不放宽是系统不变量；编译与应答两条入口都必须可溯源。

## D-016 Run 级成本用包装客户端计量（RunCostRecorder）

- **背景**：网关/Fake 客户端是共享实例，其内部账本是进程级，不是 Run 级。
- **决策**：编排器为每个 Run 构造 `RunCostRecorder(共享客户端, item.cost)`；
  成功按 LLMResult 计 token/延迟，失败按 LLMFailure.kind 记零 token 失败记录。
- **影响**：报告 `budget.llm` 反映该 Run 的真实模型成本（调用数/token/延迟/失败原因）。

## D-015 location 允许空串作为"待追问"中间态

- **背景**：编译/规则提取可能产出无地点的部分意图；必填字段使中间态无法表达，
  任务直接 FAILED。
- **决策**：`RouteIntent.location` 允许空串；Completeness Gate 负责强制补齐后才推进。
- **理由**：强制点是门禁而非数据模型——中间态必须可表示才能实现 HITL 追问闭环。
  preview 接口对空地点返回 NO_MATCH + warning（不再 422）。

## D-014 地点归一化匹配（fixture 层）

- **背景**：真实模型输出"北京延庆/怀柔/上海周边"等自然写法，严格子串匹配不到
  fixture 的"北京·延庆"，导致空候选。
- **决策**：FakeProvider 匹配前对两侧做归一化（去 `·`/空格、去一个行政后缀
  市/区/县/省/周边）；评测的地点准确率比较用同样的归一化 + 包含语义。
- **理由**：这是召回层的写法容错，不改变验证语义；真实 ES 适配器用分析器解决同类问题。

## D-013 编排器只从 QUEUED 进入 RUNNING

- **背景**：run() 无条件置 RUNNING 会覆盖排队期间到达的 CANCEL_REQUESTED（真实竞态）。
- **决策**：仅 `status == QUEUED` 时置 RUNNING；终态判定在 finalize 统一处理。
- **影响**：由 `test_cancel_requested_before_finalize` 回归覆盖。

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
