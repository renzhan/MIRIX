import datetime as dt
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ARRAY, JSON, Column, String
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from mirix.constants import MAX_EMBEDDING_DIM
from mirix.orm.custom_columns import CommonVector, EmbeddingConfigColumn
from mirix.orm.mixins import OrganizationMixin, UserMixin
from mirix.orm.sqlalchemy_base import SqlalchemyBase
from mirix.schemas.procedural_memory import (
    ProceduralMemoryItem as PydanticProceduralMemoryItem,
)
from mirix.settings import settings

if TYPE_CHECKING:
    from mirix.orm.organization import Organization
    from mirix.orm.user import User


class ProceduralMemoryItem(SqlalchemyBase, OrganizationMixin, UserMixin):
    """
    Stores procedural memory entries, such as workflows, step-by-step guides, or how-to knowledge.

    type:        The category or tag of the procedure (e.g. 'workflow', 'guide', 'script')
    description: Short descriptive text about what this procedure accomplishes
    steps:       Step-by-step instructions or method
    metadata_:   Additional fields/notes
    """

    __tablename__ = "procedural_memory"
    __pydantic_model__ = PydanticProceduralMemoryItem

    # Primary key
    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        doc="Unique ID for this procedural memory entry",
    )

    # Distinguish the type/category of the procedure
    entry_type: Mapped[str] = mapped_column(
        String, doc="Category or type (e.g. 'workflow', 'guide', 'script')"
    )

    # A human-friendly description of this procedure
    summary: Mapped[str] = mapped_column(
        String, doc="Short description or title of the procedure"
    )

    # Steps or instructions stored as a JSON object/list
    steps: Mapped[list] = mapped_column(
        JSON, doc="Step-by-step instructions stored as a list of strings"
    )

    # Hierarchical categorization path
    tree_path: Mapped[list] = mapped_column(
        JSON,
        default=list,
        nullable=False,
        doc="Hierarchical categorization path as an array of strings",
    )

    # When was this item last modified and what operation?
    last_modify: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=lambda: {
            "timestamp": datetime.now(dt.timezone.utc).isoformat(),
            "operation": "created",
        },
        doc="Last modification info including timestamp and operation type",
    )

    # Optional metadata
    metadata_: Mapped[dict] = mapped_column(
        JSON,
        default={},
        nullable=True,
        doc="Arbitrary additional metadata as a JSON object",
    )

    # Email tags for categorization
    email_tag: Mapped[list] = mapped_column(
        ARRAY(String),
        default=list,
        nullable=True,
        doc="Array of email-related tags for categorization",
    )

    # Flow tags for workflow categorization
    flow_tag: Mapped[list] = mapped_column(
        ARRAY(String),
        default=list,
        nullable=True,
        doc="Array of workflow/flow-related tags for categorization",
    )

    embedding_config: Mapped[Optional[dict]] = mapped_column(
        EmbeddingConfigColumn, nullable=True, doc="Embedding configuration"
    )

    # Vector embedding field based on database type
    if settings.mirix_pg_uri_no_default:
        from pgvector.sqlalchemy import Vector

        summary_embedding = mapped_column(Vector(MAX_EMBEDDING_DIM), nullable=True)
        steps_embedding = mapped_column(Vector(MAX_EMBEDDING_DIM), nullable=True)
    else:
        summary_embedding = Column(CommonVector, nullable=True)
        steps_embedding = Column(CommonVector, nullable=True)

    @declared_attr
    def organization(cls) -> Mapped["Organization"]:
        """
        Relationship to organization (mirroring your existing patterns).
        Adjust 'back_populates' to match the collection name in your `Organization` model.
        """
        return relationship(
            "Organization", back_populates="procedural_memory", lazy="selectin"
        )

    @declared_attr
    def user(cls) -> Mapped["User"]:
        """
        Relationship to the User that owns this procedural memory item.
        """
        return relationship("User", lazy="selectin")
