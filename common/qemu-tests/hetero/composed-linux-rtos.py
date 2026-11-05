#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Boot two A55 Linux systems and three R5F FreeRTOS shells, then reset."""
import argparse
import hashlib
import importlib.util
import json
import pathlib
import re
import socket
import time
import threading

spec = importlib.util.spec_from_file_location(
    "smoke", pathlib.Path(__file__).with_name("hetero-smoke.py"))
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class Serial:
    def __init__(self, path, log):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(str(path))
        self.sock.settimeout(.2)
        self.buffer = bytearray()
        self.cursor = 0
        self.log = log.open("wb")
        self.done = False
        self.reader = threading.Thread(target=self.pump, daemon=True)
        self.reader.start()

    def pump(self):
        # Drain every socket continuously: a full serial backend can block BQL.
        while not self.done:
            try:
                data = self.sock.recv(65536)
                if not data:
                    break
                self.buffer.extend(data)
                self.log.write(data)
                self.log.flush()
            except socket.timeout:
                pass
            except OSError:
                break

    def wait(self, marker, timeout=60):
        deadline = time.monotonic() + timeout
        start = self.cursor
        while marker not in self.buffer[start:]:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"waiting for {marker!r}: {self.buffer[-400:]!r}")
            time.sleep(.02)
        self.cursor = len(self.buffer)
        return bytes(self.buffer[start:self.cursor])

    def command(self, text, prompt):
        self.cursor = len(self.buffer)
        self.sock.sendall(text.encode() + b"\n")
        return self.wait(prompt, 10)

    def close(self):
        self.done = True
        self.reader.join(timeout=1)
        self.sock.close()
        self.log.close()


def run(args):
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    command = [str(args.binary.resolve()), "-M", "multiarch-virt", "-accel",
               "tcg,thread=" + args.thread, "-display",
               "cocoa,layout=2+3" if args.display == "cocoa" else "none",
               "-name", "D9M / 2 Linux + 3 FreeRTOS / MTTCG", "-monitor", "none",
               "-qmp", "stdio", "-S"]
    for i in range(5):
        path = out / f"uart{i}.sock"
        path.unlink(missing_ok=True)
        domain = f"linux{i}" if i < 2 else f"rtos{i-2}"
        if args.display == "cocoa":
            command += ["-chardev", f"vc,id=view{i},cols=80,rows={24 if i < 2 else 18}",
                        "-chardev", f"socket,id=control{i},path={path},server=on,wait=off",
                        "-chardev", f"hub,id=u{i},chardevs.0=view{i},chardevs.1=control{i}"]
        else:
            command += ["-chardev", f"socket,id=u{i},path={path},server=on,wait=off"]
        if i < 2:
            command += ["-domain", f"id={domain},platform=arm-virt,cpu=cortex-a55,"
                        f"cpus={8 if i == 0 else 4},ram=" +
                        ("1G" if i == 0 else "512M") + f",chardev=u{i}",
                        "-domain-loader", f"domain={domain},kernel={args.linux.resolve()}/Image,"
                        f"initrd={args.linux.resolve()}/rootfs.cpio.gz,"
                        "append=console=ttyAMA0 rdinit=/init panic=0"]
        else:
            command += ["-domain", f"id={domain},platform=arm-mcu,cpu=cortex-r5f,"
                        f"cpus=1,ram=16M,chardev=u{i}", "-domain-loader",
                        f"domain={domain},firmware={args.firmware.resolve()}/freertos-r5f-{i-1}.elf"]
    (out / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    (out / "result.json").unlink(missing_ok=True)
    results = []
    serials = []
    with (out / "qemu.log").open("w+") as log:
        vm = smoke.QEMU(command, log)
        try:
            cpus = vm.qmp("query-cpus-fast")
            assert len(cpus) == 15
            assert all(c["qom-type"] == "cortex-a55-arm-cpu" for c in cpus[:12])
            assert all(c["qom-type"] == "cortex-r5f-arm-cpu" for c in cpus[12:])
            (out / "cpus.json").write_text(json.dumps(cpus, indent=2) + "\n")
            for i in range(5):
                serials.append(Serial(out / f"uart{i}.sock", out / f"uart{i}.log"))
            for repeat in (0, 1):
                if repeat:
                    vm.qmp("system_reset")
                vm.qmp("cont")
                for i, serial in enumerate(serials[:2]):
                    serial.wait(b"login:")
                    serial.command("root", b"# ")
                    reply = serial.command(
                        f"hostname linux{i}; uname -m; nproc; head -1 /proc/meminfo", b"# ")
                    assert b"\r\naarch64\r\n" in reply
                    assert f"\r\n{8 if i == 0 else 4}\r\n".encode() in reply
                    memory = int(re.search(rb"MemTotal:\s+(\d+) kB", reply)[1])
                    assert (900000 < memory < 1048576) if i == 0 else (400000 < memory < 524288)
                    # Separate rootfs instances have separate hostnames/files.
                    reply = serial.command(f"echo linux{i} >/tmp/domain; cat /tmp/domain; hostname", b"# ")
                    assert reply.splitlines().count(f"linux{i}".encode()) == 2
                    results.append({"boot": repeat, "domain": f"linux{i}",
                                    "cpus": 8 if i == 0 else 4, "MemTotal_kB": memory})
                for i, serial in enumerate(serials[2:], 1):
                    prompt = f"mcu{i}> ".encode()
                    serial.wait(prompt)
                    reply = serial.command("arch", prompt)
                    assert f"FreeRTOS on ARM MCU {i}".encode() in reply
                    assert b"Cortex-R5F" in reply
                    previous = None
                    for _ in range(5):
                        reply = serial.command("ticks", prompt)
                        now = tuple(map(int, re.search(
                            rb"ticks=(\d+) heartbeat=(\d+) uart_irqs=(\d+)", reply).groups()))
                        if previous is not None:
                            assert all(a > b for a, b in zip(now, previous)), (now, previous)
                        previous = now
                        time.sleep(.25)
                    results.append({"boot": repeat, "domain": f"rtos{i-1}",
                                    "MCU": i, "ticks": now[0], "heartbeat": now[1],
                                    "uart_irqs": now[2]})
                vm.qmp("stop")
            vm.qmp("cont")
            if args.display == "cocoa":
                for i, serial in enumerate(serials[:2]):
                    serial.command(
                        "printf '\\033[2J\\033[H'; printf 'Linux%d / %d x Cortex-A55 / RAM %s\\n'; "
                        "uname -m; nproc; head -3 /proc/meminfo; cat /tmp/domain" %
                        (i, 8 if i == 0 else 4, "1 GiB" if i == 0 else "512 MiB"), b"# ")
                for i, serial in enumerate(serials[2:], 1):
                    serial.command("uname", f"mcu{i}> ".encode())
                    serial.command("ticks", f"mcu{i}> ".encode())
            (out / "results.json").write_text(json.dumps(results, indent=2) + "\n")
            (out / "result.json").write_text(json.dumps({
                "status": "PASS", "qemu_pid": vm.proc.pid,
                "display": args.display, "thread": args.thread, "consoles": 5, "vcpus": 15,
                "checks": ["two Linux shells", "three FreeRTOS shells",
                           "private rootfs", "ticks/heartbeat/IRQ progress", "system reset"],
                "binary_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest()
            }, indent=2) + "\n")
            print("PASS five consoles: 8+4 A55 Linux + 3 R5F FreeRTOS, private roots, ticks, IRQs, reset", flush=True)
            if args.hold_seconds:
                print("Verified five-console VM PID:", vm.proc.pid, flush=True)
                try:
                    time.sleep(args.hold_seconds)
                except KeyboardInterrupt:
                    pass
            vm.qmp("quit")
            assert vm.proc.wait(timeout=5) == 0
        finally:
            vm.close()
            for serial in serials:
                serial.close()
    (out / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print("PASS 8+4 A55 Linux (1G/512M) + 3 R5F FreeRTOS: five shells, ticks, IRQs, reset")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("binary", "linux", "firmware", "output"):
        parser.add_argument(name, type=pathlib.Path)
    parser.add_argument("--thread", choices=["single", "multi"], default="multi")
    parser.add_argument("--display", choices=["none", "cocoa"], default="none")
    parser.add_argument("--hold-seconds", type=int, default=0)
    args = parser.parse_args()
    if args.hold_seconds < 0:
        parser.error("--hold-seconds must be nonnegative")
    run(args)
