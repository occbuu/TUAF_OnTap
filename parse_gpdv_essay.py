"""
parse_gpdv_essay.py
-------------------
Parse Docs/OnTap_GPDV.docx → data/OnTap_F_essay.json (+ .js)
Tách câu trắc nghiệm từ OnTap_F.html → data/OnTap_F_mcq.json (+ .js)

Cách dùng:
    python -X utf8 parse_gpdv_essay.py
"""

from __future__ import annotations

import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
DOCX_PATH = SCRIPT_DIR / "Docs" / "OnTap_GPDV.docx"
HTML_PATH = SCRIPT_DIR / "OnTap_F.html"
DATA_DIR = SCRIPT_DIR / "data"

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

Q_HEAD_RE = re.compile(r"^Câu\s+(\d+)\.\s*(.+)$")
SKIP_RE = re.compile(
    r"^(ÔN THI KẾT THÚC|Chúc bạn ôn tập|\(Bản rút gọn)",
    re.IGNORECASE,
)

# Đoạn ngắn kết thúc bằng ':' hoặc mục đánh số → heading
HEADING_RE = re.compile(
    r"^(?:"
    r".{1,80}:$"
    r"|\d+\.\s+.{1,80}$"
    r"|So sánh(?:\s+\S+){0,8}$"
    r"|Khác nhau$"
    r"|Giống nhau:?$"
    r")$"
)

DIAGRAM_Q44 = {
    "type": "diagram",
    "root": "HỆ THẦN KINH GIA SÚC",
    "children": [
        {
            "label": "Thần kinh động vật (TKĐV)",
            "note": "cơ vân, hoạt động tự ý",
            "children": [
                {"label": "Trung ương", "items": ["Não bộ", "Tủy sống"]},
                {
                    "label": "Ngoại biên",
                    "items": [
                        "12 đôi dây TK não",
                        "Các đôi dây TK tủy (cổ, ngực, lưng, hông, khum, đuôi)",
                    ],
                },
            ],
        },
        {
            "label": "Thần kinh thực vật (TKTV)",
            "note": "cơ trơn, cơ tim, tuyến – tự động",
            "children": [
                {
                    "label": "Giao cảm",
                    "note": "kích thích, tiêu hao NL",
                    "items": ["Trung khu", "Hạch", "Dây TK"],
                },
                {
                    "label": "Phó giao cảm",
                    "note": "bảo vệ, tích lũy, giảm hoạt động tim",
                    "items": ["Trung khu", "Hạch", "Dây TK"],
                },
            ],
        },
    ],
}


def _local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def cell_text(tc: ET.Element) -> str:
    parts = []
    for t in tc.findall(".//w:t", NS):
        if t.text:
            parts.append(t.text)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def para_text(p: ET.Element) -> str:
    parts = []
    for t in p.findall(".//w:t", NS):
        if t.text:
            parts.append(t.text)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def parse_table(tbl: ET.Element) -> dict:
    rows = []
    for tr in tbl.findall("w:tr", NS):
        cells = [cell_text(tc) for tc in tr.findall("w:tc", NS)]
        if any(cells):
            rows.append(cells)
    if not rows:
        return {"type": "table", "headers": [], "rows": []}
    return {"type": "table", "headers": rows[0], "rows": rows[1:]}


def is_heading(text: str) -> bool:
    if SKIP_RE.match(text):
        return False
    if Q_HEAD_RE.match(text):
        return False
    if HEADING_RE.match(text):
        # "Xoang phế mạc: là xoang ảo..." là câu giải thích, không phải heading
        if " là " in text.lower() and len(text) > 40:
            return False
        return True
    # Tiêu đề ngắn không có dấu chấm (Môi, Má, Lưỡi, …)
    if len(text) <= 40 and "." not in text and "?" not in text and "→" not in text:
        if not text[0].isdigit():
            return True
    return False


def flatten_blocks(blocks: list[dict]) -> str:
    chunks = []
    for b in blocks:
        t = b.get("type")
        if t in ("heading", "paragraph"):
            chunks.append(b.get("text", ""))
        elif t == "table":
            chunks.extend(b.get("headers") or [])
            for row in b.get("rows") or []:
                chunks.extend(row)
        elif t == "diagram":
            chunks.append(b.get("root", ""))

            def walk(nodes):
                for n in nodes or []:
                    chunks.append(n.get("label", ""))
                    chunks.append(n.get("note", ""))
                    chunks.extend(n.get("items") or [])
                    walk(n.get("children"))

            walk(b.get("children"))
    return " ".join(c for c in chunks if c)


def parse_docx(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    body = root.find("w:body", NS)
    if body is None:
        raise RuntimeError("Không tìm thấy w:body trong docx")

    questions: list[dict] = []
    current: dict | None = None

    def close_current():
        nonlocal current
        if not current:
            return
        current["searchText"] = (
            current["text"] + " " + flatten_blocks(current["blocks"])
        ).lower()
        questions.append(current)
        current = None

    for child in list(body):
        tag = _local(child.tag)
        if tag == "tbl":
            if current is not None:
                current["blocks"].append(parse_table(child))
            continue
        if tag != "p":
            continue

        text = para_text(child)
        if not text:
            continue
        if SKIP_RE.match(text):
            continue

        m = Q_HEAD_RE.match(text)
        if m:
            close_current()
            current = {
                "id": int(m.group(1)),
                "text": m.group(2).strip(),
                "blocks": [],
            }
            continue

        if current is None:
            continue

        # Sơ đồ câu 44: bỏ đoạn ASCII bị dồn 1 dòng, thay bằng diagram
        if current["id"] == 44 and text.startswith("HỆ THẦN KINH GIA SÚC"):
            current["blocks"].append(DIAGRAM_Q44)
            continue

        current["blocks"].append(
            {
                "type": "heading" if is_heading(text) else "paragraph",
                "text": text,
            }
        )

    close_current()
    questions.sort(key=lambda q: q["id"])
    return {
        "title": "Ôn thi kết thúc học phần Giải phẫu động vật",
        "subtitle": "Bản rút gọn – Hệ từ xa",
        "source": "Docs/OnTap_GPDV.docx",
        "count": len(questions),
        "questions": questions,
    }


# ── Tách MCQ từ HTML (object-literal JS → JSON) ──

_JS_STRING_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "b": "\b",
    "f": "\f",
    "\\": "\\",
    '"': '"',
    "'": "'",
    "0": "\0",
}


def _normalize_js_strings(text: str) -> str:
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
                        chars.append(chr(int(text[i + 2 : i + 6], 16)))
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
            i += 1
            out.append(json.dumps("".join(chars), ensure_ascii=False))
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _repair_js_object_to_json(text: str) -> str:
    text = _normalize_js_strings(text)
    parts = re.split(r'("(?:\\.|[^"\\])*")', text)
    for idx in range(0, len(parts), 2):
        seg = parts[idx]
        seg = re.sub(r"([{,]\s*)([A-Za-z_$][\w$]*)(\s*:)", r'\1"\2":', seg)
        seg = re.sub(r",(\s*[}\]])", r"\1", seg)
        parts[idx] = seg
    return "".join(parts)


def _with_mcq_search(data: list[dict]) -> list[dict]:
    for q in data:
        answers = q.get("answers") or []
        q["searchText"] = (
            (q.get("text") or "")
            + " "
            + " ".join(answers)
            + " "
            + (q.get("explanation") or "")
            + " "
            + (q.get("reference") or "")
        ).lower()
    return data


def extract_mcq(html_path: Path) -> list[dict]:
    json_path = DATA_DIR / "OnTap_F_mcq.json"
    if json_path.exists():
        return _with_mcq_search(json.loads(json_path.read_text(encoding="utf-8")))

    content = html_path.read_text(encoding="utf-8")
    m = re.search(r"const\s+QUESTIONS\s*=\s*(\[.*?\]);", content, re.DOTALL)
    if not m:
        raise RuntimeError(
            "Không tìm thấy data/OnTap_F_mcq.json và cũng không có const QUESTIONS trong HTML."
        )
    raw = m.group(1)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = json.loads(_repair_js_object_to_json(raw))
    return _with_mcq_search(data)


def write_json_js(json_path: Path, js_path: Path, var_name: str, data) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    json_path.write_text(payload + "\n", encoding="utf-8")
    js_path.write_text(
        f"window.{var_name} = {payload};\n",
        encoding="utf-8",
    )


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    if not DOCX_PATH.exists():
        print(f"[LỖI] Không thấy {DOCX_PATH}")
        sys.exit(1)

    DATA_DIR.mkdir(exist_ok=True)

    essay = parse_docx(DOCX_PATH)
    ids = [q["id"] for q in essay["questions"]]
    print(f"Tự luận : {essay['count']} câu  (id {min(ids) if ids else '-'}–{max(ids) if ids else '-'})")
    missing = [i for i in range(1, 46) if i not in ids]
    if missing:
        print(f"  [!] Thiếu câu: {missing}")

    write_json_js(
        DATA_DIR / "OnTap_F_essay.json",
        DATA_DIR / "OnTap_F_essay.js",
        "ESSAY_QUESTIONS",
        essay,
    )

    if HTML_PATH.exists():
        try:
            mcq = extract_mcq(HTML_PATH)
            print(f"Trắc nghiệm: {len(mcq)} câu")
            write_json_js(
                DATA_DIR / "OnTap_F_mcq.json",
                DATA_DIR / "OnTap_F_mcq.js",
                "MCQ_QUESTIONS",
                mcq,
            )
        except Exception as e:
            print(f"  [!] Bỏ qua MCQ: {e}")

    print(f"\nĐã ghi vào {DATA_DIR}")
    print("  OnTap_F_essay.json / OnTap_F_essay.js")
    print("  OnTap_F_mcq.json   / OnTap_F_mcq.js")


if __name__ == "__main__":
    main()
