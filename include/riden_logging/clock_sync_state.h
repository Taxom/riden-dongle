#pragma once

#include <stdint.h>

namespace RidenDongle {

struct ClockSyncState {
    volatile bool pending = false;
    bool checked = false;
    bool output_known = false;
    bool output_on = true;
    uint32_t last_check_ms = 0;
    uint32_t last_write_ms = 0;
    uint32_t checks = 0;
    uint32_t deferred_output_on = 0;
    uint32_t output_read_failures = 0;
    uint32_t writes = 0;
    uint32_t successes = 0;
    uint32_t write_failures = 0;
};

inline ClockSyncState &clock_sync_state() {
    static ClockSyncState state;
    return state;
}

} // namespace RidenDongle
