"""BR-21: tach CHI DINH (khach yeu cau) khoi PHAN CONG (BE tinh) o reservation

Truoc: `therapist_id` mang hai nghia — ERD ghi "khach chi dinh dich danh", nhung
create_booking lai ghi nguoi BE tu phan cong vao do (cho ca nhom >=2). Hau qua:

  * `therapist_gender` thanh cot chet: CHECK cu (therapist_id IS NULL OR
    therapist_gender IS NULL) khien khong the luu dong thoi "khach yeu cau nu" +
    "BE phan Hana" -> yeu cau cua khach mat trang sau khi tao booking.
  * PATCH khong doc lai duoc y dinh cua khach: doc therapist_id = Hana nhung khong
    biet la "khach doi Hana" hay "BE tu phan Hana" -> khong biet co duoc doi nguoi
    khong (US-02 AC2).

Sau:
  * requested_therapist_id (moi)  = khach chi dinh dich danh  \\ INPUT — tuy chon
  * therapist_gender (giu)        = khach chi dinh gioi tinh  /  loai tru nhau
  * therapist_id (giu)            = BE phan cong                 OUTPUT — luon co (BR-21)
  * CHECK chuyen sang cap CHI DINH, khong con lien quan toi therapist_id.

Data cu: requested_therapist_id = NULL cho moi dong => "khong chi dinh". Dung voi
thuc te vi truoc gio he thong chua bao gio luu chi dinh cua khach.

Revision ID: b7e1a4f92c30
Revises: 2c6133818132
Create Date: 2026-07-16

"""
from alembic import op
import sqlalchemy as sa


revision = 'b7e1a4f92c30'
down_revision = '2c6133818132'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'reservation',
        sa.Column(
            'requested_therapist_id',
            sa.Integer(),
            nullable=True,
            comment='Khách chỉ định ĐÍCH DANH — chỉ booking 1 người (BR-04, BR-05)',
        ),
    )
    op.create_foreign_key(
        'fk_reservation_requested_therapist',
        'reservation', 'therapist',
        ['requested_therapist_id'], ['id'],
    )

    # CHECK cu rang buoc sai cap: no gop "phan cong" voi "chi dinh gioi tinh".
    op.drop_constraint('chk_therapist_exclusive', 'reservation', type_='check')
    op.create_check_constraint(
        'chk_therapist_exclusive',
        'reservation',
        'requested_therapist_id IS NULL OR therapist_gender IS NULL',
    )

    op.alter_column(
        'reservation', 'therapist_id',
        existing_type=sa.Integer(),
        existing_nullable=True,
        comment='Therapist THỰC SỰ phục vụ suất này, BE tính lúc tạo (BR-21). '
                'NULL chỉ còn ở data cũ trước BR-21',
        existing_comment='Chỉ định đích danh — chỉ booking 1 người (BR-04)',
    )
    op.alter_column(
        'reservation', 'therapist_gender',
        existing_type=sa.Enum('male', 'female', name='reservation_therapist_gender_enum'),
        existing_nullable=True,
        comment='Khách chỉ định theo GIỚI TÍNH — loại trừ với requested_therapist_id',
        existing_comment='Chỉ định theo giới tính — loại trừ với therapist_id',
    )


def downgrade():
    # CHECK cu khong cho ton tai dong thoi therapist_id + therapist_gender. Sau khi
    # co BR-21 thi dong "khach yeu cau nu, BE phan Hana" co du ca hai -> tra CHECK cu
    # ve se lam DDL that bai. Xoa yeu cau gioi tinh truoc; day la du lieu ma schema cu
    # von khong co cho chua.
    op.execute(
        'UPDATE reservation SET therapist_gender = NULL WHERE therapist_id IS NOT NULL'
    )

    op.drop_constraint('chk_therapist_exclusive', 'reservation', type_='check')
    op.drop_constraint('fk_reservation_requested_therapist', 'reservation', type_='foreignkey')
    op.drop_column('reservation', 'requested_therapist_id')

    op.create_check_constraint(
        'chk_therapist_exclusive',
        'reservation',
        'therapist_id IS NULL OR therapist_gender IS NULL',
    )

    op.alter_column(
        'reservation', 'therapist_id',
        existing_type=sa.Integer(),
        existing_nullable=True,
        comment='Chỉ định đích danh — chỉ booking 1 người (BR-04)',
    )
    op.alter_column(
        'reservation', 'therapist_gender',
        existing_type=sa.Enum('male', 'female', name='reservation_therapist_gender_enum'),
        existing_nullable=True,
        comment='Chỉ định theo giới tính — loại trừ với therapist_id',
    )
