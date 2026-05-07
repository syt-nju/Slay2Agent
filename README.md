# slay2agent

一个 **train-free** 的 Agent 框架,目标是驱动 LLM 自主通关 *Slay the Spire 2*。

## 目标

不做任何模型训练或微调,仅依靠 prompt、工具调用、记忆与反思等机制,让现成的 LLM 作为决策核心,完成从牌组构建、地图选择到战斗回合的全流程通关。

## 现状

LLM 适配层已通链路,正在搭游戏交互层与 agent orchestrator。详见 [`plan.md`](./plan.md) 与 [`docs/llm-adapter.md`](./docs/llm-adapter.md)。

## 环境要求

- Python **3.11+**(仓库已用 `.python-version` 钉住 3.11)
- [uv](https://docs.astral.sh/uv/)(推荐;也可以用 `pip`)

## 安装

```bash
uv venv --python 3.11
uv pip install -e ".[dev]"
```

## 配置 API Key

所有 LLM 调用走 [OpenRouter](https://openrouter.ai),一套 key 覆盖 Claude / GPT / Gemini / DeepSeek 等 200+ 模型。

1. 到 https://openrouter.ai/keys 生成一把 key(`sk-or-v1-...`)。
2. 复制模板到本地 `.env`(该文件已在 `.gitignore`,不会被提交):

   ```bash
   cp .env.example .env
   ```

3. 编辑 `.env`,把你的 key 填进去:

   ```env
   OPENROUTER_API_KEY=sk-or-v1-你的key
   # 可选
   # OPENROUTER_MODEL=anthropic/claude-sonnet-4
   ```

也可以直接导到当前 shell:

```bash
export OPENROUTER_API_KEY=sk-or-v1-你的key
```

> **安全提示**:key 只能出现在 `.env` 或本地 shell,**不要**写进任何 tracked 文件(code / test / commit message)。如不慎泄露,到 OpenRouter 控制台立即 revoke。

## 冒烟测试

确认 LLM 链路通:

```bash
# 离线单测(不需要 key)
.venv/bin/pytest -q

# 在线冒烟(需要 key)
.venv/bin/python -m slay2agent.llm.smoke
# 或指定模型
.venv/bin/python -m slay2agent.llm.smoke --model=anthropic/claude-sonnet-4
```

在线冒烟会跑两条场景:纯文本返回 + tool_call 触发,两个都 PASS 即通。
