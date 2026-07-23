"""GD2 chatbot: them bang idempotency_key va channel_api_key

Hai thay doi BE cho kenh chatbot (DD_chatbot Q1/Q2; api-design §7):
  * idempotency_key: chong tao booking trung cho client tu retry (§7.1). Chatbot
    dat Idempotency-Key = conversation_id -> map cung ve dung 1 booking.
  * channel_api_key: xac thuc kenh chatbot qua header X-Api-Key (§7.2). Chi luu
    hash cua key; rate-limit rieng theo key.

Chi THEM bang, khong dung bang cu -> doc lap hoan toan luong nghiep vu GD1.

Revision ID: c1d9f2a4b6e8
Revises: b7e1a4f92c30
Create Date: 2026-07-23

"""
from alembic import op
import sqlalchemy as sa


revision = 'c1d9f2a4b6e8'
down_revision = 'b7e1a4f92c30'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'idempotency_key',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('idem_key', sa.String(length=64), nullable=False,
                  comment='Header Idempotency-Key (chatbot: conversation_id)'),
        sa.Column('booking_id', sa.Integer(), nullable=False),
        sa.Column('request_hash', sa.String(length=64), nullable=True,
                  comment='SHA-256 payload — phát hiện tái dùng key với nội dung khác'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'),
                  nullable=False, comment='Cron dọn key cũ hơn ~24h'),
        sa.ForeignKeyConstraint(['booking_id'], ['booking.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idem_key'),
    )
    op.create_table(
        'channel_api_key',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False,
                  comment='Tên kênh, vd "chatbot-web"'),
        sa.Column('key_hash', sa.String(length=255), nullable=False,
                  comment='Hash của API key (không lưu key thô)'),
        sa.Column('key_prefix', sa.String(length=12), nullable=False,
                  comment='Vài ký tự đầu để nhận diện trong log (không bí mật)'),
        sa.Column('rate_limit_per_min', sa.Integer(), server_default=sa.text('60'),
                  nullable=False, comment='Hạn mức request/phút riêng cho kênh này'),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key_hash'),
    )


def downgrade():
    op.drop_table('channel_api_key')
    op.drop_table('idempotency_key')
