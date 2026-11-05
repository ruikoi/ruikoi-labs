# Gemma 模型资产

[English](gemma.md) | 简体中文

这里区分两组模型，不能混用检查点或把一条路线的结果当作另一条路线的结果。

| 路线 | 模型与计算类型 | 执行位置 |
|---|---|---|
| Coral compiler | 官方 Gemma3-270M JAX BF16 检查点 | 宿主 IREE/PJRT + 模拟器 |
| QEMU guest / QBox RTL | HF IT 模型的完整 embedding 与 INT8 投影导出，其他算子 FP32 | guest 分词与调度，NPU 固件执行 |

官方 JAX 检查点通过 `download_gemma_jax.py` 下载，公开对象索引固定 generation 和校验和。官方分词器地址为 `https://storage.googleapis.com/gemma-data/tokenizers/tokenizer_gemma3.model`，下载到工作磁盘并设置 `CORAL_GEMMA_TOKENIZER`。

guest/RTL 模型采用 coralnpu 固定基线内的 `tests/cocotb/rvv/ml_ops/gemma_inference/` 工具。先按模型的访问要求准备 HF 本地模型目录，不把访问凭据写入仓库。

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

模型环境需要 NumPy、PyTorch、Transformers，以及导出工具的 Python 依赖。若使用 CT2 INT8 模型，先用同一上游目录的 `import_ct2_int8.py` 导入，不能替换为人工生成的权重或候选词表。

`GEMMA_ARTIFACTS` 应包含 `gemma-ddr.bin`、`gemma-platform.bin`、`gemma-embeddings.bf16`、`gemma-layer0-reference.bin`、`manifest.json`。guest 对话还需匹配模型的 SentencePiece `tokenizer.model`。打包工具保留完整 262144 词表、18 层参数与 KV cache；默认使用全部 64 个输出词表分片。模型文件不纳入 Git。

QEMU guest 构建另需 SentencePiece v0.2.1 源码，可从 `https://github.com/google/sentencepiece` 获取该 release，设置 `SENTENCEPIECE_SOURCE`；工程脚本将其交叉编译为 RV64 静态库。执行 `build --suite gemma-guest` 时，NPU runner 从当前固定 coralnpu 源码构建。

QBox 的高内存配置使用相同权重布局，但 guest runner、IRQ 驱动和 RTL 模型均独立构建。`--profile gemma` 默认只执行 layer 0 数值验证；完整对话另需更长的测试预算和完整 token 审计，不能把部分进度当作通过。
