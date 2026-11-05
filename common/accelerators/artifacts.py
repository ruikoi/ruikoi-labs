"""Append generated workloads to a supplied BusyBox Linux initramfs."""
import gzip
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess


def header(name, size, mode, ino):
    raw = name.encode() + b'\0'
    fields = [ino, mode, 0, 0, 1, 0, size, 0, 0, 0, 0, len(raw), 0]
    block = b'070701' + b''.join(f'{x:08x}'.encode() for x in fields) + raw
    return block + b'\0' * (-len(block) % 4)


def pack(base, target, init_name, init, files):
    with gzip.open(target, 'wb', compresslevel=1) as out:
        with gzip.open(base, 'rb') as src:
            shutil.copyfileobj(src, out, 1 << 20)
            out.write(b'\0' * (-src.tell() % 4))
        data = init.encode()
        out.write(header(init_name, len(data), stat.S_IFREG | 0o755, 10000))
        out.write(data)
        out.write(b'\0' * (-len(data) % 4))
        for ino, (name, src, mode) in enumerate(files, 10001):
            size = src.stat().st_size if src.is_file() else 0
            out.write(header(name, size, mode, ino))
            if src.is_file():
                with src.open('rb') as f:
                    shutil.copyfileobj(f, out, 1 << 20)
                out.write(b'\0' * (-size % 4))
        out.write(header('TRAILER!!!', 0, 0, 30000))


START = '''#!/bin/sh
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mkdir -p /tmp
uname -a
'''
END = '''sync
poweroff -f
while true; do sleep 1; done
'''


def prepare(c):
    if not c.a.images:
        raise ValueError('Supply --images DIR containing arm64/ and riscv64/ Linux payloads')
    images = c.a.images.expanduser().resolve() / c.a.arch
    guest = c.build / 'guest'
    rtl = c.a.project.endswith('rtl')
    coral = 'coral' in c.a.project
    files = []
    if coral and not rtl and c.a.suite == 'gemma-guest':
        models = Path(os.environ['GEMMA_ARTIFACTS']).resolve()
        from recipes import HERE
        stage = c.build / 'gemma-guest-artifacts'
        stage.mkdir(exist_ok=True)
        for name in ['tokenizer.model', 'gemma-platform.bin', 'gemma-embeddings.bf16', 'gemma-ddr.bin']:
            path = stage / name
            if not path.exists():
                path.symlink_to(models / name)
        for name in ['guest-chat', 'gemma3_runner.elf']:
            shutil.copy2(guest / name, stage)
        c.execute(['python3', HERE / 'pack_gemma_guest.py', '--base', images / 'rootfs.cpio.gz',
                   '--artifacts', stage, '--output', c.artifacts / 'riscv64-gemma.cpio.gz',
                   '--mode', 'prompt', '--prompt', os.environ.get('CORAL_GEMMA_PROMPT', '介绍下你是什么模型')])
        for name in ['Image', 'fw_dynamic.bin']:
            shutil.copy2(images / name, c.artifacts)
        return
    if coral and not rtl:
        names = ['coral-runner-' + c.a.arch, 'add.elf', 'model.elf']
        init = START + '/coral-runner /add.elf /model.elf'
        if c.a.suite == 'digits':
            names += ['digits.elf']
            init += ' /digits.elf'
        init += '\nrc=$?\necho CORAL_TEST_EXIT=$rc\n' + END
        for name in names:
            files.append(('coral-runner' if name.startswith('coral-runner-') else name,
                          guest / name, stat.S_IFREG | 0o755))
        initname, archive = 'coral-init', c.a.arch + '-coral.cpio.gz'
    elif coral and c.a.profile == 'small':
        names = ['coral-rtl-runner', 'add.elf', 'digits.elf', 'digits-external.elf',
                 'cases.bin', 'coral_rtl_irq.ko']
        init = START + '''insmod /coral_rtl_irq.ko || { dmesg | tail -n 30; echo CORAL_RTL_TEST_EXIT=1; poweroff -f; exit 1; }
/coral-rtl-runner /add.elf /digits.elf /digits-external.elf /cases.bin
rc=$?
echo CORAL_RTL_TEST_EXIT=$rc
''' + END
        files = [(n, guest / n, stat.S_IFREG | 0o755) for n in names]
        initname, archive = 'coral-rtl-init', 'riscv64-rtl.cpio.gz'
    elif coral:
        models = Path(os.environ['GEMMA_ARTIFACTS']).resolve()
        names = ['guest-gemma', 'gemma3_runner.elf', 'gemma-platform.bin',
                 'gemma-embeddings.bf16', 'gemma-layer0-reference.bin', 'coral_rtl_irq.ko']
        init = START + '''echo GEMMA_QBOX_LINUX_START
insmod /coral_rtl_irq.ko || { dmesg | tail -n 30; echo GEMMA_QBOX_TEST_EXIT=1; poweroff -f; exit 1; }
/guest-gemma /gemma3_runner.elf /gemma-platform.bin /gemma-embeddings.bf16 /gemma-layer0-reference.bin 0
rc=$?
echo GEMMA_QBOX_TEST_EXIT=$rc
''' + END
        files = [(n, (guest if n in {'guest-gemma','gemma3_runner.elf','coral_rtl_irq.ko'} else models) / n, stat.S_IFREG | 0o755) for n in names]
        shutil.copy2(models / 'gemma-ddr.bin', c.artifacts)
        initname, archive = 'coral-rtl-init', 'riscv64-rtl.cpio.gz'
    else:
        from vortex_guest import init_script
        payload = c.build / 'payload'
        for path in sorted(payload.rglob('*')):
            files.append((str(path.relative_to(payload)), path,
                          (stat.S_IFDIR if path.is_dir() else stat.S_IFREG) | 0o755))
        if not (payload / 'vortex/demo').is_file():
            raise ValueError('Build the Vortex guest payload first')
        init = START + init_script(c) + END
        initname = 'vortex-rtl-init' if rtl else 'vortex-init'
        archive = 'riscv64-vortex-rtl.cpio.gz' if rtl else 'riscv64-vortex.cpio.gz'
    target = c.artifacts / archive
    pack(images / 'rootfs.cpio.gz', target, initname, init, files)
    shutil.copy2(images / 'Image', c.artifacts)
    if c.a.arch == 'riscv64':
        shutil.copy2(images / 'fw_dynamic.bin', c.artifacts)
    if rtl:
        relative = ('integration/coral-rtl/' + c.a.profile if coral else 'integration/vortex-rtl')
        text = (c.source('qbox') / relative / 'platform.dts').read_text()
        text = text.replace('linux,initrd-end = <0 0x88000000>',
                            f'linux,initrd-end = <0 0x{0x88000000 + target.stat().st_size:x}>')
        (c.artifacts / 'platform.dts').write_text(text)
        c.execute(['dtc', '-I', 'dts', '-O', 'dtb', c.artifacts / 'platform.dts',
                   '-o', c.artifacts / 'platform.dtb'])
    (c.artifacts / 'workload.json').write_text(json.dumps({'project': c.a.project,
        'arch': c.a.arch, 'profile': c.a.profile, 'suite': c.a.suite, 'samples': c.a.samples}, indent=2) + '\n')
    print('ARTIFACTS_READY', target)
