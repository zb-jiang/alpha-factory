"""统一 LLM Agent 调用封装。

提供 AgentConfig 数据类和 call_llm_agent 函数，处理：
- 代理环境变量清理
- httpx 客户端创建
- OpenAI SDK 调用
- 超时、重试、JSON 解析
"""
from __future__ import annotations

import json
import os
import re
import time
import threading
from dataclasses import dataclass
from typing import Any

import httpx
from openai import OpenAI


_LOG_LOCK = threading.Lock()


def _log(message: str) -> None:
    with _LOG_LOCK:
        print(message, flush=True)


@dataclass
class AgentConfig:
    """Agent LLM 配置。"""

    model: str
    base_url: str
    api_key: str
    temperature: float = 0.2
    timeout_seconds: float = 60.0
    max_retries: int = 2
    request_name: str = ""


def _create_http_client() -> httpx.Client:
    """创建 httpx 客户端，清除所有代理环境变量。"""
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        os.environ.pop(key, None)
    try:
        return httpx.Client(verify=False, trust_env=False, proxy=None)
    except TypeError:
        return httpx.Client(verify=False, trust_env=False)


def _parse_json_from_text(text: str) -> Any:
    """从大模型输出中提取并解析 JSON，兼容 Markdown 代码块和脏输出。"""
    stripped = text.strip()
    if not stripped:
        raise ValueError("LLM 返回空内容")
    # 提取 Markdown 代码块
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()
    # 直接解析
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    # 尝试从文本中提取 JSON 对象或数组
    object_match = re.search(r"(\{.*\})", stripped, flags=re.DOTALL)
    if object_match:
        return json.loads(object_match.group(1))
    array_match = re.search(r"(\[.*\])", stripped, flags=re.DOTALL)
    if array_match:
        return json.loads(array_match.group(1))
    raise ValueError("无法从大模型输出中提取 JSON")


def call_llm_agent(config: AgentConfig, messages: list[dict[str, str]]) -> dict[str, Any]:
    """调用 LLM Agent，返回解析后的 JSON 字典。

    处理超时、重试、JSON 解析错误。如果所有重试都失败，抛出 RuntimeError。
    """
    http_client = _create_http_client()
    client = OpenAI(api_key=config.api_key, base_url=config.base_url, http_client=http_client)
    caller_name = str(config.request_name or config.model)

    last_error: Exception | None = None
    for attempt in range(config.max_retries + 1):
        _log(
            f"    [LLM Agent] {caller_name} | model={config.model} | 发起请求 "
            f"({attempt + 1}/{config.max_retries + 1}, timeout={config.timeout_seconds}s)"
        )
        started_at = time.monotonic()
        try:
            response = client.chat.completions.create(
                model=config.model,
                messages=messages,
                temperature=config.temperature,
                timeout=config.timeout_seconds,
            )
            content = response.choices[0].message.content or ""
            parsed = _parse_json_from_text(content)
            elapsed = time.monotonic() - started_at
            _log(
                f"    [LLM Agent] {caller_name} | model={config.model} | 请求成功，耗时 {elapsed:.1f}s"
            )
            return parsed
        except Exception as error:
            last_error = error
            elapsed = time.monotonic() - started_at
            if attempt >= config.max_retries:
                raise RuntimeError(
                    f"LLM Agent 调用失败: model={config.model}, "
                    f"timeout={config.timeout_seconds}s, retries={config.max_retries}, error={error}"
                ) from error
            wait_seconds = 3.0 * (attempt + 1)
            _log(
                f"    [LLM Agent] {caller_name} | model={config.model} | 第 {attempt + 1} 次失败，"
                f"耗时 {elapsed:.1f}s，{wait_seconds:.1f}s 后重试: {error}"
            )
            time.sleep(wait_seconds)

    # 理论上不会执行到这里，但保留以防万一
    raise RuntimeError(f"LLM Agent 调用失败: {last_error}")
