#!/usr/bin/env python3
"""Fetch pinned public sources and build guest payloads on Linux."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    'buildroot': ('https://gitlab.com/buildroot.org/buildroot.git',
                  '7fdeadb3b9f12f78e5fccca914d7fef47090ab9a'),
    'opensbi': ('https://github.com/riscv-software-src/opensbi.git',
                'a32a91069119e7a5aa31e6bc51d5e00860be3d80'),
    'freertos': ('https://github.com/FreeRTOS/FreeRTOS-Kernel.git',
                 'a4b28e35103d699edf074dfff4835921b481b301'),
}
KERNEL_URL = 'https://cdn.kernel.org/pub/linux/kernel/v7.x/linux-7.2.8.tar.xz'
KERNEL_SHA256 = '12e8d5a973d1ad7c5a5c69882e4022b131ed715db7003fdcd760ddf8c3e51941'


def run(args, cwd=None, env=None):
    print('+', ' '.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, env=env, check=True)


def fetch_git(work, name):
    url, revision = SOURCES[name]
    dest = work / 'sources' / name
    if not dest.exists():
        dest.mkdir(parents=True)
        run(['git', 'init', dest])
        run(['git', 'remote', 'add', 'origin', url], dest)
    actual = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=dest, text=True,
                            capture_output=True).stdout.strip()
    if actual != revision:
        if subprocess.check_output(['git', 'status', '--porcelain'], cwd=dest).strip():
            raise ValueError('Refusing to replace modified dependency: ' + name)
        run(['git', 'fetch', '--depth=1', 'origin', revision], dest)
        run(['git', 'checkout', '--detach', revision], dest)
    return dest


def verify_file(path, expected):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    if h.hexdigest() != expected:
        raise ValueError('SHA256 mismatch: ' + path.name)


def safe_extract(archive, destination):
    with tarfile.open(archive) as tf:
        root = destination.resolve()
        for member in tf.getmembers():
            target = (root / member.name).resolve()
            if root != target and root not in target.parents:
                raise ValueError('Archive path escapes destination')
            if member.issym() or member.islnk():
                target = ((root / member.name).parent / member.linkname).resolve()
                if root != target and root not in target.parents:
                    raise ValueError('Archive link escapes destination')
        tf.extractall(destination)


def kernel_source(work):
    dest = work / 'sources' / 'linux-7.2.8'
    if not dest.exists():
        download = work / 'downloads' / 'linux-7.2.8.tar.xz'
        download.parent.mkdir(parents=True, exist_ok=True)
        if not download.exists():
            temporary = download.with_suffix('.partial')
            urllib.request.urlretrieve(KERNEL_URL, temporary)
            verify_file(temporary, KERNEL_SHA256)
            temporary.replace(download)
        verify_file(download, KERNEL_SHA256)
        dest.parent.mkdir(parents=True, exist_ok=True)
        safe_extract(download, dest.parent)
    return dest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['payloads', 'firmware'])
    p.add_argument('--work', required=True, type=Path)
    p.add_argument('--jobs', default=8, type=int)
    p.add_argument('--arch', choices=['arm64', 'riscv64', 'rv32', 'all'], default='all')
    p.add_argument('--firmware-target', choices=['r5f', 'sg200x', 'all'], default='all')
    a = p.parse_args()
    work = a.work.resolve()
    env = os.environ.copy()
    env.update(KBUILD_BUILD_USER='ruikoi', KBUILD_BUILD_HOST='builder',
               KBUILD_BUILD_TIMESTAMP='2026-10-28 00:00:00 UTC', SOURCE_DATE_EPOCH='1793145600')
    tests = ROOT / 'common/qemu-tests'
    if a.action == 'firmware':
        kernel = fetch_git(work, 'freertos')
        out = work / 'firmware'
        out.mkdir(parents=True, exist_ok=True)
        if a.firmware_target in ('r5f', 'all'):
            clang = os.environ.get('CLANG') or shutil.which('clang')
            linker = os.environ.get('LD_LLD') or shutil.which('ld.lld')
            if not clang or not linker:
                raise ValueError('Install clang and lld, or set CLANG and LD_LLD')
            run(['sh', tests / 'hetero/build-freertos-virt.sh', kernel, clang, linker, out], env=env)
        if a.firmware_target in ('sg200x', 'all'):
            cross = os.environ.get('RISCV64_CC') or shutil.which('riscv64-linux-gnu-gcc')
            if not cross:
                candidates = sorted((work / 'build/buildroot-riscv64/host/bin').glob('riscv64*-gcc'))
                cross = str(candidates[0]) if candidates else None
            if not cross:
                raise ValueError('Install an RV64 GCC toolchain or set RISCV64_CC')
            run(['sh', tests / 'multiarch/sg200x/build-freertos.sh', kernel, cross,
                 out / 'sg200x-freertos.elf'], env=env)
        return
    if platform.system() != 'Linux':
        raise ValueError('Linux/Buildroot payloads must be built on Linux; transfer images to macOS afterwards')
    br = fetch_git(work, 'buildroot')
    linux = kernel_source(work)
    configs = ROOT / 'projects/qemu-multiarch/configs'
    arches = ['arm64', 'riscv64', 'rv32'] if a.arch == 'all' else [a.arch]
    for arch in arches:
        brout = work / 'build' / ('buildroot-' + arch)
        kout = work / 'build' / ('linux-' + arch)
        images = work / 'images' / arch
        for d in (brout, kout, images):
            d.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(configs / (arch + '-buildroot.config'), brout / '.config')
        brmake = ['make', '-C', br, 'O=' + str(brout)]
        run(brmake + ['olddefconfig'], env=env)
        run(brmake + ['-j' + str(a.jobs)], env=env)
        shutil.copyfile(configs / (arch + '-linux.config'), kout / '.config')
        compilers = sorted((brout / 'host/bin').glob('*-gcc'))
        compilers = [x for x in compilers if x.name.startswith(('aarch64-', 'riscv'))]
        if not compilers:
            raise ValueError('Buildroot target compiler not found')
        cross = str(compilers[0])[:-3]
        karch = 'arm64' if arch == 'arm64' else 'riscv'
        kmake = ['make', '-C', linux, 'O=' + str(kout), 'ARCH=' + karch,
                 'CROSS_COMPILE=' + cross]
        initrd = brout / 'images/rootfs.cpio.gz'
        if arch == 'rv32':
            run([linux / 'scripts/config', '--file', kout / '.config',
                 '--set-str', 'INITRAMFS_SOURCE', initrd], env=env)
        run(kmake + ['olddefconfig'], env=env)
        run(kmake + ['-j' + str(a.jobs), 'Image'], env=env)
        shutil.copyfile(kout / 'arch' / karch / 'boot/Image', images / 'Image')
        shutil.copyfile(initrd, images / 'rootfs.cpio.gz')
        shutil.copyfile(kout / '.config', images / 'linux.config')
        shutil.copyfile(brout / '.config', images / 'buildroot.config')
        if arch == 'riscv64':
            sbi = fetch_git(work, 'opensbi')
            sbiout = work / 'build/opensbi'
            run(['make', '-C', sbi, 'O=' + str(sbiout), 'PLATFORM=generic',
                 'CROSS_COMPILE=' + cross, '-j' + str(a.jobs)], env=env)
            shutil.copyfile(sbiout / 'platform/generic/firmware/fw_dynamic.bin', images / 'fw_dynamic.bin')
            # The 64 MiB Duo board needs a smaller kernel than the generic one.
            duo = work / 'build/linux-duo'
            duo.mkdir(parents=True, exist_ok=True)
            dm = ['make', '-C', linux, 'O=' + str(duo), 'ARCH=riscv', 'CROSS_COMPILE=' + cross]
            run(dm + ['KCONFIG_ALLCONFIG=' + str(tests / 'multiarch/sg200x/duo-linux.config'), 'allnoconfig'], env=env)
            run(dm + ['-j' + str(a.jobs), 'Image'], env=env)
            dest = work / 'images/sg200x/duo-linux'
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(duo / 'arch/riscv/boot/Image', dest / 'Image')
        manifest = {f.name: hashlib.sha256(f.read_bytes()).hexdigest()
                    for f in images.iterdir() if f.is_file() and f.name != 'sha256.json'}
        (images / 'sha256.json').write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    main()
