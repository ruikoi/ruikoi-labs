# Shared accelerator tooling

English | [简体中文](README.zh-CN.md)

`tools/accelerator.py` fetches the revisions pinned by each `project.json`, then invokes build, payload-packaging, and acceptance recipes. The default work directory is `.work/` in this repository; use `LAB_WORK` or `--work` to select another disk. Source, builds, models, caches, and results are stored separately.

```sh
export LAB_WORK="$PWD/.work"
mkdir -p "$LAB_WORK/cache/tmp"
export TMPDIR="$LAB_WORK/cache/tmp"
export XDG_CACHE_HOME="$LAB_WORK/cache/xdg"
python3 tools/accelerator.py qemu-coral-isa doctor
projects/qemu-coral-isa/fetch.sh
projects/qemu-coral-isa/build.sh --component qemu
```

`--sources` selects the parent directory containing sources such as `qemu/`, `coralnpu/`, and `mpact/`. `--build` and `--artifacts` override their output directories. Before the first fetch, override repository URLs with `LAB_QEMU_URL`, `LAB_CORALNPU_URL`, `LAB_MPACT_URL`, `LAB_COMPILER_URL`, `LAB_QBOX_URL`, or `LAB_VORTEX_URL`. Revisions remain pinned; the selected fork must contain those commits.

The accelerator integration environment is Linux x86_64. Entry points require Python 3.8 or newer; model dependencies need their own supported Python version. Accelerator macOS support remains under development and is not listed as supported by these manifests. CPU projects retain their documented macOS support. Install host packages with the root `Dockerfile.accelerators`.

Use Bazel 8.6.0 for MPACT/Coral RTL and Bazel 9.1.0 for Coral compiler, selecting binaries with `BAZEL8` and `BAZEL9`. `HOST_CC` and `HOST_CXX` select host compilers, defaulting to clang-19 when available or the system compiler. Upstream Coral compiler requires clang-19. GPU kernels need Vortex LLVM; generic RISC-V GCC cannot replace it. The restricted Coral TFLite compiler requires the `ai-edge-litert` FlatBuffer schema; select that Python environment with `TFLITE_PYTHON`. Retraining digits requires TensorFlow.

## Linux payloads and toolchains

`--images DIR` expects:

```text
DIR/
  arm64/{Image,rootfs.cpio.gz}
  riscv64/{Image,rootfs.cpio.gz,fw_dynamic.bin}
```

The kernel needs virt-platform support, UART, PCI/sysfs, devtmpfs, and initramfs. RTL paths also need the PLIC and kernel-module loading. Rootfs must include BusyBox `mount` and `poweroff`; Vortex RTL also uses `su`. Build payloads with the [CPU project guide](../../projects/qemu-multiarch/README.md#build-linux-payloads), or supply equivalent images. RISC-V firmware can use `pc-bios/opensbi-riscv64-generic-fw_dynamic.bin` from the pinned QEMU source.

`ARM64_CC`, `RISCV64_CC`, and `RISCV64_CXX` override Linux cross compilers, defaulting to distribution `aarch64-linux-gnu-*` / `riscv64-linux-gnu-*` tools. Coral RV32 firmware uses `NPU_CC` and `NPU_LD`, defaulting to clang-19 and lld-19. Basic V2 uses `rv32imf_zve32x`; Gemma vector-floating-point firmware follows its separate upstream configuration.

RTL `guest` and `driver` stages build user programs/firmware and kernel modules separately. They can run in different Linux environments; copy the resulting `.ko` into the corresponding build directory. The default `all` build includes both. Kernel modules must match the kernel being booted:

```sh
export KERNEL_SOURCE=/path/to/linux-source
export KERNEL_BUILD=/path/to/matching-kernel-build
export KERNEL_CROSS_COMPILE=/path/to/cross-toolchain/bin/riscv64-linux-
```

These are user-supplied paths. Avoid mixing modules and kernels from different builds.

## Results and acceptance scope

Each `test` writes results and serial logs under `runs/<project>/<timestamp>/` in the work directory. Timeout, process failure, and missing numerical or hardware evidence fail acceptance. `run` uses the same workload and validation entry point. After changing suites, rebuild the required payload and run `prepare` with the same suite. `--samples` must also match between `prepare` and `test`; pass `--samples 100` to both for the complete MNIST image set.

- `backend`: calls the functional simulator directly on the host, covering instructions and interfaces.
- `linux` / `digits`: boots Linux and submits actual NPU/GPU work from the guest.
- `mobilenet` / `gemma`: execution scope depends on the project. Host IREE/PJRT results do not establish guest or RTL validation of the same model.
- RTL `smoke`: exercises basic RTL/TLM interfaces. Linux numerical suites additionally check interrupts, shared memory, faults, and recovery.

Tools originate from google-coral/coralnpu, coralnpu-mpact, coralnpu-compiler, vortexgpgpu/vortex, and QBox integration experiments. Original license notices are retained. `patches/libqemu-headless.patch` applies to the pinned Qualcomm libqemu; `patches/ramulator-template.patch` applies to Vortex's pinned Ramulator2 submodule. These small dependency patches do not need separate forks. TLM-2.0 ships with official SystemC source and does not need a separate TLM repository.
