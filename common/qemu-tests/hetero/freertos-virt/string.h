/* SPDX-License-Identifier: MIT */
#ifndef DEMO_STRING_H
#define DEMO_STRING_H
#include <stddef.h>
void *memcpy(void *, const void *, size_t);
void *memset(void *, int, size_t);
size_t strlen(const char *);
int strcmp(const char *, const char *);
#endif
