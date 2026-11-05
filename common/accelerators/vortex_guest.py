"""Build RV32 GPU kernels and the RV64 Linux client/driver separately."""
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
from recipes import configure_vortex, tool


def build(c):
    source, out, tools = configure_vortex(c)
    payload = c.build / 'payload'
    target = payload / 'vortex'
    library = target / 'lib'
    library.mkdir(parents=True, exist_ok=True)
    cxx = tool('RISCV64_CXX', 'riscv64-linux-gnu-g++')
    cc = tool('RISCV64_CC', 'riscv64-linux-gnu-gcc')
    c.execute(['make', '-C', out / 'sw/runtime/stub', 'CXX=' + cxx,
               'DESTDIR=' + str(library), 'CONFIGS=-DVX_QEMU_TRANSPORT', '-j', c.a.jobs])
    flags = shlex.split(subprocess.check_output(['python3', 'ci/gen_config.py',
                 '--config=' + str(source / 'VX_config.toml'), '--cflags=-DVX_CFG_XLEN=32'],
                 cwd=out, env=c.env, text=True))
    includes = [source / x for x in ('sw/runtime/include', 'sw/runtime/common',
                                     'sw/common', 'sw/kernel/include')]
    includes += [out / 'sw', out / 'hw']
    common = ['-std=c++17', '-O2', '-DNDEBUG', '-fPIC', '-ffp-contract=off',
              '-DVX_CFG_XLEN=32', '-DVX_CFG_XLEN_32', *['-I' + str(p) for p in includes], *flags]
    rtl = c.a.project.endswith('rtl')
    route = c.source('qbox') / 'integration/vortex-rtl' if rtl else source / 'integration/qemu'
    c.execute([cxx, *common, '-shared', '-pthread', route / ('runtime-qbox.cpp' if rtl else 'runtime-qemu.cpp'),
               '-o', library / ('libvortex-qbox.so' if rtl else 'libvortex-qemu.so')])
    apps = ['demo', 'sgemm']
    if c.a.suite in ('mnist', 'full'):
        apps.append('mnist')
    if c.a.suite in ('mobilenet', 'full'):
        apps.append('mobilenet')
    llvm = tools / 'llvm-vortex/bin'
    gpu = [str(llvm / 'clang'), '--target=riscv32-unknown-elf', '--sysroot=' + str(tools / 'libc32'),
           '-DVX_USE_MUSL', '-Xclang', '-target-feature', '-Xclang', '+xvortex',
           '-Xclang', '-target-feature', '-Xclang', '+zicond']
    gpu_cxx = [str(llvm / 'clang++'), *gpu[1:], '-fuse-ld=lld', '-Wl,-z,norelro']
    c.execute(['make', '-C', out / 'sw/kernel', 'CC=' + shlex.join(gpu),
               'AR=' + str(llvm / 'llvm-ar'), 'DP=' + str(llvm / 'llvm-objdump'),
               'CP=' + str(llvm / 'llvm-objcopy'), '-j', c.a.jobs])
    for app in apps:
        name = app + '-classify' if app in ('mnist', 'mobilenet') else app
        c.execute([cxx, *common, source / 'tests/regression' / app / 'main.cpp',
                   '-L' + str(library), '-lvortex', '-pthread', '-Wl,-rpath,/vortex/lib',
                   '-o', target / name])
        build = out / 'tests/regression' / app
        c.execute(['make', '-C', build, 'RISCV_SYSROOT=' + str(tools / 'libc32'),
                   'RISCV_TOOLCHAIN_PATH=' + str(tools / 'libc32'), 'CONFIGS=-DVX_USE_MUSL',
                   'VX_CXX=' + shlex.join(gpu_cxx), 'CC=' + shlex.join(gpu),
                   'AR=' + str(llvm / 'llvm-ar'), 'DP=' + str(llvm / 'llvm-objdump'),
                   'CP=' + str(llvm / 'llvm-objcopy'), 'kernel.vxbin', '-j', c.a.jobs])
        directory = target / (app if app in ('mnist', 'mobilenet') else app + '-kernel')
        directory.mkdir(exist_ok=True)
        shutil.copy2(build / 'kernel.vxbin', directory)
    libs = payload / 'lib'
    libs.mkdir(exist_ok=True)
    for name in ('ld-linux-riscv64-lp64d.so.1', 'libc.so.6', 'libm.so.6', 'libstdc++.so.6',
                 'libgcc_s.so.1', 'libpthread.so.0', 'libdl.so.2', 'librt.so.1'):
        path = subprocess.check_output([cxx, '-print-file-name=' + name], text=True).strip()
        if path == name:
            raise ValueError('Cross toolchain did not resolve required library: ' + name)
        shutil.copy2(Path(path).resolve(), libs / name)
    if rtl:
        guest = c.build / 'guest'
        guest.mkdir(exist_ok=True)
        for name in ('queue-reset', 'driver-test'):
            c.execute([cc, '-O2', '-Wall', '-Wextra', route / (name + '.c'), '-o', target / name])
        c.execute([cxx, *common, route / 'interface-test.cpp', '-L' + str(library),
                   '-lvortex', '-pthread', '-Wl,-rpath,/vortex/lib', '-o', target / 'interface-test'])
    else:
        c.execute([cc, '-O2', '-static', route / 'transport-test.c', '-o', target / 'transport-test'])
    print('VORTEX_GUEST_BUILD_PASS; export model fixtures before prepare for model suites')


def init_script(c):
    rtl = c.a.project.endswith('rtl')
    if rtl:
        start = '''export LD_LIBRARY_PATH=/vortex/lib:/lib
export VORTEX_DRIVER=qbox
echo VORTEX_QBOX_LINUX_START
insmod /vortex/vortex_qbox.ko || { dmesg | tail -n 30; echo VORTEX_RTL_TEST_EXIT=1; poweroff -f; exit 1; }
chmod 666 /dev/vortex0
echo "vortex:x:1000:1000:Vortex test:/tmp:/bin/sh" >> /etc/passwd
echo "vortex:x:1000:" >> /etc/group
'''
    else:
        start = 'export LD_LIBRARY_PATH=/vortex/lib:/lib\nexport VORTEX_DRIVER=qemu\necho VORTEX_LINUX_START\n'
    prefix = 'VORTEX_RTL' if rtl else 'VORTEX'
    command = 'su -s /bin/sh -c "LD_LIBRARY_PATH=/vortex/lib:/lib VORTEX_DRIVER=qbox $*" vortex' if rtl else '"$@"'
    start += f'''run() {{
 name=$1
 shift
 echo {prefix}_CASE_START=$name
 {command}
 rc=$?
 echo {prefix}_CASE_EXIT=$name:$rc
 if [ "$rc" != 0 ]; then echo {prefix}_TEST_EXIT=1; poweroff -f; exit 1; fi
}}
'''
    if rtl:
        start += '''run driver /vortex/driver-test
run demo /vortex/demo -n4 -k/vortex/demo-kernel/kernel.vxbin
run demo2d /vortex/demo -n16 -x2 -y2 -k/vortex/demo-kernel/kernel.vxbin
run sgemm /vortex/sgemm -n8 -k/vortex/sgemm-kernel/kernel.vxbin
'''
    else:
        start += '''run transport /vortex/transport-test
run demo64 /vortex/demo -n64 -k/vortex/demo-kernel/kernel.vxbin
run demo2d /vortex/demo -n256 -x8 -y2 -k/vortex/demo-kernel/kernel.vxbin
run sgemm16 /vortex/sgemm -n16 -k/vortex/sgemm-kernel/kernel.vxbin
run sgemm32 /vortex/sgemm -n32 -k/vortex/sgemm-kernel/kernel.vxbin
'''
    if c.a.suite in ('mnist', 'full'):
        start += f'run mnist /vortex/mnist-classify /vortex/mnist --samples {c.a.samples}\n'
    if c.a.suite in ('mobilenet', 'full'):
        start += 'run mobilenet /vortex/mobilenet-classify /vortex/mobilenet\n'
    if rtl:
        if c.a.suite in ('interface', 'full'):
            start += 'run interface /vortex/interface-test\n'
        start += '''cat /proc/interrupts
run reset /vortex/queue-reset
run recovery /vortex/demo -n4 -k/vortex/demo-kernel/kernel.vxbin
echo VORTEX_QBOX_RTL_ALL_PASS
'''
    else:
        start += 'echo VORTEX_GUEST_ALL_PASS\n'
    return start + f'echo {prefix}_TEST_EXIT=0\n'
