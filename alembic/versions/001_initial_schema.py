"""Initial schema — all core tables with pgvector support.

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-09-30

Tables created:
  suppliers
  products           (with name_embedding vector)
  product_variants
  product_aliases
  alias_observations
  receipts           (with content_embedding vector)
  purchase_items

Enums created:
  alias_source_enum
  receipt_status_enum
  resolution_method_enum
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers used by Alembic
revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── pgvector extension ─────────────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ── Enums ──────────────────────────────────────────────────────────────────
    alias_source_enum = postgresql.ENUM(
        "manual", "auto_learned", "extracted",
        name="alias_source_enum",
        create_type=False,
    )
    receipt_status_enum = postgresql.ENUM(
        "pending", "processing", "validated", "stored", "failed", "rejected",
        name="receipt_status_enum",
        create_type=False,
    )
    resolution_method_enum = postgresql.ENUM(
        "unresolved", "exact_alias", "normalized", "fuzzy", "vector", "llm", "manual",
        name="resolution_method_enum",
        create_type=False,
    )

    alias_source_enum.create(op.get_bind(), checkfirst=True)
    receipt_status_enum.create(op.get_bind(), checkfirst=True)
    resolution_method_enum.create(op.get_bind(), checkfirst=True)

    # ── suppliers ──────────────────────────────────────────────────────────────
    op.create_table(
        "suppliers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column("contact_info", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_suppliers_name", "suppliers", ["name"], unique=True)
    op.create_index("ix_suppliers_normalized_name", "suppliers", ["normalized_name"])

    # ── products ───────────────────────────────────────────────────────────────
    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("canonical_name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        # pgvector column — 768-dim for Google text-embedding-004
        sa.Column("name_embedding", sa.Text, nullable=True),  # raw DDL below
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_products_canonical_name", "products", ["canonical_name"], unique=True)
    op.create_index("ix_products_normalized_name", "products", ["normalized_name"])
    op.create_index("ix_products_category", "products", ["category"])

    # Add the actual vector column via raw SQL (Alembic doesn't know the type natively)
    op.execute("ALTER TABLE products DROP COLUMN name_embedding")
    op.execute("ALTER TABLE products ADD COLUMN name_embedding vector(768)")

    # IVFFlat index for approximate nearest-neighbour search (created after data load)
    # op.execute(
    #   "CREATE INDEX ix_products_name_embedding ON products "
    #   "USING ivfflat (name_embedding vector_cosine_ops) WITH (lists = 100)"
    # )

    # ── product_variants ───────────────────────────────────────────────────────
    op.create_table(
        "product_variants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pack_size", sa.Numeric(10, 3), nullable=True),
        sa.Column("pack_unit", sa.String(50), nullable=True),
        sa.Column("purchase_unit", sa.String(50), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_product_variants_product_id", "product_variants", ["product_id"])

    # ── product_aliases ────────────────────────────────────────────────────────
    op.create_table(
        "product_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "supplier_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("suppliers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("alias", sa.String(255), nullable=False),
        sa.Column("normalized_alias", sa.String(255), nullable=False),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("is_confirmed", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "source",
            postgresql.ENUM(
                "manual", "auto_learned", "extracted",
                name="alias_source_enum",
                create_type=False,
            ),
            nullable=False,
            server_default="extracted",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_product_aliases_product_id", "product_aliases", ["product_id"])
    op.create_index("ix_product_aliases_supplier_id", "product_aliases", ["supplier_id"])
    op.create_index("ix_product_aliases_normalized_alias", "product_aliases", ["normalized_alias"])
    op.create_unique_constraint(
        "uq_alias_normalized_supplier",
        "product_aliases",
        ["normalized_alias", "supplier_id"],
    )

    # ── alias_observations ─────────────────────────────────────────────────────
    op.create_table(
        "alias_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("raw_name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column(
            "supplier_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("suppliers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "resolved_product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("occurrence_count", sa.Integer, nullable=False, server_default="1"),
        sa.Column("resolution_confidence", sa.Float, nullable=True),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_seen_unit", sa.String(50), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_alias_observations_raw_name", "alias_observations", ["raw_name"])
    op.create_index("ix_alias_observations_normalized_name", "alias_observations", ["normalized_name"])
    op.create_index("ix_alias_observations_supplier_id", "alias_observations", ["supplier_id"])
    op.create_index("ix_alias_observations_resolved_product_id", "alias_observations", ["resolved_product_id"])

    # ── receipts ───────────────────────────────────────────────────────────────
    op.create_table(
        "receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "supplier_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("suppliers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("supplier_raw_name", sa.String(255), nullable=True),
        sa.Column("receipt_date", sa.Date, nullable=True),
        sa.Column("invoice_number", sa.String(100), nullable=True),
        sa.Column("receipt_total", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(10), nullable=False, server_default="INR"),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending", "processing", "validated", "stored", "failed", "rejected",
                name="receipt_status_enum",
                create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("extraction_confidence", sa.Float, nullable=True),
        sa.Column("classifier_confidence", sa.Float, nullable=True),
        sa.Column("image_path", sa.String(500), nullable=True),
        sa.Column("raw_extraction", postgresql.JSONB, nullable=True),
        sa.Column("validation_notes", sa.Text, nullable=True),
        sa.Column("content_embedding", sa.Text, nullable=True),  # replaced below
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_receipts_supplier_id", "receipts", ["supplier_id"])
    op.create_index("ix_receipts_receipt_date", "receipts", ["receipt_date"])
    op.create_index("ix_receipts_status", "receipts", ["status"])

    op.execute("ALTER TABLE receipts DROP COLUMN content_embedding")
    op.execute("ALTER TABLE receipts ADD COLUMN content_embedding vector(768)")

    # ── purchase_items ─────────────────────────────────────────────────────────
    op.create_table(
        "purchase_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "receipt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("receipts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "product_variant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_variants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("raw_name", sa.String(255), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit", sa.String(50), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("line_total", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(10), nullable=False, server_default="INR"),
        sa.Column(
            "resolution_method",
            postgresql.ENUM(
                "unresolved", "exact_alias", "normalized", "fuzzy", "vector", "llm", "manual",
                name="resolution_method_enum",
                create_type=False,
            ),
            nullable=False,
            server_default="unresolved",
        ),
        sa.Column("resolution_confidence", sa.Float, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_purchase_items_receipt_id", "purchase_items", ["receipt_id"])
    op.create_index("ix_purchase_items_product_id", "purchase_items", ["product_id"])
    op.create_index("ix_purchase_items_product_variant_id", "purchase_items", ["product_variant_id"])

    # ── Analytics indexes ──────────────────────────────────────────────────────
    # Composite index to accelerate monthly aggregation queries:
    # "How much rice did I buy in September?"
    op.create_index(
        "ix_purchase_items_product_date",
        "purchase_items",
        ["product_id"],
        postgresql_using="btree",
    )
    # Partial index: fast lookup of unresolved items for the resolver job
    op.execute(
        "CREATE INDEX ix_purchase_items_unresolved ON purchase_items (id) "
        "WHERE resolution_method = 'unresolved'"
    )


def downgrade() -> None:
    op.drop_table("purchase_items")
    op.drop_table("receipts")
    op.drop_table("alias_observations")
    op.drop_table("product_aliases")
    op.drop_table("product_variants")
    op.drop_table("products")
    op.drop_table("suppliers")

    op.execute("DROP TYPE IF EXISTS resolution_method_enum")
    op.execute("DROP TYPE IF EXISTS receipt_status_enum")
    op.execute("DROP TYPE IF EXISTS alias_source_enum")
    op.execute("DROP EXTENSION IF EXISTS vector")
