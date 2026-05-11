#ifndef NRF_VWG_BRIDGE_RX_BRIDGE_PROTO_H_
#define NRF_VWG_BRIDGE_RX_BRIDGE_PROTO_H_

#include <stdint.h>
#include <stddef.h>

#include <zephyr/device.h>

#define BRIDGE_MAGIC_0 'V'
#define BRIDGE_MAGIC_1 'W'
#define BRIDGE_PROTO_VERSION 1
#define BRIDGE_HEADER_LEN 8
#define BRIDGE_CRC_LEN 4
#define BRIDGE_RX_META_LEN 11
#define BRIDGE_MAX_PSDU_LEN 127

enum bridge_frame_type {
	BRIDGE_FRAME_HELLO = 0x01,
	BRIDGE_FRAME_HELLO_RESP = 0x02,
	BRIDGE_FRAME_RX_FRAME = 0x03,
	BRIDGE_FRAME_TX_RAW_RESERVED = 0x04,
	BRIDGE_FRAME_STATE = 0x05,
	BRIDGE_FRAME_ERROR = 0x06,
	BRIDGE_FRAME_GET_STATS = 0x07,
	BRIDGE_FRAME_GET_STATS_RESP = 0x08,
};

struct bridge_rx_payload {
	uint64_t timestamp_us;
	uint8_t channel;
	int8_t rssi_dbm;
	int8_t lqi;
	const uint8_t *psdu;
	uint8_t psdu_len;
};

int bridge_proto_send_frame(const struct device *uart,
			    enum bridge_frame_type type,
			    uint8_t flags,
			    uint8_t seq,
			    const uint8_t *payload,
			    uint16_t payload_len);

int bridge_proto_send_hello_resp(const struct device *uart, uint8_t seq);

int bridge_proto_send_rx_frame(const struct device *uart,
			       uint8_t seq,
			       const struct bridge_rx_payload *rx);

#endif
