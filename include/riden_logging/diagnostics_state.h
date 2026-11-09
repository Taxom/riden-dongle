#pragma once

namespace RidenDongle {

// Basic transport/error counters stay active. Per-byte UART observation,
// duplicate diagnostic CRC checks and timing are optional and start disabled.
inline bool &detailed_diagnostics_flag()
{
    static bool enabled = false;
    return enabled;
}

inline bool detailed_diagnostics_enabled()
{
    return detailed_diagnostics_flag();
}

inline void set_detailed_diagnostics_enabled(bool enabled)
{
    detailed_diagnostics_flag() = enabled;
}

} // namespace RidenDongle
