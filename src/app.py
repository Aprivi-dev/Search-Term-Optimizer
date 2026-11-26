"""Search Term Optimizer : interface Streamlit.

Parcours : contexte client -> chargement des termes -> entraînement (lots à valider)
-> scoring du reste -> exports Google Ads Editor. L'outil ne modifie jamais le compte.
"""

from __future__ import annotations

import datetime as dt
import os
import random

import pandas as pd
import streamlit as st

from core import auth, demo, export, rules, scoring
from core.config import Settings
from core.models import (
    DECISION_LABELS,
    NEW_ADGROUP,
    BrandPolicy,
    Channel,
    Decision,
    Evaluation,
    KpiMode,
    Level,
    MatchType,
)
from core.storage import ProfileStore

st.set_page_config(page_title="Search Term Optimizer", page_icon="🔎", layout="wide")

SETTINGS = Settings()
ss = st.session_state

CHANNEL_LABELS = {
    Channel.SEARCH.value: "Search",
    Channel.DSA.value: "DSA",
    Channel.SHOPPING.value: "Shopping",
    Channel.PMAX.value: "Performance Max",
}
POLICY_LABELS = {
    BrandPolicy.EXCLUDE: "Exclure systématiquement",
    BrandPolicy.BUY: "Traiter comme un terme normal (achat possible)",
    BrandPolicy.IGNORE: "Ignorer (aucune recommandation)",
}
PERF_BADGE = {"GOOD": "🟢 Bonne perf.", "BAD": "🔴 Mauvaise perf.", "NEUTRAL": "⚪ Données insuffisantes"}
DECISIONS = [Decision.ADD, Decision.EXCLUDE, Decision.IGNORE]
EDIT_LABELS = {d: DECISION_LABELS[d] for d in DECISIONS}
KPI_LABELS = {KpiMode.CPA: "CPA (coût par conversion)", KpiMode.ROAS: "ROAS (valeur / coût)"}
MATCH_LABELS = {MatchType.EXACT: "Exact", MatchType.PHRASE: "Expression", MatchType.BROAD: "Large"}
LEVEL_LABELS = {Level.CAMPAIGN: "Campagne", Level.ADGROUP: "Groupe d'annonces"}


def choose(widget, label: str, mapping: dict, current=None, **kwargs):
    """Liste de choix affichant des libellés et renvoyant la valeur associée."""
    keys, labels = list(mapping), list(mapping.values())
    index = keys.index(current) if current in keys else 0
    return keys[labels.index(widget(label, labels, index=index, **kwargs))]


def from_label(mapping: dict, label, default):
    return next((k for k, v in mapping.items() if v == label), default)


# ---------------------------------------------------------------- services


@st.cache_resource
def get_store() -> ProfileStore:
    return ProfileStore()


def get_scorer():
    if "scorer" not in ss:
        if SETTINGS.demo_mode:
            ss.scorer = demo.FakeScorer()
        else:
            from core.llm import GeminiScorer

            ss.scorer = GeminiScorer(SETTINGS)
    return ss.scorer


def ensure_auth() -> str:
    """Renvoie un refresh token, ou affiche le bouton de connexion et s'arrête."""
    if SETTINGS.demo_mode:
        return "demo"
    # Jeton fixe : réservé au poste local. Ignoré sur Cloud Run (URL publique).
    if SETTINGS.refresh_token and not os.getenv("K_SERVICE"):
        return SETTINGS.refresh_token
    if ss.get("refresh_token"):
        return ss.refresh_token
    params = st.query_params
    if "code" in params:
        if not auth.check_state(params.get("state", ""), SETTINGS):
            st.error("Connexion expirée ou invalide. Recommence.")
        else:
            try:
                ss.refresh_token = auth.exchange_code(params["code"], SETTINGS)
                st.query_params.clear()
                st.rerun()
            except Exception as err:  # noqa: BLE001
                st.error(f"Échec de la connexion Google : {err}")
        st.query_params.clear()
    st.info("Connecte-toi avec le compte Google qui a accès au MCC.")
    st.link_button("Se connecter avec Google", auth.login_url(SETTINGS), type="primary")
    st.stop()


def get_reader(refresh_token: str):
    if SETTINGS.demo_mode:
        return None
    if "reader" not in ss:
        from core.ads import AdsReader

        ss.reader = AdsReader(SETTINGS, refresh_token)
    return ss.reader


def api_error(prefix: str, err: Exception) -> None:
    details = []
    failure = getattr(err, "failure", None)
    if failure is not None:
        details = [e.message for e in failure.errors]
    st.error(f"{prefix} : {'; '.join(details) if details else err}")


def reset_analysis(keep_data: bool = False) -> None:
    for k in ("evals", "human", "batch", "batch_evals", "pairs", "editing", "finished"):
        ss.pop(k, None)
    if not keep_data:
        for k in ("data", "raw_terms", "terms"):
            ss.pop(k, None)


# ---------------------------------------------------------------- étape 1 : contexte


def _lines(text: str) -> list[str]:
    return [l.strip() for l in text.splitlines() if l.strip()]


def section_context(profile, scorer) -> None:
    with st.expander("1. Contexte client", expanded=not profile.context_ready()):
        c1, c2 = st.columns([4, 1])
        url = c1.text_input("Site du client", value=profile.homepage_url, placeholder="https://...")
        c2.write("")
        c2.write("")
        if c2.button("Analyser le site", disabled=not url.startswith("http")):
            try:
                with st.spinner("Lecture du site et rédaction du résumé..."):
                    if SETTINGS.demo_mode:
                        summary = scorer.summarize("")
                    else:
                        from core.llm import fetch_page_text

                        summary = scorer.summarize(fetch_page_text(url))
                profile.homepage_url = url
                profile.company_pitch = summary
                ss.pitch_input = summary
            except Exception as err:  # noqa: BLE001
                st.error(f"Impossible d'analyser le site : {err}. Renseigne le contexte à la main.")

        ss.setdefault("pitch_input", profile.company_pitch)
        pitch = st.text_area(
            "✅ Ce que vend le client (résumé, corrige-le librement)", key="pitch_input", height=140
        )
        exclude = st.text_area(
            "❌ Ce qu'on ne veut pas cibler",
            value=profile.exclude_pitch,
            placeholder="Ex. particuliers, occasion, emploi, SAV, produits non vendus...",
            height=80,
        )

        st.markdown("**Objectif et seuils de performance**")
        t = profile.thresholds
        k1, k2, k3 = st.columns(3)
        kpi = choose(k1.radio, "Indicateur de pilotage", KPI_LABELS, t.kpi_mode, horizontal=True)
        target_cpa = k2.number_input("CPA cible (€)", 0.1, 100000.0, float(t.target_cpa), disabled=kpi != KpiMode.CPA)
        target_roas = k3.number_input(
            "ROAS cible (4 = 400 %)", 0.1, 1000.0, float(t.target_roas), disabled=kpi != KpiMode.ROAS
        )
        e1, e2, e3 = st.columns(3)
        min_clicks = e1.number_input("Exclure dès X clics sans conversion", 1, 100000, int(t.min_clicks_no_conv))
        max_cost = e2.number_input("...ou dès X € dépensés sans conversion", 0.1, 1000000.0, float(t.max_cost_no_conv))
        bad_ratio = e3.number_input(
            "Mauvais si X fois pire que la cible", 1.0, 100.0, float(t.bad_ratio), step=0.5,
            help="CPA supérieur à cible x X, ou ROAS inférieur à cible / X (et dépense au-dessus du seuil ci-contre).",
        )
        a1, a2, _ = st.columns(3)
        min_conv = a1.number_input("Acheter à partir de X conversions", 0.1, 100000.0, float(t.min_conv_add), step=1.0)
        good_ratio = a2.number_input(
            "...avec une tolérance de X fois la cible", 0.1, 100.0, float(t.good_ratio), step=0.1,
            help="1 = au moins aussi bon que la cible. 1,2 = jusqu'à 20 % moins bon accepté.",
        )

        st.markdown("**Marques**")
        b1, b2 = st.columns(2)
        competitors = b1.text_area("Marques concurrentes (une par ligne)", "\n".join(profile.competitor_brands), height=110)
        comp_policy = choose(
            b1.selectbox, "Consigne marques concurrentes", POLICY_LABELS, profile.competitor_policy
        )
        own = b2.text_area("Marque(s) du client (une par ligne)", "\n".join(profile.own_brands), height=110)
        own_policy = choose(
            b2.selectbox, "Consigne marque du client", POLICY_LABELS, profile.own_brand_policy
        )

        st.caption(f"{len(profile.examples)} décision(s) mémorisée(s) pour ce client.")
        if st.button("Enregistrer le contexte", type="primary"):
            if not pitch.strip():
                st.error("Décris au moins ce que vend le client.")
                return
            profile.homepage_url = url
            profile.company_pitch = pitch
            profile.exclude_pitch = exclude
            profile.thresholds = t.model_copy(update=dict(
                kpi_mode=kpi, target_cpa=target_cpa, target_roas=target_roas,
                min_clicks_no_conv=min_clicks, max_cost_no_conv=max_cost, bad_ratio=bad_ratio,
                min_conv_add=min_conv, good_ratio=good_ratio,
            ))
            profile.competitor_brands = _lines(competitors)
            profile.competitor_policy = comp_policy
            profile.own_brands = _lines(own)
            profile.own_brand_policy = own_policy
            get_store().save(profile)
            if "raw_terms" in ss:  # seuils ou marques modifiés : on recalcule
                ss.terms = rules.enrich(ss.raw_terms, profile)
                reset_analysis(keep_data=True)
            st.rerun()


# ---------------------------------------------------------------- étape 2 : données


def section_data(profile, reader) -> None:
    with st.expander("2. Termes de recherche", expanded="terms" not in ss):
        today = dt.date.today()
        c1, c2, c3, c4 = st.columns([2, 3, 1, 1])
        period = c1.date_input(
            "Période", (today - dt.timedelta(days=30), today - dt.timedelta(days=1)), max_value=today
        )
        picked = c2.multiselect(
            "Types de campagnes", list(CHANNEL_LABELS.values()), default=list(CHANNEL_LABELS.values())
        )
        channels = [k for k, v in CHANNEL_LABELS.items() if v in picked]
        min_impr = c3.number_input("Impressions min.", 0, 1000000, 10)
        max_terms = c4.number_input(
            "Termes max.", 10, 100000, 2000, step=500,
            help="Les termes sont gardés par coût décroissant. Limite le temps et le coût d'analyse.",
        )
        if st.button("Charger les termes de recherche", type="primary"):
            if len(period) != 2 or not channels:
                st.error("Choisis une période complète et au moins un type de campagne.")
                return
            try:
                with st.spinner("Lecture du compte Google Ads..."):
                    if SETTINGS.demo_mode:
                        data = demo.demo_account()
                        data.terms = data.terms[data.terms["channel"].isin(channels)]
                    else:
                        data = reader.load_account(
                            profile.customer_id, period[0], period[1], channels, min_impr, profile.name
                        )
            except Exception as err:  # noqa: BLE001
                api_error("Lecture du compte impossible", err)
                return
            data.terms = (
                data.terms.sort_values("cost", ascending=False).head(int(max_terms)).reset_index(drop=True)
            )
            reset_analysis()
            ss.data = data
            ss.raw_terms = data.terms
            ss.terms = rules.enrich(data.terms, profile)
            st.rerun()

        if "terms" in ss:
            for w in ss.data.warnings:
                st.warning(w)
            terms = ss.terms
            if terms.empty:
                st.warning("Aucun terme de recherche sur ce périmètre.")
                return
            m = st.columns(5)
            m[0].metric("Termes", f"{len(terms):,}".replace(",", " "))
            m[1].metric("Coût", f"{terms['cost'].sum():,.0f} €".replace(",", " "))
            counts = terms["perf"].value_counts()
            m[2].metric("🟢 Bonne perf.", int(counts.get("GOOD", 0)))
            m[3].metric("🔴 Mauvaise perf.", int(counts.get("BAD", 0)))
            m[4].metric("⚪ Insuffisant", int(counts.get("NEUTRAL", 0)))


# ---------------------------------------------------------------- étape 3 : entraînement


def _row(key: str):
    return next(ss.terms[ss.terms["key"] == key].itertuples())


def _remember(profile, ev: Evaluation, agreed: bool) -> None:
    ss.evals[ev.key] = ev
    ss.human.add(ev.key)
    ss.pairs[-1].append(agreed)
    profile.add_example(scoring.example_from(ev, _row(ev.key)))
    get_store().save(profile)
    ss.editing = None


def cb_agree(profile, key: str) -> None:
    ev = ss.batch_evals[key].model_copy(update={"source": "IA validée"})
    _remember(profile, ev, True)


def cb_edit(key: str | None) -> None:
    ss.editing = key


def cb_save(profile, key: str) -> None:
    row = _row(key)
    llm_ev = ss.batch_evals[key]
    wanted = from_label(EDIT_LABELS, ss[f"dec_{key}"], Decision.IGNORE)
    ev = Evaluation(
        key=key, term=row.term,
        decision=wanted, reason=ss[f"rea_{key}"].strip() or "Décision du gestionnaire.",
        priority=max(llm_ev.priority, 3),
        match_type=from_label(MATCH_LABELS, ss.get(f"mat_{key}"), MatchType.EXACT),
        level=from_label(LEVEL_LABELS, ss.get(f"lev_{key}"), Level.CAMPAIGN),
        negative_text=ss.get(f"neg_{key}", ""),
        target_adgroup=ss.get(f"tgt_{key}", ""),
        source="Humain",
    )
    groups = list(ss.data.adgroups.get(str(row.campaign_id), {}))
    ev = rules.apply_guardrails(ev, row, ss.data.exact_keywords, groups, strict=False)
    if ev.decision != wanted:
        ss.flash = f"« {row.term} » ne peut pas être acheté : {ev.reason.split('[')[-1].rstrip(']')}"
    _remember(profile, ev, ev.decision == llm_ev.decision)


def _proposal(ev: Evaluation) -> str:
    if ev.decision == Decision.ADD:
        target = "nouveau groupe à créer" if ev.target_adgroup == NEW_ADGROUP else ev.target_adgroup
        return f"➕ Mot clé **{MATCH_LABELS[ev.match_type].lower()}** dans : **{target}**"
    if ev.decision == Decision.EXCLUDE:
        level = "groupe d'annonces" if ev.level == Level.ADGROUP else "campagne"
        return f"⛔ Exclure **{ev.negative_text}** en **{MATCH_LABELS[ev.match_type].lower()}**, niveau **{level}**"
    return ""


def render_card(profile, ev: Evaluation) -> None:
    row = _row(ev.key)
    key = ev.key
    with st.container(border=True):
        st.markdown(f"#### {ev.term}")
        where = row.campaign_name + (f" › {row.adgroup_name}" if row.adgroup_name else "")
        st.caption(f"{CHANNEL_LABELS.get(row.channel, row.channel)} · {where}")
        st.markdown(
            f"{PERF_BADGE[row.perf]} · {int(row.clicks)} clics · {row.cost:.2f} € · "
            f"{row.conversions:g} conv. · {row.conv_value:.0f} € de valeur  \n_{row.perf_reason}_"
        )
        if _proposal(ev):
            st.markdown(_proposal(ev))
        st.markdown(f"🤖 _{ev.reason or 'Sans explication'}_ ({ev.source}, priorité {ev.priority})")

        if ss.get("editing") != key:
            b1, b2, _ = st.columns([1, 1, 2])
            b1.button("✅ D'accord", key=f"ok_{key}", on_click=cb_agree, args=(profile, key))
            b2.button("❌ Corriger", key=f"ko_{key}", on_click=cb_edit, args=(key,))
            return

        decision = choose(
            st.selectbox, "Ta décision", EDIT_LABELS,
            ev.decision if ev.decision in DECISIONS else Decision.IGNORE, key=f"dec_{key}",
        )
        if decision == Decision.ADD:
            groups = list(ss.data.adgroups.get(str(row.campaign_id), {})) + [NEW_ADGROUP]
            default = ev.target_adgroup if ev.target_adgroup in groups else (
                row.adgroup_name if row.adgroup_name in groups else NEW_ADGROUP)
            st.selectbox("Groupe d'annonces cible", groups, index=groups.index(default), key=f"tgt_{key}")
            choose(st.selectbox, "Type de correspondance", MATCH_LABELS, MatchType.EXACT, key=f"mat_{key}")
        elif decision == Decision.EXCLUDE:
            st.text_input("Texte à exclure", value=ev.negative_text or rules.normalize(row.term), key=f"neg_{key}")
            m1, m2 = st.columns(2)
            choose(m1.selectbox, "Type de correspondance", MATCH_LABELS, ev.match_type, key=f"mat_{key}")
            levels = LEVEL_LABELS if row.adgroup_name else {Level.CAMPAIGN: "Campagne"}
            choose(m2.selectbox, "Niveau", levels, ev.level, key=f"lev_{key}")
        st.text_input("Pourquoi ? (l'IA s'en servira pour la suite)", key=f"rea_{key}")
        s1, s2, _ = st.columns([1, 1, 2])
        s1.button("💾 Enregistrer", key=f"sv_{key}", on_click=cb_save, args=(profile, key), type="primary")
        s2.button("Annuler", key=f"cn_{key}", on_click=cb_edit, args=(None,))


def new_batch(profile, scorer) -> None:
    batch = scoring.sample_batch(ss.terms, set(ss.evals), SETTINGS.batch_size, ss.seed)
    ss.seed += 1
    with st.spinner("L'IA analyse un nouveau lot de termes..."):
        ss.batch_evals = scoring.score_rows(batch, profile, ss.data, scorer)
    ss.batch = list(batch["key"])
    ss.pairs.append([])


def score_remaining(profile, scorer) -> None:
    remaining = ss.terms[~ss.terms["key"].isin(set(ss.evals))]
    # Les termes du lot en cours non validés gardent la proposition déjà calculée.
    pending = {k: v for k, v in ss.get("batch_evals", {}).items() if k not in ss.evals}
    remaining = remaining[~remaining["key"].isin(set(pending))]
    bar = st.progress(0.0, text="Analyse des termes restants...")
    evals = scoring.score_rows(
        remaining, profile, ss.data, scorer,
        on_progress=lambda d, t: bar.progress(d / max(t, 1), text=f"Analyse des termes restants... {d}/{t}"),
    )
    ss.evals.update(pending)
    ss.evals.update(evals)
    ss.finished = True


def section_training(profile, scorer) -> None:
    st.header("3. Entraînement")
    ss.setdefault("evals", {})
    ss.setdefault("human", set())
    ss.setdefault("pairs", [])
    ss.setdefault("seed", random.randint(1000, 100000))
    if "batch" not in ss:
        try:
            new_batch(profile, scorer)
        except Exception as err:  # noqa: BLE001
            st.error(f"Analyse IA impossible : {err}")
            st.stop()
    if ss.get("flash"):
        st.warning(ss.pop("flash"))

    pending = [ss.batch_evals[k] for k in ss.batch if k not in ss.human]
    done_pairs = [p for p in ss.pairs if p]
    c1, c2, c3 = st.columns(3)
    if done_pairs:
        last = sum(done_pairs[-1]) / len(done_pairs[-1])
        delta = None
        if len(done_pairs) > 1:
            delta = f"{(last - sum(done_pairs[-2]) / len(done_pairs[-2])) * 100:+.0f} pts"
        c1.metric("Accord IA / toi sur ce lot", f"{last:.0%}", delta)
    c2.metric("Décisions mémorisées pour ce client", len(profile.examples))
    c3.progress(
        (len(ss.batch) - len(pending)) / max(len(ss.batch), 1),
        text=f"Lot en cours : {len(ss.batch) - len(pending)}/{len(ss.batch)}",
    )
    st.info(
        "Valide ou corrige mes propositions. Chaque correction est mémorisée pour ce client "
        "et réutilisée dans les lots suivants.", icon="🎓",
    )

    cols = st.columns(3)
    for col, decision in zip(cols, DECISIONS):
        items = sorted((e for e in pending if e.decision == decision), key=lambda e: -e.priority)
        with col:
            st.subheader(f"{DECISION_LABELS[decision]} ({len(items)})")
            for ev in items:
                render_card(profile, ev)
    unknown = [e for e in pending if e.decision == Decision.UNKNOWN]
    if unknown:
        st.subheader(f"Indécis ({len(unknown)})")
        for ev in unknown:
            render_card(profile, ev)

    remaining = len(ss.terms) - len(ss.evals)
    st.divider()
    b1, b2, b3 = st.columns(3)
    if b1.button("Nouveau lot", disabled=remaining - len(pending) <= 0):
        # Les propositions non traitées du lot sont abandonnées : elles reviendront plus tard.
        try:
            new_batch(profile, scorer)
        except Exception as err:  # noqa: BLE001
            st.error(f"Analyse IA impossible : {err}")
            st.stop()
        st.rerun()
    if b2.button(f"Analyser les {remaining} termes restants", type="primary", disabled=remaining <= 0):
        try:
            score_remaining(profile, scorer)
        except Exception as err:  # noqa: BLE001
            st.error(f"Analyse interrompue : {err}. Les décisions déjà prises sont conservées.")
            st.stop()
        st.rerun()
    if b3.button("Arrêter et voir les résultats"):
        ss.finished = True
        st.rerun()
    st.caption("Aucune de ces actions ne modifie le compte Google Ads.")


# ---------------------------------------------------------------- étape 4 : résultats


def _download(label: str, df: pd.DataFrame, filename: str) -> None:
    st.dataframe(df, height=280, hide_index=True)
    st.download_button(label, export.to_csv(df), file_name=filename, mime="text/csv", disabled=df.empty)


def section_results(profile) -> None:
    st.header("4. Résultats")
    results = export.results_frame(ss.terms, ss.evals)
    if results.empty:
        st.warning("Aucun terme évalué pour l'instant.")
    else:
        add = export.keywords_to_add(results)
        new = export.new_adgroup_suggestions(results)
        neg = export.negatives_to_add(results)
        wasted = results.loc[results["_decision"] == Decision.EXCLUDE, "Coût"].sum()
        m = st.columns(4)
        m[0].metric("Mots clés à acheter", len(add) + len(new))
        m[1].metric("Exclusions à ajouter", len(neg))
        m[2].metric("Coût des termes à exclure", f"{wasted:,.0f} €".replace(",", " "))
        m[3].metric("Termes évalués", f"{len(results)}/{len(ss.terms)}")
        st.caption(
            "Fichiers au format Google Ads Editor (Compte > Importer > Depuis un fichier). "
            "Les colonnes Raison, Priorité et Source sont à ignorer à l'import."
        )
        slug = profile.customer_id
        t1, t2, t3, t4 = st.tabs([
            f"➕ À acheter ({len(add)})", f"🆕 Groupes à créer ({len(new)})",
            f"⛔ À exclure ({len(neg)})", f"Tout ({len(results)})",
        ])
        with t1:
            _download("Télécharger les mots clés à ajouter", add, f"mots_cles_a_ajouter_{slug}.csv")
        with t2:
            st.caption("Termes rentables sans groupe d'annonces adapté : à ranger toi-même.")
            _download("Télécharger les suggestions", new, f"groupes_a_creer_{slug}.csv")
        with t3:
            _download("Télécharger les exclusions", neg, f"exclusions_a_ajouter_{slug}.csv")
        with t4:
            _download("Télécharger l'analyse complète", export.public_view(results), f"analyse_complete_{slug}.csv")
    if st.button("Reprendre l'entraînement"):
        ss.finished = False
        ss.pop("batch", None)
        st.rerun()


# ---------------------------------------------------------------- page


def main() -> None:
    st.title("🔎 Search Term Optimizer")
    st.caption("Termes de recherche à acheter en mot clé ou à exclure, par client. Lecture seule.")
    if SETTINGS.demo_mode:
        st.warning("Mode démo : compte fictif et IA simulée. Aucune donnée réelle.", icon="🧪")

    missing = SETTINGS.missing()
    if missing:
        st.error("Configuration incomplète. Variables d'environnement manquantes : " + ", ".join(missing))
        st.markdown("Voir le fichier `.env.example` et le README, section Configuration.")
        st.stop()

    refresh_token = ensure_auth()
    try:
        reader = get_reader(refresh_token)
        if "customers" not in ss:
            ss.customers = demo.DEMO_CUSTOMERS if SETTINGS.demo_mode else reader.list_customers()
    except Exception as err:  # noqa: BLE001
        api_error("Connexion à l'API Google Ads impossible", err)
        st.stop()
    if not ss.customers:
        st.error("Aucun compte actif trouvé sous ce MCC.")
        st.stop()

    customer_id = choose(
        st.sidebar.selectbox, "Client", {c: f"{n} ({c})" for c, n in ss.customers.items()}
    )
    if ss.get("customer_id") != customer_id:  # changement de client : rien ne fuit de l'un à l'autre
        reset_analysis()
        ss.pop("pitch_input", None)
        ss.customer_id = customer_id
        ss.profile = get_store().load(customer_id, ss.customers[customer_id])
        ss.profile.name = ss.customers[customer_id]
    profile = ss.profile
    st.sidebar.caption(f"Modèle : {'simulé' if SETTINGS.demo_mode else SETTINGS.gemini_model}")
    st.sidebar.caption("L'outil ne modifie jamais le compte : il produit des fichiers pour Google Ads Editor.")

    scorer = get_scorer()
    section_context(profile, scorer)
    if not profile.context_ready():
        st.info("Renseigne et enregistre le contexte du client pour continuer.")
        st.stop()
    section_data(profile, reader)
    if "terms" not in ss or ss.terms.empty:
        st.stop()
    if ss.get("finished"):
        section_results(profile)
    else:
        section_training(profile, scorer)


main()
