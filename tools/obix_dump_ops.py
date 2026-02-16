#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.niagara_client import NiagaraClient, NiagaraLoginOptions


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _header(headers: dict[str, str], name: str) -> str | None:
    needle = name.lower()
    for key, value in headers.items():
        if key.lower() == needle:
            return value
    return None


def _normalize_component_path(raw: str) -> str:
    value = raw.strip()
    value = value.lstrip("/")
    return value


def _probe_ord_for_path(component_path: str) -> str:
    normalized = component_path.rstrip("/")
    if not normalized.lower().endswith("/out"):
        normalized = f"{normalized}/out"
    return f"station:|slot:/{normalized}"


def _is_login_or_html_body(client: NiagaraClient, body: str) -> bool:
    if client._looks_like_login_html(body):
        return True
    body_l = body.lower()
    return "<html" in body_l or "<!doctype html" in body_l


def _looks_writable_real(attrs: dict[str, str]) -> bool:
    text = " ".join(
        [
            attrs.get("name", ""),
            attrs.get("href", ""),
            attrs.get("display", ""),
        ]
    ).lower()
    if not text:
        return False
    if re.search(r"\bin\d+\b", text):
        return True
    keywords = ("set", "override", "write", "priority")
    return any(key in text for key in keywords)


def _format_element_line(tag: str, attrs: dict[str, str], keys: tuple[str, ...]) -> str:
    parts: list[str] = []
    for key in keys:
        if key in attrs and attrs[key] != "":
            parts.append(f'{key}="{attrs[key]}"')
    joined = " ".join(parts)
    if joined:
        return f"<{tag} {joined}/>"
    return f"<{tag}/>"


def _fetch_config_once(client: NiagaraClient, config_path: str) -> Any:
    return client._request_once(
        method="GET",
        path=config_path,
        ord_query=None,
        data=None,
        headers={"Accept": "application/xml,text/xml,*/*"},
    )


def _fetch_ord_once(client: NiagaraClient, ord_query: str) -> Any:
    return client._request_once(
        method="GET",
        path="/ord",
        ord_query=ord_query,
        data=None,
        headers={"Accept": "application/xml,text/xml,*/*"},
    )


def _is_xml_body(content_type: str, body: str) -> bool:
    if "xml" in content_type.lower():
        return True
    trimmed = body.lstrip()
    if trimmed.startswith("<") and not trimmed.lower().startswith("<html"):
        return True
    return False


def _extract_first_real_href(body: str) -> str | None:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None

    for elem in root.iter():
        if _local_name(elem.tag) != "real":
            continue
        href = elem.attrib.get("href")
        if isinstance(href, str) and href.strip():
            return href.strip()
    return None


def _href_to_path(href: str) -> str | None:
    value = href.strip()
    if not value:
        return None
    lower = value.lower()
    if lower.startswith("station:|slot:") or lower.startswith("slot:/"):
        return None
    parsed = urlparse(value)
    if parsed.scheme and parsed.scheme not in ("http", "https"):
        return None
    if parsed.scheme and parsed.netloc:
        if not parsed.path:
            return None
        return parsed.path
    if value.startswith("/"):
        return value
    return f"/{value}"


def _parent_path(path: str) -> str:
    normalized = path.rstrip("/")
    if "/" not in normalized:
        return normalized
    return normalized.rsplit("/", 1)[0]


def _build_config_path_candidates(component_path: str, ord_real_href: str | None) -> list[str]:
    candidates: list[str] = [
        f"/obix/config/{component_path}",
        f"/obix/config/{component_path}/",
        f"/obix/config/slot:/{component_path}",
        f"/obix/config/slot:/{component_path}/",
        f"/obix/config/station:|slot:/{component_path}",
        f"/obix/config/station:|slot:/{component_path}/",
    ]
    if ord_real_href:
        href_path = _href_to_path(ord_real_href)
        if href_path:
            candidates.append(href_path)
            candidates.append(f"{href_path.rstrip('/')}/")
            if href_path.rstrip("/").lower().endswith("/out"):
                parent = _parent_path(href_path)
                candidates.append(parent)
                candidates.append(f"{parent.rstrip('/')}/")

    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _build_ord_component_candidates(component_path: str) -> list[str]:
    base = f"station:|slot:/{component_path.rstrip('/')}"
    bases = [
        base,
        f"{base}/proxyExt",
        f"{base}/writeValue",
        f"{base}/in8",
        f"{base}/in10",
        f"{base}/in16",
        f"{base}/set",
        f"{base}/override",
        f"{base}/emergencyOverride",
    ]

    candidates: list[str] = []
    for item in bases:
        candidates.append(item)
        candidates.append(f"{item}|view:obix")
        candidates.append(f"{item}|view:config")
        candidates.append(f"{item}|view:slot")
        candidates.append(f"{item}|view:control")
        if not item.lower().endswith("/out"):
            out_item = f"{item}/out"
            candidates.append(out_item)
            candidates.append(f"{out_item}|view:obix")
            candidates.append(f"{out_item}|view:config")
            candidates.append(f"{out_item}|view:slot")
            candidates.append(f"{out_item}|view:control")

    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _extract_ops_and_reals(
    xml_body: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[tuple[str, dict[str, str]]]]:
    root = ET.fromstring(xml_body)
    ops: list[dict[str, str]] = []
    real_candidates: list[dict[str, str]] = []
    href_candidates: list[tuple[str, dict[str, str]]] = []
    for elem in root.iter():
        local = _local_name(elem.tag)
        attrs = {str(k): str(v) for k, v in elem.attrib.items()}
        if local == "op":
            ops.append(attrs)
        elif local == "real" and _looks_writable_real(attrs):
            real_candidates.append(attrs)
        elif "href" in attrs:
            text = " ".join(
                [
                    local,
                    attrs.get("name", ""),
                    attrs.get("href", ""),
                    attrs.get("display", ""),
                ]
            ).lower()
            if any(k in text for k in ("set", "override", "write", "priority", "in8", "in10", "in16")):
                href_candidates.append((local, attrs))
    return ops, real_candidates, href_candidates


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dump oBIX config operations for a Niagara component path."
    )
    parser.add_argument("--host", required=True, help="Niagara host, e.g. 192.168.15.12")
    parser.add_argument(
        "--scheme",
        default="http",
        choices=("http", "https"),
        help="Niagara scheme (default: http)",
    )
    parser.add_argument(
        "--user-env",
        default="NIAGARA_USER",
        help="Environment variable containing Niagara username (default: NIAGARA_USER)",
    )
    parser.add_argument(
        "--pass-env",
        default="NIAGARA_PASS",
        help="Environment variable containing Niagara password (default: NIAGARA_PASS)",
    )
    parser.add_argument(
        "--path",
        required=True,
        help='Component path under station, e.g. "Drivers/.../points/OccupiedCoolingSetpoint"',
    )
    parser.add_argument(
        "--probe-ord",
        default=None,
        help=(
            "Optional explicit ORD used only for login validation. "
            "Default derives from --path with '/out' appended."
        ),
    )
    parser.add_argument(
        "--login-path",
        default="/login",
        help="Niagara login endpoint path (default: /login)",
    )
    parser.add_argument(
        "--timeout-sec",
        type=float,
        default=10.0,
        help="HTTP timeout in seconds (default: 10.0)",
    )
    args = parser.parse_args()

    username = os.environ.get(args.user_env)
    password = os.environ.get(args.pass_env)
    if not username:
        print(f"missing username env var: {args.user_env}", file=sys.stderr)
        return 2
    if not password:
        print(f"missing password env var: {args.pass_env}", file=sys.stderr)
        return 2

    component_path = _normalize_component_path(args.path)
    config_path = f"/obix/config/{component_path}"
    probe_ord = args.probe_ord.strip() if args.probe_ord else _probe_ord_for_path(component_path)

    login_options = NiagaraLoginOptions(
        endpoint_path=args.login_path,
        auth_mode="cookieDigest",
    )
    client = NiagaraClient(
        host=args.host,
        scheme=args.scheme,
        username=username,
        password=password,
        timeout_sec=args.timeout_sec,
        login_options=login_options,
    )

    try:
        client.ensure_login(probe_ord_path=probe_ord)
    except Exception as exc:  # noqa: BLE001 - explicit debug tool behavior
        print(f"login failed: {exc}", file=sys.stderr)
        return 1

    ord_response = _fetch_ord_once(client, probe_ord)
    ord_content_type = _header(ord_response.headers, "Content-Type") or ""
    ord_body = ord_response.body or ""
    ord_real_href: str | None = None
    if (
        ord_response.status == 200
        and _is_xml_body(ord_content_type, ord_body)
        and not _is_login_or_html_body(client, ord_body)
    ):
        ord_real_href = _extract_first_real_href(ord_body)

    config_paths = _build_config_path_candidates(component_path, ord_real_href)
    response = None
    chosen_config_path = config_path
    attempts: list[tuple[str, int, str]] = []

    for candidate in config_paths:
        trial = _fetch_config_once(client, candidate)
        needs_retry = trial.status in (302, 303) or _is_login_or_html_body(client, trial.body)
        if needs_retry:
            try:
                client.login(probe_ord_path=probe_ord)
            except Exception:
                pass
            trial = _fetch_config_once(client, candidate)

        content_type = _header(trial.headers, "Content-Type") or "-"
        attempts.append((candidate, trial.status, content_type))
        body = trial.body or ""
        if (
            trial.status == 200
            and _is_xml_body(content_type, body)
            and not _is_login_or_html_body(client, body)
        ):
            response = trial
            chosen_config_path = candidate
            break

    ord_view_source: str | None = None
    ord_view_body: str | None = None
    ord_attempts: list[tuple[str, int, str]] = []
    best_real_only_source: str | None = None
    best_real_only_body: str | None = None
    if response is None:
        for ord_candidate in _build_ord_component_candidates(component_path):
            trial = _fetch_ord_once(client, ord_candidate)
            needs_retry = trial.status in (302, 303) or _is_login_or_html_body(client, trial.body)
            if needs_retry:
                try:
                    client.login(probe_ord_path=probe_ord)
                except Exception:
                    pass
                trial = _fetch_ord_once(client, ord_candidate)

            content_type = _header(trial.headers, "Content-Type") or "-"
            ord_attempts.append((ord_candidate, trial.status, content_type))
            body = trial.body or ""
            if (
                trial.status == 200
                and _is_xml_body(content_type, body)
                and not _is_login_or_html_body(client, body)
            ):
                try:
                    ops_probe, reals_probe, _ = _extract_ops_and_reals(body)
                except ET.ParseError:
                    continue
                if ops_probe:
                    ord_view_source = ord_candidate
                    ord_view_body = body
                    break
                if reals_probe and best_real_only_body is None:
                    best_real_only_source = ord_candidate
                    best_real_only_body = body

    if response is None and ord_view_body is None and best_real_only_body is not None:
        ord_view_source = best_real_only_source
        ord_view_body = best_real_only_body

    if response is None and ord_view_body is None:
        last_status = attempts[-1][1] if attempts else -1
        last_ct = attempts[-1][2] if attempts else "-"
        print(
            f"obix config not accessible with this user "
            f"(status={last_status}, content_type={last_ct})"
        )
        print("# tried config paths:")
        for path, status, content_type in attempts:
            print(f"- {path} -> status={status} content_type={content_type}")
        print("# tried /ord component views:")
        for ord_q, status, content_type in ord_attempts:
            print(f"- {ord_q} -> status={status} content_type={content_type}")
        if ord_real_href:
            print(f"# ord out href hint: {ord_real_href}")
        return 1

    if ord_view_body is not None:
        print(f"# URL: {args.scheme}://{args.host}/ord?{ord_view_source}")
        print("# source=ord_view_fallback")
        if ord_real_href:
            print(f"# ord out href hint: {ord_real_href}")
        try:
            ops, real_candidates, href_candidates = _extract_ops_and_reals(ord_view_body)
        except ET.ParseError as exc:
            print(f"xml parse failed: {exc}", file=sys.stderr)
            return 1
    else:
        content_type = _header(response.headers, "Content-Type") or ""
        body = response.body or ""
        print(f"# URL: {args.scheme}://{args.host}{chosen_config_path}")
        print(f"# status={response.status} content_type={content_type or '-'}")
        if ord_real_href:
            print(f"# ord out href hint: {ord_real_href}")
        try:
            ops, real_candidates, href_candidates = _extract_ops_and_reals(body)
        except ET.ParseError as exc:
            print(f"xml parse failed: {exc}", file=sys.stderr)
            return 1

    if ops:
        print("# ops")
        for attrs in ops:
            print(_format_element_line("op", attrs, ("name", "href", "in", "out", "is")))
    else:
        print("# ops")
        print("no <op ...> elements found")

    if real_candidates:
        print("# writable-looking real slots")
        for attrs in real_candidates:
            print(
                _format_element_line(
                    "real",
                    attrs,
                    ("name", "href", "val", "status", "display", "unit", "is"),
                )
            )
    else:
        print("# writable-looking real slots")
        print("no writable-looking <real ...> elements found")

    if href_candidates:
        print("# writable-looking href elements")
        for tag, attrs in href_candidates:
            print(
                _format_element_line(
                    tag,
                    attrs,
                    ("name", "href", "display", "is"),
                )
            )
    else:
        print("# writable-looking href elements")
        print("no additional writable-looking href elements found")

    if ord_view_source is not None and not ops:
        print("# tried /ord component views:")
        for ord_q, status, content_type in ord_attempts:
            print(f"- {ord_q} -> status={status} content_type={content_type}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
