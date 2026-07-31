"""Pure-Python reader for R's serialized data files (``.rda`` / ``.RData`` / ``.rds``).

The R package keeps four objects the pipeline needs inside ``R/sysdata.rda``:
``player_ids``, ``bonus_col_coefs``, ``bonus_col_sets`` and ``pts_bracket_coefs``.
Those are produced by ``data-raw/`` scripts that fit models with ``nflfastR`` and
``nlme``, so they cannot be regenerated without R.  Rather than transcribe or
re-derive them (which would mean guessing), this module reads the real file.

The container is bzip2/gzip/xz-compressed R serialization format 2 or 3 ("RDX2"
/ "RDX3") in XDR (big-endian) encoding.  Only the SEXP types that actually occur
in the package's data files are implemented; anything else raises rather than
returning something subtly wrong.
"""

from __future__ import annotations

import bz2
import gzip
import lzma
import struct
from pathlib import Path
from typing import Any

__all__ = ["read_rdata", "read_rds", "RDataError"]


class RDataError(Exception):
    """Raised when a file is not R serialization data we can decode."""


# SEXP type codes (Rinternals.h)
NILSXP, SYMSXP, LISTSXP, CLOSXP = 0, 1, 2, 3
ENVSXP, PROMSXP, LANGSXP = 4, 5, 6
CHARSXP, LGLSXP = 9, 10
INTSXP, REALSXP, CPLXSXP, STRSXP = 13, 14, 15, 16
DOTSXP, VECSXP, EXPRSXP = 17, 19, 20
RAWSXP, S4SXP = 24, 25

# Pseudo-types used by the serializer
ALTREP_SXP = 238
BASEENV_SXP = 241
EMPTYENV_SXP = 242
GLOBALENV_SXP = 253
NILVALUE_SXP = 254
REFSXP = 255

NA_INTEGER = -0x80000000

_PAIRLIST_TYPES = (LISTSXP, LANGSXP, DOTSXP)


class _Reader:
    """Cursor over the uncompressed XDR byte stream."""

    __slots__ = ("buf", "pos", "refs")

    def __init__(self, buf: bytes) -> None:
        self.buf = buf
        self.pos = 0
        self.refs: list[Any] = []

    def int32(self) -> int:
        (value,) = struct.unpack_from(">i", self.buf, self.pos)
        self.pos += 4
        return value

    def float64(self) -> float:
        (value,) = struct.unpack_from(">d", self.buf, self.pos)
        self.pos += 8
        return value

    def take(self, n: int) -> bytes:
        chunk = self.buf[self.pos : self.pos + n]
        self.pos += n
        return chunk


def _decode_string(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _read_ref(reader: _Reader, flags: int) -> Any:
    index = flags >> 8
    if index == 0:
        index = reader.int32()
    return reader.refs[index - 1]


def _read_pairlist(reader: _Reader, flags: int) -> list[tuple[Any, Any]]:
    """Read a pairlist (LISTSXP), returning ``[(tag, value), ...]``.

    A top-level ``.rda`` is exactly this: name -> object bindings.
    """
    items: list[tuple[Any, Any]] = []
    while True:
        if flags & 0x200:  # attributes on the CONS cell
            _read_item(reader)
        tag = _read_item(reader) if flags & 0x400 else None
        items.append((tag, _read_item(reader)))

        flags = reader.int32()
        kind = flags & 0xFF
        if kind == NILVALUE_SXP:
            return items
        if kind == REFSXP:
            items.append((None, _read_ref(reader, flags)))
            return items
        if kind not in _PAIRLIST_TYPES:
            raise RDataError(f"unexpected type {kind} in pairlist at byte {reader.pos}")


def _read_item(reader: _Reader) -> Any:
    flags = reader.int32()
    kind = flags & 0xFF
    has_attributes = bool(flags & 0x200)

    if kind == REFSXP:
        return _read_ref(reader, flags)
    if kind == NILVALUE_SXP or kind == NILSXP:
        return None
    if kind in (GLOBALENV_SXP, EMPTYENV_SXP, BASEENV_SXP):
        return None

    if kind == SYMSXP:
        name = _read_item(reader)
        reader.refs.append(name)
        return name

    if kind == CHARSXP:
        length = reader.int32()
        if length == -1:  # NA_character_
            return None
        return _decode_string(reader.take(length))

    if kind in _PAIRLIST_TYPES:
        return _read_pairlist(reader, flags)

    if kind == ALTREP_SXP:
        # info, state, attributes.  Only compact integer sequences and deferred
        # string conversions appear in practice; both are fully described by the
        # state, which we hand back for the caller to normalise.
        info = _read_item(reader)
        state = _read_item(reader)
        _read_item(reader)
        return _expand_altrep(info, state)

    values = _read_vector(reader, kind)

    if has_attributes:
        attributes = _read_item(reader)
        if attributes:
            attrs = {tag: value for tag, value in attributes if tag is not None}
            return _apply_attributes(values, attrs)
    return values


def _read_vector(reader: _Reader, kind: int) -> Any:
    if kind == LGLSXP:
        n = reader.int32()
        raw = [reader.int32() for _ in range(n)]
        return [None if v == NA_INTEGER else bool(v) for v in raw]
    if kind == INTSXP:
        n = reader.int32()
        raw = [reader.int32() for _ in range(n)]
        return [None if v == NA_INTEGER else v for v in raw]
    if kind == REALSXP:
        n = reader.int32()
        return [reader.float64() for _ in range(n)]
    if kind == STRSXP:
        n = reader.int32()
        return [_read_item(reader) for _ in range(n)]
    if kind in (VECSXP, EXPRSXP):
        n = reader.int32()
        return [_read_item(reader) for _ in range(n)]
    if kind == RAWSXP:
        n = reader.int32()
        return list(reader.take(n))
    if kind in (CLOSXP, ENVSXP, PROMSXP, S4SXP):
        raise RDataError(
            f"SEXP type {kind} (closure/environment) at byte {reader.pos}: this file "
            "holds R code objects, not data. The package's own data files "
            "(R/sysdata.rda, data/nfl_cols.rda) contain no such objects; "
            "data/projection_sources.rda does, and is unused by the R package."
        )
    raise RDataError(f"unsupported SEXP type {kind} at byte {reader.pos}")


def _expand_altrep(info: Any, state: Any) -> Any:
    """Materialise the ALTREP forms R uses for compact vectors."""
    class_name = info[0][1] if isinstance(info, list) and info else info
    if class_name == "compact_intseq" and isinstance(state, list) and len(state) == 3:
        length, start, step = int(state[0]), state[1], state[2]
        return [start + i * step for i in range(length)]
    if class_name == "deferred_string" and isinstance(state, list) and state:
        return [str(v) for v in state[0]]
    return state


def _apply_attributes(values: Any, attrs: dict) -> Any:
    """Turn an attributed R vector into the closest natural Python value.

    * a ``data.frame`` becomes ``{"columns": {name: values}, "nrow": n}``
    * a plain named vector keeps its order (R allows duplicate names and looks
      up the *first* match, which a dict would silently break) and is returned
      as a list of ``(name, value)`` pairs
    * anything else is returned with its attributes attached
    """
    names = attrs.get("names")
    classes = attrs.get("class") or []
    if isinstance(classes, str):
        classes = [classes]

    if "data.frame" in classes and names:
        return {
            "columns": dict(zip(names, values)),
            "names": list(names),
            "nrow": len(values[0]) if values else 0,
        }
    if names:
        return list(zip(names, values))
    return {"values": values, "attributes": attrs}


def _decompress(raw: bytes) -> bytes:
    for magic, decompress in (
        (b"BZh", bz2.decompress),
        (b"\x1f\x8b", gzip.decompress),
        (b"\xfd7zXZ", lzma.decompress),
    ):
        if raw.startswith(magic):
            return decompress(raw)
    return raw  # stored uncompressed


def _open_stream(path: str | Path) -> _Reader:
    data = _decompress(Path(path).read_bytes())

    if data[:5] in (b"RDX3\n", b"RDX2\n"):
        header_len = 5
    elif data[:2] in (b"X\n", b"A\n"):
        header_len = 0
    else:
        raise RDataError(f"{path} is not R serialization data")

    if data[header_len : header_len + 2] != b"X\n":
        raise RDataError(f"{path} is not in XDR format (only binary XDR is supported)")

    reader = _Reader(data)
    reader.pos = header_len + 2

    version = reader.int32()
    reader.int32()  # writer R version
    reader.int32()  # minimum reader R version
    if version >= 3:
        reader.take(reader.int32())  # native encoding name
    return reader


def read_rdata(path: str | Path) -> dict[str, Any]:
    """Read a ``.rda``/``.RData`` file into ``{object_name: value}``."""
    reader = _open_stream(path)
    bindings = _read_item(reader)
    if not isinstance(bindings, list):
        raise RDataError(f"{path} does not contain a name->object pairlist")
    return {tag: value for tag, value in bindings if tag is not None}


def read_rds(path: str | Path) -> Any:
    """Read a single-object ``.rds`` file."""
    return _read_item(_open_stream(path))
