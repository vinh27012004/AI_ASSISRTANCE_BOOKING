import os
import warnings

from dotenv import load_dotenv
from flask import Flask

from app.extensions import db, migrate

# HS256 ký bằng HMAC-SHA256: RFC 7518 §3.2 đòi key tối thiểu bằng độ dài đầu ra
# của hàm băm, tức 32 byte. Ngắn hơn thì PyJWT cũng tự cảnh báo.
_MIN_SECRET_BYTES = 32

# Chỉ dùng khi chạy dev. Đủ dài để không dính cảnh báo của PyJWT, và đặt tên
# thẳng thừng để không ai vô tình bê lên production.
_DEV_SECRET = "dev-only-insecure-secret-do-not-use-in-production"

_MISSING_SECRET = (
    "SECRET_KEY chưa được đặt. Production bắt buộc phải có: thiếu nó thì token ký "
    "bằng hằng số nằm sẵn trong source, ai đọc được repo cũng tự cấp được token admin.\n"
    'Sinh key: python -c "import secrets; print(secrets.token_urlsafe(48))"'
)


def _is_debug() -> bool:
    """Mặc định coi là production khi không biết chắc — fail closed. Thiếu biến này
    ở production mà lại đoán là dev thì đúng cái ta đang muốn chặn lại lọt qua."""
    return os.environ.get("FLASK_DEBUG", "").strip().lower() in ("1", "true", "on")


def _resolve_secret_key() -> str:
    secret = os.environ.get("SECRET_KEY", "").strip()
    debug = _is_debug()

    if not secret:
        if not debug:
            raise RuntimeError(_MISSING_SECRET)
        warnings.warn(
            "SECRET_KEY chưa đặt — đang dùng key dev cố định. Đặt SECRET_KEY trong .env.",
            stacklevel=2,
        )
        return _DEV_SECRET

    if len(secret.encode()) < _MIN_SECRET_BYTES:
        message = (
            f"SECRET_KEY chỉ dài {len(secret.encode())} byte, tối thiểu "
            f"{_MIN_SECRET_BYTES} byte cho HS256 (RFC 7518 §3.2)."
        )
        if not debug:
            raise RuntimeError(message)
        warnings.warn(message, stacklevel=2)

    return secret


def create_app() -> Flask:
    load_dotenv()

    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ["DATABASE_URL"]
    # Giải một lần lúc khởi động: thiếu key thì chết ngay khi boot, chứ không phải
    # đến lúc khách đầu tiên bấm đăng nhập mới lòi ra.
    app.config["SECRET_KEY"] = _resolve_secret_key()

    db.init_app(app)
    # Phải init SAU db, và models phải được import trước khi autogenerate chạy,
    # nếu không `flask db migrate` sẽ thấy metadata rỗng -> sinh migration trống.
    migrate.init_app(app, db)

    from app.models import shop  # noqa: F401 — đăng ký model vào db.metadata

    from app import api
    api.init_app(app)

    return app
