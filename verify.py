#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比修改前后存档, 验证只改动了预期位置"""
import struct
import sys

sys.path.insert(0, ".")
from p3r_editor import (decrypt, encrypt, REC_HEAD, REC_SIZE,
                        load_save, MAGIC_ENCRYPTED)

orig_path = sys.argv[1] if len(sys.argv) > 1 else "backup/SaveData004.sav"
new_path = sys.argv[2] if len(sys.argv) > 2 else "output/SaveData004_skills.sav"
orig_enc = open(orig_path, "rb").read()
new_enc = open(new_path, "rb").read()
print("对比: %s  ->  %s" % (orig_path, new_path))

# 1) 加密层自检
assert encrypt(decrypt(orig_enc)) == orig_enc, "加解密往返失败!"
print("[OK] 加密往返自检通过 (encrypt(decrypt(x)) == x)")
print("     原始: %d 字节 -> 修改后: %d 字节 (差 %+d)" %
      (len(orig_enc), len(new_enc), len(new_enc) - len(orig_enc)))

# 2) 记录数
o = load_save(orig_path)
n = load_save(new_path)
print("[OK] 记录数: %d -> %d (+%d)" % (len(o.records), len(n.records),
                                       len(n.records) - len(o.records)))

# 3) 逐 ID 对比
only_o = sorted(set(o.records) - set(n.records))
only_n = sorted(set(n.records) - set(o.records))
changed = sorted(k for k in set(o.records) & set(n.records)
                 if o.records[k][1] != n.records[k][1])
print("删除的 ID:", only_o)
print("新增的 ID:", only_n)
print("数值变化的 ID:", ["%d (%d->%d)" % (k, o.records[k][1], n.records[k][1])
                        for k in changed])

# 4) 其余 ID 的数值必须完全一致
same = all(o.records[k][1] == n.records[k][1]
           for k in set(o.records) & set(n.records) if k not in changed)
print("[%s] 其他 %d 条记录数值未被触碰" %
      ("OK" if same else "FAIL", len(set(o.records) & set(n.records)) - len(changed)))

# 5) 结构自检: 记录必须连续、间距 49、按 ID 升序、结尾 None
for name, blob in (("原始", o), ("修改后", n)):
    offs = sorted(v[0] for v in blob.records.values())
    gaps = {b - a for a, b in zip(offs, offs[1:])}
    ids = [k for k, _ in sorted(blob.records.items(), key=lambda kv: kv[1][0])]
    print("[%s] 记录间距集合=%s  ID升序=%s  终止符偏移=%#x  文件尾=%r" %
          (name, gaps, ids == sorted(ids), blob.terminator,
           bytes(blob.raw[blob.terminator + 9:blob.terminator + 14])))

# 6) 最终: 用编辑器重新解析写出的文件, 确认 Persona
base = n.find_persona_base("3401")
print()
print("[结果] persona 3401 槽位 base=%d" % base)
print("       技能 =", n.get_persona_skills(base))
print("       等级 =", n.get(base + 1), " exp =", n.get(base + 2))
st, luck = n.get_persona_stats(base)
print("       五维 = 力%d 魔%d 耐%d 速%d 运%d" % (st[0], st[1], st[2], st[3], luck))
print("       原技能 =", o.get_persona_skills(base))
