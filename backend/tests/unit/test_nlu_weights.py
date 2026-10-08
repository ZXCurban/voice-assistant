"""Split weights: assemble, verify, and refuse a damaged checkout."""

from pathlib import Path

import pytest

from app.nlu import weights
from app.nlu.weights import WeightsError

BLOB = bytes(range(256)) * 40  # 10 240 bytes


def _model_dir(tmp_path: Path) -> Path:
    for relative in weights.WEIGHTS_PATHS:
        target = tmp_path / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(BLOB + relative.parts[0].encode())
    return tmp_path


def _split_and_remove(model_dir: Path) -> None:
    for relative in weights.WEIGHTS_PATHS:
        assert len(weights.split(model_dir / relative, part_size=4096)) >= 3
    weights.write_checksums(model_dir)
    for relative in weights.WEIGHTS_PATHS:
        (model_dir / relative).unlink()


def test_parts_are_joined_back_to_the_original(tmp_path: Path) -> None:
    model_dir = _model_dir(tmp_path)
    originals = {r: (model_dir / r).read_bytes() for r in weights.WEIGHTS_PATHS}
    _split_and_remove(model_dir)
    built = weights.ensure_weights(model_dir)
    assert len(built) == 2
    for relative, content in originals.items():
        assert (model_dir / relative).read_bytes() == content
    assert weights.ensure_weights(model_dir) == []  # nothing to do the second time


def test_damaged_part_fails_the_checksum_and_leaves_no_file(tmp_path: Path) -> None:
    model_dir = _model_dir(tmp_path)
    _split_and_remove(model_dir)
    target = model_dir / weights.WEIGHTS_PATHS[0]
    first = weights.part_files(target)[0]
    first.write_bytes(b"x" + first.read_bytes()[1:])
    with pytest.raises(WeightsError, match="checksum"):
        weights.ensure_weights(model_dir)
    assert not target.exists()
    assert not target.with_name(target.name + ".tmp").exists()


def test_missing_parts_are_left_to_the_caller(tmp_path: Path) -> None:
    assert weights.ensure_weights(tmp_path) == []
    assert weights.main([str(tmp_path)]) == 1


def test_cli_assembles_and_reports(tmp_path: Path) -> None:
    model_dir = _model_dir(tmp_path)
    _split_and_remove(model_dir)
    assert weights.main([str(model_dir)]) == 0
    assert all((model_dir / r).is_file() for r in weights.WEIGHTS_PATHS)
