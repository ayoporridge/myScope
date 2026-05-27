# MyScope 改进规格说明

> 基于系统性分析，整理出的具体改进方向和实施建议。
> 用途：喂给 AI coding agent，指导其优化 MyScope 系统。

---

## 一、系统组件价值分类

### A 类：直接创造"上下文差异化"

这些组件直接让 AI 知道"你是谁"，是系统的核心价值所在。

| 组件 | 核心价值 |
|------|---------|
| `hippocampus_formation.py` | 从 AI 对话里提取你的决策、偏好、项目背景 |
| `hippocampus_recall.py` | 把"你"召回到对话里 |
| `layer1_rag.py` | 微信收藏 + Obsidian = 你主动留下的判断和笔记 |
| `dayflow_daily_summary.py` | 你昨天实际做了什么，构建时间轴叙述 |
| `memory_mcp_server.py` | 唯一连接记忆系统与 Claude 的接口 |

### B 类：支撑管道

这些让 A 类能运转，但本身不直接创造差异化。

- FreshRSS、RSSHub、`layer3_index.py`、`layer3_wechat.py`
- `layer2_wiki.py`（放大 Layer 1，但非核心）
- `dayflow_sync.py`（原始数据，A 类是基于它的日摘）
- Meilisearch、memory-api、Anda 云服务、launchd 任务

**关键发现**：整个系统里数据量最大、管道最复杂的部分（Layer 3 哈勃半径：110+ 公众号 + 18 播客）实际上是最弱的 A 类。它存的是外部内容，而非"你"本人。最不可替代的反而是 `hippocampus_formation.py`——一个读 JSONL 文件、调一个 API 的脚本。

---

## 二、当前系统的三个核心问题

### 问题 1：静默失败——脚本挂了你不知道

7 个脚本全在凌晨静默运行。成功不报告，失败也不报告。唯一的"监控"是偶尔发现"AI 好像不记得我了"——距故障发生可能已过数天。

### 问题 2：质量没有保障——Anda 在学噪音

现有代码只过滤了两种系统 prompt 开头（`<permissions`、`<environment_context`），但系统注入内容有十几种形态。HTTP 200 ≠ 成功写入了有价值的内容。

### 问题 3：没有趋势感知——不知道系统在变好还是变差

有日志（stdout 写到 launchd），但没有指标。无法回答："这个系统上周比这周好还是差？"

---

## 三、具体改进建议

### 改进 1：有效消息过滤器（优先级：高）

**位置**：`hippocampus_formation.py`，在 `post_formation()` 前加过滤

**有效消息判断标准**（同时满足以下条件）：

```python
def is_meaningful(msg: dict) -> bool:
    text = msg.get("content", "")
    role = msg.get("role", "")
    
    # 1. 长度在合理范围（太短是工具调用，太长是系统 prompt）
    if not (20 <= len(text) <= 800):
        return False
    
    # 2. 不以 XML 结构开头（系统注入）
    if text.strip().startswith("<"):
        return False
    
    # 3. 不包含大段 JSON 结构（工具定义/调用结果）
    if text.count("{") > 5 and text.count("}") > 5:
        return False
    
    # 4. user 消息：必须有实质内容
    if role == "user" and len(text) < 10:
        return False
    
    return True
```

**告警阈值**：每次 formation 运行后，若有效消息数 < 5 条，视为无效运行，触发告警。

---

### 改进 2：三层告警机制（优先级：高）

新建 `scripts/health_check.py`，每天 **06:30** 运行（formation 结束后），通过飞书机器人推送告警。

**需检查的三件事：**

#### 层级 1：存活性检查（脚本有没有跑完）

检查每个脚本的最后运行时间，超过 25 小时未运行视为异常：

```
检查项：
- layer3_wechat.py  上次运行时间 vs 现在
- dayflow_sync.py   上次运行时间 vs 现在
- layer1_rag.py     上次运行时间 vs 现在
- layer2_wiki.py    上次运行时间 vs 现在
- hippocampus_formation.py 上次运行时间 vs 现在
```

实现方式：每个脚本运行结束时，向 `logs/last_run.json` 写入时间戳。health_check.py 读这个文件。

#### 层级 2：质量性检查（写进去的内容有没有信息量）

从 `logs/metrics.jsonl` 读昨日数据：
- hippocampus formation 有效消息数 < 5 → 告警
- memory_chunks 新增 < 3 条 → 告警
- wiki_entries 连续 3 天无新增 → 告警

#### 层级 3：效果性检查（冒烟测试，每周一次）

选一个最近 3 天内与 AI 讨论过的具体问题，调用 `hippocampus_recall.py` 查询，检查 Anda 是否记录了相关内容。这步需要手动确认，不做自动判断。

---

### 改进 3：指标记录（优先级：中）

**目标**：让系统有"健康历史"，能看出趋势。

在每个核心脚本运行结束时，追加写入 `logs/metrics.jsonl`：

```json
{
  "date": "2026-05-26",
  "script": "hippocampus_formation",
  "total_messages": 52,
  "meaningful_messages": 14,
  "batches_success": 2,
  "batches_total": 2,
  "run_duration_seconds": 38
}
```

```json
{
  "date": "2026-05-26",
  "script": "layer2_wiki",
  "new_chunks_processed": 23,
  "wiki_entries_written": 4,
  "run_duration_seconds": 12
}
```

**对 MyScope 最有价值的 4 个指标**：

| 指标 | 从哪里采集 | 告诉你什么 |
|------|----------|-----------|
| `formation_meaningful_msgs` | hippocampus_formation.py | hippocampus 在学真东西还是学噪音 |
| `wiki_entries_total`（每日） | layer2_wiki.py 运行后查询 Meilisearch | 知识库在增长还是停滞 |
| `memory_chunks_added_today` | layer1_rag.py 运行后统计 | Layer 1 采集是否正常 |
| `recall_latency_ms` | health_check.py 中发一次 recall 请求计时 | Anda 是否活着且健康 |

---

### 改进 4：加强 formation 过滤（优先级：中）

现有代码（第 136 行）的过滤太弱：

```python
# 现有（不够）
if text and not text.startswith("<permissions") and not text.startswith("<environment_context"):
```

建议替换为调用上方的 `is_meaningful()` 函数，并在 `main()` 中统计过滤前后数量：

```python
raw_count = len(all_messages)
all_messages = [m for m in all_messages if is_meaningful(m)]
filtered_count = raw_count - len(all_messages)
print(f"  过滤系统 prompt：{filtered_count} 条 → 剩余 {len(all_messages)} 条有效消息")
```

---

## 四、实施优先级

```
第 1 优先（本周）：
  → 改进 1：有效消息过滤器
  → 改进 2 层级 1：存活性检查 + 飞书告警

第 2 优先（下周）：
  → 改进 3：metrics.jsonl 指标记录
  → 改进 2 层级 2：质量性告警（依赖 metrics）

长期维护：
  → 改进 2 层级 3：每周冒烟测试（手动）
  → 定期回顾 metrics 趋势（每月）
```

---

## 五、判断原则（给未来自己的提醒）

1. **改系统前先问**：这个改动是在解决本质问题，还是在增加偶然复杂度？
2. **每个新脚本都要有**：最后运行时间记录、运行结果写入 metrics
3. **Layer 3 要克制**：外部信息源不是你，增加公众号/播客订阅前先问自己它会带来差异化还是噪音
4. **hippocampus 是核心**：formation + recall 的质量是整个系统最值得投入的地方

---

*生成日期：2026-05-26*  
*基于 MyScope 系统架构分析及 meta-learner 学习会话*
