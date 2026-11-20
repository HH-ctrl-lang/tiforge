"""Invoke AgentArts Runtime and stream its JSON events to a file."""

from getpass import getpass
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
from uuid import uuid4

import requests


DEFAULT_QUERY = (
    "设计一种生物医用低模量β钛合金。允许使用Ti、Nb、Zr、Ta、Sn、Mo，"
    "禁止使用V、Al、Ni。Ta允许使用但尽量减少。采用wt.%，生成两个候选方案。"
)
BASE_DIR = Path(__file__).resolve().parents[1]
LOCAL_SETTINGS = BASE_DIR / "config" / "runtime.local.json"


def read_credentials():
    url = os.environ.get("AGENTARTS_API_URL", "").strip()
    key = os.environ.get("AGENTARTS_API_KEY", "").strip()
    if not (url and key) and LOCAL_SETTINGS.is_file():
        try:
            settings = json.loads(LOCAL_SETTINGS.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"runtime.local.json 格式错误，请检查第 {exc.lineno} 行、第 {exc.colno} 列。"
            ) from None
        if not isinstance(settings, dict):
            raise ValueError("runtime.local.json 必须是 JSON 对象。")
        if not url:
            url = read_setting(settings, "api_url")
        if not key:
            key = read_setting(settings, "api_key")

    if not url:
        url = input("请输入 AgentArts API 调用路径：").strip()
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("请输入不含账号或密码的完整 HTTPS 调用地址。")
    if not key:
        key = getpass("请输入 API Key：").strip()
    if not key:
        raise ValueError("API Key 不能为空。")
    return url, key


def read_setting(settings, name):
    value = settings.get(name, "")
    if not isinstance(value, str):
        raise ValueError(f"runtime.local.json 中的 {name} 必须是字符串。")
    return value.strip()


def capture_events(url, key, inputs, output, session_id, request_id):
    headers = {
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "X-Invoke-Mode": "debug",
        "x-hw-agentarts-session-id": session_id,
        "X-Request-Id": request_id,
    }
    count = 0
    finished = False
    with output.open("x", encoding="utf-8") as stream:
        try:
            with requests.post(
                url, headers=headers, json={"inputs": inputs}, stream=True,
                timeout=(15, 300), allow_redirects=False,
            ) as response:
                code = response.status_code
                print(f"HTTP 状态码：{code}")
                if not 200 <= code < 300:
                    return {"kind": "http_error", "http_status": code}

                response.encoding = "utf-8"
                for line in response.iter_lines(decode_unicode=True):
                    if not line or not line.startswith("data:"):
                        continue
                    content = line[5:].strip()
                    if not content or content == "[DONE]":
                        continue
                    try:
                        event = json.loads(content)
                    except json.JSONDecodeError:
                        print("收到无法解析的 SSE 数据。")
                        continue
                    if not isinstance(event, dict):
                        continue
                    stream.write(content + "\n")
                    stream.flush()
                    count += 1
                    name = event.get("event", "unknown")
                    finished = finished or name == "workflow_finished"
                    print(f"[事件 {count}] {name}")
        except requests.exceptions.Timeout:
            return {"kind": "request_timeout"}
        except requests.exceptions.RequestException as exc:
            return {"kind": "request_error", "exception_type": type(exc).__name__}
        except KeyboardInterrupt:
            return {"kind": "interrupted"}

    if not finished:
        return {"kind": "incomplete_stream" if count else "no_events"}
    return None


def main():
    """Keep the original standalone smoke test entry point."""
    url, key = read_credentials()
    output = BASE_DIR / "scratch" / "runtime_smoke_events.jsonl"
    output.parent.mkdir(exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid4().hex}")
    result = capture_events(
        url, key, {"query": DEFAULT_QUERY}, temporary, str(uuid4()), str(uuid4())
    )
    temporary.replace(output)
    if result:
        print(f"采集异常：{result['kind']}")
        return 1
    print(f"原始事件：{output.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
