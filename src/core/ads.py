"""Lecture du compte Google Ads (lecture seule, aucune écriture)."""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging

import pandas as pd

from core.config import Settings
from core.models import Channel
from core.rules import normalize

logger = logging.getLogger(__name__)

TERM_COLUMNS = [
    "term", "status", "account_id", "account_name", "campaign_id", "campaign_name",
    "channel", "adgroup_id", "adgroup_name",
    "impressions", "clicks", "cost", "conversions", "conv_value",
]

_Q_CUSTOMERS = """
    SELECT customer_client.id, customer_client.descriptive_name
    FROM customer_client
    WHERE customer_client.manager = FALSE AND customer_client.status = 'ENABLED'
"""

# Search, DSA et Shopping : agrégé par terme et par groupe d'annonces.
_Q_SEARCH_TERMS = """
    SELECT
      search_term_view.search_term, search_term_view.status,
      campaign.id, campaign.name, campaign.advertising_channel_type,
      ad_group.id, ad_group.name, ad_group.type,
      metrics.impressions, metrics.clicks, metrics.cost_micros,
      metrics.conversions, metrics.conversions_value
    FROM search_term_view
    WHERE segments.date BETWEEN '{start}' AND '{end}'
      AND campaign.status = 'ENABLED' AND ad_group.status = 'ENABLED'
      AND campaign.advertising_channel_type IN ({channels})
      AND metrics.impressions >= {min_impressions}
"""

# Performance Max : pas de groupe d'annonces, agrégé par terme et par campagne.
_Q_PMAX_TERMS = """
    SELECT
      campaign_search_term_view.search_term,
      campaign.id, campaign.name,
      metrics.impressions, metrics.clicks, metrics.cost_micros,
      metrics.conversions, metrics.conversions_value
    FROM campaign_search_term_view
    WHERE segments.date BETWEEN '{start}' AND '{end}'
      AND campaign.status = 'ENABLED'
      AND campaign.advertising_channel_type = 'PERFORMANCE_MAX'
      AND metrics.impressions >= {min_impressions}
"""

_Q_KEYWORDS = """
    SELECT
      ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type,
      ad_group.name, campaign.id
    FROM ad_group_criterion
    WHERE ad_group_criterion.type = 'KEYWORD'
      AND ad_group_criterion.negative = FALSE
      AND ad_group_criterion.status = 'ENABLED'
      AND ad_group.status = 'ENABLED' AND campaign.status = 'ENABLED'
      AND campaign.advertising_channel_type = 'SEARCH'
"""

_Q_CAMPAIGN_NEGATIVES = """
    SELECT campaign.id, campaign_criterion.keyword.text
    FROM campaign_criterion
    WHERE campaign_criterion.type = 'KEYWORD'
      AND campaign_criterion.negative = TRUE
      AND campaign.status = 'ENABLED'
"""


@dataclasses.dataclass
class AccountData:
    terms: pd.DataFrame
    # Mots clés exacts déjà achetés (normalisés), pour ne pas les reproposer.
    exact_keywords: set[str]
    # campaign_id -> {nom du groupe d'annonces: [exemples de mots clés]}
    adgroups: dict[str, dict[str, list[str]]]
    warnings: list[str] = dataclasses.field(default_factory=list)


class AdsReader:
    def __init__(self, settings: Settings, refresh_token: str):
        from google.ads.googleads.client import GoogleAdsClient

        self.client = GoogleAdsClient.load_from_dict(
            {
                "developer_token": settings.developer_token,
                "login_customer_id": settings.mcc_id,
                "client_id": settings.oauth_client_id,
                "client_secret": settings.oauth_client_secret,
                "refresh_token": refresh_token,
                "use_proto_plus": True,
            }
        )
        self.mcc_id = settings.mcc_id
        self.service = self.client.get_service("GoogleAdsService")

    def _rows(self, customer_id: str, query: str):
        for batch in self.service.search_stream(customer_id=str(customer_id), query=query):
            yield from batch.results

    def list_customers(self) -> dict[str, str]:
        """Comptes non-MCC actifs sous le MCC : {id: nom}."""
        out = {}
        for r in self._rows(self.mcc_id, _Q_CUSTOMERS):
            cc = r.customer_client
            out[str(cc.id)] = cc.descriptive_name or str(cc.id)
        return dict(sorted(out.items(), key=lambda kv: kv[1].lower()))

    def load_account(
        self,
        customer_id: str,
        start: dt.date,
        end: dt.date,
        channels: list[str],
        min_impressions: int,
        account_name: str = "",
    ) -> AccountData:
        self._account = (str(customer_id), account_name or str(customer_id))
        warnings: list[str] = []
        records: list[dict] = []
        api_channels = []
        if Channel.SEARCH.value in channels or Channel.DSA.value in channels:
            api_channels.append("'SEARCH'")
        if Channel.SHOPPING.value in channels:
            api_channels.append("'SHOPPING'")

        if api_channels:
            q = _Q_SEARCH_TERMS.format(
                start=start, end=end, channels=", ".join(api_channels),
                min_impressions=int(min_impressions),
            )
            for r in self._rows(customer_id, q):
                ch = r.campaign.advertising_channel_type.name
                if ch == "SEARCH" and r.ad_group.type_.name == "SEARCH_DYNAMIC_ADS":
                    ch = Channel.DSA.value
                if ch not in channels:
                    continue
                records.append(
                    self._record(r, r.search_term_view.search_term, ch,
                                 r.search_term_view.status.name,
                                 str(r.ad_group.id), r.ad_group.name)
                )

        if Channel.PMAX.value in channels:
            q = _Q_PMAX_TERMS.format(start=start, end=end, min_impressions=int(min_impressions))
            try:
                for r in self._rows(customer_id, q):
                    records.append(
                        self._record(r, r.campaign_search_term_view.search_term,
                                     Channel.PMAX.value, "NONE", "", "")
                    )
            except Exception as err:  # ressource récente : dépend de la version d'API
                logger.exception("Requête PMax en échec")
                warnings.append(f"Termes Performance Max non récupérés : {err}")

        terms = pd.DataFrame(records, columns=TERM_COLUMNS)

        exact: set[str] = set()
        adgroups: dict[str, dict[str, list[str]]] = {}
        for r in self._rows(customer_id, _Q_KEYWORDS):
            text = r.ad_group_criterion.keyword.text
            if r.ad_group_criterion.keyword.match_type.name == "EXACT":
                exact.add(normalize(text))
            samples = adgroups.setdefault(str(r.campaign.id), {}).setdefault(r.ad_group.name, [])
            if len(samples) < 5:
                samples.append(text)

        negatives: dict[str, set[str]] = {}
        for r in self._rows(customer_id, _Q_CAMPAIGN_NEGATIVES):
            negatives.setdefault(str(r.campaign.id), set()).add(
                normalize(r.campaign_criterion.keyword.text)
            )

        return AccountData(clean_terms(terms, negatives), exact, adgroups, warnings)

    def _record(self, r, term, channel, status, adgroup_id, adgroup_name) -> dict:
        m = r.metrics
        return {
            "term": term, "status": status,
            "account_id": self._account[0], "account_name": self._account[1],
            "campaign_id": str(r.campaign.id), "campaign_name": r.campaign.name,
            "channel": channel, "adgroup_id": adgroup_id, "adgroup_name": adgroup_name,
            "impressions": m.impressions, "clicks": m.clicks,
            "cost": m.cost_micros / 1_000_000,
            "conversions": m.conversions, "conv_value": m.conversions_value,
        }


def clean_terms(terms: pd.DataFrame, negatives: dict[str, set[str]]) -> pd.DataFrame:
    """Retire les termes déjà exclus (statut, ou exclusion campagne identique)."""
    if terms.empty:
        return terms
    in_negatives = pd.Series(
        [
            normalize(t) in negatives.get(str(c), set())
            for t, c in zip(terms["term"], terms["campaign_id"])
        ],
        index=terms.index,
    )
    already = terms["status"].isin(["EXCLUDED"]) | in_negatives
    return terms[~already].reset_index(drop=True)
