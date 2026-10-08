"""Model weights shipped as split files (GitHub rejects files over 100 MB).

The repository keeps each `model.safetensors` as `model.safetensors.part00`,
`.part01`, … plus a `SHA256SUMS` file at the root of the model directory.
`ensure_weights` joins the parts on first start (skipped when the joined
file already exists) and verifies the checksum; it is also a CLI:

    python -m app.nlu.weights models/nlu            # assemble + verify
    python -m app.nlu.weights models/nlu --split    # (maintainers) cut into parts
"""

import argparse
import hashlib
import sys
from pathlib import Path

WEIGHTS_NAME = "model.safetensors"
# Relative to the model directory (ml-training `artifacts/` layout).
WEIGHTS_PATHS = (Path("intent/pytorch") / WEIGHTS_NAME, Path("slots/pytorch") / WEIGHTS_NAME)
CHECKSUMS_NAME = "SHA256SUMS"
PART_SIZE = 90 * 1024 * 1024  # stays under GitHub's 100 MiB per-file limit
_CHUNK = 8 * 1024 * 1024


class WeightsError(RuntimeError):
    """The weights cannot be assembled or fail verification."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(_CHUNK):
            digest.update(block)
    return digest.hexdigest()


def read_checksums(model_dir: Path) -> dict[str, str]:
    """`SHA256SUMS` as {relative posix path: sha256}; empty if the file is absent."""
    path = model_dir / CHECKSUMS_NAME
    if not path.is_file():
        return {}
    sums: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, _, name = line.strip().partition("  ")
        if digest and name:
            sums[name.strip()] = digest
    return sums


def part_files(target: Path) -> list[Path]:
    return sorted(target.parent.glob(f"{target.name}.part[0-9][0-9]*"))


def assemble(target: Path, expected_sha256: str | None = None) -> None:
    """Join `target.part*` into `target`, verifying the checksum if known."""
    parts = part_files(target)
    if not parts:
        raise WeightsError(f"{target} is missing and there are no {target.name}.part* files")
    temp = target.with_name(target.name + ".tmp")
    digest = hashlib.sha256()
    try:
        with temp.open("wb") as out:
            for part in parts:
                with part.open("rb") as handle:
                    while block := handle.read(_CHUNK):
                        digest.update(block)
                        out.write(block)
        if expected_sha256 is not None and digest.hexdigest() != expected_sha256:
            raise WeightsError(
                f"{target.name}: checksum mismatch after joining {len(parts)} parts "
                "(incomplete git checkout?)"
            )
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


def ensure_weights(model_dir: Path) -> list[Path]:
    """Make sure every `model.safetensors` exists; returns the files assembled now."""
    sums = read_checksums(model_dir)
    built: list[Path] = []
    for relative in WEIGHTS_PATHS:
        target = model_dir / relative
        if target.is_file():
            continue
        if not part_files(target):
            continue  # let the caller report the missing weights
        assemble(target, sums.get(relative.as_posix()))
        built.append(target)
    return built


def split(source: Path, part_size: int = PART_SIZE) -> list[Path]:
    """Cut `source` into `source.partNN` files (maintainers; `source` is kept)."""
    parts: list[Path] = []
    with source.open("rb") as handle:
        index = 0
        while block := handle.read(part_size):
            part = source.with_name(f"{source.name}.part{index:02d}")
            part.write_bytes(block)
            parts.append(part)
            index += 1
    return parts


def write_checksums(model_dir: Path) -> Path:
    """Write `SHA256SUMS` for the joined weights found under `model_dir`."""
    lines = [
        f"{sha256_file(model_dir / relative)}  {relative.as_posix()}"
        for relative in WEIGHTS_PATHS
        if (model_dir / relative).is_file()
    ]
    path = model_dir / CHECKSUMS_NAME
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assemble (or split) the NLU weights.")
    parser.add_argument("model_dir", type=Path)
    parser.add_argument(
        "--split", action="store_true", help="cut joined weights into parts + write SHA256SUMS"
    )
    args = parser.parse_args(argv)
    model_dir: Path = args.model_dir
    try:
        if args.split:
            for relative in WEIGHTS_PATHS:
                source = model_dir / relative
                if source.is_file():
                    print(f"{relative}: {len(split(source))} parts")
            print(f"wrote {write_checksums(model_dir)}")
            return 0
        built = ensure_weights(model_dir)
    except (WeightsError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    missing = [str(r) for r in WEIGHTS_PATHS if not (model_dir / r).is_file()]
    if missing:
        print(f"error: weights missing: {', '.join(missing)}", file=sys.stderr)
        return 1
    print(f"ok: assembled {len(built)} file(s), all weights present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
