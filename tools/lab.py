#!/usr/bin/env python3
"""Build and exercise a pinned QEMU project without machine-specific defaults."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ('qemu-multiarch', 'qemu-d9m', 'qemu-sg200x')


def execute(argv, cwd=None, env=None):
    if str(argv[0]) == 'git':
        argv = ['git', '-c', 'core.fsmonitor=false', *argv[1:]]
    print('+', ' '.join(map(str, argv)), flush=True)
    subprocess.run(list(map(str, argv)), cwd=cwd, env=env, check=True)


def capture(argv, cwd=None):
    if str(argv[0]) == 'git':
        argv = ['git', '--no-optional-locks', '-c', 'core.fsmonitor=false', *argv[1:]]
    return subprocess.check_output(list(map(str, argv)), cwd=cwd, text=True).strip()


def configure_args(gui=False):
    args = ['--target-list=multiarch-softmmu', '--disable-hvf', '--disable-rust',
            '--disable-docs', '--disable-werror', '--disable-gtk', '--disable-sdl']
    if platform.system() == 'Darwin':
        args += ['--disable-pvg', '--enable-cocoa' if gui else '--disable-cocoa']
    elif gui:
        raise ValueError('The d9m Cocoa frontend requires macOS')
    return args


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('project', choices=PROJECTS)
    p.add_argument('action', choices=['doctor', 'fetch', 'build', 'run', 'test', 'payloads', 'firmware'])
    p.add_argument('--work', type=Path, default=Path(os.environ.get('LAB_WORK', ROOT / '.work')))
    p.add_argument('--source', type=Path, default=os.environ.get('QEMU_SOURCE'))
    p.add_argument('--binary', type=Path, default=os.environ.get('QEMU_BINARY'))
    p.add_argument('--images', type=Path, default=os.environ.get('GUEST_IMAGES'))
    p.add_argument('--firmware', type=Path)
    p.add_argument('--sd', type=Path)
    p.add_argument('--fip', type=Path)
    p.add_argument('--suite', choices=['smoke', 'linux', 'board'], default='smoke')
    p.add_argument('--main-isa', choices=['arm64', 'riscv64'], default='arm64')
    p.add_argument('--layout', choices=['two', 'five'], default='two')
    p.add_argument('--thread', choices=['single', 'multi'], default='multi')
    p.add_argument('--display', choices=['none', 'cocoa'], default='none',
                   help='Linux acceptance display; Cocoa requires a qemu-d9m binary')
    p.add_argument('--hold-seconds', type=int, default=0,
                   help='Keep the verified Linux acceptance VM open for inspection')
    p.add_argument('--jobs', type=int, default=min(os.cpu_count() or 2, 8))
    a = p.parse_args()
    if a.hold_seconds < 0:
        p.error('--hold-seconds must be nonnegative')
    if (a.display != 'none' or a.hold_seconds) and not (
            a.action == 'test' and a.suite == 'linux' and a.project != 'qemu-sg200x'):
        p.error('--display and --hold-seconds apply to the Linux acceptance test')
    a.work = a.work.expanduser().resolve()
    manifest = json.loads((ROOT / 'projects' / a.project / 'project.json').read_text())
    source = (a.source or a.work / 'sources' / a.project).expanduser().resolve()
    build = a.work / 'build' / a.project
    binary = (a.binary or build / 'qemu-system-multiarch').expanduser().resolve()
    images = (a.images or a.work / 'images').expanduser().resolve()
    tests = ROOT / 'common' / 'qemu-tests'
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    output = a.work / 'runs' / a.project / stamp

    if a.action == 'doctor':
        required = ['git', 'python3', 'ninja', 'pkg-config', 'cc']
        found = {tool: bool(shutil.which(tool)) for tool in required}
        print(json.dumps({'host': platform.system(), 'tools': found,
                          'source_present': source.exists(), 'binary_present': binary.is_file(),
                          'payload_build_host': 'Linux'}, indent=2))
        return 0 if all(found.values()) else 1

    if a.action == 'fetch':
        revision = manifest['commit']
        if len(revision) != 40:
            raise ValueError('Project source lock has not been finalized')
        if not source.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            source.mkdir()
            execute(['git', 'init', source])
            execute(['git', 'remote', 'add', 'origin', os.environ.get('QEMU_URL', manifest['qemu_url'])], source)
        if capture(['git', '-c', 'core.fsmonitor=false', 'status', '--porcelain'], source):
            raise ValueError('Source has local changes; use --source for development builds')
        execute(['git', 'fetch', '--depth=1', 'origin', revision], source)
        execute(['git', 'checkout', '--detach', revision], source)
        return 0

    if a.action == 'build':
        if not (source / 'configure').is_file():
            raise ValueError('Fetch QEMU first, or supply --source PATH')
        if a.jobs < 1:
            raise ValueError('--jobs must be positive')
        build.mkdir(parents=True, exist_ok=True)
        execute([source / 'configure', *configure_args(a.project == 'qemu-d9m')], build)
        execute(['ninja', '-C', build, '-j', a.jobs, 'qemu-system-multiarch'])
        info = {'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                'configure': configure_args(a.project == 'qemu-d9m')}
        if (source / '.git').exists():
            info['qemu_commit'] = capture(['git', 'rev-parse', 'HEAD'], source)
            info['source_dirty'] = bool(capture(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false', 'status', '--porcelain', '--untracked-files=no'], source))
        binary.with_name(binary.name + '.build.json').write_text(json.dumps(info, indent=2) + '\n')
        return 0

    if a.action in ('payloads', 'firmware'):
        execute([sys.executable, ROOT / 'tools' / 'payloads.py', a.action,
                 '--work', a.work, '--jobs', a.jobs,
                 *(['--firmware-target', 'sg200x' if a.project == 'qemu-sg200x' else 'r5f']
                   if a.action == 'firmware' else [])])
        return 0

    if not binary.is_file():
        raise ValueError('Build QEMU first, or supply --binary PATH')
    output.mkdir(parents=True)
    metadata = {'project': a.project, 'action': a.action, 'suite': a.suite,
                'host_os': platform.system(), 'host_arch': platform.machine(),
                'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                'version': capture([binary, '--version']).splitlines()[0],
                'status': 'RUNNING'}
    if (source / '.git').exists():
        metadata['source_checkout_commit'] = capture(['git', 'rev-parse', 'HEAD'], source)
        metadata['source_checkout_dirty'] = bool(capture(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false', 'status', '--porcelain', '--untracked-files=no'], source))
    build_info = binary.with_name(binary.name + '.build.json')
    if build_info.exists():
        info = json.loads(build_info.read_text())
        if info['binary_sha256'] != metadata['binary_sha256']:
            raise ValueError('Binary does not match its build provenance')
        metadata['binary_build'] = info
    else:
        metadata['binary_build'] = 'unrecorded; checkout metadata does not identify an external binary'
    (output / 'result.json').write_text(json.dumps(metadata, indent=2) + '\n')
    try:
        if a.action == 'test':
            if a.suite == 'smoke':
                if a.project == 'qemu-sg200x':
                    execute([sys.executable, tests / 'multiarch/sg200x/hardware.py',
                             '--qemu', binary, '--output', output / 'hardware', '--thread', a.thread])
                    execute([sys.executable, tests / 'multiarch/sg200x/peripherals.py',
                             '--qemu', binary, '--output', output / 'peripherals'])
                else:
                    for script in ['composed-virt.py', 'mttcg.py', 'multiarch-virt.py']:
                        execute([sys.executable, tests / 'hetero' / script, binary])
            elif a.project == 'qemu-sg200x':
                if a.suite == 'board':
                    if not a.sd or not a.fip:
                        raise ValueError('Board acceptance requires --sd and --fip')
                    execute([sys.executable, tests / 'multiarch/sg200x/debian-rtos.py',
                             '--qemu', binary, '--sd', a.sd.resolve(), '--fip', a.fip.resolve(),
                             '--output', output / 'board', '--thread', a.thread, '--reset',
                             *(['--rtos', a.firmware.resolve()] if a.firmware else [])])
                else:
                    if not a.firmware:
                        raise ValueError('Direct Linux/RTOS acceptance requires --firmware ELF')
                    execute([sys.executable, tests / 'multiarch/sg200x/linux-rtos.py',
                             '--qemu', binary, '--images', images, '--rtos', a.firmware.resolve(),
                             '--output', output / 'linux', '--thread', a.thread, '--reset-stress'])
            elif a.project == 'qemu-d9m' and a.layout == 'five':
                execute([sys.executable, tests / 'hetero/composed-linux-rtos.py',
                         binary, images / 'arm64', a.firmware or a.work / 'firmware',
                         output / 'linux', '--display', a.display, '--thread', a.thread,
                         '--hold-seconds', a.hold_seconds])
            else:
                execute([sys.executable, ROOT / 'tools' / 'dual-linux-shell-test.py',
                         '--qemu', binary, '--images', images, '--logs', output / 'linux',
                         '--main-isa', a.main_isa, '--thread', a.thread,
                         '--display', a.display, '--hold-seconds', a.hold_seconds])
        else:
            cmd = [binary, '-accel', 'tcg,thread=' + a.thread, '-monitor', 'none']
            if a.project == 'qemu-sg200x':
                if not a.sd or not a.fip:
                    raise ValueError('Interactive board boot requires --sd and --fip')
                cmd += ['-M', 'milkv-duos,boot-mode=fip,fip=' + str(a.fip.resolve()) + (',rtos=' + str(a.firmware.resolve()) if a.firmware else ''),
                        '-drive', 'file=' + str(a.sd.resolve()) + ',if=sd,format=raw,snapshot=on',
                        '-display', 'none', '-serial', 'stdio',
                        '-serial', 'tcp:127.0.0.1:54321,server=on,wait=off']
                print('Second UART: nc 127.0.0.1 54321; see project README for U-Boot commands')
            elif a.project == 'qemu-d9m' and a.layout == 'five':
                cmd += ['-M', 'multiarch-virt', '-display', 'cocoa,layout=2+3']
                fw = a.firmware or a.work / 'firmware'
                for i, (cpus, ram) in enumerate([(8, '1G'), (4, '512M')]):
                    cmd += ['-chardev', f'vc,id=linux{i},cols=80,rows=24', '-domain',
                            f'id=linux{i},platform=arm-virt,cpu=cortex-a55,cpus={cpus},ram={ram},chardev=linux{i}',
                            '-domain-loader', f'domain=linux{i},kernel={images}/arm64/Image,initrd={images}/arm64/rootfs.cpio.gz,append=console=ttyAMA0 rdinit=/init']
                for i in range(3):
                    cmd += ['-chardev', f'vc,id=rtos{i},cols=80,rows=16', '-domain',
                            f'id=rtos{i},platform=arm-mcu,cpu=cortex-r5f,cpus=1,ram=16M,chardev=rtos{i}',
                            '-domain-loader', f'domain=rtos{i},firmware={fw}/freertos-r5f-{i+1}.elf']
            elif a.project == 'qemu-d9m':
                isa = a.main_isa
                cmd += ['-M', 'multiarch-virt-' + ('arm' if isa == 'arm64' else 'riscv') + ',mcu=rv32-mcu,mcu-ram=32M,mcu-kernel=' + str(images / 'rv32/Image'),
                        '-m', '256M', '-kernel', images / isa / 'Image', '-initrd', images / isa / 'rootfs.cpio.gz',
                        '-append', 'console=' + ('ttyAMA0' if isa == 'arm64' else 'ttyS0') + ' rdinit=/init',
                        '-display', 'cocoa,split=on', '-serial', 'vc', '-serial', 'vc']
                if isa == 'riscv64':
                    cmd += ['-bios', images / isa / 'fw_dynamic.bin']
            else:
                profile = 'arm-virt' if a.main_isa == 'arm64' else 'riscv-virt'
                cpu = 'cortex-a55' if a.main_isa == 'arm64' else 'rv64'
                loader = f'domain=linux0,kernel={images}/{a.main_isa}/Image,initrd={images}/{a.main_isa}/rootfs.cpio.gz,append=console=' + ('ttyAMA0' if a.main_isa == 'arm64' else 'ttyS0') + ' rdinit=/init'
                if a.main_isa == 'riscv64':
                    loader += ',firmware=' + str(images / 'riscv64/fw_dynamic.bin')
                cmd += ['-M', 'multiarch-virt', '-display', 'none', '-chardev', 'stdio,id=console',
                        '-domain', f'id=linux0,platform={profile},cpu={cpu},cpus=2,ram=512M,chardev=console',
                        '-domain-loader', loader]
            execute(cmd)
        metadata['status'] = 'PASS' if a.action == 'test' else 'FINISHED_UNAUDITED'
    except KeyboardInterrupt:
        verified = output / 'linux' / 'result.json'
        if (a.action == 'test' and a.suite == 'linux' and a.hold_seconds and
                verified.is_file() and json.loads(verified.read_text()).get('status') == 'PASS'):
            metadata['status'] = 'PASS'
            print('Verified VM closed by user')
        else:
            metadata['status'] = 'FAIL'
            raise
    except BaseException:
        metadata['status'] = 'FAIL'
        raise
    finally:
        (output / 'result.json').write_text(json.dumps(metadata, indent=2) + '\n')
        print('Result:', output / 'result.json')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
