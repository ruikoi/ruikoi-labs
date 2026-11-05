#!/usr/bin/env python3
"""Boot two Linux instances and verify independent interactive UART shells."""
import argparse, hashlib, json, pathlib, selectors, socket, subprocess, tempfile, time
ap=argparse.ArgumentParser()
ap.add_argument('--qemu',required=True)
ap.add_argument('--images',required=True)
ap.add_argument('--logs',required=True)
ap.add_argument('--timeout',type=int,default=90)
ap.add_argument('--display',choices=['none','cocoa'],default='none',help='Cocoa requires a qemu-d9m build')
ap.add_argument('--hold-seconds',type=int,default=0,help='Keep the verified VM visible before exiting')
ap.add_argument('--thread',choices=['single','multi'],default='multi')
ap.add_argument('--main-only',action='store_true')
ap.add_argument('--main-isa',choices=['arm64','riscv64'],default='arm64')
ap.add_argument('--machine-prefix',choices=['hetero','multiarch'],default='multiarch')
args=ap.parse_args()
if args.hold_seconds < 0:ap.error('--hold-seconds must be nonnegative')
if args.display == 'cocoa' and args.main_only:ap.error('Cocoa demo requires both serial consoles')
images=pathlib.Path(args.images)
logs=pathlib.Path(args.logs);logs.mkdir(parents=True,exist_ok=True)
(logs/'result.json').unlink(missing_ok=True)
with tempfile.TemporaryDirectory(prefix='dual-linux-',dir='/tmp') as td:
 paths=[pathlib.Path(td)/'main.sock',pathlib.Path(td)/'mcu.sock']
 if args.main_only:paths=paths[:1]
 cmd=[args.qemu,'-M',((args.machine_prefix+'-virt-arm' if args.main_isa=='arm64' else args.machine_prefix+'-virt-riscv') + ',mcu=rv32-mcu,mcu-ram=64M' + ('' if args.main_only else f',mcu-kernel={images}/rv32/Image')),
      '-accel','tcg,thread='+args.thread,'-m','256M','-display',('cocoa,split=on' if args.display=='cocoa' else 'none'),'-monitor','none','-nic','none',
      '-kernel',str(images/args.main_isa/'Image'),'-initrd',str(images/args.main_isa/'rootfs.cpio.gz'),
      '-append',('console=ttyAMA0' if args.main_isa=='arm64' else 'console=ttyS0')+' rdinit=/init panic=0']
 if args.main_isa=='riscv64':cmd+=['-bios',str(images/'riscv64/fw_dynamic.bin')]
 if args.display=='cocoa':cmd+=['-name',('ARM64 Linux + RV32 no-MMU Linux' if args.main_isa=='arm64' else 'RV64 Linux + RV32 no-MMU Linux')+' / MTTCG']
 for i,path in enumerate(paths):
  if args.display == 'cocoa':
   cmd+=['-chardev',f'vc,id=view{i},cols=80,rows=30',
         '-chardev',f'socket,id=control{i},path={path},server=on,wait=off',
         '-chardev',f'hub,id=uart{i},chardevs.0=view{i},chardevs.1=control{i}']
  else:
   cmd+=['-chardev',f'socket,id=uart{i},path={path},server=on,wait=off']
  cmd+=['-serial',f'chardev:uart{i}']
 (logs/'command.txt').write_text('\n'.join(cmd)+'\n')
 with (logs/'qemu.log').open('wb') as err:
  p=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=err,stderr=err)
  socks=[];sel=selectors.DefaultSelector();data=[bytearray() for _ in paths];sent=[False for _ in paths];logged=[False for _ in paths]
  deadline=time.monotonic()+args.timeout
  verification_sent=False
  expected=['aarch64' if args.main_isa=='arm64' else 'riscv64','riscv32'][:len(paths)]
  tokens=['MAIN_PRIVATE','MCU_PRIVATE'][:len(paths)]
  try:
   for i,path in enumerate(paths):
    while not path.exists():
     if p.poll() is not None:raise RuntimeError((logs/'qemu.log').read_text())
     if time.monotonic()>deadline:raise TimeoutError('UART socket not created')
     time.sleep(.05)
    s=socket.socket(socket.AF_UNIX);s.connect(str(path));s.setblocking(False);socks.append(s);sel.register(s,selectors.EVENT_READ,i)
   while time.monotonic()<deadline:
    for key,_ in sel.select(.2):
     i=key.data;chunk=key.fileobj.recv(65536)
     if not chunk:raise RuntimeError('UART disconnected')
     data[i]+=chunk
     (logs/('main.uart.log' if i==0 else 'mcu.uart.log')).write_bytes(data[i])
     tail=bytes(data[i][-4096:])
     if b'login:' in tail and not logged[i]:key.fileobj.sendall(b'root\n');logged[i]=True
     if logged[i] and (b'# ' in tail or b'#\r' in tail) and not sent[i]:
      marker=b'MAIN_SHELL_OK' if i==0 else b'MCU_SHELL_OK'
      key.fileobj.sendall(b"uname -m; echo "+tokens[i].encode()+b" >/tmp/domain-proof; sleep 1; printf '\\n"+marker+b"\\n'\n")
      sent[i]=True
    shell_ready=all(sent) and b'\nMAIN_SHELL_OK\r\n' in data[0] and (args.main_only or b'\nMCU_SHELL_OK\r\n' in data[1])
    if shell_ready and not verification_sent:
     # Verify after BOTH domains wrote the same path, so a shared rootfs fails.
     for i,s in enumerate(socks):
      label='ARM64 / Linux (MMU)' if i==0 and args.main_isa=='arm64' else ('RV64 / Linux (MMU)' if i==0 else 'RV32 / Linux (no-MMU)')
      s.sendall(("printf '\\033[2J\\033[H'; printf '\\n"+label+"\\n'; uname -m; uname -r; head -1 /proc/meminfo; "
                 "printf 'private file: '; cat /tmp/domain-proof; printf 'uptime: '; cat /proc/uptime; "
                 "sleep 1; printf '\\nCHECK_COMPLETE_%d\\n'\n" % i).encode())
     verification_sent=True
    if verification_sent and all(('\nCHECK_COMPLETE_%d\r\n'%i).encode() in data[i] for i in range(len(paths))):
     for i,isa in enumerate(expected):
      assert ('\r\n'+isa+'\r\n').encode() in data[i], 'ISA mismatch on UART %d'%i
      assert ('private file: '+tokens[i]+'\r\n').encode() in data[i], 'private rootfs mismatch'
     result={'status':'PASS','qemu_pid':p.pid,'display':args.display,'thread':args.thread,
             'architectures':expected,
             'binary_sha256':hashlib.sha256(pathlib.Path(args.qemu).read_bytes()).hexdigest(),
             'qemu_version':subprocess.check_output([args.qemu,'--version'],text=True).splitlines()[0],
             'checks':['independent UART shells','exact uname architecture','private files at the same path','timed commands']}
     (logs/'result.json').write_text(json.dumps(result,indent=2)+'\n')
     print(f'PASS: {args.main_isa} MMU Linux' + ('' if args.main_only else ' + RV32 no-MMU Linux') + ' UART shells, exact ISA, private rootfs and timed commands',flush=True)
     if args.hold_seconds:
      print('Verified VM PID:',p.pid,'holding for',args.hold_seconds,'seconds',flush=True)
      until=time.monotonic()+args.hold_seconds
      while time.monotonic()<until and p.poll() is None:
       # Keep draining UARTs while the GUI remains interactive.
       for key,_ in sel.select(.2):
        i=key.data;chunk=key.fileobj.recv(65536)
        if not chunk:raise RuntimeError('UART disconnected during GUI inspection')
        data[i]+=chunk
        (logs/('main.uart.log' if i==0 else 'mcu.uart.log')).write_bytes(data[i])
     break
   else:raise TimeoutError('Two Linux shell markers not received; inspect UART logs')
  except KeyboardInterrupt:
   if not (logs/'result.json').exists():raise
  finally:
   for s in socks:s.close()
   sel.close()
   if p.poll() is None:p.terminate()
   p.wait(timeout=5)
