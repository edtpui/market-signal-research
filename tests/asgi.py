"""A minimal synchronous ASGI client (the venv has no httpx, which Starlette's TestClient needs)."""
from __future__ import annotations

import json
from urllib.parse import urlencode

import anyio


class Response:
    def __init__(self, status: int, headers: list, body: bytes):
        self.status_code, self.headers, self.content = status, headers, body

    def json(self):
        return json.loads(self.content)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")


class Client:
    def __init__(self, app):
        self.app = app

    def request(self, method: str, path: str, params: dict | None = None, json_body=None) -> Response:
        body = b"" if json_body is None else json.dumps(json_body).encode()
        query = urlencode({k: v for k, v in (params or {}).items() if v is not None})
        scope = dict(type="http", asgi={"version": "3.0"}, http_version="1.1", method=method, scheme="http",
                     path=path, raw_path=path.encode(), query_string=query.encode(), root_path="",
                     headers=[(b"host", b"test"), (b"content-type", b"application/json"),
                              (b"content-length", str(len(body)).encode())],
                     client=("127.0.0.1", 1), server=("test", 80))
        out = dict(status=None, headers=[], body=b"")
        sent = {"done": False}

        async def receive():
            if sent["done"]:
                return {"type": "http.disconnect"}
            sent["done"] = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(msg):
            if msg["type"] == "http.response.start":
                out["status"], out["headers"] = msg["status"], msg.get("headers", [])
            elif msg["type"] == "http.response.body":
                out["body"] += msg.get("body", b"")

        anyio.run(self.app, scope, receive, send)
        return Response(out["status"], out["headers"], out["body"])

    def get(self, path, params=None):
        return self.request("GET", path, params)

    def post(self, path, json=None, params=None):
        return self.request("POST", path, params, json)

    def delete(self, path):
        return self.request("DELETE", path)
