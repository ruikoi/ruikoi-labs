#!/bin/sh
# SPDX-License-Identifier: MIT
# Build three independently identified ARM Cortex-R5F FreeRTOS validation apps.
set -eu
if [ "$#" != 4 ]; then
    echo "usage: $0 FREERTOS_SOURCE CLANG LD_LLD OUTPUT_DIRECTORY" >&2
    exit 1
fi
src=$(cd "$1" && pwd)
cc=$2
ld=$3
out=$4
mkdir -p "$out"
demo=$(CDPATH= cd -- "$(dirname -- "$0")/freertos-virt" && pwd)
port=$src/portable/GCC/ARM_CR5
for id in 1 2 3; do
    "$cc" --target=arm-none-eabi -mcpu=cortex-r5 -marm -mfpu=vfpv3-d16 \
        -mfloat-abi=softfp -fno-stack-protector -nostdlib -static -Os -g \
        -ffreestanding -fno-builtin -ffunction-sections -fdata-sections \
        -I"$demo" -I"$src/include" -I"$port" -DMCU_ID="$id" \
        -T"$demo/link.ld" --ld-path="$ld" -Wl,--gc-sections \
        "$demo/start.S" "$demo/main.c" "$src/tasks.c" "$src/list.c" \
        "$src/queue.c" "$src/portable/MemMang/heap_4.c" \
        "$port/port.c" "$port/portASM.S" -o "$out/freertos-r5f-$id.elf"
done
