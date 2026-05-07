# slay2agent Framework Design

本文档记录相对稳定的架构边界。任务拆解和执行进度放在 `docs/implementation-todo.md`。

## Overview

slay2agent 是一个 train-free 游戏 Agent。它不读取画面、不模拟键鼠、不训练模型,而是通过 STS2MCP REST API 读取结构化 state 并执行 action。决策由云端 LLM 完成,所有模型调用都经过统一 LLM adapter。

架构目标分两步:

1. 先打通一个能跑完整局的最小 Agent 闭环。
2. 再通过 trace、metrics、memory 和 reflect loop 提升胜率与 token 效率。

## Layers

```text
Evaluation / Trace
Agent Orchestrator
Skill Router + Skill Library
Tool Bridge
LLM Adapter
State / Action Domain Model
Game HTTP Client
STS2MCP REST API
```

## Core Abstractions

### Game Client

`game/client.py` 是 STS2MCP REST 的薄封装。它只负责 HTTP、序列化和错误暴露,不包含策略。

核心能力:

- `get_state(...) -> dict`
- `post_action(name, **kwargs) -> dict`
- 手动 inspect 入口

### Action Layer

`game/actions.py` 提供 STS2MCP action 的 Python 封装。action 函数签名和 docstring 是 LLM tool schema 的来源之一。

Action layer 不决定何时执行动作;合法性过滤和恢复交给 tool bridge。

### State Model

`game/schema.py` 把原始 JSON 转换为领域对象。Agent、Skill 和 Prompt 只依赖领域模型,不直接消费原始 dict。

首版提供:

- 按 `state_type` 区分的 pydantic 模型。
- 常用领域对象,如 Card、Enemy、Relic、Potion、MapNode。
- `to_compact_prompt()`。

`diff(prev)` 是状态压缩阶段的后续能力。

### LLM Adapter

`llm/` 统一云端模型调用。上层只使用 canonical dataclass,不感知 OpenRouter、OpenAI、Anthropic 等 provider 差异。

首版 provider 是 OpenRouter。usage 只记录 token,不计算价格,不做预算熔断。

### Agent Orchestrator

Agent 的长期形态是:

```text
Perceive -> Plan -> Execute -> Reflect -> Finalize
```

最小可跑版本先实现:

```text
Perceive -> Execute -> Finalize
```

Plan 和 Reflect 在 memory 阶段补齐。这样可以先验证游戏通路、LLM tool 调用和 trace,再让 memory 进入优化闭环。

### Skill Library

Skill 是按场景组织的决策单元。首版按 `state_type` 硬分派,不上 BM25 或向量检索。

初始 skill:

- `combat.default`
- `map.default`
- `event.default`
- `rewards.default`
- `fallback.default`

后续可补 shop、rest、card_select 等更细 skill。

### Tool Bridge

LLM 不能直接调用 game action,必须经过 tool bridge。

首版职责:

- `gate`: 根据 state_type 与 skill allowlist 筛合法 action。
- `pre_execute`: 检查参数和显然非法动作。
- `loop_detector`: 终止重复无效动作循环。

后续可加入 recovery 和更细的 settle-aware 修复策略。

### Trace and Metrics

Trace 是 memory 和评估的输入,因此早于 memory 实现。

首版每步至少记录:

- step
- timestamp
- state_type
- LLM response
- action

run 级 metrics 用于建立 baseline,并在 memory 阶段验证改进。

### Memory and Reflect Loop

Memory 是提升胜率和 token 效率的核心后续模块,但必须在 trace/eval baseline 之后实现。

职责划分:

- Reflect 从 episode/run 结果生成经验。
- Playbook 保存 skill-local 经验。
- Memory 保存跨 skill 或跨 run 的经验。
- Plan 在决策前读取 playbook/memory,形成当前目标。
- Metrics 对比 memory 前后的表现。

Memory 的存储格式、检索方式和更新策略仍是延后设计点。

## Data Flow

```text
get_state
  -> parse StateModel
  -> Perceive compact view
  -> route Skill
  -> Plan with memory/playbook when available
  -> LLM decide
  -> Tool Bridge gate/pre_execute
  -> post_action
  -> settle
  -> trace step
  -> Reflect and memory update when enabled
```

## Invariants

- Client 层不包含策略。
- 策略层不直接依赖 STS2MCP 原始 dict。
- 所有 LLM 调用经过 adapter。
- 所有 LLM action 调用经过 tool bridge。
- Memory 优化必须能用 baseline metrics 验证。
- 异常不能静默吞掉;使用 `except` 时必须记录 `logger.error(...)`。

## Deferred Decisions

- Memory 最小存储格式和检索策略。
- 是否需要 replay 工具。
- 是否支持自动开局和批量 eval。
- 是否引入 provider 原生能力,如 prompt caching 或 extended thinking。
- 是否允许 LLM 自动改写 prompt 或 skill 模板。
