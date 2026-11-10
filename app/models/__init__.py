"""
Models package — exports all ORM models so they are discoverable by:
  - Alembic autogenerate
  - SQLAlchemy relationship resolution

Import order matters: models that are referenced by others must be
imported before the models that reference them, so SQLAlchemy can
resolve the relationship strings.
"""
from app.models.base import Base, TimestampMixin, UUIDMixin  # noqa: F401
from app.models.supplier import Supplier  # noqa: F401
from app.models.product import Product  # noqa: F401
from app.models.product_variant import ProductVariant  # noqa: F401
from app.models.product_alias import ProductAlias, AliasSource  # noqa: F401
from app.models.alias_observation import AliasObservation  # noqa: F401
from app.models.receipt import Receipt, ReceiptStatus  # noqa: F401
from app.models.purchase_item import PurchaseItem, ResolutionMethod  # noqa: F401
from app.models.user import User  # noqa: F401

__all__ = [
    "Base",
    "TimestampMixin",
    "UUIDMixin",
    "Supplier",
    "Product",
    "ProductVariant",
    "ProductAlias",
    "AliasSource",
    "AliasObservation",
    "Receipt",
    "ReceiptStatus",
    "PurchaseItem",
    "ResolutionMethod",
    "User",
]
