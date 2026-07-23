"""CLI ops cho kênh chatbot GĐ2 — cấp/liệt kê/thu hồi API key (api-design §7.2).

Cấp key là thao tác admin/ops, ngoài luồng khách. Key THÔ chỉ in ra MỘT LẦN lúc tạo;
DB chỉ giữ hash (không khôi phục được) → mất key phải cấp key mới.

    python -m flask channel-key create --name chatbot-web
    python -m flask channel-key create --name chatbot-web --rate-limit 120
    python -m flask channel-key list
    python -m flask channel-key revoke <id>
"""

import secrets

import click
from flask.cli import AppGroup

from app.extensions import db
from app.api.channel_auth import hash_api_key
from app.models.shop import ChannelApiKey

channel_key_cli = AppGroup("channel-key", help="Quản lý API key kênh chatbot (GĐ2).")


@channel_key_cli.command("create")
@click.option("--name", required=True, help='Tên kênh, vd "chatbot-web".')
@click.option("--rate-limit", "rate_limit", default=60, show_default=True,
              help="Hạn mức request/phút cho key này.")
def create_key(name: str, rate_limit: int):
    """Cấp key mới. In key thô ra một lần — lưu ngay, không xem lại được."""
    raw_key = "cbk_" + secrets.token_urlsafe(32)
    channel = ChannelApiKey(
        name=name,
        key_hash=hash_api_key(raw_key),
        key_prefix=raw_key[:12],
        rate_limit_per_min=rate_limit,
    )
    db.session.add(channel)
    db.session.commit()

    click.echo(f"Đã cấp key cho kênh '{name}' (id={channel.id}, {rate_limit} req/phút).")
    click.echo("Gửi header:  X-Api-Key: " + raw_key)
    click.echo("LƯU NGAY — key thô không hiển thị lại (DB chỉ giữ hash).")


@channel_key_cli.command("list")
def list_keys():
    """Liệt kê các key (không lộ key thô — chỉ prefix/nhận diện)."""
    keys = ChannelApiKey.query.order_by(ChannelApiKey.id).all()
    if not keys:
        click.echo("Chưa có key nào.")
        return
    for k in keys:
        status = "active" if k.is_active else "revoked"
        last = k.last_used_at.isoformat(sep=" ", timespec="seconds") if k.last_used_at else "chưa dùng"
        click.echo(f"#{k.id}  {k.name:<16} {k.key_prefix}…  {k.rate_limit_per_min}/phút  {status}  last_used={last}")


@channel_key_cli.command("revoke")
@click.argument("key_id", type=int)
def revoke_key(key_id: int):
    """Thu hồi key (is_active=false) — request sau dùng key này sẽ nhận 401."""
    channel = db.session.get(ChannelApiKey, key_id)
    if channel is None:
        raise click.ClickException(f"Không có key id={key_id}.")
    channel.is_active = False
    db.session.commit()
    click.echo(f"Đã thu hồi key #{key_id} ('{channel.name}').")


def init_app(app):
    app.cli.add_command(channel_key_cli)
