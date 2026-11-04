"""Run the fuzz target on random and mutated inputs in every CI run.

tests/fuzz/fuzz_camera_data.py is the same target for coverage guided
fuzzing with Atheris; see its docstring.
"""

from __future__ import annotations

import json
from pathlib import Path
import random

from .fuzz.fuzz_camera_data import fuzz_one

FIXTURE = Path(__file__).parent / "fixtures" / "pyxis_6k.json"
ITERATIONS = 5000

SEEDS = [
    b"",
    b'{"iso": 800}',
    b'{"a": NaN, "b": Infinity, "c": -Infinity}',
    b"[" * 3000 + b"]" * 3000,
    b'{"x": "' + b"y" * 5000 + b'"}',
    b"192.168.1.50",
    b"https://cam.local:443/control",
    b"[fe80::1]:443",
    b"fe80::1%eth0",
    "cam.local:²".encode(),
    b"LCD,MainSDI,../../transports/0/record,f1,f/2",
    b"\xff\xfe\x00\x00not utf-8",
    b"9" * 40,
]


def _seeds() -> list[bytes]:
    answers = json.loads(FIXTURE.read_text())
    return SEEDS + [json.dumps(answer.get("json")).encode() for answer in answers.values()]


def _mutate(rng: random.Random, data: bytes, seeds: list[bytes]) -> bytes:
    """Change data the way a simple mutation fuzzer would."""
    value = bytearray(data)
    for _ in range(rng.randint(1, 4)):
        operation = rng.randrange(5)
        position = rng.randint(0, len(value))
        if operation == 0 and value:
            value[min(position, len(value) - 1)] = rng.randrange(256)
        elif operation == 1:
            value[position:position] = rng.randbytes(rng.randint(1, 8))
        elif operation == 2 and value:
            del value[position : position + rng.randint(1, 16)]
        elif operation == 3 and value:
            chunk = value[position : position + rng.randint(1, 32)]
            value[position:position] = chunk * rng.randint(1, 50)
        else:
            other = rng.choice(seeds)
            value[position:position] = other[: rng.randint(0, len(other))]
    return bytes(value)


def test_fuzz_target_on_seeds_and_mutations() -> None:
    """The parsers never crash and always return bounded results."""
    seeds = _seeds()
    for seed in seeds:
        fuzz_one(seed)
    rng = random.Random(20261104)  # Fixed seed: a failure can be reproduced.
    for _ in range(ITERATIONS):
        fuzz_one(_mutate(rng, rng.choice(seeds), seeds))


def test_fuzz_target_on_random_bytes() -> None:
    """Pure noise is handled too."""
    rng = random.Random(4)
    for _ in range(ITERATIONS):
        fuzz_one(rng.randbytes(rng.randint(0, 512)))
