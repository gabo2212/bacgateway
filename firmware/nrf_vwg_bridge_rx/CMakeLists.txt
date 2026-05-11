cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(nrf_vwg_bridge_rx)

target_sources(app PRIVATE
    src/main.c
    src/bridge_proto.c
    src/radio_rx.c
)
