from __future__ import annotations

import json
import socket
import time
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any


class _Handler(BaseHTTPRequestHandler):
    server_version = "FakeOpenAI/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: Any) -> None:
        return None

    def do_POST(self) -> None:  # noqa: N802
        size = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(size).decode("utf-8"))
        model = str(body.get("model") or "")
        self.server.last_requests[model] = body
        counts = self.server.request_counts  # type: ignore[attr-defined]
        counts[model] = counts.get(model, 0) + 1
        if model == "error-401":
            return self._error(401, "invalid_api_key")
        if model == "error-404":
            return self._error(404, "model_not_found")
        if (model == 'glm-low-output' and body.get('max_tokens',16) <= 16
                and (body.get('thinking') or {}).get('type') != 'disabled'):
            # Reproduce the observed GLM usage: all 16 output tokens are reasoning.
            if body.get('stream'):
                return self._sse([_chunk(model,{'reasoning_content':'thinking only'}),
                    _chunk(model,{},finish_reason='length',usage={'prompt_tokens':8,'completion_tokens':16,'total_tokens':24})])
            response = _completion(model,'')
            response['choices'][0]['message']['reasoning_content']='thinking only'
            response['choices'][0]['finish_reason']='length'
            return self._json(response)
        if model == "error-429":
            return self._error(429, "UPSTREAM-SECRET-BODY")
        if model.startswith("rate-limit-once-") and counts[model] == 1:
            retry_after = {
                "rate-limit-once-valid": "0.01",
                "rate-limit-once-missing": None,
                "rate-limit-once-malformed": "later",
            }[model]
            return self._error(429, "UPSTREAM-SECRET-BODY", retry_after=retry_after)
        if model == "timeout":
            time.sleep(0.25)
            return self._json(_completion(model, "late"))
        if model == "disconnect":
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "1024")
            self.end_headers()
            self.wfile.write(b'{"id":"incomplete"')
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return None

        messages = body.get("messages") or []
        prompt = _message_text(messages[-1]) if messages else ""
        stream = bool(body.get("stream"))
        submission = getattr(self.server,'skill_submission',None)
        if submission is not None and any(t.get('function',{}).get('name')=='submit_skill_draft' for t in body.get('tools',[])):
            completion=_completion(model,'')
            completion['choices'][0].update(finish_reason='tool_calls',message={'role':'assistant','content':None,
                'tool_calls':[{'id':'draft-http','type':'function','function':{'name':'submit_skill_draft','arguments':json.dumps(submission,ensure_ascii=False)}}]})
            return self._json(completion)
        if stream:
            if body.get("tools"):
                return self._tool_stream(model)
            return self._text_stream(model)
        if any(message.get("role") == "tool" for message in messages):
            return self._json(_completion(model, "tool-resume-ok"))
        return self._json(_completion(model, f"text-ok:{prompt}"))

    def _error(self, status: int, code: str, *, retry_after: str | None = None) -> None:
        payload = {"error": {"message": code, "type": code, "code": code}}
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        if retry_after is not None:
            self.send_header("Retry-After", retry_after)
        self.end_headers()
        self.wfile.write(encoded)

    def _json(self, payload: dict) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _sse(self, chunks: list[dict]) -> None:
        encoded = ("".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
                   + "data: [DONE]\n\n").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
        self.wfile.flush()

    def _text_stream(self, model: str) -> None:
        self._sse([
            _chunk(model, {"content": "stream-"}),
            _chunk(model, {"content": "ok"}),
            _chunk(model, {}, finish_reason="stop", usage={
                "prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4,
            }),
        ])

    def _tool_stream(self, model: str) -> None:
        self._sse([
            _chunk(model, {"tool_calls": [{
                "index": 0,
                "id": "call-contract",
                "type": "function",
                "function": {"name": "contract_tool", "arguments": "{\"value\":"},
            }]}),
            _chunk(model, {"tool_calls": [{
                "index": 0,
                "function": {"arguments": "2}"},
            }]}),
            _chunk(model, {}, finish_reason="tool_calls", usage={
                "prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5,
            }),
        ])


def _message_text(message: dict) -> str:
    content = message.get("content")
    return content if isinstance(content, str) else ""


def _completion(model: str, text: str) -> dict:
    return {
        "id": "chatcmpl-contract",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
    }


def _chunk(
    model: str, delta: dict, *, finish_reason: str | None = None,
    usage: dict | None = None,
) -> dict:
    return {
        "id": "chatcmpl-contract",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        **({"usage": usage} if usage is not None else {}),
    }


class FakeProviderServer(AbstractContextManager["FakeProviderServer"]):
    def __init__(self,*,skill_submission=None) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.request_counts = {}  # type: ignore[attr-defined]
        self._server.last_requests = {}
        self._server.skill_submission = skill_submission
        self._thread = Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}/v1"

    def request_count(self, model: str) -> int:
        return int(self._server.request_counts.get(model, 0))  # type: ignore[attr-defined]

    def last_request(self, model: str) -> dict:
        return self._server.last_requests.get(model,{})

    def __enter__(self) -> "FakeProviderServer":
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
