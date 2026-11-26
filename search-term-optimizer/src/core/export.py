"""Mise en forme des résultats et exports CSV pour Google Ads Editor."""

from __future__ import annotations

import pandas as pd

from core.models import (
    DECISION_LABELS,
    NEW_ADGROUP,
    Decision,
    Evaluation,
    Level,
    MatchType,
)

_MATCH = {MatchType.EXACT: "Exact", MatchType.PHRASE: "Phrase", MatchType.BROAD: "Broad"}

METRIC_COLS = ["impressions", "clicks", "cost", "conversions", "conv_value"]


def results_frame(terms: pd.DataFrame, evals: dict[str, Evaluation]) -> pd.DataFrame:
    """Une ligne par terme évalué, avec métriques et recommandation."""
    rows = []
    by_key = terms.set_index("key")
    for key, ev in evals.items():
        if key not in by_key.index:
            continue
        r = by_key.loc[key]
        rows.append(
            {
                "Décision": DECISION_LABELS[ev.decision],
                "Priorité": ev.priority,
                "Terme de recherche": ev.term,
                "Campagne": r["campaign_name"],
                "Groupe d'annonces": r["adgroup_name"],
                "Type": r["channel"],
                "Impr.": int(r["impressions"]),
                "Clics": int(r["clicks"]),
                "Coût": round(float(r["cost"]), 2),
                "Conv.": round(float(r["conversions"]), 2),
                "Valeur conv.": round(float(r["conv_value"]), 2),
                "Performance": r["perf"],
                "Détail perf.": r["perf_reason"],
                "Raison": ev.reason,
                "Source": ev.source,
                "_decision": ev.decision.value,
                "_match": ev.match_type.value,
                "_level": ev.level.value,
                "_negative": ev.negative_text,
                "_target": ev.target_adgroup,
                "_account": r["account_name"],
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["Priorité", "Coût"], ascending=False).reset_index(drop=True)
    return df


def keywords_to_add(results: pd.DataFrame) -> pd.DataFrame:
    """Mots clés à acheter dans un groupe d'annonces existant (format Editor)."""
    if results.empty:
        return pd.DataFrame()
    d = results[(results["_decision"] == Decision.ADD) & (results["_target"] != NEW_ADGROUP)]
    return pd.DataFrame(
        {
            "Account": d["_account"],
            "Campaign": d["Campagne"],
            "Ad Group": d["_target"],
            "Keyword": d["Terme de recherche"],
            "Criterion Type": d["_match"].map(lambda m: _MATCH[MatchType(m)]),
            "Status": "Enabled",
            "Raison": d["Raison"],
            "Priorité": d["Priorité"],
            "Source": d["Source"],
        }
    ).reset_index(drop=True)


def new_adgroup_suggestions(results: pd.DataFrame) -> pd.DataFrame:
    """Termes à acheter pour lesquels aucun groupe existant ne convient."""
    if results.empty:
        return pd.DataFrame()
    d = results[(results["_decision"] == Decision.ADD) & (results["_target"] == NEW_ADGROUP)]
    return pd.DataFrame(
        {
            "Account": d["_account"],
            "Campaign": d["Campagne"],
            "Groupe d'origine": d["Groupe d'annonces"],
            "Keyword": d["Terme de recherche"],
            "Criterion Type": d["_match"].map(lambda m: _MATCH[MatchType(m)]),
            "Clics": d["Clics"],
            "Coût": d["Coût"],
            "Conv.": d["Conv."],
            "Raison": d["Raison"],
            "Priorité": d["Priorité"],
        }
    ).reset_index(drop=True)


def negatives_to_add(results: pd.DataFrame) -> pd.DataFrame:
    """Exclusions à ajouter (format Editor), dédoublonnées."""
    if results.empty:
        return pd.DataFrame()
    d = results[results["_decision"] == Decision.EXCLUDE].copy()
    if d.empty:
        return pd.DataFrame()
    adgroup_level = d["_level"] == Level.ADGROUP
    prefix = adgroup_level.map({True: "Negative ", False: "Campaign Negative "})
    out = pd.DataFrame(
        {
            "Account": d["_account"],
            "Campaign": d["Campagne"],
            "Ad Group": d["Groupe d'annonces"].where(adgroup_level, ""),
            "Keyword": d["_negative"],
            "Criterion Type": prefix + d["_match"].map(lambda m: _MATCH[MatchType(m)]),
            "Terme déclencheur": d["Terme de recherche"],
            "Coût": d["Coût"],
            "Raison": d["Raison"],
            "Priorité": d["Priorité"],
            "Source": d["Source"],
        }
    )
    return out.drop_duplicates(
        subset=["Campaign", "Ad Group", "Keyword", "Criterion Type"]
    ).reset_index(drop=True)


def public_view(results: pd.DataFrame) -> pd.DataFrame:
    """Tableau complet sans les colonnes techniques."""
    if results.empty:
        return results
    out = results.copy()
    out["Correspondance"] = out["_match"]
    out["Niveau exclusion"] = out["_level"].where(out["_decision"] == Decision.EXCLUDE, "")
    out["Texte à exclure"] = out["_negative"]
    out["Groupe cible"] = out["_target"]
    return out[[c for c in out.columns if not c.startswith("_")]]


def to_csv(df: pd.DataFrame) -> bytes:
    """CSV UTF-8 avec BOM : lisible par Excel et par Google Ads Editor."""
    return df.to_csv(index=False).encode("utf-8-sig")
