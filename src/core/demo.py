"""Mode démo (DEMO_MODE=1) : compte fictif et faux modèle.

Sert à voir l'interface et à tester le parcours sans developer token ni clé Gemini.
Aucune donnée réelle, aucun appel externe.
"""

from __future__ import annotations

import zlib

import pandas as pd

from core.ads import TERM_COLUMNS, AccountData, clean_terms
from core.models import Decision, Level, LlmEvaluation, MatchType
from core.rules import normalize

DEMO_CUSTOMERS = {"1111111111": "Démo - Emballages Dupont (fictif)"}

DEMO_PITCH = (
    "- Vend des emballages aux professionnels : cartons, enveloppes, film étirable, adhésifs\n"
    "- Clientèle B2B (e-commerçants, logistique, industrie)\n"
    "- Livraison rapide, vente par lots"
)

_OFF_TOPIC = ["gratuit", "emploi", "occasion", "definition", "recyclage", "dessin"]

# (terme, canal, campagne, groupe, impr, clics, coût, conv, valeur, statut)
_ROWS = [
    ("carton demenagement lot 20", "SEARCH", "Search - Cartons", "Cartons standard", 900, 80, 64.0, 6, 540.0, "NONE"),
    ("carton double cannelure 60x40", "SEARCH", "Search - Cartons", "Cartons standard", 400, 45, 41.0, 4, 380.0, "NONE"),
    ("carton demenagement gratuit", "SEARCH", "Search - Cartons", "Cartons standard", 1200, 60, 38.0, 0, 0.0, "NONE"),
    ("carton pas cher", "SEARCH", "Search - Cartons", "Cartons standard", 3000, 140, 150.0, 1, 40.0, "NONE"),
    ("emploi cartonnerie lyon", "SEARCH", "Search - Cartons", "Cartons standard", 150, 9, 6.5, 0, 0.0, "NONE"),
    ("carton", "SEARCH", "Search - Cartons", "Cartons standard", 5000, 30, 21.0, 2, 90.0, "ADDED"),
    ("enveloppe bulle a4", "SEARCH", "Search - Enveloppes", "Enveloppes bulles", 700, 66, 39.0, 5, 310.0, "NONE"),
    ("enveloppe kraft dessin", "SEARCH", "Search - Enveloppes", "Enveloppes bulles", 90, 6, 3.1, 0, 0.0, "NONE"),
    ("pochette matelassee professionnelle", "DSA", "Search - Enveloppes", "DSA catalogue", 300, 28, 19.0, 3, 205.0, "NONE"),
    ("cartonexpress carton", "SEARCH", "Search - Cartons", "Cartons standard", 260, 22, 17.0, 0, 0.0, "NONE"),
    ("film etirable manuel", "SHOPPING", "Shopping - Catalogue", "Tous les produits", 800, 70, 33.0, 5, 260.0, "NONE"),
    ("film etirable occasion", "SHOPPING", "Shopping - Catalogue", "Tous les produits", 210, 31, 14.0, 0, 0.0, "NONE"),
    ("ruban adhesif definition", "PERFORMANCE_MAX", "PMax - Global", "", 120, 12, 5.0, 0, 0.0, "NONE"),
    ("adhesif emballage 48mm", "PERFORMANCE_MAX", "PMax - Global", "", 640, 52, 30.0, 4, 190.0, "NONE"),
    ("recyclage carton entreprise", "PERFORMANCE_MAX", "PMax - Global", "", 310, 40, 71.0, 0, 0.0, "NONE"),
    ("caisse carton renforcee", "SEARCH", "Search - Cartons", "Cartons standard", 180, 14, 11.0, 1, 75.0, "NONE"),
]

_CAMPAIGN_IDS = {"Search - Cartons": "101", "Search - Enveloppes": "102",
                 "Shopping - Catalogue": "201", "PMax - Global": "301"}


def demo_account() -> AccountData:
    records = []
    for term, ch, camp, ag, impr, clicks, cost, conv, value, status in _ROWS:
        records.append(
            dict(zip(TERM_COLUMNS, [
                term, status, "1111111111", DEMO_CUSTOMERS["1111111111"],
                _CAMPAIGN_IDS[camp], camp, ch,
                (_CAMPAIGN_IDS[camp] + str(zlib.crc32(ag.encode()) % 1000)) if ag else "", ag,
                impr, clicks, cost, conv, value,
            ]))
        )
    terms = clean_terms(pd.DataFrame(records, columns=TERM_COLUMNS), {})
    adgroups = {
        "101": {"Cartons standard": ["carton", "carton emballage"],
                "Cartons déménagement": ["carton demenagement", "kit demenagement"]},
        "102": {"Enveloppes bulles": ["enveloppe bulle", "enveloppe a bulles"],
                "Pochettes matelassées": ["pochette matelassee"]},
    }
    return AccountData(terms, {"carton"}, adgroups)


class FakeScorer:
    """Imite le modèle avec des règles simples, pour le mode démo et les tests."""

    def summarize(self, text: str) -> str:
        return DEMO_PITCH

    def score(self, profile, rows, adgroups) -> list[LlmEvaluation]:
        out = []
        for i, r in enumerate(rows.itertuples()):
            words = normalize(r.term).split()
            bad_word = next((w for w in words if w in _OFF_TOPIC), None)
            if bad_word:
                out.append(LlmEvaluation(
                    id=i, decision=Decision.EXCLUDE, priority=4,
                    reason=f"Intention hors cible : le mot {bad_word} ne correspond pas à une vente B2B.",
                    match_type=MatchType.PHRASE, level=Level.CAMPAIGN, negative_text=bad_word))
            elif r.perf == "BAD":
                out.append(LlmEvaluation(
                    id=i, decision=Decision.EXCLUDE, priority=3,
                    reason="Terme coûteux sans retour suffisant.",
                    match_type=MatchType.EXACT, level=Level.ADGROUP, negative_text=r.term))
            elif r.perf == "GOOD":
                groups = adgroups.get(str(r.campaign_id), {})
                target = next(
                    (g for g, kws in groups.items()
                     if any(set(normalize(k).split()) <= set(words) and len(k.split()) > 1 for k in kws)),
                    "NOUVEAU",
                )
                out.append(LlmEvaluation(
                    id=i, decision=Decision.ADD, priority=5,
                    reason="Terme pertinent et rentable, à sécuriser en mot clé exact.",
                    match_type=MatchType.EXACT, target_adgroup=target))
            else:
                out.append(LlmEvaluation(
                    id=i, decision=Decision.IGNORE, priority=1,
                    reason="Pertinent mais pas assez de données pour conclure."))
        return out
