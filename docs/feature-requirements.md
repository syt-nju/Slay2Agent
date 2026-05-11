# slay2agent Feature Requirements

本文档是 slay2agent 的需求来源。实现顺序可调整,但需求变更需要先在对话中确认。

## Final Goal

slay2agent 是一个**研究型 testbed**,用于探索"什么样的 memory 与 context 管理机制能让 LLM 真的玩好 Slay the Spire 2"。

阶段一验证 testbed 自身合格:Agent 从游戏 main menu 启动,按配置选定角色与 ascension,跑完一局并自然结束(`game_over` 或死循环终止),期间过程产生可复盘的完整 trace。

阶段二起,在此 testbed 上**纵向迭代** memory 设计(skill / oracle 分层、sub-agent 触发时机、工具接口等都是研究变量),通过 `docs/memory-iteration-log.md` 文档化每版改动与观察。**不设横向 baseline 对照**,研究产出形式是迭代日志 + trace 复盘。

## Scope

### Goals

- STS2MCP REST 是唯一游戏感知/执行通路,云端 LLM API 是唯一推理通路。
- 单脚本入口:从 main menu 启动,按配置选角色 + ascension,跑到 `game_over` 或死循环终止,无人工介入。
- 三层 memory 架构:
  - **L0 小关内 in-context**(`state_type` 切换即清空)
  - **L1 skill 库**(metadata 强插 system prompt + 模型主动 read body)
  - **L2 `oracle.md`**(强插 system prompt,run 结束时由独立 sub-agent 重写)
- 三类 agent(主 agent / skill creator / oracle updater)**共用底层基础设施**(LLM client、tool dispatch、token tracking、trace writer)。
- 每次 run 结束输出三类 agent 各自的 input/output token 量。
- 维护 `docs/memory-iteration-log.md` 记录 memory 设计每次迭代。

### Non-goals

- 不训练、微调、RLHF、本地 GPU 推理。
- 不读取画面、不模拟键鼠。
- 不追求胜率 / Act 通关进度作为成功条件。
- 不做横向对照 baseline(无 memory-off 对照、无人类对比、无外部 bot 对比)。
- 不做自动开新 run、不做菜单跨局自动重启,memory dir 不内置版本切换(用户用 git 管理)。
- 不做批量 eval / replay 工具 / 多 provider 原生适配 / LLM 自动改写 prompt 模板。
- 不在主 agent 暴露 python exec、compact 等通用代码执行类工具。
- 阶段一不解决高 ascension / 多角色全覆盖,默认 Ironclad + A0;角色与 ascension 通过配置可改但需游戏内已解锁。

## Features

### F-002 LLM Adapter

**Status:** implemented

所有 LLM 调用必须通过 provider-agnostic 适配层。首版支持 OpenRouter,统一 chat、tool call、usage、retry 和错误分类。三类 agent(主 / skill creator / oracle updater)共用同一 adapter。

**Acceptance criteria**

- 上层只依赖 canonical request/response 类型,不直接依赖 provider wire format。
- 支持 text response、tool calls、stop reason、token usage。
- transient 错误可重试,不可重试错误可分类返回。
- usage 按 `(agent_role, model)` 分桶记录 input/output token,不做价格计算或预算熔断。`agent_role` 是上层语义,在 `UsageTracker.record(role, model, usage)` 处显式传入,**不污染** adapter 接口。
- 离线测试覆盖协议类型、错误分类、retry 和 usage(含 role-aware 分桶)。

### F-003 Game Communication Path

**Status:** implemented

系统需要打通 STS2MCP REST 通路,提供薄封装 client、action 元数据表和动作后 settle 机制。

**Acceptance criteria**

- `get_state` 能读取当前游戏 JSON state。
- `post_action` 能调用 STS2MCP action 并返回响应。
- 维护一份 **声明式 action 元数据表**(`ACTION_SCHEMAS`),覆盖 STS2MCP 当前版本暴露的全部 singleplayer action,字段包含 name / description / 参数 schema / `applicable_state_types`。表本身就是 LLM tool 描述来源,也是 F-006 tool bridge gate 的输入。新增 STS2MCP action = 表里加一行,不写新函数。
- 提供 `dispatch(client, name, args)` 通用 runner;未知 action 抛 `KeyError`,STS2MCP 报错走 `ActionError` 路径。
- 动作后由 `post_action_and_settle` 统一加一个短延时再 `get_state`,避免 end_turn 后读取到旧 hand;STS2MCP 本身同步响应,不做严格收敛轮询(真有 race 由 demo loop 触发后再加强)。
- 超时或动作失败时记录 `logger.error(...)`,不静默吞掉异常。
- 有 fixture 驱动测试覆盖 schema 表完整性、`dispatch` 请求体、`actions_for_state` gate、schema → canonical `ToolSchema` 的渲染、以及 `slay2agent inspect` 在 mod-reachable 与 unreachable 两条路径上的行为。
- `slay2agent inspect` 在 mod 运行时打印当前 state JSON;mod 不可达时打印错误并返回非零状态。

### F-004 State Parser & Compact View

**Status:** implemented

系统需要把原始 JSON state 转换为策略层和 prompt 层都稳定可依赖的视图。技术选型不进入需求,只要求 *输入稳定 / 输出可压缩 / 可被测试*。

**Acceptance criteria**

- 提供按 `state_type` 分类的解析入口,主 agent 不直接消费原始 dict。
- 暴露当前小关相关的领域信息(如 combat 内 hand / energy / enemies、map 内可选节点、reward 内可选项),细节范围以"主 agent 决策所需"为准。
- 提供 `to_compact_prompt(state)`,输出 token 预算可控的 state 文本表达,作为主 agent 每步 user message 的核心内容。
- 已知 `state_type` 至少有一个 fixture 解析测试(可复用 `tests/fixtures/real/` 已收集的 14 个真实样本)。
- 未识别的 `state_type` 走 fallback,不让 Agent 崩溃。

### F-005 Phase 1 Demo Loop

**Status:** planned

系统需要把"主 agent 从 main menu 跑完一局"这条端到端通路打通。这个 feature 的通过判据就是 testbed 自身合格的标志。

**Acceptance criteria**

- 入口脚本(如 `slay2agent play`)接受配置文件,指定角色与 ascension(默认 Ironclad + A0)。
- Agent 从 main menu 状态启动,完成菜单 → 角色选择 → ascension 选择 → 进入 run 的导航。
- Agent 持续推理直到出现 `game_over` 状态,或被 loop detector 终止。
- **死循环判定即终止 run**(不做 recovery),终止原因写入 trace metadata。
- 每个 `state_type` 切换处,主 agent 的 in-context 历史(L0)被显式清空,只保留 skill metadata + `oracle.md` 作为强插内容。
- run 结束后输出三类 agent 各自 input/output token 总量(F-008 未实施时 sub-agent 部分为 0,但字段必须存在)。
- 一次完整 run 对应一个 `runs/<run_id>/` 目录,内含 trace 和 token 汇总。

### F-006 Tool Bridge & Loop Detector

**Status:** planned

主 agent 通过 tool bridge 调用游戏 action;tool bridge 负责合法 action 集合 gate 与死循环检测。**不做 skill 拆分,不做 pre_execute 参数预校验**(STS2MCP 报错由 `ActionError` 路径处理,不在客户端二次校验)。

**Acceptance criteria**

- 按当前 `state_type` 暴露给 LLM 的 tool 集合自动收窄(如 combat 内不出现 map 选择 tool)。
- loop detector 识别 *最近 N 步内 `(action, args)` 重复达到阈值* 的情况,直接终止 run 并写 trace metadata。
- N 与阈值通过配置可调,默认值在 framework-design 中定义。
- 单元测试覆盖 gate 收窄、loop 触发、loop 未触发三类场景。

### F-007 Trace & Token Accounting

**Status:** planned

trace 是 memory 设计迭代的唯一研究素材,必须在 F-008 之前可用。**不记录胜率 / Act 进度等性能指标**,只记录可复盘所需信息和 token 消耗。

**Acceptance criteria**

- 每步至少记录:`step`、`timestamp`、`state_type`、L0 是否在此步被清空、注入到 system prompt 的 skill metadata 列表 + `oracle.md` 版本标识、主 agent 完整 LLM request/response、被调用的 tool 与参数、settle 后的新 state 摘要。
- 每次 sub-agent 触发(skill creator / oracle updater)记录:触发原因、输入摘要、完整 LLM request/response、产生的文件级修改 diff(skill 文件增删改,或 `oracle.md` 重写)。
- run 结束后输出 `runs/<run_id>/summary.json`,包含终止原因(`game_over` / `loop_terminated` / `error`)、三类 agent 各自 input/output token 总量、调用次数。
- trace 必须能被人工或脚本以 jsonl/json 形式直接读取,不依赖运行时数据库。

### F-008a Skill Registry + Read Tool

**Status:** planned

主 agent 侧的 skill 系统:metadata 强插,正文按需 read。

**Acceptance criteria**

- skill 以文件形式存放在固定 memory dir(具体路径由 framework-design 定义),格式对齐 mainstream Claude Code / Cursor `.cursor/skills/*/SKILL.md` 约定。
- 每个 skill 文件 = YAML frontmatter (`name` + `description`) + 自包含的 markdown body。`description` 是注入主 agent 的唯一触发信号,必须同时表达"做什么"与"何时使用"(pattern: `<summary>. Use when <trigger>.`)。
- 主 agent 每步 system prompt 强制注入:全部 skill 的 metadata 列表 + 当前 `oracle.md` 全文。
- 主 agent 暴露两个 memory 工具:`list_skills()`、`read_skill(skill_id)`。**无 write、无 python exec、无 compact**。
- skill 文件格式应允许人工编辑,以便研究者直接调整。
- skill 库为空 / `oracle.md` 为空时主 agent 仍能正常决策。

### F-008b Skill Creator Sub-agent

**Status:** planned after F-008a

一个独立 sub-agent,在每次 `state_type` 切换边界自动启动,基于刚结束的小关 trace 维护 skill 库。

**Acceptance criteria**

- 触发时机:`state_type` 切换且上一段不是空段(刚启动除外)。
- 输入:上一段 L0 完整 trace + 当前 `oracle.md` + 现有 skill 库可读访问。
- 工具集:`list_skills` / `read_skill` / `write_skill` / `delete_skill`,**不能修改 `oracle.md`**。
- prompt 强制流程:在做任何 `write` / `delete` 之前,必须先通过 `list` + `read` 检查是否存在相似 skill,优先选择"扩写已有 skill"或"合并相似 skill",最后才考虑"新建 skill"。匹配过程必须出现在 sub-agent 的 reasoning 中并写入 trace。
- 与主 agent 共用同一个 LLM adapter / tool dispatch / token tracker / trace writer,不允许重复实现。
- 触发失败(网络 / LLM 错误)记录 `logger.error(...)`,不阻断主 agent 推理。

### F-008c Oracle Updater Sub-agent

**Status:** planned after F-008a

一个独立 sub-agent,在每局结束(`game_over` 或死循环终止)时启动,产出新版 `oracle.md`。

**Acceptance criteria**

- 触发时机:run 结束(任意原因)。
- 输入:整局 trace + 该局所有 skill creator 的 reasoning 摘要 + 上一版 `oracle.md`。
- 输出:覆盖写入 `oracle.md`。
- **软上限默认 4k tokens**;超出时 sub-agent 必须自行裁剪/总结,不允许直接超长写盘。该上限通过配置可调。
- 与主 agent / skill creator 共用同一基础设施。
- **不能修改 skill 库**。
- 失败时记录 `logger.error(...)` 并保留上一版 `oracle.md`。

### F-009 Live Context Viewer

**Status:** implemented

**动机:** 目前 Agent 运行时唯一的观察手段是事后翻 `steps.jsonl`。研究者需要在 run 过程中实时看到模型拿到的上下文,直观感受记忆系统的效果。

**Acceptance criteria**

- `slay2agent play --live` 启动时,在后台 daemon 线程启动本地 HTTP server,终端打印访问地址(如 `http://localhost:8765`);不加 `--live` 时行为与现有完全一致,零开销。
- 页面通过 SSE (`text/event-stream`) 接收实时事件,无需手动刷新。
- **主面板 — 对话流:** 每一步展示:
  - 当前 step 序号、`state_type`
  - User message(compact game state)完整内容
  - 模型的响应(选择的 tool call + 参数)
  - Tool 执行结果摘要(settle 后的 state 摘要)
- **侧边栏 — 记忆状态:**
  - Oracle:显示版本/长度摘要,点击展开查看完整 `oracle.md` 内容
  - Skill 列表:显示所有 skill 的 id + name,点击展开查看完整 body
- **记忆事件模块:** 两个小指示器分别对应 skill_creator 和 oracle_updater,触发时高亮并显示简要结果(如"updated skill: xxx"或"oracle rewritten")。
- **Token 用量模块:** 显示三类 agent 的累计 input/output token,每 60 秒自动更新。
- 对 `loop.py` 的侵入最小化:通过 observer 协议解耦,loop 只在关键节点调用 `observer.on_xxx()`,不改变核心逻辑。
- **零新依赖:** HTTP server 用 Python stdlib (`http.server` + `threading`),前端用原生 `EventSource` + vanilla JS,前端为项目内单个 HTML 文件。
- 一次只支持一个 run 的实时查看。

**不做:**

- 不做事后回放 / trace 文件加载。
- 不做多 run 并发查看。
- 不做前端 build pipeline(无 npm/node)。
- 不做用户从浏览器操控 Agent。

## Open Questions

- skill metadata 是否需要在 `name` + `description` 之外再加 `examples` / `tags` / `applicable_state_types` 等结构化字段 → v1 起对齐 mainstream,只保留 `name` + `description`(`description` 自带 "use when ..." 触发条件);后续若主 agent 召回不稳定,再考虑加结构化字段。
- loop detector 的 N 与阈值默认值 → F-006 实施后用实际 trace 估计。
- skill creator 是否需要"建议"边界(每小关最多写 N 个 skill)→ 首版默认不限,F-008b 跑过后再评估。
- `oracle.md` 4k tokens 软上限是否合理 → 阶段二第一次跑通后回看 token 占比再调整。
- skill creator 与 main agent 之间的同步关系(同步阻塞 vs 异步限时)→ 首版同步阻塞,后续看延迟数据决定。