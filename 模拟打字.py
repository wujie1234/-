# -*- coding: utf-8 -*-
"""
模拟打字机 (Human Typer)
--------------------------------------------------
用途：某些网页输入框禁止粘贴，只能手动敲。本程序把文字逐字"敲"进当前
      光标所在的输入框，节奏、停顿、错字回改都模仿真人。

原理：调用 Win32 SendInput + KEYEVENTF_UNICODE 直接注入 Unicode 字符，
      不经过剪贴板、不依赖输入法，因此中文/英文/符号都能打。

用法：
  图形界面：双击 启动.bat
  命令行  ：python 模拟打字.py -f 文本.txt -d 5 -c 6
            python 模拟打字.py -t "要输入的文字" -d 3
       -f/--file   文本文件（UTF-8）
       -t/--text   直接给文字
       -d/--delay  开始前倒计时秒数（默认 5，期间请点好目标输入框）
       -c/--cps    每秒打几个字（默认 6）
       --no-typo   关闭错字模拟

热键（打字过程中）：ESC 立即停止 · F9 暂停/继续
"""

import ctypes
import ctypes.wintypes as wt
import math
import os
import random
import sys
import threading
import time

# ============================================================
# 一、Win32 键盘注入
# ============================================================

user32 = ctypes.WinDLL("user32", use_last_error=True)

# 把系统计时精度提到 1ms，否则 time.sleep 会被系统拉到 ~15ms 一档，
# 高速打字时节奏会明显偏慢
try:
    _winmm = ctypes.WinDLL("winmm")
    _winmm.timeBeginPeriod(1)
    import atexit
    atexit.register(_winmm.timeEndPeriod, 1)
except Exception:
    pass

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wt.LONG),
        ("dy", wt.LONG),
        ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wt.DWORD),
        ("wParamL", wt.WORD),
        ("wParamH", wt.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wt.UINT

VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_F9 = 0x78


def _ki(wVk=0, wScan=0, dwFlags=0):
    return INPUT(type=INPUT_KEYBOARD,
                 u=_INPUTUNION(ki=KEYBDINPUT(wVk, wScan, dwFlags, 0, 0)))


def _send(events):
    """一次 SendInput 提交，返回是否全部注入成功"""
    n = len(events)
    arr = (INPUT * n)(*events)
    sent = user32.SendInput(n, arr, ctypes.sizeof(INPUT))
    return sent == n


def _key_down(vk):
    return _send([_ki(vk, 0, 0)])


def _key_up(vk):
    return _send([_ki(vk, 0, KEYEVENTF_KEYUP)])


def _tap(vk, hold=0.025):
    _key_down(vk)
    time.sleep(hold)
    _key_up(vk)


def _send_char(ch):
    """注入一个字符（自动处理 emoji 等代理对），不经过输入法"""
    data = ch.encode("utf-16-le")
    ok = True
    for i in range(0, len(data), 2):
        cu = data[i] | (data[i + 1] << 8)
        ok = _send([
            _ki(0, cu, KEYEVENTF_UNICODE),
            _ki(0, cu, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
        ]) and ok
    return ok


def _key_is_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def foreground_window():
    """当前前台窗口句柄，用于确认字没打到别的窗口里"""
    return user32.GetForegroundWindow()


def window_title(hwnd):
    if not hwnd:
        return ""
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


# ============================================================
# 二、像人一样的节奏模型
# ============================================================

SOFT_PUNCT = set("，,、；;：:")
HARD_PUNCT = set("。.！!？?…—~～")
ASCII_POOL = "abcdefghijklmnopqrstuvwxyz0123456789"


def _build_neighbors():
    """QWERTY 相邻键，用来生成"手滑"打错的字符"""
    rows = ["1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm"]
    nb = {}
    for row in rows:
        for i, ch in enumerate(row):
            cand = []
            if i > 0:
                cand.append(row[i - 1])
            if i + 1 < len(row):
                cand.append(row[i + 1])
            if cand:
                nb[ch] = cand
    return nb


NEIGHBORS = _build_neighbors()


def is_cjk(ch):
    o = ord(ch)
    return (0x3400 <= o <= 0x4DBF or 0x4E00 <= o <= 0x9FFF or
            0xF900 <= o <= 0xFAFF or 0x3040 <= o <= 0x30FF or
            0xAC00 <= o <= 0xD7AF or 0xFF00 <= o <= 0xFFEF)


class Config:
    """打字参数"""

    def __init__(self, cps=3.0, jitter=0.45, typo=0.008, punct=True,
                 think=0.03, pause_scale=1.0, guard=True, focus_wait=20.0):
        self.cps = max(0.5, float(cps))
        self.jitter = min(1.0, max(0.0, float(jitter)))
        self.typo = min(0.05, max(0.0, float(typo)))
        self.punct = bool(punct)
        self.think = float(think)
        self.pause_scale = float(pause_scale)
        self.guard = bool(guard)          # 焦点一变就停，防止打错窗口
        self.focus_wait = float(focus_wait)  # 焦点被切走后等多久（秒）

    @property
    def base(self):
        return 1.0 / self.cps


class Typer:
    """把文本逐字注入到当前焦点窗口"""

    def __init__(self, config=None):
        self.cfg = config or Config()
        self.stop_flag = threading.Event()
        self.pause_flag = threading.Event()
        self.typed = 0
        self.failed = False
        self.lost_focus = False
        self.stopped_by = ""      # esc / focus / inject / done

    # ---------- 内部工具 ----------
    def _wait(self, seconds):
        """可被打断的等待"""
        end = time.time() + seconds
        while time.time() < end:
            if self.stop_flag.is_set():
                return False
            while self.pause_flag.is_set():
                if self.stop_flag.is_set():
                    return False
                time.sleep(0.05)
            time.sleep(min(0.02, max(0.0, end - time.time())))
        return not self.stop_flag.is_set()

    def _char_delay(self, ch):
        c = self.cfg
        d = c.base * math.exp(random.gauss(0, 0.30 * c.jitter + 0.10))
        d = max(c.base * 0.35, min(d, c.base * 3.4))
        if c.punct:
            if ch in HARD_PUNCT:
                d += random.uniform(0.18, 0.50) * c.pause_scale
            elif ch in SOFT_PUNCT:
                d += random.uniform(0.08, 0.26) * c.pause_scale
            elif ch == "\n":
                d += random.uniform(0.25, 0.75) * c.pause_scale
            elif ch == " ":
                d += random.uniform(0.02, 0.10) * c.pause_scale
        if random.random() < c.think:
            d += random.uniform(0.35, 1.10) * c.pause_scale
        return d

    def _type_one(self, ch):
        if ch == "\n":
            _tap(VK_RETURN)
            return True
        if ch == "\t":
            _tap(VK_TAB)
            return True
        return _send_char(ch)

    def _maybe_typo(self, ch):
        """模拟手滑：先打错，再退格改回来"""
        if self.cfg.typo <= 0:
            return
        if ch in NEIGHBORS and random.random() < self.cfg.typo:
            wrong = random.choice(NEIGHBORS[ch]) if random.random() < 0.6 \
                else random.choice(ASCII_POOL)
            if wrong == ch:
                return
            self._type_one(wrong)
            self._wait(random.uniform(0.09, 0.22))
            _tap(VK_BACK)
            self._wait(random.uniform(0.07, 0.18))
        elif is_cjk(ch) and random.random() < self.cfg.typo * 0.5:
            # 中文：打成后立刻删掉重打，像"拼音选错又删掉"
            self._type_one(ch)
            self._wait(random.uniform(0.10, 0.25))
            _tap(VK_BACK)
            self._wait(random.uniform(0.08, 0.20))

    def _settle_target(self, max_wait=12.0):
        """等前台窗口稳定再开始，避免第一个字打进正在切换的窗口"""
        end = time.time() + max_wait
        prev = foreground_window()
        while time.time() < end:
            if self.stop_flag.is_set():
                return prev
            time.sleep(0.45)
            cur = foreground_window()
            if cur and cur == prev:
                return cur
            prev = cur
        return prev

    def _wait_focus_back(self, target):
        """焦点被切走后等它回到目标窗口，超时返回 False"""
        deadline = time.time() + self.cfg.focus_wait
        while time.time() < deadline:
            if self.stop_flag.is_set():
                return False
            if _key_is_down(VK_ESCAPE):
                self.stop_flag.set()
                self.stopped_by = "esc"
                return False
            if foreground_window() == target:
                time.sleep(0.15)          # 回来后再确认一下，不是一闪而过
                if foreground_window() == target:
                    return True
                continue
            time.sleep(0.06)
        return False

    # ---------- 主流程 ----------
    def run(self, text, on_progress=None):
        self.stop_flag.clear()
        self.pause_flag.clear()
        self.typed = 0
        self.failed = False
        self.lost_focus = False
        self.stopped_by = ""

        text = text.replace("\r\n", "\n").replace("\r", "\n")
        total = len(text)

        # 开始前的迟疑，让焦点切换更自然
        if not self._wait(random.uniform(0.25, 0.7)):
            return 0

        target = self._settle_target() if self.cfg.guard else 0
        next_at = time.time()          # 用于抵消注入开销的时间漂移

        last_report = 0.0
        for i, ch in enumerate(text):
            if self.stop_flag.is_set():
                break
            while self.pause_flag.is_set():
                if self.stop_flag.is_set():
                    break
                time.sleep(0.05)
            if self.stop_flag.is_set():
                break

            # 焦点守卫：目标窗口被切走时先暂停等它回来（例如弹窗抢了焦点），
            # 超过 focus_wait 还没回来才停手，避免把字打进别的窗口
            if target and foreground_window() != target:
                if on_progress:
                    on_progress(self.typed, total, "lost")
                if self._wait_focus_back(target):
                    if on_progress:
                        on_progress(self.typed, total, "running")
                else:
                    self.lost_focus = True
                    if not self.stopped_by:
                        self.stopped_by = "focus"
                    self.stop_flag.set()
                    break

            # 全局热键：ESC 停止 / F9 暂停
            if _key_is_down(VK_ESCAPE):
                self.stopped_by = "esc"
                self.stop_flag.set()
                break
            if _key_is_down(VK_F9):
                while _key_is_down(VK_F9):      # 先等松手，避免连跳
                    time.sleep(0.03)
                self.pause_flag.set()
                if on_progress:
                    on_progress(self.typed, total, "paused")
                while not self.stop_flag.is_set():
                    if _key_is_down(VK_F9):
                        while _key_is_down(VK_F9):
                            time.sleep(0.03)
                        break
                    time.sleep(0.04)
                self.pause_flag.clear()
                if on_progress:
                    on_progress(self.typed, total, "running")
                if self.stop_flag.is_set():
                    break

            self._maybe_typo(ch)
            if self.stop_flag.is_set():
                break
            if not self._type_one(ch):
                self.failed = True
                self.stopped_by = "inject"
                break
            self.typed = i + 1

            now = time.time()
            if on_progress and (now - last_report > 0.12 or i == total - 1):
                on_progress(self.typed, total, "running")
                last_report = now

            next_at += self._char_delay(ch)
            wait = next_at - time.time()
            if wait > 0:
                if not self._wait(wait):
                    break
            else:
                next_at = time.time()   # 已经落后就重置，不让误差累积

        if not self.stopped_by:
            self.stopped_by = "done" if self.typed >= total else "esc"
        return self.typed


def estimate_seconds(text, cfg):
    """估算总耗时（不实际输入）"""
    probe = Typer(cfg)
    return sum(probe._char_delay(ch) for ch in text.replace("\r\n", "\n"))


# ============================================================
# 三、图形界面
# ============================================================

def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    BG = "#f4f5f7"
    CARD = "#ffffff"
    TEXT = "#1f2328"
    MUTED = "#6b7280"
    ACCENT = "#2563eb"
    BORDER = "#e3e5e8"

    root = tk.Tk()
    root.title("模拟打字机 · 逐字输入")
    root.geometry("780x780")
    root.minsize(700, 660)
    root.configure(bg=BG)

    try:
        root.iconbitmap(default="")
    except Exception:
        pass

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD, relief="flat")
    style.configure("TLabel", background=BG, foreground=TEXT,
                    font=("Microsoft YaHei UI", 10))
    style.configure("Card.TLabel", background=CARD, foreground=TEXT,
                    font=("Microsoft YaHei UI", 10))
    style.configure("Hint.TLabel", background=BG, foreground=MUTED,
                    font=("Microsoft YaHei UI", 9))
    style.configure("CardHint.TLabel", background=CARD, foreground=MUTED,
                    font=("Microsoft YaHei UI", 9))
    style.configure("Title.TLabel", background=BG, foreground=TEXT,
                    font=("Microsoft YaHei UI", 15, "bold"))
    style.configure("Step.TLabel", background=BG, foreground=ACCENT,
                    font=("Microsoft YaHei UI", 9, "bold"))
    style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(12, 7))
    style.configure("Go.TButton", font=("Microsoft YaHei UI", 11, "bold"),
                    padding=(18, 10))
    style.map("Go.TButton",
              background=[("active", "#1d4ed8"), ("!disabled", ACCENT)],
              foreground=[("!disabled", "#ffffff")])
    style.configure("TCheckbutton", background=CARD, foreground=TEXT,
                    font=("Microsoft YaHei UI", 10))
    style.configure("Horizontal.TScale", background=CARD)
    style.configure("TProgressbar", background=ACCENT, troughcolor="#e9ecf1",
                    borderwidth=0, thickness=8)

    # ---------- 顶部标题 ----------
    head = ttk.Frame(root)
    head.pack(fill="x", padx=18, pady=(16, 8))
    ttk.Label(head, text="模拟打字机", style="Title.TLabel").pack(side="left")
    ttk.Label(head, text="  逐字敲入，绕过禁止粘贴的输入框",
              style="Hint.TLabel").pack(side="left", pady=(6, 0))

    # ---------- 文本区 ----------
    ttk.Label(root, text="① 粘贴要输入的文字（这里可以正常粘贴）",
              style="Step.TLabel").pack(anchor="w", padx=18)

    text_card = tk.Frame(root, bg=CARD, highlightbackground=BORDER,
                         highlightthickness=1)

    txt = tk.Text(text_card, wrap="word", undo=True, bd=0, relief="flat",
                  bg=CARD, fg=TEXT, insertbackground=ACCENT,
                  font=("Microsoft YaHei UI", 11), padx=12, pady=10,
                  spacing1=2, spacing3=3)
    scroll = ttk.Scrollbar(text_card, orient="vertical", command=txt.yview)
    txt.configure(yscrollcommand=scroll.set)
    txt.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")

    # ---------- 参数区 ----------
    # 注意：底部几块统一在最后按 bottom 顺序打包，文本框最后占满剩余空间，
    # 否则可扩展的文本框会把下面的控件全挤出窗口
    lbl_step2 = ttk.Label(root, text="② 调节节奏", style="Step.TLabel")

    card = tk.Frame(root, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
    inner = tk.Frame(card, bg=CARD)
    inner.pack(fill="x", padx=14, pady=10)
    inner.columnconfigure(1, weight=1)

    v_cps = tk.DoubleVar(value=3.0)
    v_jit = tk.DoubleVar(value=45.0)
    v_typo = tk.DoubleVar(value=0.8)
    v_punct = tk.BooleanVar(value=True)
    v_guard = tk.BooleanVar(value=True)
    v_delay = tk.IntVar(value=5)

    def add_slider(row, label, var, frm, to, res, fmt):
        ttk.Label(inner, text=label, style="Card.TLabel").grid(
            row=row, column=0, sticky="w", pady=4)
        val_lbl = ttk.Label(inner, text="", style="Card.TLabel", width=19)
        val_lbl.grid(row=row, column=2, sticky="e", pady=4)

        def upd(*_):
            val_lbl.config(text=fmt(var.get()))
            refresh_estimate()

        sc = ttk.Scale(inner, from_=frm, to=to, variable=var,
                       orient="horizontal", command=lambda v: (round_to(var, res), upd()))
        sc.grid(row=row, column=1, sticky="ew", padx=10, pady=4)
        upd()
        return sc

    def round_to(var, res):
        var.set(round(var.get() / res) * res)

    # ---------- 状态 ----------
    v_status = tk.StringVar(value="就绪")
    v_count = tk.StringVar(value="0 字")
    v_est = tk.StringVar(value="")

    def current_text():
        return txt.get("1.0", "end-1c")

    def refresh_estimate(*_):
        t = current_text()
        n = len(t)
        v_count.set(f"{n} 字")
        if n == 0:
            v_est.set("")
            return
        secs = estimate_seconds(t, build_config())
        m, s = divmod(int(secs), 60)
        v_est.set(f"预计 {m} 分 {s} 秒" if m else f"预计 {s} 秒")

    def build_config():
        return Config(cps=v_cps.get(), jitter=v_jit.get() / 100.0,
                      typo=v_typo.get() / 100.0, punct=v_punct.get(),
                      guard=v_guard.get())

    add_slider(0, "速度", v_cps, 1.0, 25.0, 0.5,
               lambda v: f"{v:.1f} 字/秒 = {v * 60:.0f} 字/分")
    add_slider(1, "节奏抖动", v_jit, 0.0, 100.0, 5.0,
               lambda v: f"{v:.0f} %")
    add_slider(2, "错字回改率", v_typo, 0.0, 5.0, 0.1,
               lambda v: f"{v:.1f} %")

    opt = tk.Frame(inner, bg=CARD)
    opt.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))

    def checkbox(parent, text, var, cmd=None):
        # clam 主题的指示器会画成叉号，这里用经典复选框保证是对勾
        return tk.Checkbutton(parent, text=text, variable=var, command=cmd,
                              bg=CARD, fg=TEXT, activebackground=CARD,
                              activeforeground=TEXT, selectcolor="#ffffff",
                              bd=0, highlightthickness=0, cursor="hand2",
                              font=("Microsoft YaHei UI", 10))

    checkbox(opt, "标点/换行处停顿", v_punct, refresh_estimate).pack(side="left")
    checkbox(opt, "焦点跑掉就自动停", v_guard).pack(side="left", padx=(16, 0))
    ttk.Label(opt, text="   倒计时", style="Card.TLabel").pack(side="left")
    sp = ttk.Spinbox(opt, from_=1, to=30, width=4, textvariable=v_delay)
    sp.pack(side="left", padx=4)
    ttk.Label(opt, text="秒（留时间点中目标输入框）",
              style="CardHint.TLabel").pack(side="left")

    # ---------- 操作区 ----------
    bar = tk.Frame(root, bg=BG)

    prog = ttk.Progressbar(bar, mode="determinate", maximum=100)
    prog.pack(fill="x", pady=(0, 8))

    row = tk.Frame(bar, bg=BG)
    row.pack(fill="x")

    btn_go = ttk.Button(row, text="开始打字  (Ctrl+Enter)", style="Go.TButton")
    btn_go.pack(side="left")
    btn_stop = ttk.Button(row, text="停止  (ESC)", state="disabled")
    btn_stop.pack(side="left", padx=(10, 0))

    def do_paste_clip():
        try:
            data = root.clipboard_get()
        except Exception:
            data = ""
        if data:
            txt.delete("1.0", "end")
            txt.insert("1.0", data)
            refresh_estimate()
        else:
            messagebox.showinfo("提示", "剪贴板里没有文字。")

    def do_import():
        path = filedialog.askopenfilename(
            title="选择文本文件",
            filetypes=[("文本文件", "*.txt *.md *.csv *.json *.log"), ("所有文件", "*.*")])
        if not path:
            return
        for enc in ("utf-8-sig", "utf-8", "gbk", "utf-16"):
            try:
                with open(path, "r", encoding=enc) as f:
                    data = f.read()
                break
            except UnicodeDecodeError:
                continue
        else:
            messagebox.showerror("读取失败", "无法识别该文件的编码。")
            return
        txt.delete("1.0", "end")
        txt.insert("1.0", data)
        refresh_estimate()
        v_status.set(f"已载入 {os.path.basename(path)}")

    ttk.Button(row, text="读剪贴板", command=do_paste_clip).pack(side="left", padx=(10, 0))
    ttk.Button(row, text="导入文件", command=do_import).pack(side="left", padx=(8, 0))
    ttk.Button(row, text="清空",
               command=lambda: (txt.delete("1.0", "end"), refresh_estimate())
               ).pack(side="left", padx=(8, 0))

    # 字数 / 预计耗时放在标题行右侧，避免和按钮抢空间
    ttk.Label(head, textvariable=v_est, style="Hint.TLabel").pack(
        side="right", pady=(6, 0))
    ttk.Label(head, textvariable=v_count, style="Hint.TLabel").pack(
        side="right", padx=(0, 14), pady=(6, 0))

    # ---------- 状态栏 ----------
    foot = tk.Frame(root, bg=BG)
    ttk.Label(foot, textvariable=v_status, style="Hint.TLabel").pack(side="left")
    ttk.Label(foot, text="打字中：ESC 停止 · F9 暂停/继续",
              style="Hint.TLabel").pack(side="right")

    # ---------- 统一布局 ----------
    # 从下往上依次占位：状态栏 → 操作区 → 参数卡片 → 参数标题，
    # 最后文本框填满中间剩余空间
    foot.pack(side="bottom", fill="x", padx=18, pady=(0, 12))
    bar.pack(side="bottom", fill="x", padx=18, pady=(0, 8))
    card.pack(side="bottom", fill="x", padx=18, pady=(6, 10))
    lbl_step2.pack(side="bottom", anchor="w", padx=18)
    text_card.pack(side="top", fill="both", expand=True, padx=18, pady=(6, 12))

    # ---------- 打字流程 ----------
    state = {"running": False, "typer": None}

    def set_busy(busy):
        state["running"] = busy
        btn_go.config(state="disabled" if busy else "normal")
        btn_stop.config(state="normal" if busy else "disabled")

    def worker(text, countdown):
        typer = state["typer"]
        # 倒计时：留时间让用户点中网页输入框
        for left in range(countdown, 0, -1):
            if typer.stop_flag.is_set():
                root.after(0, _finish_impl, 0, len(text), "已取消")
                return
            root.after(0, lambda sec=left: v_status.set(
                f"{sec} 秒后开始 —— 现在去点一下网页里的输入框，让光标停在框里"))
            time.sleep(1.0)

        def on_prog(done, total, phase="running"):
            pct = done * 100.0 / max(1, total)
            if phase == "paused":
                root.after(0, lambda: v_status.set(
                    f"已暂停 {done}/{total} 字 · 再按 F9 继续，ESC 停止"))
                return
            if phase == "lost":
                root.after(0, lambda: v_status.set(
                    f"焦点被切走了，正在等你点回输入框…（{done}/{total} 字）"))
                return
            root.after(0, lambda: (prog.config(value=pct),
                                   v_status.set(f"输入中… {done}/{total} 字")))

        t0 = time.time()
        done = typer.run(text, on_prog)
        elapsed = time.time() - t0
        root.after(0, _finish_impl, done, len(text), elapsed)

    def _finish_impl(done, total, info):
        txt.config(state="normal")
        set_busy(False)
        prog.config(value=done * 100.0 / max(1, total) if total else 0)
        typer = state["typer"]

        if isinstance(info, str):
            v_status.set(info)
            return

        if done >= total:
            v_status.set(f"完成：{done} 字，用时 {info:.1f} 秒")
            try:
                import winsound
                winsound.MessageBeep(0x40)
            except Exception:
                pass
            return

        # 未打完：把剩下的字保留在输入框，直接再点开始即可续打
        if done > 0:
            rest = current_text().replace("\r\n", "\n")[done:]
            txt.delete("1.0", "end")
            txt.insert("1.0", rest)
            refresh_estimate()

        if typer and typer.lost_focus:
            v_status.set(f"目标窗口焦点被切走，已自动停止（完成 {done}/{total} 字），"
                         f"剩余部分已保留")
        elif done == 0:
            v_status.set("已停止（没有输入任何内容）")
        else:
            v_status.set(f"已停止：完成 {done}/{total} 字，剩余部分已保留在输入框，可直接再次开始")

    def start(*_):
        if state["running"]:
            return
        text = current_text()
        if not text.strip():
            messagebox.showinfo("提示", "先粘贴要输入的文字。")
            return
        typer = Typer(build_config())
        state["typer"] = typer
        set_busy(True)
        prog.config(value=0)
        txt.config(state="disabled")
        v_status.set("准备中…")
        try:
            countdown = max(0, int(float(v_delay.get())))
        except Exception:
            countdown = 5
        threading.Thread(target=worker, daemon=True,
                         args=(text, countdown)).start()

    def stop():
        if state["typer"]:
            state["typer"].stop_flag.set()

    btn_go.config(command=start)
    btn_stop.config(command=stop)

    txt.bind("<<Modified>>", lambda e: (txt.edit_modified(False), refresh_estimate()))
    txt.bind("<Control-Return>", start)
    root.bind("<Control-Return>", start)
    root.bind("<Escape>", lambda e: stop())
    root.protocol("WM_DELETE_WINDOW",
                  lambda: (stop(), root.after(120, root.destroy)))

    txt.focus_set()
    root.after(200, refresh_estimate)
    root.mainloop()


# ============================================================
# 四、命令行入口
# ============================================================

def run_cli(argv):
    import argparse

    p = argparse.ArgumentParser(description="模拟真人逐字输入到当前焦点窗口")
    p.add_argument("-f", "--file", help="文本文件路径（UTF-8/GBK 自动识别）")
    p.add_argument("-t", "--text", help="直接指定文字")
    p.add_argument("-d", "--delay", type=int, default=5,
                   help="开始前倒计时秒数，默认 5")
    p.add_argument("-c", "--cps", type=float, default=3.0,
                   help="每秒字符数，默认 3（约 180 字/分）")
    p.add_argument("--jitter", type=float, default=0.45, help="节奏抖动 0~1，默认 0.45")
    p.add_argument("--typo", type=float, default=0.008, help="错字回改率 0~0.05，默认 0.008")
    p.add_argument("--no-typo", action="store_true", help="关闭错字模拟")
    p.add_argument("--no-pause", action="store_true", help="关闭标点停顿")
    p.add_argument("--no-guard", action="store_true",
                   help="关闭焦点守卫（默认焦点一变就停，防止打错窗口）")
    p.add_argument("--focus-wait", type=float, default=20.0,
                   help="焦点被切走后等它回到目标窗口的秒数，默认 20")
    args = p.parse_args(argv)

    if args.file:
        data = None
        for enc in ("utf-8-sig", "utf-8", "gbk", "utf-16"):
            try:
                with open(args.file, "r", encoding=enc) as f:
                    data = f.read()
                break
            except UnicodeDecodeError:
                continue
        if data is None:
            print("无法识别文件编码:", args.file)
            return 2
    elif args.text is not None:
        data = args.text
    else:
        print("从标准输入读取文字，粘贴后按 Ctrl+Z 回车结束：")
        data = sys.stdin.read()

    if not data.strip():
        print("没有内容可输入。")
        return 2

    cfg = Config(cps=args.cps, jitter=args.jitter,
                 typo=0.0 if args.no_typo else args.typo,
                 punct=not args.no_pause, guard=not args.no_guard,
                 focus_wait=args.focus_wait)
    typer = Typer(cfg)

    print(f"共 {len(data)} 字，速度 {cfg.cps} 字/秒，"
          f"预计 {estimate_seconds(data, cfg):.0f} 秒")
    for left in range(args.delay, 0, -1):
        print(f"\r{left} 秒后开始，请点中目标输入框…", end="", flush=True)
        time.sleep(1.0)
    print("\r开始输入…                    ")

    last = [0.0]

    def on_prog(d, t, phase="running"):
        # 节流输出，避免几千行进度刷屏
        if phase == "paused":
            print(f"\r进度 {d}/{t}  [已暂停，F9 继续]", end="", flush=True)
            return
        if phase == "lost":
            print(f"\r进度 {d}/{t}  [焦点被切走，等你点回输入框…]", end="", flush=True)
            return
        now = time.time()
        if now - last[0] > 1.0 or d == t:
            last[0] = now
            print(f"\r进度 {d}/{t}  ({d * 100 // max(1, t)}%)", end="", flush=True)

    t0 = time.time()
    done = typer.run(data, on_prog)
    elapsed = time.time() - t0
    print(f"\n完成 {done}/{len(data)} 字，用时 {elapsed:.1f} 秒")

    reason = {
        "focus": f"目标窗口焦点被切走，超过 {cfg.focus_wait:.0f} 秒没回来，已停手",
        "inject": "字符注入被系统拒绝（目标窗口可能以管理员权限运行，"
                  "请用管理员身份重开本程序）",
        "esc": "已手动停止",
    }.get(typer.stopped_by)
    if reason:
        print("停止原因：" + reason)

    # 中途停下：把没打完的部分存下来，下次直接接着打
    if 0 < done < len(data):
        rest = data.replace("\r\n", "\n")[done:]
        base = os.path.splitext(args.file)[0] if args.file else "剩余文本"
        rest_path = base + "-剩余.txt"
        try:
            with open(rest_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(rest)
            print(f"剩余 {len(rest)} 字已保存到 {rest_path}")
        except OSError as e:
            print(f"剩余内容保存失败：{e}")
        return 1
    return 0 if done == len(data) else 1


def _attach_console():
    """打包成窗口程序后没有控制台，带参数运行时挂一个，否则输出会静默丢失"""
    if not getattr(sys, "frozen", False):
        return
    try:
        kernel32 = ctypes.WinDLL("kernel32")
        if not kernel32.AttachConsole(-1):     # ATTACH_PARENT_PROCESS
            kernel32.AllocConsole()
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = sys.stdout
    except Exception:
        pass


if __name__ == "__main__":
    if len(sys.argv) > 1:
        _attach_console()
        sys.exit(run_cli(sys.argv[1:]))
    run_gui()
