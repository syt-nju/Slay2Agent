# Memory Iteration Log

本文档记录 slay2agent memory 设计的每次有意义迭代。研究方法是**纵向迭代**，不做横向 baseline 对照。

每条 entry 字段：`version` / `change` / `motivation` / `observed`。

---

## v0 — 初始 Memory 架构（F-008a 首版）

- **version**: v0
- **change**:
  - 建立三层 memory 架构：L0（in-context history，state_type 切换时清空）、L1（skill 库，`agent_state/skills/*.md` 文件，frontmatter metadata + markdown body）、L2（`agent_state/oracle.md`，全局元策略）
  - 主 agent 每步 system prompt 强制注入：全部 skill metadata 列表 + oracle.md 全文
  - 主 agent 暴露 `list_skills()` / `read_skill(skill_id)` 两个 memory 工具（只读）
  - Skill 文件格式：YAML frontmatter（`description`、`when_to_read`）+ markdown body；id 从文件名推导
  - skill 库为空 / oracle.md 为空时主 agent 仍可正常决策
- **motivation**: 为 memory 研究建立最小可用基础；skill 库和 oracle 从空起步，后续由 skill creator（F-008b）和 oracle updater（F-008c）自动维护，研究者也可人工编辑
- **observed**: 首次真实 run 发现 skill creator 写出的文件 frontmatter 经常坏掉（`## description:` 当成 H2、缺闭合 `---`），原因是自定义 `description` + `when_to_read` 双字段并非 LLM 训练时熟悉的 skill schema；同时 `when_to_read` 与主流 Claude/Cursor SKILL.md 格式不一致，研究者人工编辑或参考公开 skill 模板时摩擦较大

---

## v1 — Skill 格式对齐 mainstream SKILL.md（保持单文件）

- **version**: v1
- **change**:
  - Frontmatter 字段从 `description` + `when_to_read` 改为 `name` + `description`，对齐 Claude Code / Cursor `.cursor/skills/*/SKILL.md` 主流约定
  - `description` 同时承担"做什么"与"何时使用"两层语义，必须形如 `<summary>. Use when <trigger>.`；它是主 agent 看到的唯一触发信号，主 agent 不再有单独的 `when_to_read` 字段可读
  - Body 要求是自包含 markdown 文档（建议以 `# <Name>` 起手），观感与 mainstream SKILL.md 一致
  - 物理布局保持扁平单文件 `agent_state/skills/<skill_id>.md`（暂不引入 `references/`、`scripts/` 子目录）
  - 同步更新 `write_skill` 工具 schema（参数：`skill_id` / `name` / `description` / `body`），并在 skill_creator system prompt 里写明新 schema 的语义
  - 旧 skill `ironclad_early_combat.md` 手动迁移到新格式
- **motivation**:
  - LLM 在生成"经典 mainstream SKILL.md"时熟练度远高于自定义 schema，预期能稳定写出合法 frontmatter
  - `description` 把"用途 + 触发条件"压在同一字段，主 agent 只看一段文本就能决定是否 `read_skill`，对齐 progressive disclosure 习惯
  - 与 `.cursor/skills/*` 共用约定后，研究者复制公开 skill 或人工编辑摩擦最小
- **observed**: （下一轮 run 后填写：观察 LLM 写出的 frontmatter 合规率、主 agent 在 metadata 注入下的 read_skill 命中率）

---

## v2 — L0 Compaction Sub-agent（F-012）

- **version**: v2
- **change**:
  - 新增 `compactor` sub-agent，在 L0 消息数超过阈值（默认 `L0_COMPACT_THRESHOLD=30`）时触发
  - Compaction 策略：保留最近 K 条原文（默认 `L0_COMPACT_KEEP=6`），将更早的消息压缩为一条 `role="user"` 摘要消息
  - 压缩后 L0 结构：`[summary_user_msg] + [最近 K 条原文]`；前缀稳定，KV cache 可复用
  - 新配置字段：`L0_COMPACT_ENABLED`（默认 true）、`L0_COMPACT_THRESHOLD`（默认 30）、`L0_COMPACT_KEEP`（默认 6）
  - Token 计入独立 role `"compactor"`，compaction 事件写入 `subagent.jsonl`
  - 失败不阻断主 agent，保留原始 L0 继续运行
- **motivation**: 同一 state_type 段落（如 crystal_sphere 232 步）内 L0 无上限增长导致 O(n²) token 爆炸。滑动窗口方案会每步改变前缀，导致 KV cache 无法复用；compaction 在超阈值时一次性压缩旧历史，之后前缀保持稳定（summary + recent K），兼顾 token 控制与 cache 效率
- **observed**: （运行后填写：compactor 实际触发频率、压缩前后 token 量对比、summary 质量、主 agent 在压缩后 L0 上的决策连贯性）

---

## v3 — Skill 维护改为离线两阶段流水线（解决 skill 数量爆炸）

- **version**: v3
- **change**:
  - **删除实时维护机制**：移除 `state_type` 边界触发的 `skill_creator`、run 末的 `skill_librarian`（去重 sub-agent）、以及两级 LRU skill cache（`skill_cache.py`）。推理期 skill 库**完全只读**，play 过程对 skill 库零写入。
  - **改为离线、CLI 手动触发、基于完整轨迹的两阶段流水线**：
    - **阶段1 `slay2agent analyze`**：扫描所有"尚未生成失败分析报告"的 run，逐条做**轨迹复盘**（不纠结输赢，每条都复盘），产出 `runs/<run_id>/failure_report.json`——分点列出若干失败原因，每条附对应轨迹片段（step 区间 + 摘录）。模型默认取 env 中正在使用的模型。
    - **阶段2 `slay2agent distill`**：取所有"尚未被处理"的失败报告，分两个 **context 隔离** 的子步骤：(2a) 只看失败原因，聚类出"相似且高频"的共性失败原因；(2b) 对每个共性失败原因开**独立 context**，对照现有 skill 的 `failure_reason` + `description`（可 `read_skill` 读 body）判定"新建 skill"或"覆盖改进已有 skill"。
  - **轨迹重建（确定性）**：固定代码逻辑，从 `steps.jsonl` 的顶层字段（`state_type` / `tool_name` / `tool_args` / `settled_state_summary` / 新增 `action_feedback`）投影出"动作 → 结果"序列。只读字段、不调 LLM、不重解析游戏 JSON，因而**免疫 L0 compaction**（不依赖 `llm_request_messages`）。
  - **trace 新增字段**：`StepRecord.action_feedback`，记录被执行动作的原始返回/报错串（旧设计里这串只存在于下一步 L0，不是一等字段）。补上后每步自描述，重建无损、报错显式。
  - **skill 文件格式新增 frontmatter 字段 `failure_reason`**：仅供阶段2 创建/改进时比对去重用；play 时主 agent **仍只看 `description`** 选 skill（`failure_reason` 不注入 play-time system prompt）。skill 严格 template = `failure_reason`（针对的失败原因）+ `description`（apply when 触发条件）+ body（具体细节）。
  - **去 LRU 后的注入策略**：play 时**全量注入所有 skill 的 `description`** 到 system prompt（数量靠本次重构压下去），`list_skills` / `read_skill` 懒加载 body 不变。
- **motivation**:
  - **核心问题：skill 数量爆炸。** 旧机制在每个 `state_type` 边界就触发一次 `skill_creator`，一局 60–100+ 次，粒度过细：每段都倾向"学到一点就新建 skill"，段与段之间缺乏全局视野，run 末的 `skill_librarian` 单次去重压不住中段已经堆出来的条目，导致 skill 库迅速膨胀、metadata 注入挤占主 agent context。
  - skill 的创建与总结**本应基于完整轨迹的全局归纳**，而非局部小关的碎片。把维护从"实时、每段触发"挪到"离线、按整局完整轨迹、手动触发"，既消除推理期写库的副作用（KV cache 抖动、并发写、噪声写入），又让总结具备跨步乃至跨局的全局视角。
  - **两阶段 + context 隔离**：先在干净 context 里独立判定"哪些失败反复出现"（不被 skill 库现状带偏），再在独立 context 里判定"是否已有 skill 覆盖 / 如何改进"，分离关注点以提升每步判断质量；去重不再依赖单独的 librarian，而是内化进阶段2 的 read-before-write 流程。
- **observed**: （运行后填写：失败报告质量、阶段2a 聚类准确度、skill 库规模是否趋稳、改进 vs 新建比例、主 agent 在全量 description 注入下的 read_skill 召回率）
