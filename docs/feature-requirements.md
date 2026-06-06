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
  - **L1 skill 库**(metadata 强插 system prompt + 模型主动 read body;**推理期只读**,由离线 CLI 流水线按完整轨迹维护 —— 见 F-013)
  - **L2 `oracle.md`**(强插 system prompt,run 结束时由独立 sub-agent 重写)
- 主 agent、oracle updater(run 末)、离线 skill 维护流水线(CLI)**共用底层基础设施**(LLM client、tool dispatch、token tracking、trace writer)。
- 每次 run 结束输出各 agent 角色各自的 input/output token 量。
- 维护 `docs/memory-iteration-log.md` 记录 memory 设计每次迭代。

### Non-goals

- 不训练、微调、RLHF、本地 GPU 推理。
- 不读取画面、不模拟键鼠。
- 不追求胜率 / Act 通关进度作为成功条件。
- 不做横向对照 baseline(无 memory-off 对照、无人类对比、无外部 bot 对比)。
- 不做自动开新 run、不做菜单跨局自动重启,memory dir 不内置版本切换(用户用 git 管理)。
- 不做批量 eval / replay 工具 / LLM 自动改写 prompt 模板。
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

- 每步至少记录:`step`、`timestamp`、`state_type`、L0 是否在此步被清空、注入到 system prompt 的 skill metadata 列表 + `oracle.md` 版本标识、主 agent 完整 LLM request/response、被调用的 tool 与参数、被执行动作的原始返回/报错串(`action_feedback`,供 F-013 离线轨迹重建用,使每步自描述)、settle 后的新 state 摘要。
- 每次 sub-agent 触发(skill creator / oracle updater)记录:触发原因、输入摘要、完整 LLM request/response、产生的文件级修改 diff(skill 文件增删改,或 `oracle.md` 重写)。
- run 结束后输出 `runs/<run_id>/summary.json`,包含终止原因(`game_over` / `loop_terminated` / `error`)、三类 agent 各自 input/output token 总量、调用次数。
- trace 必须能被人工或脚本以 jsonl/json 形式直接读取,不依赖运行时数据库。

### F-008a Skill Registry + Read Tool

**Status:** planned

主 agent 侧的 skill 系统:metadata 强插,正文按需 read。

**Acceptance criteria**

- skill 以文件形式存放在固定 memory dir(具体路径由 framework-design 定义),格式对齐 mainstream Claude Code / Cursor `.cursor/skills/*/SKILL.md` 约定。
- 每个 skill 文件 = YAML frontmatter (`name` + `failure_reason` + `description`) + 自包含的 markdown body(具体细节)。`description` 是注入主 agent 的唯一触发信号,必须同时表达"做什么"与"何时使用"(pattern: `<summary>. Use when <trigger>.`)。`failure_reason` 仅供 F-013 离线流水线创建/改进时比对去重用,**不注入 play-time system prompt**,主 agent 选 skill 只看 `description`。
- 主 agent 每步 system prompt 强制注入:全部 skill 的 `description` 列表 + 当前 `oracle.md` 全文。
- 主 agent 暴露两个 memory 工具:`list_skills()`、`read_skill(skill_id)`。**无 write、无 python exec、无 compact**;推理期 skill 库只读。
- skill 文件格式应允许人工编辑,以便研究者直接调整。
- skill 库为空 / `oracle.md` 为空时主 agent 仍能正常决策。

### F-008b Skill Creator Sub-agent

**Status:** superseded by F-013（v3 重构）

> **已废弃。** 原设计在每次 `state_type` 切换边界触发 sub-agent 维护 skill 库,实测触发频率过高(一局 60–100+ 次)、粒度过细,导致 skill 数量爆炸(motivation 见 `memory-iteration-log.md` v3)。skill 维护改为 **F-013 离线两阶段 CLI 流水线**,基于完整轨迹而非单段 L0。原 `skill_creator`(边界触发)、`skill_librarian`(run 末去重)、LRU skill cache 全部移除。

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

### F-010 Provider-Agnostic LLM Config (OpenAI-Compatible Adapter)

**Status:** planned

**背景:** 当前 `OpenRouterAdapter` 内部已经使用 `openai` Python SDK，以 `base_url="https://openrouter.ai/api/v1"` 指向 OpenRouter。本 feature 将 `base_url`、`api_key`、`extra_headers` 参数化，使同一适配层可以无代码变更地切换到任意 OpenAI-compatible endpoint（OpenAI 原生 / vLLM / DeepSeek / Together AI 等）。

**Acceptance criteria**

- 新类 `OpenAICompatibleAdapter(model, api_key, base_url, *, extra_headers=None, timeout=120.0)` 替换 `OpenRouterAdapter`；对 `LLMAdapter` ABC 的实现保持不变。
- 当 `base_url` 包含 `openrouter.ai` 时，自动注入 `HTTP-Referer` 与 `X-Title` extra headers；其他 provider 不注入。
- 配置层 env vars 改为 provider-agnostic 命名：`LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` / `LLM_TIMEOUT`。旧 `OPENROUTER_*` 系列 **不再支持**（breaking change）。`.env.example` 同步更新，注释说明 OpenRouter 与 OpenAI 原生的配置示例。
- `LLMConfig.require_api_key()` 的错误消息改为引用 `LLM_API_KEY`。
- `loop.py` / `skill_creator.py` / `oracle_updater.py` / `smoke.py` / `cli.py` 的 adapter 类型标注全部改为 `LLMAdapter`（ABC），**`llm/` 包外不允许 import 任何具体 adapter 类**。
- `slay2agent smoke` CLI 的帮助文字和环境变量检查使用新 env var 名。
- 测试文件 `tests/test_openrouter.py` 重命名为 `tests/test_openai_compat.py`，import 路径和测试逻辑对齐新类名；`tests/test_config.py` 更新为 `LLM_*` env vars。
- 无新增 Python 依赖（`openai` SDK 已在 `pyproject.toml` 中）。
- 所有现有离线测试（mock/fixture，不依赖真实 API key）继续全绿。

### F-011 UnknownView Raw Payload Exposure + Issue Logging

**Status:** planned

**动机:** `UnknownView` 当前只输出 `Top-level fields: xxx`，agent 完全看不到实际游戏数据（如 crystal_sphere 的格子状态），导致在未适配 state_type 上盲人摸象。

**Acceptance criteria**

- `_render_unknown` 将 `view.payload` 序列化为 JSON 并包含在 compact prompt 中，截断上限 3000 chars 防止 prompt 爆炸。
- 每当 agent 首次进入一个走 `UnknownView` 的 state_type 时，自动记录一条 issue 到 `issues.jsonl`，字段包含：`run_id`、`step`、`state_type`、`payload_keys`、timestamp。
- issue 目的：暴露哪些 state_type 需要后续补专用 View，方便研究者事后排查。
- 不影响已有专用 View 的 state_type 渲染逻辑。

### F-012 L0 Compaction Sub-agent

**Status:** planned

**动机:** 同一 state_type 段落内 L0 无上限增长导致 O(n²) token 爆炸（实测 crystal_sphere 232 步烧掉单次 run 75% 的 input token）。滑动窗口会破坏 KV cache 命中率（前缀每步都变），因此改用 compaction sub-agent：在 L0 达到阈值时，由独立 sub-agent 将旧历史压缩为一条摘要 message，保持前缀稳定。

**Acceptance criteria**

- 当 L0 消息数超过可配置阈值（默认 `L0_COMPACT_THRESHOLD=30` 条 message）时，触发 compaction。
- Compaction 由一个新 sub-agent（`role="compactor"`）执行：输入为当前 L0 全文，输出为一条摘要 message。
- 摘要替换 L0 中最老的 N 条 message，保留最近 K 条原文不动（K 可配置），使得最近的上下文完整、远端上下文被压缩。
- 压缩后的 L0 结构：`[summary_message] + [最近 K 条原文]`——前缀稳定，KV cache 可复用。
- Compactor 共用基础设施（LLM adapter / token tracker / trace writer），token 计入独立 role `"compactor"`。
- 触发失败不阻断主 agent（记录 `logger.error(...)`，保留原始 L0 继续运行）。
- 可通过配置关闭（`L0_COMPACT_ENABLED=false`），默认开启。
- compaction 事件写入 trace（`subagent.jsonl`），包含压缩前后 message 数、摘要内容。

### F-013 Offline Skill Maintenance Pipeline（v3 重构）

**Status:** planned（取代 F-008b）

**动机:** 旧 `skill_creator` 在每个 `state_type` 边界触发,一局 60–100+ 次、粒度过细,加上 run 末单次 `skill_librarian` 去重压不住,导致 **skill 数量爆炸**。本 feature 把 skill 的创建/总结从"推理期实时、按小关片段"重构为"**离线、CLI 手动触发、按完整轨迹的两阶段流水线**",并使推理期 skill 库完全只读。详见 `memory-iteration-log.md` v3。

**Acceptance criteria**

*推理期(play)行为变化:*

- 移除 `state_type` 边界触发的 `skill_creator`、run 末的 `skill_librarian`、两级 LRU skill cache(`skill_cache.py`)。play 过程对 skill 库**零写入**。
- play 时 system prompt **全量注入所有 skill 的 `description`**(不再分级);`list_skills` / `read_skill` 懒加载 body 不变。
- `oracle_updater`(F-008c,run 末)行为保持不变。

*轨迹存储与重建(确定性):*

- 每次 run 的完整上下文轨迹持久化在 `runs/<run_id>/steps.jsonl`(已有);新增 `StepRecord.action_feedback` 字段(F-007 amendment),记录被执行动作的原始返回/报错串。
- 提供**确定性**的轨迹重建:固定代码逻辑,仅从 `steps.jsonl` 顶层字段(`state_type` / `tool_name` / `tool_args` / `action_feedback` / `settled_state_summary`)投影出"动作 → 反馈 → 结果状态"序列。不调用 LLM、不重新解析游戏 JSON、不依赖 `llm_request_messages`(因而免疫 L0 compaction)。

*阶段1 — 失败分析(`slay2agent analyze`):*

- 扫描 `runs/` 下所有**尚未生成** `failure_report.json` 的 run,逐条分析(不纠结输赢,每条都复盘)。
- 每条产出 `runs/<run_id>/failure_report.json`(JSON,不加分类/标签字段):分点列出若干失败原因,每条含失败原因描述 + 对应轨迹片段(step 区间 + 摘录)。
- 模型默认取 env 中正在使用的模型(`LLM_MODEL`)。
- `failure_report.json` 存在 = 该 run 已分析(再次运行跳过)。

*阶段2 — skill 蒸馏(`slay2agent distill`):*

- 取所有**尚未被处理**(报告中无 `distilled_at`)的 `failure_report.json`,分两个 **context 隔离** 的子步骤:
  - **2a 聚类**:输入仅为各报告的失败原因,产出"相似且高频"的共性失败原因组(是否共性/高频完全由 LLM 判断,无固定 N 阈值)。该 context 不含 skill 库现状。
  - **2b 蒸馏**:对每个共性失败原因组开**独立 context**,喂该组失败原因 + 对应轨迹片段 + 现有 skill 的 `failure_reason` + `description`(允许 `read_skill` 读 body),由 LLM 判定"新建 skill"或"覆盖改进某条已有 skill"。改进 = 整文件覆盖重写。
- 产出/改进的 skill 遵循严格 template:`failure_reason`(针对的失败原因)+ `description`(apply when 触发条件)+ body(具体细节)。
- 处理完的报告回写 `distilled_at` 字段(再次运行跳过)。

*共用基础设施 & 错误处理:*

- analyze / distill 与主 agent 共用同一 LLM adapter / tool dispatch / token tracker。
- 单条 run 分析失败 / 单个失败原因组蒸馏失败时记录 `logger.error(...)`,跳过该条继续处理其余,不让整批中断。

## Open Questions

- skill metadata 是否需要在 `name` + `description` 之外再加 `examples` / `tags` / `applicable_state_types` 等结构化字段 → v1 起对齐 mainstream,只保留 `name` + `description`(`description` 自带 "use when ..." 触发条件);后续若主 agent 召回不稳定,再考虑加结构化字段。
- loop detector 的 N 与阈值默认值 → F-006 实施后用实际 trace 估计。
- ~~skill creator 是否需要"建议"边界(每小关最多写 N 个 skill)~~ → v3 已移除边界触发,skill 由 F-013 离线流水线维护,该问题作废。
- `oracle.md` 4k tokens 软上限是否合理 → 阶段二第一次跑通后回看 token 占比再调整。
- ~~skill creator 与 main agent 之间的同步关系~~ → v3 已移除推理期 skill 维护,该问题作废。
- (F-013) 阶段2a 聚类的批大小与"高频"判定全交 LLM 是否稳定 → 跑过若干批后回看,必要时再引入显式阈值。