from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    identifier: str = "mock"  # mock | claude | ebay (eBay image search)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"

    ebay_mode: str = "mock"  # mock | live
    ebay_env: str = "sandbox"  # sandbox | production
    ebay_client_id: str = ""
    ebay_client_secret: str = ""
    ebay_marketplace_id: str = "EBAY_US"
    ebay_user_token: str = ""
    # Seller account setup required for publishing (created once in eBay Seller Hub / Account API)
    ebay_fulfillment_policy_id: str = ""
    ebay_payment_policy_id: str = ""
    ebay_return_policy_id: str = ""
    ebay_merchant_location_key: str = ""

    # Re-price unsold cards this often so the trend chart fills in (live eBay only). 0 turns it off.
    price_refresh_hours: float = 24

    # Sold prices from SoldComps (sold-comps.com). Leave the key empty to switch the feature off.
    soldcomps_api_key: str = ""
    soldcomps_base_url: str = "https://api.sold-comps.com"
    sold_monthly_budget: int = 100  # lookups the app may spend per calendar month (the free tier gives 100)
    sold_refresh_days: int = 7  # how often a card's sold prices are refreshed automatically
    sold_min_value: float = 0  # automatic checks only for cards estimated at or above this
    sold_window_days: int = 90  # how far back sold prices count

    database_url: str = "sqlite:///./data/cardvault.db"
    image_dir: Path = Path("./data/images")
    cors_origins: str = "http://localhost:3000"
    # Public https base URL where /images is reachable (needed for live eBay listings)
    public_base_url: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
