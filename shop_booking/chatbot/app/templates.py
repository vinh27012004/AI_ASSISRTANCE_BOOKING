"""Template NLG theo state × ngôn ngữ — DD §3.1, §7.

- INSTRUCTION[state]: chỉ dẫn (đưa cho LLM ở bước ⑥) — nói Ý câu cần sinh, KHÔNG chứa số
  liệu (số liệu nằm ở `facts`). LLM diễn đạt theo `lang`, cấm bịa (§10).
- FAKE[state][lang]: câu mẫu offline khi chưa cấu hình router. Thiếu ngôn ngữ -> fallback 'vi'.

Lựa chọn (shop/course/slot…) hiển thị bằng NÚT (buttons.py) nên câu chữ giữ ngắn gọn.
"""

from __future__ import annotations

# Chỉ dẫn cho LLM (bước ⑤). Tiếng Việt mô tả ý — LLM tự dịch sang lang khách.
INSTRUCTION = {
    "GREETING": "Chào khách, giới thiệu là trợ lý AI đặt lịch massage, hỏi khách cần gì.",
    "SHOP": "Hỏi khách muốn đặt ở cửa hàng nào (danh sách hiện bằng nút).",
    "DATE": "Hỏi khách muốn đặt vào ngày nào.",
    "PARTY_SIZE": "Hỏi đặt cho mấy người, nhắc tối đa 3 người mỗi lượt.",
    "COURSE": "Hỏi khách chọn course chính (mỗi course đã kèm sẵn thời lượng).",
    "ADDON": "Hỏi khách có muốn thêm dịch vụ bổ sung (add-on) không; add-on cấm đã ẩn. Có thể bỏ qua.",
    "SLOT": "Mời khách chọn khung giờ (các giờ hiện bằng nút); nói thêm khách có thể nhập giờ cụ thể mong muốn nếu chưa thấy giờ ưng ý.",
    "THERAPIST": "Hỏi khách có muốn chỉ định nhân viên (theo tên hoặc giới tính) hay để cửa hàng sắp.",
    "CONTACT": "Xin thông tin liên hệ CÒN THIẾU (đúng theo facts.hoi) để giữ chỗ và gửi mã đặt chỗ. Nếu khách đã cho số điện thoại rồi thì CHỈ hỏi email, đừng hỏi lại số.",
    "CONFIRM": "Đọc lại toàn bộ thông tin đơn và xin khách xác nhận.",
    "DONE": "Báo đặt thành công, nói mã đặt chỗ đã gửi vào email, mời sửa/hủy nếu cần.",
    "UPDATED": "Báo đã cập nhật lịch thành công theo thông tin mới.",
    "CANCELLED": "Xác nhận đã hủy lịch, chào tạm biệt lịch sự.",
    "MODIFY": "Hỏi khách muốn đổi phần nào của lịch (ngày giờ / số người / dịch vụ) hoặc hủy.",
    "END": "Thông báo không thể đặt online, đưa số điện thoại cửa hàng, lịch sự.",
    "HANDOFF": "Xin lỗi vì chưa hỗ trợ được, mời khách gọi cửa hàng.",
    "REPROMPT": "Nói chưa hiểu rõ, xin khách nói lại ngắn gọn.",
    "ERROR": "Truyền đạt thông báo lỗi từ hệ thống một cách lịch sự, gợi ý bước tiếp theo.",
}

# Câu mẫu offline. {…} là chỗ điền facts.
FAKE = {
    "GREETING": {
        "vi": "Dạ em là trợ lý đặt lịch massage. Em có thể giúp anh/chị đặt lịch ạ. Anh/chị cần gì ạ?",
        "en": "Hi! I'm the massage booking assistant. How can I help you book today?",
        "ja": "こんにちは。マッサージ予約アシスタントです。ご予約のお手伝いをいたします。",
    },
    "SHOP": {
        "vi": "Anh/chị muốn đặt ở cửa hàng nào ạ?",
        "en": "Which shop would you like to book?",
        "ja": "どちらの店舗をご希望ですか？",
    },
    "DATE": {
        "vi": "Anh/chị muốn đặt vào ngày nào ạ?",
        "en": "What date would you like?",
        "ja": "ご希望の日にちはいつですか？",
    },
    "PARTY_SIZE": {
        "vi": "Anh/chị đặt cho mấy người ạ? (tối đa 3 người mỗi lượt)",
        "en": "For how many people? (up to 3 per booking)",
        "ja": "何名様でしょうか？（1回につき最大3名）",
    },
    "COURSE": {
        "vi": "Anh/chị chọn giúp em gói dịch vụ chính ạ.",
        "en": "Please pick a main course.",
        "ja": "メインコースをお選びください。",
    },
    "ADDON": {
        "vi": "Anh/chị có muốn thêm dịch vụ bổ sung nào không ạ? Nếu không thì bấm “Không thêm”.",
        "en": "Any add-ons? Tap “No add-on” to skip.",
        "ja": "追加オプションはいかがですか？不要なら「追加なし」を押してください。",
    },
    "SLOT": {
        "vi": "Các khung giờ còn trống: {slots}. Anh/chị chọn giờ nào ạ? (hoặc nhập giờ cụ thể mong muốn)",
        "en": "Available times: {slots}. Which one works? (or type a specific time)",
        "ja": "空き時間：{slots}。ご希望の時間をお選びください。（希望時刻を入力も可）",
    },
    "THERAPIST": {
        "vi": "Anh/chị có muốn chỉ định nhân viên không, hay để cửa hàng sắp giúp ạ?",
        "en": "Any therapist preference, or shall we assign one?",
        "ja": "指名はございますか？おまかせでもよろしいですか？",
    },
    "CONTACT": {
        "vi": "Anh/chị cho em xin {hoi} để giữ chỗ và gửi mã đặt chỗ ạ.",
        "en": "Could you share your {hoi} so we can hold the slot and send the booking code?",
        "ja": "予約確保と予約コード送付のため、{hoi}をお願いできますか？",
    },
    "CONFIRM": {
        "vi": "Em xin xác nhận đơn: {summary}. Anh/chị đồng ý đặt chứ ạ?",
        "en": "Please confirm: {summary}. Shall I book it?",
        "ja": "ご予約内容の確認：{summary}。この内容でよろしいですか？",
    },
    "DONE": {
        "vi": "Đặt thành công ạ! Mã đặt chỗ {booking_code} đã gửi vào email của anh/chị. "
              "Anh/chị có thể sửa hoặc hủy lịch ngay dưới đây.",
        "en": "Booked! Your code {booking_code} was emailed to you. "
              "You can edit or cancel below.",
        "ja": "ご予約完了です！予約コード {booking_code} をメールでお送りしました。"
              "下のボタンから変更・キャンセルできます。",
    },
    "UPDATED": {
        "vi": "Đã cập nhật lịch {booking_code} theo thông tin mới ạ. Email xác nhận đã được gửi lại.",
        "en": "Your booking {booking_code} has been updated. A confirmation email was sent.",
        "ja": "ご予約 {booking_code} を更新しました。確認メールを再送しました。",
    },
    "CANCELLED": {
        "vi": "Đã hủy lịch {booking_code} ạ. Rất mong được phục vụ anh/chị lần sau!",
        "en": "Booking {booking_code} has been cancelled. Hope to see you again!",
        "ja": "ご予約 {booking_code} をキャンセルしました。またのご利用をお待ちしております。",
    },
    "MODIFY": {
        "vi": "Anh/chị muốn đổi phần nào của lịch ạ?",
        "en": "What would you like to change?",
        "ja": "どの項目を変更しますか？",
    },
    "END": {
        "vi": "{message} Anh/chị vui lòng liên hệ cửa hàng: {shop_phone}.",
        "en": "{message} Please contact the shop: {shop_phone}.",
        "ja": "{message} お手数ですが店舗までご連絡ください：{shop_phone}。",
    },
    "HANDOFF": {
        "vi": "Dạ phần này em chưa hỗ trợ được. Anh/chị vui lòng gọi cửa hàng: {shop_phone}.",
        "en": "Sorry, I can't help with that here. Please call the shop: {shop_phone}.",
        "ja": "申し訳ございません、こちらは対応できません。店舗までお電話ください：{shop_phone}。",
    },
    "REPROMPT": {
        "vi": "Dạ em chưa rõ ý anh/chị. Anh/chị nói lại ngắn gọn giúp em nhé.",
        "en": "Sorry, I didn't catch that. Could you rephrase briefly?",
        "ja": "すみません、もう一度短くお願いできますか？",
    },
    "ERROR": {
        "vi": "{message}",
        "en": "{message}",
        "ja": "{message}",
    },
}


def fake_sentence(key: str, lang: str, facts: dict) -> str:
    per_lang = FAKE.get(key, FAKE["REPROMPT"])
    template = per_lang.get(lang) or per_lang.get("vi") or ""
    try:
        return template.format(**facts)
    except (KeyError, IndexError):
        return template
