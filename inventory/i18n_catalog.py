"""Tiny gettext toolchain so the project needs no GNU gettext: msgid extraction and .po -> .mo."""

import ast
import re
import struct
from pathlib import Path

GETTEXT_CALLS = {"_", "gettext", "gettext_lazy"}
_TRANSLATE_TAG = re.compile(r"""\{%\s*(?:translate|trans)\s+(["'])(.*?)\1""", re.S)
_BLOCK_TAG = re.compile(r"\{%\s*blocktranslate\b([^%]*)%\}(.*?)\{%\s*endblocktranslate\s*%\}", re.S)
_BLOCK_VAR = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def extract_python(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in GETTEXT_CALLS
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            found.add(node.args[0].value)
    return found


def extract_template(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    found = {match.group(2) for match in _TRANSLATE_TAG.finditer(text)}
    for options, body in _BLOCK_TAG.findall(text):
        if "trimmed" in options:
            body = " ".join(line.strip() for line in body.strip().splitlines() if line.strip())
        found.add(_BLOCK_VAR.sub(lambda m: f"%({m.group(1)})s", body))
    return found


def extract_project(base_dir: Path) -> set[str]:
    msgids = set()
    for app in ("accounts", "inventory", "config"):
        for path in (base_dir / app).rglob("*.py"):
            if "tests" not in path.parts and path.name != "tests.py" and "migrations" not in path.parts:
                msgids |= extract_python(path)
    for path in (base_dir / "templates").rglob("*.html"):
        msgids |= extract_template(path)
    return msgids


def _unquote(chunk: str) -> str:
    return ast.literal_eval(chunk)


def read_po(path: Path) -> dict[str, str]:
    """Parses plain msgid/msgstr pairs (no plurals or contexts are used in this project)."""
    entries: dict[str, str] = {}
    msgid = msgstr = None
    current = None
    fuzzy = False

    def flush():
        if msgid is not None and msgstr is not None and not fuzzy:
            entries[msgid] = msgstr

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("#,") and "fuzzy" in line:
            fuzzy = True
        elif line.startswith("msgid "):
            flush()
            msgid, msgstr, current = _unquote(line[6:]), None, "id"
        elif line.startswith("msgstr "):
            msgstr, current = _unquote(line[7:]), "str"
        elif line.startswith('"'):
            if current == "id":
                msgid += _unquote(line)
            elif current == "str":
                msgstr += _unquote(line)
        elif not line:
            flush()
            if msgid is not None:
                fuzzy = False
            msgid = msgstr = current = None
    flush()
    return entries


def write_mo(entries: dict[str, str], path: Path) -> None:
    """GNU .mo format: header, sorted key/value tables, then the strings."""
    keys = sorted(entries)
    ids = b"".join(k.encode() + b"\0" for k in keys)
    strs = b"".join(entries[k].encode() + b"\0" for k in keys)
    count = len(keys)
    key_table_offset = 7 * 4
    value_table_offset = key_table_offset + count * 8
    ids_offset = value_table_offset + count * 8
    strs_offset = ids_offset + len(ids)

    key_table, value_table = [], []
    offset = 0
    for k in keys:
        size = len(k.encode())
        key_table += [size, ids_offset + offset]
        offset += size + 1
    offset = 0
    for k in keys:
        size = len(entries[k].encode())
        value_table += [size, strs_offset + offset]
        offset += size + 1

    header = struct.pack("Iiiiiii", 0x950412DE, 0, count, key_table_offset, value_table_offset, 0, 0)
    path.write_bytes(
        header
        + struct.pack(f"{len(key_table)}i", *key_table)
        + struct.pack(f"{len(value_table)}i", *value_table)
        + ids
        + strs
    )
