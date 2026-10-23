from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.core.errors import register_exception_handlers
from app.db.pool import DbPool, create_pool
from app.db.schema import init_db
from app.routers import auth, orders, products


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = create_pool()
    pool.open(wait=True, timeout=30)
    try:
        init_db(pool)
        app.state.pool = pool
        yield
    finally:
        pool.close()


app = FastAPI(
    title="Flash Sale API",
    version="1.0.0",
    description="Limited-stock flash sale backend that never oversells and never double-charges a retry.",
    lifespan=lifespan,
)
register_exception_handlers(app)

app.include_router(auth.router)
app.include_router(products.router)
app.include_router(orders.router)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs")


@app.get("/health", tags=["health"])
def health(pool: DbPool):
    with pool.connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}
