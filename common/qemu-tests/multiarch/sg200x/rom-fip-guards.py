#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Malformed FIP tests use disposable sparse SD copies; original is unchanged."""
import binascii
import importlib.util
import json
import pathlib
import shutil
import struct
import tempfile
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
with image.open('rb') as f:
    mbr=f.read(512); base=struct.unpack_from('<I',mbr,454)[0]*512
    f.seek(base); bpb=f.read(512)
    reserved=struct.unpack_from('<H',bpb,14)[0]
    entries=struct.unpack_from('<H',bpb,17)[0]
    fats=bpb[16]; fat_sectors=struct.unpack_from('<H',bpb,22)[0]
    root_offset=base+(reserved+fats*fat_sectors)*512
    data_offset=root_offset+((entries*32+511)//512)*512
    f.seek(root_offset); directory=f.read(entries*32)
    for n in range(0,len(directory),32):
        if directory[n:n+11]==b'FIP     BIN':
            cluster=struct.unpack_from('<H',directory,n+26)[0]
            fip_offset=data_offset+(cluster-2)*bpb[13]*512
            break
    else: raise RuntimeError('FIP root entry missing')

results=[]
for case,expected in [('crc','param1 CRC'),('secure','secure/encrypted FIP unsupported'),('missing','FAT16 FIP.BIN lookup')]:
    with tempfile.TemporaryDirectory(prefix='sg200x-rom-') as td:
        bad=pathlib.Path(td)/'bad.img'
        # Only the boot partition is needed, with sparse zeros to card capacity.
        with image.open('rb') as src, bad.open('wb') as dst:
            remaining=base+204800*512
            while remaining:
                chunk=src.read(min(remaining,1<<20)); dst.write(chunk); remaining-=len(chunk)
            dst.truncate(1<<30)
        with bad.open('r+b') as f:
            if case=='missing':
                f.seek(root_offset+n); f.write(b'X')
            else:
                f.seek(fip_offset); param=bytearray(f.read(2048))
                if case=='crc':param[12]^=1
                else:
                    param[0xb4]|=0x10
                    struct.pack_into('<I',param,12,0xcafe0000|binascii.crc_hqx(param[16:],0))
                f.seek(fip_offset); f.write(param)
        uart=root/f'rom-{case}-uart.log'
        cmd=[str(qemu),'-M','milkv-duos,boot-mode=rom',
             '-bios',str(rom),'-accel','tcg,thread=single','-display','none',
             '-monitor','none','-serial','file:'+str(uart),'-serial','null','-qmp','stdio','-S',
             '-drive',f'file={bad},if=sd,format=raw,snapshot=on']
        with (root/f'rom-{case}-qmp.log').open('w') as log:
            vm=smoke.QEMU(cmd,log)
            try:
                vm.qmp('cont')
                end=time.monotonic()+5
                while time.monotonic()<end:
                    if uart.exists() and expected in uart.read_text():break
                    time.sleep(.05)
                output=uart.read_text()
                assert 'ROM STOP: '+expected in output and '\nFSBL ' not in output,output
                vm.qmp('quit'); assert vm.proc.wait(timeout=5)==0
            finally:vm.close()
        results.append([case,'PASS']);print(case,'PASS')
(root/'rom-guard-results.json').write_text(json.dumps(results,indent=2))
