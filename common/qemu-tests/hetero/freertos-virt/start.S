/* SPDX-License-Identifier: MIT */
.syntax unified
.arm
.section .text.start,"ax"
.global _start
_start:
    b reset
    b fault
    b FreeRTOS_SWI_Handler
    b fault
    b fault
    b fault
    b FreeRTOS_IRQ_Handler
    b fault
reset:
    cpsid if
    ldr r0, =_start
    mcr p15, 0, r0, c12, c0, 0
    mrc p15, 0, r0, c1, c0, 0
    bic r0, r0, #1
    mcr p15, 0, r0, c1, c0, 0
    mrc p15, 0, r0, c1, c0, 2
    orr r0, r0, #0xf00000
    mcr p15, 0, r0, c1, c0, 2
    isb
    mov r0, #0x40000000
    vmsr fpexc, r0
    cps #0x12
    ldr sp, =__irq_stack_top
    cps #0x13
    ldr sp, =__stack_top
    ldr r0, =__bss_start
    ldr r1, =__bss_end
    mov r2, #0
1:  cmp r0, r1
    strlo r2, [r0], #4
    blo 1b
    bl main
fault:
    b fault
