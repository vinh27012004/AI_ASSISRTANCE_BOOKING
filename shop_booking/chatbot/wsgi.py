"""Điểm vào WSGI. Chạy dev:  python -m flask --app wsgi run --port 5100
hoặc:  python wsgi.py
"""

from app.main import app

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5100, debug=True)
