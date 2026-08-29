# GrokSearch 项目长期交接说明

> 最后更新：2026-08-29
> 仓库：`handsomelong922/GrokSearch`
> 当前 GitHub `main`：`81ac200c798bad6f7b6c2e92c322183d15ca5b93`
> 最新成功 Docker：`ghcr.io/handsomelong922/groksearch:26`
> 当前部署状态：GitHub/GHCR 已完成；HF Space 尚未在当前对话确认切换到 `:26`，因此新 MCP schema / telemetry 尚待 live 验证。

## 当前核心原则

- ChatGPT 端优先使用 `batch_web_search`；一项列表也用于单查询。
- `REASONING_EFFORT=high` 保持不变。
- `SEARCH_PROVIDER_STRATEGY=parallel`；Grok + Gemini 仍按现有并行完整等待策略执行。
- **本阶段明确不做 Grok-first + Gemini grace window。**
- Tavily 按需增强；普通 batch 默认 `extra_sources=0`。
- Firecrawl 当前不应无理由进入普通搜索关键路径。
- 任何 MCP schema、Provider router、并发和核心接口修改必须优先保证向后兼容并做回归验证。

## PR #16：Planner 收敛 + Search/Runtime Telemetry

PR #16 已 squash merge 到 `main`。

主要变更：

1. `batch_web_search` 明确成为普通 factual / recent / comparative / multi-query 搜索的默认入口。
2. 六个旧公开 Planner 工具从默认 MCP schema 退出：
   - `plan_intent`
   - `plan_complexity`
   - `plan_sub_query`
   - `plan_search_term`
   - `plan_tool_mapping`
   - `plan_execution`
3. 旧 Planner 底层 Python 实现仍保留，以减少兼容风险。
4. 新增单一公开 `plan_search`：只有真正需要显式多阶段研究计划时才调用；普通搜索不应先调用它。
5. Planner 中旧 `web_search` 映射会规范为 `batch_web_search`，消除 schema / 内部 batch-first 策略不一致。
6. `batch_web_search` 增加低开销 request-scoped timing telemetry。
7. `get_config_info` 增加 safe runtime/build telemetry。
8. Docker workflow 注入 build provenance。
9. 新增 Pull Request pytest CI。

## Telemetry 当前字段

每个 batch query 结果可包含：

```text
timing.total_ms
timing.provider_router_ms
timing.providers_ms.Grok
timing.providers_ms.Gemini
timing.overhead_ms
timing.supplemental_enabled
cache_hit
```

batch 外层包含：

```text
batch_timing.total_ms
batch_timing.query_count
```

设计原则：

- 使用 `time.perf_counter()`；无外部 telemetry 依赖。
- 不改变 Provider 选择和搜索语义。
- `overhead_ms = total_ms - provider_router_ms`，目前聚合 cache check、可选 supplemental search、source merge 和 response construction。
- 暂不为了统计而侵入 Tavily / post-processing 每个细分阶段；如果 live 数据证明 overhead 明显偏高，再继续拆分。

## Runtime/build diagnostics

`get_config_info.runtime` 新增/目标字段包括：

```text
package_version
python_version
git_sha
build_version
docker_tag
provider_strategy
reasoning_effort
telemetry_version
timeouts_seconds
```

Docker #26 构建时已注入：

```text
GIT_SHA=81ac200c798bad6f7b6c2e92c322183d15ca5b93
BUILD_VERSION=26
DOCKER_TAG=26
```

部署后可直接区分“HF 仍运行旧镜像”与“ChatGPT MCP schema 只是缓存旧版本”。

## 验证记录

开发分支：`feat/planner-telemetry`

最初 CI 暴露 3 个 entrypoint Python 兼容入口问题；修复后重新执行完整测试：

```text
54 passed in 1.32s
```

最终 PR：`#16`

最终 main merge commit：

```text
81ac200c798bad6f7b6c2e92c322183d15ca5b93
```

Docker workflow：

```text
run #26: success
image: ghcr.io/handsomelong922/groksearch:26
```

## 当前尚未完成 / 已知问题

### 1. HF Space 尚未确认部署 :26

生产运行态不能仅凭 GitHub/GHCR 推断。必须把 Space 切换或重部署到：

```text
ghcr.io/handsomelong922/groksearch:26
```

### 2. 新 MCP schema 尚待 live 验证

HF 部署完成后应确认：

- `plan_search` 存在；
- 六个旧 `plan_*` 不再默认公开；
- `batch_web_search` tool description 明确 batch-first / planning not prerequisite；
- `switch_model` 仍保持 provider-aware schema；
- ChatGPT 没有继续缓存旧 schema。

### 3. Timing telemetry 尚待 live 数据

部署后先收集 Grok / Gemini 实际耗时，不要立刻修改 router。

重点看：

```text
providers_ms.Grok
providers_ms.Gemini
provider_router_ms
overhead_ms
total_ms
```

只有后续证据显示 Gemini 经常显著拖高 tail latency，且用户重新确认，才考虑单独设计 Grok-first + Gemini grace-window PR。

### 4. Tavily + Firecrawl supplemental allocation

当前 Firecrawl 未配置，普通 batch 默认 `extra_sources=0`，所以不是 hot path。未来同时启用时再单独处理预算分配。

### 5. switch_model hosted persistence

运行时切换会写配置并同步当前进程，但 HF 容器完整 rebuild 后文件系统覆盖可能被重置。长期固定模型仍以 HF 环境变量 / Secret 或持久化存储更稳妥。每次重新部署后以 `get_config_info` 为准。

## 下一步操作

### 第一步：HF Space 切到 Docker :26

```text
ghcr.io/handsomelong922/groksearch:26
```

完成 rebuild / restart。

### 第二步：刷新 ChatGPT MCP schema

- 断开/重新连接 GrokSearch MCP，或 disable → enable；
- 最稳妥：新开一个本 Project 对话。

### 第三步：运行 get_config_info

期望至少确认：

```text
runtime.git_sha = 81ac200c798bad6f7b6c2e92c322183d15ca5b93
runtime.build_version = 26
runtime.docker_tag = 26
runtime.reasoning_effort = high
runtime.provider_strategy = parallel
```

同时检查 Grok/Gemini 模型和 Tavily/Firecrawl 状态。

### 第四步：核对 MCP tools

应看到核心工具：

```text
batch_web_search
plan_search
get_sources
web_fetch
web_map
get_config_info
switch_model
```

六个旧 plan 工具不应再默认公开。

### 第五步：live timing 测试

单项：

```text
batch_web_search(
  queries=["one normal current query"],
  extra_sources=0
)
```

多项：

```text
batch_web_search(
  queries=["q1", "q2", "q3"],
  extra_sources=0
)
```

检查：

- 每项 query timing；
- Grok/Gemini 分别耗时；
- batch wall-clock；
- 多项仍由服务器并发执行；
- 默认没有无意义 Tavily supplemental latency。

## 新对话标准恢复顺序

1. 先读本文件。
2. 核对 GitHub `main`，不要只依赖旧聊天。
3. 涉及实际部署时调用 `get_config_info`。
4. 刚重新部署时先确认 MCP schema 已刷新。
5. ChatGPT 搜索优先 `batch_web_search`。
6. `REASONING_EFFORT` 保持 `high`。
7. Tavily 按需，普通 batch 默认 `extra_sources=0`。
8. 核心接口/router/并发修改先做回归测试。
9. merge 后检查 Docker workflow。
10. HF 重部署后再做 live MCP 验证。

## 一句话当前状态

代码侧已经完成 Planner 从“默认六阶段控制流”降级为“可选单一 plan_search”，并新增真实耗时和 build telemetry；PR #16、54 项 pytest、Docker #26 都已成功。**现在唯一关键下一步是把 HF Space 部署到 :26，刷新 ChatGPT MCP schema，然后用新 telemetry 做 live 验证；不要先做 grace window。**
