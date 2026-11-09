#include <riden_modbus/observed_uart.h>
#include <Modbus.h>
#include <cstring>

using namespace RidenDongle;

int ObservedUart::available()
{
    const int count = port->available();
    if (active && count > 0 && !rx_seen) {
        rx_seen = true;
        diagnostics.first_byte.add(uint32_t(micros() - started_us));
    }
    return count;
}

int ObservedUart::read()
{
    const int byte = port->read();
    if (byte >= 0 && active) {
        ++diagnostics.rx_bytes;
        ++attempt_rx_bytes;
        last_read_us = micros();
        if (capture_size < sizeof(capture)) capture[capture_size] = byte;
        ++capture_size;
    }
    return byte;
}

void ObservedUart::observe_tx(const uint8_t *data, size_t size)
{
    for (size_t i = 0; i < size && tx_size + i < sizeof(tx_header); ++i)
        tx_header[tx_size + i] = data[i];
    tx_size += size;
}

size_t ObservedUart::write(uint8_t byte)
{
    const size_t written = port->write(byte);
    if (written) observe_tx(&byte, 1);
    return written;
}

size_t ObservedUart::write(const uint8_t *data, size_t size)
{
    const size_t written = port->write(data, size);
    observe_tx(data, written);
    return written;
}

void ObservedUart::flush()
{
    port->flush();
    if (!tx_size) return;
    // Start after the real UART flush: outgoing wire time is excluded.
    started_us = micros();
    active = true;
    active_origin = next_origin;
    rx_seen = false;
    attempt_rx_bytes = 0;
    attempt_bad_crc = 0;
    result_code = Modbus::EX_TIMEOUT;
    std::memcpy(active_header, tx_header, sizeof(active_header));
    std::memset(tx_header, 0, sizeof(tx_header));
    tx_size = 0;
    ++diagnostics.attempts;
    if (active_origin == Origin::Tcp) ++diagnostics.tcp_attempts;
    else ++diagnostics.local_attempts;
}

void ObservedUart::end_frame()
{
    if (!capture_size) return;
    ++diagnostics.rx_frames;
    if (capture_size > sizeof(capture)) {
        ++diagnostics.capture_overflows;
        return;
    }
    if (capture_size < 4) {
        ++diagnostics.short_frames;
        return;
    }
    uint16_t crc = 0xffff;
    for (uint16_t i = 0; i < capture_size - 2; ++i) {
        crc ^= capture[i];
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = crc & 1 ? (crc >> 1) ^ 0xa001 : crc >> 1;
    }
    const uint16_t received = uint16_t(capture[capture_size - 2]) |
                              (uint16_t(capture[capture_size - 1]) << 8);
    if (crc != received) {
        ++diagnostics.crc_errors;
        ++attempt_bad_crc;
        return;
    }
    const uint32_t elapsed = uint32_t(last_read_us - started_us);
    diagnostics.crc_valid_frame_read.add(elapsed);
    const uint32_t boundaries[] = {5000, 10000, 20000, 50000, 100000, 500000};
    unsigned bucket = 0;
    while (bucket < 6 && elapsed > boundaries[bucket]) ++bucket;
    ++diagnostics.reply_buckets[bucket];
}

void ObservedUart::finish_attempt()
{
    if (!active) return;
    const uint32_t duration = uint32_t(micros() - started_us);
    diagnostics.attempt_wait.add(duration);
    if (result_code == Modbus::EX_TIMEOUT) {
        if (active_origin == Origin::Tcp) ++diagnostics.tcp_timeouts;
        else ++diagnostics.local_timeouts;
        if (rx_seen) ++diagnostics.timeouts_with_rx;
        else ++diagnostics.timeouts_no_rx;
        auto &event = diagnostics.recent_timeouts[diagnostics.next_timeout];
        event.at_ms = millis();
        event.duration_us = duration;
        event.rx_bytes = attempt_rx_bytes;
        event.bad_crc_frames = attempt_bad_crc;
        event.origin = active_origin;
        event.function = active_header[1];
        event.reg = (uint16_t(active_header[2]) << 8) | active_header[3];
        event.argument = (uint16_t(active_header[4]) << 8) | active_header[5];
        event.rx_seen = rx_seen;
        diagnostics.next_timeout = (diagnostics.next_timeout + 1) % EVENT_COUNT;
        if (diagnostics.timeout_count < EVENT_COUNT) ++diagnostics.timeout_count;
    }
    active = false;
}

const ObservedUart::TimeoutEvent &ObservedUart::timeout(unsigned chronological_index) const
{
    const unsigned first = diagnostics.timeout_count == EVENT_COUNT ? diagnostics.next_timeout : 0;
    return diagnostics.recent_timeouts[(first + chronological_index) % EVENT_COUNT];
}
