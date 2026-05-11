#include "radio_rx.h"

#include <errno.h>
#include <string.h>

#include <nrf_802154.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(radio_rx, LOG_LEVEL_INF);

K_MSGQ_DEFINE(rx_msgq, sizeof(struct radio_rx_frame), RADIO_RX_QUEUE_DEPTH, 4);

static uint8_t current_channel = RADIO_RX_DEFAULT_CHANNEL;
static struct radio_rx_stats stats;

int radio_rx_init(uint8_t channel)
{
	if (channel < 11U || channel > 26U) {
		return -EINVAL;
	}

	current_channel = channel;

	nrf_802154_init();
	nrf_802154_channel_set(channel);
	nrf_802154_promiscuous_set(true);

	if (!nrf_802154_receive()) {
		return -EIO;
	}

	LOG_INF("802.15.4 RX-only sniffer started on channel %u", channel);
	return 0;
}

int radio_rx_get(struct radio_rx_frame *frame)
{
	if (frame == NULL) {
		return -EINVAL;
	}

	return k_msgq_get(&rx_msgq, frame, K_FOREVER);
}

void radio_rx_count_output_error(void)
{
	stats.output_errors++;
}

struct radio_rx_stats radio_rx_get_stats(void)
{
	return stats;
}

void nrf_802154_received_raw(uint8_t *data, int8_t power, uint8_t lqi)
{
	struct radio_rx_frame frame;
	uint8_t psdu_len;

	if (data == NULL) {
		stats.rx_dropped++;
		return;
	}

	/*
	 * Nordic raw RX buffers are length-prefixed: data[0] is the PSDU length,
	 * followed by the raw 802.15.4 PSDU bytes expected by the Python decoder.
	 */
	psdu_len = data[0];
	if (psdu_len > RADIO_RX_MAX_PSDU_LEN) {
		stats.rx_dropped++;
		nrf_802154_buffer_free_raw(data);
		return;
	}

	memset(&frame, 0, sizeof(frame));
	frame.timestamp_us = (uint64_t)k_ticks_to_us_floor64(k_uptime_ticks());
	frame.channel = current_channel;
	frame.rssi_dbm = power;
	frame.lqi = (int8_t)lqi;
	frame.psdu_len = psdu_len;
	memcpy(frame.psdu, &data[1], psdu_len);

	if (k_msgq_put(&rx_msgq, &frame, K_NO_WAIT) != 0) {
		stats.rx_dropped++;
	} else {
		stats.rx_frames++;
	}

	nrf_802154_buffer_free_raw(data);
}

void nrf_802154_receive_failed(nrf_802154_rx_error_t error, uint32_t id)
{
	ARG_UNUSED(error);
	ARG_UNUSED(id);

	stats.rx_dropped++;
	(void)nrf_802154_receive();
}
