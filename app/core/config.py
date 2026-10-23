from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=60, gt=0)
    bcrypt_rounds: int = Field(default=12, ge=4, le=16)
    db_pool_min_size: int = Field(default=1, ge=0)
    db_pool_max_size: int = Field(default=10, ge=1)
    max_quantity_per_order: int = Field(default=5, ge=1)


settings = Settings()
