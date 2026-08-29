# GrokSearch 项目长期交接说明

> 最后更新：2026-08-29
> 仓库：`handsomelong922/GrokSearch`
> 当前 GitHub `main`：`6130b99e923cec5bffe2d2b15d8695a1f62ad6b1`（handoff docs-only）
> 当前功能代码 / Docker 构建提交：`1aaa63e6174d5c2b2cb35edade8ad6bd2527fc92`（PR #18）
> 当前已部署并 live 验证 Docker：`ghcr.io/handsomelong922/groksearch:28`

## 当前核心原则

- ChatGPT 端优先使用 `batch_web_search`；一项列表也用于单查询。
- `REASONING_EFFORT=high` 保持不变。
- `SEARCH_PROVIDER_STRATEGY=parallel`；Grok + Gemini 仍按现有并行完整等待策略执行。
- **本阶段明确不做 Grok-first + Gemini grace window。**
- Tavily 按需增强；普通 batch 应显式/默认使用 `extra_sources=0`。
- Firecrawl 当前未配置，不应进入普通搜索关键路径。
- 任何 MCP schema、Provider router、并发和核心接口修改必须优先保证向后兼容并先做回归验证。

## 当前运行态：Docker :28 已 live 验证

2026-08-29 用户完成 HF Space 切换后，`get_config_info` 实时返回：

```text
runtime.git_sha = 1aaa63e6174d5c2b2cb35edade8ad6bd2527fc92
runtime.build_version = 28
runtime.docker_tag = 28
runtime.telemetry_version = 1
runtime.provider_strategy = parallel
runtime.reasoning_effort = high
```

Provider / 配置：

```text
Grok = grok-chat-fast
Gemini = gemini-3.7-flash
SEARCH_PROVIDER_STRATEGY = parallel
REASONING_EFFORT = high
Tavily = configured
Firecrawl = not configured
```

Grok / Gemini `/models` connectivity 均成功。

## PR #16：Planner 收敛 + Search/Runtime Telemetry

PR #16 已 squash merge：

```text
81ac200c798bad6f7b6c2e92c322183d15ca5b93
```

主要变更：

1. `batch_web_search` 成为普通 factual / recent / comparative / multi-query 搜索默认入口。
2. 六个旧公开 Planner 工具退出目标默认 MCP schema：`plan_intent`、`plan_complexity`、`plan_sub_query`、`plan_search_term`、`plan_tool_mapping`、`plan_execution`。
3. 旧 Planner Python 实现保留以降低兼容风险。
4. 新增单一可选 `plan_search`；普通搜索不应先调用 Planner。
5. Planner 内部搜索映射统一为 `batch_web_search`。
6. `batch_web_search` 增加 request-scoped timing telemetry。
7. `get_config_info` 增加 runtime/build provenance。
8. Docker workflow 注入 build provenance。
9. 增加 Pull Request pytest CI。

## PR #18：Telemetry SingleFlight 修复 + GitHub Direct Fetch

PR #18 squash merge：

```text
1aaa63e6174d5c2b2cb35edade8ad6bd2527fc92
```

Docker workflow：

```text
run #28: success
Build and push: success
image: ghcr.io/handsomelong922/groksearch:28
```

### 根因 1：SingleFlight child task 导致 telemetry ContextVar 丢失

`:27` live 测试曾出现：

```text
timing.total_ms > 0
provider_router_ms = 0.0
providers_ms = {}
providers_used = [Grok, Gemini]
```

`_SearchSingleFlight` 使用 `asyncio.create_task()` 执行真实搜索。ContextVar binding 会复制给 child task，但 child task 中重新 `_LAST_TIMING.set(...)` 不会回传 parent task。

修复：对 parent task 已绑定并由 child 继承的可变 timing dict **原地更新**，不重新绑定 ContextVar；不改变 router、缓存 key、Provider 策略或 reasoning。

### :28 live telemetry 验证：已解决

单项 fresh batch：

```text
query_count = 1
cache_hit = false
total_ms = 3556.54
provider_router_ms = 3555.73
providers_ms.Grok = 1937.55
providers_ms.Gemini = 3539.04
overhead_ms = 0.81
batch_timing.total_ms = 3556.83
```

结论：Provider timing 已成功跨 SingleFlight child task 返回父 batch wrapper；此前 `provider_router_ms=0 / providers_ms={}` 的 live bug 已解决。

### Cache timing 验证：正常

重复完全相同查询：

```text
cache_hit = true
total_ms = 0.03
provider_router_ms = 0.0
providers_ms = {}
overhead_ms = 0.03
batch_timing.total_ms = 0.10
```

结论：缓存命中不会错误回放首次真实 Provider timing。

### 多 query 服务端并发验证：正常

3 个 fresh query：

```text
q1 total_ms = 4496.78
q2 total_ms = 4480.05
q3 total_ms = 4302.42
batch_timing.total_ms = 4496.88
```

每项均：

```text
cache_hit = false
provider_router_ms > 0
providers_ms.Grok > 0
providers_ms.Gemini > 0
overhead_ms < 1 ms
```

结论：3 个 query 在 MCP 服务端并发执行，batch wall-clock 基本等于最慢子查询，而不是三项耗时相加。

### 根因 2：GitHub API/raw URL 错误进入 Tavily → Firecrawl

用户后台日志曾出现：

```text
Begin Fetch: https://api.github.com/...
Tavily unavailable or failed, trying Firecrawl...
Fetch Failed!
```

Firecrawl 当前未配置，因此 GitHub API / raw URL 一旦 Tavily Extract 不适配就必然继续失败。

修复：以下两个精确 public host 直接 HTTPS GET：

```text
api.github.com
raw.githubusercontent.com
```

普通网页仍保持原有 Tavily → Firecrawl extraction path；没有开放任意 URL 直连，避免扩大 SSRF 风险面。

### :28 GitHub direct fetch live 验证：已解决

实时调用：

```text
https://api.github.com/repos/handsomelong922/GrokSearch/branches/main
```

成功直接返回 GitHub branch JSON，并确认：

```text
main = 6130b99e923cec5bffe2d2b15d8695a1f62ad6b1
parent = 1aaa63e6174d5c2b2cb35edade8ad6bd2527fc92
```

实时调用：

```text
https://raw.githubusercontent.com/handsomelong922/GrokSearch/main/src/grok_search/telemetry.py
```

成功直接返回完整 `telemetry.py` 源码。

结论：此前 GitHub API/raw → Tavily → Firecrawl → Failed 的功能性故障已解决。

## Telemetry 当前字段

每个 batch query：

```text
timing.total_ms
timing.provider_router_ms
timing.providers_ms.Grok
timing.providers_ms.Gemini
timing.overhead_ms
timing.supplemental_enabled
cache_hit
```

batch 外层：

```text
batch_timing.total_ms
batch_timing.query_count
```

当前 live 数据说明 Provider/router telemetry 本身开销很低；本次 fresh 测试 overhead 约 0.3–0.8 ms。

## 当前唯一主要未完成问题：ChatGPT MCP tool schema 仍旧

**后端 runtime 已确认是 :28，但本次 ChatGPT 会话加载到的 MCP schema 仍明显是旧版。**

当前 ChatGPT 侧仍看到：

- 六个旧 Planner：`plan_intent / plan_complexity / plan_sub_query / plan_search_term / plan_tool_mapping / plan_execution`；
- 没有目标新公开 `plan_search`；
- `batch_web_search` description 仍写“two or more independent searches”；
- `batch_web_search.extra_sources` schema 默认值仍显示 `3`；
- `switch_model` 仍只显示单个 `model` 参数，没有目标 `provider` 参数；
- `web_fetch` description 仍是旧版通用描述，没有显示 GitHub API/raw direct-fetch 说明。

与此同时，live runtime 和行为已经证明 :28 代码实际生效。因此当前剩余问题应视为 **MCP Host / ChatGPT 连接侧 schema 缓存或工具注册刷新问题**，而不是 HF Space 仍运行旧镜像。

### 下一步先处理 schema refresh，不要继续改后端搜索逻辑

建议顺序：

1. ChatGPT 中完全断开 GrokSearch MCP / App connection；
2. 重新连接；
3. 如果仍旧，disable → enable；
4. 最稳妥：在同一 Project 新开一个新对话重新加载 MCP tools；
5. 再检查工具 schema。

目标 schema：

```text
batch_web_search   # batch-first，单项也可，extra_sources 默认 0
plan_search        # 单一可选 Planner
get_sources
web_fetch          # GitHub API/raw direct fetch + normal-page existing path
web_map
get_config_info
switch_model       # provider-aware
```

六个旧 `plan_*` 不应再默认公开。

## 其他已知边界

### Tavily + Firecrawl supplemental allocation

Firecrawl 当前未配置；普通 batch 使用 `extra_sources=0`，所以不是 hot path。未来同时启用时再单独设计预算分配。

### switch_model hosted persistence

运行时切换会写配置并同步当前进程，但 HF 容器完整 rebuild 后文件系统覆盖可能重置。长期固定模型仍以 HF 环境变量 / Secret 或持久化存储更稳妥；重新部署后以 `get_config_info` 为准。

### Grok-first + Gemini grace window

**仍明确暂不做。**

当前 :28 telemetry 已可真实观察 Grok/Gemini latency。先收集实际业务查询数据，再决定是否值得单独设计 grace-window PR。

本次示例数据中：

```text
单项：Grok 1937.55 ms / Gemini 3539.04 ms
多项 q1：Grok 4495.58 ms / Gemini 4372.13 ms
多项 q2：Grok 1769.89 ms / Gemini 4477.39 ms
多项 q3：Grok 4298.83 ms / Gemini 4294.04 ms
```

说明 Gemini 有时明显更慢，但不是每次都更慢；暂不足以据此立即改 router 策略。

## 下一步标准操作

### P0：刷新 ChatGPT MCP schema

这是当前唯一最优先事项。后端 :28 已验证，无需再次修改 telemetry / GitHub fetch。

### P1：刷新后重新核对工具 schema

重点确认：

```text
plan_search exists
legacy six plan_* hidden
batch_web_search extra_sources default = 0
batch_web_search description = batch-first / one-item supported
switch_model(provider, model)
web_fetch new description
```

### P2：继续收集真实业务查询 telemetry

记录：

```text
providers_ms.Grok
providers_ms.Gemini
provider_router_ms
overhead_ms
total_ms
```

暂时不要做 Grok-first + Gemini grace window。

## 新对话标准恢复顺序

1. 先读本文件。
2. 核对 GitHub `main`，不要只依赖旧聊天。
3. 涉及实际部署时调用 `get_config_info`。
4. 刚重新部署时先确认 MCP schema 是否刷新。
5. ChatGPT 搜索优先 `batch_web_search`。
6. `REASONING_EFFORT` 保持 `high`。
7. Tavily 按需，普通 batch 使用 `extra_sources=0`。
8. 核心接口/router/并发修改先做回归测试。
9. merge 后检查 Docker workflow。
10. HF 重部署后再做 live MCP 验证。

## 一句话当前状态

**Docker :28 已在 HF Space live 生效并完成验收：SingleFlight telemetry timing 已恢复，缓存 timing 正确，3-query 服务端并发正常，GitHub API/raw direct fetch 正常。当前唯一主要未完成问题是 ChatGPT 侧仍加载旧 MCP tool schema；下一步只需刷新/重连 MCP schema，不要再改后端搜索逻辑，也不要先做 Grok-first + Gemini grace window。**
