#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""RTC/SD PHY/DMA/I2C/USB/thermal/watchdog initialization acceptance."""
import argparse,importlib.util,pathlib,tempfile,socket,time,json
parser=argparse.ArgumentParser()
parser.add_argument('--qemu',required=True,type=pathlib.Path)
parser.add_argument('--output',required=True,type=pathlib.Path)
parser.add_argument('--machine',default='milkv-duos')
parser.add_argument('--fip',type=pathlib.Path)
args=parser.parse_args()
repo=pathlib.Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('smoke',repo/'hetero/hetero-smoke.py');smoke=importlib.util.module_from_spec(spec);spec.loader.exec_module(smoke)
out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='sgp-') as tmp:
 path=pathlib.Path(tmp)/'test.sock'
 payload=pathlib.Path(tmp)/'idle.bin';payload.write_bytes(bytes.fromhex('7f2003d5ffffff17' if 'arm64' in args.machine else '730050106ff0dfff'))
 cmd=[str(args.qemu.resolve()),'-M',args.machine,'-accel','tcg,thread=single','-bios','none','-display','none','-serial','null','-serial','null','-monitor','none','-S','-qmp','stdio','-qtest',f'unix:{path},server=on,wait=off','-device',f'loader,file={payload},addr=0x0c000000,cpu-num=0,force-raw=on']
 with (out/'qmp.log').open('w') as log:
  vm=smoke.QEMU(cmd,log)
  try:
   q=socket.socket(socket.AF_UNIX);q.connect(str(path));f=q.makefile('rwb',buffering=0)
   def call(c):
    if c=='advance-time':
     vm.qmp('cont');time.sleep(.02);vm.qmp('stop');return 'OK'
    f.write((c+'\n').encode());r=f.readline().decode().strip();assert r.startswith('OK'),(c,r);return r
   def w(a,v):call(f'writel {a:#x} {v:#x}')
   def r(a):return int(call(f'readl {a:#x}').split()[1],16)
   rtc=0x05026000
   assert r(rtc+0xd0)==7 and r(rtc+0xd4)==3
   w(rtc+0xd0,0xffffffff);assert r(rtc+0xd0)==7
   w(rtc+0xd0,2);assert r(rtc+0xd0)==2
   for base in [0x04300000,0x04310000,0x04320000]:
    assert r(base+0x24c)==1
    w(base+0x240,0x3456);assert r(base+0x240)==0x3456
    w(base+0x248,0xffffffff);assert r(base+0x248)==0
   assert r(0x05025008)&0x400
   w(rtc+0x10,1700000000);w(rtc+0x14,1)
   assert r(rtc+0x18)==1700000000 and r(rtc+0x4a8)==1700000000
   w(rtc+0x4a8,0);assert r(rtc+0x4a8)==1700000000
   d=0x04330000
   call(f'writeq {d+0x10:#x} 3');assert r(d+0x10)==3
   call(f'writeq {d+0x58:#x} 1');assert r(d+0x10)==0 and r(d+0x58)==0
   assert r(d+8)==0x3130312a and r(d+0x18)==0
   for i in range(5):
    assert r(0x040000fc+i*0x10000)==0x44570140
   w(0x05021000,0x20);w(0x05021004,0x20);assert r(0x05021050)==0x20
   assert r(0x04340040)>>16==0x4f54  # DWC2 component identity
   assert r(0x030e0020)&0x1fff==852
   w(0x030e0020,0);assert r(0x030e0020)&0x1fff==852
   w(0x04108000,0x7654);assert r(0x04108000)==0x7654
   w(0x03001034,0xa1b2c3d4);call("writeb 0x03001035 0x55")
   assert r(0x03001034)==0xa1b255d4
   assert int(call("readb 0x03001036").split()[1],16)==0xb2
   wd=0x03010000
   assert r(wd)==0 and r(wd+0xf4)&64
   w(wd+4,0x66);w(wd,3);assert r(wd+8)>0
   vm.qmp('cont');time.sleep(.20);vm.qmp('stop')
   assert r(wd+0x10)==1 and r(0x70001004)&(1<<26)
   r(wd+0x14);assert r(wd+0x10)==0
   w(wd+0xc,0x76);assert r(wd+8)>0
   w(wd,0);assert r(wd)&1  # Cannot disable until reset.
   vm.qmp('system_reset');assert r(wd)==0 and r(rtc+0xd0)==7
   assert r(0x04108000)==0 and r(0x05021000)==0
   w(wd+4,0);w(wd,1);vm.qmp('cont');time.sleep(.02);vm.qmp('stop')
   assert r(rtc+0x18)>=1700000000  # Warm reset preserves RTC seconds.
   assert r(wd)==0  # Real watchdog expiration requested a system reset.
   print('PASS RTC, SD PHY, DMA reset64, five I2C, GPIOE, USB, thermal, watchdog IRQ/kick/reset')
   (out/'results.json').write_text(json.dumps({'result':'PASS'}))
   vm.qmp('quit');assert vm.proc.wait(timeout=5)==0
  finally:vm.close()
