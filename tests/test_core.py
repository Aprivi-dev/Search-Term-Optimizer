"""Tests du cœur métier : règles, garde-fous, scoring et exports (sans API)."""

import sys, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

import pandas as pd

from core import auth, demo, export, rules, scoring
from core.config import Settings
from core.models import (
    BrandPolicy, ClientProfile, Decision, Evaluation, KpiMode, Level, MatchType, Perf, Thresholds,
)
from core.storage import ProfileStore

T = Thresholds(target_cpa=30, min_clicks_no_conv=25, max_cost_no_conv=60, bad_ratio=2, min_conv_add=2)


def test_perf_cpa():
    assert rules.perf_signal(30, 10, 0, 0, T)[0] == Perf.BAD  # clics sans conv
    assert rules.perf_signal(5, 70, 0, 0, T)[0] == Perf.BAD  # coût sans conv
    assert rules.perf_signal(5, 10, 0, 0, T)[0] == Perf.NEUTRAL
    assert rules.perf_signal(80, 64, 6, 0, T)[0] == Perf.GOOD  # CPA 10,7
    assert rules.perf_signal(80, 150, 1, 0, T)[0] == Perf.BAD  # CPA 150 > 60
    assert rules.perf_signal(10, 25, 1, 0, T)[0] == Perf.NEUTRAL  # 1 conv < 2
    assert rules.perf_signal(50, 100, 2, 0, T)[0] == Perf.NEUTRAL  # CPA 50, entre les seuils


def test_perf_roas():
    t = T.model_copy(update={"kpi_mode": KpiMode.ROAS, "target_roas": 4.0})
    assert rules.perf_signal(50, 100, 3, 500, t)[0] == Perf.GOOD  # ROAS 5
    assert rules.perf_signal(50, 100, 3, 150, t)[0] == Perf.BAD  # ROAS 1,5 < 2
    assert rules.perf_signal(50, 100, 3, 300, t)[0] == Perf.NEUTRAL  # ROAS 3


def test_brands_whole_words_and_accents():
    assert rules.find_brand("Carton Éxpress pas cher", ["carton express"]) == "carton express"
    assert rules.find_brand("cartonexpressif", ["cartonexpress"]) == ""


def _setup(**kw):
    profile = ClientProfile(customer_id="1", company_pitch="x", thresholds=T, **kw)
    data = demo.demo_account()
    terms = rules.enrich(data.terms, profile)
    return profile, data, terms


def test_end_to_end_scoring_and_exports():
    profile, data, terms = _setup(competitor_brands=["cartonexpress"])
    evals = scoring.score_rows(terms, profile, data, demo.FakeScorer())
    assert len(evals) == len(terms)
    by = {e.term: e for e in evals.values()}

    # Marque concurrente : décidée par la règle, sans IA.
    assert by["cartonexpress carton"].decision == Decision.EXCLUDE
    assert by["cartonexpress carton"].source == "Règle"
    assert by["cartonexpress carton"].negative_text == "cartonexpress"
    # Bon terme Search : achat, rangé dans le bon groupe.
    assert by["carton demenagement lot 20"].decision == Decision.ADD
    assert by["carton demenagement lot 20"].target_adgroup == "Cartons déménagement"
    # Bon terme sans groupe adapté : nouveau groupe.
    assert by["carton double cannelure 60x40"].target_adgroup == "NOUVEAU"
    # Bons termes Shopping / PMax : jamais d'achat.
    assert by["film etirable manuel"].decision == Decision.IGNORE
    assert by["adhesif emballage 48mm"].decision == Decision.IGNORE
    # Déjà en mot clé : jamais reproposé.
    assert by["carton"].decision != Decision.ADD
    # Hors sujet : exclusion en expression sur le mot fautif.
    assert by["carton demenagement gratuit"].negative_text == "gratuit"
    assert by["carton demenagement gratuit"].match_type == MatchType.PHRASE
    # PMax : exclusion toujours au niveau campagne.
    assert by["recyclage carton entreprise"].level == Level.CAMPAIGN

    res = export.results_frame(terms, evals)
    add, new, neg = export.keywords_to_add(res), export.new_adgroup_suggestions(res), export.negatives_to_add(res)
    assert set(add["Keyword"]) == {
        "carton demenagement lot 20", "enveloppe bulle a4", "pochette matelassee professionnelle"}
    dsa = add[add["Keyword"] == "pochette matelassee professionnelle"].iloc[0]
    assert dsa["Ad Group"] == "Pochettes matelassées"  # terme DSA rangé dans un groupe à mots clés
    assert "carton double cannelure 60x40" in set(new["Keyword"])
    assert set(neg["Criterion Type"]) <= {
        "Campaign Negative Phrase", "Campaign Negative Exact", "Negative Exact", "Negative Phrase"}
    pmax = neg[neg["Campaign"] == "PMax - Global"]
    assert (pmax["Ad Group"] == "").all() and pmax["Criterion Type"].str.startswith("Campaign").all()
    assert export.to_csv(neg).startswith(b"\xef\xbb\xbf")


def test_brand_policies():
    profile, data, terms = _setup(competitor_brands=["cartonexpress"], competitor_policy=BrandPolicy.IGNORE)
    row = next(terms[terms["term"] == "cartonexpress carton"].itertuples())
    assert rules.pre_score(row, profile).decision == Decision.IGNORE
    profile.competitor_policy = BrandPolicy.BUY
    assert rules.pre_score(row, profile) is None  # part à l'IA comme un terme normal


def test_guardrails_reject_invented_negative_and_bad_add():
    profile, data, terms = _setup()
    row = next(terms[terms["term"] == "carton pas cher"].itertuples())
    ev = Evaluation(key=row.key, term=row.term, decision=Decision.EXCLUDE,
                    negative_text="mot inventé", match_type=MatchType.PHRASE)
    out = rules.apply_guardrails(ev, row, set(), [])
    assert out.negative_text == "carton pas cher" and out.match_type == MatchType.EXACT
    # L'IA ne peut pas acheter un terme à mauvaise perf ; l'humain peut (strict=False).
    ev = Evaluation(key=row.key, term=row.term, decision=Decision.ADD, target_adgroup="Inconnu")
    assert rules.apply_guardrails(ev, row, set(), ["Cartons standard"]).decision == Decision.IGNORE
    human = rules.apply_guardrails(ev, row, set(), ["Cartons standard"], strict=False)
    assert human.decision == Decision.ADD and human.target_adgroup == "NOUVEAU"


def test_missing_llm_answer_is_unknown():
    profile, data, terms = _setup()

    class Silent:
        def score(self, *a):
            return []

    evals = scoring.score_rows(terms.head(3), profile, data, Silent())
    assert all(e.decision == Decision.UNKNOWN for e in evals.values())


def test_already_excluded_terms_are_dropped():
    from core.ads import clean_terms
    df = pd.DataFrame({"term": ["a b", "c", "d"], "status": ["NONE", "EXCLUDED", "NONE"],
                       "campaign_id": ["1", "1", "1"]})
    assert list(clean_terms(df, {"1": {"a b"}})["term"]) == ["d"]


def test_profile_memory_is_per_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROFILES_DIR", str(tmp_path))
    store = ProfileStore()
    a = store.load("111", "A"); a.company_pitch = "cartons"; store.save(a)
    assert store.load("111").company_pitch == "cartons"
    assert store.load("222").company_pitch == ""


def test_oauth_state():
    s = Settings(oauth_client_secret="secret")
    assert auth.check_state(auth.make_state(s), s)
    assert not auth.check_state("123.bad", s) and not auth.check_state("garbage", s)


def test_prompt_contains_context_examples_and_candidates():
    from core.llm import build_prompt
    from core.models import Example
    profile, data, terms = _setup()
    profile.examples.append(Example(term="carton occasion", decision=Decision.EXCLUDE, reason="pas d'occasion"))
    p = build_prompt(profile, terms, data.adgroups)
    assert "pas d'occasion" in p and "Cartons déménagement" in p and "carton demenagement lot 20" in p
