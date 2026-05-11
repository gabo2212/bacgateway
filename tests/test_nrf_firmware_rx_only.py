from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FW = ROOT / "firmware" / "nrf_vwg_bridge_rx"
SRC = FW / "src"


def _read(relative: str) -> str:
    return (FW / relative).read_text(encoding="utf-8")


def test_phase_1b_firmware_tree_exists() -> None:
    expected = {
        "CMakeLists.txt",
        "prj.conf",
        "README.md",
        "src/main.c",
        "src/bridge_proto.h",
        "src/bridge_proto.c",
        "src/radio_rx.h",
        "src/radio_rx.c",
        "boards/nrf52840dk_nrf52840.overlay",
        "boards/nrf52840dongle_nrf52840.overlay",
    }

    for relative in expected:
        assert (FW / relative).is_file(), relative


def test_firmware_protocol_constants_match_python_host() -> None:
    header = _read("src/bridge_proto.h")
    source = _read("src/bridge_proto.c")

    assert "#define BRIDGE_MAGIC_0 'V'" in header
    assert "#define BRIDGE_MAGIC_1 'W'" in header
    assert "#define BRIDGE_PROTO_VERSION 1" in header
    assert "BRIDGE_FRAME_HELLO_RESP = 0x02" in header
    assert "BRIDGE_FRAME_RX_FRAME = 0x03" in header
    assert "BRIDGE_FRAME_TX_RAW_RESERVED = 0x04" in header
    assert "sys_put_le64(rx->timestamp_us, &payload[0])" in source
    assert "payload[8] = rx->channel" in source
    assert "payload[9] = (uint8_t)rx->rssi_dbm" in source
    assert "payload[10] = (uint8_t)rx->lqi" in source
    assert "BRIDGE_RX_META_LEN + rx->psdu_len" in source


def test_firmware_source_has_no_radio_send_path() -> None:
    forbidden = (
        "nrf_802154_transmit",
        "nrf_802154_ack_data_set",
        "nrf_802154_pan_id_set",
        "nrf_802154_short_address_set",
        "nrf_802154_extended_address_set",
    )
    source_text = "\n".join(path.read_text(encoding="utf-8") for path in SRC.glob("*.c"))

    for token in forbidden:
        assert token not in source_text


def test_firmware_docs_include_live_smoke_commands() -> None:
    readme = _read("README.md")
    bringup = (ROOT / "docs" / "radio" / "nrf_firmware_bringup.md").read_text(
        encoding="utf-8"
    )

    assert "python tools\\nrf_live_rx.py --port COM7 --channel 15 --limit 50" in readme
    assert "python tools/nrf_live_rx.py --port /dev/ttyACM0 --channel 15 --limit 50" in readme
    assert "python tools\\nrf_live_rx.py --port COM7 --channel 15 --limit 50" in bringup
    assert "python tools/nrf_live_rx.py --port /dev/ttyACM0 --channel 15 --limit 50" in bringup
