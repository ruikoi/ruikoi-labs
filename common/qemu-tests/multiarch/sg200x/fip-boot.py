#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build experimental FIP copies; validate vendor firmware and live SBI/RTOS."""
import argparse
import binascii
import importlib.util
import json
import pathlib
import re
import socket
import struct
import tempfile
import time

parser=argparse.ArgumentParser()
parser.add_argument('--qemu',required=True,type=pathlib.Path)
parser.add_argument('--fip',required=True,type=pathlib.Path)
parser.add_argument('--rtos-bin',required=True,type=pathlib.Path)
parser.add_argument('--output',required=True,type=pathlib.Path)
args=parser.parse_args()
out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
spec=importlib.util.spec_from_file_location('smoke',pathlib.Path(__file__).parents[2]/'hetero/hetero-smoke.py')
smoke=importlib.util.module_from_spec(spec);spec.loader.exec_module(smoke)

def crc(data):return 0xcafe0000|binascii.crc_hqx(data,0)
def update_param2(file,p2):struct.pack_into('<I',file,p2+8,crc(file[p2+12:p2+4096]))
original=args.fip.read_bytes()
p2=struct.unpack_from('<I',original,0xe0)[0]
rtos=args.rtos_bin.read_bytes();rtos+=bytes((-len(rtos))%512)
combined=bytearray(original)
struct.pack_into('<4I',combined,p2+32,crc(rtos),len(combined),len(rtos),0x0c000000)
combined+=rtos;update_param2(combined,p2)
(out/'fip-freertos.bin').write_bytes(combined)

# Supervisor payload repeatedly invokes the actual vendor OpenSBI BASE API.
words=[]
def emit(w):words.append(w&0xffffffff)
def li(rd,value):
    upper=(value+0x800)>>12
    emit(upper<<12|rd<<7|0x37)
    emit(((value-(upper<<12))&0xfff)<<20|rd<<15|rd<<7|0x13)
    if value&0x80000000:
        emit(32<<20|rd<<15|1<<12|rd<<7|0x13)
        emit(32<<20|rd<<15|5<<12|rd<<7|0x13)
def store(rt,offset):emit(0x2023|5<<15|rt<<20|(offset&31)<<7|(offset>>5)<<25)
li(5,0x80600000);li(6,0)
li(17,0x54494d45);li(16,0);li(10,0xffffffff)
emit(0x73);store(10,12) # TIME set_timer; error field
loop=len(words)
li(17,0x10);li(16,0);emit(0x73)
store(10,0);store(11,4)
emit(1<<20|6<<15|6<<7|0x13);store(6,8)
delta=(loop-len(words))*4;imm=delta&0x1fffff
emit(((imm>>20)&1)<<31|((imm>>1)&0x3ff)<<21|((imm>>11)&1)<<20|((imm>>12)&0xff)<<12|0x6f)
probe=struct.pack('<'+'I'*len(words),*words)
header=bytearray(struct.pack('<4IQ2I',0,0x20203342,0,32+len(probe),0x80200000,0,0))
loader=header+probe;struct.pack_into('<I',loader,8,crc(loader[12:]))
functional=bytearray(combined);struct.pack_into('<I',functional,p2+68,len(functional))
functional+=loader;update_param2(functional,p2)
(out/'fip-sbi-probe.bin').write_bytes(functional)

def read_prompt(serial,timeout=5):
    result=b'';end=time.monotonic()+timeout
    while time.monotonic()<end:
        try:
            data=serial.recv(4096)
            if not data:raise RuntimeError('serial closed')
            result+=data
            if b'rtos> ' in result:return result.decode(errors='replace')
        except socket.timeout:pass
    raise TimeoutError(result)

results=[]
for name,image in [('vendor',args.fip.resolve()),('vendor-rtos',out/'fip-freertos.bin'),('sbi-rtos',out/'fip-sbi-probe.bin')]:
    main=out/f'{name}-main.log';main.unlink(missing_ok=True)
    small_log=''
    with tempfile.TemporaryDirectory(prefix='sgfip-') as tmp:
        path=pathlib.Path(tmp)/'uart.sock'
        cmd=[str(args.qemu.resolve()),'-M',f'milkv-duos,boot-mode=fip,fip={image}',
             '-accel','tcg,thread=single','-display','none','-monitor','none',
             '-serial','file:'+str(main),'-serial',f'unix:{path},server=on,wait=off',
             '-qmp','stdio','-S']
        (out/f'{name}-command.json').write_text(json.dumps(cmd,indent=2))
        with (out/f'{name}-qmp.log').open('w') as log:
            vm=smoke.QEMU(cmd,log);serial=socket.socket(socket.AF_UNIX)
            try:
                serial.connect(str(path));serial.settimeout(.1)
                vm.qmp('cont')
                end=time.monotonic()+5
                while time.monotonic()<end:
                    if main.exists() and ('U-Boot 2021.10' if name!='sbi-rtos' else 'Domain0 Next Mode') in main.read_text(errors='replace'):break
                    time.sleep(.01)
                text=main.read_text(errors='replace')
                assert 'OpenSBI v0.9' in text and 'Platform HART Count       : 1' in text,text[:2000]
                if name=='vendor':
                    vm.qmp('stop')
                    regs=vm.qmp('human-monitor-command',**{'command-line':'info registers','cpu-index':1})
                    assert '000000009fe00000' in regs or '000000009fe00004' in regs,regs
                else:
                    small_log+=read_prompt(serial)
                    for epoch in range(2):
                        serial.sendall(b'uname\r');small_log+=read_prompt(serial)
                        samples=[]
                        for n in range(2):
                            serial.sendall(b'ticks\r');reply=read_prompt(serial);small_log+=reply
                            samples.append(tuple(map(int,re.search(r'ticks=(\d+) heartbeat=(\d+) uart_irqs=(\d+)',reply).groups())))
                            time.sleep(.2)
                        assert all(samples[1][i]>samples[0][i] for i in range(3)),samples
                        assert 'XLEN=64' in small_log
                        if name=='sbi-rtos':
                            memory=pathlib.Path(tmp)/'sbi.bin';values=[]
                            for n in range(2):
                                vm.qmp('pmemsave',val=0x80600000,size=16,filename=str(memory))
                                values.append(struct.unpack('<4I',memory.read_bytes()));time.sleep(.05)
                            assert values[0][0]==values[1][0]==0 and values[0][1]==values[1][1]==3,values
                            assert values[1][2]>values[0][2] and values[1][3]==0,values
                            regs=vm.qmp('human-monitor-command',**{'command-line':'info registers','cpu-index':0})
                            (out/f'{name}-main-registers.log').write_text(regs)
                            print(name,'epoch',epoch,'SBI',values,'RTOS',samples,flush=True)
                        if epoch==0:
                            vm.qmp('system_reset');small_log+='\n=== RESET ===\n'+read_prompt(serial)
                    (out/f'{name}-small.log').write_text(small_log)
                vm.qmp('quit');assert vm.proc.wait(timeout=5)==0
                results.append([name,'PASS']);print(name,'PASS',flush=True)
            finally:serial.close();vm.close()
(out/'results.json').write_text(json.dumps(results,indent=2))
