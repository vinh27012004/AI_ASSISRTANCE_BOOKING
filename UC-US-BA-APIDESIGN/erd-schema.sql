-- =====================================================
-- Hệ thống booking massage りらくる — Schema (MySQL)
-- Sinh từ bản phân tích nghiệp vụ (business-analysis-draft.md)
-- Scope: team tự build BE + FE; không có POS ngoài. Mã đặt chỗ, slot,
-- combo rule đều do hệ thống này quản lý.
-- =====================================================

-- Cửa hàng
CREATE TABLE shop (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    shop_code   VARCHAR(20)  NOT NULL UNIQUE COMMENT 'Mã cửa hàng',
    name        VARCHAR(100) NOT NULL,
    address     VARCHAR(255) NOT NULL,
    phone       VARCHAR(20)  NOT NULL
);

-- Course chính (もみほぐし, ドライヘッドスパ...)
CREATE TABLE course (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    shop_id      INT          NOT NULL,
    name         VARCHAR(100) NOT NULL,
    duration_min INT          NOT NULL COMMENT 'Bội số 15 phút',
    price        INT          NOT NULL COMMENT 'JPY',
    is_active    BOOLEAN      NOT NULL DEFAULT TRUE,
    FOREIGN KEY (shop_id) REFERENCES shop(id),
    CONSTRAINT chk_course_duration CHECK (duration_min % 15 = 0)   -- BR-02
);

-- Add-on (足つぼ, プレミアムマットレス...) — chỉ đi kèm course chính (BR-01)
CREATE TABLE addon (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    shop_id      INT          NOT NULL,
    name         VARCHAR(100) NOT NULL,
    duration_min INT          NOT NULL COMMENT 'Bội số 15 phút',
    price        INT          NOT NULL COMMENT 'JPY',
    is_active    BOOLEAN      NOT NULL DEFAULT TRUE,
    FOREIGN KEY (shop_id) REFERENCES shop(id),
    CONSTRAINT chk_addon_duration CHECK (duration_min % 15 = 0)    -- BR-02
);

-- Tổ hợp course + add-on KHÔNG được phép (BR-09 — chưa có data, mentor cung cấp sau;
-- bảng rỗng = mọi combo hợp lệ)
CREATE TABLE combo_restriction (
    course_id INT NOT NULL,
    addon_id  INT NOT NULL,
    PRIMARY KEY (course_id, addon_id),
    FOREIGN KEY (course_id) REFERENCES course(id),
    FOREIGN KEY (addon_id)  REFERENCES addon(id)
);

-- Therapist
CREATE TABLE therapist (
    id      INT AUTO_INCREMENT PRIMARY KEY,
    shop_id INT          NOT NULL,
    name    VARCHAR(100) NOT NULL,
    gender  ENUM('male','female') NOT NULL,
    FOREIGN KEY (shop_id) REFERENCES shop(id)
);

-- Ca làm việc của therapist
CREATE TABLE shift (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    therapist_id INT  NOT NULL,
    work_date    DATE NOT NULL,
    start_time   TIME NOT NULL,
    end_time     TIME NOT NULL,
    FOREIGN KEY (therapist_id) REFERENCES therapist(id),
    UNIQUE KEY uq_shift (therapist_id, work_date, start_time)
);

-- Tài khoản đăng nhập nội bộ (đã chốt: admin quản lý các cửa hàng,
-- therapist dùng tài khoản do admin cấp)
CREATE TABLE account (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(50)  NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role          ENUM('admin','therapist') NOT NULL,
    therapist_id  INT NULL COMMENT 'Chỉ với role therapist',
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (therapist_id) REFERENCES therapist(id)
);

-- Khách hàng (nhận dạng qua SĐT)
CREATE TABLE customer (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    phone       VARCHAR(20) NOT NULL UNIQUE,
    email       VARCHAR(255) NOT NULL COMMENT 'Xác thực + nhận mã đặt chỗ (BR-15)',
    member_type ENUM('member','guest') NOT NULL DEFAULT 'guest',
    rank        VARCHAR(20) NULL COMMENT 'Chỉ để hiển thị (BR-20); giả định chỉ member có',
    visit_count INT         NOT NULL DEFAULT 0
);

-- Danh sách SĐT bị cấm (BR-06)
CREATE TABLE ng_list (
    id       INT AUTO_INCREMENT PRIMARY KEY,
    phone    VARCHAR(20)  NOT NULL UNIQUE,
    reason   VARCHAR(255) NULL COMMENT 'Lý do cấm - có hiển thị (BR-20)',
    added_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Khung giờ khả dụng (hệ thống tự quản lý, cập nhật thời gian thực — BR-07, BR-08)
CREATE TABLE time_slot (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    shop_id    INT  NOT NULL,
    slot_date  DATE NOT NULL,
    start_time TIME NOT NULL,
    status     ENUM('available','booked') NOT NULL DEFAULT 'available',
    FOREIGN KEY (shop_id) REFERENCES shop(id),
    UNIQUE KEY uq_slot (shop_id, slot_date, start_time)
);

-- Booking: một lần đặt (1 cuộc gọi, 1 mã POS)
CREATE TABLE booking (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    booking_code VARCHAR(30) NULL UNIQUE COMMENT 'BE sinh: {yyyyMMdd}-{shop_code}-{random} (BR-12)',
    shop_id      INT  NOT NULL,
    customer_id  INT  NOT NULL,
    booking_date DATE NOT NULL,
    start_time   TIME NOT NULL,
    party_size   INT  NOT NULL DEFAULT 1 COMMENT '1-3; >=2 là booking nhóm (BR-14)',
    status       ENUM('pending','confirmed','cancelled','completed','no_show')
                 NOT NULL DEFAULT 'pending'
                 COMMENT 'COMPLETED tự động +1 visit_count (BR-19); no_show: giả định',
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME NULL ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (shop_id)     REFERENCES shop(id),
    FOREIGN KEY (customer_id) REFERENCES customer(id),
    CONSTRAINT chk_party_size CHECK (party_size BETWEEN 1 AND 3)   -- BR-14
);

-- Reservation: suất phục vụ của TỪNG NGƯỜI trong booking (BR-10)
-- Booking 1 người = 1 reservation; nhóm N người = N reservation cùng giờ
CREATE TABLE reservation (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    booking_id       INT NOT NULL,
    guest_no         INT NOT NULL COMMENT 'Người thứ mấy trong nhóm (1..party_size)',
    main_course_id   INT NOT NULL,
    therapist_id     INT NULL COMMENT 'Chỉ định đích danh — chỉ booking 1 người (BR-04)',
    therapist_gender ENUM('male','female') NULL COMMENT 'Chỉ định theo giới tính — loại trừ với therapist_id',
    slot_id          INT NULL,
    FOREIGN KEY (booking_id)     REFERENCES booking(id) ON DELETE CASCADE,
    FOREIGN KEY (main_course_id) REFERENCES course(id),
    FOREIGN KEY (therapist_id)   REFERENCES therapist(id),
    FOREIGN KEY (slot_id)        REFERENCES time_slot(id),
    UNIQUE KEY uq_reservation (booking_id, guest_no),
    -- không được vừa chỉ định tên vừa chỉ định giới tính
    CONSTRAINT chk_therapist_exclusive
        CHECK (therapist_id IS NULL OR therapist_gender IS NULL)
);

-- N-N: một reservation có thể kèm nhiều add-on
CREATE TABLE reservation_addon (
    reservation_id INT NOT NULL,
    addon_id       INT NOT NULL,
    PRIMARY KEY (reservation_id, addon_id),
    FOREIGN KEY (reservation_id) REFERENCES reservation(id) ON DELETE CASCADE,
    FOREIGN KEY (addon_id)       REFERENCES addon(id)
);

-- =====================================================
-- Các Business Rule KHÔNG thể enforce bằng schema,
-- phải xử lý ở tầng application:
--   BR-03: một therapist chỉ phục vụ 1 khách tại 1 thời điểm
--   BR-04: booking nhóm (party_size >= 2) không được chỉ định therapist
--   BR-05: therapist chỉ định phải có ca (shift) tại slot đó
--   BR-06: chặn SĐT có trong ng_list trước khi tạo booking
--   BR-08: xử lý conflict slot khi tạo booking (transaction + lock)
--   BR-09: chặn combo theo bảng combo_restriction
--   BR-10: các reservation trong booking nhóm phải cùng main_course_id (add-on mỗi người có thể khác)
--   BR-11: course/addon/therapist trong reservation phải cùng shop với booking
--   BR-16: chỉ cho sửa/hủy booking khi còn >= 1 giờ trước giờ hẹn
--   BR-17: sửa nhanh bằng edit token BE cấp lúc tạo booking (TTL 2 phút);
--          token hết hạn -> sửa/hủy qua trang quản lý web (xác thực email)
--   BR-18: sửa được đổi ngày/giờ, dịch vụ, số người (<=3); >3 hoặc đổi shop -> liên hệ shop
--   BR-19: hoàn tất phục vụ -> status COMPLETED + tự động +1 visit_count
-- =====================================================
