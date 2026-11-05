#!/usr/bin/env python3
# Local experiment; validates boot plumbing with a bare-metal entry probe.
import argparse, json, pathlib, struct, subprocess, tempfile, time
ap = argparse.ArgumentParser()
ap.add_argument('--qemu', default=str(pathlib.Path(__file__).resolve().parents[3] / 'build-hetero/qemu-system-hetero'))
args = ap.parse_args()
out = pathlib.Path(tempfile.mkdtemp(prefix='hetero-linux-boot-smoke-', dir='/tmp'))
out.mkdir(exist_ok=True)
# Tiny entry probe: output 'M' on the MCU UART, then spin. Not a Linux kernel.
probe = out/'entry.bin'
def bne(rs1, rs2, offset):
    return (0x1063 | rs1 << 15 | rs2 << 20 | ((offset >> 12) & 1) << 31 |
            ((offset >> 5) & 63) << 25 | ((offset >> 1) & 15) << 8 |
            ((offset >> 11) & 1) << 7)
words = [0x00100293, bne(10, 5, 36), 0x0005a283, 0xedfe1337,
         0xdd030313, bne(5, 6, 20), 0x400002b7, 0x04d00313,
         0x00628023, 0x0000006f, 0x400002b7, 0x04600313,
         0x00628023, 0x0000006f]
probe.write_bytes(struct.pack('<14I', *words))
qemu = args.qemu
for machine in ('hetero-virt-arm', 'hetero-virt-riscv'):
    log = out/(machine+'.uart')
    cmd = [qemu, '-M', f'{machine},mcu=rv32-mcu,mcu-ram=64M,mcu-kernel={probe}',
           '-accel','tcg,thread=single','-display','none','-monitor','none',
           '-serial','null','-serial',f'file:{log}','-qmp','stdio','-S']
    if machine.endswith('riscv'):
        cmd += ['-bios','none']
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    def recv():
        while True:
            line = p.stdout.readline()
            if not line: raise RuntimeError(p.stderr.read())
            msg=json.loads(line)
            if 'event' not in msg: return msg
    def call(execute, arguments=None):
        p.stdin.write(json.dumps({'execute':execute, **({'arguments':arguments} if arguments else {})})+'\n')
        p.stdin.flush()
        return recv()
    try:
        recv(); call('qmp_capabilities')
        cpus=call('query-cpus-fast')
        assert len(cpus['return']) == 2, cpus
        call('cont'); time.sleep(0.3); call('stop')
        regs=call('human-monitor-command', {'command-line':'cpu 1'})
        regs=call('human-monitor-command', {'command-line':'info registers -a'})
        assert log.read_bytes()==b'M', log.read_bytes()
        assert '13f00000' in regs['return'], regs
        call('system_reset'); call('cont'); time.sleep(0.2); call('stop')
        assert log.read_bytes() == b'MM', log.read_bytes()
        print(machine, 'PASS: entry, hart ID, private DTB magic/pointer, UART1, reset; two CPUs')
        (out/(machine+'.registers')).write_text(regs['return'])
        call('quit'); p.wait(timeout=5)
    finally:
        if p.poll() is None: p.kill(); p.wait()
