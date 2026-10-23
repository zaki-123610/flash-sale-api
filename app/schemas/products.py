from pydantic import BaseModel, computed_field


class ProductOut(BaseModel):
    id: int
    name: str
    description: str
    price_cents: int
    stock: int

    @computed_field
    @property
    def sold_out(self) -> bool:
        return self.stock == 0
