/* SPDX-License-Identifier: MIT */
OUTPUT_ARCH(riscv)
ENTRY(_start)
PHDRS { rom PT_LOAD FLAGS(5); ram PT_LOAD FLAGS(6); }
SECTIONS {
    . = 0x04418000;
    .text : { KEEP(*(.vectors)) *(.text.boot) *(.text*) } :rom
    .rodata : { *(.rodata*) *(.srodata*) } :rom
    ASSERT(. <= 0x04430000, "ROM overflow")
    . = 0x0c03c000;
    .bss (NOLOAD) : {
        __bss_start = .;
        *(.bss*) *(.sbss*) *(COMMON)
        . = ALIGN(8);
        __bss_end = .;
    } :ram
    ASSERT(. <= 0x0c03e000, "ROM workspace overflow")
    __stack_top = 0x0c040000;
    /DISCARD/ : { *(.comment) *(.eh_frame*) *(.riscv.attributes) }
}
