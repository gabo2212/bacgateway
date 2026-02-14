from __future__ import annotations

import base64

from gateway.niagara_client import NiagaraClient, _Response, detect_login_scheme


def test_detect_cookie_digest_scheme_from_hidden_input() -> None:
    html = """
    <html>
      <body>
        <form action="/login">
          <input type='hidden' id='scheme' value='cookieDigest'/>
        </form>
      </body>
    </html>
    """
    assert detect_login_scheme(html) == "cookieDigest"


def test_detect_login_scheme_returns_none_when_missing() -> None:
    html = "<html><body><form><input type='text' name='username'/></form></body></html>"
    assert detect_login_scheme(html) is None


def test_cookie_digest_support_flow_uses_login_support_actions(monkeypatch) -> None:
    client = NiagaraClient(host="example.com", username="user", password="pass")
    posted: list[tuple[str, str, str | None]] = []

    def fake_request_once(method, path, ord_query, data, headers):
        if method == "POST":
            body = data.decode("utf-8") if data else ""
            posted.append((path, body, (headers or {}).get("Content-Type")))
            if "sendClientFirstMessage" in body:
                first_msg = body.split("clientFirstMessage=", 1)[1]
                nonce = first_msg.split("r=", 1)[1]
                salt = base64.b64encode(b"salt").decode("ascii")
                return _Response(status=200, body=f"r={nonce}srv,s={salt},i=4096", headers={})
            if "sendClientFinalMessage" in body:
                return _Response(status=200, body="", headers={})
        return _Response(status=200, body="", headers={})

    monkeypatch.setattr(client, "_request_once", fake_request_once)
    client._login_cookie_digest_support_flow()

    assert len(posted) == 2
    assert posted[0][0] == "/login/"
    assert posted[0][2] == "application/x-niagara-login-support"
    assert posted[0][1].startswith("action=sendClientFirstMessage&clientFirstMessage=n,,")
    assert posted[1][1].startswith("action=sendClientFinalMessage&clientFinalMessage=c=biws,")
