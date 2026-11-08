// SPDX-FileCopyrightText: 2024 Peder Toftegaard Olsen
//
// SPDX-License-Identifier: MIT

#pragma once

#include <riden_modbus/riden_modbus.h>

#include <ModbusTCP.h>
#include <list>

namespace RidenDongle
{

class RidenModbusTCP : public ModbusTCP
{
  public:
    /**
     * @brief Get a list of the connected clients.
     */
    std::list<IPAddress> get_connected_clients();

    /**
     * @brief Disconnect a client.
     *
     * @param ip IP-address of client to disconnect.
     */
    void disconnect_client(const IPAddress &ip);

    // Send without using ModbusTCP's shared receive/PDU buffer. A UART reply
    // may arrive while task() is processing the next incoming TCP request.
    bool send_response(uint32_t ip, uint16_t transaction, uint8_t unit,
                       const uint8_t *data, uint8_t len);
};

/**
 * @brief Modbus TCP bridge.
 */
class RidenModbusBridge
{
  public:
    explicit RidenModbusBridge(RidenModbus &riden_modbus) : riden_modbus(riden_modbus){};
    bool begin();
    bool loop();

    uint16_t port();
    std::list<IPAddress> get_connected_clients();
    void disconnect_client(const IPAddress &ip);

    Modbus::ResultCode modbus_tcp_raw_callback(uint8_t *data, uint8_t len, void *custom_data);
    Modbus::ResultCode modbus_rtu_raw_callback(uint8_t *data, uint8_t len, void *custom);

  private:
    RidenModbus &riden_modbus;
    RidenModbusTCP modbus_tcp;
    bool initialized = false;

    void finish_request();
    void fail_request(Modbus::ResultCode error);
    bool valid_response(const uint8_t *data, uint8_t len) const;

    // State of any currently running modbus command
    bool request_pending = false;
    uint8_t request_header[5] = {};
    uint8_t request_length = 0;
    uint16_t transaction_id = 0; // ModbusTCP transaction
    uint8_t slave_id = 0;        // Request slave
    uint32_t ip = 0;
};

} // namespace RidenDongle
