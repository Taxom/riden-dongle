#!/usr/bin/env python3
"""Host regression tests using the real modbus-esp8266 RTU implementation."""
import argparse
import pathlib
import subprocess
import tempfile

parser=argparse.ArgumentParser()
parser.add_argument('--modbus-lib',type=pathlib.Path,
                    default=pathlib.Path('.pio/libdeps/esp12e/modbus-esp8266/src'))
parser.add_argument('--reproduce-upstream',action='store_true')
args=parser.parse_args()
root=pathlib.Path(__file__).resolve().parents[2]
lib=args.modbus_lib.resolve()
assert (lib/'ModbusRTU.cpp').is_file(), 'Install esp12e dependencies or pass --modbus-lib <library>/src'
with tempfile.TemporaryDirectory(prefix='riden-host-') as tmp:
    tmp=pathlib.Path(tmp)
    sources=[root/'src/riden_modbus/riden_modbus.cpp',root/'src/riden_modbus_bridge/riden_modbus_bridge.cpp']
    headers=root/'include'
    if args.reproduce_upstream:
        headers=tmp/'include'
        for relative in ['include/riden_modbus/riden_modbus.h','include/riden_modbus/riden_modbus_registers.h',
                         'include/riden_modbus_bridge/riden_modbus_bridge.h']:
            target=tmp/relative;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(subprocess.check_output(['git','show','e9a060958c9dcf82f24353e36c2282b057690be4:'+relative],cwd=root))
        sources=[]
        for relative in ['src/riden_modbus/riden_modbus.cpp','src/riden_modbus_bridge/riden_modbus_bridge.cpp']:
            target=tmp/relative;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(subprocess.check_output(['git','show','e9a060958c9dcf82f24353e36c2282b057690be4:'+relative],cwd=root))
            sources.append(target)
    cmd=['g++','-std=gnu++17','-g','-O1','-DMODBUS_USE_STL',
         '-fsanitize=address,undefined','-fno-omit-frame-pointer',
         '-I'+str(root/'test/host/include'),'-I'+str(headers),'-I'+str(root/'include'),'-I'+str(lib),
         str(root/'test/host/shared_uart.cpp'),*(str(x) for x in sources),
         str(lib/'Modbus.cpp'),str(lib/'ModbusRTU.cpp'),'-o',str(tmp/'shared_uart')]
    subprocess.run(cmd,check=True)
    subprocess.run([str(tmp/'shared_uart')]+(['--reproduce'] if args.reproduce_upstream else []),check=True)
