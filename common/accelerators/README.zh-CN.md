# 加速器工程共用工具

[English](README.md) | 简体中文

`tools/accelerator.py` 根据各项目的 `project.json` 获取固定源码，再调用这里的构建、负载打包和验收配方。默认工作目录为仓库下 `.work/`；可用 `LAB_WORK` 或 `--work` 指定其他磁盘。源码、构建、模型、下载缓存和测试结果分开保存。

```sh
export LAB_WORK="$PWD/.work"
mkdir -p "$LAB_WORK/cache/tmp"
export TMPDIR="$LAB_WORK/cache/tmp"
export XDG_CACHE_HOME="$LAB_WORK/cache/xdg"
python3 tools/accelerator.py qemu-coral-isa doctor
projects/qemu-coral-isa/fetch.sh
projects/qemu-coral-isa/build.sh --component qemu
```

`--sources` 指定项目源码父目录，例如其中包含 `qemu/`、`coralnpu/`、`mpact/`。`--build` 和 `--artifacts` 可分别指定构建与负载目录。首次下载前可通过 `LAB_QEMU_URL`、`LAB_CORALNPU_URL`、`LAB_MPACT_URL`、`LAB_COMPILER_URL`、`LAB_QBOX_URL`、`LAB_VORTEX_URL` 覆盖仓库地址；提交版本仍由清单锁定。自有 fork 须包含对应提交。

当前加速器项目的集成环境为 Linux x86_64，工程入口使用 Python 3.8 或更新版本（模型依赖另用其支持的 Python 版本）。macOS 支持继续在各源码项目推进，当前清单不将其列为已支持宿主。CPU 项目的 macOS 支持不受影响。可使用根目录 `Dockerfile.accelerators` 安装系统依赖。

工具版本分别为 MPACT/Coral RTL 的 Bazel 8.6.0 和 Coral compiler 的 Bazel 9.1.0。通过 `BAZEL8`、`BAZEL9` 指定可执行文件；`HOST_CC`、`HOST_CXX` 指定宿主编译器，默认为可用的 clang-19 或系统编译器。Coral compiler 的上游配置要求 clang-19。GPU 内核需要 Vortex 专用 LLVM，不能用普通 RISC-V GCC 替代。Coral 的受限 TFLite 编译工具只需 `ai-edge-litert` 的 FlatBuffer schema；用 `TFLITE_PYTHON` 指定已安装该包的 Python。重新训练 digits 模型才需要 TensorFlow。

## Linux 负载和工具链

`--images DIR` 要求以下目录结构：

```text
DIR/
  arm64/{Image,rootfs.cpio.gz}
  riscv64/{Image,rootfs.cpio.gz,fw_dynamic.bin}
```

内核须支持 virt 平台、串口、PCI/sysfs、devtmpfs、initramfs；RTL 路线还需要 PLIC 和模块加载。根文件系统须含 BusyBox 的 mount、poweroff；Vortex RTL 还使用 su。可从 [CPU 项目的负载准备](../../projects/qemu-multiarch/README.zh-CN.md) 生成 Linux/Buildroot 环境，或传入自己的等价负载。RISC-V OpenSBI 固件可使用固定版本 QEMU 的 `pc-bios/opensbi-riscv64-generic-fw_dynamic.bin`。

`ARM64_CC`、`RISCV64_CC`、`RISCV64_CXX` 可覆盖 Linux 交叉编译器；默认使用发行版的 `aarch64-linux-gnu-*` / `riscv64-linux-gnu-*`。Coral RV32 固件使用 `NPU_CC`、`NPU_LD`，默认 clang-19 / lld-19。基本 V2 功能后端对应 `rv32imf_zve32x`；支持向量浮点的 Gemma 固件单独按上游配置构建。

RTL 的 `guest` 和 `driver` 分别构建用户程序／固件与内核模块；可在不同 Linux 环境执行，随后把 `.ko` 放入对应构建目录。`build` 默认的 `all` 包括两者。内核驱动必须匹配实际启动的内核，设置：

```sh
export KERNEL_SOURCE=/path/to/linux-source
export KERNEL_BUILD=/path/to/matching-kernel-build
export KERNEL_CROSS_COMPILE=/path/to/cross-toolchain/bin/riscv64-linux-
```

路径均为使用者提供的工程路径。不要混用不同版本内核的 `.ko`。

## 结果与验收范围

每次 `test` 在工作目录 `runs/<项目>/<时间>/` 保存结果和串口日志；超时、进程失败、缺失数值或硬件证据均判为失败。`run` 使用相同负载和验收入口。修改 suite 后须以相同 suite 重新构建必要负载并执行 `prepare`。`prepare` 和 `test` 的 `--samples` 也须一致，例如完整 MNIST 集合应两处都传 `--samples 100`。

- `backend`：宿主直调功能模拟器，只代表指令和接口回归。
- `linux` / `digits`：Linux guest 启动并提交实际 NPU/GPU 工作。
- `mobilenet` / `gemma`：含义依路线区分，见项目说明。宿主 IREE/PJRT 结果不代表 QEMU guest 或 RTL 已验证同一模型。
- RTL 的 `smoke` 只验证实际 RTL/TLM 基本接口；Linux 数值测试另外验证中断、共享内存、故障和恢复。

复用来源：Coral 工具来自 google-coral/coralnpu、coralnpu-mpact、coralnpu-compiler 的既有本地实验；Vortex 工具来自 vortexgpgpu/vortex 及 QBox 集成实验。保留源码原有许可证声明。`patches/libqemu-headless.patch` 适用清单固定的 Qualcomm libqemu；`patches/ramulator-template.patch` 适用 Vortex 固定子模块 Ramulator2。它们是独立依赖补丁，不另建自有 fork。SystemC 的 TLM-2.0 随官方 SystemC 源码提供，无需额外 TLM 仓库。
