#include "bridge_proto.h"

#include <errno.h>
#include <string.h>

#include <zephyr/drivers/uart.h>
#include <zephyr/sys/byteorder.h>

#define BRIDGE_MAX_PAYLOAD_LEN (BRIDGE_RX_META_LEN + BRIDGE_MAX_PSDU_LEN)

static uint32_t crc32_ieee_update(uint32_t crc, const uint8_t *data, size_t len)
{
	crc = ~crc;
	for (size_t i = 0; i < len; i++) {
		crc ^= data[i];
		for (int bit = 0; bit < 8; bit++) {
			uint32_t mask = -(crc & 1U);

			crc = (crc >> 1) ^ (0xEDB88320U & mask);
		}
	}

	return ~crc;
}

static void uart_write_all(const struct device *uart, const uint8_t *data, size_t len)
{
	for (size_t i = 0; i < len; i++) {
		uart_poll_out(uart, data[i]);
	}
}

int bridge_proto_send_frame(const struct device *uart,
			    enum bridge_frame_type type,
			    uint8_t flags,
			    uint8_t seq,
			    const uint8_t *payload,
			    uint16_t payload_len)
{
	uint8_t header[BRIDGE_HEADER_LEN];
	uint8_t crc_buf[BRIDGE_CRC_LEN];
	uint32_t crc;

	if (uart == NULL) {
		return -EINVAL;
	}
	if (payload_len > 0U && payload == NULL) {
		return -EINVAL;
	}

	header[0] = BRIDGE_MAGIC_0;
	header[1] = BRIDGE_MAGIC_1;
	header[2] = BRIDGE_PROTO_VERSION;
	header[3] = (uint8_t)type;
	header[4] = flags;
	header[5] = seq;
	sys_put_le16(payload_len, &header[6]);

	crc = crc32_ieee_update(0U, header, sizeof(header));
	crc = crc32_ieee_update(crc, payload, payload_len);
	sys_put_le32(crc, crc_buf);

	uart_write_all(uart, header, sizeof(header));
	if (payload_len > 0U) {
		uart_write_all(uart, payload, payload_len);
	}
	uart_write_all(uart, crc_buf, sizeof(crc_buf));

	return 0;
}

int bridge_proto_send_hello_resp(const struct device *uart, uint8_t seq)
{
	static const uint8_t payload[] =
		"proto=1;name=nrf_vwg_bridge_rx;fw=0.1.0;cap=RX_ONLY";

	return bridge_proto_send_frame(uart, BRIDGE_FRAME_HELLO_RESP, 0U, seq,
				       payload, (uint16_t)(sizeof(payload) - 1U));
}

int bridge_proto_send_rx_frame(const struct device *uart,
			       uint8_t seq,
			       const struct bridge_rx_payload *rx)
{
	uint8_t payload[BRIDGE_MAX_PAYLOAD_LEN];

	if (rx == NULL || rx->psdu == NULL) {
		return -EINVAL;
	}
	if (rx->psdu_len > BRIDGE_MAX_PSDU_LEN) {
		return -EMSGSIZE;
	}

	sys_put_le64(rx->timestamp_us, &payload[0]);
	payload[8] = rx->channel;
	payload[9] = (uint8_t)rx->rssi_dbm;
	payload[10] = (uint8_t)rx->lqi;
	memcpy(&payload[BRIDGE_RX_META_LEN], rx->psdu, rx->psdu_len);

	return bridge_proto_send_frame(
		uart,
		BRIDGE_FRAME_RX_FRAME,
		0U,
		seq,
		payload,
		(uint16_t)(BRIDGE_RX_META_LEN + rx->psdu_len));
}
