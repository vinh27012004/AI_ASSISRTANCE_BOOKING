from typing import Any
from flask import jsonify

class APIError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self):
        rv = {
            "code": self.code,
            "message": self.message
        }
        if self.details is not None:
            rv["details"] = self.details
        return {"error": rv}


def register_error_handlers(app):
    @app.errorhandler(APIError)
    def handle_api_error(e: APIError):
        return jsonify(e.to_dict()), e.status_code

    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Dữ liệu không hợp lệ, vui lòng kiểm tra lại."
            }
        }), 400

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({
            "error": {
                "code": "RESOURCE_NOT_FOUND",
                "message": "Không tìm thấy dữ liệu yêu cầu."
            }
        }), 404

    @app.errorhandler(500)
    def internal_error(e):
        return jsonify({
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "Hệ thống đang gặp sự cố tạm thời. Vui lòng thử lại sau ít phút."
            }
        }), 500