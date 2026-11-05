/* SPDX-License-Identifier: MIT */
#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H
#include <stdint.h>
#define configUSE_PREEMPTION 1
#define configUSE_TIME_SLICING 1
#define configUSE_IDLE_HOOK 0
#define configUSE_TICK_HOOK 0
#define configCPU_CLOCK_HZ 700000000UL
#define configSYS_CLOCK_HZ 25000000UL
#define configTICK_RATE_HZ 100
#define configMAX_PRIORITIES 4
#define configMINIMAL_STACK_SIZE 256
#define configTOTAL_HEAP_SIZE (24 * 1024)
#define configMAX_TASK_NAME_LEN 16
#define configUSE_16_BIT_TICKS 0
#define configIDLE_SHOULD_YIELD 1
#define configUSE_MUTEXES 0
#define configUSE_COUNTING_SEMAPHORES 0
#define configUSE_TIMERS 0
#define configUSE_CO_ROUTINES 0
#define configCHECK_FOR_STACK_OVERFLOW 0
#define configUSE_MALLOC_FAILED_HOOK 0
#define configSUPPORT_STATIC_ALLOCATION 0
#define configSUPPORT_DYNAMIC_ALLOCATION 1
#define configISR_STACK_SIZE_WORDS 512
#define configMTIME_BASE_ADDRESS 0x7400bff8UL
#define configMTIMECMP_BASE_ADDRESS 0x74004000UL
#define INCLUDE_vTaskDelay 1
#define INCLUDE_vTaskDelete 0
#define INCLUDE_vTaskSuspend 0
#define INCLUDE_uxTaskPriorityGet 0
#define INCLUDE_vTaskPrioritySet 0
#define INCLUDE_xTaskGetSchedulerState 0
#define configASSERT(x) do { if (!(x)) { __asm__ volatile("csrci mstatus,8"); for (;;) {} } } while (0)
#endif
