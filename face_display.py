"""
面部模拟显示模块
使用 tkinter 绘制人头，支持眼睛、眉毛、嘴巴等关节的实时更新。
嘴巴有4个关节: 下巴(jaw)、右微笑(smile_r)、左微笑(smile_l)、上唇(lip_up)

GUI 提供: 文本输入框、说话按钮、停止按钮、退出按钮
"""

import threading
import queue
import tkinter as tk
import math

# ==========================
# 舵机ID → 视觉参数映射
# ==========================
SERVO_RANGE = {
    2:  {"name": "neck",     "min": 1000, "center": 1500, "max": 2000},
    3:  {"name": "jaw",      "min": 1000, "center": 1450, "max": 1450},
    4:  {"name": "smile_r",  "min": 1200, "center": 1500, "max": 1900},
    5:  {"name": "smile_l",  "min": 1200, "center": 1500, "max": 1900},
    6:  {"name": "lip_up",   "min": 1200, "center": 1500, "max": 1900},
    7:  {"name": "eye_up_r", "min": 1200, "center": 1500, "max": 1800},
    8:  {"name": "blink_r",  "min": 1000, "center": 1800, "max": 2000},
    9:  {"name": "eye_lr_r", "min": 1200, "center": 1500, "max": 1800},
    10: {"name": "eye_up_l", "min": 1200, "center": 1500, "max": 1800},
    11: {"name": "blink_l",  "min": 1000, "center": 1800, "max": 2000},
    12: {"name": "eye_lr_l", "min": 1200, "center": 1500, "max": 1800},
    13: {"name": "brow",     "min": 1200, "center": 1500, "max": 1900},
    14: {"name": "tongue",   "min": 1200, "center": 1500, "max": 1900},
}


def pwm_to_norm(servo_id, pwm):
    """将 PWM 值转换为 0~1 归一化值"""
    r = SERVO_RANGE.get(servo_id)
    if not r:
        return 0.5
    lo, hi = r["min"], r["max"]
    if hi == lo:
        return 0.5
    return max(0.0, min(1.0, (pwm - lo) / (hi - lo)))


# ==========================
# 面部绘制参数 (基准位置)
# ==========================
CANVAS_W = 500
CANVAS_H = 460   # 画布高度（不含控制区）

# 脸的中心
FACE_CX = 250
FACE_CY = 210
FACE_RX = 140   # 椭圆半宽
FACE_RY = 170   # 椭圆半高

# 左眼基准
EYE_L_CX = 195
EYE_L_CY = 155
EYE_W = 44
EYE_H = 30

# 右眼基准
EYE_R_CX = 305
EYE_R_CY = 155

# 嘴巴基准
MOUTH_CX = 250
MOUTH_CY = 285
MOUTH_HALF_W = 55    # 半宽
MOUTH_HALF_H = 18    # 半高（闭合时）

# 眉毛基准
BROW_L_Y = 118
BROW_R_Y = 118

# 鼻子
NOSE_CX = 250
NOSE_CY = 215
NOSE_W = 13
NOSE_H = 11

# 耳朵
EAR_L_X = 108
EAR_L_Y = 185
EAR_R_X = 392
EAR_R_Y = 185


class FaceDisplay:
    """面部模拟显示 (GUI 驱动)"""

    def __init__(self):
        self.queue = queue.Queue()       # 舵机 PWM 更新队列
        self.text_queue = queue.Queue()  # 文本输入队列: "text" / "__stop__" / "__exit__"
        self.servo_cmd_queue = queue.Queue()  # 滑杆发出的舵机指令 (sid, pwm)
        self.ui_cmd_queue = queue.Queue()     # 跨线程 UI 命令队列（只在主线程消费）

        self.root = None
        self.canvas = None
        self.entry = None
        self.speak_btn = None
        self.stop_btn = None
        self.status_label = None

        # 滑杆变量 (servo_id -> tk.IntVar)
        self._sliders = {}

        # 当前各关节归一化值
        self.state = {
            "jaw": 0.5,       # PWM反转: 0=张大 1=闭合
            "smile_r": 0.5,
            "smile_l": 0.5,
            "lip_up": 0.5,
            "eye_up_r": 0.5,
            "eye_up_l": 0.5,
            "eye_lr_r": 0.5,
            "eye_lr_l": 0.5,
            "blink_r": 0.9,   # 0=全闭 1=全开
            "blink_l": 0.9,
            "brow": 0.5,
            "tongue": 0.0,
            "neck": 0.5,
        }

        self._ids = {}
        self._running = False
        self._speaking = False   # 是否正在说话中

    # ---- 启动/停止 ----
    def start(self):
        """启动 tkinter 窗口（应在主线程或专用线程调用，tkinter 会阻塞在此线程）"""
        self._running = True
        self._tk_main()
        # _tk_main 在窗口关闭前不会返回

    def start_in_thread(self):
        """在后台线程中启动（窗口关闭前线程不退出）"""
        self._running = True
        t = threading.Thread(target=self._tk_main, daemon=True)
        t.start()
        return t

    def stop(self):
        self._running = False
        self.ui_cmd_queue.put(("stop", None))

    # ---- 外部调用：更新舵机 PWM ----
    def update_servo(self, servo_id, pwm):
        self.queue.put((servo_id, pwm))

    def update_pose(self, pose):
        for sid, pwm in pose.items():
            self.queue.put((sid, pwm))

    # ---- 外部调用：获取文本输入 ----
    def get_text_input(self, timeout=None):
        """
        获取用户从 GUI 输入的文本（阻塞式）。
        返回:
          - 普通字符串: 要说话的文本
          - "__stop__" : 停止当前播放
          - "__exit__" : 退出程序
          - None: 超时
        """
        try:
            return self.text_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    # ---- 设置说话状态（线程安全，通过队列）----
    def set_speaking(self, speaking):
        self._speaking = speaking
        self.ui_cmd_queue.put(("speaking", speaking))

    def set_status(self, text):
        self.ui_cmd_queue.put(("status", text))

    # ---- 外部调用：获取滑杆发出的舵机指令 ----
    def get_servo_cmd(self):
        """获取滑杆产生的 (servo_id, pwm)，无则返回 None"""
        try:
            return self.servo_cmd_queue.get_nowait()
        except queue.Empty:
            return None

    # ---- 外部调用：复位所有滑杆到中心值（线程安全，通过队列）----
    def reset_sliders(self):
        """复位所有滑杆到中心值"""
        self.ui_cmd_queue.put(("reset_sliders", None))

    # ==============================
    # 内部 tkinter 实现
    # ==============================

    def _tk_main(self):
        self.root = tk.Tk()
        self.root.title("Robot Face - 模拟显示")
        self.root.resizable(False, False)
        self.root.configure(bg="#2c2c2c")
        self.root.protocol("WM_DELETE_WINDOW", self._on_exit)  # X按钮走正常退出流程

        # --- 面部画布 ---
        self.canvas = tk.Canvas(
            self.root, width=CANVAS_W, height=CANVAS_H,
            bg="#2c2c2c", highlightthickness=0
        )
        self.canvas.pack(padx=0, pady=(0, 0))

        self._draw_face()

        # --- 嘴巴滑杆区 ---
        slider_frame = tk.Frame(self.root, bg="#3a3a3a")
        slider_frame.pack(fill=tk.X, padx=12, pady=(2, 2))

        slider_title = tk.Label(
            slider_frame, text="嘴巴关节控制 (拖动调节 PWM)",
            font=("Microsoft YaHei", 9, "bold"),
            bg="#3a3a3a", fg="#cccccc"
        )
        slider_title.pack(pady=(4, 0))

        # 4 个嘴巴舵机的滑杆
        mouth_servos = [
            (3,  "下巴",     1000, 1450, 1450),  # 默认闭合
            (4,  "右微笑",   1200, 1900, 1500),
            (5,  "左微笑",   1200, 1900, 1500),
            (6,  "上唇",     1200, 1900, 1500),
        ]

        slider_grid = tk.Frame(slider_frame, bg="#3a3a3a")
        slider_grid.pack(fill=tk.X, padx=4, pady=(2, 4))

        for row, (sid, label, mn, mx, center) in enumerate(mouth_servos):
            # 标签
            lbl = tk.Label(
                slider_grid, text=f"ID{sid} {label}",
                font=("Microsoft YaHei", 9),
                bg="#3a3a3a", fg="#dddddd", width=10, anchor="e"
            )
            lbl.grid(row=row, column=0, padx=(0, 4), sticky="e")

            # 滑杆
            var = tk.IntVar(value=center)
            self._sliders[sid] = var

            scale = tk.Scale(
                slider_grid, from_=mn, to=mx,
                variable=var, orient=tk.HORIZONTAL,
                length=240, showvalue=True,
                bg="#3a3a3a", fg="#ffffff",
                troughcolor="#555555",
                highlightthickness=0,
                sliderrelief=tk.FLAT,
                font=("Microsoft YaHei", 8),
                command=lambda val, s=sid: self._on_slider_change(s, int(float(val)))
            )
            scale.grid(row=row, column=1, padx=(0, 4), sticky="ew")

            # 数值显示
            val_lbl = tk.Label(
                slider_grid, text=str(center),
                font=("Consolas", 9),
                bg="#3a3a3a", fg="#4a90d9", width=6, anchor="w",
                name=f"val_{sid}"
            )
            val_lbl.grid(row=row, column=2, sticky="w")

        # 复位按钮
        reset_btn = tk.Button(
            slider_grid, text="复位", font=("Microsoft YaHei", 8),
            bg="#666666", fg="white", relief=tk.FLAT, bd=0, padx=8,
            command=self._on_sliders_reset
        )
        reset_btn.grid(row=len(mouth_servos), column=0, columnspan=3, pady=(4, 0))

        # --- 控制区 ---
        ctrl_frame = tk.Frame(self.root, bg="#2c2c2c")
        ctrl_frame.pack(fill=tk.X, padx=12, pady=(4, 4))

        # 输入行
        input_frame = tk.Frame(ctrl_frame, bg="#2c2c2c")
        input_frame.pack(fill=tk.X)

        self.entry = tk.Entry(
            input_frame, font=("Microsoft YaHei", 12),
            bg="#3c3c3c", fg="#ffffff", insertbackground="#ffffff",
            relief=tk.FLAT, bd=6
        )
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self.entry.bind("<Return>", self._on_enter)
        
        # 设置默认文本
        self.entry.insert(0, "你好呀，很高兴认识你")

        self.speak_btn = tk.Button(
            input_frame, text="说话", font=("Microsoft YaHei", 11),
            bg="#4a90d9", fg="white", activebackground="#3a7bc8",
            relief=tk.FLAT, bd=0, padx=16, pady=4,
            command=self._on_speak, cursor="hand2"
        )
        self.speak_btn.pack(side=tk.RIGHT)

        # 按钮行
        btn_frame = tk.Frame(ctrl_frame, bg="#2c2c2c")
        btn_frame.pack(fill=tk.X, pady=(6, 0))

        self.stop_btn = tk.Button(
            btn_frame, text="停止", font=("Microsoft YaHei", 11),
            bg="#d94a4a", fg="white", activebackground="#c83a3a",
            relief=tk.FLAT, bd=0, padx=16, pady=4,
            command=self._on_stop, cursor="hand2"
        )
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 8))

        exit_btn = tk.Button(
            btn_frame, text="退出", font=("Microsoft YaHei", 11),
            bg="#888888", fg="white", activebackground="#666666",
            relief=tk.FLAT, bd=0, padx=16, pady=4,
            command=self._on_exit, cursor="hand2"
        )
        exit_btn.pack(side=tk.LEFT)

        # 状态标签
        self.status_label = tk.Label(
            ctrl_frame, text="就绪 - 请输入文本", font=("Microsoft YaHei", 9),
            bg="#2c2c2c", fg="#aaaaaa"
        )
        self.status_label.pack(pady=(4, 0))

        # 焦点
        self.entry.focus_set()

        # 启动轮询
        self._poll_queue()
        self.root.mainloop()

    # ---- 事件处理 ----
    def _on_enter(self, event=None):
        self._on_speak()

    def _on_speak(self):
        text = self.entry.get().strip()
        if not text:
            return
        self.text_queue.put(text)
        self.entry.delete(0, tk.END)

    def _on_stop(self):
        self.text_queue.put("__stop__")

    def _on_exit(self):
        self.text_queue.put("__exit__")

    def _on_slider_change(self, servo_id, pwm):
        """滑杆拖动时：更新画面 + 通知舵机"""
        # 更新数值显示标签
        try:
            val_lbl = self.root.nametowidget(f".!frame.!frame.val_{servo_id}")
            val_lbl.config(text=str(pwm))
        except Exception:
            pass

        # 更新面部画面（通过 queue 走 _poll_queue 线程安全更新）
        self.queue.put((servo_id, pwm))

        # 通知 demo.py 发送舵机指令
        self.servo_cmd_queue.put((servo_id, pwm))

    def _on_sliders_reset(self):
        """复位所有滑杆到中心值"""
        centers = {3: 1450, 4: 1500, 5: 1500, 6: 1500}
        for sid, center in centers.items():
            if sid in self._sliders:
                self._sliders[sid].set(center)
            self._on_slider_change(sid, center)

    def _update_ui_state(self):
        """根据说话状态更新按钮状态"""
        if self._speaking:
            self.speak_btn.config(state=tk.DISABLED, bg="#666666")
            self.stop_btn.config(state=tk.NORMAL, bg="#d94a4a")
            self.status_label.config(text="正在说话...")
        else:
            self.speak_btn.config(state=tk.NORMAL, bg="#4a90d9")
            self.stop_btn.config(state=tk.DISABLED, bg="#666666")
            self.status_label.config(text="就绪 - 请输入文本")

    # ---- 轮询队列 ----
    def _poll_queue(self):
        try:
            while True:
                sid, pwm = self.queue.get_nowait()
                self._apply_servo(sid, pwm)
        except queue.Empty:
            pass

        # 处理跨线程 UI 命令（只在主线程执行，线程安全）
        try:
            while True:
                cmd, data = self.ui_cmd_queue.get_nowait()
                if cmd == "status":
                    self.status_label.config(text=data)
                elif cmd == "speaking":
                    self._speaking = data
                    self._update_ui_state()
                elif cmd == "reset_sliders":
                    self._on_sliders_reset()
                elif cmd == "stop":
                    self._running = False
                    self.root.destroy()
                    return  # 直接返回，不再调度下一次
        except queue.Empty:
            pass

        if self._running:
            self._redraw_all()
            self.root.after(16, self._poll_queue)

    # ---- 应用舵机值 ----
    def _apply_servo(self, servo_id, pwm):
        norm = pwm_to_norm(servo_id, pwm)
        name = SERVO_RANGE.get(servo_id, {}).get("name")
        if name and name in self.state:
            self.state[name] = norm

    # ---- 初始绘制 ----
    def _draw_face(self):
        c = self.canvas

        # 背景光晕
        c.create_oval(
            FACE_CX - FACE_RX - 20, FACE_CY - FACE_RY - 20,
            FACE_CX + FACE_RX + 20, FACE_CY + FACE_RY + 20,
            fill="#3a3a3a", outline="", tags="bg_glow"
        )

        # 耳朵
        self._ids["ear_l"] = c.create_oval(
            EAR_L_X - 16, EAR_L_Y - 20, EAR_L_X + 16, EAR_L_Y + 20,
            fill="#d4956b", outline="#b07850", width=2, tags="face"
        )
        self._ids["ear_r"] = c.create_oval(
            EAR_R_X - 16, EAR_R_Y - 20, EAR_R_X + 16, EAR_R_Y + 20,
            fill="#d4956b", outline="#b07850", width=2, tags="face"
        )

        # 脸
        self._ids["face"] = c.create_oval(
            FACE_CX - FACE_RX, FACE_CY - FACE_RY,
            FACE_CX + FACE_RX, FACE_CY + FACE_RY,
            fill="#e8b88a", outline="#c4946a", width=2, tags="face"
        )

        # 鼻子
        self._ids["nose"] = c.create_polygon(
            NOSE_CX, NOSE_CY - NOSE_H,
            NOSE_CX - NOSE_W, NOSE_CY + NOSE_H // 2,
            NOSE_CX + NOSE_W, NOSE_CY + NOSE_H // 2,
            fill="#d4a076", outline="#c09068", width=1, tags="face"
        )

        # 眉毛
        self._ids["brow_l"] = c.create_arc(
            EYE_L_CX - 28, BROW_L_Y - 7,
            EYE_L_CX + 28, BROW_L_Y + 7,
            start=200, extent=140,
            style=tk.ARC, outline="#5c3d2e", width=4, tags="face"
        )
        self._ids["brow_r"] = c.create_arc(
            EYE_R_CX - 28, BROW_R_Y - 7,
            EYE_R_CX + 28, BROW_R_Y + 7,
            start=200, extent=140,
            style=tk.ARC, outline="#5c3d2e", width=4, tags="face"
        )

        # 眼白
        self._ids["eye_white_l"] = c.create_oval(
            EYE_L_CX - EYE_W // 2, EYE_L_CY - EYE_H // 2,
            EYE_L_CX + EYE_W // 2, EYE_L_CY + EYE_H // 2,
            fill="white", outline="#888", width=1, tags="face"
        )
        self._ids["eye_white_r"] = c.create_oval(
            EYE_R_CX - EYE_W // 2, EYE_R_CY - EYE_H // 2,
            EYE_R_CX + EYE_W // 2, EYE_R_CY + EYE_H // 2,
            fill="white", outline="#888", width=1, tags="face"
        )

        # 瞳孔
        self._ids["pupil_l"] = c.create_oval(0, 0, 1, 1, fill="#222", tags="face")
        self._ids["pupil_r"] = c.create_oval(0, 0, 1, 1, fill="#222", tags="face")

        # 上眼皮
        self._ids["eyelid_l"] = c.create_rectangle(0, 0, 1, 1, fill="#e8b88a", outline="", tags="face")
        self._ids["eyelid_r"] = c.create_rectangle(0, 0, 1, 1, fill="#e8b88a", outline="", tags="face")

        # 嘴巴
        self._ids["mouth"] = c.create_polygon(
            0, 0, 1, 1, 2, 2, 3, 3,
            fill="#c94d4d", outline="#a03030", width=2, tags="face"
        )

        # 舌头
        self._ids["tongue"] = c.create_oval(0, 0, 1, 1, fill="#e87070", outline="#c04040", width=1, tags="face")

        self._redraw_all()

    # ---- 重绘 ----
    def _redraw_all(self):
        self._redraw_eyes()
        self._redraw_mouth()
        self._redraw_brows()
        self._redraw_tongue()

    def _redraw_eyes(self):
        c = self.canvas
        s = self.state

        for side, cx, up_key, lr_key, blink_key in [
            ("l", EYE_L_CX, "eye_up_l", "eye_lr_l", "blink_l"),
            ("r", EYE_R_CX, "eye_up_r", "eye_lr_r", "blink_r"),
        ]:
            up = (s[up_key] - 0.5) * 10
            lr = (s[lr_key] - 0.5) * 12
            pupil_cx = cx + lr
            pupil_cy = EYE_L_CY + up
            pupil_r = 8

            blink = s[blink_key]
            open_ratio = max(0.15, blink)

            c.coords(
                self._ids[f"pupil_{side}"],
                pupil_cx - pupil_r, pupil_cy - pupil_r,
                pupil_cx + pupil_r, pupil_cy + pupil_r
            )

            eyelid_top = EYE_L_CY - EYE_H // 2 * open_ratio
            c.coords(
                self._ids[f"eyelid_{side}"],
                cx - EYE_W // 2 - 2, eyelid_top - 5,
                cx + EYE_W // 2 + 2, EYE_L_CY - EYE_H // 2 - 2
            )

    def _redraw_mouth(self):
        c = self.canvas
        s = self.state

        jaw_open = 1.0 - s["jaw"]   # S3 反转: PWM越小嘴越开
        smile_r = s["smile_r"]
        smile_l = s["smile_l"]
        lip = s["lip_up"]

        # 左右半宽独立计算，各只受同侧微笑影响
        half_w_l = MOUTH_HALF_W + (smile_l - 0.5) * 28
        half_w_r = MOUTH_HALF_W + (smile_r - 0.5) * 28
        half_w_l = max(14, min(60, half_w_l))
        half_w_r = max(14, min(60, half_w_r))

        corner_lift_r = (smile_r - 0.5) * 22
        corner_lift_l = (smile_l - 0.5) * 22

        mouth_open_h = MOUTH_HALF_H + jaw_open * 28

        top_lip_offset = (lip - 0.5) * 14

        # 上唇角落 X 使用固定半宽，不受微笑影响
        top_left_x = MOUTH_CX - MOUTH_HALF_W
        top_right_x = MOUTH_CX + MOUTH_HALF_W

        # 下唇角落 X 随同侧微笑独立伸缩
        bot_left_x = MOUTH_CX - half_w_l
        bot_right_x = MOUTH_CX + half_w_r

        # 上唇只受 lip_up 控制，不随下巴(jaw)和微笑(smile)移动
        top_ly = MOUTH_CY - MOUTH_HALF_H * 0.2 + top_lip_offset
        top_cy = MOUTH_CY - MOUTH_HALF_H * 0.3 + top_lip_offset
        top_ry = MOUTH_CY - MOUTH_HALF_H * 0.2 + top_lip_offset

        bot_ly = MOUTH_CY + mouth_open_h + corner_lift_l
        bot_cy = MOUTH_CY + mouth_open_h * 1.2
        bot_ry = MOUTH_CY + mouth_open_h + corner_lift_r

        pts = [
            top_left_x, top_ly,
            MOUTH_CX, top_cy,
            top_right_x, top_ry,
            bot_right_x, bot_ry,
            MOUTH_CX, bot_cy,
            bot_left_x, bot_ly,
        ]

        c.coords(self._ids["mouth"], *pts)

    def _redraw_brows(self):
        s = self.state
        brow = s["brow"]
        offset = (brow - 0.5) * 12

        for side, cx, base_y in [
            ("l", EYE_L_CX, BROW_L_Y),
            ("r", EYE_R_CX, BROW_R_Y),
        ]:
            y = base_y + offset
            self.canvas.coords(
                self._ids[f"brow_{side}"],
                cx - 28, y - 7,
                cx + 28, y + 7
            )

    def _redraw_tongue(self):
        s = self.state
        tongue = s["tongue"]

        if tongue < 0.05:
            self.canvas.coords(self._ids["tongue"], 0, 0, 1, 1)
        else:
            tw = 15 + tongue * 7
            th = 9 + tongue * 18
            ty = MOUTH_CY + 16 + tongue * 9
            tx = MOUTH_CX
            self.canvas.coords(
                self._ids["tongue"],
                tx - tw, ty, tx + tw, ty + th
            )


# ==========================
# 独立测试入口
# ==========================
if __name__ == "__main__":
    display = FaceDisplay()
    display.start_in_thread()

    print("面部模拟显示已启动。")
    print("在 demo.py 中集成使用。")

    while True:
        cmd = display.get_text_input(timeout=1)
        if cmd == "__exit__":
            display.stop()
            break
        elif cmd == "__stop__":
            print("[停止]")
        elif cmd is not None:
            print(f"[输入文本] {cmd}")

    print("已退出。")
