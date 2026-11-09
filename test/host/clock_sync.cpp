#include <Arduino.h>
#include <riden_logging/clock_sync_state.h>
#include <cassert>
#include <ctime>
#include <iostream>

using namespace RidenDongle;
uint64_t host_time_us=0;
HostSerial Serial;
static time_t epoch_base=1700000000;

struct HostPsu {
    bool output_on=true, read_ok=true, write_ok=true;
    unsigned reads=0, writes=0;
    tm written={};
    std::function<void()> on_read, on_write;
    bool get_output_on(bool &value) {
        ++reads;
        if(on_read) on_read();
        if(!read_ok) return false;
        value=output_on;
        return true;
    }
    bool set_clock(const tm &value) {
        assert(!output_on); // no test path may write with output enabled
        ++writes;written=value;
        if(on_write) on_write();
        return write_ok;
    }
} riden_modbus;

static time_t clock_test_time(time_t *destination) {
    *destination=epoch_base+host_time_us/1000000;
    return *destination;
}
static tm *clock_test_localtime_r(const time_t *value, tm *destination) {
    return gmtime_r(value,destination);
}
#define LOG_LN(...) ((void)0)
#define time clock_test_time
#define localtime_r clock_test_localtime_r
// PRODUCTION_CLOCK_SYNC
#undef time
#undef localtime_r

static void reset() {
    clock_sync_state()=ClockSyncState{};
    riden_modbus=HostPsu{};
    host_time_us=10000000;
    epoch_base=1700000000;
}
static void check_latest_time() {
    const time_t now=epoch_base+host_time_us/1000000;
    tm expected;gmtime_r(&now,&expected);
    assert(riden_modbus.written.tm_year==expected.tm_year);
    assert(riden_modbus.written.tm_mon==expected.tm_mon);
    assert(riden_modbus.written.tm_mday==expected.tm_mday);
    assert(riden_modbus.written.tm_hour==expected.tm_hour);
    assert(riden_modbus.written.tm_min==expected.tm_min);
    assert(riden_modbus.written.tm_sec==expected.tm_sec);
}

int main() {
    reset();auto &state=clock_sync_state();
    synchronize_clock_if_output_off();
    assert(riden_modbus.reads==0 && riden_modbus.writes==0);
    state.pending=true;
    synchronize_clock_if_output_off();
    assert(state.pending && state.deferred_output_on==1 && riden_modbus.writes==0);
    // Frequent main-loop calls must not poll the output on every iteration.
    for(unsigned i=0;i<4999;++i) { delay(1);synchronize_clock_if_output_off(); }
    assert(riden_modbus.reads==1);
    riden_modbus.output_on=false;
    delay(1);synchronize_clock_if_output_off();
    assert(!state.pending && state.successes==1 && riden_modbus.writes==1);
    check_latest_time();
    synchronize_clock_if_output_off();assert(riden_modbus.reads==2);
    std::cout<<"PASS: clock sync deferred while output ON, rate-limited, applied when OFF\n";

    reset();state.pending=true;riden_modbus.output_on=false;riden_modbus.read_ok=false;
    synchronize_clock_if_output_off();
    assert(state.pending && !state.output_known && state.output_read_failures==1);
    assert(riden_modbus.writes==0);
    delay(5000);riden_modbus.read_ok=true;synchronize_clock_if_output_off();
    assert(state.successes==1 && !state.pending);check_latest_time();
    std::cout<<"PASS: unknown output state cannot authorize a clock write\n";

    reset();state.pending=true;riden_modbus.output_on=false;riden_modbus.write_ok=false;
    synchronize_clock_if_output_off();
    assert(state.pending && state.write_failures==1 && state.writes==1);
    riden_modbus.output_on=true;riden_modbus.write_ok=true;
    delay(5000);synchronize_clock_if_output_off();
    assert(state.pending && riden_modbus.writes==1);
    riden_modbus.output_on=false;delay(5000);synchronize_clock_if_output_off();
    assert(!state.pending && state.writes==2 && state.successes==1);check_latest_time();
    std::cout<<"PASS: failed clock writes recheck output before retrying\n";

    reset();state.pending=true;riden_modbus.output_on=false;
    riden_modbus.on_write=[] { clock_sync_state().pending=true;epoch_base+=60; };
    synchronize_clock_if_output_off();
    assert(state.pending && state.successes==1);
    riden_modbus.on_write=nullptr;delay(5000);synchronize_clock_if_output_off();
    assert(!state.pending && state.successes==2);check_latest_time();
    std::cout<<"PASS: an NTP update during the write remains pending\n";

    reset();state.pending=true;riden_modbus.output_on=false;
    riden_modbus.on_read=[] { epoch_base+=3600; };
    synchronize_clock_if_output_off();
    assert(!state.pending && state.successes==1);check_latest_time();
    std::cout<<"PASS: deferred sync writes current time, not the original NTP timestamp\n";

    reset();host_time_us=((uint64_t(1)<<32)-2000)*1000;
    state.pending=true;synchronize_clock_if_output_off();
    riden_modbus.output_on=false;delay(4999);synchronize_clock_if_output_off();
    assert(riden_modbus.writes==0);
    delay(1);synchronize_clock_if_output_off();
    assert(riden_modbus.writes==1 && !state.pending);
    std::cout<<"PASS: clock-sync retry interval survives millis rollover\n";
}
