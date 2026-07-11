"""Persona + expected-JSON determinism (design spec §6 Stage 1) — pure Python, no rasteriser.

The core Stage 1 gate lives here: **same seed -> identical expected JSON**, and the committed
golden truth matches what the generator produces for the formpack's declared seeds.
"""

from __future__ import annotations

from papereyes.config.loader import load_formpack
from papereyes.synth.generator import expected_json_bytes
from papereyes.synth.personas import expected_json, generate_persona, persona_seeds
from tests.support import REPO_ROOT

UK_CH2 = REPO_ROOT / "formpacks" / "uk-ch2"


def test_persona_is_pure_function_of_seed() -> None:
    a = generate_persona(7000)
    b = generate_persona(7000)
    assert a == b
    assert generate_persona(7001) != a


def test_expected_json_is_deterministic() -> None:
    p = generate_persona(7003)
    assert expected_json(p) == expected_json(generate_persona(7003))


def test_persona_seeds_are_stable_and_count_independent() -> None:
    assert persona_seeds(7, 6) == [7000, 7001, 7002, 7003, 7004, 7005]
    # doc i is independent of the total count
    assert persona_seeds(7, 3)[:3] == persona_seeds(7, 6)[:3]


def test_committed_expected_json_matches_generator() -> None:
    formpack = load_formpack(UK_CH2)
    assert len(formpack.golden.docs) == 6
    for doc in formpack.golden.docs:
        committed = (UK_CH2 / doc.expected).read_bytes()
        regenerated = expected_json_bytes(expected_json(generate_persona(doc.seed)))
        assert committed == regenerated, f"{doc.id}: committed truth drifted from the generator"


def test_expected_json_has_required_leaf_paths() -> None:
    formpack = load_formpack(UK_CH2)
    p = generate_persona(formpack.golden.docs[0].seed)
    payload = expected_json(p)
    assert payload["claimant"]["last_name"]
    assert payload["claimant"]["nino"]
    assert payload["children"]
    assert payload["children"][0]["date_of_birth"]
