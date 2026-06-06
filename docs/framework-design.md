# slay2agent Framework Design

本文档记录相对稳定的架构边界。需求来源是 `docs/feature-requirements.md`,任务拆解和执行进度放在 `docs/implementation-todo.md`。

## Overview

slay2agent 是 train-free 的研究型 Agent。它不读取画面、不模拟键鼠、不训练模型,通过 STS2MCP REST API 读取结构化 state 并执行 action,所有决策由云端 LLM 完成,统一经过 LLM adapter。

研究焦点是 **memory 与 context 管理机制**;Agent 框架本身只是承载这一研究的 testbed。研究方法是**纵向迭代**(`docs/memory-iteration-log.md`),不做横向 baseline 对照。

## Layers

```text
Offline Skill Maintenance (CLI: analyze / distill)  ← 离线,按完整轨迹维护 skill 库 (F-013)
Live Context Viewer (--live, browser SSE)  ← optional, observer 模式挂载
Memory Iteration Log (docs/memory-iteration-log.md)
Trace (runs/<run_id>/)
Main Agent Loop  ──┐
Oracle Updater    ─┘   ← run 末触发, 与主 agent 共用 Sub-agent Runner
Memory Layer (skills/ + oracle.md)   ← 推理期只读
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

usage 按 `(agent_role, model)` 分桶记录 input/output token。`agent_role`(如 `main` / `oracle_updater` / `compactor`,以及 F-013 离线流水线的 `failure_analyzer` / `distiller_*`)是上层语义,**adapter 不感知** —— orchestrator / sub-agent runner / CLI 拿到 `LLMResponse` 后调 `tracker.record(role, resp.model, resp.usage)`。这一拆分是 trace 与 run summary 报告"各 agent 角色各自 token"的硬基础。

### Tool Bridge

LLM 不直接调用 game action,统一经过 bridge:

- **gate**:按当前 `state_type` 决定可见 game tool 集合;memory tool(`list_skills` / `read_skill`)始终可见。
- **loop_detector**:若最近 N 步内同一 `(action, args)` 出现次数达到阈值,直接终止当前 run 并写 trace metadata。**不做 recovery**。
- **不做 pre_execute 参数预校验**;STS2MCP 报错走 `ActionError` 路径。

默认值(可配置):`window_size=10`、`repeat_threshold=4`。这两个数字会在 F-006 跑过真实 trace 后调整。

### Memory Layer

三层结构,职责互不重叠:

- **L0 in-context history**:主 agent 单次小关(同一 `state_type` 段落)内的对话历史。`state_type` 切换边界处显式清空。
- **L1 skill 库**:文件形式的策略片段。每个 skill 含极简 metadata(≤ 几十 token)+ body。所有 `description` 在每步主 agent 调用时强制注入 system prompt;body 由主 agent 通过 `read_skill` 主动获取。**推理期只读** —— skill 的创建/改进/去重全部由离线 CLI 流水线(F-013)按完整轨迹完成,play 过程不写 skill 库。
- **L2 `oracle.md`**:全局元策略文档。每次主 agent 调用强插入 system prompt。run 边界由 oracle updater 重写。软上限默认 4k tokens。

存放路径(惯例):

```text
agent_state/
  skills/
    <skill_id>.md       # failure_reason + description frontmatter + markdown body
  oracle.md
```

Skill 文件格式(严格 template,frontmatter 在 mainstream SKILL.md 基础上加 `failure_reason`)：

```markdown
---
name: <人类可读显示名>
failure_reason: <这条 skill 针对的失败原因，简短；仅供 F-013 蒸馏时比对去重，不注入 play-time prompt>
description: <一两句话，同时说明"做什么"和"何时使用"，play 时主 agent 只凭此字段决定是否 read_skill>
---

# <Name>

<具体细节：可操作的策略，read_skill 才会加载>
```

- `description` 是 **play 时唯一的触发信号**，必须自带"use when ..."触发条件。
- `failure_reason` 只在 F-013 阶段2 创建/改进 skill 时被读取用于去重判断，**不出现在 play-time system prompt**。

`agent_state/` 由用户通过 git 管理(commit / branch / rollback);代码侧不实现 snapshot 切换。

### Sub-agent Runner

Oracle updater(run 末)与离线 skill 维护流水线(F-013 的 analyze / distill)都复用主 agent 的**底层基础设施**:LLM adapter、tool dispatch、token tracker、错误处理。这是硬约束(见 invariants),不允许各写各的。

差别只在:

- prompt 模板
- 工具集(主 agent 仅 read 类;oracle updater 不暴露文件 IO,直接由 runner 接收返回内容写盘;distill 含 read + write + delete skill)
- 触发时机(oracle updater = run 末;analyze / distill = 用户手动跑 CLI)
- 输入装配方式

> **v3 移除**:旧 `skill_creator`(`state_type` 边界触发)与 `skill_librarian`(run 末去重)已删除。skill 维护不再发生在推理期,改由 F-013 离线流水线按完整轨迹完成。

### Main Agent Loop

```text
loop:
    state = get_state()
    if state_type 与上一步不同 and 上一段非空:
        flush L0                                  # 仅清 L0,不再触发任何 skill 维护
    inject (all skill descriptions, oracle.md) into system prompt   # skill 库只读
    main_agent.decide(compact_view(state)) -> tool call
    tool_bridge.gate -> post_action_and_settle
    write trace step (含 action_feedback)
    if loop_detector triggers OR state == game_over:
        run oracle_updater(full_run_trace)
        break
```

推理期对 skill 库**零写入**;skill 的维护离线进行(见下方 Offline Skill Maintenance)。死循环终止与正常 `game_over` 对 oracle updater 来说一视同仁,都触发 run 结束流程。trace 中标记终止原因。

### Offline Skill Maintenance (F-013)

skill 库由两条**手动触发的 CLI 命令**离线维护,基于完整轨迹而非推理期片段:

```text
slay2agent analyze   # 阶段1：失败分析
  for run in runs/ where not exists failure_report.json:
      transcript = deterministic_reconstruct(run/steps.jsonl)
      report = LLM(role=failure_analyzer).analyze(transcript)   # 不纠结输赢,逐条复盘
      write run/failure_report.json   # 失败原因 + 轨迹片段(step 区间 + 摘录)

slay2agent distill   # 阶段2：skill 蒸馏(两个 context 隔离的子步骤)
  reports = [r for r in all failure_report.json if r.distilled_at is None]
  clusters = LLM(role=distiller_cluster).cluster(reports.failure_reasons)   # 2a：只看失败原因
  for cluster in clusters:                                                  # 2b：每组独立 context
      ctx = cluster + evidence + existing_skills(failure_reason, description)
      LLM(role=distiller_write, tools=[read_skill, write_skill, delete_skill]).decide(ctx)
          -> 新建 skill 或 整文件覆盖改进已有 skill
  mark reports.distilled_at
```

**轨迹重建 `deterministic_reconstruct`**:固定代码逻辑,逐 step 读 `steps.jsonl` 顶层字段
(`state_type` / `tool_name` / `tool_args` / `action_feedback` / `settled_state_summary`),
投影成"动作 → 反馈 → 结果状态"序列。不调 LLM、不重解析游戏 JSON、不读 `llm_request_messages`,
因而免疫 L0 compaction。`failure_report.json` 存在 = 已分析;报告含 `distilled_at` = 已蒸馏;两者都用文件状态做幂等去重。

### Trace and Token Accounting

`runs/<run_id>/`:

- `steps.jsonl`:主 agent 每步一行(含 `action_feedback` = 被执行动作的原始返回/报错串,使每步自描述,供 F-013 离线重建)
- `subagent.jsonl`:oracle updater 每次触发一行
- `summary.json`:终止原因 + 各 agent 角色各自 input/output token 总量、调用次数
- `failure_report.json`:F-013 阶段1 产出(存在 = 已分析;含 `distilled_at` = 已被阶段2 蒸馏)

trace 是后续 memory 设计迭代的研究素材,也是 F-013 离线 skill 维护的唯一输入;summary 用于在 `docs/memory-iteration-log.md` 中归档。

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
  -> 检测 state_type 切换 (仅清空 L0; 不触发任何 skill 维护)
  -> parse compact view
  -> assemble system prompt: oracle.md + 全部 skill description (只读)
  -> main agent decide (LLM call, role=main)
  -> tool bridge gate
  -> post_action_and_settle
  -> write trace step (含 action_feedback)
  -> loop or terminate

run termination
  -> run oracle_updater (LLM call, role=oracle_updater)
  -> rewrite oracle.md
  -> write run summary

离线 (F-013, 用户手动跑 CLI, 与 play 解耦)
  analyze:  逐条未分析 run -> 确定性重建轨迹 -> LLM 失败分析 -> 写 failure_report.json
  distill:  取未蒸馏报告
            -> 2a 聚类共性失败原因 (context 仅含失败原因)
            -> 2b 每组独立 context: 对照现有 skill 判定 新建/覆盖改进 -> write/delete skill
            -> 回写 distilled_at
```

## Invariants

- Game client 不包含策略。
- 策略层不直接依赖 STS2MCP 原始 dict,只通过 State Parser 的 compact view。
- 所有 LLM 调用经过 adapter,且必须传入 `agent_role` 标记。
- 所有 LLM action 调用经过 tool bridge。
- L0 in-context 在 `state_type` 切换时必须清空。
- 主 agent 不能 write 任何 memory 文件(只 read);**推理期(play)对 skill 库零写入**,skill 维护只发生在离线 CLI 流水线(F-013)。
- F-013 distill 不能修改 `oracle.md`;oracle updater 不能修改 skill 库。
- F-013 的轨迹重建必须是**确定性固定代码逻辑**(不调 LLM、不重解析游戏 JSON、不依赖 `llm_request_messages`)。
- 各 agent 角色共用一份 LLM adapter / tool dispatch / token tracker / trace writer 实现,不允许重复实现。
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

- skill metadata 是否需要在 `name` + `failure_reason` + `description` 之外再加结构化字段（`examples` / `tags` / `applicable_state_types` 等）—— 暂不加，`description` 自带 "use when ..." 触发条件。
- skill body 是否需要长度上限。
- (F-013) 阶段2a "高频/相似" 完全交 LLM 判断是否稳定，必要时再引入显式批大小或频次阈值。
- `oracle.md` 4k tokens 软上限是否合适。
- 是否引入第二个 LLM provider。**→ F-010 已实现为 OpenAI-compatible 通用适配层，不再 deferred。**
- loop_detector 的 `window_size=10` / `repeat_threshold=4` 默认是否合适。
- 是否允许主 agent 在 prompt 里以 "thought" 段方式 reason 后再 tool call。
- 暂未收集 fixture 的 `state_type`(`rest_site` / `shop` / `fake_merchant` / `treasure` / `bundle_select` / `relic_select` / `crystal_sphere` / `boss`)是否要在 F-004 内补齐专用 view —— 首版统一走 `UnknownView` fallback,等 demo loop 跑出真实样本后再补。

