# Corpus FAQ — làn QUERY của chatbot

Mỗi mục `## ` là MỘT câu trả lời trọn vẹn. Mặc định nó đi qua LLM diễn đạt lại cho khớp
câu khách hỏi (bước G — `app/answers/faq.py::_augment`), nhưng **mọi nhánh hỏng đều lùi về
trả nguyên văn**: router lỗi, quá hạn chờ, model tự nhận không đủ dữ kiện, hoặc chạy với
`FAQ_GENERATE=0`. Vì vậy vẫn viết như thể sẽ được đọc nguyên văn:

- Viết bằng giọng trợ lý, 1–3 câu, kết thúc gọn. Câu đọc lại việc đang dở sẽ được nối
  ngay phía sau theo template `INFO` = `"{noi_dung} {cau_hoi}"`.
- **Chỉ viết CHÍNH SÁCH / QUY TRÌNH.** Tuyệt đối không ghi giờ mở cửa, giá, địa chỉ, số
  điện thoại hay lịch nghỉ vào đây — mấy thứ đó thay đổi liên tục và đã có resolver gọi
  thẳng `shop_api` (`shop_info.py`, `location.py`). Ghi vào đây là dữ liệu chết, sẽ sai.
  Quy ước này còn là thứ giữ cho bước G an toàn: chunk không có số liệu sống thì LLM
  không có gì để chép sai.
- Tiêu đề đặt đúng như cách KHÁCH hỏi (tiêu đề được đánh trọng số gấp đôi khi tìm kiếm).
- Dòng bắt đầu bằng `> ` là **cách hỏi khác**: chỉ dùng để tìm kiếm, KHÔNG đọc cho khách.
  Đây là núm chỉnh chất lượng — thấy bot không nhận ra câu nào thì thêm một dòng `> `.
- Kho này là **nguồn tin cậy, review qua git**. Đừng làm bảng cho staff sửa trong trang
  admin: nội dung ở đây đi thẳng vào câu bot nói, ai sửa được file là nói thay bot được.
- Chỉ tiêu đề `## ` mới thành một mục. Tiêu đề phụ trong phần hướng dẫn phải dùng `###`,
  không thì nó bị đếm thành một mục FAQ rỗng nghĩa (đã dính đúng lỗi này lúc viết).

### Bố cục thư mục

Kho nằm ở `data/faq/`, chia theo chủ đề — mỗi file một mảng, gộp lại thành một corpus:

| File | Nội dung |
|---|---|
| `co-ban.md` | Quy ước viết (chính file này) + các mục nền đầu tiên |
| `dat-lich.md` | Quy trình đặt, chọn giờ, đặt hộ, đặt nhiều buổi |
| `nhom.md` | Đi nhóm: số người, dùng chung dịch vụ, xếp chỗ |
| `dich-vu.md` | Gói chính, dịch vụ thêm, thời lượng |
| `nhan-vien.md` | Chỉ định người, giới tính, đổi người |
| `sua-huy.md` | Sửa, hủy, xác thực, tra lại lịch |
| `thong-tin-khach.md` | Email, số điện thoại, thành viên, dữ liệu |
| `tro-giup.md` | Phạm vi hỗ trợ, sự cố, khiếu nại |

Thêm câu hỏi mới = thêm một mục `## ` vào file hợp chủ đề, hoặc tạo file `.md` mới trong
thư mục này. Không phải sửa code, không phải thêm luật vào `nlu._detect_question`.

### Cái bẫy khi kho lớn dần

Bigram phía câu hỏi (tiêu đề + dòng `> `) là thứ `_confident` dùng để quyết định nhận hay
từ chối. Kho càng nhiều mục thì càng dễ có hai mục **giành nhau một bigram**, và mục mới có
thể cướp câu hỏi vốn thuộc về mục cũ — hoặc tệ hơn, cướp một câu lẽ ra phải bị TỪ CHỐI.

Ba lần đã gặp thật khi kho lên 82 mục (28/8):

- `"cho tôi đặt lịch"` là câu ĐẶT CHỖ, phải rơi về luồng đặt lịch chứ không phải tra cứu.
  Bất kỳ tiêu đề/alias nào chứa cụm `đặt lịch` cũng cướp mất nó → dùng `đặt chỗ` thay.
- `"hôm nay mấy giờ đóng cửa"` là dữ liệu SỐNG, phải đi qua `shop_api`. Một alias viết
  `"vì sao chỉ có mấy giờ này"` đủ để cướp nó qua bigram `mấy_giờ`.
- `"tôi muốn chọn nhân viên phục vụ"` bị mục nói về `phục vụ hai khách cùng lúc` giành mất.

Vì vậy: **thêm mục xong luôn chạy `check_faq.py`**, và đọc phần `MUST_REJECT` trước phần
`MUST_ANSWER` — trả lời tự tin mà lạc chủ đề tệ hơn nói "em chưa hỗ trợ được".

```bash
python tests/check_faq.py          # 60 câu dò + recall@1/@3 + từ chối oan
python tests/check_faq_gen.py      # chất lượng bước sinh (cần router)
python tests/test_chatbot.py       # bộ test đầy đủ
```
## Hủy lịch thì làm thế nào, có mất phí không
> hủy lịch
> hủy đặt chỗ
> hủy booking
> bỏ lịch đã đặt
> không đi nữa
> bận đột xuất không tới được
> mất tiền cọc không nếu bỏ

Dạ anh/chị hủy được ạ, miễn là hủy trước giờ hẹn ít nhất 1 tiếng. Anh/chị nhắn em "hủy
lịch" là em xử lý luôn, hoặc vào trang quản lý đặt chỗ trên web với email và mã đặt chỗ ạ.

## Đổi lịch, dời ngày giờ đã đặt
> đổi lịch
> dời lịch
> dời sang ngày khác
> dời sang hôm khác
> đổi sang giờ khác
> đổi ngày giờ
> chuyển lịch sang hôm khác
> sửa lịch đã đặt
> kẹt lịch dời qua bữa khác

Dạ anh/chị đổi được ngày giờ, dịch vụ và số người, miễn là đổi trước giờ hẹn ít nhất 1
tiếng ạ. Anh/chị nhắn em "đổi lịch" là em mở giúp, hoặc sửa trên trang quản lý đặt chỗ.

## Sát giờ hẹn rồi có sửa hay hủy được không
> sát giờ hẹn
> gần tới giờ hẹn
> còn ít phút nữa tới giờ

Dạ trong vòng 1 tiếng trước giờ hẹn thì hệ thống không cho sửa hay hủy nữa ạ. Trường hợp
này anh/chị gọi trực tiếp cho cửa hàng để được hỗ trợ giúp em ạ.

## Vừa đặt xong muốn sửa ngay
> vừa đặt xong
> mới đặt xong muốn sửa
> sửa nhanh sau khi đặt

Dạ ngay sau khi đặt xong anh/chị có 2 phút để sửa nhanh chính lịch vừa tạo ạ. Quá 2 phút
thì vẫn sửa được, nhưng phải qua trang quản lý đặt chỗ với email và mã đặt chỗ ạ.

## Đổi sang cửa hàng khác
> đổi cửa hàng
> chuyển sang chi nhánh khác
> đổi chi nhánh

Dạ lịch đã đặt thì không đổi sang cửa hàng khác được ạ, vì dịch vụ và lịch nhân viên của
mỗi cửa hàng là riêng. Anh/chị hủy lịch cũ rồi đặt lại ở cửa hàng mới, hoặc gọi cửa hàng
để được hỗ trợ ạ.

## Đặt được tối đa mấy người một lần
> đi mấy người
> đi 2 người
> đi 3 người
> đi 4 người
> đi 5 người
> đặt cho nhóm
> nhóm đông người
> đi đông người
> tối đa bao nhiêu người

Dạ một lần đặt được tối đa 3 người ạ. Nhóm đông hơn 3 người thì anh/chị liên hệ trực tiếp
cửa hàng để bên em sắp xếp giúp ạ.

## Đi nhóm có chỉ định được nhân viên không
> nhóm chỉ định nhân viên
> đi 2 người chọn nhân viên
> nhóm chọn kỹ thuật viên

Dạ đặt từ 2 người trở lên thì hệ thống không cho chỉ định nhân viên ạ, cửa hàng sẽ sắp
người phù hợp để cả nhóm được phục vụ cùng giờ. Chỉ khi đặt một mình anh/chị mới chọn
được người ạ.

## Nhóm đi cùng có chọn dịch vụ khác nhau được không
> nhóm chọn gói khác nhau
> mỗi người một dịch vụ 
> nhóm khác gói
> add-on riêng từng người
> mỗi người thêm món khác
> nhóm chọn thêm riêng

Dạ cả nhóm dùng chung một gói chính và chung một bộ add-on, cùng khung giờ ạ. Ai muốn
dịch vụ khác thì mình tách ra đặt thành lịch riêng giúp em ạ.

## Chỉ định nhân viên cụ thể
> chọn nhân viên
> chỉ định kỹ thuật viên
> chọn người phục vụ
> yêu cầu nhân viên nữ

Dạ anh/chị đặt một mình thì chỉ định được người ạ. Em sẽ chỉ hiện những khung giờ mà
người đó thực sự có ca làm và còn trống, nên danh sách giờ có thể ít hơn bình thường ạ.

## Add-on đặt riêng một mình được không
> add on riêng
> chỉ đặt add-on
> đặt mỗi add on

Dạ add-on phải đi kèm một gói chính, không đặt riêng được ạ. Anh/chị chọn gói chính
trước, rồi em mời thêm add-on sau ạ.

## Vì sao có add-on không chọn được
> add on bị mờ
> không chọn được add on
> add on không hiện
> cái kia bị mờ chọn không được

Dạ một số add-on không kết hợp được với gói chính đang chọn nên em ẩn bớt ạ. Anh/chị đổi
gói chính khác thì danh sách add-on cũng đổi theo ạ.

## Mã đặt chỗ gửi ở đâu, dùng để làm gì
> mã đặt chỗ
> booking code
> mã xác nhận gửi ở đâu
> xem lại lịch đã đặt ở đâu
> tra cứu đơn đã đặt

Dạ đặt xong hệ thống gửi mã đặt chỗ về email anh/chị đăng ký ạ. Mã đó cùng với email dùng
để tra cứu, sửa hoặc hủy lịch trên trang quản lý đặt chỗ ạ.

## Quên hoặc mất mã đặt chỗ
> quên mã đặt chỗ
> mất mã đặt chỗ
> không tìm thấy mã đặt chỗ

Dạ anh/chị kiểm tra lại email đã dùng lúc đặt giúp em, mã nằm trong thư xác nhận ạ. Nếu
vẫn không thấy thì anh/chị gọi cửa hàng, báo email và số điện thoại để bên em tra giúp ạ.

## Vì sao cần số điện thoại khi đặt
> tại sao cần số điện thoại
> bắt buộc số điện thoại
> không cho số điện thoại

Dạ số điện thoại dùng để tra thông tin thành viên và xác nhận khi cần liên hệ gấp ạ. Còn
mã đặt chỗ thì gửi qua email, nên em xin cả hai giúp ạ.

## Không đặt được, hệ thống báo từ chối
> bị từ chối đặt
> không tạo được booking
> hệ thống chặn

Dạ có trường hợp số điện thoại nằm trong danh sách hạn chế của cửa hàng nên hệ thống
không tạo lịch tự động được ạ. Anh/chị liên hệ trực tiếp cửa hàng để được hỗ trợ giúp em ạ.

## Thành viên, hạng thành viên có ưu đãi gì
> hạng thành viên
> thành viên có giảm giá
> khách quen có ưu đãi
> tích điểm

Dạ hạng thành viên hiện chỉ hiển thị để cửa hàng nhận biết khách quen thôi ạ, chưa ảnh
hưởng tới giá hay quyền đặt chỗ. Mỗi lần dùng dịch vụ xong hệ thống tự cộng thêm một lượt
ghé cho anh/chị ạ.

## Cần đặt trước bao lâu, đặt trong ngày được không
> đặt trong ngày
> đặt hôm nay luôn
> đặt gấp
> đặt sát giờ
> cần đặt trước bao lâu
> chiều nay ghé liền

Dạ anh/chị đặt trong ngày vẫn được, miễn là còn khung giờ trống ạ. Em hiện giờ theo lịch
thực tế của nhân viên nên giờ nào hiện ra là giờ đó đặt được ạ.

## Vì sao giờ vừa chọn lại báo hết chỗ
> báo hết chỗ
> giờ vừa chọn hết chỗ
> slot bị mất

Dạ khung giờ được tính theo thời gian thực nên có thể vừa có khách khác đặt trước ạ. Em
gợi ý ngay mấy giờ gần đó còn trống để anh/chị chọn lại giúp em ạ.

## Đến muộn thì sao
> đến muộn
> tới trễ
> đi trễ
> trễ giờ hẹn

Dạ anh/chị báo sớm cho cửa hàng giúp em ạ, vì mỗi nhân viên chỉ phục vụ một khách trong
một khung giờ nên lịch phía sau sẽ bị ảnh hưởng. Nếu muộn nhiều thì mình đổi sang giờ
khác sẽ thoải mái hơn ạ.

## Thời lượng dịch vụ tính thế nào
> thời lượng gói
> gói bao nhiêu phút
> dịch vụ kéo dài bao lâu

Dạ thời lượng đã nằm sẵn trong tên gói anh/chị chọn ạ, các gói đều tính theo bội số 15
phút. Anh/chị chọn gói là em biết luôn thời lượng, không cần nhập riêng ạ.

## Muốn gặp nhân viên tư vấn, nói chuyện với người thật
> gặp người thật
> gặp nhân viên tư vấn
> nói chuyện với người

Dạ anh/chị nhắn em "gặp nhân viên" là em chuyển giúp ạ, hoặc gọi thẳng số của cửa hàng
cũng được ạ.
