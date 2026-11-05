#!/usr/bin/env python3
"""Acquire, build and verify the pinned accelerator experiments."""
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
sys.path.insert(0, str(ROOT / 'common/accelerators'))
PROJECTS = ('qemu-coral-isa', 'qemu-coral-rtl', 'qemu-vortex-isa', 'qemu-vortex-rtl')


def run(cmd, cwd=None, env=None):
    cmd = list(map(str, cmd))
    print('+', ' '.join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def git(path, *args):
    return subprocess.check_output(['git', '-c', 'core.fsmonitor=false', '-C', str(path),
                                    *args], text=True).strip()


class Context:
    def __init__(self, args):
        self.a = args
        self.manifest = json.loads((ROOT / 'projects' / args.project / 'project.json').read_text())
        self.work = args.work.expanduser().resolve()
        self.sources = (args.sources or self.work / 'sources' / args.project).expanduser().resolve()
        profile_dir = args.profile if args.project == 'qemu-coral-rtl' else ''
        artifact_dir = args.arch if args.project == 'qemu-coral-isa' else profile_dir
        self.build = (args.build or self.work / 'build' / args.project / profile_dir).expanduser().resolve()
        self.artifacts = (args.artifacts or self.work / 'artifacts' / args.project / artifact_dir).expanduser().resolve()
        self.cache = self.work / 'cache'
        self.env = os.environ.copy()
        for name, suffix in {'TMPDIR': 'tmp', 'XDG_CACHE_HOME': 'xdg', 'PIP_CACHE_DIR': 'pip',
                             'CCACHE_DIR': 'ccache', 'KERAS_HOME': 'keras', 'HF_HOME': 'huggingface',
                             'TORCH_HOME': 'torch', 'CLANG_MODULE_CACHE_PATH': 'clang',
                             'PYTHONPYCACHEPREFIX': 'python'}.items():
            path = self.cache / suffix
            path.mkdir(parents=True, exist_ok=True)
            self.env[name] = str(path)
        self.build.mkdir(parents=True, exist_ok=True)
        self.artifacts.mkdir(parents=True, exist_ok=True)

    def source(self, name):
        return self.sources / name

    def execute(self, cmd, cwd=None, env=None):
        run(cmd, cwd, self.env if env is None else env)

    def bazel(self, name, version, targets, extra=(), action='build'):
        executable = os.environ.get('BAZEL' + version, 'bazel' + version)
        cc = os.environ.get('HOST_CC', shutil.which('clang-19') or shutil.which('cc'))
        cxx = os.environ.get('HOST_CXX', shutil.which('clang++-19') or shutil.which('c++'))
        if not cc or not cxx:
            raise ValueError('Set HOST_CC and HOST_CXX to installed host compilers')
        command = [executable, '--output_user_root=' + str(self.cache / ('bazel' + version)),
                      '--host_jvm_args=-Djava.io.tmpdir=' + str(self.cache / 'tmp'),
                      action, '--jobs=' + str(self.a.jobs),
                      '--repository_cache=' + str(self.cache / 'bazel-repositories'),
                      '--repo_env=CC=' + cc, '--repo_env=CXX=' + cxx,
                      '--action_env=CC=' + cc,
                      *(['--repo_contents_cache=' + str(self.cache / 'bazel9-contents')] if version == '9' else []),
                      *extra, *targets]
        if action == 'cquery':
            return subprocess.check_output(list(map(str, command)), cwd=self.source(name),
                                           env=self.env, text=True).splitlines()
        self.execute(command, self.source(name))


def fetch(c):
    for name, lock in c.manifest['sources'].items():
        if c.a.component != 'all' and c.a.component != name:
            continue
        src = c.source(name)
        if not src.exists():
            src.mkdir(parents=True)
            c.execute(['git', 'init', src])
            url = os.environ.get('LAB_' + name.upper() + '_URL', lock['url'])
            c.execute(['git', 'remote', 'add', 'origin', url], src)
        if git(src, 'status', '--porcelain'):
            raise ValueError(f'{src} has changes; preserve them or choose another --sources directory')
        c.execute(['git', 'fetch', '--depth=1', 'origin', lock['commit']], src)
        c.execute(['git', 'checkout', '--detach', lock['commit']], src)
        if git(src, 'rev-parse', 'HEAD') != lock['commit']:
            raise ValueError(f'Source lock mismatch: {name}')
        if name == 'vortex':
            c.execute(['git', 'submodule', 'update', '--init', '--recursive', '--depth=1', '--',
                       'third_party/softfloat', 'third_party/ramulator', 'third_party/hardfloat',
                       'third_party/cvfpu', 'third_party/cocogfx'], src)
        if name == 'compiler':
            c.execute(['git', 'submodule', 'update', '--init', '--depth=1', '--',
                       'third_party/iree', 'third_party/llvm-project', 'third_party/coralnpu'], src)
            c.execute(['git', 'submodule', 'update', '--init', '--depth=1', '--', '.',
                       ':(exclude)third_party/llvm-project'], src / 'third_party/iree')
            for patch in sorted(src.glob('*-*.patch')):
                target = ('iree' if patch.name.startswith('iree-') else
                          'llvm-project' if patch.name.startswith('llvm-project-') else None)
                if target:
                    apply_patch(c, src / 'third_party' / target, patch)


def apply_patch(c, source, patch):
    base = ['git', '-C', str(source), 'apply']
    if subprocess.run([*base, '--reverse', '--check', str(patch)],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        return
    c.execute([*base, '--check', patch])
    c.execute([*base, patch])


def build(c):
    from recipes import build_backend, build_qbox, build_guest, build_rtl, build_driver
    part = c.a.component
    if part == 'all':
        parts = ['qemu', 'backend', 'guest'] if c.a.project.endswith('isa') else ['rtl', 'qbox', 'guest', 'driver']
    else:
        parts = [part]
    for part in parts:
        if part == 'qemu':
            source = c.source('qemu')
            out = c.build / 'qemu'
            out.mkdir(exist_ok=True)
            options = ['--target-list=aarch64-softmmu,riscv64-softmmu', '--disable-docs',
                       '--disable-rust', '--disable-werror', '--disable-gtk', '--disable-sdl']
            c.execute([source / 'configure', *options], out)
            c.execute(['ninja', '-C', out, '-j', c.a.jobs,
                       'qemu-system-aarch64', 'qemu-system-riscv64'])
            for target in ('aarch64', 'riscv64'):
                binary = out / ('qemu-system-' + target)
                info = {'commit': git(source, 'rev-parse', 'HEAD'),
                        'dirty': bool(git(source, 'status', '--porcelain', '--untracked-files=no')),
                        'sha256': hashlib.sha256(binary.read_bytes()).hexdigest(), 'configure': options}
                binary.with_suffix('.build.json').write_text(json.dumps(info, indent=2) + '\n')
        elif part == 'backend':
            build_backend(c)
        elif part == 'compiler':
            if c.a.project != 'qemu-coral-isa':
                raise ValueError('compiler applies to qemu-coral-isa')
            c.bazel('compiler', '9', ['@iree_core//tools:iree-compile',
                    '@iree_core//lib:libIREECompiler.so', '//pjrt_plugin:iree_pjrt_coralnpu_dylib',
                    '//crt:coralnpu_tcm_highmem_ld', '//integration/models:validate_mobilenet'],
                    ['--config=dev', '--repo_env=PATH=' + c.env['PATH']])
        elif part == 'rtl':
            build_rtl(c)
        elif part == 'qbox':
            build_qbox(c)
        elif part == 'driver':
            if platform.system() != 'Linux':
                raise ValueError('Kernel modules must be built on Linux')
            build_driver(c)
        elif part == 'guest':
            if platform.system() != 'Linux':
                raise ValueError('Guest workloads and kernel modules must be built on Linux')
            build_guest(c)
        else:
            raise ValueError('Unknown build component: ' + part)


def test(c, interactive=False):
    from acceptance import acceptance
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    output = c.work / 'runs' / c.a.project / stamp
    output.mkdir(parents=True)
    report = {'project': c.a.project, 'suite': c.a.suite, 'status': 'RUNNING',
              'host': platform.system(), 'arch': platform.machine(), 'sources': {},
              'expected_source_lock': c.manifest['sources']}
    for name in c.manifest['sources']:
        src = c.source(name)
        if (src / '.git').exists():
            report['sources'][name] = {'commit': git(src, 'rev-parse', 'HEAD'),
                    'dirty': bool(git(src, 'status', '--porcelain', '--untracked-files=no'))}
    path = output / 'result.json'
    path.write_text(json.dumps(report, indent=2) + '\n')
    try:
        acceptance(c, output, interactive)
        report['status'] = 'PASS'
    except BaseException as exc:
        report.update(status='FAIL', error=str(exc))
        raise
    finally:
        path.write_text(json.dumps(report, indent=2) + '\n')
        print(path)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('project', choices=PROJECTS)
    p.add_argument('action', choices=['doctor', 'fetch', 'build', 'prepare', 'run', 'test'])
    p.add_argument('--work', type=Path, default=Path(os.environ.get('LAB_WORK', ROOT / '.work')))
    p.add_argument('--sources', type=Path, default=os.environ.get('LAB_SOURCE_ROOT'))
    p.add_argument('--build', type=Path, default=os.environ.get('LAB_BUILD_ROOT'))
    p.add_argument('--artifacts', type=Path, default=os.environ.get('LAB_ARTIFACTS'))
    p.add_argument('--images', type=Path, default=os.environ.get('GUEST_IMAGES'))
    p.add_argument('--component', default='all')
    p.add_argument('--suite', choices=['backend', 'smoke', 'linux', 'digits', 'mnist',
                   'mobilenet', 'interface', 'full', 'gemma', 'gemma-guest'], default='linux')
    p.add_argument('--arch', choices=['arm64', 'riscv64'], default='riscv64')
    p.add_argument('--profile', choices=['small', 'gemma'], default='small')
    p.add_argument('--timeout', type=int, default=600)
    p.add_argument('--jobs', type=int, default=min(os.cpu_count() or 2, 8))
    p.add_argument('--samples', type=int, default=2)
    p.add_argument('--backpressure', action='store_true')
    a = p.parse_args()
    if a.jobs < 1 or a.timeout < 1 or not 1 <= a.samples <= 100:
        p.error('jobs/timeout must be positive; samples must be 1..100')
    if a.action == 'doctor':
        names = ['git', 'python3', 'ninja', 'cmake', 'pkg-config', 'cc']
        tools = {name: shutil.which(name) is not None for name in names}
        print(json.dumps({'host': platform.system(), 'tools': tools,
                          'supported_hosts': ['Linux'], 'guest_build_host': 'Linux',
                          'source_lock': str(ROOT / 'projects' / a.project / 'project.json')}, indent=2))
        return 0 if all(tools.values()) else 1
    c = Context(a)
    if a.action == 'fetch':
        fetch(c)
    elif a.action == 'build':
        build(c)
    elif a.action == 'prepare':
        from recipes import prepare
        prepare(c)
    else:
        test(c, a.action == 'run')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, FileNotFoundError, subprocess.CalledProcessError) as exc:
        sys.exit(str(exc))
