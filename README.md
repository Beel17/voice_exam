# Voice Exam Agent

Offline, CPU-only voice service that lets blind candidates take computer-based exams by
conversation. The design doc ("Voice Exam Agent — Service Design") is the source of truth.

## Development

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

## Running it (phase 1: typed text, sample exams)

1. Issue a device token. Keep the token for the client; put the digest in the config.

   ```bash
   uv run python tools/issue_token.py
   ```

2. Copy `config/centre.example.toml` to `config/centre.toml` (not tracked) and set
   `device_token_sha256 = ["<digest>"]`. Without model files, set `[llm] enabled = false`
   or leave it on: the service logs that the files are missing and runs on rules only.

3. Start the service, then take an exam by typing in another terminal:

   ```bash
   uv run python -m voice_agent
   uv run python tools/text_client.py --token <token>
   ```

   The sample exam is `UTME-SAMPLE`; candidates `10000001AA` to `10000003AA`.

Every setting can be overridden by environment variables prefixed `VOICE_AGENT_`, and
`VOICE_AGENT_CONFIG` points at another config file.

## Language model

Put the llama.cpp `llama-server` binary (CPU build) and a GGUF model in `models/`, or set
`[llm] server_binary` and `model_file`. The service starts it on 127.0.0.1, warms it, and
restarts it if it dies; without it, the exam runs on rules only.

## Benchmark

Run on the target machine and send back the printed table and `bench-report.json`:

```bash
uv run python -m tools.bench
uv run python -m tools.bench --llm-binary models/llama-server --llm-model models/qwen2.5-1.5b-instruct-q4_k_m.gguf
```

It starts the service as its own process and reports per-turn latency (rules path, and
the model path when a model is given), RAM per live session, and sessions per core.

## Layout

See the design doc's "Code base structure". Phase 1 covers `contract.py`, `config.py`,
`session/`, `understanding/`, `adapters/` (fake), `api/` and `spec/`.
