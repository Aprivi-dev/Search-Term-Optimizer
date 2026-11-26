"""Configuration lue dans les variables d'environnement (ou un fichier .env)."""

from __future__ import annotations

import dataclasses
import os

import dotenv

dotenv.load_dotenv(dotenv.find_dotenv(usecwd=True))


@dataclasses.dataclass(frozen=True)
class Settings:
    developer_token: str = os.getenv("GOOGLE_ADS_API_TOKEN", "")
    mcc_id: str = os.getenv("MCC_ID", "").replace("-", "")
    oauth_client_id: str = os.getenv("OAUTH_CLIENT_ID", "")
    oauth_client_secret: str = os.getenv("OAUTH_CLIENT_SECRET", "")
    oauth_redirect_uri: str = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8080")
    # Facultatif : évite la connexion Google à chaque lancement en local.
    refresh_token: str = os.getenv("GOOGLE_ADS_REFRESH_TOKEN", "")
    gemini_api_key: str = os.getenv("GOOGLE_API_KEY", "") or os.getenv(
        "GOOGLE_VERTEXAI_API_KEY", ""
    )
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    batch_size: int = int(os.getenv("BATCH_SIZE", "15"))
    demo_mode: bool = os.getenv("DEMO_MODE", "") == "1"

    def missing(self) -> list[str]:
        """Variables indispensables absentes."""
        if self.demo_mode:
            return []
        required = {
            "GOOGLE_ADS_API_TOKEN": self.developer_token,
            "MCC_ID": self.mcc_id,
            "OAUTH_CLIENT_ID": self.oauth_client_id,
            "OAUTH_CLIENT_SECRET": self.oauth_client_secret,
            "GOOGLE_API_KEY": self.gemini_api_key,
        }
        return [k for k, v in required.items() if not v]
