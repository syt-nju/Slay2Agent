# slay2agent Framework Design

本文档记录相对稳定的架构边界。需求来源是 `docs/feature-requirements.md`,任务拆解和执行进度放在 `docs/implementation-todo.md`。

## Overview

slay2agent 是 train-free 的研究型 Agent。它不读取画面、不模拟键鼠、不训练模型,通过 STS2MCP REST API 读取结构化 state 并执行 action,所有决策由云端 LLM 完成,统一经过 LLM adapter。

研究焦点是 **memory 与 context 管理机制**;Agent 框架本身只是承载这一研究的 testbed。研究方法是**纵向迭代**(`docs/memory-iteration-log.md`),不做横向 baseline 对照。

## Layers

```text
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

`src/slay2agent/game/schema.py`(待建):把 raw JSON 按 `state_type` 分发到对应解析路径,产出供 prompt 使用的 *compact view*。技术栈不绑定;dataclass 或 pydantic 任选,只要稳定输入输出 + 可测试。

`to_compact_prompt(state)` 是策略侧的唯一入口,token 预算可控。

### LLM Adapter

`src/slay2agent/llm/`:统一云端模型调用。canonical dataclass + provider 适配。首版只接 OpenRouter。

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
    <skill_id>.md       # frontmatter metadata + markdown body
  oracle.md
```

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

## Deferred Decisions

- skill metadata 的最终字段集(`name` + `description` 之外是否加 `when_to_read` / `examples` / `tags`)。
- skill body 是否需要长度上限。
- skill creator 是否限制每小关最多写 N 个 skill。
- `oracle.md` 4k tokens 软上限是否合适。
- 是否引入第二个 LLM provider。
- loop_detector 的 `window_size=10` / `repeat_threshold=4` 默认是否合适。
- 是否允许主 agent 在 prompt 里以 "thought" 段方式 reason 后再 tool call。
- skill creator 是否改为限时异步(首版同步阻塞)。
