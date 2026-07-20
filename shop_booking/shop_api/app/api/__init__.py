from flask import Blueprint
from app.api.errors import register_error_handlers

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")

# Import views to register routes
from app.api import booking_flow, booking_manage, auth_admin, therapist  # noqa: F401,E402 — phải import sau khi
# tạo api_bp thì decorator @api_bp.route mới có blueprint để gắn vào.

def init_app(app):
    register_error_handlers(app)
    app.register_blueprint(api_bp)
