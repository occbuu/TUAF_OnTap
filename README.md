# Ôn Tập - Tổng Hợp Câu Hỏi Trắc Nghiệm

Công cụ tổng hợp câu hỏi từ các file Moodle quiz HTML, tự động loại bỏ câu trùng và tạo file ôn tập có đáp án.

---

## Cấu trúc thư mục

```
OnTap/
├── combine_quiz.py       # Script chính
├── README.md
│
├── OnTap_DayDu.html      # Kết quả môn mặc định (LT*.html)
├── OnTap_A.html          # Kết quả môn A (LT_A*.html)  ← tự tạo
│
├── LT1.html  LT2.html … # File quiz gốc từ Moodle (môn mặc định)
├── LT_A01.html … LT_A06.html  # File quiz môn A
└── ...
```

---

## Cài đặt (1 lần duy nhất)

```bash
pip install beautifulsoup4
```

---

## Cách chạy

### Môn mặc định (LT*.html → OnTap_DayDu.html)

```bash
# Quét tự động tất cả LT*.html trong thư mục
python -X utf8 combine_quiz.py

# Chỉ thêm file cụ thể
python -X utf8 combine_quiz.py LT11.html LT12.html
```

### Môn mới — tự động nhận diện theo tên file

```bash
# LT_A*.html → tự tạo OnTap_A.html
python -X utf8 combine_quiz.py LT_A01.html LT_A02.html LT_A03.html

# LT_B*.html → tự tạo OnTap_B.html
python -X utf8 combine_quiz.py LT_B01.html LT_B02.html
```

> Script đọc prefix của tên file (`LT_A`, `LT_B`, ...) và tự đặt tên output tương ứng.

### Tuỳ chỉnh nâng cao

```bash
# Chỉ định file output rõ ràng
python -X utf8 combine_quiz.py -o MyOutput.html LT_A01.html LT_A02.html

# Đặt tên môn học hiển thị trên trang
python -X utf8 combine_quiz.py -s "Dược Lý Học" LT_A01.html LT_A02.html

# Kết hợp cả hai
python -X utf8 combine_quiz.py -s "Dược Lý Học" -o OnTap_DuocLy.html LT_A01.html
```

### Quy tắc đặt tên output tự động

| Prefix file input | File output tạo ra |
|------------------|--------------------|
| `LT`             | `OnTap_DayDu.html` |
| `LT_A`           | `OnTap_A.html`     |
| `LT_B`           | `OnTap_B.html`     |
| `LT_XYZ`         | `OnTap_XYZ.html`   |

---

## Lấy file quiz từ Moodle

1. Vào trang **Xem lại lần làm thử** của bài quiz trên eTUAF
2. Dùng trình duyệt: **Ctrl+S** → lưu dạng **Webpage, HTML only**
3. Đặt tên file theo quy ước:
   - Môn mặc định: `LT11.html`, `LT12.html`, ...
   - Môn mới A: `LT_A01.html`, `LT_A02.html`, ...
4. Chép vào thư mục `OnTap/`
5. Chạy script

---

## Xem kết quả

Mở file `.html` tương ứng bằng trình duyệt (Chrome, Edge, Firefox).

- **Tìm kiếm real-time**: gõ từ khóa ở thanh search đầu trang, lọc theo đề bài hoặc đáp án
- Từ khóa tìm thấy được **highlight vàng** ngay trong câu hỏi
- Bộ đếm `X / 382 câu` cập nhật theo kết quả tìm kiếm
- Đáp án đúng được **highlight màu xanh lá** với dấu ✓
- Phần giải thích hiển thị nền vàng bên dưới mỗi câu
- Nút cuộn lên đầu trang ở góc dưới phải
- Responsive đầy đủ cho mobile

---

## Kiến trúc kỹ thuật

Mỗi file `.html` nhúng toàn bộ dữ liệu dưới dạng **JSON** trong thẻ `<script>`:

```js
const QUESTIONS = [
  { "id": 1, "text": "Câu hỏi...", "answers": ["A", "B", "C", "D"],
    "correct": 2, "explanation": "Vì...", "reference": "Mục 3.1" },
  ...
];
```

JavaScript render và search trực tiếp trên JSON này — không cần backend, không cần mạng.

---

## Lưu ý

- Chạy nhiều lần **an toàn** — câu đã có sẽ không bị thêm trùng
- Khi update nội dung file cũ (có câu mới), chạy lại với file đó là đủ
- Script **không xoá** câu cũ, chỉ **thêm** câu chưa xuất hiện

---

## Lịch sử

| Thời gian | Files | Câu hỏi duy nhất |
|-----------|-------|-----------------|
| Lần 1 | LT1 – LT5 | 78 câu |
| Lần 2 | + LT6 – LT9 | 116 câu |
| Lần 3 | Update LT3–LT9, thêm LT10 | 375 câu |



Lưu ý:
 - D : Độc chất học thú y
 - E : Dinh dưỡng động vật
 - F : Giải phẫu động vật

Lệnh chạy:
 python -X utf8 combine_quiz.py LT_F01.html LT_F02.html LT_F03.html LT_F04.html

python -X utf8 combine_quiz.py -s "Bệnh truyền nhiễm Thú y" LT_A01.html LT_A02.html  