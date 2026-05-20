# slay2agent Implementation Todo

本文档是执行进度 tracker,不是需求来源。需求以 `docs/feature-requirements.md` 为准,架构边界以 `docs/framework-design.md` 为准。

## Current Mode

Execution Mode(除非 memory 设计需要再次重审,届时回 Design Mode)。

阶段一目标:跑出"main menu → 一局 `game_over`"的端到端 demo loop,期间 memory 系统从空 skill / 空 oracle 起步。
阶段二目标:启用 skill creator / oracle updater 两个 sub-agent,在跑通的 demo loop 上开始 memory 设计的纵向迭代。

## Feature Order

1. F-002 LLM Adapter ✅
2. F-003 Game Communication Path ✅
3. F-004 State Parser & Compact View ✅
4. F-005 Phase 1 Demo Loop ✅
5. F-006 Tool Bridge & Loop Detector ✅
6. F-007 Trace & Token Accounting ✅
7. F-008a Skill Registry + Read Tool ✅
8. F-008b Skill Creator Sub-agent ✅
9. F-008c Oracle Updater Sub-agent ✅
10. F-009 Live Context Viewer ✅
11. F-010 Provider-Agnostic LLM Config (OpenAI-Compatible Adapter) ✅

F-005 / F-006 / F-007 互相耦合,会在同一个 phase 内推进:demo loop 走通必须有 tool bridge + trace。F-008a 是 F-008b/c 的硬前置(没有 skill 文件结构,sub-agent 无处写)。

## Progress Summary

- F-002 OpenRouter baseline + retry + role-aware usage(`(role, model)` 分桶)+ smoke entry
- F-003 GameClient + 28-action 声明式 schema 表(`ACTION_SCHEMAS`)+ `dispatch` + `actions_for_state`(F-006 gate 直接消费)+ `to_tool_schema`(LLM tool 描述来源)+ fixture 驱动测试 + `slay2agent inspect`
- `tests/fixtures/real/` 已收集 14 个真实 STS2MCP state 样本
- `vendor/sts2mcp-docs/` 已就位
- F-004 State Parser & compact view(`schema.py`,dataclass + per-state-type 渲染器 + UnknownView fallback)
- F-005 Phase 1 demo loop(main_menu → game_over)
- F-006 Tool Bridge + Loop Detector
- F-007 Trace + Token Accounting
- F-008a Skill Registry + Read Tool
- F-008b Skill Creator Sub-agent
- F-008c Oracle Updater Sub-agent

## Phase 0 — Setup (done)

- CLI / config / `.env.example` / README 前置条件
- STS2MCP 通路打通 + 真实 fixtures 收集

## Phase 1 — State Parser & Compact View (F-004) ✅

- 选定解析方案:**dataclass**(`@dataclass(frozen=True)`,无新增依赖,与 `action_schemas.py` 风格一致),已记录在 framework-design 的 State Parser 一节。
- 按 `state_type` 分发:`menu` / `monster` / `elite` / `boss` / `hand_select` / `map` / `event` / `rewards` / `card_reward` / `card_select` / `game_over`,其余走 `UnknownView` fallback。
- `to_compact_prompt(state)` 按 view 分别渲染,默认抑制牌堆全文 / 远端 map 节点 / 已 chosen 事件项;实测 14 个 fixture 输出长度 < 700 字符,设计层 token 上限 4000 字符。
- fixture 解析测试 + 未知 `state_type` 的 fallback 测试(`tests/test_state_schema.py`,47/47 绿)。

未来扩展(在 demo loop 跑出真实样本后再补,F-004 不阻断):

- 暂未收集 fixture 的 `rest_site` / `shop` / `fake_merchant` / `treasure` / `bundle_select` / `relic_select` / `crystal_sphere` / `boss` 默认走 `UnknownView`;F-005 demo loop 验证时若被频繁踩到再补专用 view。

## Phase 2 — Demo Loop + Tool Bridge + Trace (F-005 / F-006 / F-007) ✅

这三个 feature 同步推进,任何一项缺失都不能验证另外两项。

### F-006 Tool Bridge & Loop Detector ✅

- gate 直接消费 `actions_for_state(state_type)`,叠加 memory tool(F-008a 的 `list_skills` / `read_skill`)始终可见
- loop detector(最近 N 步同 `(action, args)` 重复达阈值 → 终止)
- gate 收窄 / loop 触发 / loop 未触发 三类单元测试(`tests/test_tool_bridge.py`, 16/16 绿)

### F-007 Trace & Token Accounting ✅

- `runs/<run_id>/steps.jsonl` 写入器(主 agent 每步一行)
- `runs/<run_id>/subagent.jsonl` 写入器(F-008b/c 才会用,接口已暴露)
- `UsageTracker` 在 `record()` 处加 per-(role, model) 调用次数计数(`role_call_counts()`)
- `runs/<run_id>/summary.json`(终止原因 + `tracker.snapshot()` + `tracker.role_totals()` + 调用次数,三类 agent 角色始终存在,未用者为 0)
- `tests/test_trace.py` 14/14 绿

### F-005 Phase 1 Demo Loop ✅

- `slay2agent play` 入口(`--character`, `--ascension`, `--runs-dir`, `--window-size`, `--repeat-threshold`)
- `state_type` 切换时显式清空 L0(含 log)
- system prompt 注入空 skill metadata 列表 + 空 `oracle.md`(F-008 前占位)
- `game_over` / `LoopDetected` 触发终止 + summary 写盘
- `finally` 确保 summary.json 在所有终止路径(含 exception)都写盘

## Phase 3 — Skill-based Memory v0 (F-008a / F-008b / F-008c)

启动该 phase 之前**必须先创建** `docs/memory-iteration-log.md`,首条 entry 为 v0 设计描述。

### F-008a Skill Registry + Read Tool ✅

- 定义 skill 文件格式(frontmatter metadata + markdown body)
- `agent_state/skills/` + `agent_state/oracle.md` 读取层
- 主 agent 暴露 `list_skills()` / `read_skill(id)` 两个 memory tool
- system prompt 注入:全部 skill metadata + `oracle.md` 全文
- 接受 skill 库为空 / oracle 为空时主 agent 仍可正常决策

### F-008b Skill Creator Sub-agent ✅

- 抽取 sub-agent runner(LLM adapter / tool dispatch / token tracker / trace writer 复用)
- `state_type` 切换边界触发 skill creator
- sub-agent prompt 强制"先 list + read 匹配相似 skill,再决定 write / extend / merge / delete / no-op"
- 工具集:`list_skills` / `read_skill` / `write_skill` / `delete_skill`
- 失败 / 超时不阻断主 agent 推理,记录 `logger.error(...)`
- 写 `subagent.jsonl`

### F-008c Oracle Updater Sub-agent ✅

- run 结束(`game_over` 或死循环终止)触发
- 输入装配:整局 run trace 摘要 + 上一版 `oracle.md`(+ 可选读取 skill 库)
- 软上限默认 4k tokens(可配置 `ORACLE_MAX_TOKENS`),超出字符级截断兜底
- 覆盖写 `oracle.md`;失败保留旧版
- 写 `subagent.jsonl`

验证结果(真实环境,run 20260511T013704_16d632ec):

- 118 步，`game_over` 终止
- `skill_creator` 正确触发（monster → game_over），更新了 `ironclad_early_combat` skill
- `oracle_updater` 正确触发，写入首版 oracle.md（4038 chars）
- `summary.json` 三类 agent token 均非零：main=300379, skill_creator=166422, oracle_updater=1169

Expected verification (已完成):

- ✅ 一次完整 run 后，`runs/<run_id>/summary.json` 三类 agent token 都非零
- ✅ skill 库被 skill creator 真实修改；`oracle.md` 在 run 结束后被改写
- ✅ skill creator 的 reasoning 中观察到"先 list_skills → read_skill → write_skill"的流程

## Phase 4 — Live Context Viewer (F-009) ✅

### F-009a Observer 协议 + loop 挂载 ✅

- [x] 定义 `RunObserver` Protocol(`on_step_start` / `on_llm_response` / `on_tool_result` / `on_memory_event` / `on_run_end`)
- [x] 实现 `NoOpObserver`(默认,零开销)
- [x] `run_demo_loop` 接受 `observer` 参数,在 6 个关键节点加 emit 调用
- [x] 确认不加 `--live` 时与现有行为完全一致(234 tests pass)

### F-009b Web Server + SSE ✅

- [x] `src/slay2agent/viewer/server.py`:stdlib `http.server` + daemon 线程
- [x] SSE endpoint (`/events`):从 `queue.Queue` 读事件,推 `text/event-stream`
- [x] 静态文件 serve(`index.html`)
- [x] `WebObserver` 实现 `RunObserver`,将事件序列化写入 queue
- [x] `/usage` JSON endpoint 供前端定时拉取 token snapshot

### F-009c 前端页面 ✅

- [x] `src/slay2agent/viewer/index.html`:单文件,vanilla JS + CSS
- [x] 左侧主面板:对话流时间线(step / state_type / user message / tool call / result)
- [x] 右侧侧边栏:oracle 可点击展开 + skill 列表可点击展开
- [x] 记忆事件指示器:skill_creator / oracle_updater 触发时 flash 动画高亮
- [x] token 用量模块:三类 agent 累计 input/output,每 30s 自动刷新

### F-009d CLI 集成 ✅

- [x] `slay2agent play` 增加 `--live` / `--live-port` flags
- [x] `--live` 时启动 `WebObserver` + HTTP server,终端打印访问地址
- [x] server 在 run 结束后保持 30s 供最终查看,然后优雅关闭

## Phase 5 — Provider-Agnostic LLM Config (F-010)

### F-010 OpenAI-Compatible Adapter

**背景:** `OpenRouterAdapter` 内部已用 `openai` SDK，只需把 `base_url` / `extra_headers` 参数化即可支持任意 OpenAI-compatible provider。

- [x] `src/slay2agent/llm/openai_compat.py`:新建 `OpenAICompatibleAdapter`，接受 `base_url` + `extra_headers` 参数；`openrouter.ai` 自动注入 OpenRouter headers
- [x] `src/slay2agent/llm/openrouter.py`:标记废弃或删除
- [x] `src/slay2agent/llm/__init__.py`:导出 `OpenAICompatibleAdapter`，移除 `OpenRouterAdapter`
- [x] `src/slay2agent/config.py`:env vars 改为 `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` / `LLM_TIMEOUT`；`require_api_key()` 错误消息更新
- [x] `.env.example`:更新为新 env var 名，加 OpenRouter / OpenAI 原生使用示例
- [x] `loop.py` / `skill_creator.py` / `oracle_updater.py`:类型标注 `LLMAdapter`，移除 `OpenRouterAdapter` import
- [x] `smoke.py` / `cli.py`:使用新 env var 名，类型标注更新
- [x] `tests/test_openrouter.py` → `tests/test_openai_compat.py`:重命名 + import 对齐新类名
- [x] `tests/test_config.py`:env vars 更新为 `LLM_*`
- [x] 全部离线测试绿（252 passed）

## 持续任务 — Memory Iteration Log

- 启动 F-008 阶段时创建 `docs/memory-iteration-log.md`,定义 entry schema(`version` / `change` / `motivation` / `observed`)
- 每次对 memory 设计做有意义改动(skill schema、强插内容、sub-agent prompt、触发时机、工具集等)必须新增一条 entry

## Phase 6 — Context Management (F-011 / F-012)

### F-011 UnknownView Raw Payload Exposure + Issue Logging ✅

- [x] `_render_unknown` 改为 dump `view.payload` JSON（截断 3000 chars）
- [x] 在 loop.py 中检测首次进入 UnknownView state_type，写 issue 到 `issues.jsonl`
- [x] 测试：UnknownView 渲染包含 payload 内容、截断生效、issue 写入（55/55 绿）

### F-012 L0 Compaction Sub-agent

- [x] `config.py` 新增 `L0_COMPACT_THRESHOLD` / `L0_COMPACT_KEEP` / `L0_COMPACT_ENABLED` 配置
- [x] 实现 compactor sub-agent（prompt 模板 + 输入装配 + 摘要输出解析）
- [x] loop.py 集成：L0 超阈值时触发 compaction，替换旧消息为 summary
- [x] token 计入 `role="compactor"`，trace 写入 `subagent.jsonl`
- [x] 失败兜底：compaction 失败保留原始 L0
- [x] 测试：阈值触发、压缩后 L0 结构正确、失败不阻断（16/16 绿）
- [x] `memory-iteration-log.md` 新增 entry（v2）

## Open Blocks

- 需要本地 STS2 + STS2MCP 运行环境做 F-005 起的端到端验证(无 GPU 要求,但需要游戏客户端)。
- skill creator / oracle updater 的 prompt 设计在 v0 实施时由实际 trace 反推迭代,不在文档里定终稿。
- loop_detector 的 `window_size` / `repeat_threshold` 默认值在 F-006 实施后基于实际 trace 估计。

## Discontinued

旧 doc 中的以下内容已删除或重写,记录于此防止混淆:

- 旧 F-001 Runtime Boundary:并入 Phase 0 Setup,不再占 feature ID。
- 旧 F-003 内的 9 个 Python action wrapper(`actions.py`):废弃,改为 `action_schemas.py` 声明式表 + `dispatch` 通用 runner。理由:wrapper 全是 pure pass-through,新增 action 反而要写函数;改成表后 F-006 gate / LLM `ToolSchema` / 测试都共用同一个 SSOT。
- 旧 F-006 内的 skill 拆分(combat / map / event / rewards / fallback):废弃,改为 prompt 层 skill 元数据自动注入。
- 旧 F-006 内的 `pre_execute` 参数预校验:废弃,STS2MCP 报错走 `ActionError` 路径。
- 旧 F-007 内的胜率 / Act 进度 / baseline 对照:删除,研究方法改为纵向迭代 + memory-iteration-log。
- 旧 F-008 内的 reflect / playbook / memory 三选一架构:替换为 L0 / L1 / L2 三层 + 两个 sub-agent。
- 旧 F-009 Deferred Extensions:并入 Non-goals。
- 旧 Open Block "F-003 needs samples":已 stale,真实 fixtures 早已收集。
- 主 agent 的 python exec / compact / 通用文件 write 工具:不在 v0 范围。