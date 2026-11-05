"""Run the actual simulator and require workload-specific evidence."""
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def logged(c, cmd, log, cwd=None, env=None):
    command = list(map(str, cmd))
    print('+', ' '.join(command), flush=True)
    with log.open('w') as out:
        result = subprocess.run(command, cwd=cwd, env=env or c.env, stdout=out,
                                stderr=subprocess.STDOUT, timeout=c.a.timeout)
    text = log.read_text(errors='replace')
    if result.returncode:
        print(text[-6000:])
        raise ValueError(f'Process exited {result.returncode}: {log}')
    return text


def require(text, markers):
    missing = [x for x in markers if x not in text]
    if missing:
        raise ValueError('Missing acceptance evidence: ' + ', '.join(missing))


def acceptance(c, output, interactive=False):
    suite = c.a.suite
    coral = 'coral' in c.a.project
    rtl = c.a.project.endswith('rtl')
    supported = {
        'qemu-coral-isa': {'backend', 'smoke', 'linux', 'digits', 'mobilenet', 'gemma', 'gemma-guest'},
        'qemu-coral-rtl': {'smoke', 'linux', 'digits', 'gemma'},
        'qemu-vortex-isa': {'smoke', 'linux', 'mnist', 'mobilenet', 'full'},
        'qemu-vortex-rtl': {'smoke', 'linux', 'mnist', 'mobilenet', 'interface', 'full'},
    }
    if suite not in supported[c.a.project]:
        raise ValueError('Suite is not available for this project: ' + suite)
    if coral and rtl and suite == 'gemma' and c.a.profile != 'gemma':
        raise ValueError('Gemma RTL acceptance requires --profile gemma')
    if suite in ('gemma', 'mobilenet') and coral and not rtl:
        from models import acceptance as model_acceptance
        return model_acceptance(c, output)
    if not coral and not (rtl and suite == 'smoke'):
        prepared = c.artifacts / 'workload.json'
        if prepared.exists():
            meta = json.loads(prepared.read_text())
            if meta['suite'] != suite or meta['samples'] != c.a.samples:
                raise ValueError(f"Prepared suite={meta['suite']} samples={meta['samples']}; "
                                 f"requested suite={suite} samples={c.a.samples}. Run prepare with matching arguments")
    if rtl:
        build = c.build / 'qbox'
        if suite == 'smoke':
            if coral and c.a.profile == 'gemma':
                raise ValueError('Use --suite gemma after prepare for the high-memory Gemma workload')
            binary = build / ('coral-platform-smoke' if coral else 'vortex-platform-smoke')
            command = [binary, c.build / 'guest/add.elf', '-'] if coral else [binary]
            text = logged(c, command, output / 'rtl-smoke.log', build)
            require(text, ['CORAL_RTL_TLM_SMOKE_PASS'] if coral else ['VORTEX_RTL_TLM_CONTROL_PASS'])
            return
        profile = 'integration/coral-rtl/' + c.a.profile if coral else 'integration/vortex-rtl'
        script = ('coral_gemma_rtl_test.py' if coral and c.a.profile == 'gemma' else
                  'coral_rtl_test.py' if coral else 'vortex_rtl_test.py')
        cmd = [sys.executable, HERE / script, '--qbox', c.source('qbox'), '--build', build,
               '--platform', c.source('qbox') / profile / 'platform.lua',
               '--artifacts', c.artifacts, '--log', output / 'serial.log', '--timeout', c.a.timeout]
        if not coral:
            cmd += ['--stage', suite if suite in ('smoke', 'mnist', 'interface', 'mobilenet', 'full') else 'smoke',
                    '--samples', c.a.samples]
            if c.a.backpressure:
                cmd += ['--backpressure']
        c.execute(cmd)
        return
    if coral and suite == 'backend':
        env = c.env.copy()
        env['PYTHONPATH'] = str(c.build / 'guest')
        text = logged(c, [sys.executable, HERE / 'coral_backend_test.py',
                          c.source('mpact') / 'bazel-bin/sim/libcoral_qemu.so'],
                      output / 'backend.log', c.build / 'guest', env)
        require(text, ['BACKEND_ALL_PASS'])
        text = logged(c, [sys.executable, HERE / 'coral_gemma_backend_test.py', '--backend',
                          c.source('mpact') / 'bazel-bin/sim/libcoral_gemma.so'], output / 'gemma-backend.log')
        require(text, ['CORAL_GEMMA_BACKEND_TEST_PASS'])
        return
    if not coral:
        cmd = [sys.executable, HERE / 'vortex_isa_test.py', '--binary',
               c.build / 'qemu/qemu-system-riscv64', '--images', c.artifacts,
               '--backend', c.build / 'vortex/sim/simx/libvortex-gem5.so',
               '--initrd', c.artifacts / 'riscv64-vortex.cpio.gz', '--log', output / 'serial.log',
               '--timeout', c.a.timeout]
        if suite in ('mnist', 'full'):
            cmd += ['--mnist', '--mnist-samples', c.a.samples]
        if suite in ('mobilenet', 'full'):
            cmd += ['--mobilenet']
        c.execute(cmd)
        return
    if suite == 'gemma-guest':
        binary = c.build / 'qemu/qemu-system-riscv64'
        cmd = [binary, '-M', 'virt', '-m', '2048M', '-nographic', '-monitor', 'none',
               '-nic', 'none', '-no-reboot', '-bios', c.artifacts / 'fw_dynamic.bin',
               '-kernel', c.artifacts / 'Image', '-initrd', c.artifacts / 'riscv64-gemma.cpio.gz',
               '-append', 'console=ttyS0 rdinit=/gemma-chat-init', '-device',
               'coral-npu-gemma,backend=' + str(c.source('mpact') / 'bazel-bin/sim/libcoral_gemma.so')]
        log = output / 'serial.log'
        logged(c, cmd, log)
        c.execute([sys.executable, HERE / 'audit_gemma_guest.py', log, '--output', output / 'audit.json'])
        return
    arch = 'aarch64' if c.a.arch == 'arm64' else 'riscv64'
    console = 'ttyAMA0' if c.a.arch == 'arm64' else 'ttyS0'
    binary = c.build / 'qemu' / ('qemu-system-' + arch)
    command = [binary, '-M', 'virt', '-accel', 'tcg', '-m', '256M', '-display', 'none',
               '-monitor', 'none', '-serial', 'stdio', '-nic', 'none', '-no-reboot',
               '-device', 'coral-npu,backend=' + str(c.source('mpact') / 'bazel-bin/sim/libcoral_qemu.so'),
               '-kernel', c.artifacts / 'Image', '-initrd', c.artifacts / (c.a.arch + '-coral.cpio.gz'),
               '-append', 'console=' + console + ' rdinit=/coral-init panic=1']
    command += ['-cpu', 'cortex-a53'] if arch == 'aarch64' else ['-bios', c.artifacts / 'fw_dynamic.bin']
    (output / 'binary.sha256').write_text(hashlib.sha256(binary.read_bytes()).hexdigest() + '\n')
    text = logged(c, command, output / 'serial.log')
    require(text, ['CORAL_GUEST_ALL_PASS', 'CORAL_TEST_EXIT=0'])
    if suite == 'digits':
        require(text, ['REAL_TFLITE_ALL_PASS cases=20 exact=20/20'])
    print('CORAL_LINUX_ACCEPTANCE_PASS', c.a.arch, output)
