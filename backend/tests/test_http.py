"""HTTP 层集成测试：不依赖数据库（绕过启动期建池），验证路由/校验/序列化。

真正连 postgres:16-alpine 的端到端在 docker compose 环境核对；
内核 SQL 行为本身由 test_sqlgen.py / test_expected_queries.py 覆盖。
"""
from __future__ import annotations

import asyncio
import json

import pytest

fastapi = pytest.importorskip("fastapi")

from app.main import create_app
from app.modeling.loader import load_sample_model
from app.modeling.validator import validate_model


class _FakeStore:
    def __init__(self, model):
        self._model = model

    async def get(self):
        return self._model

    async def save(self, model):
        self._model = model


class TinyClient:
    """最小 ASGI 调用器：不引第三方测试库，避免 httpx 版本差异。"""

    def __init__(self, app):
        self.app = app

    def _request(self, method, path, body=None):
        async def run():
            received = {"body": b""}

            async def receive():
                if received["body"]:
                    return {"type": "http.disconnect"}
                received["body"] = b"" if body is None else json.dumps(body).encode()
                return {"type": "http.request", "body": received["body"]}

            response = {}

            async def send(message):
                if message["type"] == "http.response.start":
                    response["status"] = message["status"]
                    response["headers"] = {
                        k.decode(): v.decode()
                        for k, v in message["headers"]
                    }
                elif message["type"] == "http.response.body":
                    response.setdefault("chunks", []).append(message["body"])

            scope = {
                "type": "http",
                "http_version": "1.1",
                "method": method,
                "scheme": "http",
                "path": path,
                "raw_path": path.encode(),
                "query_string": b"",
                "headers": [
                    (b"host", b"test"),
                    (b"content-type", b"application/json"),
                ],
                "client": ("testclient", 123),
                "server": ("testserver", 80),
            }
            await self.app(scope, receive, send)
            return response

        resp = asyncio.run(run())
        raw = b"".join(resp.get("chunks", []))
        return _Response(resp["status"], raw, resp.get("headers", {}))


class _Response:
    def __init__(self, status, raw, headers):
        self.status_code = status
        self.text = raw.decode()
        self.headers = headers

    def json(self):
        return json.loads(self.text)


@pytest.fixture()
def client():
    app = create_app()
    app.state.store = _FakeStore(load_sample_model())
    app.state.pool = None
    return TinyClient(app)


def test_health(client):
    r = client._request("GET", "/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_get_model(client):
    r = client._request("GET", "/api/model")
    assert r.status_code == 200
    body = r.json()
    assert len(body["tables"]) == 5
    assert body["hierarchies"][0]["id"] == "h_geo"


def test_explain_single_table(client):
    r = client._request("POST", "/api/query/explain", {
        "rows": [{"dimension": "dim_order_status"}],
        "measures": ["m_order_total"],
        "filters": [{
            "kind": "dimension", "target": "dim_order_channel",
            "op": "eq", "value": "web",
        }],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "JOIN" not in body["sql"].upper()
    assert body["joined_tables"] == ["orders"]
    assert "web" not in body["sql"]
    assert "web" in body["params"]
    assert [c["key"] for c in body["columns"]] == [
        "dim_order_status", "m_order_total"]


def test_explain_fanout(client):
    r = client._request("POST", "/api/query/explain", {
        "rows": [{"dimension": "dim_product_category"}],
        "measures": ["m_order_total"],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["fanout_tables"] == ["orders"]
    assert "SELECT DISTINCT" in body["sql"]


def test_bad_query_returns_400(client):
    r = client._request("POST", "/api/query/explain",
                        {"rows": [], "measures": []})
    assert r.status_code == 400


def test_unknown_measure_400(client):
    r = client._request("POST", "/api/query/explain", {"measures": ["nope"]})
    assert r.status_code == 400


def test_validate_expression_ok(client):
    r = client._request("POST", "/api/validate-expression", {
        "table": "orders",
        "expression": "IF(total_amount > 1000, UPPER(status), 'x')",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["valid"] is True
    assert set(body["referenced"]) >= {"total_amount", "status"}


def test_validate_expression_error_position(client):
    r = client._request("POST", "/api/validate-expression", {
        "table": "orders",
        "expression": "total_amount + ",
    })
    body = r.json()
    assert body["valid"] is False
    err = body["error"]
    assert err["line"] == 1 and isinstance(err["col"], int)


def test_validate_expression_rejects_non_whitelist(client):
    r = client._request("POST", "/api/validate-expression", {
        "table": "orders",
        "expression": "EVIL(total_amount)",
    })
    body = r.json()
    assert body["valid"] is False
    assert "白名单" in body["error"]["message"]


def test_sample_model_is_valid():
    assert validate_model(load_sample_model()) == []


def test_put_model_roundtrip(client):
    model = load_sample_model().model_dump()
    r = client._request("PUT", "/api/model", {"model": model})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_put_model_rejects_bad_relation(client):
    model = load_sample_model().model_dump()
    model["relations"][0]["left_column"] = "no_such_column"
    r = client._request("PUT", "/api/model", {"model": model})
    assert r.status_code == 400
    assert "errors" in r.json()["detail"]
