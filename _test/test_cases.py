# -*- coding: utf-8 -*-
"""多场景自测：中文/全角标点、换行与 Tab、高错字率下的退格回改"""
import ctypes
import importlib.util
import os
import sys
import threading
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(os.path.dirname(HERE), "模拟打字.py")
spec = importlib.util.spec_from_file_location("human_typer", MAIN)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

CASES = [
    ("中文+全角标点", "你好，世界！测试123", dict(cps=40, typo=0.0)),
    ("换行与Tab", "第一行\n第二行\t结束", dict(cps=40, typo=0.0)),
    ("高错字率回改", "abcdefg 测试文字 xyz", dict(cps=40, typo=0.25)),
    ("开启焦点守卫", "守卫模式下的输入 ok", dict(cps=40, typo=0.0, guard=True,
                                                focus_wait=3)),
]

root = tk.Tk()
root.title("typing-cases")
root.geometry("520x180+160+160")
root.attributes("-topmost", True)
box = tk.Text(root, font=("Microsoft YaHei UI", 12))
box.pack(fill="both", expand=True, padx=8, pady=8)
root.update()

stop_keep = threading.Event()


def keep_focus():
    u = ctypes.WinDLL("user32")
    top = u.GetAncestor(root.winfo_id(), 2)
    while not stop_keep.is_set():
        try:
            u.SetForegroundWindow(top)
            u.SetFocus(top)
        except Exception:
            pass
        time.sleep(0.06)


threading.Thread(target=keep_focus, daemon=True).start()

results = []


def run_all():
    box.focus_force()
    root.update()
    time.sleep(0.7)
    for name, text, kw in CASES:
        box.delete("1.0", "end")
        root.update()
        kw_all = dict(guard=False, punct=False, jitter=0.2)
        kw_all.update(kw)
        typer = mod.Typer(mod.Config(**kw_all))
        t0 = time.time()
        done = typer.run(text, None)
        dt = time.time() - t0
        time.sleep(0.2)
        got = box.get("1.0", "end-1c")
        results.append((name, text, got, done, dt))
    stop_keep.set()
    root.after(50, root.destroy)


root.after(600, lambda: threading.Thread(target=run_all, daemon=True).start())
root.mainloop()

ok_all = True
for name, text, got, done, dt in results:
    ok = got == text
    ok_all = ok_all and ok
    print(f"[{'通过' if ok else '失败'}] {name}  注入 {done}/{len(text)}  "
          f"耗时 {dt:.2f}s")
    if not ok:
        print("   期望:", repr(text))
        print("   实际:", repr(got))
sys.exit(0 if ok_all else 1)
