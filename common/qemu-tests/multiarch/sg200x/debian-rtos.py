#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Existing Debian SD/FIP + FreeRTOS acceptance; disk uses a temporary snapshot.

The vendor kernel advertises RVV 0.7 in its DT. The local C906 model implements
scalar instructions only, so this test adjusts the loaded DT in U-Boot RAM.
No kernel, rootfs or SD image is edited.
"""
import argparse
import importlib.util
import json
import pathlib
import re
import select
import socket
import tempfile
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--qemu', required=True, type=pathlib.Path)
parser.add_argument('--sd', required=True, type=pathlib.Path)
parser.add_argument('--fip', required=True, type=pathlib.Path)
parser.add_argument('--output', required=True, type=pathlib.Path)
parser.add_argument('--reset', action='store_true')
parser.add_argument('--rtos', type=pathlib.Path)
parser.add_argument('--thread', choices=['single', 'multi'], default='single')
parser.add_argument('--kernel', default='/vmlinuz-5.10.4-20240527-2+')
parser.add_argument('--dtb', default='/fdt/linux-image-duos-5.10.4-20240527-2+/cv181x_milkv_duos_sd.dtb')
args = parser.parse_args()
repo = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('smoke', repo / 'hetero/hetero-smoke.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=True)
logs = [bytearray(), bytearray()]
results = []
with tempfile.TemporaryDirectory(prefix='sgdebian-') as tmp:
    paths = [pathlib.Path(tmp) / f'u{i}.sock' for i in range(2)]
    cmd = [str(args.qemu.resolve()), '-M', f'milkv-duos,boot-mode=fip,fip={args.fip.resolve()}' + (f',rtos={args.rtos.resolve()}' if args.rtos else ''),
           '-accel', f'tcg,thread={args.thread}', '-display', 'none', '-monitor', 'none',
           '-drive', f'file={args.sd.resolve()},if=sd,format=raw,snapshot=on',
           '-qmp', 'stdio', '-S', '-d', 'guest_errors,unimp', '-D', str(out / 'mmio.log')]
    for path in paths:
        cmd += ['-serial', f'unix:{path},server=on,wait=off']
    (out / 'command.json').write_text(json.dumps(cmd, indent=2))
    with (out / 'qmp.log').open('w') as log:
        vm = smoke.QEMU(cmd, log)
        (out / 'qemu.pid').write_text(str(vm.proc.pid) + '\n')
        consoles = []
        try:
            for path in paths:
                s = socket.socket(socket.AF_UNIX)
                s.connect(str(path))
                s.setblocking(False)
                consoles.append(s)

            def receive(marker=None, timeout=5, channel=0):
                data = bytearray()
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    ready = select.select(consoles, [], [], min(.1, max(0, deadline - time.monotonic())))[0]
                    for s in ready:
                        chunk = s.recv(65536)
                        if not chunk:
                            raise RuntimeError('serial closed')
                        i = consoles.index(s)
                        logs[i] += chunk
                        (out / ('main.log' if i == 0 else 'small.log')).write_bytes(logs[i])
                        if i == channel:
                            data += chunk
                    if marker and marker in data:
                        return bytes(data)
                if marker:
                    raise TimeoutError(f'UART{channel} expected {marker!r}: {bytes(data)[-2000:]!r}')
                return bytes(data)

            def uboot(command):
                consoles[0].sendall(command.encode() + b'\r')
                return receive(b'cv181x_c906# ', 20)

            for cycle in range(2 if args.reset else 1):
                vm.qmp('cont')
                boot = receive(b'Hit any key', 20)
                assert b'OpenSBI' in boot and b'U-Boot' in boot, boot
                print(f'BOOT cycle {cycle}: OpenSBI -> U-Boot ({args.thread})', flush=True)
                consoles[0].sendall(b' ')
                receive(b'cv181x_c906# ', 5)
                uboot('mmc dev 0')
                assert b'bytes read' in uboot(f'fatload mmc 0:1 0x80200000 {args.kernel}')
                assert b'bytes read' in uboot(f'fatload mmc 0:1 0x81200000 {args.dtb}')
                uboot('fdt addr 0x81200000')
                uboot('fdt set /cpus/cpu@0 riscv,isa rv64imafdc')
                uboot('setenv bootargs console=ttyS0,115200 earlycon=sbi root=/dev/mmcblk0p2 rootwait rw')
                consoles[0].sendall(b'booti 0x80200000 - 0x81200000\r')
                receive(b'login:', 240)
                print(f'BOOT cycle {cycle}: Debian login reached', flush=True)
                consoles[0].sendall(b'root\r')
                receive(b'Password:', 20)
                consoles[0].sendall(b'rv\r')
                receive(b'# ', 120)
                consoles[0].sendall(b"uname -a; uname -m; cat /etc/os-release; cat /proc/cpuinfo; cat /proc/interrupts; hwclock -r; printf '\\nDEBIAN_%s\\n' OK\r")
                main = receive(b'\r\nDEBIAN_OK\r\n', 30)
                assert b'riscv64' in main and b'Debian GNU/Linux' in main
                assert b'Oops' not in logs[0] and b'Kernel panic' not in logs[0]
                receive(timeout=.2)
                consoles[1].sendall(b'uname\r')
                small = receive(b'rtos> ', 10, channel=1)
                assert b'FreeRTOS' in small and b'XLEN=64' in small
                samples = []
                for _ in range(3):
                    consoles[1].sendall(b'ticks\r')
                    data = receive(b'rtos> ', 10, channel=1)
                    sample = re.search(rb'ticks=(\d+) heartbeat=(\d+) uart_irqs=(\d+)', data)
                    assert sample, data
                    samples.append(tuple(int(n) for n in sample.groups()))
                    receive(timeout=.4)
                assert all(b > a for a, b in zip(samples[0], samples[-1])), samples
                consoles[0].sendall(b"printf '\\nDEBIAN_AFTER_RTOS_OK\\n'; cat /proc/uptime\r")
                receive(b'\r\nDEBIAN_AFTER_RTOS_OK\r\n', 20)
                cpus = vm.qmp('query-cpus-fast')
                assert len(cpus) == 2, cpus
                (out / f'cpus-cycle{cycle}.json').write_text(json.dumps(cpus, indent=2))
                results.append({'cycle': cycle, 'thread': args.thread,
                                'boot_chain': ['OpenSBI', 'U-Boot', 'Debian'],
                                'Debian': 'riscv64 shell',
                                'FreeRTOS': 'XLEN=64 shell', 'samples': samples,
                                'cpu_count': len(cpus)})
                (out / 'results.json').write_text(json.dumps(results, indent=2))
                print(f'PASS cycle {cycle}: Debian riscv64 + FreeRTOS, {samples}', flush=True)
                if args.reset and cycle == 0:
                    vm.qmp('stop')
                    for i, name in enumerate(['main', 'small']):
                        (out / f'{name}-before-reset.log').write_bytes(logs[i])
                        logs[i].clear()
                    vm.qmp('system_reset')
            vm.qmp('quit')
            assert vm.proc.wait(timeout=5) == 0
        finally:
            for i, name in enumerate(['main', 'small']):
                (out / f'{name}.log').write_bytes(logs[i])
            for s in consoles:
                s.close()
            vm.close()
