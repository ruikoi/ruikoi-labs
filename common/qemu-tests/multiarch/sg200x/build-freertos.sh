#!/bin/sh
# SPDX-License-Identifier: MIT
# Builds an external validation app against an unchanged FreeRTOS 10.4.6 tree.
set -eu
if [ "$#" != 3 ]; then
    echo "usage: $0 FREERTOS_SOURCE CROSS_CC OUTPUT_ELF" >&2
    exit 1
fi
src=$(cd "$1" && pwd)
cc=$2
output=$3
demo=$(CDPATH= cd -- "$(dirname -- "$0")/freertos-demo" && pwd)
port=$src/portable/GCC/RISC-V
"$cc" -march=rv64imac_zicsr_zifencei -mabi=lp64 -mcmodel=medany \
    -mno-relax -fno-stack-protector -nostdlib -static -no-pie -Os -g \
    -ffreestanding -fno-builtin -ffunction-sections -fdata-sections \
    -I"$demo" -I"$src/include" -I"$port" \
    -I"$port/chip_specific_extensions/RV32I_CLINT_no_extensions" \
    -DportasmHANDLE_INTERRUPT=demo_interrupt -T"$demo/link.ld" \
    -Wl,--gc-sections "$demo/start.S" "$demo/main.c" "$src/tasks.c" \
    "$src/list.c" "$src/queue.c" "$src/portable/MemMang/heap_4.c" \
    "$port/port.c" "$port/portASM.S" -o "$output"
