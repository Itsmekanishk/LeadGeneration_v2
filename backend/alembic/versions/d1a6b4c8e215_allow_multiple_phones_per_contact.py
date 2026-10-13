"""allow multiple distinct phone contacts per business

Revision ID: d1a6b4c8e215
Revises: c9f5a3e71b02
Create Date: 2026-10-12

``uq_contact_email_per_business`` keyed contact identity on
``(business_id, email)``. Every phone-only row stores ``email = ''``, so a lead
could hold at most one such row and a second distinct number found on the
website was silently discarded. The constraint becomes the contact's full
identity — ``(business_id, email, phone_normalized)`` — so each distinct phone
is retained while exact duplicates are still rejected.

SQLite cannot alter a constraint in place, so the table is rebuilt in batch
mode. ``copy_from`` is passed explicitly because SQLite reflection cannot
recover the constraint's name, and the three indexes are declared on the
copied table so the rebuild keeps them.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd1a6b4c8e215'
down_revision: Union[str, None] = 'c9f5a3e71b02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_CONSTRAINT = 'uq_contact_email_per_business'
_NEW_CONSTRAINT = 'uq_contact_identity_per_business'


def _table(constraint: str, columns: tuple) -> sa.Table:
    """``business_contacts`` plus the unique constraint *currently* in place.

    Batch mode matches the constraint to drop by name, so ``copy_from`` must
    declare the constraint as it exists right now — the old one while
    upgrading, the new one while downgrading.
    """
    table = sa.Table(
        'business_contacts',
        sa.MetaData(),
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('business_id', sa.String(length=36),
                  sa.ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('phone', sa.String(length=32), nullable=False),
        sa.Column('phone_normalized', sa.String(length=32), nullable=False),
        sa.Column('contact_name', sa.String(length=255), nullable=False),
        sa.Column('source_url', sa.String(length=1024), nullable=False),
        sa.Column('source', sa.String(length=64), nullable=False),
        sa.Column('confidence', sa.String(length=8), nullable=False),
        sa.Column('email_disposable', sa.Boolean(), nullable=True),
        sa.Column('verified', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint(*columns, name=constraint),
    )
    sa.Index('ix_business_contacts_business_id', table.c.business_id)
    sa.Index('ix_business_contacts_email', table.c.email)
    sa.Index('ix_contacts_business', table.c.business_id)
    return table


def upgrade() -> None:
    copy_from = _table(_OLD_CONSTRAINT, ('business_id', 'email'))
    with op.batch_alter_table('business_contacts', copy_from=copy_from) as batch:
        batch.drop_constraint(_OLD_CONSTRAINT, type_='unique')
        batch.create_unique_constraint(
            _NEW_CONSTRAINT, ['business_id', 'email', 'phone_normalized'],
        )


def downgrade() -> None:
    # Restoring the stricter (business_id, email) constraint is lossy: extra
    # phone rows for a business share email = ''. Keep the strongest row of each
    # group and drop the rest, otherwise the constraint cannot be recreated.
    op.execute("""
        DELETE FROM business_contacts WHERE id NOT IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY business_id, email
                    ORDER BY CASE confidence
                                 WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1
                                 WHEN 'LOW' THEN 2 ELSE 3 END,
                             created_at
                ) AS rn FROM business_contacts
            ) WHERE rn = 1
        )
    """)
    copy_from = _table(_NEW_CONSTRAINT,
                       ('business_id', 'email', 'phone_normalized'))
    with op.batch_alter_table('business_contacts', copy_from=copy_from) as batch:
        batch.drop_constraint(_NEW_CONSTRAINT, type_='unique')
        batch.create_unique_constraint(
            _OLD_CONSTRAINT, ['business_id', 'email'],
        )
