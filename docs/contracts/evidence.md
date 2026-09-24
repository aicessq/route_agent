# 契约：Evidence（证据记录）

所有进入验证链路的事实只有一种结构。**未在本契约中声明的数据不得参与判定。**

## EvidenceRecord 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `evidence_id` | string | 证据实例 ID（同批次内唯一） |
| `route_id` | string | 归属路线 |
| `fact_type` | FactType | `distance_km / elevation_gain_m / duration_minutes / risk_types / facilities / rating / weather` |
| `status` | FactStatus | 见下表 |
| `raw_value` / `normalized_value` | any | 原始值与归一化值（数值带单位归一） |
| `unit` | string? | `km / m / minutes / stars / enum[]` |
| `source` | string | 来源标识（provider 名 + 通道） |
| `source_kind` | string | `public_track / curated_route / synthetic_fixture / ...` |
| `observed_at` | datetime | 观测时间 |
| `valid_until` | datetime? | 有效期（静态事实可为 null） |
| `entity_revision` | string? | 路线实体修订版本 |
| `source_fingerprint` | string? | 来源指纹（幂等/缓存键） |
| `citation_id` | 派生 | `E-<hash10>`，报告引用的稳定标识 |

## FactStatus 状态机

| 状态 | 含义 | 进入验证 |
| --- | --- | --- |
| `OK` | 在有效期内且无冲突 | 可用 |
| `STALE` | 已过 `valid_until` | 仅静态事实在宽限期内可用（check 标记 stale） |
| `CONFLICT` | 多来源不可消解的分歧 | 不可用 → check UNKNOWN |
| `UNAVAILABLE` | 依赖故障（超时/熔断） | 不可用 → check UNKNOWN |
| `NOT_FOUND` | 明确无该数据 | 不可用 → check UNKNOWN |

## 有效期与宽限期（FactPolicy）

| 事实 | criticality | stale 宽限 | 说明 |
| --- | --- | --- | --- |
| distance_km / elevation_gain_m | hard | 3600s | 静态统计 |
| risk_types | hard | **0** | 动态风险，过期即 UNKNOWN |
| duration_minutes | soft | 3600s | — |
| facilities | soft | 900s | — |
| rating | soft | 86400s | — |
| weather | context | 0 | 仅触发重新验证 |

判定规则：

1. `OK` 且未过期 → 可用。
2. `STALE` 且策略允许 → 距过期时长 ≤ 宽限期才可用，check 的 `stale=true` 并写入 tradeoffs。
3. 其余（含超出宽限期的 STALE）→ `UNKNOWN`，**不因数据缺失而默认满足**。

## 冲突语义（字段级策略，阶段 2 完整实现）

同一事实多来源不一致时：容差内取主源；超出容差 → `CONFLICT` → `UNKNOWN`。
硬约束事实冲突时**不得**择一通过。当前实现：显式注入 CONFLICT 即 UNKNOWN。

## 来源可信度

来源标志作用于**具体事实及其用途**，不给整条路线盖"可信"章：

- 真实轨迹统计 → 支持"有人记录过此路径"与距离/爬升。
- 合成海拔 → 只支持估计，不支撑临界值判定（阈值贴近时优先 UNKNOWN）。
- 用户评价 → 软排序与风险线索，不可成为安全事实。
