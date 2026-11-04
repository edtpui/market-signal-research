"""Read-only client for ptrade's API v1 (the books endpoints). GET only: Strategy Lab never creates, changes or
cancels anything on the paper host through this client.

Transports:
  HttpTransport   requests against a base URL (connection errors, timeouts and 5xx are retried with backoff;
                  a 4xx is not retried)
  LocalTransport  calls MockPtradeHost's handlers in-process, round-tripping every payload through JSON so that it
                  is exactly what HTTP would carry
"""
from __future__ import annotations

import json
import time
from typing import Callable

import requests

from strategy_lab.paper import api_models as A


class PtradeApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class HttpTransport:
    def __init__(self, base_url: str, timeout: float = 10.0, retries: int = 3, backoff: float = 0.5,
                 session: requests.Session | None = None):
        if not base_url.startswith(("http://", "https://")):
            raise ValueError(f"bad base url {base_url!r}")
        self.base = base_url.rstrip("/")
        self.timeout, self.retries, self.backoff = timeout, retries, backoff
        self.session = session or requests.Session()

    def get(self, path: str, params: dict | None = None):
        url = f"{self.base}{path}"
        last = None
        for attempt in range(self.retries + 1):
            try:
                r = self.session.get(url, params={k: v for k, v in (params or {}).items() if v is not None},
                                     timeout=self.timeout, headers={"Accept": "application/json"})
            except (requests.ConnectionError, requests.Timeout) as e:
                last = PtradeApiError(f"GET {url}: {type(e).__name__}: {e}")
            else:
                if r.status_code < 400:
                    try:
                        return r.json()
                    except ValueError:
                        raise PtradeApiError(f"GET {url}: response is not JSON", r.status_code) from None
                detail = r.text[:300]
                if r.status_code < 500:
                    raise PtradeApiError(f"GET {url}: {r.status_code} {detail}", r.status_code)
                last = PtradeApiError(f"GET {url}: {r.status_code} {detail}", r.status_code)
            if attempt < self.retries:
                time.sleep(self.backoff * (2 ** attempt))
        raise last


class LocalTransport:
    """Routes the API v1 paths to MockPtradeHost handlers."""

    def __init__(self, host):
        self.host = host

    def get(self, path: str, params: dict | None = None):
        p = {k: v for k, v in (params or {}).items() if v is not None}
        parts = [x for x in path.split("/") if x]
        try:
            if parts == ["books"]:
                out = self.host.api_books()
            elif len(parts) == 2 and parts[0] == "books":
                out = self.host.api_book(parts[1])
            elif len(parts) == 3 and parts[0] == "books":
                h: Callable = {"equity": lambda: self.host.api_equity(parts[1], p.get("from"), p.get("to")),
                               "holdings": lambda: self.host.api_holdings(parts[1], p.get("asof")),
                               "trades": lambda: self.host.api_trades(parts[1], p.get("from"), p.get("to"),
                                                                      int(p.get("limit", 2000))),
                               "picks": lambda: self.host.api_picks(parts[1], p.get("asof"))}.get(parts[2])
                if h is None:
                    raise PtradeApiError(f"GET {path}: 404 not found", 404)
                out = h()
            else:
                raise PtradeApiError(f"GET {path}: 404 not found", 404)
        except KeyError as e:
            raise PtradeApiError(f"GET {path}: 404 {e}", 404) from None
        return json.loads(json.dumps(out, default=str))


class PtradeApiClient:
    def __init__(self, transport):
        self.t = transport

    def books(self) -> list[A.Book]:
        data = self.t.get("/books")
        if not isinstance(data, list):
            raise PtradeApiError("GET /books: expected a list")
        return [A.Book.model_validate(x) for x in data]

    def book(self, book_id: str) -> A.BookDetail:
        return A.BookDetail.model_validate(self.t.get(f"/books/{book_id}"))

    def equity(self, book_id: str, start: str | None = None, end: str | None = None) -> A.Equity:
        return A.Equity.model_validate(self.t.get(f"/books/{book_id}/equity", {"from": start, "to": end}))

    def holdings(self, book_id: str, asof: str | None = None) -> A.AsOf:
        return A.AsOf.model_validate(self.t.get(f"/books/{book_id}/holdings", {"asof": asof}))

    def trades(self, book_id: str, start: str, end: str, limit: int = 5000) -> A.AsOf:
        return A.AsOf.model_validate(self.t.get(f"/books/{book_id}/trades", {"from": start, "to": end,
                                                                               "limit": limit}))

    def picks(self, book_id: str, asof: str | None = None) -> A.Picks:
        return A.Picks.model_validate(self.t.get(f"/books/{book_id}/picks", {"asof": asof}))
