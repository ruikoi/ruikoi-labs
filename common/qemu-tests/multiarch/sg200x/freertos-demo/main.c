/* SPDX-License-Identifier: MIT */
/* External validation application; vendor FreeRTOS kernel is unchanged. */
#include <stddef.h>
#include <stdint.h>
#include "FreeRTOS.h"
#include "task.h"
#define REG(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define UART 0x04150000UL
static volatile unsigned rx_read, rx_write, uart_irqs, beats;
static char rx[256];
static void putc_uart(char c) { while (!(REG(UART + 0x14) & 0x20)) {} REG(UART) = c; }
static void puts_uart(const char *s) { while (*s) putc_uart(*s++); }
static void number(uint64_t n) { char b[24]; unsigned i=0; do { b[i++]='0'+n%10; n/=10; } while(n); while(i) putc_uart(b[--i]); }
void *memcpy(void *d, const void *s, size_t n) { char *p=d; const char *q=s; while(n--) *p++=*q++; return d; }
void *memset(void *d, int c, size_t n) { char *p=d; while(n--) *p++=c; return d; }
size_t strlen(const char *s) { size_t n=0; while(s[n]) n++; return n; }
int __clzdi2(uint64_t x) { int n=0; while (!(x & (1ULL<<63)) && n<64) { n++; x<<=1; } return n; }
static int equal(const char *a,const char *b) { while(*a && *a==*b) { a++; b++; } return *a==*b; }
extern volatile uint64_t *pullMachineTimerCompareRegister;
extern uint64_t ullNextTime;
extern const size_t uxTimerIncrementsForOneTick;
void vPortSetupTimerInterrupt(void)
{
    /* SG200X small-domain compare register is local, not indexed by mhartid. */
    uint64_t now;
    __asm__ volatile("rdtime %0" : "=r"(now));
    pullMachineTimerCompareRegister=(void *)(uintptr_t)0x74004000UL;
    ullNextTime=now+uxTimerIncrementsForOneTick;
    *pullMachineTimerCompareRegister=ullNextTime;
    ullNextTime+=uxTimerIncrementsForOneTick;
}
void demo_interrupt(uint64_t cause, uint64_t epc)
{
    unsigned irq=REG(0x70200004UL);
    if (irq==31) {
        uart_irqs++;
        while(REG(UART+0x14)&1) {
            char c=REG(UART);
            unsigned next=(rx_write+1)&255;
            if(next!=rx_read) { rx[rx_write]=c; rx_write=next; }
        }
    }
    if(irq) REG(0x70200004UL)=irq;
}
static void heartbeat(void *arg) { for(;;) { beats++; vTaskDelay(10); } }
static void command(const char *cmd)
{
    if(equal(cmd,"uname") || equal(cmd,"arch")) {
        uint64_t hart,misa;
        __asm__ volatile("csrr %0,mhartid" : "=r"(hart));
        __asm__ volatile("csrr %0,misa" : "=r"(misa));
        puts_uart("FreeRTOS 10.4.6 / SG200X C906 / riscv64 / M-mode / hart="); number(hart);
        puts_uart(" / XLEN="); number((misa>>62)==2 ? 64:32); puts_uart("\r\n");
    } else if(equal(cmd,"ticks")) {
        puts_uart("ticks="); number(xTaskGetTickCount()); puts_uart(" heartbeat="); number(beats);
        puts_uart(" uart_irqs="); number(uart_irqs); puts_uart("\r\n");
    } else puts_uart("commands: uname arch ticks\r\n");
}
static void shell(void *arg)
{
    char line[64]; unsigned n=0;
    puts_uart("SG200X FreeRTOS scheduler running; UART1 + private PLIC/CLINT\r\nrtos> ");
    for(;;) {
        if(rx_read==rx_write) { vTaskDelay(1); continue; }
        char c=rx[rx_read]; rx_read=(rx_read+1)&255;
        if(c=='\r' || c=='\n') { puts_uart("\r\n"); line[n]=0; command(line); n=0; puts_uart("rtos> "); }
        else if(n<sizeof(line)-1) { line[n++]=c; putc_uart(c); }
    }
}
int main(void)
{
    REG(UART+0x0c)=0x80; REG(UART)=14; REG(UART+4)=0;
    REG(UART+0x0c)=3; REG(UART+8)=0; REG(UART+4)=1;
    REG(0x70000000UL+31*4)=1;
    REG(0x70002000UL)=1UL<<31;
    REG(0x70200000UL)=0;
    xTaskCreate(shell,"shell",512,0,2,0);
    xTaskCreate(heartbeat,"heartbeat",256,0,1,0);
    vTaskStartScheduler();
    for(;;) {}
}
