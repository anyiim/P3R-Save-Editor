#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比修改前后存档, 验证只有预期位置被改动

用法:
    python verify.py 原档.sav 新档.sav [--persona-code 0134]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from p3r_editor import (decrypt, encrypt, load_save, REC_SIZE,
                        LEVEL_OFFSET, EXP_OFFSET)


def main():
    ap = argparse.ArgumentParser(description="改档前后完整性对比")
    ap.add_argument("orig", help="原始存档")
    ap.add_argument("new", help="修改后的存档")
    ap.add_argument("--persona-code",
                    help="额外复核指定 Persona (完整编号如 0134); 省略则复核第一个非空槽位")
    a = ap.parse_args()

    with open(a.orig, "rb") as f:
        orig_blob = f.read()
    with open(a.new, "rb") as f:
        new_blob = f.read()
    print("对比: %s  ->  %s" % (a.orig, a.new))

    # 1) 加密层自检
    ok = encrypt(decrypt(orig_blob)) == orig_blob
    print("[%s] 加密往返自检 (encrypt(decrypt(x)) == x)" % ("OK" if ok else "FAIL"))
    print("     原始: %d 字节 -> 修改后: %d 字节 (差 %+d)"
          % (len(orig_blob), len(new_blob), len(new_blob) - len(orig_blob)))

    # 2) 记录数
    o = load_save(a.orig)
    n = load_save(a.new)
    print("[OK] 记录数: %d -> %d (%+d)"
          % (len(o.records), len(n.records), len(n.records) - len(o.records)))

    # 3) 逐 ID 对比
    only_o = sorted(set(o.records) - set(n.records))
    only_n = sorted(set(n.records) - set(o.records))
    changed = sorted(k for k in set(o.records) & set(n.records)
                     if o.records[k][1] != n.records[k][1])

    def _brief(seq, limit=20):
        seq = list(seq)
        return seq[:limit] + (["...共 %d 条" % len(seq)] if len(seq) > limit else [])

    print("删除的 ID (%d 条): %s" % (len(only_o), _brief(only_o)))
    print("新增的 ID (%d 条): %s" % (len(only_n), _brief(only_n)))
    print("数值变化的 ID (%d 条): %s"
          % (len(changed),
             _brief(["%d (%d->%d)" % (k, o.records[k][1], n.records[k][1]) for k in changed])))
    if not (only_o or only_n or changed):
        print("[提示] 两份文件的数据完全一致")

    # 4) 其余 ID 的数值必须完全一致
    untouched = len(set(o.records) & set(n.records)) - len(changed)
    same = all(o.records[k][1] == n.records[k][1]
               for k in set(o.records) & set(n.records) if k not in changed)
    print("[%s] 其他 %d 条记录数值未被触碰" % ("OK" if same else "FAIL", untouched))

    # 5) 结构自检: 记录必须连续、间距 49、按 ID 升序、结尾 None
    for name, blob in (("原始", o), ("修改后", n)):
        offs = sorted(v[0] for v in blob.records.values())
        gaps = sorted({y - x for x, y in zip(offs, offs[1:])})
        by_offset = [k for k, _ in sorted(blob.records.items(), key=lambda kv: kv[1][0])]
        good = (gaps == [REC_SIZE]) and (by_offset == sorted(by_offset))
        print("[%s] %s: 记录间距=%s  ID升序=%s  终止符偏移=%#x"
              % ("OK" if good else "FAIL", name, gaps,
                 by_offset == sorted(by_offset), blob.terminator))

    # 6) Persona 复核
    base = None
    if a.persona_code:
        cands, _ = n.find_persona_bases(a.persona_code)
        if not cands:
            print("\n[警告] 新档里找不到 Persona 编号 %s" % a.persona_code)
            return 1
        base = cands[0][1]
    else:
        for b in n.persona_slot_bases():
            if n.persona_id(b):
                base = b
                break
        if base is None:
            print("\n[警告] 新档里没有非空 Persona 槽位")
            return 1

    slot = n.persona_slot_bases().index(base)
    print()
    print("[结果] Persona %04x (槽%d, base=%d)" % (n.persona_id(base), slot, base))
    print("       技能   : %s" % n.get_persona_skills(base))
    print("       原技能 : %s" % o.get_persona_skills(base))
    print("       等级   : %d  exp: %d" % (n.get(base + LEVEL_OFFSET, 0), n.get(base + EXP_OFFSET, 0)))
    print("       原等级 : %d  exp: %d" % (o.get(base + LEVEL_OFFSET, 0), o.get(base + EXP_OFFSET, 0)))
    st, luck = n.get_persona_stats(base)
    ost, oluck = o.get_persona_stats(base)
    print("       五维   : 力%d 魔%d 耐%d 速%d 运%d" % (st[0], st[1], st[2], st[3], luck))
    print("       原五维 : 力%d 魔%d 耐%d 速%d 运%d" % (ost[0], ost[1], ost[2], ost[3], oluck))
    return 0


if __name__ == "__main__":
    sys.exit(main())
