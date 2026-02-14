from __future__ import annotations

import pytest

from gateway.niagara_client import NiagaraParseError, parse_obix_real


def test_parse_obix_real_with_namespace() -> None:
    xml = '<obj xmlns="http://obix.org/ns/schema/1.0"><real val="72.5" unit="obix:units/degF" display="72.5 F"/></obj>'
    parsed = parse_obix_real(xml)
    assert parsed.value == pytest.approx(72.5)
    assert parsed.unit == "obix:units/degF"
    assert parsed.display == "72.5 F"


def test_parse_obix_real_without_namespace_and_missing_unit() -> None:
    xml = '<obj><real val="68.0" display="68.0"/></obj>'
    parsed = parse_obix_real(xml)
    assert parsed.value == pytest.approx(68.0)
    assert parsed.unit is None
    assert parsed.display == "68.0"


def test_parse_obix_real_raises_on_malformed_xml() -> None:
    with pytest.raises(NiagaraParseError):
        parse_obix_real('<obj><real val="70.0"></obj>')
