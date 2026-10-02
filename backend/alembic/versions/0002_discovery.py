"""Automatic discovery: aggregator sources, company discovery state, robots.txt cache."""
from alembic import op
import sqlalchemy as sa
revision="0002"
down_revision="0001"
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('companies', sa.Column('name_key', sa.String(length=200), nullable=True))
    op.add_column('companies', sa.Column('discovered_via', sa.String(length=50), nullable=True))
    op.add_column('companies', sa.Column('discovery_status', sa.String(length=30), nullable=False, server_default='pending'))
    op.add_column('companies', sa.Column('discovery_checked_at', sa.DateTime(), nullable=True))
    op.create_index(op.f('ix_companies_name_key'), 'companies', ['name_key'], unique=False)
    op.create_index(op.f('ix_companies_discovery_status'), 'companies', ['discovery_status'], unique=False)
    op.create_table('robots_cache',
    sa.Column('origin', sa.String(length=300), nullable=False),
    sa.Column('state', sa.String(length=20), nullable=False),
    sa.Column('status_code', sa.Integer(), nullable=True),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('fetched_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('origin')
    )
    op.create_table('source_runs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('source', sa.String(length=50), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.Column('count', sa.Integer(), nullable=False),
    sa.Column('companies', sa.Integer(), nullable=False),
    sa.Column('cursor', sa.DateTime(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_source_runs_source'), 'source_runs', ['source'], unique=False)
    # Demo companies are never probed.
    op.execute("UPDATE companies SET discovery_status='skip' WHERE is_demo")

def downgrade():
    op.drop_index(op.f('ix_source_runs_source'), table_name='source_runs')
    op.drop_table('source_runs')
    op.drop_table('robots_cache')
    op.drop_index(op.f('ix_companies_discovery_status'), table_name='companies')
    op.drop_index(op.f('ix_companies_name_key'), table_name='companies')
    op.drop_column('companies', 'discovery_checked_at')
    op.drop_column('companies', 'discovery_status')
    op.drop_column('companies', 'discovered_via')
    op.drop_column('companies', 'name_key')
