# ruikoi-labs

English | [简体中文](README.zh-CN.md)

A collection of build, test, and CI experiments for compilers, simulators, and system software. We use AI to turn ideas into runnable experiments and learn through implementation, testing, and analysis.

Source code is maintained in separate repositories. This repository pins source revisions and provides build environments and run entry points.

| Project | Focus | Hosts |
|---|---|---|
| [qemu-multiarch](projects/qemu-multiarch/README.md) | Same-ISA / mixed-ISA execution domains and MTTCG | Linux / macOS |
| [qemu-d9m](projects/qemu-d9m/README.md) | Cocoa interaction across five serial consoles | macOS |
| [qemu-sg200x](projects/qemu-sg200x/README.md) | SG200X SoCs and Milk-V Duo boards | Linux / macOS |
| [qemu-coral-isa](projects/qemu-coral-isa/README.md) | Coral NPU instruction-level functional simulation | Linux |
| [qemu-coral-rtl](projects/qemu-coral-rtl/README.md) | QBox / Coral NPU RTL co-simulation | Linux |
| [qemu-vortex-isa](projects/qemu-vortex-isa/README.md) | Vortex GPGPU instruction-level simulation | Linux |
| [qemu-vortex-rtl](projects/qemu-vortex-rtl/README.md) | QBox / Vortex AFU RTL co-simulation | Linux |

For example, to build and test qemu-multiarch:

```sh
python3 tools/lab.py qemu-multiarch doctor
python3 tools/lab.py qemu-multiarch fetch
projects/qemu-multiarch/build.sh
projects/qemu-multiarch/test.sh
```

See each project's guide for dependencies, payload preparation, implementation notes, and example output. [Dockerfile](Dockerfile) provides the CPU projects' Linux build environment; accelerator projects use [Dockerfile.accelerators](Dockerfile.accelerators). Linux kernel and Buildroot payloads must be built on Linux.

Each `project.json` pins source revisions. Repository overrides and build options are documented per project. Build outputs and logs default to `.work/`.

Use the [project template](templates/project/README.md) when adding an experiment. Keep the English `README.md` and Chinese `README.zh-CN.md` in sync.

Repository tooling is [MIT-licensed](LICENSE); reused tests retain their [original licenses](common/qemu-tests/README.md).

Before publishing, review the files and images you choose to share for personal information, local paths, and credentials.
