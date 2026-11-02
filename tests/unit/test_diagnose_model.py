"""The model diagnosis's engine-build variants (running it needs the real llama.cpp)."""

from pathlib import Path

from tools.diagnose_model import AVX512_BUILDS, builds, copy_with_builds

ROOT = Path(__file__).parents[2]


def test_a_variant_keeps_only_the_chosen_engine_builds(tmp_path: Path) -> None:
    source = tmp_path / "llama.cpp"
    source.mkdir()
    for name in ("llama-server.exe", "ggml.dll", "ggml-base.dll", "ggml-cpu-x64.dll",
                 "ggml-cpu-haswell.dll", "ggml-cpu-icelake.dll"):  # fmt: skip
        (source / name).write_bytes(b"x")
    variant = copy_with_builds(source, tmp_path / "older", {"x64", "haswell"})
    assert builds(variant) == ["haswell", "x64"]
    # Everything that is not a CPU engine build comes along.
    assert (variant / "llama-server.exe").is_file() and (variant / "ggml-base.dll").is_file()


def test_the_avx512_list_matches_the_shipped_builds() -> None:
    shipped = ROOT / "models" / "llama.cpp"
    if shipped.is_dir():
        assert set(builds(shipped)) >= AVX512_BUILDS
        assert {"x64", "haswell", "alderlake"} <= set(builds(shipped)) - AVX512_BUILDS
