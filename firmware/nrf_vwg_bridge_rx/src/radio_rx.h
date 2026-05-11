#ifndef NRF_VWG_BRIDGE_RX_RADIO_RX_H_
#define NRF_VWG_BRIDGE_RX_RADIO_RX_H_

#include <stdint.h>

#define RADIO_RX_DEFAULT_CHANNEL 15
#define RADIO_RX_QUEUE_DEPTH 16
#define RADIO_RX_MAX_PSDU_LEN 127

struct radio_rx_frame {
	uint64_t timestamp_us;
	uint8_t channel;
	int8_t rssi_dbm;
	int8_t lqi;
	uint8_t psdu_len;
	uint8_t psdu[RADIO_RX_MAX_PSDU_LEN];
};

struct radio_rx_stats {
	uint32_t rx_frames;
	uint32_t rx_dropped;
	uint32_t output_errors;
};

int radio_rx_init(uint8_t channel);
int radio_rx_get(struct radio_rx_frame *frame);
void radio_rx_count_output_error(void);
struct radio_rx_stats radio_rx_get_stats(void);

#endif
