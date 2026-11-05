#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Execute MCU firmware/reset on canonical and compatibility virt machines."""
import argparse
import importlib.util
import pathlib
import struct
import sys
import tempfile
import time

spec = importlib.util.spec_from_file_location(
    "smoke", pathlib.Path(__file__).with_name("hetero-smoke.py"))
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def firmware(profile):
    if profile == "rv32-mcu":
        # LUI UART base; ADDI 'R'; SB; JAL to self.
        words = (0x400002b7, 0x05200313, 0x00628023, 0x6f)
        return struct.pack("<4I", *words), b"R"
    image = bytearray(0x110)
    struct.pack_into("<II", image, 0, 0x100ff000, 0x10000101)
    # Thumb LDR literal; MOVS 'A'; STRB; B to self; padding; UART literal.
    struct.pack_into("<6HI", image, 0x100,
                     0x4802, 0x2141, 0x7001, 0xe7fe, 0xbf00, 0xbf00, 0x40000000)
    return image, b"A"


def run(binary, machine, profile, thread="single"):
    with tempfile.TemporaryDirectory(prefix="multiarch-virt-") as directory:
        directory = pathlib.Path(directory)
        image, marker = firmware(profile)
        (directory / "mcu.bin").write_bytes(image)
        uart = directory / "mcu.uart"
        machine_opts = (f"{machine},mcu={profile},"
                        f"mcu-firmware={directory / 'mcu.bin'}")
        command = [str(binary), "-M", machine_opts,
                   "-accel", f"tcg,thread={thread}", "-display", "none",
                   "-monitor", "none", "-nic", "none", "-serial", "null",
                   "-serial", f"file:{uart}",
                   "-qmp", "stdio", "-S"]
        if machine.endswith("riscv"):
            command += ["-bios", "none"]
        with (directory / "qemu.log").open("w+") as log:
            qemu = smoke.QEMU(command, log)
            try:
                cpus = qemu.qmp("query-cpus-fast")
                assert len(cpus) == 2, cpus
                if sys.platform != "darwin":
                    assert len({cpu["thread-id"] for cpu in cpus}) == (
                        2 if thread == "multi" else 1), cpus
                assert cpus[1]["target"] == (
                    "riscv32" if profile == "rv32-mcu" else "arm"), cpus
                for repeat in (1, 2):
                    if repeat == 2:
                        qemu.qmp("system_reset")
                    qemu.qmp("cont")
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        if (uart.exists() and
                                uart.read_bytes() == marker * repeat):
                            break
                        time.sleep(0.01)
                    else:
                        raise AssertionError(
                            f"MCU UART/reset failed: {machine}/{profile}")
                    qemu.qmp("stop")
                qemu.qmp("quit")
                assert qemu.proc.wait(timeout=5) == 0
                print(f"PASS {machine} + {profile}: ISA, private UART, reset")
            except Exception:
                log.flush()
                log.seek(0)
                print(log.read())
                raise
            finally:
                qemu.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=pathlib.Path)
    parser.add_argument("--thread", choices=["single", "multi"], default="single")
    args = parser.parse_args()
    for name in ("multiarch-virt-arm", "multiarch-virt-riscv",
                 "hetero-virt-arm", "hetero-virt-riscv"):
        for core in ("cortex-m3-arm-cpu", "rv32-mcu"):
            run(args.binary.resolve(), name, core, args.thread)
