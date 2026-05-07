# slay2agent 实施规划

本文档用于对齐 slay2agent 的实现路径与代码架构。作为活文档,随进度迭代。

## 1. 目标与非目标

**目标**
- 构建一个 **train-free** 的 Agent 框架,驱动现成 LLM 通关 *Slay the Spire 2*。
- 以 [STS2MCP](https://github.com/Gennadiyev/STS2MCP) 的 mod 作为感知/执行底层,**不自建视觉或输入模拟**。
- 通过 prompt、工具调用、记忆与反思等机制迭代胜率与 token 效率。

**非目标**
- 不做模型训练、微调、RLHF。
- 不自己逆向游戏、写视觉模型、模拟键鼠。
- 初期不追求 Ascension 高难或多角色全通,先把 Act 1–3 单角色基准跑通。

## 2. 整体架构

分层设计,自下而上:

```
┌──────────────────────────────────────────────────────────┐
│                   Evaluation / Replay                    │  离线评估与 trace 回放
├──────────────────────────────────────────────────────────┤
│                  Agent Orchestrator                      │  4-stage pipeline:
│   Perceive → Plan → Execute(ReAct) → Reflect → Finalize  │  借鉴 Memento-Skills 抽象
│                                                          │
│   ┌─────────────┐    ┌──────────────────────────────┐    │
│   │ Skill       │←── │  Skill Library (本地)        │    │  state_type → skill 分派
│   │ Router      │    │  combat / map / event / ...  │    │  每个 skill 自带 playbook
│   └─────────────┘    └──────────────────────────────┘    │
│          ↓                                               │
│   ┌─────────────────────────────────────────────────┐    │
│   │ Tool Bridge: gate / pre_execute / recovery /    │    │  action 调用安全层
│   │             loop_detector                      │    │
│   └─────────────────────────────────────────────────┘    │
├──────────────────────────────────────────────────────────┤
│               LLM Adapter (provider-agnostic)            │  统一 chat / tool_call / token 计费接口
├──────────────────────────────────────────────────────────┤
│      State Model   │   Action Layer   │   Prompt Lib     │  领域对象与提示模板
├──────────────────────────────────────────────────────────┤
│                  Game HTTP Client                        │  对 STS2MCP REST 的薄封装
├──────────────────────────────────────────────────────────┤
│         STS2_MCP.dll  (localhost:15526 REST API)         │  外部依赖,不在本仓库
└──────────────────────────────────────────────────────────┘
```

核心原则:
- **Client 层**只做 HTTP 与序列化,不带任何策略。
- **State / Action 层**把原始 JSON 转成类型化对象,屏蔽 mod 的 wire format 变化。
- **Agent 层**借鉴 Memento-Skills 的 4-stage + Skill library + Tool bridge 抽象,skill 是一等公民,所有 action 调用走 bridge 做安全检查。
- **Skill-specific 经验走 playbook**,跨 skill/跨 run 的记忆由独立 memory 模块承担(待外部参考)。
- **LLM Adapter** 统一所有云端 API 调用,支持 Anthropic / OpenAI 等 provider 切换(不走本地推理)。

## 3. 代码结构

目录先按"单文件起步,长过 400 行再拆"的原则列。✅ = 已落地;其余是规划。

```
slay2agent/
├── main.py                         # CLI 入口(首版只做 `run`,其它子命令待定)
├── pyproject.toml                  # ✅
├── plan.md                         # ✅
├── README.md                       # ✅
├── .env.example / .env             # ✅
├── src/slay2agent/
│   ├── __init__.py                 # ✅
│   ├── config.py                   # LLMConfig + 其它配置(按需加)
│   │
│   ├── llm/                        # ── LLM 适配层 ──
│   │   ├── protocol.py             # ✅ canonical 类型 + LLMAdapter ABC
│   │   ├── errors.py               # ✅ 错误分类 + classify()
│   │   ├── retry.py                # ✅ jittered_backoff + call_with_retry
│   │   ├── usage.py                # ✅ UsageTracker(只记 token,不算价)
│   │   ├── openrouter.py           # ✅ OpenRouterAdapter
│   │   └── smoke.py                # ✅ live 冒烟脚本
│   │
│   ├── game/                       # ── 与 STS2MCP mod 通信 ──
│   │   ├── client.py               # HTTP client:get_state / post_action
│   │   ├── actions.py              # 38 个 action 的 Python 封装
│   │   ├── schema.py               # pydantic 模型(按 state_type discriminated union)
│   │   └── settle.py               # 动作后轮询至状态稳定
│   │
│   ├── agent/                      # ── 决策核心 ──
│   │   ├── orchestrator.py         # 4-stage pipeline 主控
│   │   ├── phases.py               # perceive / plan / execute / reflect / finalize(单文件)
│   │   ├── skills.py               # Skill 基类 + router + 全部 skill 实现(单文件起步)
│   │   ├── playbook.py             # skill-specific 经验的读写
│   │   ├── tool_bridge.py          # gate / pre_execute / recovery / loop_detector(单文件)
│   │   └── types.py                # Agent/Skill/Tool 之间的 DTO(与 llm/protocol.py 区分)
│   │
│   # memory/ ── 跨 skill / 跨 run 的记忆系统:待外部参考后补齐 ──
│   │
│   ├── prompts/                    # system / rules / heuristics(md 资源 + 加载器)
│   ├── trace.py                    # 每步结构化日志落盘(从简)
│   └── eval/                       # 评估:metrics.py(必做) + replay.py(TBD)
│
└── tests/
    ├── test_client.py / test_schema.py / test_agent.py ...  # 按文件匹配单测
```

拆分原则:任一 `.py` 超过 ~400 行或出现 3+ 大类不同职责时再拆子目录。

## 4. 关键模块设计要点

### 4.1 Game Client (`game/client.py`, `game/actions.py`)
- 对齐 STS2MCP 的 `/api/v1/singleplayer` REST 接口;初期只做单人。
- `get_state(format="json") → dict`、`post_action(name, **kwargs) → dict`。
- `actions.py` 按 `server.py` 里 MCP 工具的签名一一对应,生成 Python 函数 +  docstring。这些 docstring 后面直接喂给 LLM 作为工具说明。

### 4.2 State Model (`game/schema.py`)
- 用 `pydantic` 建模,顶层按 `state_type` 做 discriminated union。
- 暴露领域对象(`Card`、`Enemy`、`Relic`、`Potion`、`MapNode`……),策略层只依赖这些对象,不直接碰 dict。
- `to_compact_prompt()` 是 M2 必做;`diff(prev)` 延后到 M4 状态压缩阶段。

### 4.3 Settle 机制 (`game/settle.py`)
- STS2MCP 的 `AGENTS.md` 明确:end_turn 后可能要再调一次 `get_state` 才能拿到新手牌。
- 所有 action 调用后,统一走 `execute_and_settle()`:轮询 state,直到 `is_play_phase=true` 或 `state_type` 变化,或超时。
- 超时时 `logger.error(...)` 记录原 state 和最后响应,不静默吞掉。

### 4.4 LLM Adapter (`llm/`) — ✅ 已落地

- 所有 LLM 调用走 **云端 API**(无本地 GPU 环境),必须通过适配层。
- 统一接口:`chat(messages, tools, ...) -> LLMResponse`(含 text、tool_calls、usage、stop_reason)。
- 实现层隔离 provider 差异,上层只认 canonical dataclass(OpenAI-style 的扁平 `tool_calls`)。
- 首版只实现 `OpenRouterAdapter`(OpenAI-compatible,一把 key 覆盖 Claude / GPT / Gemini / DeepSeek 等 200+ 模型)。新 provider = 新增一个继承 `LLMAdapter` 的子类文件。
- `UsageTracker` 按模型分桶累加 tokens,**不算价、不熔断**。真需要预算护栏时再加。
- 详细设计见 [`docs/llm-adapter.md`](./docs/llm-adapter.md)。

### 4.5 Agent Orchestrator (`agent/orchestrator.py`)

借鉴 [Memento-Skills](https://github.com/Memento-Teams/Memento-Skills) 的 4-stage pipeline,但简化为适配固定 action 空间的版本。每次进入一个新的 state_type 都走一遍完整流水线:

```
while not terminal:
    view    = Perceive(state, prev_state)            # 解析 + diff + compact
    skill   = Router.route(view.state_type)          # 分派到 skill
    plan    = Plan(skill, view)                      # 战斗才需,其他可跳过
    episode = Execute(skill, view, plan)             # ReAct 多步,直到 step_boundary
    report  = Reflect(skill, episode)                # 归因 + 更新 skill.playbook
    Finalize(report)                                 # 结构化落盘
    state   = episode.terminal_state
```

- Execute 内部是 ReAct 循环:`LLM decide → tool_bridge → action → new_state`,由 `skill.step_boundary()` 决定何时结束。
- 与 Memento 不同:我们**不自动生成新 skill 代码**(action 空间固定),Reflect 只更新 playbook 文本和 utility 分数,不重写 Python。
- **为什么是 4 阶段**:Perceive + Execute + Finalize 是最小闭环(读状态、决策、落盘);Plan 把"回合目标"从 Execute 内部抽出来,只在战斗 skill 必需;Reflect 承载 skill-level 学习闭环。M3 骨架先只做 Perceive/Execute/Finalize 三阶段(Plan/Reflect 空实现直通),M5 再补齐——**不是一上来就 4 阶段**。

### 4.6 Skill 抽象 (`agent/skills.py`)

Skill 是可独立测试、可迭代的"场景决策程序",一等公民:

```
class Skill:
    id: str                      # "combat.default"
    triggers: list[StateType]    # ["monster", "elite", "boss"]
    prompt_template: str         # 含 heuristic,从 prompts/ 注入
    allowed_tools: list[str]     # tool_bridge.gate 用此做白名单
    step_boundary: fn(state)->bool   # 何时退出 skill
    evaluate:      fn(episode)->Report  # 打 utility 分 + 归因
    playbook:      Playbook      # skill-specific 经验,Reflect 阶段读写
```

初始 skill 与 state_type 的映射:

| Skill | 触发 state_type |
|---|---|
| `combat.default` | `monster` / `elite` / `boss` |
| `map.default` | `map` |
| `event.default` | `event` |
| `shop.default` | `shop` / `fake_merchant` |
| `rest.default` | `rest_site` |
| `rewards.default` | `rewards` / `card_reward` |
| `card_select.default` | `card_select` / `hand_select` / `bundle_select` / `relic_select` / `treasure` |

### 4.7 Tool Bridge (`agent/tool_bridge.py`)

LLM → action 的调用必须穿过 bridge,提供四道防护:

| 组件 | 职责 |
|---|---|
| `gate` | 按当前 `state_type` + `skill.allowed_tools` 筛合法 action |
| `pre_execute` | 参数校验(card_index 越界、target 不存在、能量不够) |
| `recovery` | action 失败/超时的降级处理(回滚状态 + 提示 LLM 重选) |
| `loop_detector` | 检测重复动作循环(反复查 state、反复同一无效 play),防止烧爆预算 |

`loop_detector` 对 StS2 尤其关键:一个 run 可能 8M tokens,卡住几十步就爆表。

### 4.8 Read-Execute-Reflect-Write 闭环

- **Episode 级**(一场战斗 / 事件 / 商店):Reflect 写回对应 skill 的 playbook(观察 → 教训),高频、本地。
- **Run 级**(一整局游戏):结束时做跨 skill 总复盘,写入跨 run memory(`memory/` 模块,待设计)。
- 划分线:skill-local 的经验走 playbook;跨 skill / 跨 run 的走 memory。前者初始可从 STS2MCP 的 AGENTS.md / docs/raw-\*.md 拆解灌入,后者 M6+ 再定。

### 4.9 记忆系统(占位)
- **本轮不设计**。跨 skill / 跨 run 的记忆等待外部参考项目确定后补齐 `src/slay2agent/memory/`。
- 在那之前,skill-specific 经验靠各 skill 的 playbook,近若干步的上下文靠 working context 直接拼 prompt。

### 4.10 Logging / Replay(Replay 为 TBD)
- **Logging 必做**:每一步写一条 JSON line。首版最小字段:`{step, ts, state_type, llm_response, action}` —— 5 个就够人工复盘;跑通一把再按需加 state_hash / tokens / skill_id 等。
- **Replay 工具**:读 trace 离线重跑 LLM 涉及 state 的纯函数性、LLM determinism 等问题,可行性 M3 之后再评估,先不承诺。

### 4.11 评估指标 (`eval/metrics.py`)
- 胜率 / 通关 Act / 平均 HP 保留率 / 平均 tokens per run / 平均 tokens per 战斗回合 / 平均 API 成本。
- 初期人工开游戏,后期如果 STS2MCP 有开局接口,再做批量 eval。

## 5. 实施里程碑

每个里程碑都产出可跑起来、可验证的东西。依赖链:**M0 → M1 → M2 → M3 → M4**(M5 可并行 M4 之后)。

### M0 — 骨架
- [x] 包结构 + `pyproject.toml` + `.env.example`/`.env` 指引。
- [x] **LLM 适配层**:`OpenRouterAdapter` + retry + usage,38 离线测试 + 2 在线冒烟全绿(详见 `docs/llm-adapter.md`)。
- [ ] **Mod 可达性冒烟**:`curl http://localhost:15526/` 能返回 200。此条需要本地装 STS2MCP mod,**不是 LLM 冒烟的前置**。

### M1 — 游戏通路打通
- 实现 `game/client.py`(get_state / post_action)+ `game/actions.py`(38 个 action 的 Python 封装,docstring 直接喂给 LLM)。
- **验收**:
  - `tests/test_client.py` 用 STS2MCP 样例 JSON fixture 覆盖 ≥ 5 个典型 action 的 request/response 往返,pytest 全绿。
  - 手动 `python -m slay2agent.game.client inspect` 能打印当前 state(需要 mod 跑起来)。

### M2 — 领域建模
- `game/schema.py` 用 pydantic discriminated union 覆盖所有 `state_type`。附 `to_compact_prompt()`(核心方法)。`diff(prev)` 延后到 M4 再做。
- 把 STS2MCP/docs/raw-\*.md + AGENTS.md 内容整理进 `prompts/`。
- **验收**:`tests/test_schema.py` 用 raw-\*.md 里每个 state_type 至少一个样例 JSON 做解析测试,全绿。

### M3 — 最小可行 Agent
- orchestrator 只做 Perceive → Execute → Finalize 三阶段;Plan / Reflect 是 `pass`。
- `skills.py` 先只做 `combat` / `map` / `event` / `rewards` 四个,其余状态走 `fallback.default`(直接让 LLM 看 state 决策)。
- `tool_bridge.py`:只实现 `gate`(按 state_type 过滤合法 action)+ `pre_execute`(参数校验)+ `loop_detector`(连发相同 `(action, args)` ≥ 5 次报错终止)。
- Trace 按 §4.10 最小 5 字段落盘。
- **验收**:
  - 能跑完一把真实游戏(手动开局,胜负不计);运行期间 loop_detector 未触发终止即通过。
  - trace 文件非空,可人工逐步复盘。

### M4 — 状态压缩
- 加 `schema.diff(prev)` + compact prompt,把单回合 prompt 降下来。
- **初步目标**:同一把游戏回放 tokens 较 M3 下降 ≥ 40%(跑到这里再按实际数据校准)。

### M5 — Plan + Reflect 补齐
- 战斗 skill 加 Plan 阶段(回合目标)。
- Reflect 阶段打 utility 分 + 归因,写回 `skill.playbook`。
- 把 AGENTS.md / raw-\*.md 拆解灌入各 skill 初始 playbook。
- **初步目标**:跑数场完整 run(具体数量依 API 成本决定),对比 M3 baseline 看胜率 / token 效率。

### M6+ — 跨 run 记忆 & 可选扩展
- 参考外部记忆项目后补齐 `memory/` 模块。
- 评估是否引入 Memento 式 "LLM 自动改写 skill.prompt_template"。
- 评估 replay 工具可行性。

## 6. 关键设计决策与权衡

| 决策点 | 选择 | 理由 |
|---|---|---|
| 感知方式 | 走 STS2MCP JSON state,不做 VLM | mod 已提供结构化状态,VLM 又慢又贵又不准 |
| 传输层 | 直接 HTTP,不走 MCP server | MCP 只是给 Claude Desktop 的胶水,我们自己的 loop 用 REST 更可控 |
| Agent 架构 | 4-stage pipeline + Skill library + Tool bridge,借鉴 Memento-Skills | 有清晰的 Read→Execute→Reflect→Write 闭环,skill 粒度利于独立迭代 |
| 是否自动生成 skill 代码 | **不做**,只迭代 playbook/prompt | StS2 的 action 空间固定在 38 个,不需要发明新工具 |
| Skill 检索 | 初期按 state_type 硬分发,不上 BM25/向量 | skill 数量 < 20,检索过度设计 |
| 记忆系统 | 本轮暂不设计;skill 内经验走 playbook,跨 skill/跨 run 的 memory 待外部参考 | playbook 够覆盖 episode 级,跨 run 留给专门 memory 项目参考 |
| LLM 接入 | 走云端 API,所有调用经统一 adapter | 无本地 GPU 环境;adapter 屏蔽 provider 差异 |
| LLM provider | 初版只接 OpenRouter | 一把 key 覆盖 Claude / GPT / Gemini / DeepSeek 等 200+ 模型,换模型零改代码;真需要 provider 原生特性(prompt caching / extended thinking)时再加原生 adapter |
| Replay 工具 | 先占位,实际实现待评估 | determinism 与 state 纯函数性存疑,不承诺 M3 前落地 |

## 7. 风险与未决问题

- **Mod 版本依赖**:以 STS2MCP 仓库 README 当前锁定的 STS2 版本为准(M1 接入时同步确认)。游戏更新可能破坏接口 → client 层单独隔离,便于替换。
- **不确定是否能通过 API 开新 run**(README/MCP tool list 里没看到 `start_new_run`)。需要在 M1 阶段验证;如不支持,批量评估需要人工介入或另寻 hook。
- **Token 成本高**(作者报告 Ironclad 单 run ~8M tokens),M4 的状态压缩是硬指标。
- **动画/settle 时序**细节多(结束回合、胜利结算、选牌弹窗),在 M1–M3 会持续出现边界 case,需依赖充分的 trace 日志排查。
- 用户环境无 GPU → 全部依赖云端 LLM API,所有调试需基于 log / trace,不走本地推理。

## 8. 外部依赖

- **运行时**:游戏进程 + STS2_MCP mod(需手动安装,详见 STS2MCP 仓库)。
- **Python 包**:
  - 已加入(`pyproject.toml`):`openai`(指向 OpenRouter 端点)、`python-dotenv`、`pytest`(dev)。
  - 待加入(M1/M2 需要):`pydantic>=2`(state 建模)、`httpx`(如 openai SDK 内置不够用时再显式加)。
  - 日志用标准库 `logging` 即可。
