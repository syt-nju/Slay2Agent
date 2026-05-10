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
- **observed**: （首次 run 跑通后填写）
