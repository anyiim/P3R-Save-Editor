#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P3R (Persona 3 Reload) Steam 存档修改器
=======================================

存档结构 (已完整逆向):
  外层  : XOR + 半字节交换加密, 密钥 "ae5zeitaix1joowooNgie3fahP5Ohph"
  中间层: UE4.27 GVAS (Unreal SaveGame)
    - 文件头 + CustomVersionContainer + SaveGameClassName("/Script/xrd777.XRD777SaveGame")
    - 结构体属性 "SaveDataHeadder" (存 SaveSlotName / 姓名 / 日期 / PlayerLevel ...)
    - 之后是真正的游戏数据: 7617 条名为 "SaveDataArea" 的 UInt32Property

单条 SaveDataArea 记录 (固定 49 字节):
  +0   int32  nameLen(13) + "SaveDataArea\\0"          (17 字节)
  +17  int32  typeLen(15) + "UInt32Property\\0"        (19 字节)
  +36  int32  padding_static = 4                       (4  字节)  -> 固定 04 00 00 00
  +40  uint32 padding = 数值 ID                        (4  字节)
  +44  uint8  0x00                                     (1  字节)
  +45  uint32 value = 真正的数据                        (4  字节)

记录按 ID 升序排列; value 为 0 的条目不会写入文件。

Persona 槽位 (当前版本, 旧版 ID 需 -4):
  base + 0 : persona ID      (4 字节: 01 <code> 00 01, code 见 persona_code)
  base + 1 : level
  base + 2 : exp
  base + 3 : skill 1/2       (u32 = 高16位<<16 | 低16位, 每字段装 2 个技能)
  base + 4 : skill 3/4
  base + 5 : skill 5/6
  base + 6 : skill 7/8
  base + 7 : 力/魔/耐/速      (每字节一个: byte0=力 byte1=魔 byte2=耐 byte3=速)
  base + 8 : 运
槽位 base 序列: 13090 + 12*k  (k = 0..6)
"""
import argparse
import struct
import sys

# ----------------------------------------------------------------------------
# 加密层
# ----------------------------------------------------------------------------
SAVE_KEY = b"ae5zeitaix1joowooNgie3fahP5Ohph"
KEYLEN = len(SAVE_KEY)
MAGIC_ENCRYPTED = 0x0B650015
MAGIC_GVAS = 0x53415647


def _dec_byte(d, k):
    b = (d ^ k) & 0xFF
    return ((b >> 4) & 3) | ((b & 3) << 4) | (b & 0xCC)


def _enc_byte(d, k):
    b = ((d >> 4) & 3) | ((d & 3) << 4) | (d & 0xCC)
    return b ^ k


def decrypt(blob: bytes) -> bytes:
    return bytes(_dec_byte(c, SAVE_KEY[i % KEYLEN]) for i, c in enumerate(blob))


def encrypt(blob: bytes) -> bytes:
    return bytes(_enc_byte(c, SAVE_KEY[i % KEYLEN]) for i, c in enumerate(blob))


# ----------------------------------------------------------------------------
# 记录层
# ----------------------------------------------------------------------------
REC_HEAD = (b"\x0d\x00\x00\x00" + b"SaveDataArea\x00" +
            b"\x0f\x00\x00\x00" + b"UInt32Property\x00" +
            b"\x04\x00\x00\x00")
REC_SIZE = 49


def make_record(rid: int, value: int) -> bytes:
    """构造一条完整的 SaveDataArea 记录 (49 字节)"""
    return REC_HEAD + struct.pack("<I", rid) + b"\x00" + struct.pack("<I", value)


class P3RSave:
    """解析后的存档: 有序的 (id, value) 字典 + 原始字节"""

    def __init__(self, data: bytes):
        """data 为解密后的 GVAS 字节"""
        if data[:4] != b"GVAS":
            raise ValueError("不是解密后的 GVAS 数据 (magic=%r)" % data[:4])
        self.raw = bytearray(data)
        self.records = {}   # id -> (offset, value)
        self._scan()

    # ---- 扫描 ----
    def _scan(self):
        self.records.clear()
        s = 0
        while True:
            i = self.raw.find(REC_HEAD, s)
            if i < 0:
                break
            rid = struct.unpack_from("<I", self.raw, i + 40)[0]
            val = struct.unpack_from("<I", self.raw, i + 45)[0]
            gap = self.raw[i + 44]
            if gap != 0:
                raise ValueError("记录 %d 的填充字节异常: %02x" % (rid, gap))
            self.records[rid] = (i, val)
            s = i + REC_SIZE
        # 找到结尾 "None" 终止符位置
        tail = self.raw.rfind(b"\x05\x00\x00\x00None\x00")
        if tail < 0:
            raise ValueError("未找到结尾 None 终止符")
        self.terminator = tail

    # ---- 读 ----
    def get(self, rid: int, default=0):
        return self.records.get(rid, (None, default))[1]

    # ---- 写 ----
    def set(self, rid: int, value: int):
        """写入数值; 不存在则按 ID 顺序插入; value==0 则删除该条目"""
        if rid in self.records:
            off, _ = self.records[rid]
            if value == 0:
                # 删除 (游戏不保存值为 0 的条目)
                del self.raw[off:off + REC_SIZE]
                del self.records[rid]
                self._rescan_after_shift(off, -REC_SIZE)
                return
            struct.pack_into("<I", self.raw, off + 45, value)
            self.records[rid] = (off, value)
            return

        if value == 0:
            return  # 本来就没有, 无需处理

        rec = make_record(rid, value)
        # 找到第一条 ID 更大的记录
        nxt = None
        for r in sorted(self.records):
            if r > rid:
                nxt = self.records[r][0]
                break
        pos = nxt if nxt is not None else self.terminator
        self.raw[pos:pos] = rec
        self._rescan_after_shift(pos, REC_SIZE)

    def _rescan_after_shift(self, from_off, delta):
        self._scan()

    # ---- Persona ----
    def find_persona_base(self, code_hex: str):
        """code_hex 形如 '3401' -> 定位该 persona 所在槽位 base"""
        hi = int(code_hex[0:2], 16)
        lo = int(code_hex[2:4], 16)
        for k in range(0, 7):
            base = 13090 + 12 * k
            v = self.get(base, 0)
            if ((v >> 16) & 0xFF) == hi and (v & 0xFF) == lo:
                return base
        return None

    def get_persona_skills(self, base):
        out = []
        for j in range(3, 7):
            v = self.get(base + j, 0)
            h, l = (v >> 16) & 0xFFFF, v & 0xFFFF
            if h:
                out.append(h)
            if l:
                out.append(l)
        return out

    def set_persona_skills(self, base, skills):
        if len(skills) > 8:
            raise ValueError("技能最多 8 个")
        s = list(skills) + [0] * (8 - len(skills))
        for j in range(4):
            hi, lo = s[j * 2], s[j * 2 + 1]
            self.set(base + 3 + j, (hi << 16) | lo)

    def get_persona_stats(self, base):
        v = self.get(base + 7, 0)
        return [v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF, (v >> 24) & 0xFF], self.get(base + 8, 0)

    def set_persona_stats(self, base, st=None, luck=None):
        if st is not None:
            v = st[0] | (st[1] << 8) | (st[2] << 16) | (st[3] << 24)
            self.set(base + 7, v)
        if luck is not None:
            self.set(base + 8, luck)

    # ---- 输出 ----
    def to_gvas(self) -> bytes:
        return bytes(self.raw)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
SKILL_NAMES = {
    117: "空间杀法", 792: "斩击强化", 793: "高级斩击强化", 875: "终极斩击强化",
    897: "全体攻击强化", 834: "建言", 885: "暴击大UP", 856: "武道的资质",
}


def load_save(path):
    blob = open(path, "rb").read()
    magic = int.from_bytes(blob[:4], "little")
    if magic == MAGIC_ENCRYPTED:
        return P3RSave(decrypt(blob))
    if magic == MAGIC_GVAS:
        return P3RSave(blob)
    raise ValueError("未知存档格式")


def save_save(save, path):
    open(path, "wb").write(encrypt(save.to_gvas()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--persona-code", help="定位 persona, 如 3401")
    ap.add_argument("--skills", help="逗号分隔的技能 ID")
    ap.add_argument("--level", type=int)
    ap.add_argument("--exp", type=int, help="累计总经验 (base+2)")
    ap.add_argument("--stats", help="力,魔,耐,速,运")
    ap.add_argument("--set", action="append", default=[], help="ID=VALUE")
    ap.add_argument("--dump", action="store_true")
    a = ap.parse_args()

    save = load_save(a.input)
    print("载入: %s  记录数=%d" % (a.input, len(save.records)))

    base = None
    if a.persona_code:
        base = save.find_persona_base(a.persona_code)
        if base is None:
            raise SystemExit("找不到 persona code %s" % a.persona_code)
        print("persona %s 槽位 base=%d (Lv %s, exp %s)" %
              (a.persona_code, base, save.get(base + 1), save.get(base + 2)))
        print("  当前技能:", save.get_persona_skills(base))
        st, luck = save.get_persona_stats(base)
        print("  当前五维: 力%d 魔%d 耐%d 速%d 运%d" % (st[0], st[1], st[2], st[3], luck))

    if a.skills:
        if base is None:
            raise SystemExit("--skills 需要配合 --persona-code")
        ids = [int(x) for x in a.skills.split(",")]
        save.set_persona_skills(base, ids)
        print("写入技能:", ["%d(%s)" % (i, SKILL_NAMES.get(i, "?")) for i in ids])

    if a.level is not None:
        if base is None:
            raise SystemExit("--level 需要配合 --persona-code")
        save.set(base + 1, a.level)
        print("写入等级:", a.level)

    if a.exp is not None:
        if base is None:
            raise SystemExit("--exp 需要配合 --persona-code")
        save.set(base + 2, a.exp)
        print("写入经验:", a.exp)

    if a.stats:
        if base is None:
            raise SystemExit("--stats 需要配合 --persona-code")
        v = [int(x) for x in a.stats.split(",")]
        save.set_persona_stats(base, st=v[:4], luck=v[4])
        print("写入五维:", v)

    for kv in a.set:
        k, v = kv.split("=")
        save.set(int(k), int(v))
        print("设置 ID %s = %s" % (k, v))

    global DONE
    DONE.append((save, base))

    save_save(save, a.output)
    print("已写出:", a.output)

    if a.dump and base is not None:
        print()
        print("--- 复核 ---")
        chk = load_save(a.output)
        print("技能:", chk.get_persona_skills(base))
        print("等级:", chk.get(base + 1), "exp:", chk.get(base + 2))
        st, luck = chk.get_persona_stats(base)
        print("五维: 力%d 魔%d 耐%d 速%d 运%d" % (st[0], st[1], st[2], st[3], luck))


DONE = []
if __name__ == "__main__":
    main()
