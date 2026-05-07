# LLM Adapter 层设计方案

F-002 (LLM Adapter) 的内部设计文档,配套 [`feature-requirements.md`](./feature-requirements.md) 与 [`framework-design.md`](./framework-design.md)。目标:**跑通 chat + tool_call 最小链路**。

## TODO

- [x] OpenRouter adapter 实现(`openrouter.py`:翻译函数 + 继承 `LLMAdapter` 的子类)
- [x] 测试(离线 38 passed:`test_errors.py` / `test_retry.py` / `test_usage.py` / `test_openrouter.py`;在线冒烟 2/2 pass:纯文本 + toy tool,模型 `openai/gpt-4o-mini`)

## 1. 范围

**做**
- 单个 `OpenRouterAdapter`。OpenRouter 是 OpenAI-compatible 端点,一套代码覆盖 Claude / GPT / Gemini / DeepSeek 等 200+ 模型。
- Canonical 返回对象:文本 + 工具调用列表 + token usage。
- 带抖动的指数退避重试。
- 用量累计(tokens in/out,按 provider+model 分桶),写日志,**不算价格**。

**不做**
- 不做 `pricing.py` / 实时拉价 / 成本熔断。价格需要实时查 OpenRouter `/models` 或 `/generation`,接口返回不稳定,投入产出比差。
- 不做 Anthropic / Bedrock / 原生 OpenAI adapter——OpenRouter 就够。真要原生 Anthropic 时再补。
- 不做 streaming、prompt caching、OAuth、credential pool、extended thinking 切换。
- 不做多 provider 路由。

## 2. 参考 hermes-agent 的结论

读了 `NousResearch/hermes-agent/agent/anthropic_adapter.py` 和 `retry_utils.py`。**只借两件事**:

1. **Canonical 用 OpenAI-style 的 `{message, tool_calls, usage}` dataclass**——OpenRouter 原生就是这个 shape,只需做**轻翻译**(arguments 在 dict ↔ JSON string 之间互转、tool schema 外包一层 `function` wrapper、stop_reason 值域收敛),详见 §6.2。
2. **jittered exponential backoff**——20 行纯函数,直接抄。

hermes 其余的 OAuth / beta headers / thinking budget / credential pool / rate-limit tracker / pricing 查表——**全部不做**。

## 3. Canonical 类型(`llm/protocol.py`)

```python
Role = Literal["system", "user", "assistant", "tool"]

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict           # 已 json.loads

@dataclass
class Message:
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] | None = None   # 仅 assistant
    tool_call_id: str | None = None            # 仅 tool

@dataclass
class ToolSchema:
    name: str
    description: str
    parameters: dict          # 标准 JSON Schema

@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

@dataclass
class LLMResponse:
    message: Message
    usage: Usage
    stop_reason: Literal["stop", "tool_calls", "length", "error"]
    model: str                # OpenRouter 回的实际模型 id
    raw: Any = None           # 原始响应,trace 用
```

## 4. Adapter 抽象基类(`llm/protocol.py`)

所有 provider 的 adapter 都继承这个 ABC,上层只依赖基类接口——换 provider 零改动。

```python
class LLMAdapter(ABC):
    """Provider-agnostic LLM adapter。子类只需实现 chat()。"""

    def __init__(self, model: str):
        self.model = model

    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        *,
        tool_choice: Literal["auto", "required", "none"] = "auto",
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """发送一次请求,返回 canonical LLMResponse。不处理 retry、不累计 usage。"""
```

**约束**:
- 同步接口,不做 async(ReAct 循环本就是严格同步)。
- 子类不得在 `chat()` 里做 retry / usage 累计 / 预算判断——这些是横切关注点,由外层 `call_with_retry` + `UsageTracker` 统一处理,避免在每个 adapter 里重复。
- 输入/输出对象只能是 canonical 类型,provider 原始响应对象只允许出现在 `LLMResponse.raw`。

新增 provider = 新增一个文件 + 一个继承 `LLMAdapter` 的子类,其它代码不动。

### 4.1 典型调用范式(多轮 tool call)

读这段代码里**不应出现任何 provider 名字**——换 adapter 时这段零改动即设计达标。

```python
adapter = OpenRouterAdapter(model=cfg.model, api_key=cfg.api_key)
tracker = UsageTracker()
messages = [Message(role="system", content=SYS), Message(role="user", content=task)]

while True:
    resp = call_with_retry(lambda: adapter.chat(messages, tools=TOOLS))
    tracker.record(resp.model, resp.usage)
    messages.append(resp.message)
    if resp.stop_reason != "tool_calls":
        break
    for tc in resp.message.tool_calls:
        result = dispatch(tc.name, tc.arguments)
        messages.append(Message(role="tool", tool_call_id=tc.id, content=result))
```

## 5. 文件布局

```
src/slay2agent/llm/
├── __init__.py
├── protocol.py          # canonical 类型 + LLMAdapter ABC
├── errors.py            # LLMError / TransientError / RateLimitError / FatalError
├── retry.py             # jittered_backoff + call_with_retry
├── usage.py             # UsageTracker:按 (provider, model) 累加 tokens,快照出 dict
└── openrouter.py        # OpenRouterAdapter(唯一实现)
```

比上一版砍掉 `pricing.py` / `budget.py` / `factory.py` / `anthropic_adapter.py` / `openai_adapter.py`。

## 6. `OpenRouterAdapter` 实现要点

### 6.1 依赖

用 `openai` SDK 指到 OpenRouter base_url。OpenRouter 官方推荐这种用法,tool calling / JSON mode 完全兼容。

```python
self._client = openai.OpenAI(
    api_key=api_key,            # OPENROUTER_API_KEY
    base_url="https://openrouter.ai/api/v1",
    default_headers={
        "HTTP-Referer": "https://github.com/<owner>/slay2agent",
        "X-Title": "slay2agent",
    },
)
```

两个 header 是 OpenRouter 排行榜用,非必需但加上没坏处。

### 6.2 翻译

因为 canonical 就是 OpenAI-style,翻译几乎是恒等:

- `Message → dict`:`role/content/tool_call_id` 直出;`tool_calls` 里的 `arguments` 需要 `json.dumps()` 回字符串(OpenAI API 要求 `function.arguments` 是 string)。
- `ToolSchema → dict`:包一层 `{"type": "function", "function": {name, description, parameters}}`。
- `response → LLMResponse`:`choice.message.tool_calls[i].function.arguments` 是 JSON string,这里 `json.loads()` 一次就得到扁平 `ToolCall.arguments: dict`。`stop_reason` 映射:`stop → stop`、`tool_calls → tool_calls`、`length → length`,其它归 `stop`。

### 6.3 tool_choice 映射

- `"auto"` → 透传。
- `"required"` → OpenAI 参数就是 `"required"`,透传。
- `"none"` → 不传 tools(OpenRouter 部分模型对 `"none"` 报错,干脆不发 schema)。

### 6.4 单文件约 150 行,三个纯函数 + 一个 class:

```python
def _to_openai_messages(messages: list[Message]) -> list[dict]: ...
def _to_openai_tools(tools: list[ToolSchema]) -> list[dict]: ...
def _from_openai_response(resp) -> LLMResponse: ...

class OpenRouterAdapter(LLMAdapter):
    def __init__(self, model: str, api_key: str, timeout: float = 120.0):
        super().__init__(model)
        self._client = openai.OpenAI(...)

    def chat(self, messages, tools=None, *, tool_choice="auto", max_output_tokens=None, temperature=None) -> LLMResponse: ...
```

`chat()` 本身不做 retry 也不做 usage 累计。外部用 `call_with_retry` 装饰,外部在返回后调 `UsageTracker.record()`。

## 7. 横切关注点

### 7.1 Retry(`retry.py`)

抄 hermes 的 `jittered_backoff`,加一个装饰器:

```python
def jittered_backoff(attempt, *, base=5.0, cap=120.0, jitter_ratio=0.5) -> float: ...

def call_with_retry(fn: Callable[[], T], *, max_attempts: int = 4) -> T:
    """
    对 TransientError / RateLimitError 重试(指数退避+抖动)。
    RateLimitError 若带 retry_after,优先用它。
    FatalError 立即抛。
    每次重试 logger.warning,最终失败 logger.error 并抛出。
    """
```

### 7.2 错误(`errors.py`)

极简三类 + 分类器:

```python
class LLMError(Exception): ...
class FatalError(LLMError): ...            # 4xx(除 429)、schema 错误、认证失败
class TransientError(LLMError): ...        # 5xx、超时、网络中断
class RateLimitError(TransientError):
    retry_after: float | None = None       # 秒数,来自响应头

def classify(exc: Exception) -> LLMError:
    """识别 openai.APIStatusError / APITimeoutError / APIConnectionError,包成对应子类。未知错误包 FatalError。"""
```

**未知错误 → FatalError 的取舍**:宁愿立刻暴露奇怪错误,也不要沉默地把真 bug 当成"偶发抖动"重试掉。真 transient 的抖动在 `APITimeoutError` / `APIConnectionError` 已覆盖,跑不到 `classify()` 的 fallback 分支。

### 7.3 Usage 累计(`usage.py`)

不算钱,只记 token:

```python
@dataclass
class UsageTracker:
    _buckets: dict[str, Usage] = field(default_factory=dict)   # key = "openrouter:<model>"

    def record(self, model: str, usage: Usage) -> None:
        """累加到对应桶。每次调用 logger.info 打印本次 + 累计。"""

    def snapshot(self) -> dict[str, dict]:
        """给 trace 日志用:{model: {input, output, total}}"""

    def total(self) -> Usage:
        """全桶合计。"""
```

上层 orchestrator 持有一个 `UsageTracker` 实例,每次 `adapter.chat()` 返回后 `tracker.record(resp.model, resp.usage)`。

**不做预算熔断**。如果后面真需要,加一个 `max_total_tokens` 配置在 `record()` 里判一下就行,5 行代码,不先做。

## 8. 配置(`config.py`,最小集)

```python
@dataclass
class LLMConfig:
    # TBD:首版运行前以 https://openrouter.ai/models 查到的实际 slug 为准,
    # Anthropic / DeepSeek / Gemini 的命名规则都不同,不要凭记忆写。
    model: str = "anthropic/claude-sonnet-4.5"
    api_key: str | None = None                   # None → 读 OPENROUTER_API_KEY
```

`.env` 里只要 `OPENROUTER_API_KEY=sk-or-...`。

首版 retry 次数、request timeout 直接在代码里写死(`max_attempts=4`, `timeout=120s`)。真有调参需求再加配置字段——省得 config 字段存在但没路径流下去。

## 9. 冒烟测试

按文件分组,单元职责对齐。

### 9.1 `tests/test_errors.py`(离线)
- 429 → `RateLimitError`,且 `retry_after` 从响应头(或 SDK 字段)正确提取。
- 5xx / `APITimeoutError` / `APIConnectionError` → `TransientError`。
- 401/403/400 schema 错 → `FatalError`。
- 未知 `Exception` → `FatalError`(见 §7.2 取舍说明)。

### 9.2 `tests/test_retry.py`(离线,mock `time.sleep`)
- `TransientError` 触发重试,且重试次数达到 `max_attempts` 后抛出最后一次异常。
- `RateLimitError.retry_after` 生效:mock 时间,断言 `sleep` 被按该值调用。
- `FatalError` 立即抛出,不重试。
- 每次重试打 `logger.warning`,最终失败打 `logger.error`(`caplog` 断言)。

### 9.3 `tests/test_usage.py`(离线)
- `record()` 累加到对应 bucket,`snapshot()` 输出结构对齐。
- `total()` 全桶合计正确。

### 9.4 `tests/test_openrouter.py`(离线,mock `openai.OpenAI`)
- 纯文本往返:构造 `[system, user]`,mock 返回文本响应 → 断言 `LLMResponse.message.content` / `stop_reason="stop"` / `usage` 字段。
- Tool call 往返:mock 返回 tool_calls 响应 → 断言 `tool_calls[0].arguments` 是 dict(不是 str)、`stop_reason="tool_calls"`。
- 历史重发:构造一个含 assistant(tool_calls) + tool(result) 的 messages,断言 `_to_openai_messages` 把 `arguments` 正确 `json.dumps` 回字符串。

### 9.5 在线冒烟(`python -m slay2agent.llm.smoke`,需要 key)
- 发一条 "Return the JSON `{\"ok\": true}`",打印 `LLMResponse`。
- 定义玩具 tool `echo(text: str)`,发一条 "Call echo with 'hi'",断言 `tool_calls[0].name == "echo"` 且 `arguments == {"text": "hi"}`。

跑通 9.1–9.4 + 9.5 即算 LLM 链路通。

## 10. 实现顺序(半天以内)

每步都有 verify 条件,前 4 步完全可以自己 loop 到绿,第 5 步需要 key。

1. `protocol.py` + `errors.py` — 15 min
   → **verify**:`ruff` / `mypy` 通过;`from slay2agent.llm import Message, ToolCall, ToolSchema, Usage, LLMResponse, LLMAdapter` 可 import;`test_errors.py`(§9.1)全绿。
2. `retry.py` + `usage.py` — 45 min
   → **verify**:`test_retry.py`(§9.2)+ `test_usage.py`(§9.3)全绿。
3. `openrouter.py` 里三个翻译纯函数 + fixture 单测 — 1 h
   → **verify**:`test_openrouter.py`(§9.4)前三个 case(含历史重发)全绿。
4. `OpenRouterAdapter.chat()` + mock 测试 — 30 min
   → **verify**:mock `openai.OpenAI.chat.completions.create` 下,`adapter.chat(...)` 端到端返回正确 `LLMResponse`;`test_openrouter.py` 全绿。
5. `config.py` 接入 + live 冒烟 — 30 min
   → **verify**:`python -m slay2agent.llm.smoke` 两条场景(§9.5)均成功,stdout 打印 `LLMResponse` + usage。

## 11. 延后项

- **Anthropic 原生 adapter**:想用 prompt caching / extended thinking 时再加,新增 `anthropic.py` 继承 `LLMAdapter` 即可,上层零改动。
- **Streaming / caching / 多 key**:等真的需要再说。
