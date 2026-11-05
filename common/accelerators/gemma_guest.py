"""Cross-compile the in-guest tokenizer and Gemma client."""
import os
from pathlib import Path
import shutil
from recipes import tool


def build(c):
    out = c.build / 'guest'
    out.mkdir(exist_ok=True)
    target = '//tests/cocotb/rvv/ml_ops/gemma_inference:gemma3_runner.elf'
    flags = ['--repositories_without_autoloads=bazel_features_version,bazel_features_globals']
    c.bazel('coralnpu', '8', [target], flags)
    outputs = c.bazel('coralnpu', '8', [target], [*flags, '--output=files'], action='cquery')
    elf = [path for path in outputs if path.endswith('/gemma3_runner.elf')]
    if len(elf) != 1:
        raise ValueError('Expected one configured Gemma firmware artifact: ' + repr(outputs))
    shutil.copy2(c.source('coralnpu') / elf[0], out / 'gemma3_runner.elf')
    rtl = c.a.project.endswith('rtl')
    if rtl:
        c.execute([tool('RISCV64_CC', 'riscv64-linux-gnu-gcc'), '-static', '-O2',
                   '-I' + str(c.source('coralnpu')), c.source('coralnpu') / 'integration/rtl/guest_gemma.c', '-lm', '-o', out / 'guest-gemma'])
        return
    sp = Path(os.environ['SENTENCEPIECE_SOURCE']).resolve()
    build = c.build / 'sentencepiece-rv64'
    cc = tool('RISCV64_CC', 'riscv64-linux-gnu-gcc')
    cxx = tool('RISCV64_CXX', 'riscv64-linux-gnu-g++')
    c.execute(['cmake', '-S', sp, '-B', build, '-DCMAKE_SYSTEM_NAME=Linux',
               '-DCMAKE_SYSTEM_PROCESSOR=riscv64', '-DCMAKE_C_COMPILER=' + cc,
               '-DCMAKE_CXX_COMPILER=' + cxx, '-DCMAKE_BUILD_TYPE=Release',
               '-DSPM_ENABLE_SHARED=OFF', '-DSPM_BUILD_TEST=OFF'])
    c.execute(['cmake', '--build', build, '--target', 'sentencepiece-static', '-j', c.a.jobs])
    src = c.source('mpact') / 'integration/gemma-chat'
    c.execute([cc, '-std=c11', '-O2', '-Wall', '-Wextra', '-c', src / 'guest_chat.c', '-o', out / 'guest.o'])
    c.execute([cxx, '-std=c++17', '-O2', '-I' + str(sp / 'src'), '-c',
               src / 'tokenizer.cc', '-o', out / 'tokenizer.o'])
    c.execute([cxx, '-static', '-o', out / 'guest-chat', out / 'guest.o', out / 'tokenizer.o',
               build / 'src/libsentencepiece.a', '-pthread', '-lm'])
