# data/ 数据目录说明

本目录存放本地实验数据规则与小型 fixture。**初始化阶段不自动抓取任何外部数据。**

## 目录规划

```
data/
  README.md          # 本文件：数据与许可规则
  seeds/             # 真实种子（阶段 2：公开 GPX / 开放地理数据，附 LICENSE 与来源）
  generated/         # 合成扩增产物（gitignore，生成器版本化）
tests/fixtures/      # 阶段 1 领域测试用小型 fixture（代码内 FakeRouteProvider）
```

## 硬规则

1. **真实种子必须记录来源与许可证**（文件名/ODbL/CC 等），无法确认许可的数据不入仓。
2. **合成数据必须带 `source_kind=synthetic` 标记**，不得冒充真实用户轨迹。
3. `generated/` 不入 git；生成脚本与参数入仓（`generator_version`）。
4. 严禁写入任何 sixfoot 生产数据、内部接口地址或用户内容。
5. 阶段 2 的 10 万级索引构建：真实种子提取分布 → 参数化扩增 → 风险/设施由显式 Fixture 标注
   （GPX 无法自动证明悬崖/补给，缺失即 UNKNOWN）。

## 阶段 2 部分进展（2026-10-12）

- **确定性合成生成器已入仓**：`eval/synthetic.py`（SHA-512 派生，无随机库，同参数逐字段
  一致；10 城 × 100 区县地点池；`source_kind=synthetic_fixture` 三重打标；索引字段缺失率
  8%/8%/6%、风险/设施证据缺失 25%/20% 均为显式设计参数）。
- **灌库脚本已入仓**：`scripts/es_seed_synthetic.py`（重建幂等 + bulk 写入 + 落盘
  ndjson）；10,000 条已灌入本地 ES 索引 `route_agent_routes`。
- `data/generated/` 产物不入 git（生成脚本与参数可随时复现）。
- 风险/设施仍为显式标注（None → UNKNOWN），不从几何数据推断。

## 阶段 1 现状（历史记录）

代码内 FakeRouteProvider fixture（4 条北京路线，含 curated/public_track/synthetic_fixture
三种来源分级与风险证据缺失场景）。Golden Query 测评基于该 fixture，期望值人工冻结。
