/* SPDX-License-Identifier: MIT */
ENTRY(_start)
MEMORY { RAM (rwx) : ORIGIN = 0x10000000, LENGTH = 1M }
SECTIONS {
 .text : { *(.text.start) *(.text*) } > RAM
 .rodata : { *(.rodata*) } > RAM
 .data : { *(.data*) } > RAM
 .bss (NOLOAD) : { . = ALIGN(8); __bss_start = .; *(.bss*) *(COMMON) . = ALIGN(8); __bss_end = .; } > RAM
 .stack (NOLOAD) : { . += 4096; __irq_stack_top = .; . += 8192; __stack_top = .; } > RAM
 /DISCARD/ : { *(.eh_frame*) *(.comment*) *(.ARM.exidx*) }
}
