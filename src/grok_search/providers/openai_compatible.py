import os
import asyncio
import httpx
import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_random_exponential
from tenacity.wait import wait_base
from zoneinfo import ZoneInfo
from .base import BaseSearchProvider, SearchResult
from ..utils import get_search_prompt, search_prompt, fetch_prompt, url_describe_prompt, rank_sources_prompt
from ..logger import log_info
from ..config import config


def get_local_time_info() -> str:
    """获取本地时间信息，用于注入到搜索查询中"""
    try:
        # 尝试获取系统本地时区
        local_tz = datetime.now().astimezone().tzinfo
        local_now = datetime.now(local_tz)
    except Exception:
        # 降级使用 UTC
        local_now = datetime.now(timezone.utc)

    # 格式化时间信息
    weekdays_cn = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = weekdays_cn[local_now.weekday()]

    return (
        f"[Current Time Context]\n"
        f"- Date: {local_now.strftime('%Y-%m-%d')} ({weekday})\n"
        f"- Time: {local_now.strftime('%H:%M:%S')}\n"
        f"- Timezone: {local_now.tzname() or 'Local'}\n"
    )


def _needs_time_context(query: str) -> bool:
    """检查查询是否需要时间上下文"""
    # 中文时间相关关键词
    cn_keywords = [
        "当前", "现在", "今天", "明天", "昨天",
        "本周", "上周", "下周", "这周",
        "本月", "上月", "下月", "这个月",
        "今年", "去年", "明年",
        "最新", "最近", "近期", "刚刚", "刚才",
        "实时", "即时", "目前",
    ]
    # 英文时间相关关键词
    en_keywords = [
        "current", "now", "today", "tomorrow", "yesterday",
        "this week", "last week", "next week",
        "this month", "last month", "next month",
        "this year", "last year", "next year",
        "latest", "recent", "recently", "just now",
        "real-time", "realtime", "up-to-date",
    ]

    query_lower = query.lower()

    for keyword in cn_keywords:
        if keyword in query:
            return True

    for keyword in en_keywords:
        if keyword in query_lower:
            return True

    return False

RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class ResponseFailedError(Exception):
    """Raised when the API returns a response.failed event."""


def _is_retryable_exception(exc) -> bool:
    """检查异常是否可重试"""
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError, httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS_CODES
    if isinstance(exc, ResponseFailedError):
        return True
    return False


class _WaitWithRetryAfter(wait_base):
    """等待策略：优先使用 Retry-After 头，否则使用指数退避"""

    def __init__(self, multiplier: float, max_wait: int):
        self._base_wait = wait_random_exponential(multiplier=multiplier, max=max_wait)
        self._protocol_error_base = 3.0

    def __call__(self, retry_state):
        if retry_state.outcome and retry_state.outcome.failed:
            exc = retry_state.outcome.exception()
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                retry_after = self._parse_retry_after(exc.response)
                if retry_after is not None:
                    return retry_after
            if isinstance(exc, httpx.RemoteProtocolError):
                return self._base_wait(retry_state) + self._protocol_error_base
        return self._base_wait(retry_state)

    def _parse_retry_after(self, response: httpx.Response) -> Optional[float]:
        """解析 Retry-After 头（支持秒数或 HTTP 日期格式）"""
        header = response.headers.get("Retry-After")
        if not header:
            return None
        header = header.strip()

        if header.isdigit():
            return float(header)

        try:
            retry_dt = parsedate_to_datetime(header)
            if retry_dt.tzinfo is None:
                retry_dt = retry_dt.replace(tzinfo=timezone.utc)
            delay = (retry_dt - datetime.now(timezone.utc)).total_seconds()
            return max(0.0, delay)
        except (TypeError, ValueError):
            return None


# --- Shared HTTP client for connection reuse ---
_shared_client: httpx.AsyncClient | None = None
_shared_client_lock = asyncio.Lock()


async def _get_shared_client(timeout: httpx.Timeout) -> httpx.AsyncClient:
    """Get or create the shared HTTP client with connection pooling.

    Parallel calls reuse the same connection pool, avoiding redundant
    TCP/TLS handshakes.
    """
    global _shared_client
    async with _shared_client_lock:
        if _shared_client is None or _shared_client.is_closed:
            limits = httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30.0,
            )
            _shared_client = httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                limits=limits,
            )
        return _shared_client


class OpenAICompatibleSearchProvider(BaseSearchProvider):
    def __init__(self, api_url: str, api_key: str, model: str = "grok-4-fast", provider_name: str = "OpenAICompatible"):
        super().__init__(api_url, api_key, provider_name)
        self.model = model

    def get_provider_name(self) -> str:
        return self._provider_name

    def _build_payload(self, system_prompt: str, user_prompt: str, *, search: bool = False) -> dict:
        """Build a request in the configured OpenAI-compatible format."""
        if config.openai_api_format == "responses":
            payload = {
                "model": self.model,
                "instructions": system_prompt,
                "input": [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": user_prompt}],
                    }
                ],
                "stream": True,
            }
            if search:
                self._add_response_search_options(payload)
            return payload

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": True,
        }
        if search:
            self._add_chat_search_options(payload)
        return payload

    @staticmethod
    def _add_response_search_options(payload: dict) -> None:
        if config.web_search_enabled:
            payload["tools"] = [{"type": "web_search"}]
            payload["tool_choice"] = "auto"
        reasoning_effort = config.reasoning_effort
        if reasoning_effort:
            payload["reasoning"] = {"effort": reasoning_effort}

    @staticmethod
    def _add_chat_search_options(payload: dict) -> None:
        if config.web_search_enabled:
            payload["tools"] = [{"type": "web_search"}]
            payload["tool_choice"] = "auto"
        reasoning_effort = config.reasoning_effort
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort

    async def search(self, query: str, platform: str = "", mode: str = "balanced", min_results: int = 3, max_results: int = 10, ctx=None) -> List[SearchResult]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        platform_prompt = ""

        if platform:
            platform_prompt = "\n\nYou should search the web for the information you need, and focus on these platform: " + platform + "\n"

        time_context = get_local_time_info() + "\n"

        system_prompt = get_search_prompt(mode)
        payload = self._build_payload(
            system_prompt,
            time_context + query + platform_prompt,
            search=True,
        )
        await log_info(ctx, f"platform_prompt: { query + platform_prompt}", config.debug_enabled)
        if mode != "balanced":
            await log_info(ctx, f"search_mode: {mode}", config.debug_enabled)

        return await self._execute_stream_with_retry(headers, payload, ctx)

    async def fetch(self, url: str, ctx=None) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = self._build_payload(
            fetch_prompt,
            url + "\n获取该网页内容并返回其结构化Markdown格式",
        )
        return await self._execute_stream_with_retry(headers, payload, ctx)

    async def _parse_streaming_response(self, response, ctx=None) -> str:
        content = ""
        full_body_buffer = []
        error_message = ""
        
        async for line in response.aiter_lines():
            line = line.strip()
            if not line:
                continue
            
            full_body_buffer.append(line)

            # 兼容 "data: {...}" 和 "data:{...}" 两种 SSE 格式
            if line.startswith("data:"):
                if line in ("data: [DONE]", "data:[DONE]"):
                    break
                try:
                    # 去掉 "data:" 前缀，并去除可能的空格
                    json_str = line[5:].lstrip()
                    data = json.loads(json_str)
                    # Check for API error in chat completions response
                    if "error" in data:
                        err = data["error"]
                        if isinstance(err, dict):
                            error_message = f"API error: [{err.get('code', 'unknown')}] {err.get('message', str(err))}"
                        else:
                            error_message = f"API error: {str(err)}"
                        break
                    choices = data.get("choices", [])
                    if choices and len(choices) > 0:
                        delta = choices[0].get("delta", {})
                        if "content" in delta:
                            content += delta["content"]
                except (json.JSONDecodeError, IndexError):
                    continue
                
        if error_message and not content:
            raise ResponseFailedError(error_message)

        if not content and full_body_buffer:
            try:
                full_text = "".join(full_body_buffer)
                data = json.loads(full_text)
                if "error" in data:
                    err = data["error"]
                    if isinstance(err, dict):
                        raise ResponseFailedError(f"API error: [{err.get('code', 'unknown')}] {err.get('message', str(err))}")
                    else:
                        raise ResponseFailedError(f"API error: {str(err)}")
                if "choices" in data and len(data["choices"]) > 0:
                    message = data["choices"][0].get("message", {})
                    content = message.get("content", "")
            except json.JSONDecodeError:
                pass
        
        await log_info(ctx, f"content: {content}", config.debug_enabled)

        return content

    @staticmethod
    def _extract_annotation_url(annotation: dict) -> str | None:
        """Extract a citation URL from flat or nested Responses annotations."""
        url = annotation.get("url")
        if isinstance(url, str) and url:
            return url

        for key in ("url_citation", "citation"):
            nested = annotation.get(key)
            if isinstance(nested, dict):
                url = nested.get("url")
                if isinstance(url, str) and url:
                    return url
        return None

    @classmethod
    def _extract_response_text(cls, data: dict) -> tuple[str, list[str]]:
        """Extract final text and URL annotations from a Responses object."""
        response = data.get("response") if isinstance(data.get("response"), dict) else data
        text = response.get("output_text", "") if isinstance(response, dict) else ""
        sources: list[str] = []
        output = response.get("output", []) if isinstance(response, dict) else []
        if isinstance(output, list):
            parts = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                for content in item.get("content", []) or []:
                    if not isinstance(content, dict):
                        continue
                    for annotation in content.get("annotations", []) or []:
                        if isinstance(annotation, dict):
                            url = cls._extract_annotation_url(annotation)
                            if url:
                                sources.append(url)
                    if content.get("type") in ("output_text", "text"):
                        value = content.get("text", "")
                        if isinstance(value, str):
                            parts.append(value)
            if not text:
                text = "".join(parts)
        return text if isinstance(text, str) else "", sources

    @staticmethod
    def _append_response_sources(content: str, sources: list[str]) -> str:
        unique_sources = []
        seen = set()
        for url in sources:
            if url not in seen and url not in content:
                seen.add(url)
                unique_sources.append(url)
        if not unique_sources:
            return content
        return content.rstrip() + "\n\nSources:\n" + "\n".join(f"- {url}" for url in unique_sources)

    async def _parse_responses_streaming_response(self, response, ctx=None) -> str:
        """Parse Responses SSE events and compatible non-streaming JSON."""
        content_parts: list[str] = []
        sources: list[str] = []
        completed: dict | None = None
        fallback_text = ""
        error_message = ""
        event_type = ""

        async for line in response.aiter_lines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("event:"):
                event_type = line[6:].strip()
                continue
            raw = line[5:].lstrip() if line.startswith("data:") else line
            if raw == "[DONE]":
                break
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue

            data_type = data.get("type", "")
            current_event_type = data_type or event_type
            if current_event_type == "response.output_text.delta":
                delta = data.get("delta", "")
                if isinstance(delta, str):
                    content_parts.append(delta)
            elif current_event_type == "response.output_text.done":
                value = data.get("text", "")
                if isinstance(value, str):
                    fallback_text = value
            elif current_event_type == "response.output_text.annotation.added":
                annotation = data.get("annotation", {})
                if isinstance(annotation, dict):
                    url = self._extract_annotation_url(annotation)
                    if url:
                        sources.append(url)
            elif current_event_type == "response.completed":
                completed = data
                completed_text, completed_sources = self._extract_response_text(data)
                if completed_text:
                    fallback_text = completed_text
                sources.extend(completed_sources)
                break
            elif current_event_type in {
                "response.failed",
                "response.incomplete",
                "response.cancelled",
            }:
                error = data.get("response", {}).get("error", {}) if isinstance(data.get("response"), dict) else data.get("error", {})
                if isinstance(error, dict):
                    error_code = error.get("code", "unknown")
                    error_msg = error.get("message", str(error))
                else:
                    error_code = "unknown"
                    error_msg = str(error)
                error_message = f"API returned {current_event_type}: [{error_code}] {error_msg}"
                await log_info(ctx, error_message, config.debug_enabled)
                break
            elif current_event_type == "response.output_item.done":
                item = data.get("item")
                if isinstance(item, dict):
                    item_text, item_sources = self._extract_response_text({"output": [item]})
                    if item_text:
                        fallback_text = item_text
                    sources.extend(item_sources)
            elif current_event_type == "response.content_part.done":
                part = data.get("part")
                if isinstance(part, dict):
                    part_text, part_sources = self._extract_response_text(
                        {"output": [{"content": [part]}]}
                    )
                    if part_text:
                        fallback_text = part_text
                    sources.extend(part_sources)
            elif not current_event_type:
                fallback_text, event_sources = self._extract_response_text(data)
                sources.extend(event_sources)

            event_type = ""

        # If we got a failure event, raise an exception so retry logic can handle it
        if error_message:
            raise ResponseFailedError(error_message)

        content = "".join(content_parts)
        if not content:
            if completed:
                content, completed_sources = self._extract_response_text(completed)
                sources.extend(completed_sources)
            elif fallback_text:
                content = fallback_text
        content = self._append_response_sources(content, sources)
        await log_info(ctx, f"content: {content}", config.debug_enabled)
        return content

    async def _execute_stream_with_retry(self, headers: dict, payload: dict, ctx=None) -> str:
        """执行带重试机制的流式 HTTP 请求"""
        connect_timeout = float(os.getenv("GROK_HTTP_CONNECT_TIMEOUT_SECONDS", "10"))
        read_timeout = float(os.getenv("GROK_HTTP_READ_TIMEOUT_SECONDS", "240"))
        write_timeout = float(os.getenv("GROK_HTTP_WRITE_TIMEOUT_SECONDS", "20"))
        timeout = httpx.Timeout(
            connect=max(1.0, connect_timeout),
            read=max(10.0, read_timeout),
            write=max(1.0, write_timeout),
            pool=None,
        )

        client = await _get_shared_client(timeout)
        parser = (
            self._parse_responses_streaming_response
            if config.openai_api_format == "responses"
            else self._parse_streaming_response
        )
        endpoint = "responses" if config.openai_api_format == "responses" else "chat/completions"
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(config.retry_max_attempts + 1),
            wait=_WaitWithRetryAfter(config.retry_multiplier, config.retry_max_wait),
            retry=retry_if_exception(_is_retryable_exception),
            reraise=True,
        ):
            with attempt:
                async with client.stream(
                    "POST",
                    f"{self.api_url.rstrip('/')}/{endpoint}",
                    headers=headers,
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    return await parser(response, ctx)

    async def describe_url(self, url: str, ctx=None) -> dict:
        """让 Grok 阅读单个 URL 并返回 title + extracts"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = self._build_payload(url_describe_prompt, url)
        result = await self._execute_stream_with_retry(headers, payload, ctx)
        title, extracts = url, ""
        for line in result.strip().splitlines():
            if line.startswith("Title:"):
                title = line[6:].strip() or url
            elif line.startswith("Extracts:"):
                extracts = line[9:].strip()
        return {"title": title, "extracts": extracts, "url": url}

    async def rank_sources(self, query: str, sources_text: str, total: int, ctx=None) -> list[int]:
        """让 Grok 按查询相关度对信源排序，返回排序后的序号列表"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = self._build_payload(
            rank_sources_prompt,
            f"Query: {query}\n\n{sources_text}",
        )
        result = await self._execute_stream_with_retry(headers, payload, ctx)
        order: list[int] = []
        seen: set[int] = set()
        for token in result.strip().split():
            try:
                n = int(token)
                if 1 <= n <= total and n not in seen:
                    seen.add(n)
                    order.append(n)
            except ValueError:
                continue
        # 补齐遗漏的序号
        for i in range(1, total + 1):
            if i not in seen:
                order.append(i)
        return order
