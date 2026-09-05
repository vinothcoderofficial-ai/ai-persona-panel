"""Generate the collection links that put colleagues in front of the store.

S21's other half. `scripts/anonymise_sessions.py` turns what comes back into
the committed corpus; this hands out what goes in.

    python scripts/collect_link.py --n 100 --base-url https://twin.corp.example
    python scripts/collect_link.py --n 100 --base-url https://twin.corp.example --csv

Each participant gets their own link carrying a pre-assigned variant, rather
than one shared link that randomises in the browser. Two reasons, both about
the panel rather than convenience:

  * **Balance is guaranteed, not hoped for.** Client-side randomisation over
    ~100 people leaves a real chance of a lopsided split, and every metric that
    compares variant A against its B/C holdout gets weaker when one arm is
    thin. Here the split is exact to within one session.
  * **The batch is reproducible from a seed.** "Who got which variant" can be
    regenerated on demand, so it never has to be stored in a spreadsheet
    alongside people's names. The seed is the only thing worth keeping.

The links carry no identity. Nothing in the URL says who a person is: it is a
variant assignment and nothing else, and the `session_id` is generated in the
browser by `crypto.randomUUID()` when the session starts. Two people who swap
links produce two perfectly valid sessions -- which is fine, because the
analysis is between variants, not between people.
"""
from __future__ import annotations

import argparse
import pathlib
import random
import sys
from collections import Counter
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VARIANTS_DIR = ROOT / "data" / "variants"

# PLAN section 6: "Target >= 60 accepted, aim 100." Sessions are lost to the
# session gate, so a batch of exactly 60 links will not produce 60 accepted.
PLAN_TARGET_ACCEPTED = 60

# Browsers only expose getUserMedia in a secure context. An http:// link to
# anything but localhost silently forces every shopper into cursor_only.
SECURE_HTTP_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class CollectLinkError(Exception):
    """Base for every refusal."""


class UnknownVariant(CollectLinkError):
    """A variant with no JSON in data/variants/."""


class InsecureBaseUrl(CollectLinkError):
    """A base URL that would disable the webcam for everyone who opened it."""


@dataclass(frozen=True)
class Assignment:
    index: int
    variant_id: str
    url: str


def known_variants() -> tuple:
    """Variant ids that actually have a document, read from disk."""
    return tuple(sorted(path.stem for path in VARIANTS_DIR.glob("*.json")))


def _check_base_url(base_url: str) -> None:
    parts = urlsplit(base_url)
    if parts.scheme == "https":
        return
    if parts.scheme == "http" and (parts.hostname or "") in SECURE_HTTP_HOSTS:
        return
    raise InsecureBaseUrl(
        f"base URL {base_url!r} is not a secure context. Browsers expose "
        "getUserMedia only over https (or on localhost), so every shopper who "
        "opened this would fall back to cursor_only and the webcam arm would be "
        "empty without anyone noticing."
    )


def build_url(base_url: str, variant_id: str) -> str:
    """`base_url` with `variant` added, preserving any query it already had."""
    parts = urlsplit(base_url)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != "variant"]
    query.append(("variant", variant_id))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def assign(n: int, *, base_url: str, variants, seed: int) -> list:
    """`n` links, split as evenly as possible across `variants`, shuffled by `seed`."""
    variants = tuple(variants)
    if n < 1:
        raise ValueError(f"n must be at least 1, got {n}")
    if not variants:
        raise ValueError("at least one variant is required")

    available = known_variants()
    unknown = [v for v in variants if v not in available]
    if unknown:
        raise UnknownVariant(
            f"no document in data/variants/ for {', '.join(sorted(unknown))} "
            f"(available: {', '.join(available)}). A link to a missing variant "
            "would fail for every shopper who received it."
        )

    _check_base_url(base_url)

    # Deal round-robin, then shuffle. Dealing first is what makes the split
    # exact; shuffling stops the order from tracking whatever order the
    # operator happens to send them in.
    pool = [variants[index % len(variants)] for index in range(n)]
    random.Random(seed).shuffle(pool)

    return [
        Assignment(index=index + 1, variant_id=variant_id,
                   url=build_url(base_url, variant_id))
        for index, variant_id in enumerate(pool)
    ]


def summary(assignments) -> str:
    counts = Counter(a.variant_id for a in assignments)
    split = ", ".join(f"{variant_id} {counts[variant_id]}" for variant_id in sorted(counts))
    lines = [f"{len(assignments)} link(s): {split}"]
    if len(assignments) < PLAN_TARGET_ACCEPTED:
        lines.append(
            f"warning: short of PLAN's target -- {len(assignments)} links cannot yield "
            f"{PLAN_TARGET_ACCEPTED} accepted sessions even if every one is accepted, "
            "and the session gate will reject some."
        )
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=100, help="how many links to make")
    parser.add_argument("--base-url", required=True,
                        help="where the store is hosted, e.g. https://twin.corp.example")
    parser.add_argument("--variants", nargs="+", default=None,
                        help="variant ids to split across (default: every variant on disk)")
    parser.add_argument("--seed", type=int, default=42,
                        help="keep this: it regenerates the whole batch")
    parser.add_argument("--csv", action="store_true", help="emit index,variant_id,url")
    args = parser.parse_args(argv)

    try:
        assignments = assign(
            args.n,
            base_url=args.base_url,
            variants=args.variants if args.variants else known_variants(),
            seed=args.seed,
        )
    except (CollectLinkError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.csv:
        print("index,variant_id,url")
        for assignment in assignments:
            print(f"{assignment.index},{assignment.variant_id},{assignment.url}")
    else:
        for assignment in assignments:
            print(assignment.url)

    print(f"\n{summary(assignments)}", file=sys.stderr)
    print(f"seed {args.seed} -- rerun with the same seed to regenerate this batch",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
