#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""SG200X EPHY initialization and SPI EEPROM command-level acceptance."""
import argparse, importlib.util, json, pathlib, socket, tempfile
parser=argparse.ArgumentParser()
parser.add_argument('--qemu',required=True,type=pathlib.Path)
parser.add_argument('--output',required=True,type=pathlib.Path)
args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
spec=importlib.util.spec_from_file_location('smoke',pathlib.Path(__file__).parents[2]/'hetero/hetero-smoke.py')
smoke=importlib.util.module_from_spec(spec);spec.loader.exec_module(smoke)
with tempfile.TemporaryDirectory(prefix='sgeep-') as tmp:
 path=pathlib.Path(tmp)/'test.sock'
 image=pathlib.Path(tmp)/'seed.bin';image.write_bytes(bytes([0x91,0x82,0x73]))
 cmd=[str(args.qemu.resolve()),'-M','milkv-duos','-accel','tcg,thread=single','-bios','none','-display','none','-monitor','none','-serial','null','-serial','null','-S','-qmp','stdio','-qtest',f'unix:{path},server=on,wait=off','-device','sg200x-spi-eeprom,bus=sg200x-spi0','-device',f'sg200x-spi-eeprom,bus=sg200x-spi1,image={image}']
 with (out/'qmp.log').open('w') as log:
  vm=smoke.QEMU(cmd,log)
  try:
   q=socket.socket(socket.AF_UNIX);q.connect(str(path));f=q.makefile('rwb',buffering=0)
   def call(c):
    f.write((c+'\n').encode());r=f.readline().decode().strip();assert r.startswith('OK'),(c,r);return r
   def w(a,v):call(f'writel {a:#x} {v:#x}')
   def r(a):return int(call(f'readl {a:#x}').split()[1],16)
   e=0x03009000
   w(e+0x804,1)
   for ctrl in [0x0900,0x0904,0x0906]:w(e+0x800,ctrl);assert r(e+0x800)==ctrl
   w(e+0x7c,0x500);w(e+0x40,0xc7e);w(e+8,0x43);w(e+12,0x5649)
   assert r(e+0x48)&0x8000 and r(e+8)==0x43 and r(e+12)==0x5649
   w(e+0x7c,0);assert r(e+8)==0x43
   w(e+0x7c,0x500);assert r(e+8)==0x43
   w(e+0x804,0);assert r(e+0x804)==0
   w(0x030010e0,5);w(0x050270b0,0x11111111)
   assert r(0x030010e0)==5 and r(0x050270b0)==0x11111111
   # Same register bank is accessible through Clause 22 GMAC MDIO.
   m=0x04070000
   w(e+0x7c,0)
   def mdio(reg,data=None,phy=0):
    if data is not None:w(m+0x14,data)
    w(m+0x10,(phy<<11)|(reg<<6)|1|(2 if data is not None else 0))
    assert r(m+0x10)&1==0
    return r(m+0x14)&65535
   assert mdio(2)==0x43 and mdio(3)==0x5649
   assert mdio(1)&4==0 and mdio(2,phy=1)==65535
   mdio(0,0x8000);assert mdio(0)&0x8000==0
   mdio(31,0x500);mdio(0,0x8000);assert mdio(0)&0x8000==0
   mdio(16,0xc7e);assert r(e+0x40)==0xc7e
   assert r(0x03050120)==0 and r(0x03050124)==0
   w(m+0x1000,1);assert r(m+0x1000)&1==0
   print('EPHY PASS SDK init, page isolation, PLL-ready, pinmux')
   def transfer(data,count=0,bank=0):
    s=0x04180000+bank*0x10000
    w(s+8,0);w(s+0x10,0);w(s,0x307 if count else 0x107)
    if count:w(s+4,count-1)
    w(s+8,1)
    if count:
     for v in data:w(s+0x60,v)
     w(s+0x10,1)
     result=[r(s+0x60) for _ in range(count)]
     assert r(s+0x24)==0 and r(s+0x28)&1==0
    else:
     w(s+0x10,1)
     for v in data:w(s+0x60,v)
     result=[]
    w(s+0x10,0);w(s+8,0)
    return result
   def read(address,count,bank=0):return transfer([3,address>>8,address&255],count,bank)
   assert read(0,16)==[255]*16
   assert read(0,4,1)==[0x91,0x82,0x73,255]
   transfer([2,0,0,0x66]);assert read(0,1)==[255]
   transfer([6]);assert transfer([5],1)==[2]
   transfer([2,0,62,0x11,0x22,0x33,0x44])
   assert read(62,2)==[0x11,0x22] and read(0,2)==[0x33,0x44] and read(64,1)==[255]
   assert transfer([5],1)==[0]
   # More than one RX FIFO: the EEPROM-read mode must refill without re-command.
   transfer([6]);transfer([2,0,128]+list(range(64)))
   assert read(128,64)==list(range(64))
   # Sequential read wraps the array end, while writes wrap their own 64-byte page.
   transfer([6]);transfer([2,0x7f,0xff,0x88])
   assert read(0x7fff,3)==[0x88,0x33,0x44]
   transfer([6]);transfer([1,12]);transfer([6]);transfer([2,0,0,0x99]);assert read(0,1)==[0x33]
   transfer([6]);transfer([1,0]);transfer([6]);transfer([4]);assert transfer([5],1)==[0]
   # Standard full-duplex SPI read reaches the same actual EEPROM.
   s=0x04180000;w(s+8,0);w(s,7);w(s+0x10,1);w(s+8,1)
   answer=[]
   for v in [3,0,0,0,0]:
    w(s+0x60,v);answer.append(r(s+0x60))
   assert answer[3:]==[0x33,0x44]
   w(s+0x10,0);w(s+8,0)
   # Receive-only NDF accounting without an attached slave.
   s=0x041a0000;w(s+8,0);w(s,0x207);w(s+4,19);w(s+0x10,1);w(s+8,1)
   assert [r(s+0x60) for _ in range(20)]==[0]*20
   assert r(s+0x24)==0 and r(s+0x28)&1==0
   w(s+8,0)
   vm.qmp('system_reset')
   assert read(128,64)==list(range(64)) and read(0,2)==[0x33,0x44]
   assert r(e+0x800)==0 and r(e+8)==0x43 and r(0x030010e0)==0
   print('EEPROM PASS WREN/WRDI/status/protect/page-wrap/array-wrap/read64/reset/image')
   (out/'results.json').write_text(json.dumps({'ephy':'PASS','spi-eeprom':'PASS'}))
   vm.qmp('quit');assert vm.proc.wait(timeout=5)==0
  finally:vm.close()
