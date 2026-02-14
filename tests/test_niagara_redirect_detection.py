from __future__ import annotations

from gateway.niagara_client import NiagaraClient, _Response


def test_redirect_302_then_obix_200_sets_login_true(monkeypatch) -> None:
    client = NiagaraClient(
        host="example.com",
        username="user",
        password="pass",
    )

    responses = [
        _Response(status=302, body="", headers={"Location": "/login"}),
        _Response(
            status=200,
            body='<obj><real val="71.0"/></obj>',
            headers={"Content-Type": "application/xml"},
        ),
    ]
    login_calls = {"count": 0}

    def fake_request_once(method, path, ord_query, data, headers):
        return responses.pop(0)

    def fake_login(probe_ord_path=None) -> None:
        login_calls["count"] += 1

    monkeypatch.setattr(client, "_request_once", fake_request_once)
    monkeypatch.setattr(client, "login", fake_login)

    result = client._request_with_reauth(
        method="GET",
        path="/ord",
        ord_query="station:|slot:/foo/out",
        data=None,
        headers=None,
    )

    assert result.status == 200
    assert client.login_ok is True
    assert client.login_reason == "probe_ok"
    assert login_calls["count"] == 1


def test_redirect_302_after_retry_keeps_login_false(monkeypatch) -> None:
    client = NiagaraClient(
        host="example.com",
        username="user",
        password="pass",
    )

    responses = [
        _Response(status=302, body="", headers={"Location": "/login"}),
        _Response(status=302, body="", headers={"Location": "/login"}),
    ]
    login_calls = {"count": 0}

    def fake_request_once(method, path, ord_query, data, headers):
        return responses.pop(0)

    def fake_login(probe_ord_path=None) -> None:
        login_calls["count"] += 1

    monkeypatch.setattr(client, "_request_once", fake_request_once)
    monkeypatch.setattr(client, "login", fake_login)

    result = client._request_with_reauth(
        method="GET",
        path="/ord",
        ord_query="station:|slot:/bar/out",
        data=None,
        headers=None,
    )

    assert result.status == 302
    assert client.login_ok is False
    assert client.login_reason == "login_failed_302"
    assert login_calls["count"] == 1
