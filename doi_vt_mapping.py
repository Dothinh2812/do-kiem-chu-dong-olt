from typing import Optional


CANONICAL_DOI_VT_TO_THREAD = {
    "Tổ Kỹ thuật Địa bàn Quảng Oai": "860736048191000245",
    "Tổ Kỹ thuật Địa bàn Sơn Tây": "2842297344572809781",
    "Tổ Kỹ thuật Địa bàn Suối Hai": "7309795608264187957",
    "Tổ Kỹ thuật Địa bàn Phúc Thọ": "5692189153216431290",
}


DOI_VT_ALIASES = {
    "Quảng Oai": "Tổ Kỹ thuật Địa bàn Quảng Oai",
    "QOI": "Tổ Kỹ thuật Địa bàn Quảng Oai",
    "QOI - cb": "Tổ Kỹ thuật Địa bàn Quảng Oai",
    "Sơn Tây": "Tổ Kỹ thuật Địa bàn Sơn Tây",
    "STY": "Tổ Kỹ thuật Địa bàn Sơn Tây",
    "STY - cb": "Tổ Kỹ thuật Địa bàn Sơn Tây",
    "Suối hai": "Tổ Kỹ thuật Địa bàn Suối Hai",
    "Suối Hai": "Tổ Kỹ thuật Địa bàn Suối Hai",
    "SHI": "Tổ Kỹ thuật Địa bàn Suối Hai",
    "SHI - cb": "Tổ Kỹ thuật Địa bàn Suối Hai",
    "Phúc Thọ": "Tổ Kỹ thuật Địa bàn Phúc Thọ",
    "PTO": "Tổ Kỹ thuật Địa bàn Phúc Thọ",
    "PTO - cb": "Tổ Kỹ thuật Địa bàn Phúc Thọ",
    "Tổ Kỹ thuật Địa bàn Quảng Oai": "Tổ Kỹ thuật Địa bàn Quảng Oai",
    "Tổ Kỹ thuật Địa bàn Sơn Tây": "Tổ Kỹ thuật Địa bàn Sơn Tây",
    "Tổ Kỹ thuật Địa bàn Suối hai": "Tổ Kỹ thuật Địa bàn Suối Hai",
    "Tổ Kỹ thuật Địa bàn Suối Hai": "Tổ Kỹ thuật Địa bàn Suối Hai",
    "Tổ Kỹ thuật Địa bàn Phúc Thọ": "Tổ Kỹ thuật Địa bàn Phúc Thọ",
}


def normalize_doi_vt_name(doi_vt: str) -> str:
    doi_vt = (doi_vt or "").strip()
    if not doi_vt:
        return ""
    return DOI_VT_ALIASES.get(doi_vt, doi_vt)


def get_thread_id_for_doi_vt(doi_vt: str) -> Optional[str]:
    canonical_name = normalize_doi_vt_name(doi_vt)
    return CANONICAL_DOI_VT_TO_THREAD.get(canonical_name)
