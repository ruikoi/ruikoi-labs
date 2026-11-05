/* SPDX-License-Identifier: MIT */
ENTRY(_start)
MEMORY { SRAM (rwx) : ORIGIN = 0x0c000000, LENGTH = 0x10000 }
SECTIONS {
 .text : { *(.text.start) *(.text*) } > SRAM
 .rodata : { *(.rodata*) } > SRAM
 .data : { *(.data*) } > SRAM
 . = ALIGN(8);
 PROVIDE(__global_pointer$ = . + 0x800);
 .sdata : { *(.sdata*) } > SRAM
 .bss (NOLOAD) : { . = ALIGN(8); __bss_start = .; *(.bss*) *(.sbss*) *(COMMON) . = ALIGN(8); __bss_end = .; } > SRAM
 .stack (NOLOAD) : { . += 4096; __stack_top = .; } > SRAM
 /DISCARD/ : { *(.eh_frame*) *(.comment*) }
}
