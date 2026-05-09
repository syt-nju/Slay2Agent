# slay2agent Implementation Todo

本文档是执行进度 tracker,不是需求来源。需求以 `docs/feature-requirements.md` 为准,架构边界以 `docs/framework-design.md` 为准。

## Current Mode

Execution Mode(除非 memory 设计需要再次重审,届时回 Design Mode)。

阶段一目标:跑出"main menu → 一局 `game_over`"的端到端 demo loop,期间 memory 系统从空 skill / 空 oracle 起步。
阶段二目标:启用 skill creator / oracle updater 两个 sub-agent,在跑通的 demo loop 上开始 memory 设计的纵向迭代。

## Feature Order

1. F-002 LLM Adapter ✅(已实施 OpenRouter baseline)
2. F-003 Game Communication Path ✅(已实施 client + 9 actions + fixtures)
3. F-004 State Parser & Compact View
4. F-005 Phase 1 Demo Loop
5. F-006 Tool Bridge & Loop Detector
6. F-007 Trace & Token Accounting
7. F-008a Skill Registry + Read Tool
8. F-008b Skill Creator Sub-agent
9. F-008c Oracle Updater Sub-agent

F-005 / F-006 / F-007 互相耦合,会在同一个 phase 内推进:demo loop 走通必须有 tool bridge + trace。F-008a 是 F-008b/c 的硬前置(没有 skill 文件结构,sub-agent 无处写)。

## Progress Summary

- [x] F-002 OpenRouter baseline + retry + usage + smoke entry
- [x] F-003 GameClient + 9 action wrappers + fixture-driven tests + `slay2agent inspect`
- [x] `tests/fixtures/real/` 已收集 14 个真实 STS2MCP state 样本
- [x] `vendor/sts2mcp-docs/` 已就位
- [ ] F-004 State Parser & compact view
- [ ] F-005 Phase 1 demo loop(main_menu → game_over)
- [ ] F-006 Tool Bridge + Loop Detector
- [ ] F-007 Trace + Token Accounting
- [ ] F-008a Skill Registry + Read Tool
- [ ] F-008b Skill Creator Sub-agent
- [ ] F-008c Oracle Updater Sub-agent

## Phase 0 — Setup (done)

- [x] CLI / config / `.env.example` / README 前置条件
- [x] STS2MCP 通路打通 + 真实 fixtures 收集

## Phase 1 — State Parser & Compact View (F-004)

- [ ] 选定解析方案(dataclass 或 pydantic),记录在 framework-design 的 deferred decisions 一节
- [ ] 按 `state_type` 分发解析,覆盖 14 个真实 fixtures 中已知类型
- [ ] 实现 `to_compact_prompt(state)`,token 预算可控
- [ ] fixture 解析测试 + 未知 `state_type` 的 fallback 测试

Expected verification:

- 14 个真实 fixtures 全部能被解析为 compact view
- compact view 字符串长度可控且包含主 agent 决策必需信息

## Phase 2 — Demo Loop + Tool Bridge + Trace (F-005 / F-006 / F-007)

这三个 feature 同步推进,任何一项缺失都不能验证另外两项。

### F-006 Tool Bridge & Loop Detector

- [ ] 按 `state_type` 决定可见 game tool 集合(gate)
- [ ] loop detector(最近 N 步同 `(action, args)` 重复达阈值 → 终止)
- [ ] gate 收窄 / loop 触发 / loop 未触发 三类单元测试

### F-007 Trace & Token Accounting

- [ ] `runs/<run_id>/steps.jsonl` 写入器(主 agent 每步一行)
- [ ] `runs/<run_id>/subagent.jsonl` 写入器(F-008b/c 才会用,先把接口暴露好)
- [ ] LLM adapter 增加 `agent_role` 入参,token usage 按 `(role, model)` 分桶
- [ ] `runs/<run_id>/summary.json`(终止原因 + 三类 agent token 拆分 + 调用次数)

### F-005 Phase 1 Demo Loop

- [ ] `slay2agent play` 入口(配置文件指定角色 / ascension,默认 Ironclad + A0)
- [ ] menu / character_select / ascension / singleplayer 各 `state_type` 的 navigation 逻辑(可在主 agent prompt 里简单引导,无需独立 skill)
- [ ] `state_type` 切换时显式清空 L0
- [ ] system prompt 注入空 skill metadata 列表 + 空 `oracle.md`(F-008 之前为空字符串占位)
- [ ] 死循环 / `game_over` 触发 run 终止 + summary 写盘

Expected verification:

- 一次完整 run 能从 main_menu 跑到 `game_over` 或死循环终止
- `runs/<run_id>/summary.json` 包含三类 agent token 字段(此时 sub_agent 部分为 0)
- trace 内容可人工复盘:能看到每步 `state_type`、注入内容、LLM 调用、tool 调用、settle 后 state

## Phase 3 — Skill-based Memory v0 (F-008a / F-008b / F-008c)

启动该 phase 之前**必须先创建** `docs/memory-iteration-log.md`,首条 entry 为 v0 设计描述。

### F-008a Skill Registry + Read Tool

- [ ] 定义 skill 文件格式(frontmatter metadata + markdown body)
- [ ] `agent_state/skills/` + `agent_state/oracle.md` 读取层
- [ ] 主 agent 暴露 `list_skills()` / `read_skill(id)` 两个 memory tool
- [ ] system prompt 注入:全部 skill metadata + `oracle.md` 全文
- [ ] 接受 skill 库为空 / oracle 为空时主 agent 仍可正常决策

### F-008b Skill Creator Sub-agent

- [ ] 抽取 sub-agent runner(LLM adapter / tool dispatch / token tracker / trace writer 复用)
- [ ] `state_type` 切换边界触发 skill creator
- [ ] sub-agent prompt 强制"先 list + read 匹配相似 skill,再决定 write / extend / merge / delete / no-op"
- [ ] 工具集:`list_skills` / `read_skill` / `write_skill` / `delete_skill`
- [ ] 失败 / 超时不阻断主 agent 推理,记录 `logger.error(...)`
- [ ] 写 `subagent.jsonl`

### F-008c Oracle Updater Sub-agent

- [ ] run 结束(`game_over` 或死循环终止)触发
- [ ] 输入装配:整局 trace + 该局 skill creator reasoning 摘要 + 上一版 `oracle.md`
- [ ] 软上限默认 4k tokens(可配置),超出由 sub-agent 自行裁剪
- [ ] 覆盖写 `oracle.md`;失败保留旧版
- [ ] 写 `subagent.jsonl`

Expected verification:

- 一次完整 run 后,`runs/<run_id>/summary.json` 三类 agent token 都非零
- skill 库能被 skill creator 真实修改;`oracle.md` 在 run 结束后被改写
- skill creator 的 reasoning 中能观察到"先匹配再创建"的流程

## 持续任务 — Memory Iteration Log

- [ ] 启动 F-008 阶段时创建 `docs/memory-iteration-log.md`,定义 entry schema(`version` / `change` / `motivation` / `observed`)
- [ ] 每次对 memory 设计做有意义改动(skill schema、强插内容、sub-agent prompt、触发时机、工具集等)必须新增一条 entry

## Open Blocks

- 需要本地 STS2 + STS2MCP 运行环境做 F-005 起的端到端验证(无 GPU 要求,但需要游戏客户端)。
- skill creator / oracle updater 的 prompt 设计在 v0 实施时由实际 trace 反推迭代,不在文档里定终稿。
- loop_detector 的 `window_size` / `repeat_threshold` 默认值在 F-006 实施后基于实际 trace 估计。

## Discontinued

旧 doc 中的以下内容已删除或重写,记录于此防止混淆:

- 旧 F-001 Runtime Boundary:并入 Phase 0 Setup,不再占 feature ID。
- 旧 F-006 内的 skill 拆分(combat / map / event / rewards / fallback):废弃,改为 prompt 层 skill 元数据自动注入。
- 旧 F-006 内的 `pre_execute` 参数预校验:废弃,STS2MCP 报错走 `ActionError` 路径。
- 旧 F-007 内的胜率 / Act 进度 / baseline 对照:删除,研究方法改为纵向迭代 + memory-iteration-log。
- 旧 F-008 内的 reflect / playbook / memory 三选一架构:替换为 L0 / L1 / L2 三层 + 两个 sub-agent。
- 旧 F-009 Deferred Extensions:并入 Non-goals。
- 旧 Open Block "F-003 needs samples":已 stale,真实 fixtures 早已收集。
- 主 agent 的 python exec / compact / 通用文件 write 工具:不在 v0 范围。
