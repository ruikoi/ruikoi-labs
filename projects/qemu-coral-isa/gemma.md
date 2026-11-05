# Gemma model assets

English | [简体中文](gemma.zh-CN.md)

The two paths use different checkpoints and computation types. Assets and acceptance results are specific to each path.

| Path | Model and computation | Execution |
|---|---|---|
| Coral compiler | Official Gemma3-270M JAX BF16 checkpoint | Host IREE/PJRT and simulator |
| QEMU guest / QBox RTL | HF IT model, complete embeddings, INT8 projections, FP32 other operators | Guest tokenization/scheduling and NPU firmware |

Download the official JAX checkpoint with `download_gemma_jax.py`; the public object index pins generations and checksums. Download the official tokenizer from `https://storage.googleapis.com/gemma-data/tokenizers/tokenizer_gemma3.model` to the work disk and set `CORAL_GEMMA_TOKENIZER`.

Guest/RTL assets use the tools in the pinned coralnpu source under `tests/cocotb/rvv/ml_ops/gemma_inference/`. Prepare a local HF model directory according to its access requirements; keep credentials out of the repository.

```sh
export CORAL_SOURCE="$LAB_WORK/sources/qemu-coral-isa/coralnpu"
export HF_HOME="$LAB_WORK/cache/huggingface"
export GEMMA_ARTIFACTS="$LAB_WORK/models/gemma-platform"
MODEL_PYTHON=/path/to/model-python
DATA="$LAB_WORK/models/gemma-data"
"$MODEL_PYTHON" "$CORAL_SOURCE/tests/cocotb/rvv/ml_ops/gemma_inference/dump_gemma3_model.py" \
  --model_id /path/to/local-hf-model --out_dir "$DATA" --full_lm_head
"$MODEL_PYTHON" common/accelerators/prepare_gemma.py \
  --data "$DATA" --hf-model /path/to/local-hf-model \
  --out "$GEMMA_ARTIFACTS" --max-seq 512
```

The export environment needs NumPy, PyTorch, Transformers, and the exporter dependencies. For a CT2 INT8 model, import it with upstream `import_ct2_int8.py` from the same directory. Do not substitute generated weights or a candidate-only vocabulary.

`GEMMA_ARTIFACTS` must contain `gemma-ddr.bin`, `gemma-platform.bin`, `gemma-embeddings.bf16`, `gemma-layer0-reference.bin`, and `manifest.json`. Guest chat also needs a matching SentencePiece `tokenizer.model`. The packer retains the complete 262144-token vocabulary, parameters for all 18 layers, and KV cache, using all 64 output-vocabulary chunks by default. Model files stay outside Git.

Guest builds also need SentencePiece v0.2.1 source from `https://github.com/google/sentencepiece`, selected with `SENTENCEPIECE_SOURCE`. The build cross-compiles an RV64 static library. `build --suite gemma-guest` builds the NPU runner from the pinned coralnpu source.

The QBox high-memory profile uses the same weight layout with independently built guest runner, IRQ driver, and RTL model. `--profile gemma` defaults to layer 0 numerical validation. Full chat needs a longer test budget and complete token auditing; partial progress does not count as a pass.
