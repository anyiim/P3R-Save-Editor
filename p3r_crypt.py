#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P3R (Persona 3 Reload) Steam 存档 加解密工具
算法来源: illusion0001/P3R-Save-EnDecryptor (main.c)

加密文件魔数 0x0B650015  -> 解密后为 GVAS (Unreal Engine SaveGame, 魔数 0x53415647)

核心变换:
    key = "ae5zeitaix1joowooNgie3fahP5Ohph"  (32 字节, 循环)
    decrypt_byte(d, k):  b = d ^ k
                         return ((b >> 4) & 3) | ((b & 3) << 4) | (b & 0xcc)
    encrypt_byte(d, k):  return ((((d >> 4) & 3) | ((d & 3) << 4) | (d & 0xcc)) ^ k)
"""
import sys
import os

SAVE_KEY = b"ae5zeitaix1joowooNgie3fahP5Ohph"
KEYLEN = len(SAVE_KEY)

MAGIC_ENCRYPTED = 0x0B650015
MAGIC_GVAS = 0x53415647  # 'GVAS'


def _decrypt_byte(data: int, key: int) -> int:
    b = (data ^ key) & 0xFF
    return ((b >> 4) & 3) | ((b & 3) << 4) | (b & 0xCC)


def _encrypt_byte(data: int, key: int) -> int:
    b = ((data >> 4) & 3) | ((data & 3) << 4) | (data & 0xCC)
    return b ^ key


def decrypt(blob: bytes) -> bytes:
    out = bytearray(len(blob))
    ki = 0
    for i, byte in enumerate(blob):
        out[i] = _decrypt_byte(byte, SAVE_KEY[ki])
        ki += 1
        if ki >= KEYLEN:
            ki = 0
    return bytes(out)


def encrypt(blob: bytes) -> bytes:
    out = bytearray(len(blob))
    ki = 0
    for i, byte in enumerate(blob):
        out[i] = _encrypt_byte(byte, SAVE_KEY[ki])
        ki += 1
        if ki >= KEYLEN:
            ki = 0
    return bytes(out)


def detect(blob: bytes) -> str:
    magic = int.from_bytes(blob[:4], "little")
    if magic == MAGIC_ENCRYPTED:
        return "encrypted"
    if magic == MAGIC_GVAS:
        return "gvas"
    return "unknown(0x%08X)" % magic


def main():
    if len(sys.argv) < 3:
        print("usage: p3r_crypt.py <decrypt|encrypt|info> <infile> [outfile]")
        return 1
    mode, src = sys.argv[1], sys.argv[2]
    with open(src, "rb") as f:
        blob = f.read()

    if mode == "info":
        print("%s: %s (%d bytes)" % (src, detect(blob), len(blob)))
        return 0

    if mode == "decrypt":
        assert detect(blob) == "encrypted", "not an encrypted save: " + detect(blob)
        out = decrypt(blob)
        assert out[:4] == b"GVAS", "decrypt failed, got %r" % out[:4]
    elif mode == "encrypt":
        assert detect(blob) == "gvas", "not a GVAS file: " + detect(blob)
        out = encrypt(blob)
        assert int.from_bytes(out[:4], "little") == MAGIC_ENCRYPTED
    else:
        raise SystemExit("unknown mode: " + mode)

    dst = sys.argv[3] if len(sys.argv) > 3 else (
        "decrypt_out.sav" if mode == "decrypt" else "encrypt_out.sav")
    with open(dst, "wb") as f:
        f.write(out)
    print("%s: %s -> %s (%d bytes)" % (mode, src, dst, len(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
