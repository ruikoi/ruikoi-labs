#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Execute all ordered pairs of ARM/RISC-V CPU/MCU profiles in one Machine."""
import argparse
import importlib.util
import itertools
import json
import pathlib
import struct
import sys
import tempfile
import time

spec = importlib.util.spec_from_file_location(
    "smoke", pathlib.Path(__file__).with_name("hetero-smoke.py"))
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)

PROFILES = {
    "arm-cpu": ("cortex-a53-arm-cpu", "cortex-a53-arm-cpu", "aarch64"),
    "riscv-cpu": ("rv64-riscv-cpu", "rv64-riscv-cpu", "riscv64"),
    "arm-mcu": ("cortex-m3-arm-cpu", "cortex-m3-arm-cpu", "arm"),
    "riscv-mcu": ("rv32-mcu", "rv32-riscv-cpu", "riscv32"),
}


class Thumb:
    def __init__(self):
        self.words, self.labels, self.branches, self.literals = [], {}, [], []

    def emit(self, word):
        self.words.append(word)

    def label(self, name):
        self.labels[name] = len(self.words) * 2

    def branch(self, name, base):
        self.branches.append((len(self.words), name, base))
        self.emit(0)

    def literal(self, rd, value):
        self.literals.append((len(self.words), rd, value))
        self.emit(0)

    def binary(self):
        for index, label, base in self.branches:
            off = self.labels[label] - (index * 2 + 4)
            assert off % 2 == 0
            mask = 0xFF if base == 0xD100 else 0x7FF
            self.words[index] = base | ((off // 2) & mask)
        if len(self.words) % 2:
            self.emit(0xBF00)
        for index, rd, value in self.literals:
            off = len(self.words) * 2 - ((index * 2 + 4) & ~3)
            assert 0 <= off <= 1020 and off % 4 == 0
            self.words[index] = 0x4800 | rd << 8 | off // 4
            self.emit(value & 0xFFFF)
            self.emit(value >> 16)
        return struct.pack("<" + "H" * len(self.words), *self.words)


def thumb_program(shared, own, peer, slot):
    p = Thumb()
    store = lambda rt, rn, off: 0x6000 | off // 4 << 6 | rn << 3 | rt
    load = lambda rt, rn, off: 0x6800 | off // 4 << 6 | rn << 3 | rt
    cmp = lambda rn, rm: 0x4280 | rm << 3 | rn
    p.literal(0, 0x10000800)
    p.literal(1, own)
    p.emit(store(1, 0, 0))
    # Pend and handle IRQ 0 in this MCU's own NVIC.
    p.literal(3, 0xE000E100)  # ISER
    p.emit(0x2401)  # MOVS r4, #1
    p.emit(store(4, 3, 0))
    p.literal(3, 0xE000E200)  # ISPR
    p.emit(store(4, 3, 0))
    p.literal(2, shared)
    p.emit(store(1, 2, slot * 4))
    p.emit(0xF3BF)
    p.emit(0x8F5F)  # DMB SY
    p.literal(5, peer)
    p.label("token")
    p.emit(load(4, 2, (1 - slot) * 4))
    p.emit(cmp(4, 5))
    p.branch("token", 0xD100)  # BNE
    p.emit(store(4, 0, 4))
    p.emit(0x2301)  # MOVS r3, #1
    p.emit(store(3, 2, 8 + slot * 4))
    p.emit(0xF3BF)
    p.emit(0x8F5F)
    p.label("ack")
    p.emit(load(4, 2, 8 + (1 - slot) * 4))
    p.emit(cmp(4, 3))
    p.branch("ack", 0xD100)
    p.literal(1, smoke.DONE)
    p.emit(store(1, 0, 8))
    p.label("halt")
    p.emit(0xBF30)  # WFI
    p.branch("halt", 0xE000)
    code = p.binary()
    assert len(code) <= 0x100
    isr = Thumb()
    isr.literal(0, 0x1000080C)
    isr.emit(load(1, 0, 0))
    isr.emit(0x3101)  # ADDS r1, #1
    isr.emit(store(1, 0, 0))
    isr.emit(0x4770)  # BX LR: architectural exception return
    image = bytearray(0x200)
    struct.pack_into("<II", image, 0, 0x100FFF00, 0x10000101)
    struct.pack_into("<I", image, 16 * 4, 0x10000201)
    image[0x100:0x100 + len(code)] = code
    image += isr.binary()
    return bytes(image)


def run_case(binary, order, paging=False, thread="single"):
    own = [0x4101, 0x5202]  # Different markers even for duplicate ISA/model.
    with tempfile.TemporaryDirectory(prefix="qemu-mcu-matrix-") as directory:
        tmp = pathlib.Path(directory)
        command = [str(binary), "-M", "hetero," + ",".join(
            f"cpu{i}={PROFILES[profile][0]}"
            for i, profile in enumerate(order)),
            "-accel", f"tcg,thread={thread}", "-display", "none",
            "-serial", "none", "-monitor", "none", "-qmp", "stdio",
            "-nodefaults", "-S"]
        # Raw loader plus independent PC setters. Thumb image has real vectors.
        for i, profile in enumerate(order):
            shared = 0x20000000 + i * 0x10000000
            payload = tmp / f"slot{i}.bin"
            if profile == "arm-mcu":
                code = thumb_program(shared, own[i], own[1 - i], i)
                entry = 0x10000101
            else:
                generator = (smoke.arm_program if profile == "arm-cpu"
                             else smoke.rv_program)
                code = generator(shared, own_token=own[i],
                                 peer_token=own[1 - i], slot=i,
                                 **({"irq": True, "paging": paging and profile == "riscv-cpu"}
                                    if profile.startswith("riscv")
                                    else {}))
                entry = 0x10000000
            payload.write_bytes(code)
            command += ["-device", f"loader,file={payload},addr=0x10000000,"
                        f"cpu-num={i},force-raw=on"]
            command += ["-device", f"loader,addr={entry},cpu-num={i}"]
        with (tmp / "stderr.log").open("w+") as log:
            vm = None
            try:
                vm = smoke.QEMU(command, log)
                definitions = vm.qmp("query-cpu-definitions")
                types = {definition["typename"] for definition in definitions}
                assert all(profile[1] in types for profile in PROFILES.values())
                cpus = vm.qmp("query-cpus-fast")
                assert len(cpus) == 2, cpus
                thread_ids = {cpu["thread-id"] for cpu in cpus}
                if sys.platform != "darwin":
                    assert len(thread_ids) == (2 if thread == "multi" else 1), cpus
                for i, profile in enumerate(order):
                    assert cpus[i]["qom-type"] == PROFILES[profile][1], cpus
                    assert cpus[i]["target"] == PROFILES[profile][2], cpus
                    if profile.startswith("riscv"):
                        mmu = vm.qmp("qom-get", path=cpus[i]["qom-path"],
                                     property="mmu")
                        assert mmu == (profile == "riscv-cpu"), mmu
                dump = tmp / "memory.bin"

                def read(address, count):
                    vm.qmp("pmemsave", val=address, size=4 * count,
                           filename=str(dump))
                    return struct.unpack("<" + "I" * count, dump.read_bytes())

                assert read(0x50000000, 4) == (0, 0, 0, 0)
                for i in range(2):
                    assert read(0x40000800 + i * 0x1000000, 4) == (0, 0, 0, 0)
                vm.qmp("cont")
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    private = [read(0x40000800 + i * 0x1000000, 4)
                               for i in range(2)]
                    if all(v[2] == smoke.DONE for v in private):
                        break
                    time.sleep(0.02)
                vm.qmp("stop")
                private = [read(0x40000800 + i * 0x1000000, 4)
                           for i in range(2)]
                shared = read(0x50000000, 4)
                for i, profile in enumerate(order):
                    irq_count = 0 if profile == "arm-cpu" else 1
                    expected = (own[i], own[1 - i], smoke.DONE, irq_count)
                    assert private[i] == expected, (order, private)
                assert shared == (own[0], own[1], 1, 1), shared
                if paging:
                    for i, profile in enumerate(order):
                        if profile == "riscv-cpu":
                            base = 0x40000000 + i * 0x1000000
                            assert read(base + 0xC00, 1) == (own[i],)
                            assert read(base + 0x810, 1) == (own[i],)
                vm.qmp("quit")
                assert vm.proc.wait(timeout=5) == 0
                print(json.dumps({"result": "PASS", "profiles": order,
                                  "paging": paging, "thread": thread, "pid": vm.proc.pid, "cpus": cpus,
                                  "private": private, "shared": shared}))
            except Exception:
                log.flush()
                log.seek(0)
                print(log.read())
                raise
            finally:
                if vm:
                    vm.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build", type=pathlib.Path)
    parser.add_argument("--pair", nargs=2, choices=list(PROFILES))
    parser.add_argument("--thread", choices=["single", "multi"], default="single")
    parser.add_argument("--paging", action="store_true",
                        help="exercise RV64 Sv39 data translation")
    args = parser.parse_args()
    binary = args.build.resolve() / "qemu-system-hetero"
    pairs = [args.pair] if args.pair else itertools.product(PROFILES, repeat=2)
    for pair in pairs:
        run_case(binary, list(pair), args.paging, args.thread)
