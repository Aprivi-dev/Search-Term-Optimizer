"""Orchestration du scoring : règles -> IA -> garde-fous. Sans dépendance à l'interface."""

from __future__ import annotations

from typing import Callable, Optional, Protocol

import pandas as pd

from core import rules
from core.ads import AccountData
from core.models import ClientProfile, Decision, Evaluation, Example, LlmEvaluation

LLM_CHUNK = 50


class Scorer(Protocol):
    def score(self, profile: ClientProfile, rows: pd.DataFrame, adgroups) -> list[LlmEvaluation]: ...


def score_rows(
    rows: pd.DataFrame,
    profile: ClientProfile,
    data: AccountData,
    scorer: Scorer,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> dict[str, Evaluation]:
    """Évalue chaque ligne. Renvoie {clé du terme: Evaluation}."""
    evals: dict[str, Evaluation] = {}
    to_llm = []
    for r in rows.itertuples():
        pre = rules.pre_score(r, profile)
        if pre is not None:
            evals[r.key] = pre
        else:
            to_llm.append(r.Index)

    total = len(rows)
    done = len(evals)
    if on_progress:
        on_progress(done, total)

    for start in range(0, len(to_llm), LLM_CHUNK):
        chunk = rows.loc[to_llm[start : start + LLM_CHUNK]].reset_index(drop=True)
        answers = {a.id: a for a in scorer.score(profile, chunk, data.adgroups)}
        for i, r in enumerate(chunk.itertuples()):
            a = answers.get(i)
            if a is None:
                evals[r.key] = Evaluation(
                    key=r.key, term=r.term, decision=Decision.UNKNOWN,
                    reason="Le modèle n'a pas rendu de décision pour ce terme.",
                )
                continue
            ev = Evaluation(
                key=r.key, term=r.term, decision=a.decision, reason=a.reason,
                priority=a.priority, match_type=a.match_type, level=a.level,
                negative_text=a.negative_text, target_adgroup=a.target_adgroup,
            )
            groups = list(data.adgroups.get(str(r.campaign_id), {}))
            evals[r.key] = rules.apply_guardrails(ev, r, data.exact_keywords, groups)
        done += len(chunk)
        if on_progress:
            on_progress(done, total)
    return evals


def example_from(ev: Evaluation, row) -> Example:
    """Transforme une décision humaine en exemple pour les prochains prompts."""
    return Example(
        term=ev.term, campaign=row.campaign_name, channel=row.channel, perf=row.perf,
        decision=ev.decision, reason=ev.reason, match_type=ev.match_type,
        negative_text=ev.negative_text,
    )


def sample_batch(
    terms: pd.DataFrame, exclude: set[str], batch_size: int, seed: int
) -> pd.DataFrame:
    """Lot d'entraînement : moitié des termes les plus coûteux, moitié au hasard.

    Les gros postes de dépense sont vus en premier, le hasard garde de la variété.
    """
    pool = terms[~terms["key"].isin(exclude)]
    if len(pool) <= batch_size:
        return pool
    top = pool.sort_values("cost", ascending=False).head(batch_size // 2)
    rest = pool.drop(top.index)
    rnd = rest.sample(min(len(rest), batch_size - len(top)), random_state=seed)
    return pd.concat([top, rnd])
