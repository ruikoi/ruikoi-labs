#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Execute small bare-metal stores through TCG; no guest toolchain needed."""
import argparse
import json
import pathlib
import selectors
import struct
import subprocess
import tempfile
import time


def smoke(build, arch, riscv_machine="virt"):
    base = 0x40200000 if arch == "aarch64" else 0x80200000
    marker = 0x4152 if arch == "aarch64" else 0x52
    # AArch64: adr x0, .; movz w1, #marker;
    #          str w1, [x0, #256]; wfi; b .
    # RV64: auipc t0, 0; addi t1, zero, marker;
    #       sw t1, 256(t0); wfi; jal zero, 0
    words = ([0x10000000, 0x52800001 | marker << 5,
              0xB9010001, 0xD503207F, 0x14000000]
             if arch == "aarch64" else
             [0x00000297, 0x05200313, 0x1062A023, 0x10500073, 0x0000006F])
    with tempfile.TemporaryDirectory(prefix="qemu-baseline-") as tmp:
        tmp = pathlib.Path(tmp)
        payload = tmp / "payload.bin"
        payload.write_bytes(struct.pack("<5I", *words))
        machine = "virt" if arch == "aarch64" else riscv_machine
        cmd = [str(build / ("qemu-system-" + arch)), "-M", machine,
               "-accel", "tcg,thread=single", "-m", "128M",
               "-display", "none", "-serial", "none", "-monitor", "none",
               "-qmp", "stdio", "-S", "-nodefaults",
               "-device", f"loader,file={payload},addr={base},cpu-num=0,force-raw=on"]
        cmd += ["-cpu", "cortex-a53"] if arch == "aarch64" else ["-bios", "none"]
        with (tmp / "stderr.log").open("w+") as log:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=log, bufsize=0)
            selector = selectors.DefaultSelector()
            selector.register(proc.stdout, selectors.EVENT_READ)
            buffered = bytearray()

            def receive():
                deadline = time.monotonic() + 10
                while b"\n" not in buffered:
                    if not selector.select(max(0, deadline - time.monotonic())):
                        raise TimeoutError("QMP reply timed out")
                    chunk = proc.stdout.read(4096)
                    if not chunk:
                        raise RuntimeError("QEMU exited before QMP reply")
                    buffered.extend(chunk)
                line, _, rest = buffered.partition(b"\n")
                buffered[:] = rest
                return json.loads(line)

            def qmp(command, **arguments):
                proc.stdin.write((json.dumps({"execute": command,
                                             "arguments": arguments}) + "\n").encode())
                while True:
                    message = receive()
                    if "error" in message:
                        raise RuntimeError(message["error"])
                    if "return" in message:
                        return message["return"]

            try:
                assert "QMP" in receive()
                qmp("qmp_capabilities")
                dump = tmp / "memory.bin"
                qmp("pmemsave", val=base + 256, size=4, filename=str(dump))
                assert dump.read_bytes() == bytes(4), "marker must start at zero"
                qmp("cont")
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    qmp("pmemsave", val=base + 256, size=4, filename=str(dump))
                    if dump.read_bytes() == struct.pack("<I", marker):
                        break
                    time.sleep(0.02)
                else:
                    raise AssertionError(f"{arch}: guest store was not observed")
                qmp("stop")
                qmp("quit")
                assert proc.wait(timeout=5) == 0
                print(f"PASS {arch}: TCG guest wrote {marker:#x} to {base + 256:#x}")
            except Exception:
                log.flush()
                log.seek(0)
                print(log.read())
                raise
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                selector.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build", type=pathlib.Path)
    parser.add_argument("--riscv-machine", default="virt")
    args = parser.parse_args()
    for architecture in ("aarch64", "riscv64"):
        smoke(args.build.resolve(), architecture, args.riscv_machine)
