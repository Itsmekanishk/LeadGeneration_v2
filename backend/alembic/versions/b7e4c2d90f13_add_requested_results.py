"""add requested_results to search_jobs

Revision ID: b7e4c2d90f13
Revises: 3e7f2da40872
Create Date: 2026-09-18

Adds ``search_jobs.requested_results``: the number of results the user
originally requested (before the server-side MAX_RESULTS clamp). The UI uses
it to display honest "requested N / showing M available" notices.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7e4c2d90f13'
down_revision: Union[str, None] = '3e7f2da40872'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'search_jobs',
        sa.Column('requested_results', sa.Integer(), server_default='0',
                  nullable=False, comment='User-requested result count before server clamp'),
    )


def downgrade() -> None:
    op.drop_column('search_jobs', 'requested_results')
