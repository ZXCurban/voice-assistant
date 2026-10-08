"""NLU engines: the `NluEngine` protocol and the real torch implementation.

`TorchNluEngine` mirrors `ml-training/src/evaluate.py` (the reference
inference): temperature-scaled softmax for the intent, beam-search
generation for the slots, `clean_slots(parse_slots_linear(..))` after it.
torch/transformers are optional dependencies (extra `nlu`) and imported
lazily, so the rest of the backend works without them.
"""

import json
import logging
import threading
from pathlib import Path
from typing import Any, Protocol

from app.nlu.contract import INTENTS, build_model_input, clean_slots, parse_slots_linear
from app.nlu.schemas import NluParse

logger = logging.getLogger(__name__)

MAX_INPUT_TOKENS = 128
SLOTS_MAX_LENGTH = 64
SLOTS_NUM_BEAMS = 4


class NluUnavailableError(RuntimeError):
    """The NLU models cannot be loaded or run (missing weights, missing deps)."""


class NluEngine(Protocol):
    """Anything that turns (text, previous assistant reply) into an NluParse."""

    @property
    def version(self) -> str: ...

    def parse(self, text: str, last_response: str = "") -> NluParse: ...


class TorchNluEngine:
    """Two-stage NLU: ruBERT intent classifier + rut5 slot extractor.

    `model_dir` is the `artifacts/` directory of ml-training:
        intent/pytorch, intent/calibration.json, slots/pytorch.
    """

    def __init__(self, model_dir: Path, *, min_confidence: float | None = None) -> None:
        intent_dir = model_dir / "intent" / "pytorch"
        slots_dir = model_dir / "slots" / "pytorch"
        for required in (intent_dir / "model.safetensors", slots_dir / "model.safetensors"):
            if not required.is_file():
                raise NluUnavailableError(f"NLU weights not found: {required}")
        try:
            import torch
            from transformers import (
                AutoModelForSeq2SeqLM,
                AutoModelForSequenceClassification,
                AutoTokenizer,
            )
        except ImportError as exc:
            raise NluUnavailableError(
                'torch/transformers are not installed: pip install ".[nlu]"'
            ) from exc

        labels = json.loads((intent_dir / "labels.json").read_text(encoding="utf-8"))["intents"]
        if list(labels) != list(INTENTS):
            raise NluUnavailableError("intent labels of the model differ from the NLU contract")
        calibration_path = model_dir / "intent" / "calibration.json"
        calibration: dict[str, Any] = {}
        if calibration_path.is_file():
            calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
        self._temperature = max(float(calibration.get("temperature", 1.0)), 1e-6)
        self._threshold = (
            min_confidence if min_confidence is not None else float(calibration.get("threshold", 0))
        )
        self._labels: list[str] = list(labels)
        self._torch = torch
        self._intent_tokenizer = AutoTokenizer.from_pretrained(str(intent_dir))
        self._intent_model = AutoModelForSequenceClassification.from_pretrained(str(intent_dir))
        self._slots_tokenizer = AutoTokenizer.from_pretrained(str(slots_dir))
        self._slots_model = AutoModelForSeq2SeqLM.from_pretrained(str(slots_dir))
        self._intent_model.eval()
        self._slots_model.eval()
        # torch modules are not guaranteed to be thread-safe for concurrent calls.
        self._lock = threading.Lock()
        logger.info(
            "NLU models loaded (temperature=%.3f threshold=%.2f)",
            self._temperature,
            self._threshold,
        )

    @property
    def version(self) -> str:
        return "rubert-intent+rut5-slots"

    def parse(self, text: str, last_response: str = "") -> NluParse:
        model_input = build_model_input(text, last_response)
        torch = self._torch
        with self._lock, torch.no_grad():
            intent_batch = self._intent_tokenizer(
                [model_input],
                truncation=True,
                padding=True,
                max_length=MAX_INPUT_TOKENS,
                return_tensors="pt",
            )
            logits = self._intent_model(**intent_batch).logits[0] / self._temperature
            probs = torch.softmax(logits, dim=-1)
            best = int(torch.argmax(probs))
            confidence = float(probs[best])

            slots_batch = self._slots_tokenizer(
                [model_input],
                truncation=True,
                padding=True,
                max_length=MAX_INPUT_TOKENS,
                return_tensors="pt",
            )
            generated = self._slots_model.generate(
                **slots_batch, max_length=SLOTS_MAX_LENGTH, num_beams=SLOTS_NUM_BEAMS
            )
            decoded = self._slots_tokenizer.batch_decode(generated, skip_special_tokens=True)[0]
        slots = clean_slots(parse_slots_linear(decoded))
        return NluParse(
            intent=self._labels[best],
            confidence=min(max(confidence, 0.0), 1.0),
            confident=confidence >= self._threshold,
            slots=slots,
        )
