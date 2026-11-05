"""Host IREE/PJRT model validation, separate from QEMU guest acceptance."""
import os
import re
import shutil
from pathlib import Path
import subprocess
import sys
from acceptance import logged, require


def acceptance(c, output):
    source = c.source('compiler')
    python = os.environ.get('MODEL_PYTHON', sys.executable)
    env = c.env.copy()
    python = shutil.which(python) or python
    env['PATH'] = str(Path(python).resolve().parent) + os.pathsep + env.get('PATH', '')
    env['CORAL_NPU_TRACE'] = '1'
    env['PYTHONUNBUFFERED'] = '1'
    if c.a.suite == 'mobilenet':
        env['CORAL_MODEL_OUT'] = str(c.artifacts / 'mobilenet')
        out = Path(env['CORAL_MODEL_OUT'])
        out.mkdir(exist_ok=True)
        logged(c, [python, source / 'integration/models/prepare_mobilenet.py'],
               output / 'prepare.log', source, env)
        candidates = list((source / 'bazel-bin/external').glob('*/tools/iree-compile'))
        if not candidates:
            raise FileNotFoundError('Build the compiler component first')
        for route in ('jax', 'tflite'):
            dump = out / (route + '-executables')
            dump.mkdir(exist_ok=True)
            logged(c, [candidates[0], '--iree-hal-target-device=local',
                  '--iree-hal-local-target-device-backends=llvm-cpu', '--iree-llvmcpu-target-cpu=host',
                  '--iree-hal-target-device=coralnpu', '--coralnpu-dump-affinity-profile-format=pretty',
                  '--iree-hal-dump-executable-files-to=' + str(dump),
                  source / ('examples/mobilenetv2-' + route + '-aot/mobilenet_v2.mlir'),
                  '-o', out / (route + '.vmfb')], output / (route + '-compile.log'), source, env)
            text = logged(c, [source / 'bazel-bin/integration/models/validate_mobilenet',
                  '--vmfb', out / (route + '.vmfb'), '--input', out / 'input.npy',
                  '--reference', out / (route + '-reference.npy'),
                  '--report', output / (route + '-result.json')], output / (route + '-run.log'), source, env)
            require(text, ['CORAL_NPU_SIM_EXEC', 'MOBILENET_FULL_MODEL_PASS'])
            dispatches = re.findall(r'CORAL_NPU_SIM_EXEC ordinal=(\d+) image_bytes=(\d+) counter_delta=(\d+) status=(\d+)', text)
            if not dispatches or any(int(x[2]) <= 0 or int(x[3]) != 0 for x in dispatches):
                raise ValueError('MobileNet requires successful NPU dispatches with positive execution counters')
    else:
        checkpoint = Path(os.environ['CORAL_GEMMA_CHECKPOINT']).resolve()
        tokenizer = Path(os.environ['CORAL_GEMMA_TOKENIZER']).resolve()
        compiler = list((source / 'bazel-bin/external').glob('*/lib/libIREECompiler.so'))
        if not compiler:
            raise FileNotFoundError('Build the compiler component first')
        env.update(CORAL_GEMMA_CHECKPOINT=str(checkpoint), CORAL_GEMMA_TOKENIZER=str(tokenizer),
                   IREE_PJRT_COMPILER_LIB_PATH=str(compiler[0]),
                   PJRT_NAMES_AND_LIBRARY_PATHS='coralnpu_plugin:' + str(source / 'bazel-bin/pjrt_plugin/libiree_pjrt_coralnpu_dylib.so'),
                   ENABLE_PJRT_COMPATIBILITY='1', IREE_PJRT_LOG_LEVEL='ERROR',
                   IREE_PJRT_CACHE_DIR=str(c.cache / 'iree-pjrt'),
                   IREE_PJRT_IREE_COMPILER_OPTIONS='--coralnpu-linker-script-path=' + str(source / 'bazel-bin/crt/coralnpu_tcm_highmem.ld'))
        log = output / 'gemma.log'
        prompt = 'France capital is'
        with log.open('w') as f:
            result = subprocess.run([python, source / 'examples/gemma3-jax-pjrt/chat.py',
                       '--max_new_tokens', '2'], input=prompt + '\nexit\n', text=True,
                       stdout=f, stderr=subprocess.STDOUT, env=env, cwd=source, timeout=c.a.timeout)
        if result.returncode:
            raise ValueError(f'Gemma exited {result.returncode}; inspect {log}')
        c.execute([python, source / 'integration/models/validate_gemma_log.py',
                   '--log', log, '--report', output / 'gemma-result.json'])
    print('HOST_CORAL_MODEL_ACCEPTANCE_PASS', c.a.suite)
