"""Template NLG theo state — DD §3.1, §7. Chatbot chỉ phục vụ TIẾNG VIỆT (giai đoạn này).

- INSTRUCTION[state]: chỉ dẫn (đưa cho LLM ở bước ⑥) — nói Ý câu cần sinh, KHÔNG chứa số
  liệu (số liệu nằm ở `facts`). LLM diễn đạt tự nhiên, cấm bịa (§10).
- FAKE[state]: câu mẫu offline khi chưa cấu hình router.

KHÔNG còn nút bấm: mọi lựa chọn (cửa hàng/ngày/course/add-on/giờ) phải được ĐỌC RA trong
câu, vì khách chỉ có thể trả lời bằng lời (chat gõ tay hoặc gọi điện).
"""

from __future__ import annotations

from app import states as S

# Câu hỏi ĐANG DỞ, đọc lại sau khi trả lời một câu hỏi ngoài luồng để hội thoại không bị
# cụt. Cố ý NGẮN, không đọc lại danh sách (shop/giờ/gói): khách vừa thấy ở lượt trước, và
# đọc lại sẽ phải gọi API lần thứ hai trong cùng một lượt.
# Câu để TRẦN, chưa viết hoa: `_pending_question` ghép thêm một lời dẫn ở PENDING_LEAD.
# Bỏ tiền tố "Quay lại ạ," cũ — nó lặp nguyên si ở mọi lượt hỏi-đáp và còn thừa một chữ
# "ạ" nữa ngay trong câu, đọc lên rất máy móc (khách phản ánh bot nói chưa tự nhiên).
PENDING_QUESTION = {
    S.SHOP: "anh/chị muốn đặt ở cửa hàng nào ạ?",
    S.DATE: "anh/chị muốn đặt ngày nào ạ?",
    S.PARTY_SIZE: "anh/chị đặt cho mấy người ạ?",
    S.COURSE: "anh/chị chọn gói dịch vụ nào ạ?",
    S.ADDON: "anh/chị có muốn thêm dịch vụ bổ sung nào không ạ?",
    S.THERAPIST: "anh/chị có muốn chỉ định nhân viên không ạ?",
    S.SLOT: "anh/chị chọn khung giờ nào ạ?",
    S.CONTACT: "anh/chị cho em xin số điện thoại và email nhé.",
    S.CONFIRM: "anh/chị xác nhận đặt lịch giúp em nhé?",
}
PENDING_QUESTION_DEFAULT = "anh/chị cần em hỗ trợ gì thêm không ạ?"

# Lời dẫn xoay vòng theo số lượt (deterministic — vẫn test được). Cùng một câu hỏi bị đọc
# lại nhiều lượt thì ít nhất cách dẫn vào phải khác nhau.
PENDING_LEAD = ("Mình quay lại nhé, ", "Vậy ", "Còn ", "Giờ ")

# Chỉ dẫn cho LLM (bước ⑤).
INSTRUCTION = {
    "GREETING": "Chào khách, giới thiệu là trợ lý AI đặt lịch massage, hỏi khách cần gì.",
    "SHOP": "Hỏi khách muốn đặt ở cửa hàng nào, ĐỌC RÕ danh sách cửa hàng trong facts.",
    "DATE": "Hỏi khách muốn đặt ngày nào, nêu vài ngày cửa hàng còn làm trong facts.",
    "PARTY_SIZE": "Hỏi đặt cho mấy người, nhắc tối đa 3 người mỗi lượt.",
    "COURSE": "Hỏi khách chọn gói dịch vụ chính, ĐỌC RÕ danh sách gói trong facts (mỗi gói đã kèm sẵn thời lượng).",
    "ADDON": "(Render TẤT ĐỊNH — soạn ở nlg._addon_prompt_line, không qua LLM.) Hỏi add-on MỘT lần cho cả nhóm (BR-10), đọc rõ danh sách, cho chọn NHIỀU, và cho khách nói 'không' để bỏ qua.",
    "SLOT": "Mời khách chọn khung giờ, NÊU RÕ NGÀY và ĐỌC RÕ các giờ còn trống trong facts; khách nói giờ mong muốn là được.",
    "THERAPIST": "Hỏi khách có muốn chỉ định nhân viên (nêu tên nhân viên đang trực trong facts, hoặc theo giới tính) hay để cửa hàng sắp.",
    "CONTACT": "Xin thông tin liên hệ CÒN THIẾU (đúng theo facts.hoi) để giữ chỗ và gửi mã đặt chỗ. Nếu khách đã cho số điện thoại rồi thì CHỈ hỏi email, đừng hỏi lại số.",
    "CONFIRM": "Đọc lại toàn bộ thông tin đơn và xin khách xác nhận.",
    "DONE": "Báo đặt thành công, nói mã đặt chỗ đã gửi vào email, mời sửa/hủy nếu cần.",
    "UPDATED": "Báo đã cập nhật lịch thành công theo thông tin mới.",
    "CANCELLED": "Xác nhận đã hủy lịch, chào tạm biệt lịch sự.",
    "MODIFY": "Hỏi khách muốn đổi phần nào của lịch (giờ / số người / dịch vụ), hoặc hủy, hoặc giữ nguyên.",
    "END": "Thông báo không thể đặt online, đưa số điện thoại cửa hàng, lịch sự.",
    "HANDOFF": "Xin lỗi vì chưa hỗ trợ được, mời khách gọi cửa hàng.",
    "REPROMPT": "Nói chưa hiểu rõ, xin khách nói lại ngắn gọn.",
    "ERROR": "Truyền đạt thông báo lỗi từ hệ thống một cách lịch sự, gợi ý bước tiếp theo.",
    "INFO": "(Render TẤT ĐỊNH — câu chứa giờ/địa chỉ/giá THẬT, không qua LLM.)",
    "OUT_OF_SCOPE": "(Render TẤT ĐỊNH.)",
}

# Câu mẫu offline. {…} là chỗ điền facts.
FAKE = {
    "GREETING": "Dạ em là trợ lý đặt lịch massage. Em có thể giúp anh/chị đặt lịch ạ. Anh/chị cần gì ạ?",
    "SHOP": "Anh/chị muốn đặt ở cửa hàng nào ạ? Hiện có: {cua_hang_list}.",
    "DATE": "Anh/chị muốn đặt vào ngày nào ạ? {ngay_list}",
    "PARTY_SIZE": "Anh/chị đặt cho mấy người ạ? (tối đa 3 người mỗi lượt)",
    "COURSE": "Anh/chị chọn giúp em gói dịch vụ chính ạ: {course_list}.",
    # Câu soạn động trong nlg._addon_prompt_line (đọc danh sách add-on + cho nói 'không').
    # ADDON ở _LITERAL_SAFE_KEYS nên luôn dùng câu này, không qua LLM.
    "ADDON": "{addon_line}",
    "SLOT": "{gio_het}{ngay}các khung giờ còn trống: {slots}. Anh/chị chọn giờ nào ạ?",
    "THERAPIST": "Anh/chị có muốn chỉ định nhân viên không ạ? {nhan_vien_list}"
                 "Anh/chị có thể chọn theo tên, theo giới tính (nam/nữ), hoặc để cửa hàng sắp giúp.",
    "CONTACT": "Anh/chị cho em xin {hoi} để giữ chỗ và gửi mã đặt chỗ ạ.",
    "CONFIRM": "Em xin xác nhận đơn: {summary}. Anh/chị đồng ý đặt chứ ạ?",
    "DONE": "Đặt thành công ạ! Mã đặt chỗ {booking_code} đã gửi vào email của anh/chị. "
            "Anh/chị muốn sửa hoặc hủy thì nhắn em nhé.{sua_nhanh}",
    "UPDATED": "Đã cập nhật lịch {booking_code} theo thông tin mới ạ. Email xác nhận đã được gửi lại.{sua_nhanh}",
    "CANCELLED": "Đã hủy lịch {booking_code} ạ. Rất mong được phục vụ anh/chị lần sau!",
    "MODIFY": "Anh/chị muốn đổi phần nào ạ — giờ, số người, hay dịch vụ? "
              "Hoặc nói “hủy lịch” để hủy, “giữ nguyên” nếu thôi không đổi.{sua_nhanh}",
    "END": "{message} Anh/chị vui lòng liên hệ hỗ trợ: {shop_phone}.",
    "HANDOFF": "{message}Anh/chị vui lòng gọi để được hỗ trợ nhé: {shop_phone}.",
    "REPROMPT": "Dạ em chưa rõ ý anh/chị. Anh/chị nói lại ngắn gọn giúp em nhé.",
    "ERROR": "{message}",
    # Trả lời câu hỏi ngoài luồng rồi đọc lại câu đang dở (tờ đơn KHÔNG đổi).
    "INFO": "{noi_dung} {cau_hoi}",
    "OUT_OF_SCOPE": "Dạ chỗ này em chưa hỗ trợ được ạ, em chủ yếu giúp anh/chị đặt lịch thôi. {cau_hoi}",
}


def fake_sentence(key: str, facts: dict) -> str:
    template = FAKE.get(key, FAKE["REPROMPT"])
    try:
        return template.format(**facts)
    except (KeyError, IndexError):
        return template
