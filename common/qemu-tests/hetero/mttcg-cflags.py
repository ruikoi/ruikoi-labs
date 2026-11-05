#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Inspect executed virt TB flags when the primary SMP topology has one CPU."""
import argparse
import importlib.util
import pathlib
import re
import struct
import tempfile
import time

spec = importlib.util.spec_from_file_location(
    'smoke', pathlib.Path(__file__).with_name('hetero-smoke.py'))
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def run(binary, arch, mcu, mode):
    with tempfile.TemporaryDirectory(prefix='mttcg-cflags-') as directory:
        tmp = pathlib.Path(directory)
        if mcu == 'rv32-mcu':
            payload = struct.pack('<2I', 0x10500073, 0xffdff06f)
        else:
            payload = bytearray(0x108)
            struct.pack_into('<II', payload, 0, 0x100ff000, 0x10000101)
            struct.pack_into('<2H', payload, 0x100, 0xbf30, 0xe7fd)
        (tmp / 'mcu.bin').write_bytes(payload)
        arm = arch == 'arm'
        (tmp / 'main.bin').write_bytes(struct.pack('<I',
                                      0xd503207f if arm else 0x10500073))
        address = 0x40200000 if arm else 0x80200000
        trace = tmp / 'trace.log'
        cmd = [str(binary), '-M', f'multiarch-virt-{arch},mcu={mcu},'
               f'mcu-firmware={tmp}/mcu.bin', '-smp', '1',
               '-accel', f'tcg,thread={mode}', '-display', 'none',
               '-serial', 'null', '-serial', 'null', '-monitor', 'none',
               '-qmp', 'stdio', '-S', '-d', 'exec', '-D', str(trace),
               '-device', f'loader,file={tmp}/main.bin,addr={address},'
               'cpu-num=0,force-raw=on']
        if not arm:
            cmd += ['-bios', 'none']
        with (tmp / 'stderr.log').open('w+') as log:
            try:
                vm = smoke.QEMU(cmd, log)
            except Exception:
                log.seek(0)
                print(log.read())
                raise
            try:
                vm.qmp('cont')
                time.sleep(.2)
                vm.qmp('stop')
                vm.qmp('quit')
                assert vm.proc.wait(timeout=5) == 0
                entries = re.findall(r'Trace (\d+): .*\[[^\]]*/([0-9a-f]+)\]',
                                     trace.read_text())
                assert {int(cpu) for cpu, _ in entries} == {0, 1}, entries
                for cpu, encoded in entries:
                    flags = int(encoded, 16)
                    assert flags >> 24 == int(cpu), (cpu, encoded)
                    assert bool(flags & 0x8000) == (mode == 'multi'), entries
                print(f'PASS {arch}/{mcu}/{mode}: cluster and parallel TB flags')
            finally:
                vm.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=pathlib.Path)
    args = parser.parse_args()
    for arch in ['arm', 'riscv']:
        for mcu in ['rv32-mcu', 'cortex-m3-arm-cpu']:
            for mode in ['single', 'multi']:
                run(args.binary.resolve(), arch, mcu, mode)
