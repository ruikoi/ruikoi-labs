#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate configurable domains, private roots, shared aliases and reset."""
import argparse
import importlib.util
import pathlib
import struct
import subprocess
import tempfile
import time

spec = importlib.util.spec_from_file_location(
    "smoke", pathlib.Path(__file__).with_name("hetero-smoke.py"))
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def image(arch, token, slot):
    if arch == "rv32":
        words = [0x100102b7, token << 20 | 0x313, 0x628023,
                 0x200002b7, 0x628023 | slot << 7,
                 0x400002b7, (65 + token) << 20 | 0x313, 0x628023, 0x6f]
        return struct.pack("<9I", *words)
    # ARM state: publish private/shared tokens and print one UART byte.
    if arch == "cortex-r5f":
        words = [0xe59f0020, 0xe3a01000 | token, 0xe5801000,
                 0xe59f0018, 0xe5801000 | slot,
                 0xe59f0014, 0xe3a01000 | (65 + token), 0xe5801000,
                 0xeafffffe, 0xbf00bf00, 0x10010000, 0x20000000,
                 0x09000000]
        return struct.pack("<13I", *words)
    data = bytearray(0x130)
    struct.pack_into("<II", data, 0, 0x100ff000, 0x10000101)
    struct.pack_into("<10H", data, 0x100, 0x4804, 0x2100 | token, 0x6001,
                     0x4804, 0x6001 | (slot // 4) << 6,
                     0x4804, 0x2100 | (65 + token), 0x7001, 0xe7fe, 0xbf00)
    struct.pack_into("<3I", data, 0x114, 0x10010000, 0x20000000, 0x40000000)
    return data


def run(binary, thread):
    with tempfile.TemporaryDirectory(prefix="composed-virt-") as tmp:
        tmp = pathlib.Path(tmp)
        cmd = [str(binary), "-M", "multiarch-virt", "-accel", f"tcg,thread={thread}",
               "-display", "none", "-monitor", "none", "-qmp", "stdio", "-S",
               "-object", "memory-backend-ram,id=ipc,size=1M"]
        for i, cores in enumerate((8, 4)):
            cmd += ["-domain", f"id=linux{i},platform=arm-virt,cpu=cortex-a55,"
                    f"cpus={cores},ram=" + ("1G" if i == 0 else "512M")]
        for i, arch in enumerate(("cortex-r5f", "rv32", "cortex-m3")):
            fw = tmp / f"mcu{i}.bin"
            fw.write_bytes(image(arch, i + 1, i * 4))
            platform = "riscv-mcu" if arch == "rv32" else "arm-mcu"
            cmd += ["-chardev", f"file,id=u{i},path={tmp}/u{i}.log",
                    "-domain", f"id=mcu{i},platform={platform},cpu={arch},"
                    f"cpus=1,ram=16M,chardev=u{i}", "-domain-loader",
                    f"domain=mcu{i},firmware={fw}", "-domain-map",
                    f"domain=mcu{i},memory=ipc,addr=0x20000000"]
        with (tmp / "qemu.log").open("w+") as log:
            vm = smoke.QEMU(cmd, log)
            try:
                cpus = vm.qmp("query-cpus-fast")
                assert len(cpus) == 15, cpus
                assert [c["target"] for c in cpus[12:]] == ["arm", "riscv32", "arm"]
                def read(cpu, addr, count=1):
                    return vm.qmp("human-monitor-command", **{
                        "cpu-index": cpu,
                        "command-line": f"xp /{count}wx {addr:#x}"})
                for repeat in (1, 2):
                    if repeat == 2:
                        vm.qmp("system_reset")
                    vm.qmp("cont")
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        if all((tmp / f"u{i}.log").read_bytes() == bytes([66+i])*repeat
                               for i in range(3)):
                            break
                        time.sleep(.02)
                    else:
                        raise AssertionError("MCU UART/reset markers missing")
                    vm.qmp("stop")
                    for i in range(3):
                        assert f"0x{i+1:08x}" in read(12+i, 0x10010000)
                        shared = read(12+i, 0x20000000, 3)
                        assert all(f"0x{n:08x}" in shared for n in (1, 2, 3)), shared
                vm.qmp("quit")
                assert vm.proc.wait(timeout=5) == 0
            except Exception:
                log.flush(); log.seek(0); print(log.read())
                raise
            finally:
                vm.close()
    print(f"PASS {thread}: 15 CPUs, mixed R5F/RV32/M3, private RAM, shared aliases, reset")


def guards(binary):
    base = [str(binary), "-display", "none", "-monitor", "none", "-nodefaults"]
    valid = "id=d,platform=arm-virt,cpu=cortex-a55,cpus=1,ram=64M"
    cases = [
        (["-M", "none", "-domain", valid], "require multiarch-virt"),
        (["-M", "multiarch-virt"], "at least one -domain"),
        (["-M", "multiarch-virt", "-domain", valid.replace("cpus=1", "cpus=0")], "1..64"),
        (["-M", "multiarch-virt", "-domain", valid, "-m", "64M"], "instead of"),
        (["-M", "multiarch-virt", "-domain", valid, "-object",
          "memory-backend-ram,id=ipc,size=1M", "-domain-map",
          "domain=d,memory=ipc,addr=0x40000000"], "overlaps"),
    ]
    for args, expected in cases:
        p = subprocess.run(base + args, capture_output=True, text=True, timeout=10)
        assert p.returncode == 1 and expected in p.stderr, (args, p.stderr)
    print("PASS invalid configuration guards")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=pathlib.Path)
    args = parser.parse_args()
    binary = args.binary.resolve()
    guards(binary)
    for mode in ("single", "multi"):
        run(binary, mode)
