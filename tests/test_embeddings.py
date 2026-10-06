"""
Tests for the embeddings module.
"""

import io
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest

from rerx.embeddings import (
    ZIP_NAME,
    EmbeddingsResult,
    convert_embeddings,
    download_embeddings,
    sha256_file,
)


def _make_embeddings_zip(
    path: Path, experiments: list[str], rows_per_experiment: int = 3
) -> None:
    """Build a synthetic embeddings ZIP shaped like the real RxRx19a one."""
    with zipfile.ZipFile(path, "w") as zf:
        for exp in experiments:
            df = pd.DataFrame(
                {
                    "experiment": [exp] * rows_per_experiment,
                    "plate": [str(i % 2 + 1) for i in range(rows_per_experiment)],
                    "well": [f"A{i + 1:02d}" for i in range(rows_per_experiment)],
                    "site": list(range(1, rows_per_experiment + 1)),
                    "embedding_0": [0.1 * i for i in range(rows_per_experiment)],
                    "embedding_1": [0.2 * i for i in range(rows_per_experiment)],
                }
            )
            buf = io.StringIO()
            df.to_csv(buf, index=False)
            zf.writestr(f"rxrx19a_embeddings_{exp}.csv", buf.getvalue())


def test_sha256_file_is_deterministic(tmp_path: Path) -> None:
    f = tmp_path / "data.bin"
    f.write_bytes(b"hello world")
    assert sha256_file(f) == sha256_file(f)
    assert len(sha256_file(f)) == 64


def test_sha256_file_differs_for_different_content(tmp_path: Path) -> None:
    f1 = tmp_path / "a.bin"
    f2 = tmp_path / "b.bin"
    f1.write_bytes(b"aaa")
    f2.write_bytes(b"bbb")
    assert sha256_file(f1) != sha256_file(f2)


def test_download_embeddings_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    class FakeResponse:
        def __init__(self) -> None:
            self._chunks = [b"fake ", b"zip bytes"]

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int) -> Iterator[bytes]:
            yield from self._chunks

    def fake_get(url, timeout, stream=False):
        calls.append(url)
        return FakeResponse()

    monkeypatch.setattr("rerx.embeddings.requests.get", fake_get)

    zip_path, digest = download_embeddings(tmp_path, url="https://example.com/e.zip")
    assert zip_path == tmp_path / ZIP_NAME
    assert zip_path.exists()
    assert len(digest) == 64
    assert len(calls) == 1

    # Second call must not re-download (idempotent).
    zip_path2, digest2 = download_embeddings(tmp_path, url="https://example.com/e.zip")
    assert zip_path2 == zip_path
    assert digest2 == digest
    assert len(calls) == 1


def test_convert_embeddings_concatenates_all_experiment_csvs(tmp_path: Path) -> None:
    zip_path = tmp_path / "embeddings.zip"
    _make_embeddings_zip(
        zip_path, experiments=["HRCE-1", "HRCE-2", "VERO-1"], rows_per_experiment=4
    )

    dest_dir = tmp_path / "baseline" / "recursion_site_embeddings"
    result = convert_embeddings(zip_path, dest_dir)

    assert isinstance(result, EmbeddingsResult)
    assert result.site_count == 12  # 3 experiments * 4 rows
    assert result.parquet_path is not None
    assert result.parquet_path.exists()

    df = pd.read_parquet(result.parquet_path)
    assert len(df) == 12
    assert set(df["experiment"]) == {"HRCE-1", "HRCE-2", "VERO-1"}
    # plate/well forced to string dtype, matching real RxRx19a metadata keys.
    assert df["plate"].dtype == object
    assert df["well"].dtype == object


def test_convert_embeddings_writes_source_sidecar_with_hash(tmp_path: Path) -> None:
    zip_path = tmp_path / "embeddings.zip"
    _make_embeddings_zip(zip_path, experiments=["HRCE-1"])

    dest_dir = tmp_path / "baseline" / "recursion_site_embeddings"
    result = convert_embeddings(zip_path, dest_dir, zip_sha256="deadbeef")

    sidecar = dest_dir / "source.json"
    assert sidecar.exists()
    import json

    data = json.loads(sidecar.read_text())
    assert data["zip_sha256"] == "deadbeef"
    assert data["zip_name"] == ZIP_NAME
    assert data["site_count"] == result.site_count


def test_convert_embeddings_computes_hash_when_not_given(tmp_path: Path) -> None:
    zip_path = tmp_path / "embeddings.zip"
    _make_embeddings_zip(zip_path, experiments=["HRCE-1"])

    result = convert_embeddings(zip_path, tmp_path / "out")
    assert result.zip_sha256 == sha256_file(zip_path)


def test_convert_embeddings_raises_when_no_csvs_present(tmp_path: Path) -> None:
    zip_path = tmp_path / "empty.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("readme.txt", "no csvs here")

    with pytest.raises(RuntimeError, match="no CSVs found"):
        convert_embeddings(zip_path, tmp_path / "out")


def test_convert_embeddings_ignores_non_csv_members(tmp_path: Path) -> None:
    zip_path = tmp_path / "embeddings.zip"
    _make_embeddings_zip(zip_path, experiments=["HRCE-1"], rows_per_experiment=2)
    with zipfile.ZipFile(zip_path, "a") as zf:
        zf.writestr("README.md", "not a csv")

    result = convert_embeddings(zip_path, tmp_path / "out")
    assert result.site_count == 2
