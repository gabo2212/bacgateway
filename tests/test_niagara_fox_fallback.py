from __future__ import annotations

from gateway.niagara_client import NiagaraClient, WriteResult, _Response


def test_invoke_action_ord_uses_fox_fallback_when_http_fails(monkeypatch) -> None:
    client = NiagaraClient(host="example.com", username="user", password="pass")
    calls: list[tuple[str, str, float | None]] = []

    def fake_request_with_reauth(method, path, ord_query, data, headers, probe_ord_path=None, retried=False):
        return _Response(status=404, body="<html>not found</html>", headers={})

    def fake_fox_fallback(*, ord_path: str, action_name: str, numeric_arg: float | None) -> WriteResult:
        calls.append((ord_path, action_name, numeric_arg))
        return WriteResult(ok=True, status=200, error=None, response_body="fox ok")

    monkeypatch.setattr(client, "_request_with_reauth", fake_request_with_reauth)
    monkeypatch.setattr(client, "_invoke_action_ord_via_fox", fake_fox_fallback)

    result = client.invoke_action_ord(
        "station:|slot:/Drivers/WirelessTstatNetwork/Device2/points/OccupiedHeatingSetpoint/override(87.3)",
        ensure_auth=False,
        numeric_arg=None,
    )

    assert result.ok is True
    assert result.status == 200
    assert calls == [
        (
            "station:|slot:/Drivers/WirelessTstatNetwork/Device2/points/OccupiedHeatingSetpoint",
            "override",
            87.3,
        )
    ]


def test_fox_fallback_requires_session_cookie() -> None:
    client = NiagaraClient(host="example.com", username="user", password="pass")
    result = client._invoke_action_ord_via_fox(
        ord_path="station:|slot:/foo",
        action_name="override",
        numeric_arg=82.3,
    )

    assert result.ok is False
    assert result.error is not None
    assert "missing niagara_session cookie" in result.error
