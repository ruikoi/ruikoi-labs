#!/usr/bin/env python3
import importlib.util
import json
import pathlib
import re
import time

import argparse

parser=argparse.ArgumentParser(description='Experimental Duo S ROM/FIP acceptance')
parser.add_argument('--qemu',required=True,type=pathlib.Path)
parser.add_argument('--rom',required=True,type=pathlib.Path)
parser.add_argument('--image',required=True,type=pathlib.Path)
parser.add_argument('--output',required=True,type=pathlib.Path)
args=parser.parse_args()
root=args.output.resolve()
root.mkdir(parents=True,exist_ok=True)
qemu=args.qemu.resolve()
rom=args.rom.resolve()
image=args.image.resolve()
spec=importlib.util.spec_from_file_location('smoke',
    pathlib.Path(__file__).parents[2]/'hetero/hetero-smoke.py')
smoke=importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
cmd = [str(qemu), '-M',
       'milkv-duos,boot-mode=rom', '-bios', str(rom),
       '-accel', 'tcg,thread=single', '-display', 'none', '-monitor', 'none',
       '-serial', 'file:'+str(root/'rom-uart.log'), '-serial', 'null',
       '-drive', 'file='+str(image)+',if=sd,format=raw,snapshot=on',
       '-qmp', 'stdio', '-S', '-d', 'unimp,guest_errors,int', '-D', str(root/'rom-mmio.log')]
(root/'rom-command.json').write_text(json.dumps(cmd,indent=2))
with (root/'rom-qmp.log').open('w') as log:
    vm=smoke.QEMU(cmd,log)
    try:
        vm.qmp('cont')
        time.sleep(.3)
        vm.qmp('stop')
        uart=(root/'rom-uart.log').read_text(errors='replace')
        assert 'ROM: verified FIP' in uart and '\nFSBL ' in uart, uart
        regs=vm.qmp('human-monitor-command',**{'command-line':'info registers','cpu-index':0})
        (root/'rom-registers.log').write_text(regs)
        assert int(re.search(r'\bmtval\s+([0-9a-f]+)',regs).group(1),16)==0x03050198, regs
        for address,size,name in [(0x0c039000,4096,'loaded-param1.bin')]:
            vm.qmp('pmemsave',val=address,size=size,filename=str(root/name))
        vm.qmp('system_reset')
        vm.qmp('cont')
        time.sleep(.3)
        vm.qmp('stop')
        uart=(root/'rom-uart.log').read_text(errors='replace')
        assert uart.count('ROM: verified FIP')==2 and uart.count('\nFSBL ')==2, uart
        (root/'rom-results.json').write_text(json.dumps({
            'sd_fat16_fip_crc_and_fsbl_handoff':'PASS',
            'rom_system_reset':'PASS',
            'full_vendor_boot':'BLOCKED',
            'first_fault_address':'0x03050198',
            'first_fault_component':'EFUSE',
        },indent=2))
        vm.qmp('quit')
        assert vm.proc.wait(timeout=5)==0
    finally:
        vm.close()
print((root/'rom-uart.log').read_text(errors='replace'))
print((root/'rom-mmio.log').read_text(errors='replace')[:2000])
print((root/'rom-registers.log').read_text()[:350])
