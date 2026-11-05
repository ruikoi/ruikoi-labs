#!/usr/bin/env python3
"""Train an FP32 MNIST MLP and export weights, PGM images and reference layers."""
import argparse
import gzip
import hashlib
import json
import os
import platform
import struct
import urllib.request
from pathlib import Path

os.environ.setdefault('OPENBLAS_NUM_THREADS', '4')
import numpy as np

FILES = {
    'train-images-idx3-ubyte.gz': 'f68b3c2dcbeaaa9fbdd348bbdeb94873',
    'train-labels-idx1-ubyte.gz': 'd53e105ee54ea40749a09fcbcd1e9432',
    't10k-images-idx3-ubyte.gz': '9fb629c4189551a2d022fa330f9573f3',
    't10k-labels-idx1-ubyte.gz': 'ec29112dd5afa0611ce80d1b7f02629c',
}
BASE = 'https://storage.googleapis.com/cvdf-datasets/mnist/'


def dataset(root, name):
    path = root / name
    if not path.exists():
        temporary = path.with_suffix('.download')
        urllib.request.urlretrieve(BASE + name, temporary)
        assert hashlib.md5(temporary.read_bytes()).hexdigest() == FILES[name]
        temporary.replace(path)
    assert hashlib.md5(path.read_bytes()).hexdigest() == FILES[name]
    raw = gzip.decompress(path.read_bytes())
    magic, count = struct.unpack_from('>II', raw)
    if magic == 2051:
        assert struct.unpack_from('>II', raw, 8) == (28, 28)
        assert len(raw) == 16 + count * 784
        return np.frombuffer(raw, np.uint8, offset=16).reshape(count, 784)
    assert magic == 2049 and len(raw) == 8 + count
    return np.frombuffer(raw, np.uint8, offset=8)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--epochs', type=int, default=10)
    args = parser.parse_args()
    out = args.out
    cache = Path(os.environ.get('XDG_CACHE_HOME', str(Path(os.environ.get('LAB_WORK', '.work')) / 'cache'))) / 'mnist'
    export = out
    cache.mkdir(parents=True, exist_ok=True)
    (export / 'images').mkdir(parents=True, exist_ok=True)
    train_u8 = dataset(cache, 'train-images-idx3-ubyte.gz')
    train_y = dataset(cache, 'train-labels-idx1-ubyte.gz')
    test_u8 = dataset(cache, 't10k-images-idx3-ubyte.gz')
    test_y = dataset(cache, 't10k-labels-idx1-ubyte.gz')
    x = train_u8.astype(np.float32) / np.float32(255)
    test = test_u8.astype(np.float32) / np.float32(255)
    rng = np.random.default_rng(20261011)
    params = [
        (rng.standard_normal((64, 784)) * np.sqrt(2 / 784)).astype(np.float32),
        np.zeros(64, np.float32),
        (rng.standard_normal((10, 64)) * np.sqrt(2 / 64)).astype(np.float32),
        np.zeros(10, np.float32),
    ]
    first = [np.zeros_like(p) for p in params]
    second = [np.zeros_like(p) for p in params]
    step = 0
    history = []
    for epoch in range(args.epochs):
        order = rng.permutation(len(x))
        losses = []
        for begin in range(0, len(x), 256):
            ids = order[begin:begin + 256]
            batch = x[ids]
            labels = train_y[ids]
            w1, b1, w2, b2 = params
            hidden = np.maximum(batch @ w1.T + b1, np.float32(0))
            logits = hidden @ w2.T + b2
            exps = np.exp(logits - logits.max(axis=1, keepdims=True))
            prob = exps / exps.sum(axis=1, keepdims=True)
            losses.append(float(-np.log(prob[np.arange(len(ids)), labels]).mean()))
            prob[np.arange(len(ids)), labels] -= 1
            prob /= np.float32(len(ids))
            dh = (prob @ w2) * (hidden > 0)
            grads = [dh.T @ batch, dh.sum(axis=0), prob.T @ hidden, prob.sum(axis=0)]
            step += 1
            for i, grad in enumerate(grads):
                first[i] = np.float32(.9) * first[i] + np.float32(.1) * grad
                second[i] = np.float32(.999) * second[i] + np.float32(.001) * grad * grad
                m = first[i] / np.float32(1 - .9 ** step)
                v = second[i] / np.float32(1 - .999 ** step)
                params[i] -= np.float32(.001) * m / (np.sqrt(v) + np.float32(1e-8))
        row = {'epoch': epoch + 1, 'train_loss': float(np.mean(losses))}
        history.append(row)
        print(json.dumps(row), flush=True)
    w1, b1, w2, b2 = params
    h = np.maximum(test @ w1.T + b1, np.float32(0))
    z = h @ w2.T + b2
    accuracy = float(np.mean(z.argmax(axis=1) == test_y))
    assert accuracy >= .95, f'Full test accuracy below acceptance: {accuracy}'
    model = export / 'model.bin'
    with model.open('wb') as file:
        file.write(struct.pack('<8sIIII', b'VXMNIST1', 1, 784, 64, 10))
        for p in params:
            file.write(p.astype('<f4').tobytes())
    # Independent ordered FP32 reductions matching the declared kernel arithmetic.
    subset = test[:100]
    ordered_h = np.broadcast_to(b1, (100, 64)).copy()
    for k in range(784):
        ordered_h += subset[:, k:k + 1] * w1[None, :, k]
    ordered_h = np.maximum(ordered_h, np.float32(0))
    ordered_z = np.broadcast_to(b2, (100, 10)).copy()
    for k in range(64):
        ordered_z += ordered_h[:, k:k + 1] * w2[None, :, k]
    cases = []
    with (export / 'references.bin').open('wb') as file:
        file.write(struct.pack('<8sI', b'VXCASE01', 100))
        for i in range(100):
            label = int(test_y[i])
            name = f'mnist-{i:05d}-label{label}.pgm'
            image = export / 'images' / name
            image.write_bytes(b'P5\n28 28\n255\n' + test_u8[i].tobytes())
            file.write(struct.pack('<II', i, label))
            file.write(ordered_h[i].astype('<f4').tobytes())
            file.write(ordered_z[i].astype('<f4').tobytes())
            cases.append({'index': i, 'label': label, 'prediction': int(ordered_z[i].argmax()),
                          'image': name})
    sample_accuracy = float(np.mean(ordered_z.argmax(axis=1) == test_y[:100]))
    assert sample_accuracy >= .9
    report = {
        'model': 'trained MNIST FP32 MLP', 'shape': [784, 64, 10],
        'seed': 20261011, 'optimizer': 'Adam', 'learning_rate': .001,
        'epochs': args.epochs, 'batch_size': 256, 'train_count': len(x),
        'test_count': len(test), 'full_test_accuracy': accuracy,
        'guest_sample_rule': 'first 100 test images, no filtering',
        'sample_accuracy': sample_accuracy, 'history': history,
        'python': platform.python_version(), 'numpy': np.__version__,
        'numeric_contract': 'FP32 bias-first ascending-k dot products, separate multiply/add, ReLU',
        'tolerance': {'atol': 1e-4, 'rtol': 1e-5},
        'datasets': [{'url': BASE + n, 'md5': m,
                      'sha256': hashlib.sha256((cache / n).read_bytes()).hexdigest()}
                     for n, m in FILES.items()], 'cases': cases,
    }
    (export / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    paths = sorted(p for p in export.rglob('*') if p.is_file())
    (export / 'sha256.txt').write_text(''.join(
        f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(export)}\n' for p in paths))
    print(f'MNIST_TRAIN_EXPORT_PASS full_test={accuracy:.4%} first100={sample_accuracy:.2%}', flush=True)


if __name__ == '__main__':
    main()
