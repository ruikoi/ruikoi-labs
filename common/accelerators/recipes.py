"""Build recipes; source trees remain separate from generated outputs."""
import gzip
import json
import os
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def tool(name, default):
    return os.environ.get(name, default)


def configure_vortex(c):
    source = c.source('vortex')
    out = c.build / 'vortex'
    out.mkdir(exist_ok=True)
    tools = Path(os.environ['VORTEX_TOOLS']).resolve()
    c.execute([source / 'configure', '--xlen=32', '--tooldir=' + str(tools),
               '--osversion=ubuntu/focal'], out)
    return source, out, tools


def build_backend(c):
    if 'coral' in c.a.project:
        c.bazel('mpact', '8', ['//sim:libcoral_qemu.so', '//sim:libcoral_gemma.so',
                              '//sim:coralnpu_v2_sim'],
                ['--repositories_without_autoloads=bazel_features_version,bazel_features_globals',
                 '--copt=-O2'])
        return
    from accelerator import apply_patch
    source, out, _ = configure_vortex(c)
    apply_patch(c, source / 'third_party/ramulator', HERE / 'patches/ramulator-template.patch')
    build_softfloat(c, source)
    ram = c.build / 'ramulator'
    c.execute(['cmake', '-S', source / 'third_party/ramulator', '-B', ram,
               '-DCMAKE_POLICY_VERSION_MINIMUM=3.5', '-DCMAKE_CXX_FLAGS=-DFMT_CONSTEVAL='])
    c.execute(['cmake', '--build', ram, '--target', 'ramulator', '-j', c.a.jobs])
    # The upstream Makefile resolves these dependency paths relative to its source.
    expected = source / 'third_party/ramulator/build'
    if not expected.exists():
        expected.symlink_to(ram, target_is_directory=True)
    elif expected.resolve() != ram.resolve():
        raise ValueError('Use a fresh source tree: ramulator/build already belongs to another build')
    c.execute(['make', '-C', out / 'sim/simx', 'USE_GEM5=1', 'PERF=1',
               'CXX=' + tool('HOST_CXX', 'c++'), '-j', c.a.jobs, 'libvortex-gem5'])


def build_softfloat(c, source):
    soft = source / 'third_party/softfloat/build/Linux-x86_64-GCC'
    c.execute(['make', '-C', soft, 'clean'])
    c.execute(['make', '-C', soft, '-j', c.a.jobs,
               'SOFTFLOAT_OPTS=-fPIC -DSOFTFLOAT_ROUND_ODD -DINLINE_LEVEL=5 '
               '-DSOFTFLOAT_FAST_DIV32TO16 -DSOFTFLOAT_FAST_DIV64TO32 -DTHREAD_LOCAL=__thread'])


def build_rtl(c):
    if 'coral' in c.a.project:
        suffix = 'highmem_axi' if c.a.profile == 'gemma' else 'axi'
        c.bazel('coralnpu', '8', ['//tests/verilator_sim:rvv_core_mini_' + suffix + '_sim'],
                ['--repositories_without_autoloads=bazel_features_version,bazel_features_globals'])
        source = c.source('coralnpu')
        model = source / 'bazel-bin/hdl/chisel/src/coralnpu'
        external = (source / ('bazel-' + source.name) / 'external').resolve()
        cls = 'VRvvCoreMiniHighmemAxi' if c.a.profile == 'gemma' else 'VRvvCoreMiniAxi'
        stem = 'rvv_core_mini_' + suffix
        dest = c.build / 'rtl-generated'
        for src, relative in [(model / (stem + '_cc_library_cpp'), 'model'),
                (external / 'verilator/include', 'verilator/include'),
                (external / 'libsystemctlm_soc', 'libsystemctlm-soc'),
                (external / 'com_google_absl', 'abseil')]:
            shutil.copytree(src, dest / relative, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('*.o', '*.a', '*.so', '.git'))
        hdr = dest / 'generated/hdl/chisel/src/coralnpu'
        hdr.mkdir(parents=True, exist_ok=True)
        shutil.copy2(model / (cls + '_parameters.h'), hdr)
        shutil.copy2(source / 'bazel-bin/external/verilator/include/verilated_config.h',
                     dest / 'verilator/include')
    else:
        source, out, _ = configure_vortex(c)
        build_softfloat(c, source)
        dest = c.build / 'rtl-generated'
        vlt = Path(os.environ['VERILATOR_ROOT']).resolve()
        binary = tool('VERILATOR', str(vlt / 'bin/verilator'))
        extra = out / 'print-flags.mk'
        extra.write_text('lab-flags:\n\t@printf "%s\\n" "$(VL_FLAGS)"\n')
        flags = subprocess.check_output(['make', '-s', '-f', 'Makefile', '-f', str(extra),
                 'lab-flags', 'VCD=1', 'VERILATOR_PATH=' + str(vlt)],
                 cwd=out / 'sim/xrtsim', env=c.env, text=True)
        (dest / 'model').mkdir(parents=True, exist_ok=True)
        c.execute([binary, '--cc', *shlex.split(flags), 'vortex_afu_shim',
                   '--top-module', 'vortex_afu_shim', '--Mdir', dest / 'model'], out / 'sim/xrtsim')
        shutil.copytree(vlt / 'include', dest / 'verilator/include', dirs_exist_ok=True)
        shutil.copytree(out / 'sw', dest / 'config/sw', dirs_exist_ok=True)
        shutil.copytree(out / 'hw', dest / 'config/hw', dirs_exist_ok=True)
    from accelerator import git
    model_source = c.source('coralnpu' if 'coral' in c.a.project else 'vortex')
    (dest / 'manifest.json').write_text(json.dumps({
        'profile': c.a.profile, 'project': c.a.project,
        'source_commit': git(model_source, 'rev-parse', 'HEAD') if (model_source / '.git').exists() else None,
        'expected_source_commit': c.manifest['sources']['coralnpu' if 'coral' in c.a.project else 'vortex']['commit'],
        'format': 'generated source; recompile for the platform SystemC'}, indent=2) + '\n')


def build_qbox(c):
    from accelerator import apply_patch
    source = c.source('qbox')
    out = c.build / 'qbox'
    coral = 'coral' in c.a.project
    options = ['-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_POLICY_VERSION_MINIMUM=3.5',
               '-DCPM_DOWNLOAD_ALL=ON', '-DCPM_SOURCE_CACHE=' + str(c.cache / 'cpm'),
               '-DUBUNTU_ARCH=riscv64', '-DLIBQEMU_TARGETS=riscv64',
               '-DENABLE_PYTHON_BINDER=OFF', '-DBUILD_TESTING=OFF',
               '-DGS_ENABLE_VIRGLRENDERER=OFF', '-DGS_ENABLE_VIRCLRENDERER=OFF']
    if coral:
        options += ['-DENABLE_CORAL_RTL=ON', '-DCORAL_RTL_SOURCE=' + str(c.source('coralnpu')),
                    '-DCORAL_GENERATED=' + str(c.build / 'rtl-generated'),
                    '-DCORAL_RTL_PROFILE=' + c.a.profile]
    else:
        options += ['-DENABLE_VORTEX_RTL=ON', '-DVORTEX_SOURCE=' + str(c.source('vortex')),
                    '-DVORTEX_RTL_GENERATED=' + str(c.build / 'rtl-generated')]
    c.execute(['cmake', '-S', source, '-B', out, *options])
    for dependency in (c.cache / 'cpm/libqemu').glob('*'):
        if (dependency / 'qemu.cmake').exists():
            apply_patch(c, dependency, HERE / 'patches/libqemu-headless.patch')
            path = dependency / 'qemu.cmake'
            text = path.read_text()
            for feature in ('opengl', 'vnc', 'vnc-jpeg'):
                text = text.replace('--enable-' + feature + '\n', '--disable-' + feature + '\n')
            path.write_text(text.replace('--disable-sdl\n', '--enable-sdl\n'))
    c.execute(['cmake', '-S', source, '-B', out, *options])
    module = 'coral_npu_rtl' if coral else 'vortex_gpu_rtl'
    smoke = 'coral-platform-smoke' if coral else 'vortex-platform-smoke'
    c.execute(['cmake', '--build', out, '--parallel', c.a.jobs, '--target',
               'platforms-vp', module, smoke, 'router', 'gs_memory', 'loader', 'cpu_riscv64',
               'plic_sifive', 'riscv_aclint_swi', 'riscv_aclint_mtimer', 'sifive_test',
               'uart_16550', 'global_peripheral_initiator'])


def npu_compile(c, out, startup, linker, source, dest, includes=()):
    c.execute([tool('NPU_CC', 'clang-19'), '--target=riscv32', '-march=' + tool('NPU_MARCH', 'rv32imf_zve32x'), '-mabi=ilp32f',
               '-O2', '-ffreestanding', '-fno-builtin', '-nostdlib',
               '-fuse-ld=' + tool('NPU_LD', 'lld-19'),
               *['-I' + str(p) for p in includes], '-Wl,-T,' + str(linker), '-Wl,--no-relax',
               startup, '-x', 'c', source, '-o', dest], out)


def build_guest(c):
    if 'vortex' in c.a.project:
        from vortex_guest import build as build_vortex_guest
        build_vortex_guest(c)
        return
    if c.a.suite == 'gemma-guest' or c.a.project == 'qemu-coral-rtl' and c.a.profile == 'gemma':
        from gemma_guest import build
        return build(c)
    source = c.source('coralnpu') / 'integration/qemu'
    out = c.build / 'guest'
    shutil.copytree(source, out, dirs_exist_ok=True)
    rtl = c.a.project.endswith('rtl')
    startup = c.source('coralnpu') / 'integration/rtl/start.S' if rtl else out / 'start.S'
    npu_compile(c, out, startup, out / 'npu.ld', out / 'hello_world_add_floats.cc',
                out / 'add.elf', [out / 'include'])
    npu_compile(c, out, startup, out / 'npu.ld', out / 'model.c', out / 'model.elf')
    sys.path.insert(0, str(out))
    from elf32 import read
    add = read(out / 'add.elf')[2]
    address = read(out / 'model.elf')[2]['model_io'][0]
    (out / 'elf_symbols.h').write_text('\n'.join(
        '#define ADD_' + name.upper() + ' 0x%xu' % add[name][0]
        for name in ('input1', 'input2', 'output')) + '\n#define MODEL_IO 0x%xu\n' % address)
    if rtl or c.a.suite == 'digits':
        digits = out / 'tflite'
        c.execute([tool('TFLITE_PYTHON', sys.executable), 'compile_tflite.py'], digits)
        c.execute([sys.executable, 'prepare_cases.py'], digits)
        npu_compile(c, out, startup, out / 'npu.ld', digits / 'digits_runtime.c',
                    out / 'digits.elf', [digits / 'out'])
        address = read(out / 'digits.elf')[2]['digits_io'][0]
        (digits / 'out/digits_symbols.h').write_text('#define DIGITS_IO 0x%xu\n' % address)
        if rtl:
            npu_compile(c, out, startup, c.source('coralnpu') / 'integration/rtl/npu-external.ld',
                        digits / 'digits_runtime.c', out / 'digits-external.elf', [digits / 'out'])
            report = json.loads((digits / 'models/digits.json').read_text())
            cases = b'CRTLCASE' + struct.pack('<I', len(report['cases']))
            for case in report['cases']:
                cases += struct.pack('<64b10bBB', *case['input'], *case['output'],
                                     case['label'], case['predicted'])
            (out / 'cases.bin').write_bytes(cases)
    arches = ['riscv64'] if rtl else [c.a.arch]
    for arch in arches:
        cc = tool('ARM64_CC' if arch == 'arm64' else 'RISCV64_CC',
                  'aarch64-linux-gnu-gcc' if arch == 'arm64' else 'riscv64-linux-gnu-gcc')
        target = out / ('coral-rtl-runner' if rtl else 'coral-runner-' + arch)
        program = c.source('coralnpu') / 'integration/rtl/guest_runner.c' if rtl else out / 'guest_runner.c'
        c.execute([cc, '-static', '-O2', '-Wall', '-Wextra',
                   *(['-DCORAL_DIGITS'] if not rtl and c.a.suite == 'digits' else []),
                   program, '-o', target], out)


def build_driver(c):
    if not c.a.project.endswith('rtl'):
        raise ValueError('Only RTL routes use a kernel module')
    guest = c.build / 'guest'
    guest.mkdir(exist_ok=True)
    coral = 'coral' in c.a.project
    source = (c.source('coralnpu') / 'integration/rtl/irq-driver' if coral else
              c.source('qbox') / 'integration/vortex-rtl/driver')
    name = 'coral_rtl_irq.ko' if coral else 'vortex_qbox.ko'
    compile_module(c, source, guest / 'driver', name)
    if not coral:
        target = c.build / 'payload/vortex'
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(guest / name, target)


def compile_module(c, source, out, name):
    shutil.copytree(source, out, dirs_exist_ok=True)
    kernel = Path(os.environ['KERNEL_SOURCE']).resolve()
    output = Path(os.environ['KERNEL_BUILD']).resolve()
    options = ['O=' + str(output), 'ARCH=riscv', 'CROSS_COMPILE=' + os.environ['KERNEL_CROSS_COMPILE'],
               'KBUILD_EXTRA_SYMBOLS=' + str(output / 'vmlinux.symvers')]
    c.execute(['make', '-C', kernel, *options, 'M=' + str(out), 'modules'])
    shutil.copy2(out / name, c.build / 'guest' / name)


def prepare(c):
    from artifacts import prepare as pack
    pack(c)
