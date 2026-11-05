#!/usr/bin/env python3
"""Run the companion QBox platform and require Linux/real-RTL acceptance markers."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--qbox',type=Path,required=True)
p.add_argument('--build',type=Path,required=True)
p.add_argument('--platform',type=Path,required=True)
p.add_argument('--artifacts',type=Path,required=True)
p.add_argument('--log', type=Path, default=Path('coral-rtl-linux.log'))
p.add_argument('--waveform', type=Path)
p.add_argument('--timeout', type=int, default=600)
a = p.parse_args()
qbox = a.qbox.resolve()
build = a.build.resolve()
env = os.environ.copy()
env['CORAL_ARTIFACTS'] = str(a.artifacts.resolve())
variable = 'DYLD_LIBRARY_PATH' if sys.platform == 'darwin' else 'LD_LIBRARY_PATH'
env[variable] = str(build) + (os.pathsep+env[variable] if env.get(variable) else '')
env['CORAL_RTL_WAVEFORM'] = str(a.waveform.resolve()) if a.waveform else ''
a.log = a.log.resolve()
a.log.parent.mkdir(parents=True, exist_ok=True)
cmd = [str(build/'platforms-vp'), '-l', str(a.platform.resolve()), '-p', 'log_level=1']
try:
    with a.log.open('w') as f:
        result = subprocess.run(cmd, cwd=qbox, env=env, stdout=f, stderr=subprocess.STDOUT, timeout=a.timeout)
except subprocess.TimeoutExpired:
    sys.exit(f'FAIL: timeout; inspect {a.log}')
log = a.log.read_text(errors='replace')
required = ['CORAL_RTL_LINUX_ADD_PASS', 'CORAL_RTL_LINUX_DIGITS_PASS mode=tcm cases=20 output_bytes=200',
            'CORAL_RTL_LINUX_DIGITS_PASS mode=shared-axi cases=20 output_bytes=200',
            'CORAL_RTL_LINUX_FAULT_PASS', 'CORAL_RTL_LINUX_RECOVERY_PASS', 'CORAL_RTL_LINUX_IRQ_PASS handled=43',
            'CORAL_RTL_LINUX_ALL_PASS', 'CORAL_RTL_TEST_EXIT=0']
for line in log.splitlines():
    if 'CORAL_RTL_' in line and 'CASE_PASS' not in line:
        print(line)
missing = [s for s in required if s not in log]
if result.returncode or missing or 'CORAL_RTL_LINUX_FAIL' in log:
    sys.exit(f'FAIL: exit={result.returncode}, missing={missing}; log={a.log}')
print(f'PASS: QEMU/SystemC/TLM-2.0/Coral RTL Linux acceptance; log={a.log}')
