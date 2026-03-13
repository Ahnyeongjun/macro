"""엑셀 템플릿 읽기/쓰기 (XML 레벨 조작으로 서식 100% 보존)"""

import io
import os
import re
import zipfile
import xml.etree.ElementTree as ET

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


# ---------------------------------------------------------------------------
# 템플릿에서 카테고리 셀 위치 자동 감지
# ---------------------------------------------------------------------------

def detect_category_cells(template_path: str) -> dict[str, int]:
    """
    템플릿 xlsx의 C열을 스캔하여 {카테고리명(정규화): 행번호} 매핑을 반환합니다.
    예: {"APISS": 7, "기타": 9, "이슈사항": 10}
    """
    with zipfile.ZipFile(template_path, "r") as z:
        sheet_xml = z.read("xl/worksheets/sheet1.xml")
        strings = _read_shared_strings(z)

    root = ET.fromstring(sheet_xml)
    sheet_data = root.find(f"{{{NS}}}sheetData")

    mapping = {}
    for row in sheet_data.findall(f"{{{NS}}}row"):
        for cell in row.findall(f"{{{NS}}}c"):
            ref = cell.attrib.get("r", "")
            if not ref.startswith("C") or not ref[1:].isdigit():
                continue
            text = _cell_text(cell, strings)
            if text:
                mapping[_norm(text)] = int(ref[1:])
    return mapping


def resolve_cell_refs(categories: list[dict], cell_map: dict[str, int]):
    """config 카테고리를 템플릿 C열과 대조하여 D/H열 셀 참조를 추가합니다."""
    resolved = []
    for cat in categories:
        name = cat["name"]
        norm = _norm(name)

        row = cell_map.get(norm)
        if row is None:
            for tmpl_name, tmpl_row in cell_map.items():
                if norm in tmpl_name or tmpl_name in norm:
                    row = tmpl_row
                    break

        if row is None:
            print(f"  경고: 템플릿에서 '{name}' 카테고리를 찾을 수 없습니다 (건너뜀)")
            continue

        resolved.append({**cat, "this_week_cell": f"D{row}", "next_week_cell": f"H{row}"})
    return resolved


# ---------------------------------------------------------------------------
# 엑셀 쓰기
# ---------------------------------------------------------------------------

def update(template_path: str, output_path: str, updates: dict):
    """
    템플릿 xlsx를 복사하고 지정된 셀만 교체합니다.
    updates: {"D7": "텍스트", "B1": 45730, ...}
    """
    if os.path.abspath(template_path) == os.path.abspath(output_path):
        raise ValueError("출력 파일이 템플릿과 같은 경로입니다.")

    with zipfile.ZipFile(template_path, "r") as zin:
        contents = {n: zin.read(n) for n in zin.namelist()}

    sheet_xml = contents["xl/worksheets/sheet1.xml"]
    _register_ns(sheet_xml)

    root = ET.fromstring(sheet_xml)
    sd = root.find(f"{{{NS}}}sheetData")

    for ref, value in updates.items():
        cell = _find_or_create(sd, ref)
        _set_value(cell, value)

    new_xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    new_xml += ET.tostring(root, encoding="unicode")

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in contents.items():
            if name == "xl/worksheets/sheet1.xml":
                zout.writestr(name, new_xml.encode("utf-8"))
            else:
                zout.writestr(name, data)


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _read_shared_strings(zf):
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    tree = ET.parse(zf.open("xl/sharedStrings.xml"))
    result = []
    for si in tree.findall(f".//{{{NS}}}si"):
        parts = [t.text for t in si.iter(f"{{{NS}}}t") if t.text]
        result.append("".join(parts))
    return result


def _cell_text(elem, strings):
    ct = elem.attrib.get("t", "")
    if ct == "s":
        v = elem.find(f"{{{NS}}}v")
        if v is not None and v.text is not None:
            idx = int(v.text)
            return strings[idx] if idx < len(strings) else ""
    elif ct == "inlineStr":
        is_el = elem.find(f"{{{NS}}}is")
        if is_el is not None:
            t = is_el.find(f"{{{NS}}}t")
            return t.text if t is not None else ""
    return ""


def _register_ns(xml_bytes: bytes):
    for _, (prefix, uri) in ET.iterparse(io.BytesIO(xml_bytes), events=["start-ns"]):
        try:
            ET.register_namespace(prefix, uri)
        except ValueError:
            pass


def _find_or_create(sheet_data, cell_ref: str):
    row_num = int("".join(filter(str.isdigit, cell_ref)))

    target_row = None
    for row in sheet_data.findall(f"{{{NS}}}row"):
        if row.attrib.get("r") == str(row_num):
            target_row = row
            break
    if target_row is None:
        target_row = ET.SubElement(sheet_data, f"{{{NS}}}row")
        target_row.set("r", str(row_num))

    for c in target_row.findall(f"{{{NS}}}c"):
        if c.attrib.get("r") == cell_ref:
            return c

    elem = ET.SubElement(target_row, f"{{{NS}}}c")
    elem.set("r", cell_ref)
    return elem


def _set_value(cell_elem, value):
    for child in list(cell_elem):
        tag = child.tag.rsplit("}", 1)[-1] if "}" in child.tag else child.tag
        if tag in ("v", "is", "f"):
            cell_elem.remove(child)

    if isinstance(value, (int, float)):
        cell_elem.attrib.pop("t", None)
        v = ET.SubElement(cell_elem, f"{{{NS}}}v")
        v.text = str(int(value)) if isinstance(value, int) else str(value)
    else:
        cell_elem.set("t", "inlineStr")
        is_el = ET.SubElement(cell_elem, f"{{{NS}}}is")
        t = ET.SubElement(is_el, f"{{{NS}}}t")
        t.text = str(value)
