/* SPDX-License-Identifier: MIT */
    .option norvc
    .section .vectors,"ax"
    .globl _start
_start:
    j boot_start
    .org 0x20
    j rom_get_boot_src
    .org 0x40
    j rom_set_boot_src
    .org 0x60
    j rom_load_image
    .org 0x80
    j rom_flash_init
    .org 0xa0
    j rom_image_crc
    .org 0xc0
    j rom_get_retries
    .org 0xe0
    j rom_verify_rsa
    .org 0x100
    j rom_aes_decrypt
    .section .text.boot,"ax"
boot_start:
    li t0, 0x2000
    csrs mstatus, t0 /* FSBL's lp64d ABI expects FP enabled by BL1. */
    la sp, __stack_top
    la t0, __bss_start
    la t1, __bss_end
1:
    bgeu t0, t1, 2f
    sd zero, 0(t0)
    addi t0, t0, 8
    j 1b
2:
    call rom_main
3:
    wfi
    j 3b
