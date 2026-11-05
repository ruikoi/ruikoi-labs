#!/bin/sh
# SPDX-License-Identifier: MIT
set -eu
tool=${CROSS_COMPILE:-riscv64-linux-gnu-}
"${tool}gcc" -Os -march=rv64imac_zicsr_zifencei -mabi=lp64 -mcmodel=medany \
  -mno-relax -ffreestanding -fno-builtin -fno-stack-protector -fno-pie -no-pie -static \
  -ffunction-sections -fdata-sections -nostdlib -nostartfiles \
  -Wl,--no-relax,--gc-sections,-T,link.ld,-Map,rom.map \
  start.S rom.c -o rom.elf
"${tool}objcopy" -O binary rom.elf rom.bin
"${tool}objdump" -d rom.elf > rom.dis
"${tool}size" rom.elf
sha256sum rom.elf rom.bin
