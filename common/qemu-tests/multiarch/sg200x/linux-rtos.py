#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
import json, pathlib, socket, subprocess, time, re
import argparse
parser = argparse.ArgumentParser(description='SG200X Linux + FreeRTOS UART acceptance')
parser.add_argument('--qemu', required=True, type=pathlib.Path)
parser.add_argument('--images', required=True, type=pathlib.Path)
parser.add_argument('--rtos', required=True, type=pathlib.Path)
parser.add_argument('--output', required=True, type=pathlib.Path)
parser.add_argument('--reset-stress', action='store_true', help='reset both guests and repeat twenty serial commands')
parser.add_argument('--thread', choices=['single', 'multi'], default='single')
args = parser.parse_args()
OUT = args.output.resolve()
OUT.mkdir(parents=True, exist_ok=True)
QEMU = str(args.qemu.resolve())
IMAGES = args.images.resolve()
RTOS = args.rtos.resolve()
def connect(path,p):
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        if p.poll() is not None: raise RuntimeError('QEMU exited; see stderr')
        try:
            s=socket.socket(socket.AF_UNIX);s.connect(str(path));s.settimeout(.2);return s
        except (OSError,ConnectionError):s.close();time.sleep(.05)
    raise TimeoutError(path)
def read_until(s, token, timeout=60):
    data=b'';deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            b=s.recv(65536)
            if not b: raise RuntimeError('serial closed')
            data+=b
            if token.encode() in data:return data.decode(errors='replace')
            if b'Kernel panic' in data:raise RuntimeError(data.decode(errors='replace')[-2000:])
        except socket.timeout:pass
    raise TimeoutError(f'expected {token}: {data[-1000:]}')
def qmp_command(stream, command):
    stream.write((json.dumps({'execute': command})+'\n').encode())
    stream.flush()
    while True:
        response=json.loads(stream.readline())
        if 'error' in response: raise RuntimeError(response)
        if 'return' in response: return response['return']
results=[]
for board,isa in [('milkv-duo','riscv64'),('milkv-duo256m','riscv64'),('milkv-duo256m','arm64'),('milkv-duos','riscv64'),('milkv-duos','arm64')]:
    name=f'{board}-{isa}';paths=[pathlib.Path('/tmp')/('sg200x-'+name+f'-{i}.sock') for i in range(2)]
    for path in paths:path.unlink(missing_ok=True)
    cmd=[QEMU,'-machine',f'{board},main-isa={isa},rtos={RTOS}','-accel',f'tcg,thread={args.thread}','-display','none','-monitor','none','-kernel',str(IMAGES/isa/'Image'),'-initrd',str(IMAGES/isa/'rootfs.cpio.gz'),'-append','console=ttyS0,115200 earlycon=uart8250,mmio32,0x04140000 rdinit=/bin/sh panic=-1']
    for path in paths:cmd+=['-serial',f'unix:{path},server=on,wait=off']
    if isa=='riscv64':cmd+=['-bios',str(IMAGES/isa/'fw_dynamic.bin')]
    if board=='milkv-duo':
        cmd[cmd.index('-kernel')+1]=str(IMAGES/'sg200x/duo-linux/Image')
    qmp_path=pathlib.Path('/tmp')/('sg200x-'+name+'-qmp.sock')
    qmp_path.unlink(missing_ok=True)
    cmd+=['-S','-qmp',f'unix:{qmp_path},server=on,wait=off']
    (OUT/(name+'-command.json')).write_text(json.dumps(cmd,ensure_ascii=False,indent=2))
    with (OUT/(name+'-stderr.log')).open('wb') as f:
        p=subprocess.Popen(cmd,stdout=f,stderr=f);socks=[];qmp=None;stream=None
        try:
            socks=[connect(path,p) for path in paths]
            # Connect both UARTs before either guest can print its first prompt.
            qmp=connect(qmp_path,p)
            qmp.settimeout(15)
            stream=qmp.makefile('rwb')
            json.loads(stream.readline())
            qmp_command(stream,'qmp_capabilities')
            qmp_command(stream,'cont')
            linux=read_until(socks[0],'~ # ')
            rtos=read_until(socks[1],'rtos> ')
            socks[0].sendall(b'mount -t proc proc /proc; uname -a; uname -m; cat /proc/interrupts; echo SG200X_LINUX_OK\n')
            linux+=read_until(socks[0],'SG200X_LINUX_OK\r\n')
            socks[1].sendall(b'uname\r')
            rtos+=read_until(socks[1],'rtos> ')
            socks[1].sendall(b'ticks\r')
            rtos+=read_until(socks[1],'rtos> ')
            time.sleep(.3)
            socks[1].sendall(b'ticks\r')
            rtos+=read_until(socks[1],'rtos> ')
            if args.reset_stress:
                qmp_command(stream,'system_reset')
                linux+='\n=== SYSTEM RESET ===\n'+read_until(socks[0],'~ # ')
                rtos+='\n=== SYSTEM RESET ===\n'+read_until(socks[1],'rtos> ')
                socks[0].sendall(b'mount -t proc proc /proc; uname -m; cat /proc/interrupts\n')
                linux+=read_until(socks[0],'~ # ')
                for iteration in range(20):
                    socks[0].sendall(b'uname -m\n')
                    linux+=read_until(socks[0],'~ # ')
                    socks[1].sendall(b'uname\r')
                    rtos+=read_until(socks[1],'rtos> ')
                    socks[1].sendall(b'ticks\r')
                    rtos+=read_until(socks[1],'rtos> ')
                reset_linux=linux.split('=== SYSTEM RESET ===')[1]
                reset_rtos=rtos.split('=== SYSTEM RESET ===')[1]
                assert reset_linux.count('aarch64' if isa=='arm64' else 'riscv64')>=20
                assert reset_rtos.count('XLEN=64')==20
                reset_vals=re.findall(r'ticks=(\d+) heartbeat=(\d+) uart_irqs=(\d+)',reset_rtos)
                assert len(reset_vals)==20 and int(reset_vals[-1][2])>int(reset_vals[0][2])
                assert int(reset_vals[-1][0])>=int(reset_vals[0][0])
                print(name,'PASS system_reset + 20 dual-shell iterations',flush=True)
            (OUT/(name+'-linux.log')).write_text(linux)
            (OUT/(name+'-rtos.log')).write_text(rtos)
            vals=re.findall(r'ticks=(\d+) heartbeat=(\d+) uart_irqs=(\d+)',rtos)
            assert len(vals)>=2 and int(vals[1][0])>int(vals[0][0]) and int(vals[1][1])>int(vals[0][1]) and int(vals[1][2])>0,rtos
            assert ('aarch64' if isa=='arm64' else 'riscv64') in linux
            assert 'XLEN=64' in rtos and 'hart=0' in rtos
            print(name,'PASS',vals[:2],flush=True);results.append((name,'PASS'))
        except Exception as e:
            print(name,'FAIL',e,flush=True);results.append((name,'FAIL',str(e)))
        finally:
            if stream:stream.close()
            if qmp:qmp.close()
            qmp_path.unlink(missing_ok=True)
            for s in socks:s.close()
            p.terminate()
            try:p.wait(timeout=5)
            except subprocess.TimeoutExpired:p.kill();p.wait()
            for path in paths:path.unlink(missing_ok=True)
(OUT/'results.json').write_text(json.dumps(results,indent=2))
assert all(row[1]=='PASS' for row in results),results
