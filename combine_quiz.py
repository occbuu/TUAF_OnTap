"""
combine_quiz.py
---------------
Đọc các file Moodle quiz HTML, trích xuất câu hỏi,
lưu dưới dạng JSON nhúng trong HTML, hỗ trợ search client-side.

Cách dùng:
    python -X utf8 combine_quiz.py                          # quét LT*.html → OnTap_DayDu.html
    python -X utf8 combine_quiz.py LT11.html LT12.html      # thêm file cụ thể
    python -X utf8 combine_quiz.py LT_A01.html LT_A02.html  # môn A → OnTap_A.html
    python -X utf8 combine_quiz.py -s "Dược Lý" -o Out.html LT_A01.html

Ảnh trong đề (nếu có) được lưu vào thư mục images/ và gắn field tùy chọn
"images" trong JSON — câu không có ảnh giữ nguyên schema/cách hiển thị cũ.
Tài khoản eTUAF (.env): chỉ đăng nhập khi đề có ảnh chưa có file local.

Yêu cầu:
    pip install beautifulsoup4
"""

import sys, re, os, glob, json, argparse, hashlib, shutil, base64
from pathlib import Path
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import Request, urlopen
from bs4 import BeautifulSoup

# Fix UTF-8 console trên Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

SCRIPT_DIR      = Path(__file__).parent
DEFAULT_SUBJECT = "Kỹ Thuật Bào Chế Sinh Dược Học"
DEFAULT_OUTPUT  = "OnTap_DayDu.html"


def load_dotenv(path: Path | None = None) -> None:
    """Đọc file .env cạnh script (không ghi đè biến môi trường đã có)."""
    env_path = path or (SCRIPT_DIR / ".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val

_JS_STRING_ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
    "\\": "\\", '"': '"', "'": "'", "0": "\0",
}


def _normalize_js_strings(text: str) -> str:
    """Rewrite mọi chuỗi (single/double-quote, kiểu JS) thành chuỗi JSON
    double-quote hợp lệ. Cần vì trình soạn thảo (VD: format-on-save) có
    thể format lại khối QUESTIONS thành object-literal JS."""
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"' or c == "'":
            quote = c
            i += 1
            chars = []
            while i < n and text[i] != quote:
                if text[i] == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    if nxt == "u" and i + 6 <= n:
                        chars.append(chr(int(text[i + 2:i + 6], 16)))
                        i += 6
                    elif nxt in _JS_STRING_ESCAPES:
                        chars.append(_JS_STRING_ESCAPES[nxt])
                        i += 2
                    else:
                        chars.append(nxt)
                        i += 2
                else:
                    chars.append(text[i])
                    i += 1
            i += 1  # bỏ qua dấu ngoặc kép đóng
            out.append(json.dumps("".join(chars), ensure_ascii=False))
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _repair_js_object_to_json(text: str) -> str:
    """Chuyển object-literal kiểu JS (key không quote, dấu phẩy cuối,
    single-quote) thành JSON hợp lệ để json.loads() đọc được."""
    text = _normalize_js_strings(text)
    parts = re.split(r'("(?:\\.|[^"\\])*")', text)
    for idx in range(0, len(parts), 2):  # chỉ số chẵn = ngoài chuỗi
        seg = parts[idx]
        seg = re.sub(r"([{,]\s*)([A-Za-z_$][\w$]*)(\s*:)", r'\1"\2":', seg)
        seg = re.sub(r",(\s*[}\]])", r"\1", seg)
        parts[idx] = seg
    return "".join(parts)


# ══════════════════════════════════════════════
# 1. HELPERS
# ══════════════════════════════════════════════

def get_text(tag) -> str:
    return tag.get_text(" ", strip=True) if tag else ""

def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


# ── Ảnh trong đề Moodle (field JSON tùy chọn, không bắt buộc) ──
_HTTP_OPENER = None  # gán sau khi đăng nhập Moodle (chỉ khi có ảnh cần tải)
_MOODLE_USER = None
_MOODLE_PASS = None
_LOGIN_TRIED = False
_IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
_SKIP_IMG_RE = re.compile(
    r"(theme/|/user/icon|favicon|i/loading|i/notifications|/t/message|/t/markasread)",
    re.I,
)


def _normalize_src(src: str) -> str:
    src = (src or "").strip()
    if src.startswith("//"):
        src = "https:" + src
    return src


def _is_ui_icon(src: str) -> bool:
    return bool(_SKIP_IMG_RE.search(src or ""))


def _extract_img_srcs(tag) -> list[str]:
    if not tag:
        return []
    out = []
    seen = set()
    for img in tag.find_all("img"):
        src = _normalize_src(img.get("src") or "")
        if not src or _is_ui_icon(src) or src in seen:
            continue
        seen.add(src)
        out.append(src)
    return out


def _is_image_bytes(data: bytes, content_type: str = "") -> bool:
    if not data or len(data) < 24:
        return False
    ct = (content_type or "").lower()
    if "text/html" in ct or "application/json" in ct:
        return False
    head = data.lstrip()[:16].lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        return False
    if data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8\xff") or data.startswith(b"GIF8"):
        return True
    if data.startswith(b"RIFF") and b"WEBP" in data[8:16]:
        return True
    return "image/" in ct


def _rel_image_path(src: str) -> str:
    parsed = urlparse(src)
    name = Path(unquote(parsed.path)).name or "image.png"
    stem, ext = os.path.splitext(name)
    if ext.lower() not in _IMG_EXT:
        ext = ".png"
    digest = hashlib.md5(src.encode("utf-8")).hexdigest()[:10]
    return f"images/{stem}_{digest}{ext.lower()}"


def _find_sidecar_image(html_path: Path, src: str) -> Path | None:
    """Ảnh đi kèm khi lưu Moodle dạng Webpage, Complete (`LT_xx_files/`)."""
    name = Path(unquote(urlparse(src).path)).name
    candidates: list[Path] = []
    if name:
        files_dir = html_path.parent / f"{html_path.stem}_files"
        if files_dir.is_dir():
            candidates.append(files_dir / name)
            candidates.extend(files_dir.rglob(name))
    if src and not src.lower().startswith("http"):
        candidates.append((html_path.parent / src).resolve())
    for cand in candidates:
        try:
            if cand.is_file() and cand.stat().st_size > 50:
                return cand
        except OSError:
            continue
    return None


def configure_moodle_auth(username: str | None, password: str | None) -> None:
    """Chỉ lưu tài khoản — chưa đăng nhập cho đến khi gặp ảnh cần tải."""
    global _MOODLE_USER, _MOODLE_PASS
    _MOODLE_USER = username or None
    _MOODLE_PASS = password or None


def ensure_moodle_login() -> bool:
    """Đăng nhập Moodle một lần, chỉ gọi khi có ảnh HTTP chưa có file local."""
    global _LOGIN_TRIED
    if _HTTP_OPENER is not None:
        return True
    if _LOGIN_TRIED:
        return False
    _LOGIN_TRIED = True
    if not _MOODLE_USER or not _MOODLE_PASS:
        print("  [!] Có ảnh Moodle nhưng chưa có .env (MOODLE_USER / MOODLE_PASS).")
        return False
    print(f"  Có ảnh đề — đăng nhập eTUAF bằng user {_MOODLE_USER}")
    return moodle_login(_MOODLE_USER, _MOODLE_PASS)


def moodle_login(username: str, password: str, base: str = "https://tuaf.aum.edu.vn") -> bool:
    """Đăng nhập eTUAF để tải ảnh pluginfile. Không ghi mật khẩu ra đĩa."""
    global _HTTP_OPENER
    import http.cookiejar
    from html.parser import HTMLParser
    from urllib.request import HTTPCookieProcessor, build_opener

    class _TokenFinder(HTMLParser):
        def __init__(self):
            super().__init__()
            self.token = ""

        def handle_starttag(self, tag, attrs):
            d = dict(attrs)
            if tag == "input" and d.get("name") == "logintoken":
                self.token = d.get("value") or ""

    jar = http.cookiejar.CookieJar()
    opener = build_opener(HTTPCookieProcessor(jar))
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": f"{base}/login/index.php",
    }
    try:
        login_html = opener.open(
            Request(f"{base}/login/index.php", headers=headers), timeout=20
        ).read().decode("utf-8", "replace")
        finder = _TokenFinder()
        finder.feed(login_html)
        if not finder.token:
            print("  [!] Không lấy được logintoken Moodle.")
            return False
        payload = urlencode({
            "anchor": "",
            "logintoken": finder.token,
            "username": username,
            "password": password,
        }).encode()
        resp = opener.open(
            Request(f"{base}/login/index.php", data=payload, headers=headers),
            timeout=20,
        )
        body = resp.read().decode("utf-8", "replace")
        ok = "/login" not in resp.geturl() or "logintoken" not in body.lower()
        if not ok:
            print("  [!] Đăng nhập Moodle thất bại (sai tài khoản hoặc bị chặn).")
            return False
        _HTTP_OPENER = opener
        print("  Đăng nhập Moodle thành công — sẽ tải ảnh đề nếu có.")
        return True
    except Exception as e:
        print(f"  [!] Lỗi đăng nhập Moodle: {e}")
        return False


def materialize_image(src: str, html_path: Path) -> str | None:
    """Lưu ảnh vào images/ nếu có file local hoặc tải được; trả về path tương đối."""
    rel = _rel_image_path(src)
    dest = SCRIPT_DIR / rel
    if dest.is_file() and dest.stat().st_size > 50:
        return rel.replace("\\", "/")

    sidecar = _find_sidecar_image(html_path, src)
    if sidecar:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sidecar, dest)
        return rel.replace("\\", "/")

    if src.lower().startswith("http"):
        # Chỉ login khi thực sự phải tải ảnh từ Moodle
        if "tuaf.aum.edu.vn" in src.lower() or "pluginfile.php" in src.lower():
            ensure_moodle_login()
        try:
            req = Request(
                src,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://tuaf.aum.edu.vn/",
                },
            )
            if _HTTP_OPENER is not None:
                resp = _HTTP_OPENER.open(req, timeout=20)
            else:
                resp = urlopen(req, timeout=15)
            with resp:
                data = resp.read()
                ct = resp.headers.get("Content-Type", "")
            if _is_image_bytes(data, ct):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                print(f"    tải ảnh: {rel} ({len(data)} bytes)")
                return rel.replace("\\", "/")
        except Exception as e:
            print(f"    [!] Không tải được ảnh: {e}")
    return None


def html_image_src(src: str) -> str:
    """Đổi file local thành data URI để HTML hiện ảnh dù mở file:// hay preview."""
    if not src or src.startswith("data:"):
        return src
    p = SCRIPT_DIR / str(src).replace("\\", "/")
    if not p.is_file() or p.stat().st_size < 50:
        return src
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }.get(p.suffix.lower(), "image/png")
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


# ══════════════════════════════════════════════
# 2. PARSE MOODLE HTML
# ══════════════════════════════════════════════

def parse_moodle_file(filepath: str) -> list[dict]:
    """Trích xuất câu hỏi từ file Moodle quiz review HTML."""
    html_path = Path(filepath)
    with open(filepath, encoding="utf-8", errors="replace") as f:
        soup = BeautifulSoup(f, "html.parser")

    questions = []
    for qblock in soup.find_all("div", id=re.compile(r"^question-")):
        qtext_tag = qblock.find("div", class_="qtext")
        if not qtext_tag:
            continue
        question_text = get_text(qtext_tag)
        if not question_text:
            continue

        images = []
        for src in _extract_img_srcs(qtext_tag):
            local = materialize_image(src, html_path)
            images.append(local or src)

        answer_div = qblock.find("div", class_="answer")
        if not answer_div:
            continue
        choices = []
        for ctag in answer_div.find_all("div", class_=re.compile(r"\br\d\b")):
            for inp in ctag.find_all("input"):
                inp.decompose()
            choices.append(get_text(ctag))
        if not choices:
            continue

        right_tag = qblock.find("div", class_="rightanswer")
        right_answer = ""
        if right_tag:
            right_answer = get_text(right_tag)
            right_answer = re.sub(
                r"^(câu trả lời đúng là\s*[:\-–]?\s*|the correct answer is\s*[:\-–]?\s*)",
                "", right_answer, flags=re.IGNORECASE
            ).strip()

        correct_index = -1
        norm_right = normalize(right_answer)
        for i, ch in enumerate(choices):
            if norm_right and normalize(ch) == norm_right:
                correct_index = i; break
        if correct_index == -1 and norm_right:
            for i, ch in enumerate(choices):
                if norm_right in normalize(ch) or normalize(ch) in norm_right:
                    correct_index = i; break

        feedback = ref = ""
        fb_tag = qblock.find("div", class_="generalfeedback")
        if fb_tag:
            fb_text = get_text(fb_tag)
            ref_m = re.search(r"tham khảo\s*[:\-–]?\s*(mục\s*[\d\.]+)", fb_text, re.IGNORECASE)
            if ref_m:
                ref     = ref_m.group(1)
                fb_text = fb_text[:ref_m.start()].strip()
            feedback = re.sub(
                r"^(phản hồi\s*[:\-–]?\s*|feedback\s*[:\-–]?\s*)",
                "", fb_text, flags=re.IGNORECASE
            ).strip()

        questions.append({
            "question_text": question_text,
            "answers":       choices,
            "correct_index": correct_index,
            "right_answer":  right_answer,
            "feedback":      feedback,
            "reference":     ref,
            "images":        images,
        })

    return questions


# ══════════════════════════════════════════════
# 3. ĐỌC OUTPUT HIỆN CÓ (đọc từ JSON nhúng)
# ══════════════════════════════════════════════

def read_existing_output(filepath: Path):
    """Trả về (questions_list | [], existing_text_set, last_id)."""
    if not filepath.exists():
        return [], set(), 0

    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    # Tìm JSON nhúng: const QUESTIONS = [...];
    m = re.search(r"const\s+QUESTIONS\s*=\s*(\[.*?\]);", content, re.DOTALL)
    if not m:
        # Fallback: đọc từ HTML blocks cũ
        soup = BeautifulSoup(content, "html.parser")
        existing_texts = set()
        last_id = 0
        for blk in soup.find_all("div", class_="question-block"):
            qtag = blk.find("div", class_="question-text")
            if qtag:
                existing_texts.add(normalize(get_text(qtag)))
            mid = re.match(r"q(\d+)", blk.get("id", ""))
            if mid:
                last_id = max(last_id, int(mid.group(1)))
        return [], existing_texts, last_id

    raw = m.group(1)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Khối QUESTIONS có thể đã bị trình soạn thảo format lại thành
        # object-literal JS (key không quote, dấu phẩy cuối...). Thử
        # "sửa" nó về JSON hợp lệ trước khi bỏ cuộc.
        try:
            data = json.loads(_repair_js_object_to_json(raw))
        except json.JSONDecodeError as e:
            print(f"\n[LỖI] Không đọc được dữ liệu câu hỏi đã có trong {filepath.name}!")
            print(f"       {e}")
            print("       Để tránh mất dữ liệu, script sẽ DỪNG LẠI thay vì ghi đè.")
            print("       Hãy kiểm tra/khôi phục file này trước khi chạy lại "
                  "(vd: file có thể đã bị trình soạn thảo format lại).")
            sys.exit(1)

    existing_texts = {normalize(q["text"]) for q in data}
    last_id = max((q["id"] for q in data), default=0)
    return data, existing_texts, last_id


# ══════════════════════════════════════════════
# 4. CHUYỂN CÂU HỎI → DICT CHO JSON
# ══════════════════════════════════════════════

def make_question_dict(q: dict, number: int) -> dict:
    d = {
        "id":          number,
        "text":        q["question_text"],
        "answers":     q["answers"],
        "correct":     q["correct_index"],   # 0-based, -1 nếu không xác định
        "explanation": q["feedback"],
        "reference":   q["reference"],
    }
    # Chỉ thêm khi có ảnh — câu cũ không có field này vẫn parse/hiển thị như trước
    if q.get("images"):
        d["images"] = [html_image_src(s) for s in q["images"]]
    return d


# ══════════════════════════════════════════════
# 5. TẠO FILE HTML (template không dùng .format() để tránh lỗi JS)
# ══════════════════════════════════════════════

def build_html(subject: str, sources: str, questions: list[dict]) -> str:
    count       = len(questions)
    json_data   = json.dumps(questions, ensure_ascii=False, indent=None, separators=(",", ":"))

    # Dùng thay thế chuỗi thay vì .format() để tránh xung đột {} trong JS/CSS
    html = _HTML_TEMPLATE
    html = html.replace("__SUBJECT__",  subject)
    html = html.replace("__SOURCES__",  sources)
    html = html.replace("__COUNT__",    str(count))
    html = html.replace("__JSON__",     json_data)
    return html


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ôn Tập - __SUBJECT__ (__COUNT__ câu)</title>
<style>
:root {
  --blue:      #2563eb;
  --blue-dark: #1e40af;
  --blue-bg:   #eff6ff;
  --green:     #16a34a;
  --green-bg:  #f0fdf4;
  --green-bd:  #86efac;
  --yellow-bg: #fefce8;
  --yellow-bd: #fde047;
  --gray:      #6b7280;
  --gray-lt:   #f3f4f6;
  --border:    #e5e7eb;
  --text:      #111827;
  --radius:    12px;
  --shadow:    0 1px 4px rgba(0,0,0,.08),0 4px 12px rgba(0,0,0,.06);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
  background: #f1f5f9; color: var(--text);
  line-height: 1.65; font-size: 15px;
}

/* ── HEADER ── */
.page-header {
  background: linear-gradient(135deg, var(--blue-dark) 0%, var(--blue) 100%);
  color: #fff; padding: 28px 20px 22px; text-align: center;
  position: relative;
}
.page-header h1 { font-size: clamp(1.1em, 4vw, 1.7em); font-weight: 700; margin-bottom: 5px; }
.page-header p  { opacity: .85; font-size: .88em; }
.back-btn {
  position: absolute; left: 14px; top: 50%; transform: translateY(-50%);
  background: rgba(255,255,255,.18); border: 1.5px solid rgba(255,255,255,.35);
  color: #fff; padding: 6px 13px; border-radius: 7px;
  text-decoration: none; font-size: .82em; font-weight: 600;
  white-space: nowrap; transition: background .15s; line-height: 1.4;
}
.back-btn:hover { background: rgba(255,255,255,.3); }
@media (max-width: 480px) {
  .back-btn { position: static; transform: none; display: inline-block;
              margin-bottom: 10px; font-size: .8em; }
  .page-header { padding-top: 16px; }
}

/* ── SEARCH BAR ── */
.search-wrap {
  position: sticky; top: 0; z-index: 100;
  background: #fff; border-bottom: 1px solid var(--border);
  padding: 10px 16px;
  box-shadow: 0 2px 8px rgba(0,0,0,.08);
}
.search-inner {
  max-width: 860px; margin: 0 auto;
  display: flex; align-items: center; gap: 10px;
}
.search-box { flex: 1; position: relative; }
.search-box input {
  width: 100%; padding: 9px 36px 9px 38px;
  border: 1.5px solid var(--border); border-radius: 8px;
  font-size: .95em; outline: none; background: var(--gray-lt);
  transition: border-color .15s, background .15s;
  -webkit-appearance: none;
}
.search-box input:focus { border-color: var(--blue); background: #fff; }
.search-icon {
  position: absolute; left: 11px; top: 50%;
  transform: translateY(-50%); color: var(--gray);
  pointer-events: none; font-size: 1em;
}
.search-clear {
  position: absolute; right: 9px; top: 50%;
  transform: translateY(-50%); background: none; border: none;
  cursor: pointer; color: var(--gray); font-size: 1.1em;
  padding: 2px 4px; display: none; line-height: 1; border-radius: 50%;
}
.search-clear:hover { color: #111; background: var(--gray-lt); }
.search-count {
  white-space: nowrap; font-size: .88em; color: var(--gray);
  min-width: 90px; text-align: right;
}
.search-count strong { color: var(--blue-dark); }

/* ── CONTAINER ── */
.container {
  max-width: 860px; margin: 18px auto;
  padding: 0 14px 80px;
}

/* ── QUESTION CARD ── */
.question-block {
  background: #fff; border-radius: var(--radius);
  box-shadow: var(--shadow); margin-bottom: 16px;
  border-left: 4px solid var(--blue);
  overflow: hidden;
  transition: box-shadow .15s;
}
.question-block:hover {
  box-shadow: 0 2px 8px rgba(0,0,0,.12), 0 6px 20px rgba(0,0,0,.08);
}

.question-header {
  display: flex; align-items: center; gap: 8px;
  background: var(--blue-bg); padding: 8px 14px;
  border-bottom: 1px solid #dbeafe;
}
.q-badge {
  background: var(--blue); color: #fff;
  border-radius: 20px; padding: 1px 9px;
  font-size: .78em; font-weight: 700; letter-spacing: .3px;
}

.question-text {
  padding: 14px 16px 8px;
  font-size: 1em; font-weight: 500; color: #1e293b;
}
.question-text mark {
  background: #fef08a; color: inherit;
  border-radius: 3px; padding: 0 2px;
}
.q-images { padding: 6px 16px 12px; }
.q-images img {
  max-width: 100%; height: auto; display: block;
  border-radius: 8px; border: 1.5px solid var(--border);
  background: #fff; margin-top: 6px;
}

/* ── ANSWERS ── */
.answers {
  padding: 4px 16px 12px;
  display: flex; flex-direction: column; gap: 6px;
}
.answer-item {
  display: flex; align-items: flex-start; gap: 10px;
  padding: 8px 12px; border-radius: 8px;
  border: 1.5px solid var(--border);
  background: var(--gray-lt);
}
.answer-item.correct {
  background: var(--green-bg);
  border-color: var(--green-bd);
  font-weight: 600;
}
.answer-letter {
  font-weight: 700; font-size: .9em;
  width: 22px; flex-shrink: 0; padding-top: 1px; color: var(--gray);
}
.answer-item.correct .answer-letter { color: var(--green); }
.answer-text {
  flex: 1; color: #374151; font-size: .95em;
}
.answer-item.correct .answer-text { color: #14532d; }
.answer-text mark {
  background: #bbf7d0; color: inherit;
  border-radius: 3px; padding: 0 2px;
}
.check-mark {
  flex-shrink: 0; margin-left: auto; padding-left: 6px;
  color: var(--green); font-weight: 700;
}

/* ── FEEDBACK ── */
.feedback-section {
  background: var(--yellow-bg);
  border-top: 1.5px solid var(--yellow-bd);
  padding: 10px 16px; font-size: .875em; color: #44403c;
}
.feedback-section p { margin: 3px 0; }
.feedback-section strong { color: #1c1917; }

/* ── EMPTY STATE ── */
#noResults {
  display: none; text-align: center;
  padding: 60px 20px; color: var(--gray);
}
#noResults .nr-icon { font-size: 2.5em; margin-bottom: 12px; }
#noResults p { font-size: .95em; }

/* ── SCROLL TOP ── */
#scrollTopBtn {
  position: fixed; bottom: 20px; right: 16px;
  width: 44px; height: 44px; border-radius: 50%;
  background: var(--blue); color: #fff; border: none;
  font-size: 1.3em; cursor: pointer; display: none;
  align-items: center; justify-content: center;
  box-shadow: 0 3px 10px rgba(37,99,235,.4);
  transition: background .15s, transform .15s;
  z-index: 200; text-decoration: none;
  line-height: 44px; text-align: center;
}
#scrollTopBtn:hover { background: var(--blue-dark); transform: translateY(-2px); }

/* ── MOBILE ── */
@media (max-width: 600px) {
  body { font-size: 14px; }
  .page-header { padding: 18px 14px 14px; }
  .container { margin: 12px auto; padding: 0 10px 80px; }
  .question-text { padding: 12px 12px 6px; }
  .q-images { padding: 4px 12px 10px; }
  .answers { padding: 4px 12px 10px; gap: 5px; }
  .answer-item { padding: 7px 10px; }
  .feedback-section { padding: 9px 12px; }
  .search-count { display: none; }
}
</style>
</head>
<body>

<div class="page-header">
  <a href="index.html" class="back-btn">&#8592; Trang ch&#7911;</a>
  <h1>Ôn Tập __SUBJECT__</h1>
  <p>Câu hỏi trắc nghiệm có đáp án &mdash; Nguồn: __SOURCES__</p>
</div>

<div class="search-wrap">
  <div class="search-inner">
    <div class="search-box">
      <span class="search-icon">&#128269;</span>
      <input type="search" id="searchInput"
             placeholder="Tìm câu hỏi hoặc đáp án..." autocomplete="off">
      <button class="search-clear" id="searchClear" title="Xóa">&#10005;</button>
    </div>
    <div class="search-count" id="searchCount">
      <strong>__COUNT__</strong> / __COUNT__ câu
    </div>
  </div>
</div>

<div class="container" id="questionContainer">
  <div id="noResults">
    <div class="nr-icon">&#128269;</div>
    <p>Không tìm thấy câu hỏi phù hợp.</p>
  </div>
</div>

<a href="#" id="scrollTopBtn" title="Lên đầu trang">&#8679;</a>

<script>
const QUESTIONS = __JSON__;
</script>

<script>
(function () {
  var LETTERS = "ABCDEFGHIJ";
  var container = document.getElementById("questionContainer");
  var noResults = document.getElementById("noResults");
  var searchInput = document.getElementById("searchInput");
  var searchClear = document.getElementById("searchClear");
  var countEl = document.getElementById("searchCount");
  var total = QUESTIONS.length;

  /* ── Escape HTML ── */
  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /* ── Highlight từ khóa ── */
  function highlight(text, term) {
    if (!term) return esc(text);
    var safe = term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return esc(text).replace(
      new RegExp("(" + safe.replace(/&amp;/g, "&amp;amp;") + ")", "gi"),
      "<mark>$1</mark>"
    );
  }

  /* ── Render 1 câu hỏi ── */
  function renderQ(q, term) {
    var answersHtml = q.answers.map(function (ans, i) {
      var isCorrect = i === q.correct;
      var cls = "answer-item" + (isCorrect ? " correct" : "");
      var check = isCorrect ? '<span class="check-mark">&#10003;</span>' : "";
      return (
        '<div class="' + cls + '">' +
        '<span class="answer-letter">' + (LETTERS[i] || i + 1) + ".</span>" +
        '<span class="answer-text">' + highlight(ans, term) + "</span>" +
        check +
        "</div>"
      );
    }).join("");

    var feedbackHtml = "";
    if (q.explanation || q.reference) {
      feedbackHtml =
        '<div class="feedback-section">' +
        (q.explanation
          ? "<p><strong>Vì: </strong>" + esc(q.explanation) + "</p>"
          : "") +
        (q.reference
          ? "<p><strong>Tham khảo: </strong>" + esc(q.reference) + "</p>"
          : "") +
        "</div>";
    }

    var imagesHtml = "";
    if (q.images && q.images.length) {
      imagesHtml =
        '<div class="q-images">' +
        q.images.map(function (src) {
          return (
            '<img src="' + esc(src) + '" alt="" loading="lazy" ' +
            'onerror="this.style.display=\'none\'">'
          );
        }).join("") +
        "</div>";
    }

    return (
      '<div class="question-block" id="q' + q.id + '">' +
      '<div class="question-header">' +
      '<span class="q-badge">Câu ' + q.id + "</span>" +
      "</div>" +
      '<div class="question-text">' + highlight(q.text, term) + "</div>" +
      imagesHtml +
      '<div class="answers">' + answersHtml + "</div>" +
      feedbackHtml +
      "</div>"
    );
  }

  /* ── Render danh sách câu hỏi ── */
  function renderList(filtered, term) {
    // Xóa các block cũ (giữ lại #noResults)
    var old = container.querySelectorAll(".question-block");
    old.forEach(function (el) { el.remove(); });

    if (filtered.length === 0) {
      noResults.style.display = "block";
    } else {
      noResults.style.display = "none";
      var frag = document.createDocumentFragment();
      filtered.forEach(function (q) {
        var div = document.createElement("div");
        div.innerHTML = renderQ(q, term);
        frag.appendChild(div.firstChild);
      });
      container.appendChild(frag);
    }

    countEl.innerHTML =
      "<strong>" + filtered.length + "</strong> / " + total + " câu";
  }

  /* ── Lọc câu hỏi ── */
  function doSearch(term) {
    var t = term.trim().toLowerCase();
    var filtered = t
      ? QUESTIONS.filter(function (q) {
          return (
            q.text.toLowerCase().includes(t) ||
            q.answers.some(function (a) { return a.toLowerCase().includes(t); })
          );
        })
      : QUESTIONS;

    searchClear.style.display = term ? "block" : "none";
    renderList(filtered, term.trim());
  }

  /* ── Khởi tạo ── */
  renderList(QUESTIONS, "");

  searchInput.addEventListener("input", function () {
    doSearch(this.value);
  });
  searchClear.addEventListener("click", function () {
    searchInput.value = "";
    doSearch("");
    searchInput.focus();
  });

  /* ── Scroll to top ── */
  var scrollBtn = document.getElementById("scrollTopBtn");
  window.addEventListener("scroll", function () {
    scrollBtn.style.display = window.scrollY > 320 ? "flex" : "none";
  }, { passive: true });
  scrollBtn.addEventListener("click", function (e) {
    e.preventDefault();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
})();
</script>
</body>
</html>
"""


# ══════════════════════════════════════════════
# 6. HELPER: prefix & output filename
# ══════════════════════════════════════════════

def detect_prefix(filenames: list[str]) -> str:
    bases   = [re.sub(r"\.html$", "", os.path.basename(f), flags=re.IGNORECASE) for f in filenames]
    stripped = [re.sub(r"[\d_-]+$", "", b) for b in bases]
    prefix  = os.path.commonprefix(stripped).rstrip("_-")
    return prefix or "LT"

def output_from_prefix(prefix: str) -> str:
    if prefix.upper() == "LT":
        return DEFAULT_OUTPUT
    suffix = re.sub(r"^LT[_\-]?", "", prefix, flags=re.IGNORECASE)
    return f"OnTap_{suffix}.html" if suffix else DEFAULT_OUTPUT

def sources_label(filenames: list[str]) -> str:
    labels = sorted(
        re.sub(r"\.html$", "", os.path.basename(f), flags=re.IGNORECASE)
        for f in filenames
    )
    if len(labels) <= 6:
        return " – ".join(labels)
    return f"{labels[0]} – {labels[-1]}"


# ══════════════════════════════════════════════
# 7. MAIN
# ══════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Tổng hợp câu hỏi Moodle quiz HTML thành file ôn tập JSON-powered."
    )
    parser.add_argument("files", nargs="*",
        help="Các file LT*.html (hỗ trợ glob)")
    parser.add_argument("-o", "--output", default=None,
        help="File output HTML (mặc định: tự suy ra từ tên file input)")
    parser.add_argument("-s", "--subject", default=None,
        help=f'Tên môn học (mặc định: "{DEFAULT_SUBJECT}")')
    parser.add_argument("--moodle-user", default=None,
        help="Tài khoản eTUAF để tải ảnh (mật khẩu: .env hoặc MOODLE_PASS)")
    args = parser.parse_args()

    load_dotenv()
    configure_moodle_auth(
        args.moodle_user or os.environ.get("MOODLE_USER"),
        os.environ.get("MOODLE_PASS"),
    )

    # ── Resolve input files ──
    input_files = []
    for pattern in (args.files or []):
        matched = sorted(glob.glob(pattern))
        if matched:
            input_files.extend(matched)
        elif os.path.isfile(pattern):
            input_files.append(pattern)
        else:
            print(f"  [!] Không tìm thấy: {pattern}")
    input_files = sorted(set(input_files))

    if not input_files:
        input_files = sorted(glob.glob(str(SCRIPT_DIR / "LT*.html")))

    if not input_files:
        print("Không có file input. Dùng: python -X utf8 combine_quiz.py LT11.html ...")
        sys.exit(1)

    # ── Output & subject ──
    if args.output:
        output_file = SCRIPT_DIR / args.output
    else:
        output_file = SCRIPT_DIR / output_from_prefix(detect_prefix(input_files))

    subject = args.subject or DEFAULT_SUBJECT

    print(f"\nMôn học    : {subject}")
    print(f"File output: {output_file}")
    print(f"File input : {', '.join(os.path.basename(f) for f in input_files)}\n")

    # ── Đọc dữ liệu đã có ──
    existing_qs, existing_texts, last_id = read_existing_output(output_file)
    if existing_qs:
        print(f"Đã có: {len(existing_qs)} câu (q1–q{last_id})")
    elif existing_texts:
        print(f"Đã có: {len(existing_texts)} câu (định dạng cũ, sẽ rebuild)")
    else:
        print("File output chưa tồn tại, sẽ tạo mới.")

    # ── Trích câu hỏi mới ──
    new_qs     = []
    seen_in_new = set()
    image_map  = {}   # normalize(text) -> [image paths]
    img_ok = img_miss = 0

    for filepath in input_files:
        fname = os.path.basename(filepath)
        try:
            qs = parse_moodle_file(filepath)
        except Exception as e:
            print(f"  [!] Lỗi đọc {fname}: {e}")
            continue

        new_cnt = dup_exist = dup_new = 0
        for q in qs:
            norm = normalize(q["question_text"])
            if q.get("images"):
                image_map[norm] = q["images"]
                for src in q["images"]:
                    s = str(src)
                    if s.startswith("images/") or s.startswith("data:"):
                        img_ok += 1
                    else:
                        img_miss += 1
            if norm in existing_texts:
                dup_exist += 1
            elif norm in seen_in_new:
                dup_new += 1
            else:
                new_qs.append(q)
                seen_in_new.add(norm)
                new_cnt += 1

        print(f"  {fname}: {len(qs)} câu | mới: {new_cnt} | trùng output: {dup_exist} | trùng nhau: {dup_new}")

    print(f"\nTổng câu mới: {len(new_qs)}")
    if image_map:
        print(f"Ảnh đề: {len(image_map)} câu có hình | local: {img_ok} | chưa tải được: {img_miss}")
        if img_miss:
            print("  [!] Moodle chặn tải ảnh khi chưa đăng nhập.")
            print("      Lưu lại trang dạng 'Webpage, Complete' (có thư mục *_files) rồi chạy lại.")

    # Gắn ảnh vào câu đã có (không đổi id/text/đáp án)
    images_updated = 0
    for eq in existing_qs:
        imgs = image_map.get(normalize(eq.get("text", "")))
        if not imgs:
            continue
        imgs = [html_image_src(s) for s in imgs]
        if eq.get("images") != imgs:
            eq["images"] = imgs
            images_updated += 1
    if images_updated:
        print(f"Đã bổ sung ảnh cho {images_updated} câu đã có.")

    imaged_ids = [eq["id"] for eq in existing_qs if eq.get("images")]
    if imaged_ids:
        print("Câu có hình: " + ", ".join(f"q{i}" for i in imaged_ids))

    # Không có câu mới: vẫn ghi lại nếu vừa gắn ảnh, hoặc nếu file chưa có renderer ảnh
    need_rewrite = bool(new_qs) or bool(images_updated)
    if not need_rewrite and existing_qs and image_map:
        old_html = output_file.read_text(encoding="utf-8") if output_file.exists() else ""
        if ".q-images" not in old_html or "q.images" not in old_html:
            need_rewrite = True
            print("File output thiếu phần hiện ảnh — sẽ ghi lại.")
        else:
            print("Không có câu mới. Ảnh đã có trong file — mở và kéo tới câu có hình.")
            return

    if not need_rewrite and existing_qs:
        print("Không có câu mới. File output giữ nguyên.")
        return

    # ── Ghép dữ liệu cũ + mới ──
    all_dicts = list(existing_qs)  # giữ nguyên dữ liệu cũ từ JSON

    start_id = last_id + 1
    for i, q in enumerate(new_qs):
        all_dicts.append(make_question_dict(q, start_id + i))

    # ── Cập nhật sources ──
    new_src = sources_label(input_files)
    if existing_qs:
        # Trích sources cũ từ file
        with open(output_file, encoding="utf-8") as f:
            old_content = f.read()
        src_m = re.search(r"Nguồn:\s*(.+?)</p>", old_content)
        old_src = src_m.group(1).strip() if src_m else new_src
        sources  = old_src + " – " + new_src if old_src != new_src else old_src
    else:
        sources = new_src

    # ── Ghi file ──
    html = build_html(subject, sources, all_dicts)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\nDone! → {output_file}")
    print(f"Tổng: {len(all_dicts)} câu ({len(existing_qs)} cũ + {len(new_qs)} mới)")


if __name__ == "__main__":
    main()
