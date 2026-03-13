import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


_WORD_RE = re.compile(r"[a-z0-9]+")


ALIASES: dict[str, str] = {
    "car": "civilian vehicle",
    "civilian car": "civilian vehicle",
    "civilian truck": "utility vehicle",
    "pickup": "utility vehicle",
    "pickup truck": "utility vehicle",
    "truck": "cross country truck",
    "lorry": "cross country truck",
    "heavy tank": "tank heavy",
    "light tank": "tank light",
    "drone": "drone uav",
    "uav": "drone uav",
    "quadcopter": "drone uav",
    "fixed wing drone": "drone uav fixed wing",
    "rotary drone": "drone uav rotor",
    "helicopter": "rotary wing",
    "helo": "rotary wing",
    "civilian helicopter": "civilian rotary wing",
    "civilian plane": "civilian fixed wing",
    "plane": "fixed wing",
    "infantry": "ground unit",
    "soldier": "ground unit",
    "troops": "ground unit",
}


@dataclass(frozen=True)
class CotTypeEntry:
    """Entry for a CoT type from the catalog.

    Attributes:
        cot: CoT type code.
        full: Full description.
        desc: Short description.
        normalized_full: Normalized full description.
        normalized_desc: Normalized short description.
        tokens: Set of tokens for matching.
    """
    cot: str
    full: str
    desc: str
    normalized_full: str
    normalized_desc: str
    tokens: frozenset[str]


@dataclass(frozen=True)
class CotMatch:
    """Result of matching a target to a CoT type.

    Attributes:
        entry: The matched CoT type entry.
        canonical_query: The normalized query used.
        score: Matching score.
    """
    entry: CotTypeEntry
    canonical_query: str
    score: int


class CotTypeCatalogService:
    """Service for managing and resolving CoT type catalogs."""

    def _normalize_text(self, text: str) -> str:
        """Normalize text by lowercasing and joining words."""
        return " ".join(_WORD_RE.findall(text.lower()))

    def _tokenize(self, text: str) -> frozenset[str]:
        """Tokenize text into a set of words."""
        return frozenset(_WORD_RE.findall(text.lower()))

    def _catalog_path(self) -> Path:
        """Get the path to the CoT types XML catalog."""
        return Path(__file__).resolve().parent.parent / "assets" / "CoTtypes.xml"

    @lru_cache(maxsize=1)
    def load_catalog(self) -> tuple[CotTypeEntry, ...]:
        """Load CoT type entries from the XML catalog."""
        path = self._catalog_path()
        tree = ET.parse(path)
        root = tree.getroot()

        entries: list[CotTypeEntry] = []
        for elem in root.findall(".//cot"):
            cot = (elem.attrib.get("cot") or "").strip()
            full = (elem.attrib.get("full") or "").strip()
            desc = (elem.attrib.get("desc") or "").strip()

            if not cot:
                continue

            normalized_full = self._normalize_text(full)
            normalized_desc = self._normalize_text(desc)
            tokens = self._tokenize(f"{full} {desc}")

            entries.append(
                CotTypeEntry(
                    cot=cot,
                    full=full,
                    desc=desc,
                    normalized_full=normalized_full,
                    normalized_desc=normalized_desc,
                    tokens=tokens,
                )
            )

        if not entries:
            raise RuntimeError(f"No CoT type entries loaded from {path}")

        return tuple(entries)

    def _score_entry(self, query: str, query_tokens: frozenset[str], entry: CotTypeEntry) -> int:
        """Score how well a query matches a CoT entry."""
        score = 0

        if query == entry.normalized_desc:
            score += 1000
        if query == entry.normalized_full:
            score += 900
        if query and query in entry.normalized_desc:
            score += 300
        if query and query in entry.normalized_full:
            score += 250

        overlap = len(query_tokens & entry.tokens)
        score += overlap * 40

        if overlap:
            score += min(len(entry.tokens), 12)

        return score

    def resolve_cot_type(self, target_text: str) -> CotMatch:
        """Resolve a target text to the best matching CoT type.

        Args:
            target_text: The target description to match.

        Returns:
            The best matching CotMatch, or fallback if none.
        """
        original = " ".join(target_text.strip().split())
        if not original:
            raise ValueError("Target description is missing.")

        alias_query = ALIASES.get(original.lower(), original)
        query = self._normalize_text(alias_query)
        query_tokens = self._tokenize(query)

        if not query_tokens:
            raise ValueError("Target description is missing.")

        best: CotMatch | None = None

        for entry in self.load_catalog():
            score = self._score_entry(query, query_tokens, entry)
            if score <= 0:
                continue

            match = CotMatch(
                entry=entry,
                canonical_query=query,
                score=score,
            )

            if best is None or match.score > best.score:
                best = match

        if best is None:
            fallback = CotTypeEntry(
                cot="a-.-G-U",
                full="Gnd/Unit",
                desc="GROUND UNIT",
                normalized_full="gnd unit",
                normalized_desc="ground unit",
                tokens=frozenset({"gnd", "unit", "ground"}),
            )
            return CotMatch(entry=fallback, canonical_query=query, score=0)

        return best

