# GrokSearch 项目长期交接说明

> 最后更新：2026-08-29
> 仓库：`handsomelong922/GrokSearch`
> 当前代码基线 / Docker 构建提交：`1aaa63e6174d5c2b2cb35edade8ad6bd2527fc92`
> 最新成功 Docker：`ghcr.io/handsomelong922/groksearch:28`
> 当前 live 部署：HF Space 最后已验证仍为 `:27`（`git_sha=a45321913c7c4f434bc9edb54952f4a193ce3daa`）；`:28` 尚待用户重新部署后做 live 验证。

## 当前核心原则

- ChatGPT 端优先使用 `batch_web_search`；一项列表也用于单查询。
- `REASONING_EFFORT=high` 保持不变。
- `SEARCH_PROVIDER_STRATEGY=parallel`；Grok + Gemini 仍按现有并行完整等待策略执行。
- **本阶段明确不做 Grok-first + Gemini grace window。**
- Tavily 按需增强；普通 batch 默认 `extra_sources=0`。
- Firecrawl 当前未配置，不应无理由进入普通搜索关键路径。
- 任何 MCP schema、Provider router、并发和核心接口修改必须优先保证向后兼容并先做回归验证。

## PR #16：Planner 收敛 + Search/Runtime Telemetry

PR #16 已 squash merge。

主要变更：

1. `batch_web_search` 成为普通 factual / recent / comparative / multi-query 搜索默认入口。
2. 六个旧公开 Planner 工具退出默认 MCP schema：`plan_intent`、`plan_complexity`、`plan_sub_query`、`plan_search_term`、`plan_tool_mapping`、`plan_execution`。
3. 旧 Planner Python 实现保留，降低兼容风险。
4. 新增单一可选 `plan_search`；普通搜索不应先调用 Planner。
5. Planner 中旧 `web_search` 映射规范为 `batch_web_search`。
6. `batch_web_search` 增加 request-scoped timing telemetry。
7. `get_config_info` 增加 safe runtime/build telemetry。
8. Docker workflow 注入 build provenance。
9. 新增 Pull Request pytest CI。

PR #16 合并代码提交：

```text
81ac200c798bad6f7b6c2e92c322183d15ca5b93
```

## PR #18：Telemetry SingleFlight 修复 + GitHub Direct Fetch

### Live 复现

HF Space `:27` 已确认运行新 telemetry 代码：

```text
runtime.git_sha = a45321913c7c4f434bc9edb54952f4a193ce3daa
runtime.build_version = 27
runtime.docker_tag = 27
runtime.reasoning_effort = high
runtime.provider_strategy = parallel
```

但真实一项 `batch_web_search(extra_sources=0)` 返回：

```text
timing.total_ms = 8498.48
provider_router_ms = 0.0
providers_ms = {}
overhead_ms = 8498.48
```

搜索本身成功且 `providers_used=["Grok", "Gemini"]`，因此确认是 telemetry 数据传播失败，而不是 Provider 未执行。

### 根因 1：ContextVar 在 SingleFlight child task 中重绑定后不会回传父 task

`server.web_search` 通过 `_SearchSingleFlight.run()` 使用 `asyncio.create_task()` 执行真正搜索。

父 batch task 先执行：

```text
reset_last_search_timing() -> ContextVar 绑定到一个空 dict
```

child task 中原实现随后执行：

```text
_LAST_TIMING.set({...})
```

`asyncio.create_task()` 会复制 Context，但 child 中重新绑定 ContextVar 不会传播回 parent，因此 batch wrapper 最终仍读到父 task 的空 dict。

修复方式：不再在 child 中重新绑定 timing dict，而是**原地更新从 parent 继承的可变 dict**。这样不引入全局 request timing 状态，也不改变 Provider router 行为。

### 根因 2：`web_fetch` 错把 GitHub API / Raw URL 送进 Tavily → Firecrawl 抽取链

用户提供的 HF 后台日志显示：

```text
Begin Fetch: https://api.github.com/...
Tavily unavailable or failed, trying Firecrawl...
Fetch Failed!
```

当前 Firecrawl 未配置，因此 GitHub API endpoint 一旦 Tavily Extract 不适配/失败，fallback 必然继续失败。

修复：

- `https://api.github.com/...`
- `https://raw.githubusercontent.com/...`

这两个**精确 allowlist 公共 host**使用 direct HTTPS GET。

普通网页仍保留原有 Tavily → Firecrawl 抽取路径。没有把 `web_fetch` 变成任意 URL 直连器，避免扩大 SSRF 风险面。

GitHub direct fetch 若遇到 HTTP 4xx/5xx，会直接返回明确 HTTP 错误，不再无意义地进入 Tavily/Firecrawl fallback。

### TDD / CI 验证

先提交回归测试再改生产代码：

- SingleFlight-style child task 应保留 `provider_router_ms`；
- GitHub API direct fetch；
- raw.githubusercontent.com direct fetch；
- 非 allowlist host 不走 direct client；
- GitHub direct fetch 成功时不调用原 Tavily/Firecrawl 路径；
- 普通网页继续沿用原 `web_fetch` 路径。

RED 阶段 CI 如预期失败；修复后 PR #18 新一轮 pytest CI 完整成功。

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

备注：最初 draft PR #17 因 GitHub connector 的 `mark ready for review` GraphQL schema 错误无法切换状态，因此关闭 #17，并以完全相同 head SHA 重建非 draft PR #18；这不是 GrokSearch 仓库代码故障。

## Telemetry 当前字段

每个 batch query 结果：

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

设计原则：

- 使用 `time.perf_counter()`；无外部 telemetry 依赖。
- 不改变 Provider 选择和搜索语义。
- `overhead_ms = total_ms - provider_router_ms`。
- 当前 overhead 聚合 cache check、可选 supplemental search、source merge 和 response construction。
- 暂不侵入 Tavily / post-processing 每个细分阶段；只有 live 数据显示 overhead 明显偏高时再拆分。

## Runtime/build diagnostics

`get_config_info.runtime` 应包含：

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

Docker `:28` 构建目标 provenance：

```text
GIT_SHA=1aaa63e6174d5c2b2cb35edade8ad6bd2527fc92
BUILD_VERSION=28
DOCKER_TAG=28
```

## 当前尚未完成 / 已知问题

### 1. HF Space 需要切换到 :28

生产运行态不能仅凭 GitHub/GHCR 推断。下一步部署：

```text
ghcr.io/handsomelong922/groksearch:28
```

### 2. 部署后必须重新做 live telemetry 验证

期望普通非缓存 batch 搜索满足：

```text
provider_router_ms > 0
providers_ms.Grok > 0
providers_ms.Gemini > 0
```

如果某 Provider 本次真正失败/未执行，其 provider timing 应结合 `providers_used` / errors 判断，不能机械要求两个字段始终存在。

### 3. MCP schema 需要刷新确认

当前 ChatGPT 会话此前仍看到旧的 `batch_web_search` description/default schema，尽管 runtime 已是 `:27`。这很可能是 MCP host schema 缓存。

部署 `:28` 后：

- disable → enable / 断开重连 GrokSearch MCP；
- 必要时新开 Project 对话；
- 确认 `batch_web_search` description 明确 batch-first，默认 `extra_sources=0`；
- 确认 `plan_search` 存在，六个旧 `plan_*` 不再默认公开；
- 确认 `web_fetch` 新 description 已出现；
- 确认 `switch_model` provider-aware schema 仍存在。

### 4. GitHub direct fetch 需要 live 验证

部署 `:28` 后分别测试：

```text
https://api.github.com/repos/handsomelong922/GrokSearch/branches/main
https://raw.githubusercontent.com/handsomelong922/GrokSearch/main/src/grok_search/telemetry.py
```

后台日志不应再出现这两个 host 的：

```text
Tavily unavailable or failed, trying Firecrawl...
Fetch Failed!
```

普通网页仍允许使用 Tavily / Firecrawl extract。

### 5. Tavily + Firecrawl supplemental allocation

Firecrawl 当前未配置；普通 batch 默认 `extra_sources=0`，所以不是 hot path。未来同时启用时再单独处理预算分配。

### 6. switch_model hosted persistence

运行时切换会写配置并同步当前进程，但 HF 容器完整 rebuild 后文件系统覆盖可能被重置。长期固定模型仍以 HF 环境变量 / Secret 或持久化存储更稳妥。每次重新部署后以 `get_config_info` 为准。

## 下一步标准操作

### 第一步：HF Space 切到 Docker :28

```text
ghcr.io/handsomelong922/groksearch:28
```

完成 rebuild / restart。

### 第二步：刷新 ChatGPT MCP schema

断开/重新连接 GrokSearch MCP，或 disable → enable；必要时新开一个本 Project 对话。

### 第三步：运行 `get_config_info`

期望：

```text
runtime.git_sha = 1aaa63e6174d5c2b2cb35edade8ad6bd2527fc92
runtime.build_version = 28
runtime.docker_tag = 28
runtime.reasoning_effort = high
runtime.provider_strategy = parallel
```

### 第四步：live timing 测试

一项：

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

重点检查：

- `provider_router_ms` 不再为 0；
- `providers_ms` 能看到实际 Provider latency；
- `batch_timing.total_ms` 与最长单项耗时关系合理；
- 多项仍由服务器并发；
- 默认无 Tavily supplemental latency。

### 第五步：live GitHub Fetch 测试

调用 `web_fetch` 读取 GitHub API/raw URL，确认 direct path 工作，且后台不再出现 Tavily → Firecrawl 的错误 fallback。

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

Planner 收敛和 telemetry 基础设施已完成；live `:27` 暴露的 SingleFlight telemetry ContextVar 丢失问题，以及用户后台日志暴露的 GitHub API/raw `web_fetch` 错误 fallback 问题，均已通过 PR #18 修复、pytest CI 验证并构建为 Docker `:28`。**现在关键下一步是把 HF Space 切换到 `ghcr.io/handsomelong922/groksearch:28`，刷新 MCP schema，然后 live 验证 timing 和 GitHub direct fetch；仍然不要先做 Grok-first + Gemini grace window。**
