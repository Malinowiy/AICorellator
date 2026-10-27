# tools/ — developer tools

Not part of the `aicorellator` package. Tools in this directory are
for developers choosing models, measuring prompt changes, and
verifying the classifier before it lands in `parsing/llm_classifier/`
(D.2).

## `bench_classifier.py`

Benchmark an LLM classifier for AST symbol classification. Used to:

- Choose the model that goes into `config.yaml:llm_classifier_model`.
- Measure the effect of a prompt change **before** it lands in D.2.
- Check whether a fine-tuned model is better than the stock model
  (BACKLOG D.3).

### What it does

1. Parses a corpus with `ast` to get symbols (classes, functions,
   methods — all three; a method can be a graph node on its own).
2. Probes the first symbol to find a `response_format` form the
   server accepts: `json_schema` (LMStudio/llama.cpp/vLLM), then
   `json_object` (OpenAI legacy), then none.
3. For each symbol, calls the LLM with the classifier prompt (the
   one that will live in `parsing/llm_classifier/prompt.py`).
4. Measures latency, JSON validity, and kind/caps agreement with a
   reference labeling (if one is given).
5. Records HTTP status and the first raw error body, so a 0% result
   is diagnosable without a second run.

### Usage

```bash
# Without a reference: latency and JSON validity only.
python tools/bench_classifier.py \
    --corpus /path/to/finbot-ctf/finbot \
    --models "qwen/qwen3-4b-2507,abdulmannan-01_-_llama-3.2-3b-finetuned-for-json-generation-new,Nemotron 3 Nano 4B" \
    --output /tmp/bench.json

# With a reference: adds kind% and caps%.
python tools/bench_classifier.py \
    --corpus /path/to/finbot-ctf/finbot \
    --models "qwen/qwen3-4b-2507,abdulmannan-01_-_llama-3.2-3b-finetuned-for-json-generation-new,Nemotron 3 Nano 4B" \
    --reference tools/reference/finbot-60.json \
    --output /tmp/bench.json