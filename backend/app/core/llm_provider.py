import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any

from app.core.settings import AgentLLMSettings, load_settings


def _repair_json_trailing_commas(s: str) -> str:
    """去掉 JSON 中非法的尾逗号（部分模型会输出）。"""
    out = s
    for _ in range(8):
        nxt = re.sub(r",(\s*[\]}])", r"\1", out)
        if nxt == out:
            break
        out = nxt
    return out


def _repair_unbalanced_braces(s: str) -> str:
    """部分模型会漏写最外层 `}`（如 intent 只输出到 ...}] ），导致 json.loads 失败。"""
    t = s.strip()
    if not t.startswith("{"):
        return s
    out = t
    guard = 0
    while out.count("{") > out.count("}") and guard < 8:
        out += "}"
        guard += 1
    return out


def _extract_balanced_json_object(s: str) -> str | None:
    """从模型输出中截取最外层平衡 `{...}`，并尊重字符串内的引号与转义。"""
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    i = start
    n = len(s)
    in_string = False
    escape = False
    while i < n:
        c = s[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
        else:
            if c == '"':
                in_string = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return s[start : i + 1]
        i += 1
    return None


def _json_parse_variants(raw: str) -> list[str]:
    """生成若干候选串，依次尝试 json.loads。"""
    t = raw.replace("\ufeff", "").strip()
    t = t.replace("```json", "").replace("```", "").strip()
    t_fixed = _repair_unbalanced_braces(t)
    t_repaired = _repair_json_trailing_commas(t)
    t_both = _repair_json_trailing_commas(t_fixed)
    candidates: list[str] = [
        t,
        t_fixed,
        t_repaired,
        t_both,
    ]
    extracted = _extract_balanced_json_object(t)
    extracted_fixed = _extract_balanced_json_object(t_fixed)
    for ex in (extracted, extracted_fixed):
        if ex:
            candidates.extend([ex, _repair_json_trailing_commas(ex)])
    out: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _llm_retry_attempts() -> int:
    """环境变量 LLM_INVOKE_RETRIES：额外重试次数（默认 2，即最多共 3 次调用）。"""
    raw = os.getenv("LLM_INVOKE_RETRIES", "2")
    try:
        return max(0, min(5, int(raw)))
    except ValueError:
        return 2


def _is_transient_llm_error(exc: BaseException) -> bool:
    """Ollama/代理瞬时 502、连接抖动等可短退避重试。"""
    if isinstance(exc, (TimeoutError, FutureTimeoutError, ConnectionError, OSError)):
        return True
    s = f"{type(exc).__name__} {str(exc)}".lower()
    return any(
        x in s
        for x in (
            "502",
            "503",
            "504",
            "responseerror",
            "bad gateway",
            "connection reset",
            "connection aborted",
            "broken pipe",
            "timed out",
        )
    )


class LLMRouter:
    """统一封装本地/云端模型切换；异常时允许上层回退规则逻辑。"""

    def __init__(self, settings: AgentLLMSettings) -> None:
        self.settings = settings
        self._llm = self._build_llm()
        self._invoke_timeout_s = float(settings.llm_invoke_timeout_s)
        self._llm_extra_retries = _llm_retry_attempts()
        self._log_llm_response = load_settings().llm_response_log_enabled
        self._log_preview_chars = 1200

    def _emit_llm_response_log(self, text: str) -> None:
        if not self._log_llm_response:
            return
        preview = text if len(text) <= self._log_preview_chars else text[: self._log_preview_chars] + " ...[truncated]"
        # 使用 print 避免被 logging 级别过滤，确保在后端控制台可见。
        print(f"[LLM_RESPONSE] {preview}", flush=True)

    def _build_llm(self) -> Any:
        if not self.settings.use_local:
            if not self.settings.cloud_llm_api_key:
                return None
            try:
                from langchain_openai import ChatOpenAI
            except Exception:
                return None

            kwargs: dict[str, Any] = {
                "model": self.settings.cloud_llm_model,
                "api_key": self.settings.cloud_llm_api_key,
                "temperature": self.settings.llm_temperature,
            }
            if self.settings.cloud_llm_base_url:
                kwargs["base_url"] = self.settings.cloud_llm_base_url
            return ChatOpenAI(**kwargs)

        try:
            from langchain_ollama import ChatOllama
        except Exception:
            return None

        timeout_s = float(self.settings.ollama_http_timeout_s)
        # httpx 默认 trust_env=True 会走系统 HTTP(S)_PROXY，本机 Ollama 常被误转发成 502
        client_kw: dict[str, Any] = {"trust_env": False}
        if timeout_s > 0:
            client_kw["timeout"] = timeout_s
        ollama_kw: dict[str, Any] = {
            "model": self.settings.local_llm_model,
            "temperature": self.settings.llm_temperature,
            "base_url": self.settings.ollama_base_url,
            "sync_client_kwargs": client_kw,
            "async_client_kwargs": dict(client_kw),
            # None 时部分思考模型会默认长推理；显式传参关闭/开启（见 settings.ollama_reasoning）
            "reasoning": self.settings.ollama_reasoning,
        }
        return ChatOllama(**ollama_kw)

    def invoke_text(self, prompt: str) -> str:
        if self._llm is None:
            raise RuntimeError("LLM is not configured")
        attempts = 1 + self._llm_extra_retries
        for attempt in range(attempts):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self._llm.invoke, prompt)
                try:
                    resp = future.result(timeout=self._invoke_timeout_s)
                except FutureTimeoutError as exc:
                    future.cancel()
                    te = TimeoutError(f"LLM invoke timeout after {self._invoke_timeout_s:.1f}s")
                    te.__cause__ = exc
                    if attempt + 1 < attempts and _is_transient_llm_error(te):
                        time.sleep(0.8 * (attempt + 1))
                        continue
                    raise te from exc
                except BaseException as exc:
                    if attempt + 1 < attempts and _is_transient_llm_error(exc):
                        time.sleep(0.8 * (attempt + 1))
                        continue
                    raise
            content = getattr(resp, "content", "")
            if isinstance(content, str):
                text = content.strip()
                self._emit_llm_response_log(text)
                return text
            if isinstance(content, list):
                text = " ".join(str(x) for x in content).strip()
                self._emit_llm_response_log(text)
                return text
            text = str(content).strip()
            self._emit_llm_response_log(text)
            return text
        raise RuntimeError("LLM invoke failed without response")

    def invoke_json(self, prompt: str) -> dict[str, Any]:
        text = self.invoke_text(prompt)
        last_err: json.JSONDecodeError | None = None
        for variant in _json_parse_variants(text):
            try:
                data = json.loads(variant)
            except json.JSONDecodeError as e:
                last_err = e
                continue
            if isinstance(data, dict):
                return data
        detail = f"{last_err}" if last_err else "not a JSON object"
        snippet = text if len(text) <= 400 else text[:400] + "..."
        raise ValueError(
            f"LLM returned text that is not valid JSON object ({detail}). Raw preview: {snippet!r}"
        ) from last_err

    def get_llm(self) -> Any:
        return self._llm
