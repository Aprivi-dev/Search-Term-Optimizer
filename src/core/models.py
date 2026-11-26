"""Modèles de données : décisions, évaluations et profil client."""

from __future__ import annotations

import enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

NEW_ADGROUP = "NOUVEAU"


class Decision(enum.StrEnum):
    ADD = "ADD"  # acheter le terme en mot clé
    EXCLUDE = "EXCLUDE"  # ajouter une exclusion
    IGNORE = "IGNORE"  # ne rien faire
    UNKNOWN = "UNKNOWN"


class Perf(enum.StrEnum):
    GOOD = "GOOD"
    BAD = "BAD"
    NEUTRAL = "NEUTRAL"


class Channel(enum.StrEnum):
    SEARCH = "SEARCH"
    DSA = "DSA"
    SHOPPING = "SHOPPING"
    PMAX = "PERFORMANCE_MAX"


class MatchType(enum.StrEnum):
    EXACT = "EXACT"
    PHRASE = "PHRASE"
    BROAD = "BROAD"


class Level(enum.StrEnum):
    CAMPAIGN = "CAMPAIGN"
    ADGROUP = "ADGROUP"


class BrandPolicy(enum.StrEnum):
    EXCLUDE = "EXCLUDE"  # exclusion systématique
    BUY = "BUY"  # terme traité normalement (achat autorisé)
    IGNORE = "IGNORE"  # jamais de recommandation


class KpiMode(enum.StrEnum):
    CPA = "CPA"
    ROAS = "ROAS"


DECISION_LABELS = {
    Decision.ADD: "À acheter",
    Decision.EXCLUDE: "À exclure",
    Decision.IGNORE: "À ignorer",
    Decision.UNKNOWN: "Indécis",
}


def _coerce(enum_cls, value, default):
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value.strip().upper())
        except ValueError:
            return default
    return default


class Evaluation(BaseModel):
    """Recommandation sur un terme de recherche (IA, règle ou humain)."""

    key: str
    term: str
    decision: Decision = Decision.UNKNOWN
    reason: str = ""
    priority: int = 0
    match_type: MatchType = MatchType.EXACT
    level: Level = Level.CAMPAIGN
    negative_text: str = ""
    target_adgroup: str = ""
    source: str = "IA"  # IA | Règle | Humain

    @field_validator("priority", mode="before")
    @classmethod
    def _clamp(cls, v):
        try:
            return max(0, min(5, int(v)))
        except (TypeError, ValueError):
            return 0


class LlmEvaluation(BaseModel):
    """Schéma de sortie structurée demandé au modèle pour un terme."""

    id: int = Field(description="Identifiant du terme fourni en entrée")
    reason: str = Field(description="Explication concise, en français")
    decision: Decision = Field(description="ADD, EXCLUDE ou IGNORE")
    priority: int = Field(default=0, description="Priorité de 1 à 5, 5 = à traiter d'abord")
    match_type: MatchType = Field(
        default=MatchType.EXACT, description="EXACT, PHRASE ou BROAD"
    )
    level: Level = Field(
        default=Level.CAMPAIGN, description="Pour EXCLUDE : CAMPAIGN ou ADGROUP"
    )
    negative_text: str = Field(
        default="",
        description="Pour EXCLUDE : texte à exclure (le terme entier ou le mot fautif)",
    )
    target_adgroup: str = Field(
        default="",
        description="Pour ADD : nom exact d'un groupe d'annonces candidat, ou NOUVEAU",
    )

    @field_validator("decision", mode="before")
    @classmethod
    def _d(cls, v):
        return _coerce(Decision, v, Decision.UNKNOWN)

    @field_validator("match_type", mode="before")
    @classmethod
    def _m(cls, v):
        return _coerce(MatchType, v, MatchType.EXACT)

    @field_validator("level", mode="before")
    @classmethod
    def _l(cls, v):
        return _coerce(Level, v, Level.CAMPAIGN)


class LlmEvaluations(BaseModel):
    evaluations: list[LlmEvaluation]


class Thresholds(BaseModel):
    """Seuils de performance, réglables par client."""

    kpi_mode: KpiMode = KpiMode.CPA
    target_cpa: float = 30.0  # € par conversion
    target_roas: float = 4.0  # valeur / coût (4.0 = 400 %)
    # Exclusion pour mauvaise performance
    min_clicks_no_conv: int = 25  # clics sans conversion
    max_cost_no_conv: float = 60.0  # € dépensés sans conversion
    bad_ratio: float = 2.0  # CPA > cible x ratio, ou ROAS < cible / ratio
    # Achat en mot clé
    min_conv_add: float = 2.0
    good_ratio: float = 1.0  # CPA <= cible x ratio, ou ROAS >= cible / ratio


class Example(BaseModel):
    """Correction ou validation humaine, réinjectée dans le prompt."""

    term: str
    campaign: str = ""
    channel: str = ""
    perf: str = ""
    decision: Decision
    reason: str = ""
    match_type: MatchType = MatchType.EXACT
    negative_text: str = ""


class ClientProfile(BaseModel):
    """Tout ce que l'outil retient d'un client d'une session à l'autre."""

    customer_id: str
    name: str = ""
    homepage_url: str = ""
    company_pitch: str = ""
    exclude_pitch: str = ""
    thresholds: Thresholds = Field(default_factory=Thresholds)
    own_brands: list[str] = Field(default_factory=list)
    own_brand_policy: BrandPolicy = BrandPolicy.IGNORE
    competitor_brands: list[str] = Field(default_factory=list)
    competitor_policy: BrandPolicy = BrandPolicy.EXCLUDE
    examples: list[Example] = Field(default_factory=list)

    def add_example(self, ex: Example, max_examples: int = 300) -> None:
        self.examples = [
            e for e in self.examples if (e.term, e.campaign) != (ex.term, ex.campaign)
        ]
        self.examples.append(ex)
        self.examples = self.examples[-max_examples:]

    def context_ready(self) -> bool:
        return bool(self.company_pitch.strip())


def to_optional_float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
