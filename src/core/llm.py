"""Appels Gemini : résumé du site client et scoring sémantique des termes."""

from __future__ import annotations

import logging
import time

import pandas as pd
import requests
import yaml
from bs4 import BeautifulSoup

from core.config import Settings
from core.models import ClientProfile, LlmEvaluation, LlmEvaluations

logger = logging.getLogger(__name__)

MAX_EXAMPLES_IN_PROMPT = 60
MAX_ADGROUPS_PER_CAMPAIGN = 60
_HTML_EXCLUDE = {"[document]", "noscript", "header", "html", "meta", "head", "input", "script", "style"}

SUMMARY_PROMPT = """\
À partir de la page d'accueil ci-dessous et de tes connaissances, rédige en français \
un résumé concis à puces de cette entreprise : ce qu'elle vend (produits, services), \
à qui (B2B, B2C, secteurs), et sa valeur ajoutée.

{text}

RÉSUMÉ CONCIS À PUCES :
"""

SCORING_PROMPT = """\
Tu es un expert Google Ads. Tu analyses le rapport des termes de recherche d'un annonceur \
et tu rends, pour chaque terme, une décision parmi :

- ADD : acheter ce terme en mot clé (il est pertinent ET sa performance est prouvée).
- EXCLUDE : ajouter une exclusion (mauvaise performance, ou sémantique hors sujet).
- IGNORE : ne rien faire.

Chaque terme arrive avec ses métriques et un champ "perf" calculé par des règles fixées \
par le gestionnaire du compte : GOOD (performance prouvée), BAD (mauvaise performance \
avérée), NEUTRAL (pas assez de données). Ne recalcule pas ce signal, fais-lui confiance.

Règles de décision :
1. ADD uniquement si perf = GOOD, type = SEARCH ou DSA, et le terme correspond bien à \
l'offre de l'annonceur. Choisis "target_adgroup" parmi les groupes d'annonces candidats \
de la MÊME campagne (nom recopié à l'identique) : celui dont les mots clés sont les plus \
proches. Si aucun ne convient, réponds NOUVEAU. match_type = EXACT par défaut.
2. EXCLUDE si le terme est hors sujet par rapport à l'offre (autre produit, autre \
intention : emploi, gratuit, définition, occasion, SAV, etc. selon le contexte), même \
avec peu de données.
3. EXCLUDE si perf = BAD, sauf si le terme est au cœur de l'offre : dans ce cas réponds \
IGNORE et explique qu'il faut plutôt travailler l'enchère, l'annonce ou la page.
4. Si perf = GOOD mais que le terme te semble hors sujet, réponds IGNORE avec une \
priorité élevée pour qu'un humain tranche. N'exclus jamais un terme qui convertit bien.
5. Dans le doute, IGNORE.

Pour EXCLUDE, renseigne :
- negative_text et match_type : si un mot ou groupe de mots précis rend le terme hors \
sujet, mets ce mot seul en PHRASE (il bloquera toutes les requêtes qui le contiennent, \
donc vérifie qu'il n'est jamais pertinent pour cet annonceur). Si c'est le terme entier \
qui pose problème (mauvaise performance isolée), mets le terme entier en EXACT. \
negative_text doit être composé de mots présents dans le terme.
- level : ADGROUP si le terme est pertinent pour l'annonceur mais mal aiguillé dans ce \
groupe d'annonces ; CAMPAIGN sinon. Toujours CAMPAIGN pour PERFORMANCE_MAX.

"priority" va de 1 à 5 (5 = à traiter en premier), selon l'enjeu : coût gaspillé pour \
une exclusion, volume de conversions pour un achat.
"reason" : une ou deux phrases en français, factuelles, sans guillemets.
Rends exactement une évaluation par terme, avec le même "id".

## Contexte annonceur
{company}

## Ce que l'annonceur ne veut pas cibler
{exclude}

## Décisions déjà validées ou corrigées par le gestionnaire (à imiter)
{examples}

## Groupes d'annonces candidats, par campagne
{adgroups}

## Termes à analyser
{terms}
"""


def fetch_page_text(url: str, max_chars: int = 20000) -> str:
    """Texte visible d'une page web."""
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    }
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    texts = [
        str(t).strip()
        for t in soup.find_all(string=True)
        if t.parent.name not in _HTML_EXCLUDE and str(t).strip()
    ]
    return "\n".join(texts)[:max_chars]


def build_payload(
    rows: pd.DataFrame, adgroups: dict[str, dict[str, list[str]]]
) -> tuple[str, str]:
    """YAML des termes (id = position dans rows) et des groupes candidats."""
    terms = []
    campaigns: dict[str, dict] = {}
    for i, r in enumerate(rows.itertuples()):
        terms.append(
            {
                "id": i,
                "terme": r.term,
                "campagne": r.campaign_name,
                "groupe": r.adgroup_name or "(aucun)",
                "type": r.channel,
                "clics": int(r.clicks),
                "cout": round(float(r.cost), 2),
                "conversions": round(float(r.conversions), 2),
                "valeur_conv": round(float(r.conv_value), 2),
                "perf": r.perf,
                "detail_perf": r.perf_reason,
            }
        )
        if r.channel in ("SEARCH", "DSA") and r.perf == "GOOD":
            groups = adgroups.get(str(r.campaign_id), {})
            campaigns[r.campaign_name] = {
                name: ", ".join(kws)
                for name, kws in list(groups.items())[:MAX_ADGROUPS_PER_CAMPAIGN]
            }
    dump = lambda o: yaml.safe_dump(o, allow_unicode=True, sort_keys=False, width=1000)
    return dump(terms), (dump(campaigns) if campaigns else "(aucun achat possible dans ce lot)")


def format_examples(profile: ClientProfile) -> str:
    if not profile.examples:
        return "(aucune pour l'instant)"
    data = [
        {
            "terme": e.term,
            "campagne": e.campaign,
            "type": e.channel,
            "perf": e.perf,
            "decision": e.decision.value,
            **({"negative_text": e.negative_text, "match_type": e.match_type.value}
               if e.negative_text else {}),
            "raison": e.reason,
        }
        for e in profile.examples[-MAX_EXAMPLES_IN_PROMPT:]
    ]
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1000)


def build_prompt(profile: ClientProfile, rows: pd.DataFrame, adgroups) -> str:
    terms, groups = build_payload(rows, adgroups)
    return SCORING_PROMPT.format(
        company=profile.company_pitch.strip() or "(non renseigné)",
        exclude=profile.exclude_pitch.strip() or "(non renseigné)",
        examples=format_examples(profile),
        adgroups=groups,
        terms=terms,
    )


class GeminiScorer:
    """Scoring par Gemini avec sortie structurée (même approche que l'outil d'origine)."""

    def __init__(self, settings: Settings):
        import langchain_google_genai as genai

        if not settings.gemini_api_key:
            raise ValueError("Clé API Gemini manquante (GOOGLE_API_KEY).")
        self.llm = genai.ChatGoogleGenerativeAI(
            model=settings.gemini_model,
            google_api_key=settings.gemini_api_key,
            max_output_tokens=8192,
            temperature=0.2,
        )

    def summarize(self, text: str) -> str:
        out = self.llm.invoke(SUMMARY_PROMPT.format(text=text))
        content = out.content
        if isinstance(content, list):  # certaines versions renvoient des blocs
            content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        return str(content).strip()

    def score(self, profile: ClientProfile, rows: pd.DataFrame, adgroups) -> list[LlmEvaluation]:
        prompt = build_prompt(profile, rows, adgroups)
        structured = self.llm.with_structured_output(LlmEvaluations)
        last_err = None
        for attempt in range(3):
            try:
                result = structured.invoke(prompt)
                return list(result.evaluations) if result else []
            except Exception as err:  # quota, sortie mal formée...
                last_err = err
                logger.warning("Scoring Gemini en échec (essai %d) : %s", attempt + 1, err)
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"Gemini n'a pas répondu correctement : {last_err}")
