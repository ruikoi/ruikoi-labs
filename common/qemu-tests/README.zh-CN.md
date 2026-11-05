# QEMU 客体与平台测试

[English](README.md) | 简体中文

这些测试和固件示例提取自既有 ARM/RISC-V 与 SG200X 实验，并适配本仓库的路径和 CLI。它们在此独立维护，核心 QEMU 分支保留工程源码。

原文件保留 SPDX 许可证标识。Python 平台测试采用 GPL-2.0-or-later；FreeRTOS 示例应用和构建脚本分别声明许可证。FreeRTOS 内核从公开仓库的固定提交获取，不在此内置。

提取前对应的源码版本为 `4c2753a733`。工程工具使用通用路径、显式输出目录和可选宿主采样。macOS 宿主采样通过 `hetero/mttcg.py --sample-host` 启用；常规 smoke 验证功能进展和共享原子操作，Linux 额外检查 QMP 中不同 vCPU 的线程 ID。

`hetero/coremark-bench.py` 和 `hetero/macos-host-sampler.c` 是可选性能工具，不是构建／smoke 的依赖。使用前查看 CLI，宿主性能测量与客体正确性结果分别报告。
