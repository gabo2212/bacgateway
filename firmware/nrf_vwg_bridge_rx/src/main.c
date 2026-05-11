#include <errno.h>
#include <stdint.h>

#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/usb/usb_device.h>

#include "bridge_proto.h"
#include "radio_rx.h"

LOG_MODULE_REGISTER(nrf_vwg_bridge_rx, LOG_LEVEL_INF);

static uint8_t next_seq(void)
{
	static uint8_t seq;

	return seq++;
}

static int wait_for_cdc_acm(const struct device *uart)
{
	uint32_t dtr = 0U;

	if (!device_is_ready(uart)) {
		return -ENODEV;
	}

	if (usb_enable(NULL) != 0) {
		return -EIO;
	}

	while (dtr == 0U) {
		(void)uart_line_ctrl_get(uart, UART_LINE_CTRL_DTR, &dtr);
		k_sleep(K_MSEC(100));
	}

	return 0;
}

int main(void)
{
	const struct device *uart = DEVICE_DT_GET_ONE(zephyr_cdc_acm_uart);
	struct radio_rx_frame frame;
	int ret;

	ret = wait_for_cdc_acm(uart);
	if (ret != 0) {
		LOG_ERR("USB CDC ACM init failed: %d", ret);
		return ret;
	}

	(void)bridge_proto_send_hello_resp(uart, next_seq());

	ret = radio_rx_init(RADIO_RX_DEFAULT_CHANNEL);
	if (ret != 0) {
		static const uint8_t payload[] = "radio_rx_init_failed";

		(void)bridge_proto_send_frame(uart, BRIDGE_FRAME_ERROR, 0U, next_seq(),
					      payload, (uint16_t)(sizeof(payload) - 1U));
		return ret;
	}

	while (true) {
		ret = radio_rx_get(&frame);
		if (ret != 0) {
			radio_rx_count_output_error();
			continue;
		}

		const struct bridge_rx_payload rx = {
			.timestamp_us = frame.timestamp_us,
			.channel = frame.channel,
			.rssi_dbm = frame.rssi_dbm,
			.lqi = frame.lqi,
			.psdu = frame.psdu,
			.psdu_len = frame.psdu_len,
		};

		ret = bridge_proto_send_rx_frame(uart, next_seq(), &rx);
		if (ret != 0) {
			radio_rx_count_output_error();
		}
	}
}
