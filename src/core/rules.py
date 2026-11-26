"""Règles déterministes : signal de performance, marques, garde-fous post-IA.

Tout ce qui est ici est calculé sans IA, donc reproductible et auditable.
"""

from __future__ import annotations

import re
import unicodedata

import pandas as pd

from core.models import (
    NEW_ADGROUP,
    BrandPolicy,
    Channel,
    ClientProfile,
    Decision,
    Evaluation,
    KpiMode,
    Level,
    MatchType,
    Perf,
    Thresholds,
)

MAX_NEGATIVE_WORDS = 10
MAX_NEGATIVE_CHARS = 80


def normalize(text: str) -> str:
    """Minuscules, sans accents ni ponctuation, espaces simples."""
    text = unicodedata.normalize("NFKD", str(text).lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", text)).strip()


def _fmt(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ").replace(".", ",")


def perf_signal(
    clicks: float, cost: float, conversions: float, conv_value: float, t: Thresholds
) -> tuple[Perf, str]:
    """Classe un terme en GOOD / BAD / NEUTRAL selon les seuils du client."""
    if conversions <= 0:
        if clicks >= t.min_clicks_no_conv:
            return Perf.BAD, f"{int(clicks)} clics sans conversion (seuil {t.min_clicks_no_conv})"
        if cost >= t.max_cost_no_conv:
            return (
                Perf.BAD,
                f"{_fmt(cost)} € dépensés sans conversion (seuil {_fmt(t.max_cost_no_conv)} €)",
            )
        return Perf.NEUTRAL, "Pas assez de données"

    if t.kpi_mode == KpiMode.CPA:
        cpa = cost / conversions
        if cpa > t.target_cpa * t.bad_ratio and cost >= t.max_cost_no_conv:
            return (
                Perf.BAD,
                f"CPA {_fmt(cpa)} € > {t.bad_ratio:g} x la cible ({_fmt(t.target_cpa)} €)",
            )
        if conversions >= t.min_conv_add and cpa <= t.target_cpa * t.good_ratio:
            return (
                Perf.GOOD,
                f"{conversions:g} conv. à {_fmt(cpa)} € de CPA (cible {_fmt(t.target_cpa)} €)",
            )
        return Perf.NEUTRAL, f"CPA {_fmt(cpa)} €, entre les seuils"

    roas = conv_value / cost if cost > 0 else 0.0
    if roas < t.target_roas / t.bad_ratio and cost >= t.max_cost_no_conv:
        return (
            Perf.BAD,
            f"ROAS {_fmt(roas)} < cible {_fmt(t.target_roas)} / {t.bad_ratio:g}",
        )
    if conversions >= t.min_conv_add and roas >= t.target_roas / t.good_ratio:
        return (
            Perf.GOOD,
            f"{conversions:g} conv., ROAS {_fmt(roas)} (cible {_fmt(t.target_roas)})",
        )
    return Perf.NEUTRAL, f"ROAS {_fmt(roas)}, entre les seuils"


def find_brand(term: str, brands: list[str]) -> str:
    """Renvoie la première marque présente dans le terme (mots entiers)."""
    padded = f" {normalize(term)} "
    for brand in brands:
        b = normalize(brand)
        if b and f" {b} " in padded:
            return brand
    return ""


def enrich(df: pd.DataFrame, profile: ClientProfile) -> pd.DataFrame:
    """Ajoute perf, raison, marques détectées et clé unique à chaque ligne."""
    df = df.copy()
    if df.empty:
        return df
    signals = [
        perf_signal(r.clicks, r.cost, r.conversions, r.conv_value, profile.thresholds)
        for r in df.itertuples()
    ]
    df["perf"] = [s[0].value for s in signals]
    df["perf_reason"] = [s[1] for s in signals]
    df["own_brand"] = [find_brand(t, profile.own_brands) for t in df["term"]]
    df["competitor"] = [find_brand(t, profile.competitor_brands) for t in df["term"]]
    df["key"] = (
        df["campaign_id"].astype(str)
        + "|"
        + df["adgroup_id"].astype(str)
        + "|"
        + df["term"].astype(str)
    )
    return df.drop_duplicates(subset=["key"]).reset_index(drop=True)


def pre_score(row, profile: ClientProfile) -> Evaluation | None:
    """Décisions prises sans IA (marques). None = à envoyer au modèle."""
    if row.competitor:
        policy = profile.competitor_policy
        if policy == BrandPolicy.EXCLUDE:
            return Evaluation(
                key=row.key,
                term=row.term,
                decision=Decision.EXCLUDE,
                reason=f"Contient la marque concurrente {row.competitor} (consigne client : exclure).",
                priority=4,
                match_type=MatchType.PHRASE,
                level=Level.CAMPAIGN,
                negative_text=normalize(row.competitor),
                source="Règle",
            )
        if policy == BrandPolicy.IGNORE:
            return Evaluation(
                key=row.key,
                term=row.term,
                decision=Decision.IGNORE,
                reason=f"Contient la marque concurrente {row.competitor} (consigne client : ignorer).",
                source="Règle",
            )
    if row.own_brand:
        policy = profile.own_brand_policy
        if policy == BrandPolicy.EXCLUDE:
            return Evaluation(
                key=row.key,
                term=row.term,
                decision=Decision.EXCLUDE,
                reason=f"Contient la marque du client {row.own_brand} (consigne client : exclure).",
                priority=3,
                match_type=MatchType.PHRASE,
                level=Level.CAMPAIGN,
                negative_text=normalize(row.own_brand),
                source="Règle",
            )
        if policy == BrandPolicy.IGNORE:
            return Evaluation(
                key=row.key,
                term=row.term,
                decision=Decision.IGNORE,
                reason=f"Contient la marque du client {row.own_brand} (consigne client : ignorer).",
                source="Règle",
            )
    return None


def can_add(row, exact_keywords: set[str], strict: bool = False) -> tuple[bool, str]:
    """Un terme peut-il être proposé à l'achat ?

    strict=True (recommandations IA) : la performance doit être prouvée (GOOD).
    strict=False (décision humaine) : seules les impossibilités techniques bloquent.
    """
    if row.channel not in (Channel.SEARCH.value, Channel.DSA.value):
        return False, "pas de mots clés sur ce type de campagne"
    if row.status in ("ADDED", "ADDED_EXCLUDED"):
        return False, "déjà ajouté en mot clé"
    if normalize(row.term) in exact_keywords:
        return False, "existe déjà en mot clé exact dans le compte"
    if strict and row.perf != Perf.GOOD.value:
        return False, "performance non prouvée selon les seuils du client"
    return True, ""


def apply_guardrails(
    ev: Evaluation,
    row,
    exact_keywords: set[str],
    campaign_adgroups: list[str],
    strict: bool = True,
) -> Evaluation:
    """Corrige une recommandation IA pour qu'elle reste applicable dans Google Ads."""
    ev = ev.model_copy()
    if ev.decision == Decision.ADD:
        ok, why = can_add(row, exact_keywords, strict)
        if not ok:
            ev.decision = Decision.IGNORE
            ev.reason = f"{ev.reason} [Achat écarté : {why}.]".strip()
            return ev
        if ev.target_adgroup not in campaign_adgroups:
            ev.target_adgroup = NEW_ADGROUP
        ev.negative_text = ""
    elif ev.decision == Decision.EXCLUDE:
        term_n = normalize(row.term)
        neg = normalize(ev.negative_text) or term_n
        # Le texte exclu doit être une suite de mots du terme, sinon terme entier en exact.
        if f" {neg} " not in f" {term_n} ":
            neg, ev.match_type = term_n, MatchType.EXACT
        if ev.match_type == MatchType.BROAD and neg == term_n:
            ev.match_type = MatchType.EXACT
        words = neg.split()
        if len(words) > MAX_NEGATIVE_WORDS or len(neg) > MAX_NEGATIVE_CHARS:
            neg = " ".join(words[:MAX_NEGATIVE_WORDS])[:MAX_NEGATIVE_CHARS].strip()
            ev.match_type = MatchType.PHRASE
            ev.reason += " [Texte raccourci : limite de 10 mots / 80 caractères.]"
        ev.negative_text = neg
        ev.target_adgroup = ""
        # PMax n'a pas de groupe d'annonces : exclusion au niveau campagne.
        if row.channel == Channel.PMAX.value or not row.adgroup_name:
            ev.level = Level.CAMPAIGN
    else:
        ev.negative_text = ""
        ev.target_adgroup = ""
    return ev
