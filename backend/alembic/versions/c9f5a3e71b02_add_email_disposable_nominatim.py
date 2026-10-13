"""add email_disposable + Nominatim geocoding support

Revision ID: c9f5a3e71b02
Revises: b7e4c2d90f13
Create Date: 2026-09-23

Open-source stack integration:

* ``business_contacts.email_disposable`` — Disify verdict per email
  (True = disposable/temp host, False = not disposable, NULL = unknown).
  The pipeline downgrades disposable emails to LOW confidence.
* No schema change is needed for geocoding: Nominatim is wired in as a
  fallback *business provider* (keyless) and as the location resolver for
  queries without coordinates, reusing the existing lat/lng columns.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c9f5a3e71b02'
down_revision: Union[str, None] = 'b7e4c2d90f13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'business_contacts',
        sa.Column('email_disposable', sa.Boolean(), nullable=True,
                  comment='Disify verdict: True = disposable/temp mail host'),
    )


def downgrade() -> None:
    op.drop_column('business_contacts', 'email_disposable')
