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
