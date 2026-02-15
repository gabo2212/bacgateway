from __future__ import annotations

import base64
import hashlib
import hmac
import html as html_lib
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import urllib.response
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from http.cookiejar import Cookie, CookieJar
from typing import Mapping
from urllib.error import HTTPError, URLError

logger = logging.getLogger(__name__)

_LOGIN_MARKERS = (
    "<title>login",
    "name=\"username\"",
    "name='username'",
    "id=\"scheme\"",
    "id='scheme'",
    "cookieDigest",
    "j_security_check",
    "/login",
    "niagara login",
)
_OBIX_MARKERS = ("<obj", "<real")
_METHOD_NOT_ALLOWED_MARKERS = ("method not allowed", "unsupported method")
_SUPPORTED_HASHES = {"sha1", "sha256", "sha512"}
_LOGIN_SCHEME_COOKIE_DIGEST = "cookiedigest"
_INPUT_TAG_RE = re.compile(r"<input\b[^>]*>", re.IGNORECASE | re.DOTALL)
_INPUT_ATTR_RE = re.compile(
    r"""([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(['"])(.*?)\2""",
    re.IGNORECASE | re.DOTALL,
)


class NiagaraClientError(RuntimeError):
    """Base Niagara client exception."""


class NiagaraAuthError(NiagaraClientError):
    """Raised when Niagara authentication fails."""


class NiagaraParseError(NiagaraClientError):
    """Raised when Niagara payload parsing fails."""


@dataclass(frozen=True)
class NiagaraReal:
    value: float
    unit: str | None
    display: str | None
    raw_xml: str | None = None


@dataclass(frozen=True)
class WriteResult:
    ok: bool
    status: int | None
    error: str | None
    response_body: str | None


@dataclass(frozen=True)
class NiagaraLoginOptions:
    endpoint_path: str = "/login"
    content_mode: str = "auto"  # auto | form | json
    auth_mode: str = "auto"  # auto | scram | cookieDigest
    client_first_field: str = "clientFirstMessage"
    server_first_field: str = "serverFirstMessage"
    client_final_field: str = "clientFinalMessage"
    server_final_field: str = "serverFinalMessage"
    username_field: str = "username"
    password_field: str = "password"
    scheme_field: str = "scheme"
    token_field: str = "token"
    cookie_postfix_field: str = "cookiePostfix"
    support_action_field: str = "action"
    support_action_first: str = "sendClientFirstMessage"
    support_action_final: str = "sendClientFinalMessage"
    support_content_type: str = "application/x-niagara-login-support"
    hash_algorithms: tuple[str, ...] = ("sha256", "sha1")


@dataclass(frozen=True)
class _Response:
    status: int
    body: str
    headers: Mapping[str, str]


@dataclass(frozen=True)
class _LoginPage:
    response: _Response
    hidden_fields: Mapping[str, str]
    scheme: str | None


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None

    def _return_response(self, req, fp, code, msg, headers):
        return urllib.response.addinfourl(fp, headers, req.full_url, code=code)

    def http_error_301(self, req, fp, code, msg, headers):  # type: ignore[override]
        return self._return_response(req, fp, code, msg, headers)

    def http_error_302(self, req, fp, code, msg, headers):  # type: ignore[override]
        return self._return_response(req, fp, code, msg, headers)

    def http_error_303(self, req, fp, code, msg, headers):  # type: ignore[override]
        return self._return_response(req, fp, code, msg, headers)

    def http_error_307(self, req, fp, code, msg, headers):  # type: ignore[override]
        return self._return_response(req, fp, code, msg, headers)

    def http_error_308(self, req, fp, code, msg, headers):  # type: ignore[override]
        return self._return_response(req, fp, code, msg, headers)


def _scram_escape(value: str) -> str:
    return value.replace("=", "=3D").replace(",", "=2C")


def _parse_scram_message(message: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for part in message.split(","):
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        parsed[key] = val
    return parsed


def _clean_hash_name(hash_name: str) -> str:
    name = hash_name.strip().lower().replace("-", "")
    if name not in _SUPPORTED_HASHES:
        raise NiagaraAuthError(f"Unsupported SCRAM hash algorithm: {hash_name}")
    return name


def _extract_input_attributes(tag: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in _INPUT_ATTR_RE.finditer(tag):
        key = match.group(1).strip().lower()
        value = html_lib.unescape(match.group(3))
        attrs[key] = value
    return attrs


def _extract_hidden_inputs(html_text: str) -> dict[str, str]:
    hidden: dict[str, str] = {}
    for tag in _INPUT_TAG_RE.findall(html_text):
        attrs = _extract_input_attributes(tag)
        input_type = attrs.get("type", "").strip().lower()
        if input_type and input_type != "hidden":
            continue
        value = attrs.get("value", "")
        for key in (attrs.get("name"), attrs.get("id")):
            if not key:
                continue
            hidden[key] = value
            hidden[key.lower()] = value
    return hidden


def _normalize_login_scheme(value: str | None) -> str | None:
    if not value:
        return None
    compact = value.strip().lower().replace("-", "").replace("_", "")
    return compact or None


def detect_login_scheme(html_text: str) -> str | None:
    hidden = _extract_hidden_inputs(html_text)
    scheme = hidden.get("scheme")
    return scheme.strip() if isinstance(scheme, str) and scheme.strip() else None


def scram_client_first(username: str) -> tuple[str, str, str]:
    if not username:
        raise NiagaraAuthError("Username is required for SCRAM client first message")
    nonce = base64.b64encode(os.urandom(18)).decode("ascii").rstrip("=")
    first_bare = f"n={_scram_escape(username)},r={nonce}"
    first_message = f"n,,{first_bare}"
    return nonce, first_bare, first_message


def scram_client_final(
    password: str,
    username: str,
    client_nonce: str,
    server_first_message: str,
    hash_name: str = "sha256",
) -> tuple[str, str]:
    if not password:
        raise NiagaraAuthError("Password is required for SCRAM client final message")
    hash_name = _clean_hash_name(hash_name)
    attrs = _parse_scram_message(server_first_message)

    server_nonce = attrs.get("r")
    salt_b64 = attrs.get("s")
    iteration_text = attrs.get("i")
    if not server_nonce or not salt_b64 or not iteration_text:
        raise NiagaraAuthError("Invalid SCRAM server first message")
    if not server_nonce.startswith(client_nonce):
        raise NiagaraAuthError("SCRAM server nonce does not include client nonce")

    try:
        iterations = int(iteration_text)
    except ValueError as exc:  # pragma: no cover - defensive
        raise NiagaraAuthError("Invalid SCRAM iteration count") from exc

    first_bare = f"n={_scram_escape(username)},r={client_nonce}"
    final_wo_proof = f"c=biws,r={server_nonce}"

    digest_size = hashlib.new(hash_name).digest_size
    salted_password = hashlib.pbkdf2_hmac(
        hash_name,
        password.encode("utf-8"),
        base64.b64decode(salt_b64),
        iterations,
        dklen=digest_size,
    )

    client_key = hmac.new(salted_password, b"Client Key", hash_name).digest()
    stored_key = hashlib.new(hash_name, client_key).digest()
    auth_message = f"{first_bare},{server_first_message},{final_wo_proof}".encode("utf-8")
    client_signature = hmac.new(stored_key, auth_message, hash_name).digest()
    client_proof = bytes(a ^ b for a, b in zip(client_key, client_signature))
    proof_b64 = base64.b64encode(client_proof).decode("ascii")

    server_key = hmac.new(salted_password, b"Server Key", hash_name).digest()
    server_signature = hmac.new(server_key, auth_message, hash_name).digest()
    expected_server_signature = base64.b64encode(server_signature).decode("ascii")

    final_message = f"{final_wo_proof},p={proof_b64}"
    return final_message, expected_server_signature


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def parse_obix_real(xml_text: str) -> NiagaraReal:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise NiagaraParseError("Malformed Niagara oBIX XML payload") from exc

    real_element = None
    if _local_name(root.tag) == "real":
        real_element = root
    else:
        for elem in root.iter():
            if _local_name(elem.tag) == "real":
                real_element = elem
                break

    if real_element is None:
        raise NiagaraParseError("Niagara payload did not contain an oBIX <real> element")

    val_text = real_element.attrib.get("val")
    if val_text is None:
        raise NiagaraParseError("oBIX <real> element missing required 'val' attribute")

    try:
        value = float(val_text)
    except ValueError as exc:
        raise NiagaraParseError(f"Invalid oBIX real value: {val_text}") from exc

    return NiagaraReal(
        value=value,
        unit=real_element.attrib.get("unit"),
        display=real_element.attrib.get("display"),
        raw_xml=xml_text,
    )


class NiagaraClient:
    def __init__(
        self,
        host: str,
        scheme: str = "http",
        username: str | None = None,
        password: str | None = None,
        session_cookie: str | None = None,
        timeout_sec: float = 10.0,
        login_options: NiagaraLoginOptions | None = None,
        probe_ord_path: str | None = None,
    ) -> None:
        self.host = host.strip()
        self.scheme = scheme.strip()
        self.username = username
        self.password = password
        self.timeout_sec = float(timeout_sec)
        self.login_options = login_options or NiagaraLoginOptions()
        self._probe_ord_path = probe_ord_path

        self._cookie_jar = CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cookie_jar),
            _NoRedirectHandler(),
        )
        self._auth_lock = threading.Lock()
        self._login_ok = False
        self._login_reason = "init"
        self._login_scheme: str | None = None
        self._session_injected = False
        self._session_validated = False
        self._login_failures = 0
        self._next_login_attempt_mono = 0.0

        if session_cookie:
            self._inject_session_cookie(session_cookie)
            self._session_injected = True
            self._set_auth_state(False, "session_cookie_injected_unvalidated")

    @property
    def login_ok(self) -> bool:
        return self._login_ok

    @property
    def login_reason(self) -> str:
        return self._login_reason

    @property
    def login_scheme(self) -> str | None:
        return self._login_scheme

    def set_probe_ord_path(self, ord_path: str) -> None:
        self._probe_ord_path = ord_path

    def login(self, probe_ord_path: str | None = None) -> None:
        with self._auth_lock:
            self._login_locked(probe_ord_path or self._probe_ord_path)

    def ensure_login(self, probe_ord_path: str | None = None) -> None:
        with self._auth_lock:
            self._ensure_login_locked(probe_ord_path or self._probe_ord_path)

    def read_real(
        self,
        ord_path: str,
        ensure_auth: bool = True,
        allow_reauth: bool = True,
    ) -> NiagaraReal:
        if ensure_auth:
            self.ensure_login(ord_path)
        if allow_reauth:
            response = self._request_with_reauth(
                method="GET",
                path="/ord",
                ord_query=ord_path,
                data=None,
                headers=None,
                probe_ord_path=ord_path,
            )
        else:
            response = self._request_once(
                method="GET",
                path="/ord",
                ord_query=ord_path,
                data=None,
                headers=None,
            )
            if self._needs_login(response):
                reason = "login_failed_302" if response.status in (302, 303) else "login_html_detected"
                self._set_auth_state(False, reason)
                self._log_auth_failure(response, phase="request_auth_required_no_reauth")
        if response.status < 200 or response.status >= 300:
            raise NiagaraClientError(
                f"Niagara read failed status={response.status} path={ord_path}"
            )
        return parse_obix_real(response.body)

    def write_real(self, ord_path: str, value: float, ensure_auth: bool = True) -> WriteResult:
        if ensure_auth:
            self.ensure_login(ord_path)

        numeric_value = float(value)
        path_l = ord_path.lower()
        prefer_override_status = bool(
            path_l.endswith("/writevalue")
            or re.search(r"/in\d+$", path_l) is not None
        )
        xml_body = (
            '<real xmlns="http://obix.org/ns/schema/1.0" '
            f'val="{numeric_value}"/>'
        ).encode("utf-8")
        xml_body_status = (
            '<real xmlns="http://obix.org/ns/schema/1.0" '
            f'val="{numeric_value}" status="ok"/>'
        ).encode("utf-8")
        xml_body_status_overridden = (
            '<real xmlns="http://obix.org/ns/schema/1.0" '
            f'val="{numeric_value}" status="overridden"/>'
        ).encode("utf-8")
        xml_body_status_overr = (
            '<real xmlns="http://obix.org/ns/schema/1.0" '
            f'val="{numeric_value}" status="overr"/>'
        ).encode("utf-8")
        xml_body_no_ns = f'<real val="{numeric_value}"/>'.encode("utf-8")
        xml_body_no_ns_status = (
            f'<real val="{numeric_value}" status="ok"/>'
        ).encode("utf-8")
        xml_body_no_ns_status_overridden = (
            f'<real val="{numeric_value}" status="overridden"/>'
        ).encode("utf-8")
        xml_body_no_ns_status_overr = (
            f'<real val="{numeric_value}" status="overr"/>'
        ).encode("utf-8")
        obj_arg_body = (
            '<obj xmlns="http://obix.org/ns/schema/1.0">'
            f'<real name="arg" val="{numeric_value}"/>'
            "</obj>"
        ).encode("utf-8")
        obj_in_body = (
            '<obj xmlns="http://obix.org/ns/schema/1.0">'
            f'<real name="in" val="{numeric_value}"/>'
            "</obj>"
        ).encode("utf-8")
        obj_value_body = (
            '<obj xmlns="http://obix.org/ns/schema/1.0">'
            f'<real name="value" val="{numeric_value}"/>'
            "</obj>"
        ).encode("utf-8")
        obj_status_value_body = (
            '<obj xmlns="http://obix.org/ns/schema/1.0">'
            f'<real name="value" val="{numeric_value}" status="ok"/>'
            "</obj>"
        ).encode("utf-8")
        obj_overridden_value_body = (
            '<obj xmlns="http://obix.org/ns/schema/1.0">'
            f'<real name="value" val="{numeric_value}" status="overridden"/>'
            "</obj>"
        ).encode("utf-8")
        obj_overr_value_body = (
            '<obj xmlns="http://obix.org/ns/schema/1.0">'
            f'<real name="value" val="{numeric_value}" status="overr"/>'
            "</obj>"
        ).encode("utf-8")
        text_body = str(numeric_value).encode("utf-8")
        text_body_overr = f"{numeric_value} {{overr}}".encode("utf-8")
        text_body_overridden = f"{numeric_value} {{overridden}}".encode("utf-8")
        form_body = urllib.parse.urlencode({"value": str(numeric_value)}).encode("utf-8")
        form_arg_body = urllib.parse.urlencode({"arg": str(numeric_value)}).encode("utf-8")
        form_in_body = urllib.parse.urlencode({"in": str(numeric_value)}).encode("utf-8")
        form_val_body = urllib.parse.urlencode({"val": str(numeric_value)}).encode("utf-8")
        form_value_overr_body = urllib.parse.urlencode(
            {"value": f"{numeric_value} {{overr}}"}
        ).encode("utf-8")
        form_action_arg_body = urllib.parse.urlencode(
            {"actionArg": str(numeric_value)}
        ).encode("utf-8")
        form_action_arg_overr_body = urllib.parse.urlencode(
            {"actionArg": f"{numeric_value} {{overr}}"}
        ).encode("utf-8")

        attempts: list[tuple[str, Mapping[str, str], bytes, str]] = [
            (
                "PUT",
                {
                    "Content-Type": "application/xml; charset=utf-8",
                    "Accept": "application/xml,text/xml,*/*",
                },
                xml_body,
                "put_xml_application",
            ),
            (
                "POST",
                {
                    "Content-Type": "application/xml; charset=utf-8",
                    "Accept": "application/xml,text/xml,*/*",
                },
                xml_body,
                "post_xml_application",
            ),
            (
                "POST",
                {
                    "Content-Type": "application/xml; charset=utf-8",
                    "Accept": "application/xml,text/xml,*/*",
                },
                xml_body_status,
                "post_xml_application_status",
            ),
            (
                "POST",
                {
                    "Content-Type": "text/xml; charset=utf-8",
                    "Accept": "application/xml,text/xml,*/*",
                },
                xml_body,
                "post_xml_text",
            ),
            (
                "POST",
                {
                    "Content-Type": "text/xml; charset=utf-8",
                    "Accept": "application/xml,text/xml,*/*",
                },
                xml_body_status,
                "post_xml_text_status",
            ),
            (
                "POST",
                {
                    "Content-Type": "application/xml; charset=utf-8",
                    "Accept": "application/xml,text/xml,*/*",
                },
                xml_body_no_ns,
                "post_xml_application_no_ns",
            ),
            (
                "POST",
                {
                    "Content-Type": "application/xml; charset=utf-8",
                    "Accept": "application/xml,text/xml,*/*",
                },
                xml_body_no_ns_status,
                "post_xml_application_no_ns_status",
            ),
            (
                "POST",
                {
                    "Content-Type": "text/xml; charset=utf-8",
                    "Accept": "application/xml,text/xml,*/*",
                },
                obj_arg_body,
                "post_obj_arg_text_xml",
            ),
            (
                "POST",
                {
                    "Content-Type": "text/xml; charset=utf-8",
                    "Accept": "application/xml,text/xml,*/*",
                },
                obj_in_body,
                "post_obj_in_text_xml",
            ),
            (
                "POST",
                {
                    "Content-Type": "text/xml; charset=utf-8",
                    "Accept": "application/xml,text/xml,*/*",
                },
                obj_value_body,
                "post_obj_value_text_xml",
            ),
            (
                "POST",
                {
                    "Content-Type": "text/xml; charset=utf-8",
                    "Accept": "application/xml,text/xml,*/*",
                },
                obj_status_value_body,
                "post_obj_status_value_text_xml",
            ),
            (
                "POST",
                {
                    "Content-Type": "text/plain; charset=utf-8",
                    "Accept": "*/*",
                },
                text_body,
                "post_text_plain",
            ),
            (
                "POST",
                {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "*/*",
                },
                form_body,
                "post_form_value",
            ),
            (
                "POST",
                {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "*/*",
                },
                form_arg_body,
                "post_form_arg",
            ),
            (
                "POST",
                {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "*/*",
                },
                form_in_body,
                "post_form_in",
            ),
            (
                "POST",
                {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "*/*",
                },
                form_val_body,
                "post_form_val",
            ),
        ]

        if prefer_override_status:
            override_attempts: list[tuple[str, Mapping[str, str], bytes, str]] = [
                (
                    "PUT",
                    {
                        "Content-Type": "application/xml; charset=utf-8",
                        "Accept": "application/xml,text/xml,*/*",
                    },
                    xml_body_status_overridden,
                    "put_xml_status_overridden",
                ),
                (
                    "PUT",
                    {
                        "Content-Type": "application/xml; charset=utf-8",
                        "Accept": "application/xml,text/xml,*/*",
                    },
                    xml_body_status_overr,
                    "put_xml_status_overr",
                ),
                (
                    "PUT",
                    {
                        "Content-Type": "text/xml; charset=utf-8",
                        "Accept": "application/xml,text/xml,*/*",
                    },
                    obj_overridden_value_body,
                    "put_obj_value_status_overridden",
                ),
                (
                    "PUT",
                    {
                        "Content-Type": "text/xml; charset=utf-8",
                        "Accept": "application/xml,text/xml,*/*",
                    },
                    obj_overr_value_body,
                    "put_obj_value_status_overr",
                ),
                (
                    "POST",
                    {
                        "Content-Type": "application/xml; charset=utf-8",
                        "Accept": "application/xml,text/xml,*/*",
                    },
                    xml_body_status_overridden,
                    "post_xml_status_overridden",
                ),
                (
                    "POST",
                    {
                        "Content-Type": "application/xml; charset=utf-8",
                        "Accept": "application/xml,text/xml,*/*",
                    },
                    xml_body_no_ns_status_overridden,
                    "post_xml_no_ns_status_overridden",
                ),
                (
                    "POST",
                    {
                        "Content-Type": "application/xml; charset=utf-8",
                        "Accept": "application/xml,text/xml,*/*",
                    },
                    xml_body_no_ns_status_overr,
                    "post_xml_no_ns_status_overr",
                ),
                (
                    "POST",
                    {
                        "Content-Type": "text/xml; charset=utf-8",
                        "Accept": "application/xml,text/xml,*/*",
                    },
                    obj_overridden_value_body,
                    "post_obj_value_status_overridden",
                ),
                (
                    "POST",
                    {
                        "Content-Type": "text/xml; charset=utf-8",
                        "Accept": "application/xml,text/xml,*/*",
                    },
                    obj_overr_value_body,
                    "post_obj_value_status_overr",
                ),
                (
                    "POST",
                    {
                        "Content-Type": "text/plain; charset=utf-8",
                        "Accept": "*/*",
                    },
                    text_body_overr,
                    "post_text_overr",
                ),
                (
                    "POST",
                    {
                        "Content-Type": "text/plain; charset=utf-8",
                        "Accept": "*/*",
                    },
                    text_body_overridden,
                    "post_text_overridden",
                ),
                (
                    "POST",
                    {
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "*/*",
                    },
                    form_action_arg_body,
                    "post_form_action_arg",
                ),
                (
                    "POST",
                    {
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "*/*",
                    },
                    form_action_arg_overr_body,
                    "post_form_action_arg_overr",
                ),
                (
                    "POST",
                    {
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "*/*",
                    },
                    form_value_overr_body,
                    "post_form_value_overr",
                ),
            ]
            attempts = override_attempts + attempts

        failure_bits: list[str] = []
        last_response: _Response | None = None
        for method, headers, body, label in attempts:
            response = self._request_with_reauth(
                method=method,
                path="/ord",
                ord_query=ord_path,
                data=body,
                headers=headers,
                probe_ord_path=self._probe_ord_path or ord_path,
            )
            last_response = response
            logger.info(
                "niagara_write_attempt path=%s mode=%s method=%s status=%s",
                ord_path,
                label,
                method,
                response.status,
            )
            if 200 <= response.status < 300:
                return WriteResult(
                    ok=True,
                    status=response.status,
                    error=None,
                    response_body=response.body,
                )
            failure_bits.append(f"{label}:{response.status}")
            if response.status in (405, 501) or self._should_retry_post(response):
                continue
            # For all other non-2xx responses, keep trying the remaining compatibility fallbacks.

        if last_response is None:  # pragma: no cover - defensive
            return WriteResult(
                ok=False,
                status=None,
                error="Niagara write failed: no response",
                response_body=None,
            )

        body_snippet = self._sanitize_body_snippet(last_response.body)
        detail = f"responses={','.join(failure_bits)}"
        if body_snippet:
            detail = f"{detail} body={body_snippet!r}"
        return WriteResult(
            ok=False,
            status=last_response.status,
            error=f"Niagara write failed ({detail})",
            response_body=last_response.body,
        )

    def _ensure_login_locked(self, probe_ord_path: str | None) -> None:
        if self._login_ok:
            return
        if self._session_injected and not self._session_validated:
            self._session_validated = True
            if self._probe_authenticated(probe_ord_path):
                self._reset_login_backoff()
                return
            self._set_auth_state(False, "cookie_invalid")

        self._assert_credentials()
        self._login_locked(probe_ord_path)

    def _login_locked(self, probe_ord_path: str | None) -> None:
        self._assert_credentials()
        self._enforce_login_backoff()

        try:
            login_page = self._fetch_login_page()
            auth_mode = self._select_auth_mode(login_page.scheme)
            if auth_mode == _LOGIN_SCHEME_COOKIE_DIGEST:
                self._login_cookie_digest(login_page.hidden_fields, probe_ord_path)
            else:
                self._login_scram()
                if not self._probe_authenticated(probe_ord_path):
                    raise NiagaraAuthError("SCRAM login completed but probe failed")
        except NiagaraAuthError:
            self._record_login_failure()
            raise
        except Exception as exc:
            self._record_login_failure()
            raise NiagaraAuthError(f"Niagara auto login failed: {exc}") from exc

        self._session_validated = True
        self._reset_login_backoff()

    def _fetch_login_page(self) -> _LoginPage:
        response = self._request_once(
            method="GET",
            path=self.login_options.endpoint_path,
            ord_query=None,
            data=None,
            headers={"Accept": "text/html,*/*"},
        )
        if response.status >= 400:
            self._log_auth_failure(response, phase="login_page_fetch")
            raise NiagaraAuthError(f"Login page request failed status={response.status}")

        hidden_fields = _extract_hidden_inputs(response.body)
        scheme = detect_login_scheme(response.body) or hidden_fields.get("scheme")
        if scheme:
            self._login_scheme = scheme
        else:
            self._login_scheme = self._login_scheme or "unknown"

        return _LoginPage(
            response=response,
            hidden_fields=hidden_fields,
            scheme=scheme,
        )

    def _select_auth_mode(self, detected_scheme: str | None) -> str:
        configured_mode = self.login_options.auth_mode.strip().lower()
        if configured_mode == "scram":
            return "scram"
        if configured_mode in {_LOGIN_SCHEME_COOKIE_DIGEST, "cookiedigest"}:
            return _LOGIN_SCHEME_COOKIE_DIGEST
        if configured_mode != "auto":
            logger.warning(
                "Unknown Niagara auth_mode '%s', defaulting to auto",
                self.login_options.auth_mode,
            )

        normalized_scheme = _normalize_login_scheme(detected_scheme)
        if normalized_scheme == _LOGIN_SCHEME_COOKIE_DIGEST:
            return _LOGIN_SCHEME_COOKIE_DIGEST
        return "scram"

    def _login_cookie_digest(
        self,
        hidden_fields: Mapping[str, str],
        probe_ord_path: str | None,
    ) -> None:
        support_error: NiagaraAuthError | None = None
        try:
            self._login_cookie_digest_support_flow()
            if self._probe_authenticated(probe_ord_path):
                return
            support_error = NiagaraAuthError(
                "cookieDigest support flow completed but probe failed"
            )
        except NiagaraAuthError as exc:
            support_error = exc
            logger.warning("Niagara cookieDigest support flow failed: %s", exc)

        modes = self._resolve_content_modes(self.login_options.content_mode)
        last_error: NiagaraAuthError | None = None

        for mode in modes:
            payload = self._build_cookie_digest_payload(hidden_fields)
            response = self._post_login_payload(payload, mode)
            if response.status >= 400:
                self._log_auth_failure(response, phase=f"cookie_digest_post_{mode}")
                last_error = NiagaraAuthError(
                    f"cookieDigest login POST failed status={response.status}"
                )
                continue
            if self._probe_authenticated(probe_ord_path):
                return
            self._log_auth_failure(response, phase=f"cookie_digest_probe_failed_{mode}")
            last_error = NiagaraAuthError("cookieDigest login did not produce an authenticated session")

        if last_error:
            raise last_error
        if support_error:
            raise support_error
        raise NiagaraAuthError("cookieDigest login failed")

    def _login_cookie_digest_support_flow(self) -> None:
        assert self.username is not None
        assert self.password is not None

        client_nonce, _, first_message = scram_client_first(self.username)
        first_response = self._post_login_support_payload(
            action=self.login_options.support_action_first,
            message_field=self.login_options.client_first_field,
            message_value=first_message,
        )
        if first_response.status >= 400:
            self._log_auth_failure(first_response, phase="cookie_digest_support_first")
            raise NiagaraAuthError(
                f"cookieDigest support first step failed status={first_response.status}"
            )
        if self._looks_like_login_html(first_response.body):
            self._log_auth_failure(first_response, phase="cookie_digest_support_first_html")
            raise NiagaraAuthError("cookieDigest support first step returned login HTML")

        server_first = self._extract_field(
            first_response.body,
            preferred_key=self.login_options.server_first_field,
            fallback_scram=True,
        )
        if not server_first:
            raise NiagaraAuthError("cookieDigest support first step missing server challenge")

        hash_candidates = self._hash_candidates(server_first, first_response.body)
        last_error: NiagaraAuthError | None = None
        for hash_name in hash_candidates:
            try:
                final_message, expected_signature = scram_client_final(
                    password=self.password,
                    username=self.username,
                    client_nonce=client_nonce,
                    server_first_message=server_first,
                    hash_name=hash_name,
                )
                final_response = self._post_login_support_payload(
                    action=self.login_options.support_action_final,
                    message_field=self.login_options.client_final_field,
                    message_value=final_message,
                )
                if final_response.status >= 400:
                    self._log_auth_failure(
                        final_response,
                        phase=f"cookie_digest_support_final_{hash_name}",
                    )
                    raise NiagaraAuthError(
                        f"cookieDigest support final step failed status={final_response.status}"
                    )
                if self._looks_like_login_html(final_response.body):
                    self._log_auth_failure(
                        final_response,
                        phase=f"cookie_digest_support_final_html_{hash_name}",
                    )
                    raise NiagaraAuthError("cookieDigest support final step returned login HTML")

                server_final = self._extract_field(
                    final_response.body,
                    preferred_key=self.login_options.server_final_field,
                    fallback_scram=True,
                )
                if server_final:
                    attrs = _parse_scram_message(server_final)
                    if "e" in attrs:
                        raise NiagaraAuthError(
                            f"cookieDigest support server error: {attrs['e']}"
                        )
                    actual_sig = attrs.get("v")
                    if actual_sig and actual_sig != expected_signature:
                        raise NiagaraAuthError("cookieDigest support server signature mismatch")

                if not self._has_niagara_session_cookie():
                    logger.warning(
                        "cookieDigest support flow completed without niagara_session cookie; continuing to probe"
                    )
                return
            except NiagaraAuthError as exc:
                last_error = exc

        if last_error:
            raise last_error
        raise NiagaraAuthError("cookieDigest support flow failed without a usable SCRAM hash")

    def _build_cookie_digest_payload(self, hidden_fields: Mapping[str, str]) -> dict[str, str]:
        assert self.username is not None
        assert self.password is not None

        token_key = self.login_options.token_field
        cookie_postfix_key = self.login_options.cookie_postfix_field
        payload: dict[str, str] = {
            self.login_options.username_field: self.username,
            self.login_options.password_field: self.password,
            self.login_options.scheme_field: "cookieDigest",
            token_key: hidden_fields.get(token_key, hidden_fields.get(token_key.lower(), "")),
        }

        cookie_postfix = hidden_fields.get(
            cookie_postfix_key,
            hidden_fields.get(cookie_postfix_key.lower()),
        )
        if cookie_postfix is not None:
            payload[cookie_postfix_key] = cookie_postfix
        return payload

    def _login_scram(self) -> None:
        modes = self._resolve_content_modes(self.login_options.content_mode)
        last_error: Exception | None = None
        for mode in modes:
            try:
                self._login_scram_mode(mode)
                return
            except Exception as exc:  # noqa: BLE001 - mode fallback
                last_error = exc
                logger.warning("Niagara SCRAM login attempt failed (mode=%s): %s", mode, exc)

        if last_error:
            raise NiagaraAuthError(f"Niagara SCRAM login failed: {last_error}") from last_error
        raise NiagaraAuthError("Niagara SCRAM login failed")

    def _login_scram_mode(self, mode: str) -> None:
        assert self.username is not None
        assert self.password is not None

        client_nonce, _, first_message = scram_client_first(self.username)
        first_payload = {self.login_options.client_first_field: first_message}
        first_response = self._post_login_payload(first_payload, mode)
        if first_response.status >= 400:
            self._log_auth_failure(first_response, phase=f"scram_first_{mode}")
            raise NiagaraAuthError(
                f"SCRAM first step failed status={first_response.status}"
            )

        server_first = self._extract_field(
            first_response.body,
            preferred_key=self.login_options.server_first_field,
            fallback_scram=True,
        )
        if not server_first:
            raise NiagaraAuthError("SCRAM first step did not return serverFirstMessage")

        hash_candidates = self._hash_candidates(server_first, first_response.body)
        last_error: Exception | None = None
        for hash_name in hash_candidates:
            try:
                final_message, expected_signature = scram_client_final(
                    password=self.password,
                    username=self.username,
                    client_nonce=client_nonce,
                    server_first_message=server_first,
                    hash_name=hash_name,
                )
                final_payload = {self.login_options.client_final_field: final_message}
                final_response = self._post_login_payload(final_payload, mode)
                if final_response.status >= 400:
                    self._log_auth_failure(final_response, phase=f"scram_final_{mode}_{hash_name}")
                    raise NiagaraAuthError(
                        f"SCRAM final step failed status={final_response.status}"
                    )

                server_final = self._extract_field(
                    final_response.body,
                    preferred_key=self.login_options.server_final_field,
                    fallback_scram=True,
                )
                if server_final:
                    attrs = _parse_scram_message(server_final)
                    if "e" in attrs:
                        raise NiagaraAuthError(
                            f"SCRAM server reported error: {attrs['e']}"
                        )
                    actual_sig = attrs.get("v")
                    if actual_sig and actual_sig != expected_signature:
                        raise NiagaraAuthError("SCRAM server signature mismatch")

                if not self._has_niagara_session_cookie():
                    raise NiagaraAuthError(
                        "SCRAM login completed but niagara_session cookie was not set"
                    )
                return
            except Exception as exc:  # noqa: BLE001 - hash fallback
                last_error = exc
                logger.warning(
                    "SCRAM final step failed (mode=%s hash=%s): %s",
                    mode,
                    hash_name,
                    exc,
                )

        if last_error:
            raise NiagaraAuthError(str(last_error)) from last_error
        raise NiagaraAuthError("SCRAM login failed with all configured hash algorithms")

    def _request_with_reauth(
        self,
        method: str,
        path: str,
        ord_query: str | None,
        data: bytes | None,
        headers: Mapping[str, str] | None,
        probe_ord_path: str | None = None,
        retried: bool = False,
    ) -> _Response:
        response = self._request_once(method, path, ord_query, data, headers)
        logger.info("niagara_http method=%s path=%s status=%s", method, path, response.status)

        if self._needs_login(response):
            reason = "login_failed_302" if response.status in (302, 303) else "login_html_detected"
            self._set_auth_state(False, reason)
            self._log_auth_failure(response, phase="request_auth_required")
            if retried:
                return response
            self.login(probe_ord_path=probe_ord_path)
            return self._request_with_reauth(
                method=method,
                path=path,
                ord_query=ord_query,
                data=data,
                headers=headers,
                probe_ord_path=probe_ord_path,
                retried=True,
            )

        if self._is_authenticated_obix_response(response):
            self._set_auth_state(True, "probe_ok")
        return response

    def _request_once(
        self,
        method: str,
        path: str,
        ord_query: str | None,
        data: bytes | None,
        headers: Mapping[str, str] | None,
    ) -> _Response:
        url = self._build_url(path, ord_query)
        req = urllib.request.Request(url=url, data=data, method=method)
        for key, val in (headers or {}).items():
            req.add_header(key, val)
        try:
            with self._opener.open(req, timeout=self.timeout_sec) as resp:
                status = getattr(resp, "status", resp.getcode())
                body = resp.read().decode("utf-8", errors="replace")
                resp_headers = dict(resp.headers.items())
                return _Response(status=status, body=body, headers=resp_headers)
        except HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:  # pragma: no cover - best effort
                body = ""
            resp_headers = dict(exc.headers.items()) if exc.headers else {}
            return _Response(status=exc.code, body=body, headers=resp_headers)
        except URLError as exc:
            self._set_auth_state(False, "network_error")
            raise NiagaraClientError(f"Niagara request failed: {exc}") from exc

    def _build_url(self, path: str, ord_query: str | None) -> str:
        path = path if path.startswith("/") else f"/{path}"
        base = f"{self.scheme}://{self.host}{path}"
        if ord_query is None:
            return base
        encoded_query = urllib.parse.quote(ord_query, safe=":/|$@[]!()*+,;=-._~")
        return f"{base}?{encoded_query}"

    def _probe_authenticated(self, ord_path: str | None) -> bool:
        response = self._request_once(
            method="GET",
            path="/ord",
            ord_query=ord_path or self._probe_ord_path,
            data=None,
            headers={"Accept": "application/xml,text/xml,*/*"},
        )
        if self._needs_login(response):
            reason = "login_failed_302" if response.status in (302, 303) else "login_html_detected"
            self._set_auth_state(False, reason)
            self._log_auth_failure(response, phase="probe_auth_required")
            return False
        if response.status != 200:
            self._set_auth_state(False, f"probe_status_{response.status}")
            self._log_auth_failure(response, phase="probe_non_200")
            return False
        if not self._is_obix_body(response.body):
            self._set_auth_state(False, "probe_non_obix")
            self._log_auth_failure(response, phase="probe_non_obix")
            return False
        self._set_auth_state(True, "probe_ok")
        return True

    def _post_login_payload(self, payload: Mapping[str, str], mode: str) -> _Response:
        headers: dict[str, str]
        data: bytes
        if mode == "json":
            headers = {"Content-Type": "application/json", "Accept": "application/json,*/*"}
            data = json.dumps(payload).encode("utf-8")
        else:
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json,text/plain,*/*",
            }
            data = urllib.parse.urlencode(payload).encode("utf-8")

        response = self._request_once(
            method="POST",
            path=self.login_options.endpoint_path,
            ord_query=None,
            data=data,
            headers=headers,
        )
        logger.info("niagara_login_http mode=%s status=%s", mode, response.status)
        return response

    def _login_support_path(self) -> str:
        endpoint = self.login_options.endpoint_path.strip() or "/login"
        if not endpoint.startswith("/"):
            endpoint = f"/{endpoint}"
        if not endpoint.endswith("/"):
            endpoint = f"{endpoint}/"
        return endpoint

    def _post_login_support_payload(
        self,
        *,
        action: str,
        message_field: str,
        message_value: str,
    ) -> _Response:
        payload = (
            f"{self.login_options.support_action_field}={action}"
            f"&{message_field}={message_value}"
        ).encode("utf-8")
        response = self._request_once(
            method="POST",
            path=self._login_support_path(),
            ord_query=None,
            data=payload,
            headers={
                "Content-Type": self.login_options.support_content_type,
                "Accept": "*/*",
            },
        )
        logger.info(
            "niagara_login_support action=%s status=%s",
            action,
            response.status,
        )
        return response

    def _needs_login(self, response: _Response) -> bool:
        if response.status in (302, 303):
            return True
        return self._looks_like_login_html(response.body)

    def _looks_like_login_html(self, body: str) -> bool:
        body_l = body.lower()
        # Niagara AX login pages often expose hidden scheme input, including single-quoted attrs.
        if detect_login_scheme(body):
            return True
        return any(marker in body_l for marker in _LOGIN_MARKERS)

    def _is_obix_body(self, body: str) -> bool:
        body_l = body.lower()
        return any(marker in body_l for marker in _OBIX_MARKERS)

    def _is_authenticated_obix_response(self, response: _Response) -> bool:
        return response.status == 200 and not self._needs_login(response) and self._is_obix_body(response.body)

    def _resolve_content_modes(self, mode: str) -> tuple[str, ...]:
        normalized = mode.strip().lower()
        if normalized == "auto":
            return ("form", "json")
        if normalized in ("form", "json"):
            return (normalized,)
        logger.warning("Unknown Niagara login content mode '%s', defaulting to auto", mode)
        return ("form", "json")

    def _extract_field(self, body: str, preferred_key: str, fallback_scram: bool) -> str | None:
        data = body.strip()
        if not data:
            return None

        try:
            parsed = json.loads(data)
            if isinstance(parsed, dict):
                value = parsed.get(preferred_key)
                if isinstance(value, str):
                    return value
                for candidate in parsed.values():
                    if isinstance(candidate, str) and "r=" in candidate and "," in candidate:
                        return candidate
        except json.JSONDecodeError:
            pass

        form = urllib.parse.parse_qs(data, keep_blank_values=True)
        if preferred_key in form and form[preferred_key]:
            return form[preferred_key][0]
        if fallback_scram:
            for values in form.values():
                if values and "r=" in values[0] and "," in values[0]:
                    return values[0]

        if fallback_scram:
            for line in data.replace("\r", "\n").split("\n"):
                line = line.strip()
                if line.startswith(("r=", "v=", "e=")):
                    return line
        return None

    def _hash_candidates(self, server_first: str, body: str) -> list[str]:
        configured = [h for h in self.login_options.hash_algorithms if h]
        if not configured:
            configured = ["sha256", "sha1"]

        lower_blob = f"{server_first}\n{body}".lower()
        if "sha-1" in lower_blob or "sha1" in lower_blob:
            ordered = ["sha1"] + configured
        elif "sha-512" in lower_blob or "sha512" in lower_blob:
            ordered = ["sha512"] + configured
        else:
            ordered = configured

        deduped: list[str] = []
        for item in ordered:
            normalized = item.replace("-", "").lower()
            if normalized not in deduped and normalized in _SUPPORTED_HASHES:
                deduped.append(normalized)
        return deduped

    def _should_retry_post(self, response: _Response) -> bool:
        if response.status in (405, 501):
            return True
        if response.status == 400:
            body_l = response.body.lower()
            if any(marker in body_l for marker in _METHOD_NOT_ALLOWED_MARKERS):
                return True
        return False

    def _assert_credentials(self) -> None:
        if not self.username or not self.password:
            raise NiagaraAuthError("Niagara username/password are required for auto login")

    def _enforce_login_backoff(self) -> None:
        now = time.monotonic()
        if now >= self._next_login_attempt_mono:
            return
        wait_sec = self._next_login_attempt_mono - now
        self._set_auth_state(False, f"login_backoff_{wait_sec:.1f}s")
        raise NiagaraAuthError(f"Login backoff active ({wait_sec:.1f}s remaining)")

    def _record_login_failure(self) -> None:
        delay = min(60.0, 5.0 * (2 ** self._login_failures))
        self._login_failures += 1
        self._next_login_attempt_mono = time.monotonic() + delay
        logger.warning(
            "Niagara login failed; backoff %.1fs (attempts=%s)",
            delay,
            self._login_failures,
        )

    def _reset_login_backoff(self) -> None:
        self._login_failures = 0
        self._next_login_attempt_mono = 0.0

    def _response_header(self, response: _Response, name: str) -> str | None:
        target = name.lower()
        for key, value in response.headers.items():
            if key.lower() == target:
                return value
        return None

    def _sanitize_body_snippet(self, body: str, limit: int = 120) -> str:
        compact = " ".join(body.split())
        if "password" in compact.lower():
            return "body-redacted-password-field"
        if len(compact) <= limit:
            return compact
        return compact[:limit]

    def _log_auth_failure(self, response: _Response, phase: str) -> None:
        location = self._response_header(response, "Location")
        if self._looks_like_login_html(response.body):
            detail = "login HTML detected"
        else:
            snippet = self._sanitize_body_snippet(response.body)
            detail = f"body={snippet!r}" if snippet else "body=<empty>"
        logger.warning(
            "Niagara auth failure phase=%s scheme=%s status=%s location=%s detail=%s",
            phase,
            self._login_scheme or "unknown",
            response.status,
            location or "-",
            detail,
        )

    def _inject_session_cookie(self, value: str) -> None:
        cookie = Cookie(
            version=0,
            name="niagara_session",
            value=value,
            port=None,
            port_specified=False,
            domain=self.host.split(":", 1)[0],
            domain_specified=True,
            domain_initial_dot=False,
            path="/",
            path_specified=True,
            secure=self.scheme.lower() == "https",
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={"HttpOnly": None},
            rfc2109=False,
        )
        self._cookie_jar.set_cookie(cookie)

    def _has_niagara_session_cookie(self) -> bool:
        for cookie in self._cookie_jar:
            if cookie.name == "niagara_session":
                return True
        return False

    def _set_auth_state(self, value: bool, reason: str) -> None:
        normalized_reason = reason.strip() if reason else "unknown"
        changed = (self._login_ok != value) or (self._login_reason != normalized_reason)
        self._login_ok = value
        self._login_reason = normalized_reason
        if changed:
            logger.info(
                "Niagara login state ok=%s reason=%s scheme=%s",
                self._login_ok,
                self._login_reason,
                self._login_scheme or "unknown",
            )
