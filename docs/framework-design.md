# slay2agent Framework Design

本文档记录相对稳定的架构边界。需求来源是 `docs/feature-requirements.md`,任务拆解和执行进度放在 `docs/implementation-todo.md`。

## Overview

slay2agent 是 train-free 的研究型 Agent。它不读取画面、不模拟键鼠、不训练模型,通过 STS2MCP REST API 读取结构化 state 并执行 action,所有决策由云端 LLM 完成,统一经过 LLM adapter。

研究焦点是 **memory 与 context 管理机制**;Agent 框架本身只是承载这一研究的 testbed。研究方法是**纵向迭代**(`docs/memory-iteration-log.md`),不做横向 baseline 对照。

## Layers

```text
Live Context Viewer (--live, browser SSE)  ← optional, observer 模式挂载
Memory Iteration Log (docs/memory-iteration-log.md)
Trace (runs/<run_id>/)
Main Agent Loop  ──┐
Skill Creator     ─┤   ← 三类 agent 共用 Sub-agent Runner
Oracle Updater    ─┘
Memory Layer (skills/ + oracle.md)
Tool Bridge (gate + loop detector)
LLM Adapter (provider-agnostic, role-aware token accounting)
State Parser (state_type → compact view)
Game HTTP Client (STS2MCP REST)
```

## Core Abstractions

### Game Client

`src/slay2agent/game/client.py`:STS2MCP REST 的薄封装。只负责 HTTP、序列化、错误暴露,不含任何策略。`get_state` / `post_action` / `post_action_and_settle` 是仅有的对外接口。

### Action Layer

`src/slay2agent/game/action_schemas.py`:STS2MCP action 的**声明式元数据表** (`ACTION_SCHEMAS`),配合 `dispatch(client, name, args)` 通用 runner。表本身覆盖 STS2MCP 当前版本暴露的全部 singleplayer action(28 个),每行带 description / 参数 schema / `applicable_state_types`,既作为 LLM `ToolSchema` 来源(`to_tool_schema(action)`),又作为 tool bridge gate 的输入(`actions_for_state(state_type)`)。Action 不决定何时执行,合法性由 tool bridge 控制。新增 STS2MCP action = 表里加一行,不写新函数。

### State Parser

`src/slay2agent/game/schema.py`:把 raw JSON 按 `state_type` 分发到对应解析路径,产出供 prompt 使用的 *compact view*。实现使用 `@dataclass(frozen=True, slots=True)`(不新增依赖,与 `action_schemas.py` 风格一致),按 `state_type` 提供专用 view 类(`MenuView` / `CombatView` / `HandSelectView` / `MapView` / `EventView` / `RewardsView` / `CardRewardView` / `CardSelectView` / `GameOverView`)+ `UnknownView` fallback;`menu` 内部按 `menu_screen` 二次分发但仍走 `MenuView`。

`to_compact_prompt(state)` 是策略侧的唯一入口,每个 view 单独渲染,默认抑制牌堆全文/远端 map 节点/已 chosen 事件项等高 token 区域,从而把 prompt 体量约束在设计层而非靠运行时截断。

### LLM Adapter

`src/slay2agent/llm/`:统一云端模型调用。canonical dataclass + provider 适配。

**核心类:** `OpenAICompatibleAdapter(model, api_key, base_url, *, extra_headers=None, timeout)` — 使用 `openai` Python SDK，通过 `base_url` 参数可连接任意 OpenAI-compatible endpoint（OpenAI 原生、OpenRouter、vLLM、DeepSeek 等）。当 `base_url` 包含 `openrouter.ai` 时自动注入 OpenRouter 专用 headers，其余 provider 无需特殊处理。

**配置:** `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` / `LLM_TIMEOUT`（`config.py` 从 env 读取）。`LLMAdapter` ABC 是唯一允许在 `llm/` 包外引用的类型；具体 adapter 类不出现在 agent 层。

usage 按 `(agent_role, model)` 分桶记录 input/output token。`agent_role: Literal["main","skill_creator","oracle_updater"]` 是上层语义,**adapter 不感知** —— orchestrator / sub-agent runner 拿到 `LLMResponse` 后调 `tracker.record(role, resp.model, resp.usage)`。这一拆分是 trace 与 run summary 报告"三类 agent 各自 token"的硬基础。

### Tool Bridge

LLM 不直接调用 game action,统一经过 bridge:

- **gate**:按当前 `state_type` 决定可见 game tool 集合;memory tool(`list_skills` / `read_skill`)始终可见。
- **loop_detector**:若最近 N 步内同一 `(action, args)` 出现次数达到阈值,直接终止当前 run 并写 trace metadata。**不做 recovery**。
- **不做 pre_execute 参数预校验**;STS2MCP 报错走 `ActionError` 路径。

默认值(可配置):`window_size=10`、`repeat_threshold=4`。这两个数字会在 F-006 跑过真实 trace 后调整。

### Memory Layer

三层结构,职责互不重叠:

- **L0 in-context history**:主 agent 单次小关(同一 `state_type` 段落)内的对话历史。`state_type` 切换边界处显式清空。
- **L1 skill 库**:文件形式的策略片段。每个 skill 含极简 metadata(≤ 几十 token)+ body。所有 metadata 在每步主 agent 调用时强制注入 system prompt;body 由主 agent 通过 `read_skill` 主动获取。
- **L2 `oracle.md`**:全局元策略文档。每次主 agent 调用强插入 system prompt。run 边界由 oracle updater 重写。软上限默认 4k tokens。

存放路径(惯例):

```text
agent_state/
  skills/
    <skill_id>.md       # mainstream SKILL.md 格式：name + description frontmatter + markdown body
  oracle.md
```

Skill 文件格式对齐 Claude Code / Cursor `.cursor/skills/*/SKILL.md` 约定：

```markdown
---
name: <人类可读显示名>
description: <一两句话，同时说明"做什么"和"何时使用"，主 agent 只凭此字段决定是否 read_skill>
---

# <Name>

<完整 markdown 正文，read_skill 才会加载>
```

`description` 是唯一的触发信号，必须自带"use when ..."一类的触发条件，主 agent 看不到额外的 `when_to_read` 字段。

`agent_state/` 由用户通过 git 管理(commit / branch / rollback);代码侧不实现 snapshot 切换。

### Sub-agent Runner

Skill creator 与 oracle updater 都是 sub-agent。它们和主 agent **共用底层基础设施**:LLM adapter、tool dispatch、token tracker、trace writer、错误处理。这是硬约束(见 invariants),不允许各写各的。

差别只在:

- prompt 模板
- 工具集(主 agent 仅 read 类;skill creator 含 read + write + delete skill;oracle updater 不暴露文件 IO,直接由 runner 接收返回内容写盘)
- 触发时机
- 输入装配方式

首版 skill creator 与主 agent **同步阻塞**:主 agent 等 sub-agent 返回后再继续。后续根据延迟数据决定是否改为限时异步。

### Main Agent Loop

```text
loop:
    state = get_state()
    if state_type 与上一步不同 and 上一段非空:
        flush L0
        run skill_creator(prev_segment_trace)   # 同步,失败不阻断
    inject (skill metadata list, oracle.md) into system prompt
    main_agent.decide(compact_view(state)) -> tool call
    tool_bridge.gate -> post_action_and_settle
    write trace step
    if loop_detector triggers OR state == game_over:
        run oracle_updater(full_run_trace)
        break
```

死循环终止与正常 `game_over` 对 oracle updater 来说一视同仁,都触发 run 结束流程。trace 中标记终止原因。

### Trace and Token Accounting

`runs/<run_id>/`:

- `steps.jsonl`:主 agent 每步一行
- `subagent.jsonl`:skill creator / oracle updater 每次触发一行
- `summary.json`:终止原因 + 三类 agent 各自 input/output token 总量、调用次数

trace 是后续 memory 设计迭代的研究素材;summary 用于在 `docs/memory-iteration-log.md` 中归档。

### Live Context Viewer

`src/slay2agent/viewer/`:可选的实时观察层,通过 observer 模式与 loop 解耦。

**Observer 协议**

`RunObserver`(`Protocol`):loop.py 在关键节点调用,不加 `--live` 时传入 no-op 实现,零开销。

```text
on_step_start(step, state_type, user_message, system_summary)
on_llm_response(tool_call_name, tool_call_args, usage)
on_tool_result(action, result_summary)
on_memory_event(event_type, detail)   # L0_cleared / skill_created / skill_updated / oracle_rewritten
on_usage_snapshot(tracker_snapshot)    # 定时由 viewer 侧拉取
```

**Web 服务**

- `--live` 时后台 daemon 线程启动 stdlib `http.server`,serve 单个 HTML 文件 + SSE endpoint (`/events`)。
- observer 实现将事件写入 `queue.Queue`,SSE handler 从 queue 读取并推送。
- 端口默认 `8765`,可配置。

**前端**

- 单个 HTML 文件(`src/slay2agent/viewer/index.html`),vanilla JS + 原生 `EventSource`。
- 布局:左侧主面板(对话流时间线)、右侧侧边栏(oracle/skill 可点击展开 + 记忆事件指示器 + token 用量)。
- 无 npm/node/build step,无新 Python 依赖。

### Memory Iteration Log

`docs/memory-iteration-log.md`:**研究方法层面的硬要求**,不是可选文档。每次对 memory 设计做有意义改动(skill schema、强插内容、sub-agent prompt、触发时机、工具集等)必须新增一条 entry,字段至少:`version` / `change` / `motivation` / `observed`。

不是横向对照 baseline,而是**纵向研究记录**。

## Data Flow

```text
get_state
  -> 检测 state_type 切换
       (清空 L0; 触发 skill_creator)
  -> parse compact view
  -> assemble system prompt: oracle.md + skill metadata
  -> main agent decide (LLM call, role=main)
  -> tool bridge gate
  -> post_action_and_settle
  -> write trace step
  -> loop or terminate

run termination
  -> run oracle_updater (LLM call, role=oracle_updater)
  -> rewrite oracle.md
  -> write run summary

每次 state_type 切换 (含中间)
  -> run skill_creator (LLM call, role=skill_creator)
  -> 强制 list/read 匹配相似 skill
  -> 0 或多次 write/delete skill
  -> 写 subagent trace
```

## Invariants

- Game client 不包含策略。
- 策略层不直接依赖 STS2MCP 原始 dict,只通过 State Parser 的 compact view。
- 所有 LLM 调用经过 adapter,且必须传入 `agent_role` 标记。
- 所有 LLM action 调用经过 tool bridge。
- L0 in-context 在 `state_type` 切换时必须清空。
- 主 agent 不能 write 任何 memory 文件(只 read)。
- skill creator 不能修改 `oracle.md`;oracle updater 不能修改 skill 库。
- 三类 agent 共用一份 LLM adapter / tool dispatch / token tracker / trace writer 实现,不允许重复实现。
- 死循环检测的处理是 *直接终止 run*,不做 recovery。
- `except` 必须配合 `logger.error(...)`,不允许静默吞掉异常。
- 任何 memory 设计变更必须在 `docs/memory-iteration-log.md` 留下 entry。
- Live viewer 通过 observer 协议挂载,不改变 loop 核心逻辑;viewer 故障不阻断 agent 运行。

### L0 Compaction

当同一 `state_type` 段落内 L0 消息数超过阈值时，触发 compaction sub-agent 压缩旧历史，避免 O(n²) token 增长。

**设计要点：**

- 阈值可配置（`L0_COMPACT_THRESHOLD`，默认 30 条 message）。
- 触发时，将 L0 中除最近 K 条外的旧消息交给 compactor sub-agent，产出一条摘要 message。
- 压缩后 L0 结构：`[summary_message] + [最近 K 条原文]`。前缀（system prompt + summary）在后续步骤中保持稳定，KV cache 可复用。
- Compactor 是第四类 sub-agent（`role="compactor"`），共用基础设施。
- 失败时保留原始 L0 继续运行，不阻断主 agent。

**与滑动窗口的区别：** 滑动窗口每步丢弃最老消息导致前缀变化，KV cache 完全失效。Compaction 产出的 summary 是稳定前缀，仅在下次 compaction 时变化（频率远低于每步）。

## Deferred Decisions

- skill metadata 是否需要在 `name` + `description` 之外再加结构化字段（`examples` / `tags` / `applicable_state_types` 等）—— v1 起已对齐 mainstream，只保留 `name` + `description`，`description` 自带 "use when ..." 触发条件。
- skill body 是否需要长度上限。
- skill creator 是否限制每小关最多写 N 个 skill。
- `oracle.md` 4k tokens 软上限是否合适。
- 是否引入第二个 LLM provider。**→ F-010 已实现为 OpenAI-compatible 通用适配层，不再 deferred。**
- loop_detector 的 `window_size=10` / `repeat_threshold=4` 默认是否合适。
- 是否允许主 agent 在 prompt 里以 "thought" 段方式 reason 后再 tool call。
- skill creator 是否改为限时异步(首版同步阻塞)。
- 暂未收集 fixture 的 `state_type`(`rest_site` / `shop` / `fake_merchant` / `treasure` / `bundle_select` / `relic_select` / `crystal_sphere` / `boss`)是否要在 F-004 内补齐专用 view —— 首版统一走 `UnknownView` fallback,等 demo loop 跑出真实样本后再补。

