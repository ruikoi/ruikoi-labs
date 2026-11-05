#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Single-process dual-ISA hardware acceptance. Python stdlib only.

Emit tiny, position-independent guest programs from documented instruction
encodings. Both CPUs write private markers, publish a shared token, read the
other ISA's token and exchange acknowledgements before completing.
"""
import argparse
import json
import pathlib
import selectors
import struct
import subprocess
import tempfile
import time

CPU_TYPES = {"arm": "cortex-a53-arm-cpu", "riscv": "rv64-riscv-cpu"}
TOKENS = {"arm": 0x4152, "riscv": 0x5256}
DONE = 0x600D


class Program:
    def __init__(self):
        self.words, self.labels, self.fixups = [], {}, []

    def emit(self, word):
        self.words.append(word)

    def label(self, name):
        self.labels[name] = len(self.words)

    def branch(self, label, encode):
        self.fixups.append((len(self.words), label, encode))
        self.emit(0)

    def binary(self):
        for index, label, encode in self.fixups:
            self.words[index] = encode((self.labels[label] - index) * 4)
        return struct.pack("<" + "I" * len(self.words), *self.words)


def arm_program(shared, publish=True, own_token=0x4152,
                peer_token=0x5256, slot=0):
    p = Program()
    mov = lambda rd, imm: 0x52800000 | imm << 5 | rd  # MOVZ Wd, #imm
    store = lambda rt, rn, off: 0xB9000000 | off // 4 << 10 | rn << 5 | rt
    load = lambda rt, rn, off: 0xB9400000 | off // 4 << 10 | rn << 5 | rt
    cmp = lambda rn, rm: 0x6B00001F | rm << 16 | rn << 5
    bne = lambda off: 0x54000001 | ((off // 4) & 0x7FFFF) << 5
    p.emit(0xD2A00000 | 0x1000 << 5)  # MOVZ X0, #0x1000, LSL #16
    p.emit(mov(1, own_token))
    p.emit(store(1, 0, 0x800))
    p.emit(0xD2A00002 | (shared >> 16) << 5)  # shared base in X2
    p.emit(store(1, 2, slot * 4) if publish else 0xD503201F)  # NOP negative control
    p.emit(0xD5033FBF)  # DMB SY
    p.emit(mov(5, peer_token))
    p.label("token")
    p.emit(load(4, 2, (1 - slot) * 4))
    p.emit(cmp(4, 5))
    p.branch("token", bne)
    p.emit(store(4, 0, 0x804))  # proof the peer token was read
    p.emit(mov(3, 1))
    p.emit(store(3, 2, 8 + slot * 4))
    p.emit(0xD5033FBF)
    p.label("ack")
    p.emit(load(4, 2, 8 + (1 - slot) * 4))
    p.emit(cmp(4, 3))
    p.branch("ack", bne)
    p.emit(mov(1, DONE))
    p.emit(store(1, 0, 0x808))
    p.label("halt")
    p.emit(0xD503207F)  # WFI
    p.branch("halt", lambda off: 0x14000000 | ((off // 4) & 0x3FFFFFF))
    return p.binary()


def rv_branch(rs1, rs2, funct3, off):
    imm = off & 0x1FFF
    return (0x63 | funct3 << 12 | rs1 << 15 | rs2 << 20 |
            ((imm >> 12) & 1) << 31 | ((imm >> 5) & 0x3F) << 25 |
            ((imm >> 1) & 0xF) << 8 | ((imm >> 11) & 1) << 7)


def rv_program(shared, publish=True, own_token=0x5256,
               peer_token=0x4152, slot=1, irq=False, paging=False):
    p = Program()
    addi = lambda rd, rs, imm: 0x13 | rd << 7 | rs << 15 | (imm & 0xFFF) << 20
    load = lambda rd, rs, off: 0x2003 | rd << 7 | rs << 15 | off << 20  # LW
    store = lambda rt, rn, off: (0x2023 | rn << 15 | rt << 20 |
                                (off & 31) << 7 | (off >> 5) << 25)  # SW

    def li(rd, value):
        upper = (value + 0x800) >> 12
        p.emit(upper << 12 | rd << 7 | 0x37)  # LUI
        p.emit(addi(rd, rd, value - (upper << 12)))

    li(5, 0x10000000)
    li(6, own_token)
    # RV stores use signed 12-bit offsets: address markers through t0 + 0x800.
    p.emit(addi(5, 5, 0x400))
    p.emit(store(6, 5, 0x400))
    if paging:
        # Exercise a 4 KiB Sv39 page beyond the first 1 KiB TCG granule.
        p.emit(addi(28, 0, -1))
        p.emit(0x3B001073 | 28 << 15)  # pmpaddr0: all memory
        li(28, 0x1F)
        p.emit(0x3A001073 | 28 << 15)  # pmpcfg0: NAPOT RWX
        li(28, 0x10010)  # root page-table PPN
        p.emit(addi(29, 0, 8))
        p.emit(0x03C01013 | 29 << 15 | 29 << 7)  # SLLI t4, t4, 60
        p.emit(0x6033 | 28 << 7 | 28 << 15 | 29 << 20)  # OR t3, t3, t4
        p.emit(0x18001073 | 28 << 15)  # CSRW satp: Sv39
        p.emit(0x12000073)  # SFENCE.VMA
        li(28, 0x1800)
        p.emit(0x30003073 | 28 << 15)  # Clear MPP
        li(28, 0x20800)
        p.emit(0x30002073 | 28 << 15)  # MPRV, MPP=S
        li(28, 0x60000C00)
        p.emit(store(6, 28, 0))
        p.emit(load(29, 28, 0))
        li(28, 0x20000)
        p.emit(0x30003073 | 28 << 15)  # Clear MPRV before physical access
        p.emit(store(29, 5, 0x410))
    if irq:
        li(28, 0x10000200)
        p.emit(0x30501073 | 28 << 15)  # CSRW mtvec, t3
        li(28, 0x02000000)
        p.emit(addi(29, 0, 1))
        p.emit(store(29, 28, 0))  # Pend this core's private MSWI.
        p.emit(0x30446073)  # CSRRSI mie, 8: MSIE
        p.emit(0x30046073)  # CSRRSI mstatus, 8: MIE
        p.label("irq")
        p.emit(load(28, 5, 0x40C))
        p.branch("irq", lambda off: rv_branch(28, 0, 0, off))
    li(7, shared)
    p.emit(store(6, 7, slot * 4) if publish else 0x13)
    p.emit(0x0330000F)  # FENCE RW,RW
    li(29, peer_token)
    p.label("token")
    p.emit(load(28, 7, (1 - slot) * 4))
    p.branch("token", lambda off: rv_branch(28, 29, 1, off))
    p.emit(store(28, 5, 0x404))
    p.emit(addi(30, 0, 1))
    p.emit(store(30, 7, 8 + slot * 4))
    p.emit(0x0330000F)
    p.label("ack")
    p.emit(load(28, 7, 8 + (1 - slot) * 4))
    p.branch("ack", lambda off: rv_branch(28, 30, 1, off))
    li(31, DONE)
    p.emit(store(31, 5, 0x408))
    p.label("halt")
    p.emit(0x10500073)  # WFI

    def jump(off):
        imm = off & 0x1FFFFF
        return (0x6F | ((imm >> 20) & 1) << 31 | ((imm >> 1) & 0x3FF) << 21 |
                ((imm >> 11) & 1) << 20 | ((imm >> 12) & 0xFF) << 12)

    p.branch("halt", jump)
    if irq:
        assert len(p.words) * 4 <= 0x200
        while len(p.words) * 4 < 0x200:
            p.emit(0x13)
        li(28, 0x1000080C)
        p.emit(load(29, 28, 0))
        p.emit(addi(29, 29, 1))
        p.emit(store(29, 28, 0))
        li(28, 0x02000000)
        p.emit(store(0, 28, 0))  # Clear only this core's MSWI.
        p.emit(0x30200073)  # MRET
    image = p.binary()
    if paging:
        tables = bytearray(0x13000)
        tables[:len(image)] = image
        struct.pack_into("<Q", tables, 0x10000 + 1 * 8, (0x10011 << 10) | 1)
        struct.pack_into("<Q", tables, 0x11000 + 256 * 8, (0x10012 << 10) | 1)
        struct.pack_into("<Q", tables, 0x12000, (0x10000 << 10) | 0xCF)
        image = bytes(tables)
    return image


class QEMU:
    def __init__(self, command, log):
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=log, bufsize=0)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.proc.stdout, selectors.EVENT_READ)
        self.buffer = bytearray()
        try:
            assert "QMP" in self.receive()
            self.qmp("qmp_capabilities")
        except Exception:
            self.close()
            raise

    def receive(self):
        deadline = time.monotonic() + 10
        while b"\n" not in self.buffer:
            if not self.selector.select(max(0, deadline - time.monotonic())):
                raise TimeoutError("QMP response timed out")
            chunk = self.proc.stdout.read(4096)
            if not chunk:
                raise RuntimeError(f"QEMU exited: {self.proc.poll()}")
            self.buffer.extend(chunk)
        line, _, rest = self.buffer.partition(b"\n")
        self.buffer[:] = rest
        return json.loads(line)

    def qmp(self, command, **arguments):
        self.proc.stdin.write((json.dumps({"execute": command,
                                         "arguments": arguments}) + "\n").encode())
        while True:
            reply = self.receive()
            if "error" in reply:
                raise RuntimeError(reply["error"])
            if "return" in reply:
                return reply["return"]

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=5)
        self.selector.close()


def run_case(executable, order, entries, negative=False):
    with tempfile.TemporaryDirectory(prefix="qemu-hetero-") as directory:
        tmp = pathlib.Path(directory)
        command = [str(executable), "-M", "hetero," + ",".join(
            f"cpu{i}={CPU_TYPES[arch]}" for i, arch in enumerate(order)),
            "-accel", "tcg,thread=single", "-display", "none", "-serial", "none",
            "-monitor", "none", "-qmp", "stdio", "-nodefaults", "-S"]
        for i, arch in enumerate(order):
            payload = tmp / f"cpu{i}-{arch}.bin"
            generator = arm_program if arch == "arm" else rv_program
            payload.write_bytes(generator(0x20000000 + i * 0x10000000,
                                          publish=not (negative and arch == "riscv")))
            command += ["-device", f"loader,file={payload},addr={entries[i]},"
                        f"cpu-num={i},force-raw=on"]
        with (tmp / "stderr.log").open("w+") as log:
            vm = None
            try:
                vm = QEMU(command, log)
                cpus = vm.qmp("query-cpus-fast")
                assert len(cpus) == 2, cpus
                assert [c["cpu-index"] for c in cpus] == [0, 1], cpus
                actual_types = [c["qom-type"] for c in cpus]
                assert actual_types == [CPU_TYPES[a] for a in order], cpus
                expected_targets = ["aarch64" if a == "arm" else "riscv64" for a in order]
                assert [c["target"] for c in cpus] == expected_targets, cpus
                # One QEMU PID and one shared TCG thread serving both CPUs.
                assert cpus[0]["thread-id"] == cpus[1]["thread-id"], cpus
                dump = tmp / "dump.bin"

                def memory(address, count):
                    vm.qmp("pmemsave", val=address, size=4 * count, filename=str(dump))
                    return struct.unpack("<" + "I" * count, dump.read_bytes())

                assert memory(0x50000000, 4) == (0, 0, 0, 0)
                for i in range(2):
                    assert memory(0x40000800 + i * 0x1000000, 3) == (0, 0, 0)
                vm.qmp("cont")
                deadline = time.monotonic() + (1 if negative else 10)
                while time.monotonic() < deadline:
                    private = [memory(0x40000800 + i * 0x1000000, 3) for i in range(2)]
                    if all(values[2] == DONE for values in private):
                        break
                    time.sleep(0.02)
                vm.qmp("stop")
                private = [memory(0x40000800 + i * 0x1000000, 3) for i in range(2)]
                shared = memory(0x50000000, 4)
                if negative:
                    assert [v[0] for v in private] == [TOKENS[a] for a in order], private
                    assert all(v[2] == 0 for v in private), private
                    assert shared[1] == 0, shared
                else:
                    for i, arch in enumerate(order):
                        expected = (TOKENS[arch], TOKENS[order[1 - i]], DONE)
                        assert private[i] == expected, private
                    assert shared == (TOKENS["arm"], TOKENS["riscv"], 1, 1), shared
                vm.qmp("quit")
                assert vm.proc.wait(timeout=5) == 0
                print(json.dumps({"result": "PASS", "order": order,
                                  "entries": [hex(e) for e in entries],
                                  "negative_control": negative, "pid": vm.proc.pid,
                                  "cpus": cpus, "private": private, "shared": shared}))
            except Exception:
                log.flush()
                log.seek(0)
                print(log.read())
                raise
            finally:
                if vm:
                    vm.close()


def guards(binary):
    cases = [
        ("hetero,cpu0=not-a-cpu", "tcg,thread=single", [], "unsupported hetero CPU"),
        ("hetero", "tcg,thread=multi", ["-icount", "shift=0"],
         "No MTTCG when icount is enabled"),
        ("hetero", "tcg,thread=single", ["-m", "2M"], "fixed 1 MiB"),
    ]
    for machine, accel, extra, expected in cases:
        reply = subprocess.run([str(binary), "-M", machine, "-accel", accel,
                                "-display", "none", "-serial", "none",
                                "-monitor", "none", "-nodefaults"] + extra,
                               capture_output=True, text=True, timeout=10)
        assert reply.returncode == 1 and expected in reply.stderr, reply.stderr
        print(json.dumps({"result": "PASS", "guard": expected}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build", type=pathlib.Path)
    parser.add_argument("--emit-dir", type=pathlib.Path,
                        help="Write default guest payloads for manual use, then exit")
    args = parser.parse_args()
    binary = args.build.resolve() / "qemu-system-hetero"
    if args.emit_dir:
        args.emit_dir.mkdir(parents=True, exist_ok=True)
        (args.emit_dir / "arm.bin").write_bytes(arm_program(0x20000000))
        (args.emit_dir / "riscv.bin").write_bytes(rv_program(0x30000000))
        print(f"Guest payloads written to {args.emit_dir}")
        raise SystemExit(0)
    guards(binary)
    run_case(binary, ["arm", "riscv"], [0x10000000, 0x10000000])
    run_case(binary, ["riscv", "arm"], [0x10000100, 0x10000200])
    run_case(binary, ["arm", "riscv"], [0x10000000, 0x10000000], negative=True)
