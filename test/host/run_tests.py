#!/usr/bin/env python3
"""Host regression tests using the real modbus-esp8266 RTU implementation."""
import argparse
import json
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
    sources=[root/'src/riden_modbus/riden_modbus.cpp',root/'src/riden_modbus_bridge/riden_modbus_bridge.cpp',
             root/'src/riden_modbus/observed_uart.cpp']
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
    if args.reproduce_upstream:
        cmd.insert(1,'-DRIDEN_REPRODUCE_UPSTREAM')
    subprocess.run(cmd,check=True)
    subprocess.run([str(tmp/'shared_uart')]+(['--reproduce'] if args.reproduce_upstream else []),check=True)
    if not args.reproduce_upstream:
        cmd[cmd.index(str(root/'test/host/shared_uart.cpp'))]=str(root/'test/host/tcp_sessions.cpp')
        cmd.insert(1,'-I'+str(root/'test/host/real_tcp'))
        cmd[-1]=str(tmp/'tcp_sessions')
        subprocess.run(cmd,check=True)
        subprocess.run([str(tmp/'tcp_sessions')],check=True)
        http_source=(root/'src/riden_http_server/riden_http_server.cpp').read_text()
        handler=http_source[http_source.index('static void append_timing('):
                            http_source.index('\nvoid RidenHttpServer::handle_modbus_qps()')]
        http_test=tmp/'http_diagnostics.cpp'
        http_test.write_text((root/'test/host/http_diagnostics.cpp').read_text().replace('// PRODUCTION_HANDLER',handler))
        cmd.remove('-I'+str(root/'test/host/real_tcp'))
        cmd[cmd.index(str(root/'test/host/tcp_sessions.cpp'))]=str(http_test)
        cmd[-1]=str(tmp/'http_diagnostics')
        subprocess.run(cmd,check=True)
        result=subprocess.run([str(tmp/'http_diagnostics')],check=True,text=True,stdout=subprocess.PIPE)
        data=json.loads(result.stdout)
        assert data['version']=='1.7.0-taxom.9' and data['requests']==10
        assert data['setpoint_validation']=='strict_v1' and data['rejected_requests']==0
        assert data['diagnostics_enabled'] is True
        assert data['detail_scope']=='enabled_periods_since_boot'
        assert data['read_retries']==data['recovered_reads']==10
        assert data['uart']['tcp_timeouts']==data['uart']['timeouts_no_rx']==10
        assert len(data['uart']['recent_timeouts'])==8
        assert data['uart']['attempts']==data['uart']['attempt_wait']['count']==21
        assert data['timing']['http_diagnostics']['max_us']==120000
        assert data['system']['wifi_rssi_dbm']==-55
        assert data['system']['psu_connected'] is True
        assert data['system']['reset_reason_code']==4
        assert data['system']['flash_real_bytes']==data['system']['flash_configured_bytes']==4194304
        assert data['system']['sketch_bytes']==530000
        assert data['clock_sync']['policy']=='output_off_only'
        assert data['clock_sync']['writes']==0 and data['clock_sync']['pending'] is False
        assert data['clock_sync']['last_check_output_known'] is False
        assert data['clock_sync']['last_check_output_on'] is True
        assert data['local_reads']=={'retries':0,'recovered':0,'failures':0}
        assert data['web']['toggle_reconciled_writes']==0
        assert data['timing']['http_toggle']['count']==0
        print('PASS: production diagnostics handler JSON, HTTP send timing, no UART polling')
        status=http_source[http_source.index('void RidenHttpServer::handle_status_get('):
                           http_source.index('\nvoid RidenHttpServer::handle_set_i()')]
        toggle=http_source[http_source.index('void RidenHttpServer::handle_toggle_out()'):
                           http_source.index('\nvoid RidenHttpServer::send_redirect_root()')]
        setters=http_source[http_source.index('void RidenHttpServer::handle_set_i()'):
                            http_source.index('\nvoid RidenHttpServer::handle_toggle_out()')]
        protection=http_source[http_source.index('static String protection_to_string('):
                               http_source.index('\nstatic String outputmode_to_string(')]
        control_test=tmp/'http_control.cpp'
        control_test.write_text((root/'test/host/http_control.cpp').read_text().replace(
            '// PRODUCTION_STATUS_AND_TOGGLE',protection+'\n'+status+'\n'+setters+'\n'+toggle))
        cmd[cmd.index(str(http_test))]=str(control_test)
        cmd[-1]=str(tmp/'http_control')
        subprocess.run(cmd,check=True)
        subprocess.run([str(tmp/'http_control')],check=True)
        subprocess.run([str(tmp/'http_control'),'--diagnostics-on'],check=True)
        config_handler=http_source[http_source.index('void RidenHttpServer::handle_config_get()'):
                                   http_source.index('\nvoid RidenHttpServer::handle_firmware_update_post()')]
        config_test=tmp/'config_diagnostics.cpp'
        config_test.write_text((root/'test/host/config_diagnostics.cpp').read_text().replace(
            '// PRODUCTION_CONFIG_HANDLERS',config_handler))
        subprocess.run(['g++','-std=gnu++17','-g','-O1','-fsanitize=address,undefined',
                        '-fno-omit-frame-pointer','-DDEFAULT_UART_BAUDRATE=9600','-DBUILD_VERSION=HOST_TEST',
                        '-I'+str(root/'include'),'-I'+str(root/'test/host/include'),
                        '-I'+str(root/'src/riden_http_server'),str(config_test),
                        str(root/'src/riden_config/riden_config.cpp'),'-o',str(tmp/'config_diagnostics')],check=True)
        subprocess.run([str(tmp/'config_diagnostics')],check=True)
        update=http_source[http_source.index('void RidenHttpServer::handle_firmware_update_post()'):
                           http_source.index('\nvoid RidenHttpServer::handle_disconnect_client_post()')]
        reboot=http_source[http_source.index('void RidenHttpServer::handle_reboot_dongle_get()'):
                           http_source.index('\nvoid RidenHttpServer::handle_control_get(')]
        main_source=(root/'src/main.cpp').read_text()
        main_loop=main_source[main_source.index('void loop()'):main_source.index('\nvoid tick()')]
        update_test=tmp/'http_update.cpp'
        update_test.write_text((root/'test/host/http_update.cpp').read_text().replace(
            '// PRODUCTION_UPDATE_HANDLERS',update+'\n'+reboot).replace('// PRODUCTION_MAIN_LOOP',main_loop))
        subprocess.run(['g++','-std=gnu++17','-g','-O1','-fsanitize=address,undefined',
                        '-fno-omit-frame-pointer','-I'+str(root/'test/host/include'),
                        '-I'+str(root/'include'),'-I'+str(root/'src/riden_http_server'),
                        str(update_test),'-o',str(tmp/'http_update')],check=True)
        subprocess.run([str(tmp/'http_update')],check=True)
        clock_handler=main_source[main_source.index('static void synchronize_clock_if_output_off()'):
                                  main_source.index('\nvoid loop()')]
        clock_test=tmp/'clock_sync.cpp'
        clock_test.write_text((root/'test/host/clock_sync.cpp').read_text().replace('// PRODUCTION_CLOCK_SYNC',clock_handler))
        subprocess.run(['g++','-std=gnu++17','-g','-O1','-fsanitize=address,undefined',
                        '-fno-omit-frame-pointer','-I'+str(root/'test/host/include'),
                        '-I'+str(root/'include'),str(clock_test),'-o',str(tmp/'clock_sync')],check=True)
        subprocess.run([str(tmp/'clock_sync')],check=True)
        display_functions=http_source[http_source.index('static String voltage_to_string('):
                                      http_source.index('\nstatic String power_to_string(')]
        display_test=tmp/'display_precision.cpp'
        display_test.write_text((root/'test/host/display_precision.cpp').read_text().replace(
            '// PRODUCTION_DISPLAY_FUNCTIONS',display_functions+'\n'+protection+'\n'+status))
        cmd[cmd.index(str(control_test))]=str(display_test)
        cmd[-1]=str(tmp/'display_precision')
        subprocess.run(cmd,check=True)
        for model,current_range,v_digits,i_digits in [
            (60065,0,3,4),(60060,0,2,3),(60125,0,3,4),
            (60125,1,3,3),(60185,0,3,3),(60301,0,2,2)]:
            result=subprocess.run([str(tmp/'display_precision'),str(model),str(current_range),
                                   str(v_digits),str(i_digits)],check=True,text=True,stdout=subprocess.PIPE)
            data=json.loads(result.stdout.splitlines()[0])
            assert data['voltage_precision']==data['precision']==v_digits
            assert data['current_precision']==i_digits
            assert data['set_v']==2601/10**v_digits and data['out_v']==2603/10**v_digits
            assert data['set_c']==501/10**i_digits and data['out_c']==503/10**i_digits
            if model==60125:
                changed=json.loads(result.stdout.splitlines()[1])
                changed_digits=3 if current_range==0 else 4
                assert changed['current_precision']==changed_digits
                assert changed['set_c']==501/10**changed_digits
            print('PASS: model %d range %d API precision, scaled values and decimal read/write preservation' %
                  (model,current_range))
        subprocess.run(['node',str(root/'test/host/web_precision.js')],check=True,cwd=root)
