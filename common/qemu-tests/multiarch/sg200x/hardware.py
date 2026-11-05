#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""SG200X reset release and private CLINT acceptance, without guest images."""
import argparse
import importlib.util
import pathlib
import re
import struct
import subprocess
import sys
import tempfile
import time

spec = importlib.util.spec_from_file_location(
    'smoke', pathlib.Path(__file__).parents[2] / 'hetero/hetero-smoke.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def program(small):
    words = []

    def li(rd, value):
        upper = (value + 0x800) >> 12
        words.append((upper << 12 | rd << 7 | 0x37) & 0xffffffff)
        words.append(((value - (upper << 12)) & 0xfff) << 20 |
                     rd << 15 | rd << 7 | 0x13)

    def store(address, value):
        li(5, address)
        li(6, value)
        words.append(0x2023 | 5 << 15 | 6 << 20)

    if small:
        store(0x74000000, 1)
        store(0x0c004004, 0x5678)
    else:
        store(0x0c004000, 0x1234)
        store(0x03003024, 0)
        store(0x020b0004, 1 << 13)
        store(0x020b0020, 0x0c001000)
        store(0x020b0024, 0)
        store(0x03003024, 1 << 6)
    store(0x05200004 if small else 0x05200000, 0x5678 if small else 0x1234)
    store(0x0e000004 if small else 0x0e000000, 0x5678 if small else 0x1234)
    words += [0x10500073, 0xffdff06f]  # WFI; J -4
    return struct.pack('<' + 'I' * len(words), *words)


def run(binary, output, thread="single"):
    output.mkdir(parents=True, exist_ok=True)
    for board in ['milkv-duo', 'milkv-duo256m', 'milkv-duos']:
        with tempfile.TemporaryDirectory(prefix='sg200x-hardware-') as tmpdir:
            tmp = pathlib.Path(tmpdir)
            cmd = [str(binary), '-M', board, '-accel', f'tcg,thread={thread}',
                   '-bios', 'none', '-display', 'none', '-monitor', 'none',
                   '-serial', 'null', '-serial', 'null', '-qmp', 'stdio', '-S']
            for index, small in enumerate([False, True]):
                image = tmp / f'cpu{index}.bin'
                image.write_bytes(program(small))
                # Load the small image without assigning PC: the primary
                # must release it through the actual reset registers.
                addr = 0x0c001000 if small else 0x0c000000
                loader = f'loader,file={image},addr={addr},force-raw=on'
                if not small:
                    loader += ',cpu-num=0'
                cmd += ['-device', loader]
            with (output / f'{board}-hardware.log').open('w') as log:
                vm = smoke.QEMU(cmd, log)
                try:
                    cpus = vm.qmp('query-cpus-fast')
                    assert [c['target'] for c in cpus] == ['riscv64'] * 2
                    if sys.platform != 'darwin':
                        same_thread = cpus[0]['thread-id'] == cpus[1]['thread-id']
                        assert same_thread == (thread == 'single')
                    vm.qmp('cont')
                    dump = tmp / 'markers.bin'
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        vm.qmp('pmemsave', val=0x0c004000, size=8,
                               filename=str(dump))
                        markers = struct.unpack('<II', dump.read_bytes())
                        if markers == (0x1234, 0x5678):
                            break
                        time.sleep(.02)
                    assert markers == (0x1234, 0x5678), markers
                    # Markers are written before the new SRAM checks.
                    time.sleep(.05)
                    vm.qmp('stop')
                    shadow = 0x3c004000 if board == 'milkv-duo' else 0x2c004000
                    for address in [shadow, 0x05200000, 0x0e000000]:
                        vm.qmp('pmemsave', val=address, size=8,
                               filename=str(dump))
                        assert struct.unpack('<II', dump.read_bytes()) == (0x1234, 0x5678)
                    for index in range(2):
                        regs = vm.qmp('human-monitor-command',
                                      **{'command-line': 'info registers',
                                         'cpu-index': index})
                        log.write(regs)
                        mip = int(re.search(r'\bmip\s+([0-9a-f]+)',
                                            regs).group(1), 16)
                        hart = int(re.search(r'\bmhartid\s+([0-9a-f]+)',
                                             regs).group(1), 16)
                        assert hart == 0
                        assert bool(mip & 8) == bool(index), (index, mip)
                    vm.qmp('quit')
                    assert vm.proc.wait(timeout=5) == 0
                    print(board, 'PASS reset release, shared SRAM/aliases/RTC/AXI, private MSIP')
                finally:
                    vm.close()



def guards(binary):
    with tempfile.TemporaryDirectory(prefix='sg200x-guards-') as tmpdir:
        elf32 = pathlib.Path(tmpdir) / 'rv32.elf'
        ident = b'\x7fELF' + bytes([1, 1, 1]) + bytes(9)
        elf32.write_bytes(struct.pack('<16sHHIIIIIHHHHHH', ident,
            2, 243, 1, 0, 0, 0, 0, 52, 32, 0, 40, 0, 0))
        cases = [
            ('milkv-duo,main-isa=arm64', [], 'no Cortex-A53'),
            ('milkv-duo256m,main-isa=rv32', [], 'main-isa must be'),
            ('milkv-duo256m', ['-m', '128M'], 'board DDR size'),
            ('milkv-duo256m', ['-cpu', 'rv64'], 'selected C906/A53'),
            ('milkv-duo256m', ['-dtb', str(elf32)], 'generated DTB'),
            ('milkv-duo256m', ['-accel', 'tcg,thread=multi', '-icount', 'shift=0'],
             'No MTTCG when icount is enabled'),
            (f'milkv-duo256m,rtos={elf32}', [], 'RV64 ELF'),
            ('milkv-duos,boot-mode=invalid', [], 'boot-mode must be'),
            ('milkv-duos,boot-mode=rom', [], 'ROM boot requires'),
            ('milkv-duos,main-isa=arm64,boot-mode=rom', [], 'ROM boot requires'),
        ]
        for machine, extra, expected in cases:
            accel = [] if '-accel' in extra else ['-accel', 'tcg,thread=single']
            cmd = [str(binary), '-M', machine, '-bios', 'none', '-S',
                   '-display', 'none', '-monitor', 'none',
                   '-serial', 'null', '-serial', 'null'] + accel + extra
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=10)
            assert result.returncode != 0 and expected in result.stderr, result
        print('PASS ten invalid configuration / firmware guards')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--qemu', required=True, type=pathlib.Path)
    parser.add_argument('--output', required=True, type=pathlib.Path)
    parser.add_argument("--thread", choices=["single", "multi"], default="single")
    args = parser.parse_args()
    run(args.qemu.resolve(), args.output.resolve(), args.thread)
    guards(args.qemu.resolve())
