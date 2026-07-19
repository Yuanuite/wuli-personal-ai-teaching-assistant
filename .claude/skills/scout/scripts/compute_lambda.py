#!/usr/bin/env python3
"""
信息率计算器。从 stdin 读取 TSV（name\tp\tT_min），输出按 λ 降序排列的表格。
p=成功概率(0-1), T=预期耗时(分钟), λ = ln(1/p) / T
"""
import sys
import math

lines = sys.stdin.read().strip().split('\n')
if not lines:
    sys.exit(0)

entries = []
for line in lines:
    parts = line.strip().split('\t')
    if len(parts) < 3:
        continue
    name, p_str, t_str = parts[0], parts[1], parts[2]
    try:
        p = float(p_str)
        T = float(t_str)
    except ValueError:
        continue
    if p >= 1.0:
        lam = 0.0
    elif p <= 0.0:
        lam = float('inf')
    else:
        lam = math.log(1.0 / p) / T
    entries.append((lam, name, p, T))

entries.sort(key=lambda x: -x[0])

# 输出表格
print(f"{'动作':<30} {'p':>6} {'T(min)':>8} {'λ':>10}")
print("-" * 58)
for lam, name, p, T in entries:
    lam_str = f"{lam:.4f}" if lam > 0 else "—"
    print(f"{name:<30} {p:.0%} {T:>8.0f} {lam_str:>10}")
