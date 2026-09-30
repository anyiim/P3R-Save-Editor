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
    - 之后是真正的游戏数据: 数千条名为 "SaveDataArea" 的 UInt32Property

单条 SaveDataArea 记录 (固定 49 字节):
  +0   int32  nameLen(13) + "SaveDataArea\\0"          (17 字节)
  +17  int32  typeLen(15) + "UInt32Property\\0"        (19 字节)
  +36  int32  padding_static = 4                       (4  字节)  -> 固定 04 00 00 00
  +40  uint32 padding = 数值 ID                        (4  字节)
  +44  uint8  0x00                                     (1  字节)
  +45  uint32 value = 真正的数据                       (4  字节)

记录按 ID 升序排列; value 为 0 的条目不会写入文件。

Persona 槽位 (共 7 个):
  槽位 base 序列: 13090 + 12*k  (k = 0..6)
  base + 0 : persona 编号
             u32 = (persona_id << 16) | 0x0001
             高 16 位是完整编号, 低 16 位固定 0x0001; 空槽 value == 0
             例: 灰姑娘 0x0134 -> 0x01340001
  base + 1 : level
  base + 2 : exp          (从 Lv1 起的累计总经验, 不是当前级进度)
  base + 3 : skill 1/2    (u32 = 高16位<<16 | 低16位, 每字段装 2 个技能)
  base + 4 : skill 3/4
  base + 5 : skill 5/6
  base + 6 : skill 7/8
  base + 7 : 力/魔/耐/速  (每字节一个: byte0=力 byte1=魔 byte2=耐 byte3=速)
  base + 8 : 运

Persona 编号的两种写法:
  "0134"  完整编号 (推荐, 唯一确定)
  "3401"  旧简写 = 编号低字节 "34" + 固定标记 "01"
          只保留低字节, 所以 0x0040 / 0x0140 之类会撞车 —— 命中多个槽位时脚本会报错
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
MAGIC_GVAS = 0x53415647  # 'GVAS'


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
TERMINATOR = b"\x05\x00\x00\x00None\x00"

U8_MAX = 0xFF
U16_MAX = 0xFFFF
U32_MAX = 0xFFFFFFFF

# Persona 槽位布局
PERSONA_BASE = 13090
PERSONA_STRIDE = 12
PERSONA_SLOTS = 7
PERSONA_TAG = 0x0001      # 槽位值低 16 位的固定标记
SKILL_OFFSET = 3          # base+3 .. base+6 为 4 个技能字段
STAT_OFFSET = 7           # base+7 = 力/魔/耐/速
LUCK_OFFSET = 8           # base+8 = 运
LEVEL_OFFSET = 1
EXP_OFFSET = 2
MAX_SKILLS = 8


def _check_u32(v, what):
    if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= U32_MAX:
        raise ValueError("%s 必须是 0..%d 的整数, 收到 %r" % (what, U32_MAX, v))
    return v


def make_record(rid: int, value: int) -> bytes:
    """构造一条完整的 SaveDataArea 记录 (49 字节)"""
    return REC_HEAD + struct.pack("<I", rid) + b"\x00" + struct.pack("<I", value)


def parse_persona_code(code) -> int:
    """把用户输入的 persona 编号解析成整数; 支持 '0134' 与 '0x134' 等写法"""
    c = str(code).strip().lower()
    if c.startswith("0x"):
        c = c[2:]
    if not 1 <= len(c) <= 4 or any(ch not in "0123456789abcdef" for ch in c):
        raise ValueError("Persona 编号应为 1~4 位十六进制 (如 0134 或 3401), 收到 %r" % code)
    return int(c, 16)


class P3RSave:
    """解析后的存档: 有序的 (id, value) 字典 + 原始字节"""

    def __init__(self, data: bytes, source_format: str = "gvas"):
        """data 为解密后的 GVAS 字节"""
        if data[:4] != b"GVAS":
            raise ValueError("不是解密后的 GVAS 数据 (magic=%r)" % data[:4])
        self.raw = bytearray(data)
        self.source_format = source_format
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
        tail = self.raw.rfind(TERMINATOR)
        if tail < 0:
            raise ValueError("未找到结尾 None 终止符")
        self.terminator = tail

    # ---- 读 ----
    def get(self, rid: int, default=0):
        return self.records.get(rid, (None, default))[1]

    # ---- 写 ----
    def set(self, rid: int, value: int) -> str:
        """写入数值, 返回 'inserted' / 'updated' / 'deleted' / 'noop'

        value == 0 表示删除条目 —— 游戏本身就不保存值为 0 的记录,
        写 0 与删条目在游戏眼里等价。
        """
        _check_u32(rid, "数值 ID")
        _check_u32(value, "数值")

        if rid in self.records:
            off, old = self.records[rid]
            if value == 0:
                del self.raw[off:off + REC_SIZE]
                del self.records[rid]
                self._scan()
                return "deleted"
            if old == value:
                return "noop"
            struct.pack_into("<I", self.raw, off + 45, value)
            self.records[rid] = (off, value)
            return "updated"

        if value == 0:
            return "noop"

        rec = make_record(rid, value)
        pos = self.terminator
        for r in sorted(self.records):
            if r > rid:
                pos = self.records[r][0]
                break
        self.raw[pos:pos] = rec
        self._scan()
        return "inserted"

    # ---- Persona ----
    def persona_slot_bases(self):
        return [PERSONA_BASE + PERSONA_STRIDE * k for k in range(PERSONA_SLOTS)]

    def persona_id(self, base: int) -> int:
        """槽位值的高 16 位 = persona 完整编号; 空槽返回 0"""
        return (self.get(base, 0) >> 16) & 0xFFFF

    def find_persona_bases(self, code):
        """按编号定位槽位, 返回 (candidates, mode)

        candidates 为 [(槽位序 k, base, persona_id), ...];
        mode 为 'exact' (完整编号命中) 或 'legacy' (旧简写按低字节命中)。
        先试完整编号, 未命中才退化为旧简写 —— 既兼容老用法, 又不会误伤。
        """
        raw = parse_persona_code(code)
        slots = []
        for k, base in enumerate(self.persona_slot_bases()):
            v = self.get(base, 0)
            if v:
                slots.append((k, base, (v >> 16) & 0xFFFF, v))

        exact = [s[:3] for s in slots if s[2] == raw]
        if exact:
            return exact, "exact"

        low = (raw >> 8) & 0xFF
        legacy = [s[:3] for s in slots
                  if (s[3] & 0xFFFF) == PERSONA_TAG and (s[2] & 0xFF) == low]
        return legacy, "legacy"

    def get_persona_skills(self, base):
        out = []
        for j in range(SKILL_OFFSET, SKILL_OFFSET + 4):
            v = self.get(base + j, 0)
            h, l = (v >> 16) & 0xFFFF, v & 0xFFFF
            if h:
                out.append(h)
            if l:
                out.append(l)
        return out

    def set_persona_skills(self, base, skills):
        skills = list(skills)
        if len(skills) > MAX_SKILLS:
            raise ValueError("技能最多 %d 个, 收到 %d 个" % (MAX_SKILLS, len(skills)))
        for s in skills:
            if not 0 <= s <= U16_MAX:
                raise ValueError(
                    "技能 ID 必须在 0..%d 之间 (每个字段只装 2 个 16 位技能): %d"
                    % (U16_MAX, s))
        padded = skills + [0] * (MAX_SKILLS - len(skills))
        for j in range(4):
            hi, lo = padded[j * 2], padded[j * 2 + 1]
            self.set(base + SKILL_OFFSET + j, (hi << 16) | lo)

    def get_persona_stats(self, base):
        v = self.get(base + STAT_OFFSET, 0)
        st = [v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF, (v >> 24) & 0xFF]
        return st, self.get(base + LUCK_OFFSET, 0)

    def set_persona_stats(self, base, st=None, luck=None):
        if st is not None:
            if len(st) != 4:
                raise ValueError("五维需要 4 个值 (力,魔,耐,速), 收到 %d 个" % len(st))
            for i, v in enumerate(st):
                if not 0 <= v <= U8_MAX:
                    raise ValueError(
                        "五维第 %d 项必须在 0..%d 之间 (超范围会溢出污染相邻项): %d"
                        % (i + 1, U8_MAX, v))
            self.set(base + STAT_OFFSET, st[0] | (st[1] << 8) | (st[2] << 16) | (st[3] << 24))
        if luck is not None:
            if not 0 <= luck <= U8_MAX:
                raise ValueError("运 必须在 0..%d 之间: %d" % (U8_MAX, luck))
            self.set(base + LUCK_OFFSET, luck)

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


def _skill_label(sid):
    name = SKILL_NAMES.get(sid)
    return "%d(%s)" % (sid, name) if name else str(sid)


def _zero_note(act):
    if act == "deleted":
        return "  (值为 0: 已按游戏规则删除该条目)"
    if act == "inserted":
        return "  (新建条目)"
    if act == "noop":
        return "  (值未变化)"
    return ""


def load_save(path) -> P3RSave:
    with open(path, "rb") as f:
        blob = f.read()
    magic = int.from_bytes(blob[:4], "little")
    if magic == MAGIC_ENCRYPTED:
        return P3RSave(decrypt(blob), source_format="encrypted")
    if magic == MAGIC_GVAS:
        return P3RSave(blob, source_format="gvas")
    raise ValueError("未知存档格式 (前 4 字节 = %#010x)" % magic)


def save_save(save: P3RSave, path, force=None) -> bool:
    """写出存档。force: None 跟随输入格式 / True 强制加密 / False 强制明文"""
    do_encrypt = (save.source_format == "encrypted") if force is None else force
    data = save.to_gvas()
    with open(path, "wb") as f:
        f.write(encrypt(data) if do_encrypt else data)
    return do_encrypt


def list_personas(save: P3RSave):
    print("槽位  Persona  等级  累计经验       力/魔/耐/速/运")
    empty = []
    found = False
    for k, base in enumerate(save.persona_slot_bases()):
        pid = save.persona_id(base)
        if not pid:
            empty.append(k)
            continue
        found = True
        st, luck = save.get_persona_stats(base)
        print("%4d   %04x   %4d  %10d     %d/%d/%d/%d/%d"
              % (k, pid, save.get(base + LEVEL_OFFSET, 0), save.get(base + EXP_OFFSET, 0),
                 st[0], st[1], st[2], st[3], luck))
        sk = save.get_persona_skills(base)
        if sk:
            print("        技能: " + ", ".join(_skill_label(i) for i in sk))
    if not found:
        print("  (没有解析到任何 Persona 槽位)")
    if empty:
        print("\n空槽位: " + ", ".join("槽%d" % k for k in empty))


def _require_persona(base, opt):
    if base is None:
        raise SystemExit("%s 需要配合 --persona-code 使用" % opt)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="p3r_editor.py",
        description="P3R (Persona 3 Reload) Steam 版存档修改器")
    ap.add_argument("input", help="输入存档 (加密的 .sav 或已解密的 GVAS)")
    ap.add_argument("output", nargs="?", help="输出存档; 省略时只做读取/查看")
    ap.add_argument("--list-persona", action="store_true",
                    help="列出存档中的全部 Persona 槽位后退出 (只读)")
    ap.add_argument("--persona-code",
                    help="Persona 编号: 完整编号如 0134, 或旧简写如 3401")
    ap.add_argument("--skills", help="逗号分隔的技能 ID, 最多 8 个")
    ap.add_argument("--level", type=int, help="等级 (需配合 --persona-code)")
    ap.add_argument("--exp", type=int, help="累计总经验 (需配合 --persona-code)")
    ap.add_argument("--stats", help="五维: 力,魔,耐,速,运 (需配合 --persona-code)")
    ap.add_argument("--set", action="append", default=[], help="直接指定 数值ID=值, 可重复")
    ap.add_argument("--plain", action="store_true", help="强制输出未加密的 GVAS")
    ap.add_argument("--encrypt", action="store_true", help="强制输出加密存档")
    ap.add_argument("--dump", action="store_true", help="写出后重新读回并打印结果")
    a = ap.parse_args(argv)

    if a.plain and a.encrypt:
        ap.error("--plain 与 --encrypt 不能同时使用")

    try:
        save = load_save(a.input)
    except (OSError, ValueError) as e:
        raise SystemExit("读取失败: %s" % e)

    print("载入: %s (%s)  记录数=%d"
          % (a.input, "加密" if save.source_format == "encrypted" else "GVAS 明文",
             len(save.records)))

    if a.list_persona:
        print()
        list_personas(save)
        return 0

    if not a.output:
        ap.error("需要指定输出文件; 若只想查看内容请加 --list-persona")

    # ---- 定位 Persona ----
    base = None
    if a.persona_code:
        try:
            cands, mode = save.find_persona_bases(a.persona_code)
        except ValueError as e:
            raise SystemExit("参数错误: %s" % e)
        if not cands:
            raise SystemExit(
                "存档里找不到 Persona 编号 %s\n提示: 用 --list-persona 查看实际编号"
                % a.persona_code)
        if len(cands) > 1:
            lines = ["编号 %s 同时匹配到 %d 个槽位, 无法确定改哪一个:"
                     % (a.persona_code, len(cands))]
            for k, b, pid in cands:
                lines.append("    槽%d (base=%d)  完整编号 %04x" % (k, b, pid))
            lines.append("请改用完整编号, 例如: --persona-code %04x" % cands[0][2])
            raise SystemExit("\n".join(lines))
        k, base, pid = cands[0]
        if mode == "legacy":
            print("提示: %s 是旧简写, 按编号低字节匹配, 完整编号为 %04x"
                  % (a.persona_code, pid))
        st, luck = save.get_persona_stats(base)
        print("Persona %04x 在槽%d (base=%d)" % (pid, k, base))
        print("  当前: Lv%d  exp%d  力%d 魔%d 耐%d 速%d 运%d"
              % (save.get(base + LEVEL_OFFSET, 0), save.get(base + EXP_OFFSET, 0),
                 st[0], st[1], st[2], st[3], luck))
        print("  当前技能: %s" % (save.get_persona_skills(base) or "无"))

    # ---- 写入 ----
    try:
        if a.skills is not None:
            _require_persona(base, "--skills")
            ids = [int(x) for x in a.skills.replace(" ", "").split(",") if x]
            if not ids:
                raise SystemExit("--skills 至少需要一个技能 ID")
            save.set_persona_skills(base, ids)
            print("写入技能: " + ", ".join(_skill_label(i) for i in ids))

        if a.level is not None:
            _require_persona(base, "--level")
            act = save.set(base + LEVEL_OFFSET, a.level)
            print("写入等级: %d%s" % (a.level, _zero_note(act)))

        if a.exp is not None:
            _require_persona(base, "--exp")
            act = save.set(base + EXP_OFFSET, a.exp)
            print("写入累计经验: %d%s" % (a.exp, _zero_note(act)))

        if a.stats:
            _require_persona(base, "--stats")
            vals = [int(x) for x in a.stats.replace(" ", "").split(",")]
            if len(vals) != 5:
                raise SystemExit("--stats 需要 5 个值 (力,魔,耐,速,运), 收到 %d 个" % len(vals))
            save.set_persona_stats(base, st=vals[:4], luck=vals[4])
            print("写入五维: 力%d 魔%d 耐%d 速%d 运%d" % tuple(vals))

        for kv in a.set:
            if "=" not in kv:
                raise SystemExit("--set 需要 ID=值 的形式, 收到 %r" % kv)
            k, v = kv.split("=", 1)
            act = save.set(int(k, 0), int(v, 0))
            print("设置 ID %s = %s%s" % (k, v, _zero_note(act)))
    except ValueError as e:
        raise SystemExit("参数错误: %s" % e)

    # ---- 写出 ----
    force = True if a.encrypt else (False if a.plain else None)
    do_encrypt = save_save(save, a.output, force)
    print("已写出: %s (%s)" % (a.output, "加密" if do_encrypt else "GVAS 明文"))

    if a.dump:
        print("\n--- 复核 ---")
        chk = load_save(a.output)
        if base is not None:
            k = chk.persona_slot_bases().index(base)
            print("Persona %04x 在槽%d" % (chk.persona_id(base), k))
            print("  技能:", chk.get_persona_skills(base))
            print("  等级: %d  exp: %d"
                  % (chk.get(base + LEVEL_OFFSET, 0), chk.get(base + EXP_OFFSET, 0)))
            st, luck = chk.get_persona_stats(base)
            print("  五维: 力%d 魔%d 耐%d 速%d 运%d" % (st[0], st[1], st[2], st[3], luck))
        else:
            list_personas(chk)
    return 0


if __name__ == "__main__":
    sys.exit(main())
