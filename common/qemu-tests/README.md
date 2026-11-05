# QEMU guest and platform tests

English | [简体中文](README.zh-CN.md)

These tests and firmware examples were extracted from the existing ARM/RISC-V
and SG200X experiments and adapted to this repository's paths and CLI.
They are maintained here rather than copied into the cleaned QEMU branches.

The original files retain their SPDX license identifiers. The Python platform
tests are GPL-2.0-or-later; the FreeRTOS demonstration build scripts and
applications identify their own licenses. The FreeRTOS kernel is fetched from
its public repository at a fixed commit and is not vendored here.

The corresponding source revision was `4c2753a733` before branch cleanup.
Generic paths, optional host sampling, and explicit output directories were
introduced during this extraction. macOS host sampling is opt-in through
`hetero/mttcg.py --sample-host`; ordinary smoke validates functional progress
and shared atomics. Linux additionally checks distinct QMP vCPU thread IDs.

`hetero/coremark-bench.py` and `hetero/macos-host-sampler.c` are optional
benchmark tools, not a dependency of build/smoke. Read their CLI before use;
host-specific measurements must be reported separately from guest correctness.
