#pragma once
#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>

struct HostEEPROM {
    std::array<uint8_t,512> stored, working;
    bool commit_ok=true;
    unsigned commits=0;
    HostEEPROM() { stored.fill(0xff);working=stored; }
    bool begin(size_t size) { assert(size==stored.size());working=stored;return true; }
    template<class T> void get(int address,T &value) {
        assert(address>=0 && address+sizeof(T)<=working.size());
        std::memcpy(&value,working.data()+address,sizeof(T));
    }
    template<class T> void put(int address,const T &value) {
        assert(address>=0 && address+sizeof(T)<=working.size());
        std::memcpy(working.data()+address,&value,sizeof(T));
    }
    bool commit() { ++commits;if(commit_ok) stored=working;return commit_ok; }
};
inline HostEEPROM EEPROM;
