"""Minimal bencode helper: just enough to compute a torrent's info hash."""
import hashlib


def _skip(data, i):
    c = data[i:i + 1]
    if c == b"i":
        return data.index(b"e", i) + 1
    if c in (b"l", b"d"):
        i += 1
        while data[i:i + 1] != b"e":
            i = _skip(data, i)
        return i + 1
    colon = data.index(b":", i)
    return colon + 1 + int(data[i:colon])


def _read_str(data, i):
    colon = data.index(b":", i)
    n = int(data[i:colon])
    return data[colon + 1:colon + 1 + n], colon + 1 + n


def info_hash(data: bytes) -> str:
    if data[:1] != b"d":
        raise ValueError("not a torrent file")
    i = 1
    while data[i:i + 1] != b"e":
        key, i = _read_str(data, i)
        end = _skip(data, i)
        if key == b"info":
            return hashlib.sha1(data[i:end]).hexdigest()
        i = end
    raise ValueError("no info dict")
