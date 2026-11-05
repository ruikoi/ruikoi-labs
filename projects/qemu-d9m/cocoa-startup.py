#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Check Cocoa startup/quit for each layout; this is not an input/focus test."""
import argparse
import importlib.util
from pathlib import Path
import sys
import tempfile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--binary', required=True, type=Path)
a = p.parse_args()
if sys.platform != 'darwin':
    raise SystemExit('Cocoa startup requires macOS')
root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('smoke', root / 'common/qemu-tests/hetero/hetero-smoke.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
for display, count in [('cocoa', 1), ('cocoa,split=on', 2), ('cocoa,layout=2+3', 5)]:
    with tempfile.TemporaryDirectory(prefix='cocoa-startup-') as td:
        command = [str(a.binary.resolve()), '-M', 'multiarch-virt', '-accel', 'tcg,thread=single',
                   '-display', display, '-monitor', 'none', '-qmp', 'stdio', '-S']
        for i in range(count):
            command += ['-chardev', f'vc,id=console{i},cols=80,rows=24',
                        '-domain', f'id=mcu{i},platform=arm-mcu,cpu=cortex-r5f,cpus=1,ram=16M,chardev=console{i}']
        with (Path(td) / 'qemu.log').open('w+') as log:
            vm = smoke.QEMU(command, log)
            try:
                assert len(vm.qmp('query-cpus-fast')) == count
                vm.qmp('query-status')
                vm.qmp('quit')
                assert vm.proc.wait(timeout=10) == 0
                print('PASS Cocoa startup/quit:', display)
            except BaseException:
                log.seek(0)
                print(log.read())
                raise
            finally:
                vm.close()
