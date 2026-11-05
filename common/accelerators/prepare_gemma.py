#!/usr/bin/env python3
"""Pack real weights for TLM DDR and a complete CPU embedding table.

No HF-generated candidate vocabulary or generated embeddings are used by
the Linux decoder. All 262144 output columns are made available to the NPU.
"""
import os
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import numpy as np

ROOT = Path(os.environ["CORAL_SOURCE"]).resolve()
sys.path.insert(0, str(ROOT))
from tests.cocotb.rvv.ml_ops.gemma_inference import gemma3_ref as ref

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--hf-model', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--max-seq', type=int, default=16)
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    full = np.load(args.data/'gemma3_lm_head_i8.npy', mmap_mode='r')
    scales = np.load(args.data/'gemma3_lm_head_s.npy')
    m = ref.QuantizedGemma3.from_npz(np.load(args.data/'gemma3_weights.npz'))
    m = m.select_rows(np.arange(ref.MAX_VOCAB_ROWS), full, scales)
    image, desc = ref.build_image(m, 0x20000000, args.max_seq)
    heads = []
    for start in range(0, ref.VOCAB, ref.MAX_VOCAB_ROWS):
        heads.append((image.add(f'head{start}', full[:, start:start+ref.MAX_VOCAB_ROWS]),
                      image.add(f'scale{start}', scales[start:start+ref.MAX_VOCAB_ROWS])))
    blob = image.to_bytes()
    assert blob.size < 0x20000000
    (args.out/'gemma-ddr.bin').write_bytes(blob.tobytes())
    prompt = np.load(args.data/'gemma3_prompt.npz')
    oracle, _ = ref.Gemma3Reference(m, args.max_seq).layers(
        prompt['embeds'][0], 0, 1, pos=0)
    (args.out/'gemma-layer0-reference.bin').write_bytes(oracle.astype('<f4').tobytes())
    # Little endian fixed-width ABI consumed by guest_gemma.c.
    header = (b'G3QBOX01' + struct.pack('<IIII', ref.MODEL_WORDS, len(heads),
                len(prompt['input_ids']), args.max_seq) + desc.astype('<u4').tobytes()
              + np.asarray(heads, dtype='<u4').tobytes()
              + prompt['input_ids'].astype('<u4').tobytes())
    (args.out/'gemma-platform.bin').write_bytes(header)
    import torch
    from transformers import AutoModelForCausalLM
    hf = AutoModelForCausalLM.from_pretrained(str(args.hf_model), torch_dtype=torch.bfloat16)
    hf.eval()
    # Calling the actual HF embedding module preserves its BF16 scaling and
    # rounding. Every token ID can then be looked up after an NPU argmax.
    with torch.no_grad(), (args.out/'gemma-embeddings.bf16').open('wb') as f:
        for start in range(0, ref.VOCAB, 1024):
            e = hf.model.embed_tokens(torch.arange(start, start+1024))
            assert e.dtype == torch.bfloat16
            f.write(e.view(torch.uint16).cpu().numpy().astype('<u2').tobytes())
    manifest = {'architecture': 'Gemma3-270M', 'dtype': 'int8 projections, fp32 other operators',
                'ddr_base': hex(image.base), 'ddr_bytes': int(blob.size),
                'vocabulary': ref.VOCAB, 'head_chunks': len(heads),
                'prompt_ids': prompt['input_ids'].tolist(), 'max_seq': args.max_seq,
                'embedding': 'complete BF16 scaled embedding table; CPU lookup',
                'files': {}}
    for name in ['gemma-ddr.bin','gemma-platform.bin','gemma-embeddings.bf16',
                 'gemma-layer0-reference.bin']:
        file = args.out/name
        h = hashlib.sha256()
        with file.open('rb') as f:
            for block in iter(lambda: f.read(1<<20), b''): h.update(block)
        manifest['files'][name] = {'bytes': file.stat().st_size, 'sha256': h.hexdigest()}
    (args.out/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print('GEMMA_PLATFORM_PACK_PASS', json.dumps(manifest))

if __name__ == '__main__': main()
