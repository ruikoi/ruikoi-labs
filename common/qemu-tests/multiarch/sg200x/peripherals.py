#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Check SG200X peripheral MMIO, controller pending bits and system reset."""
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
   arm = 'arm64' in args.machine
   pending_base = 0x01f01200 if arm else 0x70001000
   def pending(irq):
    return bool(r(pending_base + 4 * (irq // 32)) & (1 << (irq % 32)))
   w(0x01901008,0x30001);assert r(0x01901008)==0x30001
   # Timer7 with interrupts masked: raw bit7 remains set, EOI clears.
   t=0x030a0000+7*0x14
   w(t,25000);w(t+8,7);call('advance-time')
   assert r(0x030a00a8)==128 and r(0x030a00a0)==0
   w(t+8,3);assert r(0x030a00a0)==128
   assert pending(102 if arm else 86)
   assert r(0x030a00a4)==0 and r(0x030a00a8)==0
   call('advance-time');assert r(t+16)==1
   assert r(t+12)==0 and r(0x030a00a8)==0
   w(t,50000);assert r(t)==50000 and 0 < r(t+4) <= 50000
   w(t+8,0);count=r(t+4);call('advance-time');assert r(t+4)==count
   w(t,25000);w(t+8,1);call('advance-time')
   assert r(t+16)==1 and r(t+4)>25000
   w(t+8,0);r(t+12)
   print('timer PASS channel7 mask/raw/EOI/reload/disable/free-running')
   # GPIO output and injected rising-edge input, masking, falling edge.
   g=0x03020000;w(g,0x80);w(g+4,0x80);assert r(g+0x50)==0x80
   w(g+0x38,1);w(g+0x3c,1);w(g+0x30,1)
   call('set_irq_in /machine/soc/gpio0 unnamed-gpio-in 0 1')
   assert r(g+0x44)==1 and r(g+0x40)==1 and r(g+0x50)==0x81
   assert pending(76 if arm else 60)
   w(g+0x34,1);assert r(g+0x40)==0 and r(g+0x44)==1
   w(g+0x4c,1);assert r(g+0x44)==0
   w(g+0x3c,0);w(g+0x34,0);call('set_irq_in /machine/soc/gpio0 unnamed-gpio-in 0 0');assert r(g+0x40)==1
   w(g+0x4c,1);w(g+0x38,0);assert r(g+0x40)==1
   w(g+0x4c,1);assert r(g+0x40)==1
   call('set_irq_in /machine/soc/gpio0 unnamed-gpio-in 0 1');assert r(g+0x40)==0
   print('gpio PASS output/input/edge/level/mask/EOI')
   # SPI 16-bit loopback; RX overflow/underflow, FIFO flush and CS.
   s=0x04180000;w(s,0x80f);w(s+0x2c,0x10);w(s+0x10,1);w(s+8,1)
   w(s+0x60,0xabcd)
   assert pending(70 if arm else 54)
   assert r(s+0x24)==1 and r(s+0x60)==0xabcd
   r(s+0x60);assert r(s+0x34)&4;r(s+0x40);assert not r(s+0x34)&4
   for n in range(9):w(s+0x60,n)
   assert r(s+0x24)==8 and r(s+0x34)&8
   r(s+0x3c);assert not r(s+0x34)&8
   w(s+8,0);assert r(s+0x24)==0
   for n in range(9):w(s+0x60,n)
   assert r(s+0x20)==8 and r(s+0x34)&2
   vm.qmp('system_reset');assert r(s)==7 and r(s+0x20)==0 and r(g)==0 and r(t+8)==0 and r(0x01901008)==0
   for i in range(4):
    w(0x03020000+i*0x1000,1<<i);assert r(0x03020000+i*0x1000)==1<<i
    w(0x04180000+i*0x10000,7);assert r(0x04180000+i*0x10000)==7
   for i in range(8):
    w(0x030a0000+i*0x14,100+i);assert r(0x030a0000+i*0x14)==100+i
   print('spi PASS 16bit loopback RX/TX errors, reset and all instances PASS')
   # Isolate fresh controller state: PLIC pending may remain latched after
   # device EOI/masking until claim/complete. Do not equate it with IRQ level.
   timers = [(0x030a0000 + i * 0x14, (95 if arm else 79) + i)
             for i in range(8)]
   gpios = [(0x03020000 + i * 0x1000, (76 if arm else 60) + i)
            for i in range(4)]
   gpios.append((0x05021000, 86 if arm else 70))
   spis = [(0x04180000 + i * 0x10000, (70 if arm else 54) + i)
           for i in range(4)]
   sources = {**{f'timer{i}': irq for i, (_, irq) in enumerate(timers)},
              **{f'gpio{i}': irq for i, (_, irq) in enumerate(gpios)},
              **{f'spi{i}': irq for i, (_, irq) in enumerate(spis)}}
   masks = {}
   for irq in sources.values():
    word = irq // 32
    masks[word] = masks.get(word, 0) | (1 << (irq % 32))
   def check_pending(expected):
    words = {word: r(pending_base + 4 * word) for word in masks}
    for word, mask in masks.items():
     assert words[word] & mask == (mask if expected else 0), (
         'controller pending', hex(pending_base + 4 * word),
         hex(words[word]), hex(mask), expected)
    return {hex(pending_base + 4 * word): hex(value)
            for word, value in words.items()}
   def check_reset_devices():
    assert r(0x030a00a0) == 0 and r(0x030a00a8) == 0
    for base, _ in timers:
     assert r(base) == 0 and r(base + 8) == 0 and r(base + 16) == 0
    for base, _ in gpios:
     for offset in (0, 4, 0x30, 0x34, 0x38, 0x3c, 0x40, 0x44):
      assert r(base + offset) == 0, ('GPIO reset', hex(base + offset))
    for base, _ in spis:
     assert r(base) == 7 and r(base + 8) == 0
     assert r(base + 0x20) == 0 and r(base + 0x24) == 0
     assert r(base + 0x2c) == 0x3f
     # TX-empty is a reset raw condition, not a pending IRQ when disabled.
     assert r(base + 0x34) == 1
   vm.qmp('system_reset')
   check_reset_devices()
   check_pending(False)
   cycles = []
   for cycle in range(2):
    for base, _ in timers:
     w(base, 25000);w(base + 8, 3)
    for i, (base, _) in enumerate(gpios):
     w(base + 0x38, 1);w(base + 0x3c, 1);w(base + 0x30, 1)
     call(f'set_irq_in /machine/soc/gpio{i} unnamed-gpio-in 0 1')
     assert r(base + 0x40) == 1 and r(base + 0x44) == 1
    for base, _ in spis:
     w(base, 0x80f);w(base + 0x2c, 0x10);w(base + 0x10, 1)
     w(base + 8, 1);w(base + 0x60, 0xabcd)
     assert r(base + 0x24) == 1 and r(base + 0x30) == 0x10
    call('advance-time')
    assert r(0x030a00a0) == 0xff and r(0x030a00a8) == 0xff
    armed = check_pending(True)
    # Leave every source asserted: reset must clear devices and controller.
    vm.qmp('system_reset')
    check_reset_devices()
    reset = check_pending(False)
    call('advance-time')
    check_reset_devices()
    resumed = check_pending(False)
    cycles.append({'cycle': cycle, 'armed': armed,
                   'after_reset': reset, 'after_resume': resumed})
   print('interrupts PASS 8 timers / 5 GPIO / 4 SPI pending, '
         'system reset, resume and rearm')
   (out/'results.json').write_text(json.dumps({
       'machine': args.machine, 'result': 'PASS',
       'interrupt_acceptance': {'controller': 'GICv2' if arm else 'PLIC',
                                'sources': sources, 'cycles': cycles}}, indent=2))
   vm.qmp('quit');vm.proc.wait(timeout=5)
  finally:vm.close()
if args.fip:
 # Let original loader execute beyond banner, while small RTOS remains alive.
 cmd=[str(args.qemu.resolve()),'-M',f'milkv-duos,boot-mode=fip,fip={args.fip.resolve()}','-accel','tcg,thread=single','-display','none','-serial',f'file:{out}/uboot-main.log','-serial',f'file:{out}/rtos.log','-monitor','none','-S','-qmp','stdio','-d','guest_errors,unimp','-D',str(out/'mmio.log')]
 with (out/'uboot-qmp.log').open('w') as log:
  vm=smoke.QEMU(cmd,log)
  try:
   vm.qmp('cont');time.sleep(3);vm.qmp('stop');regs=vm.qmp('human-monitor-command',**{'command-line':'info registers','cpu-index':0});(out/'uboot-regs.log').write_text(regs)
   vm.qmp('quit');vm.proc.wait(timeout=5)
  finally:vm.close()
 print((out/'uboot-main.log').read_text(errors='replace')[-1800:])
