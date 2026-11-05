/* SPDX-License-Identifier: MIT */
/* External validation app against an unchanged FreeRTOS ARM_CR5 port. */
#include <stddef.h>
#include <stdint.h>
#include "FreeRTOS.h"
#include "task.h"
#ifndef MCU_ID
#define MCU_ID 1
#endif
#define REG(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define UART 0x09000000UL
#define GIC 0x08000000UL
#define TIMER 0x09010000UL
static volatile unsigned rx_read, rx_write, uart_irqs, beats;
static char rx[256];
static void putc_uart(char c)
{
    while (REG(UART + 0x18) & 0x20) {
    }
    REG(UART) = c;
}
static void puts_uart(const char *s)
{
    while (*s)
        putc_uart(*s++);
}
static void number(uint32_t n)
{
    char b[12];
    unsigned i = 0;
    do {
        b[i++] = '0' + n % 10;
        n /= 10;
    } while (n);
    while (i)
        putc_uart(b[--i]);
}
void *memcpy(void *d, const void *s, size_t n)
{
    char *p = d;
    const char *q = s;
    while (n--)
        *p++ = *q++;
    return d;
}
void *memset(void *d, int c, size_t n)
{
    char *p = d;
    while (n--)
        *p++ = c;
    return d;
}
size_t strlen(const char *s)
{
    size_t n = 0;
    while (s[n])
        n++;
    return n;
}
int strcmp(const char *a, const char *b)
{
    while (*a && *a == *b) {
        a++;
        b++;
    }
    return (unsigned char)*a - (unsigned char)*b;
}
void __aeabi_memcpy(void *d, const void *s, size_t n)
{
    memcpy(d, s, n);
}
void __aeabi_memset(void *d, size_t n, int c)
{
    memset(d, c, n);
}
void __aeabi_memclr(void *d, size_t n)
{
    memset(d, 0, n);
}
extern void FreeRTOS_Tick_Handler(void);
void clear_tick(void)
{
    REG(TIMER + 0xc) = 1;
}
void setup_tick(void)
{
    REG(TIMER) = 1000000 / configTICK_RATE_HZ;
    REG(TIMER + 8) = 0xe2;
}
void vApplicationIRQHandler(uint32_t iar)
{
    unsigned irq = iar & 1023;
    if (irq == 33)
        FreeRTOS_Tick_Handler();
    if (irq == 32) {
        uart_irqs++;
        while (!(REG(UART + 0x18) & 0x10)) {
            char c = REG(UART);
            unsigned next = (rx_write + 1) & 255;
            if (next != rx_read) {
                rx[rx_write] = c;
                rx_write = next;
            }
        }
        REG(UART + 0x44) = 0x40;
    }
}
static void identity(void)
{
    uint32_t midr;
    __asm__ volatile("mrc p15,0,%0,c0,c0,0" : "=r"(midr));
    puts_uart("FreeRTOS on ARM MCU ");
    number(MCU_ID);
    puts_uart(" / Cortex-R5F / armv7-r\r\nFreeRTOS 10.4.6 / MIDR=");
    number(midr);
    puts_uart("\r\n");
}
static void heartbeat(void *arg)
{
    for (;;) {
        beats++;
        vTaskDelay(10);
    }
}
static void command(const char *cmd)
{
    if (!strcmp(cmd, "uname") || !strcmp(cmd, "arch"))
        identity();
    else if (!strcmp(cmd, "ticks")) {
        puts_uart("ticks=");
        number(xTaskGetTickCount());
        puts_uart(" heartbeat=");
        number(beats);
        puts_uart(" uart_irqs=");
        number(uart_irqs);
        puts_uart("\r\n");
    } else
        puts_uart("commands: uname arch ticks\r\n");
}
static void prompt(void)
{
    puts_uart("mcu");
    number(MCU_ID);
    puts_uart("> ");
}
static void shell(void *arg)
{
    char line[64];
    unsigned n = 0;
    identity();
    prompt();
    for (;;) {
        if (rx_read == rx_write) {
            vTaskDelay(1);
            continue;
        }
        char c = rx[rx_read];
        rx_read = (rx_read + 1) & 255;
        if (c == '\r' || c == '\n') {
            puts_uart("\r\n");
            line[n] = 0;
            command(line);
            n = 0;
            prompt();
        } else if (n < sizeof(line) - 1) {
            line[n++] = c;
            putc_uart(c);
        }
    }
}
int main(void)
{
    REG(UART + 0x30) = 0;
    REG(UART + 0x24) = 13;
    REG(UART + 0x28) = 1;
    REG(UART + 0x2c) = 0x60;
    REG(UART + 0x30) = 0x301;
    REG(UART + 0x38) = 0x50;
    REG(GIC) = 1;
    REG(GIC + 0x104) = 3;
    *(volatile uint8_t *)(uintptr_t)(GIC + 0x420) = 0xe0;
    *(volatile uint8_t *)(uintptr_t)(GIC + 0x421) = 0xe0;
    *(volatile uint8_t *)(uintptr_t)(GIC + 0x820) = 1;
    *(volatile uint8_t *)(uintptr_t)(GIC + 0x821) = 1;
    REG(GIC + 0x10000) = 1;
    REG(GIC + 0x10004) = 0xff;
    REG(GIC + 0x10008) = 0;
    xTaskCreate(shell, "shell", 512, 0, 2, 0);
    xTaskCreate(heartbeat, "heartbeat", 256, 0, 1, 0);
    vTaskStartScheduler();
    for (;;) {
    }
}
