# Flomo 增量采集与幂等重建设计

## 背景

现有 `scripts/layer1_flomo.py` 通过 DOM selector 抓取 Flomo 页面。页面第一条是置顶 memo，而 OpenCLI 的 Markdown 提取与脚本正则没有形成逐条 memo 边界，导致任务每天只把置顶内容重新交给 DeepSeek 切片。日志与 metrics 显示成功，但最近 memo 实际没有进入 `memory_chunks`。

当前 Meilisearch 中有 293 条 legacy flomo 文档，均没有 `memo_id`，无法与原始 memo 做可靠映射。Flomo API 的只读审计结果为 844 条历史记录，其中 820 条 active、24 条已删除；DOM 无限滚动只能得到 799 条 active，因此 DOM 不能继续作为全量真相。

## 目标

- 每天 19:10 自动读取 Flomo 新出现的 active memo。
- 已处理的 `memo_id` 不重复采集、不重复写入。
- 短 memo、长 memo、纯图片 memo 都留下可检索记录。
- 采集、写入或索引任务失败时不推进 state，下一次自动重试。
- 一次性重建现有 flomo 索引，去掉 legacy 重复内容并补齐 active memo。
- 合法的“今天没有新 memo”是成功状态，不产生误报警。

## 非目标

- 本次不解决 `layer2_wiki` 只读取 Memory API 前 200 条结果、因而可能看不到 flomo 的既有问题。新文档会包含 `text` 与 `date`，保证可直接搜索，也为后续修复 Layer 2 提供正确字段。
- 本次不把 Flomo 删除动作同步成 myScope 删除；增量链路只关心新 memo。首次重建会排除已经删除的 24 条历史记录。
- 不升级或修改系统级 OpenCLI 安装。

## 方案选择

采用 OpenCLI 内置的结构化 `flomo memos` 适配器，不再使用 DOM selector，也不再使用 DeepSeek 切片。

未采用的方案：

- DOM 抓取：受置顶、虚拟列表和页面结构变化影响，已证明会漏数据。
- 在 myScope 内复制完整 Flomo 内部 API 客户端：需要维护签名与认证细节，重复 OpenCLI 已有能力。
- 继续 DeepSeek 切片：增加余额、超时、JSON 解析和非确定性风险；Flomo memo 本身已经是原子记忆单元。

## 数据源与分页

生产采集调用：

```text
opencli flomo memos --limit 200 --since <unix-seconds> -f json
```

已验证的接口语义：

- 返回结构包含 `id`、`content`、`tags`、`images`、`created_at`、`updated_at` 和 URL。
- 结果按 `updated_at` 从旧到新排列。
- `--since` 是 inclusive，页与页之间会重复边界项，必须用 `memo_id` 去重。
- `--slug` 在 OpenCLI 1.8.5 中不能可靠推进分页，不作为游标。
- exit code `66` 且错误码为 `EMPTY_RESULT` 表示没有新 memo，应按成功处理；其他非零退出均视为采集失败。
- 满 200 条时用本页最大 `updated_at` 请求下一页。若时间戳不前进或没有出现新 ID，则中止并报错，避免死循环或静默漏数。

Browser Bridge 仍复用现有自动唤醒逻辑；连接失败时不更新 state。

## State 模型

`logs/layer1_flomo_state.json` 升级为 version 2：

```json
{
  "version": 2,
  "cursor_updated_at": 1783595400,
  "seen_memo_ids": ["memo-id-1", "memo-id-2"],
  "last_success_at": "2026-07-10T19:10:00+08:00"
}
```

- `cursor_updated_at` 只用于减少 API 返回量。
- `seen_memo_ids` 是幂等性的最终依据；inclusive 分页或任务重试都不能重复生成文档。
- legacy state（只有 `last_run`）触发首次全量模式。
- state 使用临时文件加 `os.replace` 原子写入。
- 只有所有 ingest tasks 均为 `succeeded` 后，才一次性提交 cursor、seen IDs 和成功时间。
- 任一批次失败时 state 完全不变；已进入 Meilisearch 的稳定 ID 文档在重试时被 replace，不会新增重复项。

## 文档模型

一条 active Flomo memo 对应一条 canonical `memory_chunks` 文档：

```python
{
    "id": sha1(f"flomo:{memo_id}"),
    "memo_id": memo_id,
    "title": first_nonempty_line[:60],
    "text": normalized_plain_text,
    "content": normalized_plain_text,
    "source": "flomo",
    "date": created_at[:10],
    "created_at": created_at,
    "updated_at": updated_at,
    "indexed_at": now,
    "url": memo_url,
    "tags": normalized_tags,
    "images": image_urls,
}
```

- HTML 使用标准库转换为保留段落的 plain text。
- `text` 用于 Memory API 与 Layer 2；`content` 保持 dashboard 和其他消费者兼容。
- 当前 active memo 的 plain-text 最大长度约 4,322 字，Meilisearch 可直接存储，无需非确定性分段。
- 纯图片 memo 的 `text` 使用可识别占位文本并保留图片 URL。
- 空文本且无图片的记录不写入，但其 ID 会被标记为 seen，避免每天重试无意义记录。

## 写入确认与凭据

`/ingest` 返回 `task_uid` 只表示 Meilisearch 接受任务，不表示索引已经成功。采集器必须轮询任务状态，看到 `succeeded` 后才能提交 state。

- `MEMORY_API_TOKEN` 继续从 `.env` 读取。
- 新增本机环境变量 `MEILI_MASTER_KEY`，只写入 gitignored `.env`，禁止在代码、设计或测试中硬编码。
- `MEILI_URL` 默认 `http://localhost:7700`，可通过环境变量覆盖。
- 缺少 `MEILI_MASTER_KEY` 时 fail closed，不写 state。

## 首次重建流程

用户已确认允许清理现有 flomo 索引。执行顺序必须保证回填失败时 legacy 数据仍在：

1. 通过只读 Flomo API 审计获得 active IDs 与 deleted IDs；认证 token 不落盘。
2. 把现有 293 条 `source=flomo` 且缺少 `memo_id` 的 legacy 文档完整备份到 gitignored `logs/backups/`。
3. 从 cursor `0` 分页采集并写入全部 820 条 active memo，等待所有 Meilisearch tasks 成功。
4. 验证 distinct `memo_id` 覆盖全部 active IDs、没有 deleted IDs、字段与内容非空规则正确。
5. 仅删除步骤 2 快照中的 legacy IDs，并等待删除 task 成功；不得重新按 `source=flomo` 枚举删除，以免误删新文档。
6. 再写入 version 2 state。

## 日常运行流程

1. 加载 version 2 state，并唤醒 Browser Bridge。
2. 从 `cursor_updated_at` 开始分页读取；对 inclusive 边界按 ID 去重。
3. 过滤 `seen_memo_ids`，得到真正的新 memo。
4. 规范化并构建稳定 ID 文档，按批写入 Memory API。
5. 等待全部 Meilisearch tasks 成功。
6. 原子提交 state，记录 `last_run` 与 metrics。
7. 若没有新 memo，写入成功 metrics，但 `documents_written=0`，索引不发生变化。

## Metrics 与健康检查

`layer1_flomo` 每次记录：

- `fetched_memos`
- `new_memos`
- `skipped_seen_memos`
- `skipped_empty_memos`
- `documents_written`
- `latest_memo_updated_at`
- `collect_errors` / `collect_error_summary`
- `ingest_errors` / `ingest_error_summary`
- `run_duration_seconds`

health check 保留采集、认证和写入失败红警；合法的 `new_memos=0` 不触发 Layer 1 低吞吐告警。只有存在输入但最终 `documents_written=0` 时才视为质量异常。

## 测试策略

新增 `tests/test_layer1_flomo.py`，严格按 TDD 覆盖：

- OpenCLI JSON 字段解析、缺 ID、malformed JSON 与重复 ID。
- `since` inclusive 分页去重、时间游标推进、分页无进展保护。
- exit 66 `EMPTY_RESULT` 与其他非零退出的不同语义。
- legacy state 迁移、已见 ID 跳过、不同 ID 的同内容仍各自收录。
- HTML 转 plain text、短 memo、长 memo、纯图片与真正空记录。
- 文档字段、原始日期和稳定 ID。
- ingest 第二批失败时 state 不变；重试得到相同 ID。
- 所有 tasks 成功后才保存 state 与 `last_run`。
- 第二次运行新增 0、写入 0。

扩展 `tests/test_health_check.py`，覆盖“成功但无新 flomo 不告警”和“有新 memo 但写入 0 告警”。

验证命令使用 launchd 同款 Python 3.9：

```bash
/usr/bin/python3 -m unittest discover -s tests -p 'test_layer1_flomo.py' -v
/usr/bin/python3 -m unittest discover -s tests -p 'test_health_check.py' -v
```

完成后进行两次连续真实运行：首次重建验证覆盖，第二次验证 `new_memos=0`、`documents_written=0`、索引数量不变。最后检查唯一的 `com.myscope.layer1-flomo` 仍按每天 19:10 调度。

## 文件范围

- 修改 `scripts/layer1_flomo.py`
- 新增 `tests/test_layer1_flomo.py`
- 修改 `scripts/health_check.py`
- 修改 `tests/test_health_check.py`
- 修改 `README.md` 中 flomo 的 DeepSeek/DOM 描述
- 新增本设计文档和后续实施计划

保留工作区现有 runtime state、metrics、`reports/` 与 `scripts/tests/`，不纳入本次提交。
