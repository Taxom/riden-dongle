#pragma once

#include <Arduino.h>
#include <riden_logging/timing.h>

namespace RidenDongle {

// A transparent Stream adapter. It observes bytes already read by ModbusRTU;
// it never reads ahead, changes frame boundaries, retries, or writes logs.
class ObservedUart : public Stream {
  public:
    enum class Origin : uint8_t { Local = 0, Tcp = 1 };
    struct TimeoutEvent {
        uint32_t at_ms = 0;
        uint32_t duration_us = 0;
        uint32_t rx_bytes = 0;
        uint16_t reg = 0;
        uint16_t argument = 0;
        uint16_t bad_crc_frames = 0;
        uint8_t function = 0;
        Origin origin = Origin::Local;
        bool rx_seen = false;
    };
    static constexpr unsigned EVENT_COUNT = 8;
    struct Diagnostics {
        uint32_t attempts = 0;
        uint32_t tcp_attempts = 0;
        uint32_t local_attempts = 0;
        uint32_t tcp_timeouts = 0;
        uint32_t local_timeouts = 0;
        uint32_t timeouts_no_rx = 0;
        uint32_t timeouts_with_rx = 0;
        uint32_t rx_bytes = 0;
        uint32_t rx_frames = 0;
        uint32_t crc_errors = 0;
        uint32_t short_frames = 0;
        uint32_t capture_overflows = 0;
        TimingStats first_byte;
        TimingStats crc_valid_frame_read;
        TimingStats attempt_wait;
        uint32_t reply_buckets[7] = {}; // <=5,10,20,50,100,500 ms, >500 ms
        TimeoutEvent recent_timeouts[EVENT_COUNT];
        uint8_t timeout_count = 0;
        uint8_t next_timeout = 0;
    };

    void begin(Stream &stream, uint32_t baudrate) { port = &stream; baud = baudrate; }
    uint32_t baudRate() const { return baud; }
    void set_origin(Origin value) { next_origin = value; }
    void result(uint16_t value) { result_code = value; }
    const Diagnostics &get_diagnostics() const { return diagnostics; }
    const TimeoutEvent &timeout(unsigned chronological_index) const;

    void begin_frame() { capture_size = 0; }
    void end_frame();
    void finish_attempt();

    int available() override;
    int read() override;
    int peek() override { return port->peek(); }
    size_t write(uint8_t byte) override;
    size_t write(const uint8_t *data, size_t size) override;
    void flush() override;

  private:
    Stream *port = nullptr;
    uint32_t baud = 0;
    Diagnostics diagnostics;
    Origin next_origin = Origin::Local;
    Origin active_origin = Origin::Local;
    bool active = false;
    bool rx_seen = false;
    uint32_t started_us = 0;
    uint32_t attempt_rx_bytes = 0;
    uint32_t last_read_us = 0;
    uint16_t attempt_bad_crc = 0;
    uint16_t result_code = 0;
    uint8_t tx_header[6] = {};
    uint8_t active_header[6] = {};
    uint16_t tx_size = 0;
    uint8_t capture[260] = {};
    uint16_t capture_size = 0;
    void observe_tx(const uint8_t *data, size_t size);
};

} // namespace RidenDongle
