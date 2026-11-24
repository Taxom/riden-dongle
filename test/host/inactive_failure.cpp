#include <riden_modbus_bridge/riden_modbus_bridge.h>
#include <cassert>
#include <iostream>
uint64_t host_time_us=10000;
HostSerial Serial;
int main() {
    RidenDongle::RidenModbus bus; // deliberately not initialized
    RidenDongle::RidenModbusBridge bridge(bus);assert(bridge.begin());
    auto &tcp=*ModbusTCP::instance;auto &client=tcp.addClient(100);
    tcp.enqueueClient(client,0,{3,0,8,0,1});bridge.loop();
    assert(client.packets.size()==1);
    const std::vector<uint8_t> expected={0,0,0,0,0,3,1,0x83,0x0b};
    assert(client.packets[0]==expected);
    std::cout<<"PASS: initial UART wait failure returns a gateway exception with the original slot and TID zero\n";
}
