# 契约：Tool Provider（静态版本化目录）

私有工具能力目录。**静态注册、版本化声明**；不做动态 MCP 发现。
Planner（阶段 4）面向"缺少什么事实"规划，不面向内部工具名；
内部接口迁移/降级不进入模型契约。

## Protocol 定义（`app/providers/contracts.py`）

```python
class SearchProvider(Protocol):
    async def search(self, intent: RouteIntent, *, limit: int) -> list[RouteCandidate]: ...


class EvidenceProvider(Protocol):
    async def collect(
        self, route_ids: Sequence[str], fact_types: Sequence[FactType]
    ) -> list[EvidenceRecord]: ...
```

## FactProviderMetadata（目录条目）

| 字段 | 说明 |
| --- | --- |
| `name / version` | 提供者标识与契约版本 |
| `fact_types` | 支持的事实类型集合 |
| `read_only` | 恒为 true（V1 严格只读） |
| `max_batch_size` | 单批最大候选数 |
| `timeout_seconds` | 调用硬超时 |

## 批量语义（阶段 2 完整落地）

- 请求：`route_ids[] + fact_types[]`，消除 N+1 扇出。
- 响应逐路线、逐事实携带状态：`OK / STALE / CONFLICT / UNAVAILABLE / NOT_FOUND`；
  **单条失败不使整批失败**。
- 响应体与批大小有硬上限（`evidence_batch_size / max_response_bytes`）。

## 错误分类

| 类别 | 处理 |
| --- | --- |
| `timeout` | 计入调用账本；有界重试后 UNAVAILABLE |
| `rate_limited` | 退避；配额耗尽 → 走降级矩阵 |
| `unavailable` | 熔断窗口内不再发起；事实缺失 → UNKNOWN |
| `invalid_response` | 不重试（契约校验失败）；记录 schema 版本 |

调用账本状态机：`PREPARED → RUNNING → SUCCEEDED / FAILED / UNKNOWN`。
`UNKNOWN`（发出后崩溃）在恢复时先对账，不无界重放。

## 现有实现

| 提供者 | 状态 | 说明 |
| --- | --- | --- |
| `FakeRouteProvider` | ✅ | 确定性 fixture；支持 `fail_search / fail_evidence / delay` 故障注入 |
| `ElasticsearchRouteProvider` | ✅（原型简化） | search：归一化 + 分词 AND 地点匹配、缺索引字段候选保留（UNKNOWN 进验证层）；collect：分批 `_mget`，字段缺失 → `UNAVAILABLE`。当前"索引即证据源"，独立批量网关与响应体上限仍是阶段 2 待办 |
| 内部服务 Adapter（sixfoot-map-api / Search V2 / 行程） | 阶段 2+ | 实现同一 Evidence 契约 |

## 安全边界

- Provider 只接受类型化参数，不接受模型生成的 SQL、ES DSL 或任意 URL。
- 下游凭据为只读 M2M；用户 Bearer 不透传（网关鉴权 + 签名任务声明）。
- 评论文本等 UGC 只作为数据字段进入，不可被解释为工具指令。
