"""Đổi dữ liệu mẫu tiếng Nhật đang có trong DB sang dữ liệu Việt (chạy MỘT LẦN).

Chạy từ thư mục shop_api, venv ở gốc repo đã activate:

    python localize_data_vi.py            # xem trước, KHÔNG ghi
    python localize_data_vi.py --apply    # ghi thật

UPDATE tại chỗ chứ không xoá-tạo-lại: booking/reservation/shift/account đều trỏ theo id,
xoá đi là mất sạch dữ liệu test đã tích. Đổi tên và giá thì id giữ nguyên, mọi thứ còn
nguyên vẹn.

An toàn khi chạy lại: mọi phép đổi đều tra theo GIÁ TRỊ CŨ, chạy lần hai không còn gì
khớp nên không làm gì. Bản ghi do người dùng tự thêm (vd shop 1305) không nằm trong bảng
tra nên không bị đụng tới.

Giá đổi từ JPY sang VND theo mức niêm yết hợp lý ở VN, KHÔNG quy đổi theo tỉ giá —
đây là dữ liệu test, con số tròn dễ đọc quan trọng hơn.
"""

import sys

from app import create_app
from app.extensions import db
from app.models.shop import Account, Addon, Course, Customer, NgList, Shop, Therapist

# --- Bảng tra: giá trị CŨ -> giá trị MỚI --------------------------------------

SHOPS = {
    # shop_code: (name, address, phone)
    "1301": ("Cửa hàng Hoàn Kiếm", "25 Hàng Bài, Hoàn Kiếm, Hà Nội", "024 3826 1301"),
    "1302": ("Cửa hàng Hải Châu", "88 Bạch Đằng, Hải Châu, Đà Nẵng", "0236 3812 1302"),
    "1303": ("Cửa hàng Sài Gòn", "45 Lê Lợi, Quận 1, TP. Hồ Chí Minh", "028 3822 1303"),
    "1304": ("Cửa hàng Huế", "20 Hùng Vương, Phường Phú Nhuận, Thành phố Huế", "0234 3845 1304"),
}

# Tên gói không kèm chữ "phút" — chỗ hiển thị đã ghép sẵn "· {duration_min} phút".
COURSES = {
    # tên cũ: (tên mới, giá VND)
    "Momihogushi 30": ("Massage body 30", 190000),
    "Momihogushi 60": ("Massage body 60", 350000),
    "Momihogushi 90": ("Massage body 90", 490000),
    "Momihogushi 120": ("Massage body 120", 650000),
    "Dry Head Spa": ("Gội đầu dưỡng sinh", 280000),
    "Aroma Oil 90": ("Massage tinh dầu 90", 590000),
}

ADDONS = {
    "Ashitsubo": ("Bấm huyệt bàn chân", 80000),
    "Premium Mattress": ("Nệm cao cấp", 60000),
    "Hot Stone": ("Đá nóng", 90000),
    "Aroma Oil": ("Tinh dầu thơm", 120000),
}

# (shop_code, tên cũ) -> (tên mới, username cũ|None, username mới|None).
# Giới tính giữ nguyên: tên Việt chọn đúng giới nên không phải sửa cột gender.
THERAPISTS = {
    ("1301", "Hana"): ("Nguyễn Thị Hạnh", "hana01", "hanh01"),
    ("1301", "Yuki"): ("Trần Thu Hà", "yuki01", "ha01"),
    ("1301", "Ken"): ("Phạm Minh Khôi", "ken01", "khoi01"),
    ("1301", "Mai"): ("Lê Ngọc Mai", "mai01", "mai01"),
    ("1301", "Mi"): ("Võ Thảo My", "mi01", "my01"),
    ("1302", "Sakura"): ("Nguyễn Thu Trang", "sakura02", "trang02"),
    ("1302", "Riku"): ("Trần Quốc Bảo", "riku02", "bao02"),
    ("1302", "Aoi"): ("Lê Ngọc Ánh", "aoi02", "anh02"),
    ("1302", "Haruto"): ("Phạm Hoàng Long", None, None),
    ("1303", "Sora"): ("Vũ Thanh Thảo", "sora03", "thao03"),
    ("1303", "Ren"): ("Đặng Minh Quân", "ren03", "quan03"),
    ("1303", "Yui"): ("Bùi Khánh Linh", "yui03", "linh03"),
    ("1303", "Daichi"): ("Hoàng Đại Nghĩa", "daichi03", "nghia03"),
    ("1303", "Nao"): ("Ngô Phương Nhi", None, None),
    ("1304", "Kaito"): ("Lý Gia Huy", "kaito04", "huy04"),
    ("1304", "Mei"): ("Trịnh Mỹ Duyên", None, None),
}

# SĐT khách mẫu kiểu Nhật (080…/070…) -> số di động Việt 10 chữ số.
PHONES = {
    "08011110001": "0901110001",
    "08011110002": "0901110002",
    "08011110003": "0901110003",
    "08011110004": "0901110004",
    "08011110005": "0901110005",
    "08011110006": "0901110006",
    "08022220001": "0902220001",
    "08022220002": "0902220002",
    "07033330001": "0703330001",
    "07033330002": "0703330002",
}

NG_PHONES = {
    "08099990001": "0909990001",
    "08099990002": "0909990002",
    "07099990003": "0709990003",
}


def main(apply: bool) -> None:
    app = create_app()
    with app.app_context():
        changes: list[str] = []

        for code, (name, address, phone) in SHOPS.items():
            sh = Shop.query.filter_by(shop_code=code).first()
            if sh and (sh.name, sh.address, sh.phone) != (name, address, phone):
                changes.append(f"shop {code}: {sh.name!r} -> {name!r}")
                sh.name, sh.address, sh.phone = name, address, phone

        for model, table in ((Course, COURSES), (Addon, ADDONS)):
            for old, (new, price) in table.items():
                for row in model.query.filter_by(name=old).all():
                    changes.append(
                        f"{model.__name__.lower()} #{row.id} (shop {row.shop_id}): "
                        f"{old!r} {row.price} -> {new!r} {price}"
                    )
                    row.name, row.price = new, price

        for (code, old_name), (new_name, old_user, new_user) in THERAPISTS.items():
            sh = Shop.query.filter_by(shop_code=code).first()
            if not sh:
                continue
            t = Therapist.query.filter_by(shop_id=sh.id, name=old_name).first()
            if t:
                changes.append(f"therapist #{t.id} (shop {code}): {old_name!r} -> {new_name!r}")
                t.name = new_name
            if old_user and new_user and old_user != new_user:
                acc = Account.query.filter_by(username=old_user).first()
                # Trùng username là hỏng đăng nhập của người khác -> bỏ qua, báo ra màn hình.
                if acc and not Account.query.filter_by(username=new_user).first():
                    changes.append(f"account #{acc.id}: {old_user!r} -> {new_user!r}")
                    acc.username = new_user
                elif acc:
                    print(f"  BỎ QUA account {old_user!r}: username {new_user!r} đã có người dùng")

        for old, new in PHONES.items():
            c = Customer.query.filter_by(phone=old).first()
            if c and not Customer.query.filter_by(phone=new).first():
                changes.append(f"customer #{c.id}: {old} -> {new}")
                c.phone = new

        for old, new in NG_PHONES.items():
            n = NgList.query.filter_by(phone=old).first()
            if n and not NgList.query.filter_by(phone=new).first():
                changes.append(f"ng_list #{n.id}: {old} -> {new}")
                n.phone = new

        if not changes:
            print("Không có gì để đổi — dữ liệu đã là bản tiếng Việt.")
            db.session.rollback()
            return

        for line in changes:
            print(" ", line)
        print(f"\nTổng: {len(changes)} bản ghi.")

        if apply:
            db.session.commit()
            print("ĐÃ GHI vào DB.")
        else:
            db.session.rollback()
            print("Mới là xem trước — chạy lại với --apply để ghi thật.")


if __name__ == "__main__":
    main("--apply" in sys.argv)
