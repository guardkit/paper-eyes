"""Seeded synthetic personas for the CH2 corpus (design spec §6 Stage 1).

A persona is a **pure function of its integer seed** — same seed, identical persona, identical
ground-truth JSON. That is what makes the golden corpus regenerable from committed seeds without
committing the scans. All people are synthetic (Faker ``en_GB``); none is a real individual and
none derives from the reference client engagement.

Determinism rules that matter here:
- no relative-to-today dates anywhere (they would drift day to day); all date ranges are
  absolute bounds fed to Faker's seeded RNG;
- a fixed field-generation order, so the RNG sequence is stable;
- the National Insurance number is synthesised from a separate seeded ``random.Random`` — a
  plausible-looking synthetic identifier, not a validated real one.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date
from typing import Any

from faker import Faker

# NINO prefix letters, roughly following the real disallowed-letter rule (no D, F, I, O, Q, U, V)
# so the synthetic identifiers *look* right without reproducing any real allocation.
_NINO_LETTERS = "ABCEGHJKLMNPRSTWXYZ"
_NINO_SUFFIX = "ABCD"
_TITLES = ("Mr", "Mrs", "Ms", "Miss")


@dataclass(frozen=True)
class Child:
    """One synthetic child on the claim."""

    first_names: str
    last_name: str
    date_of_birth: str  # ISO YYYY-MM-DD


@dataclass(frozen=True)
class Persona:
    """A synthetic Child Benefit claimant and their children — deterministic per seed."""

    seed: int
    title: str
    first_names: str
    last_name: str
    nino: str
    date_of_birth: str  # ISO YYYY-MM-DD
    address_lines: tuple[str, ...]
    postcode: str
    children: tuple[Child, ...]


def _synth_nino(rng: random.Random) -> str:
    prefix = rng.choice(_NINO_LETTERS) + rng.choice(_NINO_LETTERS)
    digits = f"{rng.randint(0, 999999):06d}"
    return f"{prefix}{digits}{rng.choice(_NINO_SUFFIX)}"


def generate_persona(seed: int) -> Persona:
    """Generate the persona for ``seed`` — a pure function of the seed."""
    fake = Faker("en_GB")
    fake.seed_instance(seed)
    rng = random.Random(seed)

    title = _TITLES[rng.randrange(len(_TITLES))]
    first_names = fake.first_name()
    last_name = fake.last_name()
    nino = _synth_nino(rng)
    dob = fake.date_between_dates(date(1970, 1, 1), date(2003, 12, 31)).isoformat()
    address_lines = (fake.street_address().replace("\n", ", "), fake.city())
    postcode = fake.postcode()

    n_children = rng.randint(1, 3)
    children: list[Child] = []
    for _ in range(n_children):
        child_first = fake.first_name()
        child_dob = fake.date_between_dates(date(2010, 1, 1), date(2024, 12, 31)).isoformat()
        children.append(
            Child(first_names=child_first, last_name=last_name, date_of_birth=child_dob)
        )

    return Persona(
        seed=seed,
        title=title,
        first_names=first_names,
        last_name=last_name,
        nino=nino,
        date_of_birth=dob,
        address_lines=address_lines,
        postcode=postcode,
        children=tuple(children),
    )


def persona_seeds(base_seed: int, count: int) -> list[int]:
    """The deterministic per-doc seeds derived from a base seed.

    A stable, order-independent derivation so ``--seed S --count N`` always yields the same N
    personas, and doc *i* is independent of ``count``.
    """
    return [base_seed * 1000 + i for i in range(count)]


def expected_json(persona: Persona) -> dict[str, Any]:
    """The ground-truth extraction JSON for a persona (matches ``formpacks/uk-ch2/schema.json``).

    This is what a perfect run of the pipeline should extract — the generator knows the truth,
    so the golden set is self-labelling (no hand annotation).
    """
    return {
        "claimant": {
            "title": persona.title,
            "first_names": persona.first_names,
            "last_name": persona.last_name,
            "full_name": f"{persona.first_names} {persona.last_name}",
            "nino": persona.nino,
            "date_of_birth": persona.date_of_birth,
            "address_lines": list(persona.address_lines),
            "postcode": persona.postcode,
        },
        "children": [
            {
                "first_names": c.first_names,
                "last_name": c.last_name,
                "full_name": f"{c.first_names} {c.last_name}",
                "date_of_birth": c.date_of_birth,
            }
            for c in persona.children
        ],
    }
