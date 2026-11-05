# ruikoi-labs

[English](README.md) | 简体中文

ruikoi-labs 汇集编译器、模拟器与系统软件的构建、测试及 CI 实验。结合 AI 将想法转化为可运行的实验，在实现、测试与分析中理解技术原理、积累实践经验。

各项目源码独立维护；本仓库统一管理源码版本、构建环境和运行入口。

| 项目 | 内容 | 宿主支持 |
|---|---|---|
| [qemu-multiarch](projects/qemu-multiarch/README.zh-CN.md) | 同／异 ISA 多执行域与 MTTCG | Linux / macOS |
| [qemu-d9m](projects/qemu-d9m/README.zh-CN.md) | Cocoa 五串口交互 | macOS |
| [qemu-sg200x](projects/qemu-sg200x/README.zh-CN.md) | SG200X SoC 与 Milk-V Duo 系列板卡 | Linux / macOS |
| [qemu-coral-isa](projects/qemu-coral-isa/README.zh-CN.md) | Coral NPU 指令级功能模拟 | Linux |
| [qemu-coral-rtl](projects/qemu-coral-rtl/README.zh-CN.md) | QBox／Coral NPU RTL 联合仿真 | Linux |
| [qemu-vortex-isa](projects/qemu-vortex-isa/README.zh-CN.md) | Vortex GPGPU 指令级模拟 | Linux |
| [qemu-vortex-rtl](projects/qemu-vortex-rtl/README.zh-CN.md) | QBox／Vortex AFU RTL 联合仿真 | Linux |

以 qemu-multiarch 为例：

```sh
python3 tools/lab.py qemu-multiarch doctor
python3 tools/lab.py qemu-multiarch fetch
projects/qemu-multiarch/build.sh
projects/qemu-multiarch/test.sh
```

依赖、负载准备和运行方式见各项目说明。CPU 项目的 Linux 构建环境可使用 [Dockerfile](Dockerfile)，加速器项目使用 [Dockerfile.accelerators](Dockerfile.accelerators)；Linux 内核与 Buildroot 负载需在 Linux 构建。

源码版本由各项目的 `project.json` 锁定，地址覆盖和构建参数见项目说明。构建产物和日志默认保存在 `.work/`。

新增项目参考[项目模板](templates/project/README.zh-CN.md)，同步维护英文 `README.md` 与中文 `README.zh-CN.md`。

工程工具采用 [MIT](LICENSE)；复用测试的许可见[来源与许可](common/qemu-tests/README.zh-CN.md)。

发布前，请自行确认拟公开的文件和图片中不包含个人信息、本机路径或凭据。
