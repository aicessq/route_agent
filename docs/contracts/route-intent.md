# 契约：RouteIntent（route-intent.v1）

自然语言/表单需求的**唯一**中间表示。LLM 或表单只能产出本结构；
任何执行组件不得重新解释用户原文。

## 字段

| 字段 | 类型 | 约束强度 | 说明 |
| --- | --- | --- | --- |
| `location` | string(1..200) | 必填 | 地点名，用于候选召回 |
| `travel_date` | date? | 上下文 | 出行日（天气判定需要） |
| `participants` | object | 上下文 | `children: bool`、`child_ages: int[]`（0–18） |
| `max_distance_km` | float?(0,500] | 硬 | 距离上限 |
| `max_elevation_gain_m` | float?(0,10000] | 硬 | 累计爬升上限 |
| `avoid_risk_types` | RiskType[]≤20 | 硬 | 规避的风险类型枚举 |
| `max_duration_minutes` | float?(0,1440] | 软 | 预计时长偏好 |
| `required_facilities` | FacilityType[]≤20 | 软 | 设施偏好 |
| `min_rating` | float?[0,5] | 软 | 评分偏好 |
| `relaxation_order` | string[]≤8 | 元数据 | 放宽顺序（须已声明） |
| `relaxation` | map<field, RelaxationAuthorization> | 元数据 | 字段级放宽授权 |

### RiskType 枚举

`risk_cliff_exposure` / `risk_river_crossing` / `risk_road_closure` / `risk_landslide` / `risk_ice`

### FacilityType 枚举

`water_source` / `store` / `restroom` / `camp`

### RelaxationAuthorization

```json
{"authorized": true, "max_value": 10.0, "order": 0, "source_text": "可以放宽到10公里"}
```

校验规则：

1. `authorized=true` 必须给出 `max_value`；`max_value` 必须伴随 `authorized=true`。
2. `relaxation_order` 只能引用 `relaxation` 已声明的字段。
3. **`avoid_risk_types` 永不可出现在 relaxation 中**（风险约束不可放宽）。
4. `extra=forbid`：未知字段直接拒绝（防止模型夹带越权字段）。

## 示例

```json
{
  "schema_version": "route-intent.v1",
  "location": "北京·延庆",
  "participants": {"children": true, "child_ages": [8]},
  "max_distance_km": 8,
  "max_elevation_gain_m": 300,
  "avoid_risk_types": ["risk_cliff_exposure"],
  "max_duration_minutes": 120,
  "required_facilities": ["water_source", "store"],
  "relaxation_order": ["max_distance_km"],
  "relaxation": {
    "max_distance_km": {"authorized": true, "max_value": 10.0, "order": 0}
  }
}
```

## 派生语义

- `fingerprint()`：对排序后 JSON 做 SHA-256，作为意图版本指纹；任何约束变化即改变。
  用于报告绑定、缓存键、幂等冲突检测。
- `required_fact_fields()`：返回本次决策需要取证的事实类型集合：
  距离/爬升/风险/时长/设施/评分，仅包含用户实际声明的约束。

## 版本规则

- `schema_version` 必须为 `route-intent.v1`。
- 破坏性变更（字段语义/枚举增删）必须升 `v2` 并保持 v1 消费方兼容期；
  Prompt/编译器变更通过独立 `prompt_version` 标注，不与结构版本耦合。
