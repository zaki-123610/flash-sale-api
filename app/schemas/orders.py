from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings


class OrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    product_id: int = Field(gt=0)
    quantity: int = Field(ge=1, le=settings.max_quantity_per_order)


class OrderOut(BaseModel):
    id: int
    product_id: int
    product_name: str
    quantity: int
    unit_price_cents: int
    total_cents: int
    created_at: datetime
