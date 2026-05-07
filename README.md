# slay2agent

一个 **train-free** 的 Agent 框架,目标是驱动云端 LLM 自主通关 *Slay the Spire 2*。

不训练任何模型,不读屏幕,不模拟键鼠。游戏状态与动作只走 [STS2MCP](https://github.com/) mod 暴露的本地 REST 接口,决策只走云端 LLM API(首版 OpenRouter)。

## 现状

- [x] F-001 Runtime Boundary and Configuration:统一 CLI(`slay2agent` console script)+ Config 加载 + STS2MCP env 占位 + 前置文档
- [x] F-002 LLM Adapter:OpenRouter 适配 + 统一 retry / usage 链路(38 个离线测试)
- [ ] F-003 Game Communication Path
- [ ] F-005 Minimal Runnable Agent Loop
- [ ] F-007 Trace, Metrics, Baseline
- [ ] F-008 Memory and Reflect Loop

完整需求与架构见 [`docs/feature-requirements.md`](./docs/feature-requirements.md) 与 [`docs/framework-design.md`](./docs/framework-design.md)。LLM 适配层内部细节见 [`docs/llm-adapter.md`](./docs/llm-adapter.md)。

## 前置条件

| 依赖 | 用途 | 是否必需 |
|---|---|---|
| Python **3.11+**(`.python-version` 已钉) | 运行 Agent | 必需 |
| [OpenRouter](https://openrouter.ai) API key | 云端 LLM 调用(Claude / GPT / Gemini / DeepSeek …) | 运行 LLM 链路必需 |
| Slay the Spire 2 客户端 | 真实游戏运行环境 | `inspect` / `run` 必需 |
| STS2MCP mod(运行在游戏进程内) | 暴露 game state / action 的本地 REST 服务 | `inspect` / `run` 必需 |
| GPU / 本地模型 | — | **不需要**,本项目不做本地推理 |

> Agent 通过 STS2MCP 提供的本地 REST 端点(由 `STS2MCP_BASE_URL` 指定)读 state、发 action;LLM 决策走 OpenRouter。两个服务必须分别可达。

## 安装

```bash
uv venv --python 3.11
uv pip install -e ".[dev]"
```

## 配置

所有运行时配置都从环境变量读取,可通过 `.env`(已在 `.gitignore`)注入:

```bash
cp .env.example .env
# 编辑 .env,填入 OPENROUTER_API_KEY,以及(可选)OPENROUTER_MODEL / STS2MCP_BASE_URL
```

可用变量:

| 变量 | 默认 | 说明 |
|---|---|---|
| `OPENROUTER_API_KEY` | 无 | 必填,`sk-or-v1-...`。 |
| `OPENROUTER_MODEL` | `openai/gpt-4o-mini` | 任意 OpenRouter slug。 |
| `OPENROUTER_TIMEOUT` | `120` | 单次 LLM 请求秒数。 |
| `STS2MCP_BASE_URL` | 未设置 | STS2MCP 监听的 base URL,例如 `http://127.0.0.1:8080`。 |
| `STS2MCP_TIMEOUT` | `30` | 单次 REST 请求秒数。 |

> **安全提示**:key 只放 `.env` 或本地 shell,**不要**写进 tracked 文件、测试或 commit。如不慎泄露,到 OpenRouter 控制台立即 revoke。

查看当前生效配置(密钥默认 mask):

```bash
slay2agent config
```

## CLI

安装后获得 `slay2agent` 命令(也可以 `python -m slay2agent.cli`,或 `python main.py`):

```bash
slay2agent --help

slay2agent config            # 打印当前配置(密钥 mask)
slay2agent smoke             # 跑 OpenRouter 链路冒烟(需 OPENROUTER_API_KEY)
slay2agent smoke --model anthropic/claude-sonnet-4
slay2agent inspect           # 打印 STS2MCP 当前 state(F-003 待实现)
slay2agent run               # 让 Agent 接管当前局(F-005 待实现)
```

## 测试

```bash
# 离线单测(无网络、无 key)
.venv/bin/pytest -q

# 在线 LLM 冒烟(需要 OPENROUTER_API_KEY)
slay2agent smoke
```

离线单测全绿即视为 LLM 链路类型/重试/usage 协议层 OK。在线冒烟 2/2 PASS 即视为 OpenRouter 端到端通。
