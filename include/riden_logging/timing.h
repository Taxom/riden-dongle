#pragma once

#include <Arduino.h>
#include <stdint.h>
#include <riden_logging/diagnostics_state.h>

namespace RidenDongle {

// Elapsed wall time, including waits/yields. This is not CPU utilization.
struct TimingStats {
    uint32_t count = 0;
    uint32_t last_us = 0;
    uint32_t min_us = UINT32_MAX;
    uint32_t max_us = 0;
    uint64_t total_us = 0;

    void add(uint32_t us) {
        ++count;
        last_us = us;
        if (us < min_us) min_us = us;
        if (us > max_us) max_us = us;
        total_us += us;
    }
    uint32_t average_us() const { return count ? total_us / count : 0; }
};

class ScopedTiming {
    TimingStats &stats;
    const bool enabled;
    const uint32_t started;
  public:
    explicit ScopedTiming(TimingStats &stats) : stats(stats),
        enabled(detailed_diagnostics_enabled()), started(enabled ? micros() : 0) {}
    ~ScopedTiming() {
        if (enabled && detailed_diagnostics_enabled())
            stats.add(uint32_t(micros() - started));
    }
    ScopedTiming(const ScopedTiming &) = delete;
    ScopedTiming &operator=(const ScopedTiming &) = delete;
};

inline TimingStats &main_loop_timing() {
    static TimingStats stats;
    return stats;
}

} // namespace RidenDongle
