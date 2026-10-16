#!/usr/bin/env python3
"""
由 paper/Highlights.txt 生成 Elsevier 要求的可编辑 Highlights 文件。

为什么要有这一步
----------------
此前 Highlights.txt 与 Highlights.docx 是分别手工维护的，两者曾出现过
不同步：.txt 停在上一轮冻结的数字上，而稿件的数字已经更新。让 .docx 由
.txt 生成，两者就不再可能漂移；同时在这里检查 Elsevier 的硬性约束。

约束（Elsevier）：
  每条不超过 85 个字符（含空格）；
  3 到 5 条。

用法：python3 scripts/make_highlights.py
"""
import os
import sys

from docx import Document
from docx.shared import Pt

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SRC = os.path.join(ROOT, "paper", "Highlights.txt")
OUT = os.path.join(ROOT, "paper", "Highlights.docx")
MAX_LEN = 85


def main():
    lines = [ln.strip() for ln in open(SRC, encoding="utf-8") if ln.strip()]
    bad = [(ln, len(ln)) for ln in lines if len(ln) > MAX_LEN]
    if not 3 <= len(lines) <= 5:
        sys.exit(f"条数 {len(lines)} 不在 3--5 之间")
    if bad:
        for ln, n in bad:
            print(f"超长（{n} > {MAX_LEN}）: {ln}")
        sys.exit(1)

    doc = Document()
    style = doc.styles["List Bullet"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(12)
    for ln in lines:
        doc.add_paragraph(ln, style="List Bullet")
    doc.save(OUT)

    print(f"写出 {OUT}")
    for ln in lines:
        print(f"  [{len(ln):>2}] {ln}")


if __name__ == "__main__":
    main()
