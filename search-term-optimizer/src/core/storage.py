"""Mémoire par client : un fichier JSON par compte, en local ou sur GCS.

Les clients sont cloisonnés : contexte, seuils, marques et corrections
ne sont jamais partagés d'un compte à l'autre.
"""

from __future__ import annotations

import logging
import os
import pathlib
import re

from core.models import ClientProfile

logger = logging.getLogger(__name__)



def _local_dir() -> pathlib.Path:
    return pathlib.Path(os.getenv("PROFILES_DIR", "data/clients"))

_GCS_PREFIX = "search_term_optimizer/clients"


def _safe_id(customer_id: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]", "", str(customer_id))


class ProfileStore:
    def __init__(self, bucket_name: str | None = None):
        self.bucket = None
        bucket_name = bucket_name or os.getenv("DEFAULT_BUCKET_NAME")
        if bucket_name:
            from google.cloud import storage  # import tardif : inutile en local

            self.bucket = storage.Client().bucket(bucket_name)

    def load(self, customer_id: str, name: str = "") -> ClientProfile:
        cid = _safe_id(customer_id)
        raw = None
        try:
            if self.bucket:
                blob = self.bucket.blob(f"{_GCS_PREFIX}/{cid}.json")
                raw = blob.download_as_text() if blob.exists() else None
            else:
                path = _local_dir() / f"{cid}.json"
                raw = path.read_text(encoding="utf-8") if path.exists() else None
        except Exception:  # stockage indisponible : on repart d'un profil vide
            logger.exception("Lecture du profil %s impossible", cid)
        if raw:
            try:
                return ClientProfile.model_validate_json(raw)
            except ValueError:
                logger.exception("Profil %s illisible, recréation", cid)
        return ClientProfile(customer_id=cid, name=name)

    def save(self, profile: ClientProfile) -> None:
        cid = _safe_id(profile.customer_id)
        raw = profile.model_dump_json(indent=2)
        if self.bucket:
            self.bucket.blob(f"{_GCS_PREFIX}/{cid}.json").upload_from_string(
                raw, content_type="application/json"
            )
        else:
            _local_dir().mkdir(parents=True, exist_ok=True)
            (_local_dir() / f"{cid}.json").write_text(raw, encoding="utf-8")
