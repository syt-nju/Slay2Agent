# slay2agent Feature Requirements

本文档是 slay2agent 的需求来源。实现顺序可调整,但需求变更需要先在对话中确认。

## Final Goal

slay2agent 的最终目标是构建一个 train-free、仅依赖 STS2MCP JSON/REST 与云端 LLM API 的 Slay the Spire 2 Agent。

第一阶段先实现能自主跑完整局的最小闭环;第二阶段通过 trace、评估指标和 memory 系统,让 Agent 能基于历史经验持续提升胜率与 token 效率,并用可度量结果验证改进。

## Scope

### Goals

- 使用 STS2MCP mod 作为唯一游戏感知与执行底层。
- 所有 LLM 调用走云端 API,并通过统一适配层接入。
- 初期先支持单人、单角色、手动开局后 Agent 接管。
- 先做到完整 run 可跑通,再做 memory 驱动的表现提升。
- 用 trace 与评估指标验证胜率和 token 效率变化。

### Non-goals

- 不训练、微调、RLHF 或使用本地 GPU 推理。
- 不自建视觉模型,不模拟键鼠输入。
- 初期不承诺高 Ascension、多角色全覆盖或批量自动开局。
- 不在首版实现 LLM 自动改写 Python skill 代码。

## Features

### F-001 Runtime Boundary and Configuration

**Status:** planned

系统必须明确运行边界:只接 STS2MCP REST,只使用云端 LLM API,并通过本地配置提供模型、endpoint、API key 和运行参数。

**Acceptance criteria**

- 项目文档说明 STS2MCP、游戏进程和云端 LLM API 的前置条件。
- 本地配置不要求 GPU 环境。
- 敏感信息不提交到仓库,示例配置只包含占位值。
- CLI 至少能暴露后续 inspect/run/smoke 所需的配置入口。

### F-002 LLM Adapter

**Status:** implemented for OpenRouter baseline

所有 LLM 调用必须通过 provider-agnostic 适配层。首版支持 OpenRouter,统一 chat、tool call、usage、retry 和错误分类。

**Acceptance criteria**

- 上层只依赖 canonical request/response 类型,不直接依赖 provider wire format。
- 支持 text response、tool calls、stop reason 和 token usage。
- transient 错误可重试,不可重试错误可分类返回。
- usage 按模型分桶记录 token,不在首版做价格计算或预算熔断。
- 离线测试覆盖协议类型、错误分类、retry 和 usage。

### F-003 Game Communication Path

**Status:** planned

系统需要打通 STS2MCP REST 通路,提供薄封装 client、action 调用和动作后 settle 机制。

**Acceptance criteria**

- `get_state` 能读取当前游戏 JSON state。
- `post_action` 能调用 STS2MCP action 并返回响应。
- action 封装与 STS2MCP 暴露的工具签名保持一致。
- action docstring 可作为 LLM tool 描述来源。
- 动作后统一等待状态稳定,避免 end turn 后读取到旧状态。
- 超时或动作失败时记录 `logger.error(...)`,不静默吞掉异常。
- 有 fixture 驱动测试覆盖至少 5 个典型 action 的请求/响应往返。
- 有手动 inspect 命令用于在 mod 运行时打印当前 state。

### F-004 State and Action Domain Model

**Status:** planned

系统需要把原始 JSON state 转换为策略层可依赖的领域对象,并提供 prompt 压缩入口。

**Acceptance criteria**

- 使用 pydantic v2 建模主要 state_type。
- 策略层不直接依赖原始 dict。
- 暴露 Card、Enemy、Relic、Potion、MapNode 等领域对象。
- 提供 `to_compact_prompt()` 作为首版 prompt 输入。
- `diff(prev)` 延后到状态压缩阶段,不阻塞首版建模。
- 每个已知 state_type 至少有一个 fixture 解析测试。

### F-005 Minimal Runnable Agent Loop

**Status:** planned

系统需要先实现最小可跑 Agent:Perceive -> Execute -> Finalize。Plan 和 Reflect 在首版可以是空实现或直通。

**Acceptance criteria**

- Agent 能从当前游戏 state 开始循环决策。
- Execute 阶段能调用 LLM 并通过 tool bridge 执行动作。
- Agent 能跑完一整局真实游戏,胜负不作为首个通过条件。
- 运行期间若 loop detector 未触发终止,视为最小闭环通过。
- Finalize 能为每步写入结构化 trace。

### F-006 Skill Routing and Tool Bridge

**Status:** planned

系统需要按 state_type 分派 skill,并通过 tool bridge 限制 LLM 可执行动作。

**Acceptance criteria**

- 首版至少实现 combat、map、event、rewards 四类 skill。
- 未覆盖 state_type 走 fallback skill,不导致 Agent 崩溃。
- 每个 skill 声明允许使用的 action/tool。
- gate 根据当前 state_type 和 skill allowlist 筛合法 action。
- pre_execute 能拦截明显非法参数,如 card index 越界、target 不存在、能量不足。
- loop_detector 能识别连续相同 `(action, args)` 达到阈值的循环并终止。

### F-007 Trace, Metrics, and Baseline Evaluation

**Status:** planned

在 memory 优化前,系统必须先能记录 baseline 并计算可对比指标。

**Acceptance criteria**

- 每步至少记录 step、timestamp、state_type、LLM response 和 action。
- run 级指标至少包含是否完成、通关 Act、胜负、token usage。
- 支持统计平均 tokens per run 和平均 tokens per combat turn。
- 能从 trace 人工复盘 Agent 决策过程。
- Memory 改进前必须先保存一个可对比 baseline。

### F-008 Memory and Reflect Improvement Loop

**Status:** planned after F-007

系统需要在 trace/eval baseline 之后实现 memory 驱动的优化闭环。Memory 负责沉淀跨 episode 或跨 run 的经验;Plan/Reflect/playbook 负责把经验用于后续决策并评估效果。

**Acceptance criteria**

- Reflect 能从 episode 或 run 结果中生成可读经验记录。
- Memory 能持久化经验,并在后续决策前按当前 state/skill 读取相关内容。
- Skill-local 经验优先进入 playbook;跨 skill 或跨 run 经验进入 memory。
- Plan 阶段能读取 memory/playbook,形成当前回合或当前场景目标。
- 改进效果必须通过 F-007 指标与 baseline 对比验证。
- 如果 memory 未带来可度量提升,需要保留失败 trace 和分析记录。

### F-009 Deferred Extensions

**Status:** deferred

以下能力不进入首个可跑通版本,只在核心闭环稳定后评估:

- 批量 eval 和自动开局。
- replay 工具。
- 更多 LLM provider 原生适配。
- 多角色、多难度、高 Ascension。
- LLM 自动改写 prompt 或 skill 模板。

## Open Questions

- STS2MCP 当前版本是否提供自动开新 run 的接口?
- 首个支持角色是否固定为 Ironclad,还是以当前游戏状态为准?
- F-007 baseline 至少需要多少个 run 才算可比较?
- F-008 memory 的最小可行存储格式是 markdown、JSONL、SQLite,还是向量索引?
- token 效率提升目标是否需要设定明确阈值,例如下降 40%?
- memory 改进以胜率优先,还是以 token 效率优先?
