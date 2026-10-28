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

Prepare `models/` once, on any machine with internet (about 1.6 GB), then copy it with
the package; exam PCs never need internet:

```bash
uv run python -m tools.fetch_models
```

It downloads pinned versions of the llama.cpp server and the Qwen2.5 models, checks each
against its published SHA-256, and writes `models/manifest.json`. At start-up the service
verifies the files it uses against the manifest, starts the model on 127.0.0.1, warms it,
and restarts it if it dies. If anything is missing or does not match, it logs why and
runs the exam on rules only.

## Benchmark

Run on the target machine and send back the printed table and `bench-report.json`:

```bash
uv run python -m tools.bench
uv run python -m tools.bench --llm-model models/qwen2.5-1.5b-instruct-q4_k_m.gguf
```

It starts the service as its own process and reports per-turn latency (rules path, and
the model path when a model is given), RAM per live session, and sessions per core.

## Layout

See the design doc's "Code base structure". Phase 1 covers `contract.py`, `config.py`,
`session/`, `understanding/`, `adapters/` (fake), `api/` and `spec/`.
