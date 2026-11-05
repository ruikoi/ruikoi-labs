#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Two-vCPU MTTCG: shared atomics and progress while the peer is halted."""
import argparse
import importlib.util
import pathlib
import struct
import subprocess
import sys
import tempfile
import time

spec = importlib.util.spec_from_file_location(
    'smoke', pathlib.Path(__file__).with_name('hetero-smoke.py'))
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
TYPES = {'arm': 'cortex-a53-arm-cpu', 'rv64': 'rv64-riscv-cpu',
         'rv32': 'rv32-mcu'}
ITERATIONS = 10000


def program(isa, slot, busy=False):
    p = smoke.Program()
    shared = 0x20000000 + slot * 0x10000000
    if isa == 'arm':
        p.emit(0xd2a00002 | (shared >> 16) << 5)  # MOVZ X2, shared
        p.emit(0x52800005 | ITERATIONS << 5)  # MOVZ W5, iterations
        p.label('increment')
        p.emit(0x885ffc43)  # LDAXR W3, [X2]
        p.emit(0x11000463)  # ADD W3, W3, #1
        p.emit(0x8804fc43)  # STLXR W4, W3, [X2]
        p.branch('increment', lambda off: 0x35000004 |
                 ((off // 4) & 0x7ffff) << 5)  # CBNZ W4
        if busy:
            p.branch('increment', lambda off: 0x14000000 |
                     ((off // 4) & 0x3ffffff))
        else:
            p.emit(0x710004a5)  # SUBS W5, W5, #1
            p.branch('increment', lambda off: 0x54000001 |
                     ((off // 4) & 0x7ffff) << 5)  # B.NE
            p.emit(0x52800023)  # MOV W3, #1
            p.emit(0xb9000000 | (slot + 1) << 10 | 2 << 5 | 3)
            p.emit(0xd5033fbf)  # DMB SY
            p.label('halt')
            p.emit(0xd503207f)  # WFI
            p.branch('halt', lambda off: 0x14000000 |
                     ((off // 4) & 0x3ffffff))
    else:
        p.emit(shared | 5 << 7 | 0x37)  # LUI t0, shared
        p.emit(0x00100313)  # ADDI t1, zero, 1
        p.emit(0x2000 | 7 << 7 | 0x37)  # LUI t2, 2
        p.emit((ITERATIONS - 0x2000 & 0xfff) << 20 |
               7 << 15 | 7 << 7 | 0x13)  # ADDI t2
        p.label('increment')
        p.emit(0x0600202f | 5 << 15 | 6 << 20)  # AMOADD.W.AQRL zero,t1,(t0)
        if busy:
            p.branch('increment', lambda off:
                     smoke.rv_branch(0, 0, 0, off))
        else:
            p.emit(0xfff38393)  # ADDI t2, t2, -1
            p.branch('increment', lambda off:
                     smoke.rv_branch(7, 0, 1, off))
            p.emit(0x2023 | 5 << 15 | 6 << 20 | (slot + 1) * 4 << 7)
            p.emit(0x0330000f)  # FENCE RW,RW
            p.label('halt')
            p.emit(0x10500073)  # WFI
            p.branch('halt', lambda off: smoke.rv_branch(0, 0, 0, off))
    return p.binary()


def run(binary, pair, halt=False, sample_host=False):
    with tempfile.TemporaryDirectory(prefix='multiarch-mttcg-') as directory:
        tmp = pathlib.Path(directory)
        cmd = [str(binary), '-M', 'hetero,' + ','.join(
            f'cpu{i}={TYPES[isa]}' for i, isa in enumerate(pair)),
            '-accel', 'tcg,thread=multi', '-display', 'none',
            '-serial', 'none', '-monitor', 'none', '-qmp', 'stdio',
            '-nodefaults', '-S']
        for i, isa in enumerate(pair):
            image = tmp / f'cpu{i}.bin'
            image.write_bytes(program(isa, i, busy=halt and i == 1))
            cmd += ['-device', f'loader,file={image},addr=0x10000000,'
                    f'cpu-num={i},force-raw=on', '-device',
                    f'loader,addr=0x10000000,cpu-num={i}']
        with (tmp / 'stderr.log').open('w+') as log:
            vm = smoke.QEMU(cmd, log)
            try:
                cpus = vm.qmp('query-cpus-fast')
                if sys.platform != 'darwin':
                    assert len({c['thread-id'] for c in cpus}) == 2, cpus
                dump = tmp / 'shared.bin'

                def read():
                    vm.qmp('pmemsave', val=0x50000000, size=12,
                           filename=str(dump))
                    return struct.unpack('<3I', dump.read_bytes())

                vm.qmp('cont')
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    values = read()
                    if values[1] and (halt or values[2]):
                        break
                    time.sleep(.01)
                else:
                    raise AssertionError(('completion timeout', pair, values))
                if halt:
                    before = read()[0]
                    time.sleep(.05)
                    after = read()[0]
                    assert after > before, (pair, before, after)
                    if sys.platform == 'darwin' and sample_host:
                        sample = tmp / 'threads.txt'
                        subprocess.run(['/usr/bin/sample', str(vm.proc.pid),
                                        '1', '1', '-file', str(sample)],
                                       check=True, capture_output=True)
                        report = sample.read_text()
                        assert 'CPU 0/TCG' in report, report
                        assert 'CPU 1/TCG' in report, report
                else:
                    assert values == (ITERATIONS * 2, 1, 1), (pair, values)
                vm.qmp('stop')
                vm.qmp('quit')
                assert vm.proc.wait(timeout=5) == 0
                print(f'PASS {pair}: {"halted peer + live peer" if halt else "shared atomic counter"}')
            except Exception:
                log.seek(0)
                print(log.read())
                raise
            finally:
                vm.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=pathlib.Path)
    parser.add_argument('--sample-host', action='store_true', help='Also validate macOS host thread names using sample; may require additional permission')
    args = parser.parse_args()
    for pair in [('arm', 'arm'), ('rv64', 'rv32'), ('arm', 'rv32'),
                 ('rv64', 'arm')]:
        for halt in [False, True]:
            run(args.binary.resolve(), pair, halt, args.sample_host)
