# slay2agent

一个 **train-free** 的 Agent 框架，目标是驱动云端 LLM 自主通关 *Slay the Spire 2*。

不训练任何模型、不读屏幕、不模拟键鼠。游戏状态与动作只走 [STS2MCP](https://github.com/Gennadiyev/STS2MCP) mod 暴露的本地 REST 接口，决策只走云端 LLM API（首版走 OpenRouter）。

## 功能概览

- **端到端 demo loop**：`slay2agent play` 从 main menu 自动驱动到 `game_over`，无需人工干预
- **OpenRouter LLM 适配**：retry + per-`(role, model)` token 统计
- **STS2MCP REST 通路**：`GameClient` + 28 个声明式 action schema + state-type gate
- **紧凑 state 视图**：按 `state_type` 分发渲染，压到 < 700 字符喂 LLM
- **三层 memory**：L0（in-context history）+ L1（`agent_state/skills/*.md`）+ L2（`agent_state/oracle.md`）
- **两个 sub-agent**：`skill_creator`（state_type 切换边界）+ `oracle_updater`（run 结束）自动维护长期记忆
- **完整 trace**：`runs/<run_id>/{steps.jsonl, subagent.jsonl, summary.json}` + memory 快照

完整需求与架构见 [`docs/feature-requirements.md`](./docs/feature-requirements.md) 与 [`docs/framework-design.md`](./docs/framework-design.md)。LLM 适配层细节见 [`docs/llm-adapter.md`](./docs/llm-adapter.md)。Memory 设计的演化记录见 [`docs/memory-iteration-log.md`](./docs/memory-iteration-log.md)（建立中）。

## 前置条件

| 依赖 | 用途 | 是否必需 |
|---|---|---|
| Python **3.11+**（`.python-version` 已钉） | 运行 Agent | 必需 |
| [`uv`](https://docs.astral.sh/uv/) | 依赖管理 / 运行入口 | 必需 |
| [OpenRouter](https://openrouter.ai) API key | 云端 LLM 调用（Claude / GPT / Gemini / DeepSeek …） | 跑 `smoke` / `play` 必需 |
| *Slay the Spire 2* 客户端 | 真实游戏运行环境 | 跑 `inspect` / `play` 必需 |
| [STS2MCP](https://github.com/Gennadiyev/STS2MCP) mod（运行在游戏进程内） | 暴露 game state / action 的本地 REST 服务 | 跑 `inspect` / `play` 必需 |
| GPU / 本地模型 | — | **不需要**，本项目不做本地推理 |

> Agent 通过 STS2MCP 的本地 REST 端点（`STS2MCP_BASE_URL`）读 state、发 action；LLM 决策走 OpenRouter。两个服务必须分别可达。

## 快速开始

```bash
# 1. 装依赖（在仓库根目录执行）
uv venv --python 3.11
uv pip install -e ".[dev]"

# 2. 配置密钥
cp .env.example .env
# 用编辑器填入 OPENROUTER_API_KEY，按需调 OPENROUTER_MODEL / STS2MCP_BASE_URL

# 3. 离线自检（不需要 key / 不需要游戏）
uv run pytest -q

# 4. LLM 链路冒烟（需要 OPENROUTER_API_KEY）
uv run slay2agent smoke

# 5. STS2MCP 通路自检（需要游戏 + mod 启动并停在 main menu）
uv run slay2agent inspect --health
uv run slay2agent inspect

# 6. 端到端跑一局
uv run slay2agent play
```

`play` 结束后会在 `runs/<run_id>/` 留下完整 trace + memory 快照（见下文「运行产物」）。

## 配置

所有运行时配置都从环境变量读取，可通过 `.env`（在 `.gitignore`）注入：

```bash
cp .env.example .env
```

可用变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `OPENROUTER_API_KEY` | 无 | 必填，`sk-or-v1-...`。 |
| `OPENROUTER_MODEL` | `openai/gpt-4o-mini` | 任意 OpenRouter slug。 |
| `OPENROUTER_TIMEOUT` | `120` | 单次 LLM 请求秒数。 |
| `STS2MCP_BASE_URL` | `http://127.0.0.1:15526` | STS2MCP mod 监听的 base URL（端口对齐上游默认）。 |
| `STS2MCP_TIMEOUT` | `30` | 单次 REST 请求秒数。 |
| `AGENT_STATE_DIR` | `agent_state` | skill 库 + oracle 的根目录。 |
| `ORACLE_MAX_TOKENS` | `4000` | oracle_updater 写 `oracle.md` 的软上限。 |

查看当前生效配置（密钥默认 mask）：

```bash
uv run slay2agent config
```

> **安全提示**：key 只放 `.env` 或本地 shell，**不要**写进 tracked 文件、测试或 commit。如不慎泄露，到 OpenRouter 控制台立即 revoke。

## CLI

安装后 `slay2agent` 命令可用，也可以 `uv run python -m slay2agent.cli`：

```bash
uv run slay2agent --help

uv run slay2agent config            # 打印当前配置（密钥 mask）
uv run slay2agent smoke             # OpenRouter 链路冒烟
uv run slay2agent smoke --model anthropic/claude-sonnet-4

uv run slay2agent inspect           # 打印 STS2MCP 当前 game state
uv run slay2agent inspect --health  # 仅打印 STS2MCP `/` 健康响应

uv run slay2agent play              # 端到端跑一局：main menu → game_over
```

`play` 的可选参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--character` | `IRONCLAD` | 角色 id（大写）。 |
| `--ascension` | `0` | Ascension 等级。 |
| `--runs-dir` | `runs` | trace 输出目录。 |
| `--window-size` | `12` | loop detector 滑窗大小。 |
| `--repeat-threshold` | `6` | 同 `(action, args)` 在窗口内的重复触发阈值。 |

跑之前**游戏必须停在 main menu**——`play` 启动后会自动从 main menu → singleplayer → standard → 选角 → embark，然后才把方向盘交给 LLM。

## 项目结构

```
.
├── src/slay2agent/
│   ├── cli.py                # 入口：config / smoke / inspect / play
│   ├── config.py             # env → dataclass 配置
│   ├── llm/                  # OpenRouter 适配 + retry + UsageTracker
│   ├── game/                 # STS2MCP client + 28 action schema + 状态解析
│   ├── memory/               # skill registry + oracle 读取
│   └── agent/                # 主 loop + tool bridge + skill_creator + oracle_updater + trace
├── tests/                    # 233 个离线单测，全 fixture 驱动
├── docs/                     # 需求 / 架构 / LLM 适配 / 实施进度
├── .env.example              # 复制为 .env 后填 key
├── agent_state/              # （本地，gitignored）skill 库 + oracle.md，跨 run 累积
├── runs/                     # （本地，gitignored）每局 trace + memory 快照
└── pyproject.toml            # 依赖 + 入口
```

## 运行产物

### 每局 trace：`runs/<run_id>/`

`<run_id>` 形如 `20260511T013704_16d632ec`（`time + uuid8`）。每次 `slay2agent play` 都会创建一个新目录：

```
runs/<run_id>/
├── steps.jsonl                  # 主 agent 每步一行：state / LLM 请求响应 / tool call / 落地 state
├── subagent.jsonl               # skill_creator / oracle_updater 每次调用一行
├── summary.json                 # 终止原因 + 三类 agent 的 token 总计 + per-(role, model) 用量
└── agent_state_snapshot/        # ← 跑完之后，oracle/skill 的当时快照（详见下文）
    ├── oracle.md
    └── skills/*.md
```

### Memory：`agent_state/`

```
agent_state/
├── oracle.md                    # L2：全局 meta-strategy，每次 run 结束被 oracle_updater 覆盖
└── skills/<skill_id>.md         # L1：state_type 切换边界由 skill_creator write/extend/merge
```

`agent_state/` 跨 run 持续累积，是 agent 长期记忆的物理载体。它**默认 gitignored**——研究迭代过程中希望版本可追溯时，看 `runs/<run_id>/agent_state_snapshot/` 即可。

### 看 oracle 怎么迭代

每跑完一次 `play`，`oracle_updater` 写完 `agent_state/oracle.md` 后，`agent_state/` 会被整个复制到 `runs/<run_id>/agent_state_snapshot/`。这样每条 run 都自带一份 immutable 的 memory 快照，可以按时间排序 diff：

```bash
# 按时间列出所有 snapshot
ls -1d runs/*/agent_state_snapshot/oracle.md | sort

# 两两 diff
diff runs/<old_run_id>/agent_state_snapshot/oracle.md \
     runs/<new_run_id>/agent_state_snapshot/oracle.md

# skill 同理
diff runs/<old>/agent_state_snapshot/skills/ironclad_early_combat.md \
     runs/<new>/agent_state_snapshot/skills/ironclad_early_combat.md
```

Memory **设计层面**（schema / prompt / 触发时机 / 工具集）的演化由 [`docs/memory-iteration-log.md`](./docs/memory-iteration-log.md) 手动记录；上面这套快照只跟踪**内容**演化。

## 测试

```bash
uv run pytest -q              # 233 个离线单测
uv run pytest tests/test_trace.py -v   # 只跑某个模块
```

离线单测全绿即视为协议层 / state parser / tool bridge / trace writer / skill registry 都 OK。

LLM 链路 / 真实游戏链路必须分别用 `slay2agent smoke` 和 `slay2agent inspect` 验证。

## 常见问题

- **`OPENROUTER_API_KEY is not set`**：`.env` 没建，或者建了但变量名拼错。`uv run slay2agent config` 会显示 `api_key = <unset>`。
- **`inspect` 报 connect refused**：STS2MCP mod 没启动，或者 `STS2MCP_BASE_URL` 端口对不上。默认 `15526`，与上游 mod 一致。
- **`play` 启动后没动作**：游戏不在 main menu。`play` 只在 `state_type == "menu"` 时自动走开局导航，否则直接把当前局交给 agent。
- **`play` 跑了一半被 loop detector 终止**：窗口内同一个 `(action, args)` 重复了 `--repeat-threshold` 次（默认 6 次/12 步）。如果策略性重复（如多回合同 attack）被误杀，调大 `--repeat-threshold`；如果 agent 真的卡死，看 `runs/<run_id>/summary.json` 里的 `loop_detail` 复盘。
- **agent token 用量超预期**：`summary.json` 里 `tokens.<role>` 三块分别是 `main` / `skill_creator` / `oracle_updater`。oracle_updater 受 `ORACLE_MAX_TOKENS` 限制；skill_creator 每个 state_type 切换都跑一次，频率高 token 多属正常。
