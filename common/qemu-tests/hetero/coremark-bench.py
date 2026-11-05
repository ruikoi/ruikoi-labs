#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run CoreMark on the primary Linux CPU, with an idle, periodic, or fully busy RV32 peer."""
import argparse
import gzip
import hashlib
import json
import math
import pathlib
import re
import socket
import sys
import statistics
import struct
import subprocess
import tempfile
import time


def archive(name, data, mode=0o100755):
    def entry(name, data, mode):
        encoded = name.encode() + b'\0'
        fields = [1, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0,
                  len(encoded), 0]
        head = b'070701' + ''.join(f'{v:08x}' for v in fields).encode()
        result = head + encoded
        result += b'\0' * (-len(result) % 4)
        return result + data + b'\0' * (-len(data) % 4)
    return entry(name, data, mode) + entry('TRAILER!!!', b'', 0)



def periodic_firmware():
    words = []

    def emit(value):
        words.append(value & 0xffffffff)

    def li(rd, value):
        upper = (value + 0x800) >> 12
        emit(upper << 12 | rd << 7 | 0x37)
        emit((value - (upper << 12) & 0xfff) << 20 |
             rd << 15 | rd << 7 | 0x13)

    def rearm():
        li(5, 0x0200bff8)  # mtime
        emit(0x0002a303)  # LW t1, 0(t0)
        li(7, 100000)  # 10 ms at 10 MHz
        emit(0x00730333)  # ADD t1,t1,t2
        li(29, 0x02004000)  # mtimecmp for domain hart 1 at +8
        li(30, 0xffffffff)
        emit(0x01eea623)  # SW t5,12(t4)
        emit(0x006ea423)  # SW t1,8(t4)
        emit(0x000ea623)  # SW zero,12(t4)

    li(5, 0x10000100)
    emit(0x30529073)  # CSRW mtvec,t0
    rearm()
    li(5, 0x80)
    emit(0x3042a073)  # CSRRS mie,t0
    li(5, 8)
    emit(0x3002a073)  # CSRRS mstatus,t0
    emit(0x10500073)
    emit(0xffdff06f)  # WFI loop
    while len(words) < 64:
        emit(0x13)
    li(30, 50000)
    emit(0xffff0f13)  # ADDI t5,t5,-1
    emit(0xfe0f1ee3)  # BNE t5,zero,-4
    emit(0x001e0e13)  # ADDI t3,t3,1 (interrupt heartbeat)
    rearm()
    emit(0x30200073)  # MRET
    return struct.pack('<' + 'I' * len(words), *words)


def busy_firmware():
    """Start continuous register computation after a 20-second boot grace."""
    words = []

    def emit(value):
        words.append(value & 0xffffffff)

    def li(rd, value):
        upper = (value + 0x800) >> 12
        emit(upper << 12 | rd << 7 | 0x37)
        emit((value - (upper << 12) & 0xfff) << 20 |
             rd << 15 | rd << 7 | 0x13)

    li(5, 0x10000100)
    emit(0x30529073)  # mtvec
    li(5, 0x0200bff8)
    emit(0x0002a303)  # read mtime low
    li(7, 200000000)  # 20 seconds, 10 MHz
    emit(0x00730333)
    li(29, 0x02004000)
    li(30, 0xffffffff)
    emit(0x01eea623)
    emit(0x006ea423)
    emit(0x000ea623)
    li(5, 0x80)
    emit(0x3042a073)
    li(5, 8)
    emit(0x3002a073)
    emit(0x10500073)
    emit(0xffdff06f)
    while len(words) < 64:
        emit(0x13)
    emit(0x30401073)  # disable mie; do not return to WFI
    li(28, 1)  # ready marker
    emit(0x001e8e93)  # ADDI t4,t4,1; continuous loop counter
    emit(0xffdff06f)  # JAL zero,-4; no sleeps or memory accesses
    return struct.pack('<' + 'I' * len(words), *words)


def duty50_firmware(phase_ms=5):
    """Equal target compute/WFI phases, measured using the 10 MHz timer."""
    phase_ticks = phase_ms * 10000
    words = []

    def emit(value):
        words.append(value & 0xffffffff)

    def li(rd, value):
        upper = (value + 0x800) >> 12
        emit(upper << 12 | rd << 7 | 0x37)
        emit((value - (upper << 12) & 0xfff) << 20 |
             rd << 15 | rd << 7 | 0x13)

    def clock(rd):
        emit(0xc01 << 20 | 2 << 12 | rd << 7 | 0x73)  # CSRRS rd,time,zero

    def rearm():
        clock(6)
        li(7, phase_ticks)  # 10 MHz
        emit(0x00730333)  # ADD t1,t1,t2
        li(26, 0x02004000)
        li(30, 0xffffffff)
        emit(30 << 20 | 26 << 15 | 2 << 12 | 12 << 7 | 0x23)
        emit(6 << 20 | 26 << 15 | 2 << 12 | 8 << 7 | 0x23)
        emit(26 << 15 | 2 << 12 | 12 << 7 | 0x23)

    li(5, 0x10000100)
    emit(0x30529073)
    rearm()
    li(5, 0x80)
    emit(0x3042a073)
    li(5, 8)
    emit(0x3002a073)
    emit(0x10500073)
    emit(0xffdff06f)
    while len(words) < 64:
        emit(0x13)
    clock(8)  # s0 = busy interval start
    li(7, phase_ticks)
    emit(7 << 20 | 8 << 15 | 7 << 7 | 0x33)  # deadline = start+50000
    loop = len(words)
    emit(0x001e8e93)  # work counter
    clock(6)
    emit(0x40000000 | 7 << 20 | 6 << 15 | 30 << 7 | 0x33)
    offset = (loop - len(words)) * 4
    immediate = offset & 0x1fff
    emit((immediate >> 12) << 31 | ((immediate >> 5) & 0x3f) << 25 |
         30 << 15 | 4 << 12 | ((immediate >> 1) & 0xf) << 8 |
         ((immediate >> 11) & 1) << 7 | 0x63)  # BLT t5,zero,loop
    emit(0x40000000 | 8 << 20 | 6 << 15 | 30 << 7 | 0x33)
    emit(30 << 20 | 25 << 15 | 25 << 7 | 0x33)  # accumulated busy ticks
    emit(0x001e0e13)  # completed busy intervals
    rearm()
    emit(0x30200073)
    return struct.pack('<' + 'I' * len(words), *words)


def duty_snapshot(registers):
    ticks = int(re.search(r'\bx25/s9\s+([0-9a-f]+)', registers)[1], 16)
    now = int(re.search(r'^ time\s+([0-9a-f]+)', registers, re.M)[1], 16)
    return ticks, now


def peer_registers(stream, cpu_index=1):
    qmp(stream, 'stop')
    stream.write((json.dumps({'execute': 'human-monitor-command',
                             'arguments': {'command-line': 'info registers',
                                           'cpu-index': cpu_index}}) + '\n').encode())
    stream.flush()
    while True:
        response = json.loads(stream.readline())
        if 'error' in response:
            raise RuntimeError(response)
        if 'return' in response:
            return response['return']


def read_until(sock, token, log, timeout=180):
    data = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            part = sock.recv(65536)
        except socket.timeout:
            continue
        if not part:
            raise RuntimeError('serial disconnected')
        log.write(part)
        log.flush()
        data.extend(part)
        if token in data:
            return bytes(data)
        if b'Kernel panic' in data:
            raise RuntimeError(data[-2000:].decode(errors='replace'))
    raise TimeoutError(data[-2000:].decode(errors='replace'))


def qmp(stream, command):
    stream.write((json.dumps({'execute': command}) + '\n').encode())
    stream.flush()
    while True:
        result = json.loads(stream.readline())
        if 'error' in result:
            raise RuntimeError(result)
        if 'return' in result:
            return result['return']


def connect(path, process):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('QEMU exited')
        try:
            sock = socket.socket(socket.AF_UNIX)
            sock.connect(str(path))
            sock.settimeout(.1)
            return sock
        except OSError:
            sock.close()
            time.sleep(.02)
    raise TimeoutError(str(path))


class MacHostSampler:
    """Use libproc thread CPU times and Mach per-core tick counters."""

    def __init__(self, pid, output, name, binary, interval):
        self.path = output / f'{name}-host-samples.jsonl'
        self.log = self.path.open('wb')
        self.errors = (output / f'{name}-host-sampler.log').open('wb')
        self.process = subprocess.Popen([str(binary), str(pid), str(round(interval * 1000))],
                                        stdout=self.log, stderr=self.errors)
        deadline = time.monotonic() + 5
        while self.path.stat().st_size == 0:
            if self.process.poll() is not None or time.monotonic() >= deadline:
                self.process.terminate()
                self.process.wait(timeout=5)
                raise RuntimeError('Host sampler could not obtain initial counters')
            time.sleep(.01)
        self.interval = interval

    def finish(self):
        self.process.terminate()
        status = self.process.wait(timeout=5)
        self.log.close()
        self.errors.close()
        assert status == 0, status
        rows = [json.loads(line) for line in self.path.read_text().splitlines()]
        assert len(rows) >= 2 and all(r['threads'] and r['cpus'] for r in rows)
        assert all(len(r['cpus']) == len(rows[0]['cpus']) for r in rows)
        first, last = rows[0], rows[-1]
        seconds = (last['monotonic_ns'] - first['monotonic_ns']) / 1e9
        initial = {t['tid']: t for t in first['threads']}
        threads = []
        for end in last['threads']:
            begin = initial.get(end['tid'])
            if begin:
                cpu_seconds = (end['user_ns'] + end['system_ns'] - begin['user_ns'] - begin['system_ns']) / 1e9
                states = [t['state'] for r in rows for t in r['threads'] if t['tid'] == end['tid']]
                threads.append(dict(thread_handle=end['tid'], name=end['name'], cpu_seconds=cpu_seconds,
                                    cpu_percent=cpu_seconds / seconds * 100,
                                    state_counts={str(state): states.count(state) for state in set(states)}))
        cpus = []
        for begin, end in zip(first['cpus'], last['cpus']):
            ticks = [(b - a) & 0xffffffff for a, b in zip(begin['ticks'], end['ticks'])]
            cpus.append(dict(cpu=end['cpu'], busy_percent=(ticks[0] + ticks[1] + ticks[3]) / sum(ticks) * 100))
        return dict(seconds=seconds, samples=len(rows), interval=self.interval, threads=threads, cpus=cpus)


def run(args, isa, mode, peer, iterations, tag, formal=True):
    output = args.output
    name = f'{isa}-{peer}-{mode}-{tag}'
    with tempfile.TemporaryDirectory(prefix='cm-', dir='/tmp') as directory:
        tmp = pathlib.Path(directory)
        cmd = [str(args.qemu), '-M',
               f'multiarch-virt-{"arm" if isa == "arm64" else "riscv"},'
               f'mcu=rv32-mcu,mcu-firmware={output / (peer + ".bin")}',
               '-accel', f'tcg,thread={mode},tb-size=128', '-smp', '1',
               '-m', '256M', '-display', 'none', '-monitor', 'none',
               '-parallel', 'none', '-nic', 'none',
               '-kernel', str(args.images / isa / 'Image'),
               '-initrd', str(output / f'{isa}-coremark.cpio.gz'),
               '-append', ('console=ttyAMA0' if isa == 'arm64' else
                           'console=ttyS0') + ' rdinit=/init panic=0',
               '-chardev', f'socket,id=main,path={tmp}/serial,server=on,wait=off',
               '-serial', 'chardev:main', '-serial', 'null', '-S',
               '-qmp', f'unix:{tmp}/qmp,server=on,wait=off']
        if isa == 'riscv64':
            cmd += ['-bios', str(args.images / isa / 'fw_dynamic.bin')]
        if args.taskpolicy_clamp:
            cmd = ['/usr/sbin/taskpolicy', '-c', args.taskpolicy_clamp] + cmd
        (output / f'{name}-command.json').write_text(json.dumps(cmd, indent=2))
        with (output / f'{name}-stderr.log').open('wb') as err, \
                (output / f'{name}-serial.log').open('wb') as log:
            process = subprocess.Popen(cmd, stdout=err, stderr=err)
            serial = control = stream = None
            sampler = None
            try:
                serial = connect(tmp / 'serial', process)
                control = connect(tmp / 'qmp', process)
                control.settimeout(10)
                stream = control.makefile('rwb')
                json.loads(stream.readline())
                qmp(stream, 'qmp_capabilities')
                cpus = qmp(stream, 'query-cpus-fast')
                qmp(stream, 'cont')
                boot_start = time.monotonic()
                read_until(serial, b'login:', log)
                serial.sendall(b'root\n')
                read_until(serial, b'# ', log)
                serial.sendall(b'uname -m; echo SYSTEM_READY\n')
                boot = read_until(serial, b'\r\nSYSTEM_READY\r\n', log)
                assert (b'aarch64' if isa == 'arm64' else b'riscv64') in boot
                # Populate guest pages and TBs identically before measured work.
                serial.sendall(b'/usr/bin/coremark 0 0 0x66 10000; echo WARM_DONE\n')
                read_until(serial, b'\r\nWARM_DONE\r\n', log)
                time.sleep(.2)
                before = None
                if peer == 'busy':
                    # Keep Linux boot and warmup outside the fully loaded phase.
                    time.sleep(max(0, 22 - (time.monotonic() - boot_start)))
                    before = peer_registers(stream)
                    (output / f'{name}-mcu-before.txt').write_text(before)
                    assert int(re.search(r'\bx28/t3\s+([0-9a-f]+)', before)[1], 16) == 1
                    qmp(stream, 'cont')
                if peer == 'duty50':
                    before = peer_registers(stream)
                    (output / f'{name}-mcu-before.txt').write_text(before)
                    qmp(stream, 'cont')
                command = f'/usr/bin/coremark 0 0 0x66 {iterations}; echo BENCH_DONE\n'
                if args.host_sampler:
                    sampler = MacHostSampler(cpus[0]['thread-id'], output, name, args.host_sampler, args.sample_interval)
                start = time.perf_counter_ns()
                serial.sendall(command.encode())
                try:
                    raw = read_until(serial, b'\r\nBENCH_DONE\r\n', log, timeout=args.timeout)
                except TimeoutError as exc:
                    wall = (time.perf_counter_ns() - start) / 1e9
                    host = sampler.finish() if sampler else None
                    sampler = None
                    for index in (0, 1):
                        registers = peer_registers(stream, index)
                        (output / f'{name}-timeout-cpu{index}.txt').write_text(registers)
                    row = dict(isa=isa, mode=mode, peer=peer, tag=tag,
                               iterations=iterations, status='timeout',
                               timeout_seconds=args.timeout, elapsed_seconds=wall,
                               detail=str(exc), cpus=cpus, host=host)
                    (output / f'{name}-result.json').write_text(json.dumps(row, indent=2))
                    qmp(stream, 'quit')
                    process.wait(timeout=5)
                    print(json.dumps(row), flush=True)
                    return row
                wall = (time.perf_counter_ns() - start) / 1e9
                host = sampler.finish() if sampler else None
                sampler = None
                result = raw.decode(errors='replace')
                assert 'Cannot validate' not in result, result
                assert not re.search(r'ERROR!.*crc', result, re.I), result
                if formal:
                    assert 'Correct operation validated.' in result, result
                guest_time = float(re.search(r'Total time \(secs\)\s*:\s*([\d.]+)', result)[1])
                if formal and guest_time < 10:
                    raise RuntimeError('Formal CoreMark run must last at least ten seconds')
                crcs = re.findall(r'(?:seedcrc|\[0\]crc\w*)\s*:\s*0x([\da-f]+)', result)
                assert len(crcs) >= 4, result
                row = dict(isa=isa, mode=mode, peer=peer, tag=tag,
                           iterations=iterations, wall_seconds=wall,
                           guest_seconds=guest_time, host_iterations_per_sec=iterations / wall,
                           crc=crcs, cpus=cpus, host=host)
                (output / f'{name}-result.json').write_text(json.dumps(row, indent=2))
                if peer in ('periodic', 'busy', 'duty50'):
                    registers = peer_registers(stream)
                    (output / f'{name}-mcu-registers.txt').write_text(registers)
                    heartbeat = int(re.search(r'\bx28/t3\s+([0-9a-f]+)', registers)[1], 16)
                    assert heartbeat > 0, registers
                    if peer == 'busy':
                        begin = int(re.search(r'\bx29/t4\s+([0-9a-f]+)', before)[1], 16)
                        finish = int(re.search(r'\bx29/t4\s+([0-9a-f]+)', registers)[1], 16)
                        assert begin != finish, registers
                        row['peer_counter_before'] = begin
                        row['peer_counter_after'] = finish
                    elif peer == 'duty50':
                        busy_begin, time_begin = duty_snapshot(before)
                        busy_end, time_end = duty_snapshot(registers)
                        busy_ticks = (busy_end - busy_begin) & 0xffffffff
                        elapsed_ticks = (time_end - time_begin) & 0xffffffff
                        assert elapsed_ticks > 0 and busy_ticks > 0
                        row['peer_busy_ticks'] = busy_ticks
                        row['peer_elapsed_ticks'] = elapsed_ticks
                        row['peer_busy_fraction'] = busy_ticks / elapsed_ticks
                        row['peer_interrupts'] = heartbeat
                    else:
                        row['peer_interrupts'] = heartbeat
                    (output / f'{name}-result.json').write_text(json.dumps(row, indent=2))
                qmp(stream, 'quit')
                assert process.wait(timeout=5) == 0
                print(json.dumps(row), flush=True)
                return row
            finally:
                if sampler:
                    sampler.finish()
                if stream:
                    stream.close()
                if control:
                    control.close()
                if serial:
                    serial.close()
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True, type=pathlib.Path)
    parser.add_argument('--images', required=True, type=pathlib.Path)
    parser.add_argument('--output', required=True, type=pathlib.Path)
    parser.add_argument('--repeats', type=int, default=7)
    parser.add_argument('--target-seconds', type=float, default=14)
    parser.add_argument('--peers', nargs='+', choices=['wfi', 'periodic', 'busy', 'duty50'], default=['wfi', 'periodic'])
    parser.add_argument('--timeout', type=float, default=90)
    parser.add_argument('--fixed-iterations', nargs=2, type=int, metavar=('ARM64', 'RV64'))
    parser.add_argument('--taskpolicy-clamp', choices=['utility', 'background', 'maintenance'])
    parser.add_argument('--host-sampler', type=pathlib.Path)
    parser.add_argument('--sample-interval', type=float, default=.25)
    parser.add_argument('--duty-phase-ms', type=int, default=5,
                        help='Target duration of each duty50 compute/WFI phase')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if (args.taskpolicy_clamp or args.host_sampler) and sys.platform != 'darwin':
        parser.error('taskpolicy and this host sampler require macOS')
    if args.sample_interval <= 0:
        parser.error('Sample interval must be positive')
    if not 1 <= args.duty_phase_ms <= 1000:
        parser.error('Duty phase must be between 1 and 1000 ms')
    if args.host_sampler:
        args.host_sampler = args.host_sampler.resolve()
    args.qemu = args.qemu.resolve()
    args.images = args.images.resolve()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    # Identical peer work in both modes; MMIO is limited to its timer.
    (args.output / 'wfi.bin').write_bytes(struct.pack('<2I', 0x10500073, 0xffdff06f))
    (args.output / 'periodic.bin').write_bytes(periodic_firmware())
    (args.output / 'busy.bin').write_bytes(busy_firmware())
    (args.output / 'duty50.bin').write_bytes(duty50_firmware(args.duty_phase_ms))
    hashes = {}
    for isa in ['arm64', 'riscv64']:
        binary = args.output / f'coremark-{isa}'
        hashes[str(binary)] = hashlib.sha256(binary.read_bytes()).hexdigest()
        initrd = (args.images / isa / 'rootfs.cpio.gz').read_bytes()
        overlay = gzip.compress(archive('usr/bin/coremark', binary.read_bytes()), mtime=0)
        (args.output / f'{isa}-coremark.cpio.gz').write_bytes(initrd + overlay)
    hashes[str(args.qemu)] = hashlib.sha256(args.qemu.read_bytes()).hexdigest()
    hash_file = args.output / 'hashes.json'
    if args.resume and hash_file.exists():
        if json.loads(hash_file.read_text()) != hashes:
            raise RuntimeError('Cannot resume with different QEMU or benchmark binaries')
    hash_file.write_text(json.dumps(hashes, indent=2))
    rows = json.loads((args.output / "results.json").read_text()) if args.resume else []
    counts = {}
    for isa in ['arm64', 'riscv64']:
        previous = [r for r in rows if r['isa'] == isa]
        if previous:
            counts[isa] = previous[0]['iterations']
        elif args.fixed_iterations:
            counts[isa] = args.fixed_iterations[['arm64', 'riscv64'].index(isa)]
        else:
            pilot = run(args, isa, 'multi', 'wfi', 10000, 'pilot', formal=False)
            counts[isa] = math.ceil(args.target_seconds / min(pilot['wall_seconds'], pilot['guest_seconds']) * 10000 / 1000) * 1000
        print(f'CALIBRATED {isa}: {counts[isa]} iterations', flush=True)
        for peer in args.peers:
            for repeat in range(args.repeats):
                modes = ['multi', 'single'] if repeat % 2 == 0 else ['single', 'multi']
                for mode in modes:
                    if any(r['isa'] == isa and r['mode'] == mode and r['peer'] == peer and r['tag'] == str(repeat) for r in rows):
                        continue
                    rows.append(run(args, isa, mode, peer, counts[isa], str(repeat)))
                    (args.output / 'results.json').write_text(json.dumps(rows, indent=2))
    summary = []
    for isa in counts:
        for peer in args.peers:
            group = [r for r in rows if r['isa'] == isa and r['peer'] == peer]
            assert len({tuple(r['crc']) for r in group if 'crc' in r}) <= 1, group
            medians = {}
            item = dict(isa=isa, peer=peer, iterations=counts[isa])
            for mode in ['single', 'multi']:
                times = [r['wall_seconds'] for r in group if r['mode'] == mode and 'wall_seconds' in r]
                if not times:
                    item[mode] = dict(samples=0, timeouts=sum(r.get('status') == 'timeout' for r in group if r['mode'] == mode))
                    continue
                medians[mode] = statistics.median(times)
                item[mode] = dict(median=medians[mode], minimum=min(times),
                                  maximum=max(times), samples=len(times),
                                  throughput=counts[isa] / medians[mode])
            item['speedup'] = medians['single'] / medians['multi'] if len(medians) == 2 else None
            summary.append(item)
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()
