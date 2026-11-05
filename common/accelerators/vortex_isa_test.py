#!/usr/bin/env python3
"""Run real guest acceptance, bounded in wall time, and preserve its serial log."""
import argparse
import re
import subprocess
import time
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--images', type=Path, required=True)
    parser.add_argument('--backend', type=Path, required=True)
    parser.add_argument('--mobilenet', action='store_true')
    parser.add_argument('--initrd', type=Path, required=True)
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--mnist', action='store_true', help='require MNIST layer and image checks')
    parser.add_argument('--mnist-samples', type=int, default=100, help='expected sample count; default full acceptance')
    args = parser.parse_args()
    if not 1 <= args.mnist_samples <= 100 or (not args.mnist and args.mnist_samples != 100):
        parser.error('--mnist-samples requires --mnist and a value in 1..100')
    image = args.images

    backend = args.backend
    cmd = [str(args.binary),
           '-M', 'virt', '-m', '512M', '-smp', '2', '-nographic', '-monitor', 'none',
           '-bios', str(image / 'fw_dynamic.bin'), '-kernel', str(image / 'Image'),
           '-initrd', str(args.initrd.resolve()), '-append', 'console=ttyS0 rdinit=/vortex-init',
           '-device', f'vortex-gpu,backend={backend}']
    begin = time.monotonic()
    args.log.parent.mkdir(parents=True, exist_ok=True)
    with args.log.open('w') as output:
        proc = subprocess.Popen(cmd, stdout=output, stderr=subprocess.STDOUT)
        try:
            code = proc.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            raise SystemExit(f'FAIL timeout: {args.log}')
    text = args.log.read_text(errors='replace')
    required = ['VORTEX_PCI_WINDOW_RESET_PASS', 'VORTEX_BUDGET_RECOVERY_PASS',
                'VORTEX_CASE_EXIT=transport:0', 'VORTEX_CASE_EXIT=demo64:0',
                'VORTEX_CASE_EXIT=demo2d:0', 'VORTEX_CASE_EXIT=sgemm16:0',
                'VORTEX_CASE_EXIT=sgemm32:0', 'VORTEX_GUEST_ALL_PASS', 'VORTEX_TEST_EXIT=0']
    if args.mnist:
        required += ['VORTEX_CASE_EXIT=mnist:0', 'VORTEX_MNIST_ALL_PASS',
                     'MNIST_GPU_LAYER1_PERF', 'MNIST_GPU_LAYER2_PERF',
                     'MNIST_LAYER_CHECK=GPU_vs_CPU_hidden', 'MNIST_LAYER_CHECK=GPU_vs_CPU_logits',
                     'MNIST_LAYER_CHECK=GPU_vs_Python_hidden', 'MNIST_LAYER_CHECK=GPU_vs_Python_logits']
        results = re.findall(r'MNIST_RESULT index=(\d+) label=(\d+) gpu=(\d+) cpu=(\d+) python=(\d+) correct=(\d+)', text)
        if len(results) != args.mnist_samples or [int(r[0]) for r in results] != list(range(args.mnist_samples)):
            raise SystemExit('FAIL: missing or duplicate MNIST image results')
        if any(r[2] != r[3] or r[2] != r[4] for r in results):
            raise SystemExit('FAIL: MNIST prediction reference mismatch')
        if any('errors=0' not in line for line in text.splitlines() if 'MNIST_LAYER_CHECK=' in line):
            raise SystemExit('FAIL: MNIST layer mismatch')
        for layer in (1, 2):
            part = text.split(f'MNIST_GPU_LAYER{layer}_PERF', 1)[-1]
            match = re.search(r'PERF: instrs=(\d+), cycles=(\d+)', part)
            if not match or min(map(int, match.groups())) <= 0:
                raise SystemExit(f'FAIL: no GPU execution evidence for layer {layer}')
    if args.mobilenet:
        required += ['VORTEX_CASE_EXIT=mobilenet:0', 'VORTEX_MOBILENET_ALL_PASS']
        checks = re.findall(r'MOBILENET_LAYER_CHECK name=([^ ]+) compare=([^ ]+) elements=(\d+) max_abs=([^ ]+) atol=([^ ]+) rtol=([^ ]+) errors=(\d+)', text)
        launches = re.findall(r'MOBILENET_GPU_LAUNCH layer=(\d+)/(\d+)', text)
        if len(checks) != 207 or any(int(x[-1]) for x in checks) or [int(x[0]) for x in launches] != list(range(1, 70)):
            raise SystemExit('FAIL: 69 model operations and 207 comparisons required')
    missing = [marker for marker in required if marker not in text]
    if code or missing or 'VORTEX_TEST_EXIT=1' in text:
        raise SystemExit(f'FAIL qemu_exit={code}, missing={missing}: {args.log}')
    print(f'PASS: Linux guest transport + 4 GPU kernels' + (f' + {args.mnist_samples} MNIST images' if args.mnist else '') + f'; elapsed={time.monotonic()-begin:.2f}s')
    print(args.log)

if __name__ == '__main__':
    main()
