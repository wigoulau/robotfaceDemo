import time
import threading
import queue
import wave

import numpy as np
import sounddevice as sd

from pypinyin import lazy_pinyin

from piper.voice import PiperVoice
voice = PiperVoice.load(
    "models/tts/zh_CN-huayan-medium.onnx"
)

# ==========================
# 导入舵机控制器 + 面部显示
# ==========================

from servo_controller import ServoController
from face_display import FaceDisplay


# ==========================
# Viseme
# ==========================

def pinyin_to_viseme(py):
    if py.endswith(("a", "ai", "an", "ang", "ia", "ua")):
        return "A"
    elif py.endswith(("o", "ao", "ou", "uo")):
        return "O"
    elif py.endswith(("i", "ie", "in", "ing")):
        return "E"
    elif py.endswith(("u", "ui", "un")):
        return "U"
    return "A"


def text_to_visemes(text):
    pys = lazy_pinyin(text)
    return [pinyin_to_viseme(x) for x in pys]


# ==========================
# 嘴型定义
# ==========================

POSE = {
    "A": {3: 1200, 4: 1500, 5: 1500, 6: 1650},   # A: 中张嘴
    "O": {3: 1300, 4: 1350, 5: 1350, 6: 1550},   # O: 小张嘴
    "E": {3: 1380, 4: 1850, 5: 1850, 6: 1600},   # E: 微张嘴
    "U": {3: 1420, 4: 1250, 5: 1250, 6: 1500},   # U: 接近闭合
}


# ==========================
# Mixer
# ==========================

def mix_pose(viseme, rms):
    pose = POSE[viseme]
    center = 1100
    jaw_target = pose[3]
    jaw = int(center + (jaw_target - center) * (0.3 + rms * 0.7))
    result = dict(pose)
    result[3] = jaw
    return result


# ==========================
# RMS
# ==========================

def calc_rms(chunk):
    audio = chunk.astype(np.float32)
    rms = np.sqrt(np.mean(audio * audio))
    rms /= 32768.0
    rms = max(0.0, min(1.0, rms * 8))
    return rms


# ==========================
# Servo Engine
# ==========================

class ServoEngine:

    def __init__(self, ctrl, face_display=None):
        self.ctrl = ctrl
        self.face = face_display

    def apply_pose(self, pose):
        if self.ctrl:
            for sid, pwm in pose.items():
                self.ctrl.send_pwm(sid, int(pwm), 50)

        if self.face:
            self.face.update_pose(pose)


# ==========================
# Blink Thread
# ==========================

class BlinkThread(threading.Thread):

    def __init__(self, ctrl, face_display=None):
        super().__init__(daemon=True)
        self.ctrl = ctrl
        self.face = face_display

    def run(self):
        while True:
            time.sleep(np.random.uniform(2, 5))

            # 闭眼
            if self.ctrl:
                self.ctrl.send_pwm(8, 1000, 80)
                self.ctrl.send_pwm(11, 1000, 80)
            if self.face:
                self.face.update_servo(8, 1000)
                self.face.update_servo(11, 1000)

            time.sleep(0.08)

            # 睁眼
            if self.ctrl:
                self.ctrl.send_pwm(8, 1800, 80)
                self.ctrl.send_pwm(11, 1800, 80)
            if self.face:
                self.face.update_servo(8, 1800)
                self.face.update_servo(11, 1800)


# ==========================
# Neck Thread
# ==========================

class NeckThread(threading.Thread):

    def __init__(self, ctrl, face_display=None):
        super().__init__(daemon=True)
        self.ctrl = ctrl
        self.face = face_display

    def run(self):
        t = 0
        while True:
            pwm = int(1500 + np.sin(t) * 80)

            if self.ctrl:
                self.ctrl.send_pwm(2, pwm, 50)
            if self.face:
                self.face.update_servo(2, pwm)

            t += 0.05
            time.sleep(0.05)


# ==========================
# WAV播放+同步舵机 (支持停止)
# ==========================

def play_wav_with_servo(wav_file, visemes, servo_engine, stop_event):
    """播放WAV并同步舵机。stop_event 被 set() 时立即停止。"""

    wf = wave.open(wav_file, "rb")
    sr = wf.getframerate()
    total_frames = wf.getnframes()
    
    # 播放速度：0.8 = 80%速度（更慢）, 1.0 = 原速
    PLAYBACK_SPEED = 0.7
    
    # 计算实际播放时长和步长
    actual_duration = (total_frames / sr) / PLAYBACK_SPEED
    viseme_step = actual_duration / max(len(visemes), 1)
    
    chunk_size = 1024

    # 舵机更新节流：每 150ms 才发送一次舵机指令
    SERVO_INTERVAL = 0.15
    last_servo_time = 0

    # 输出采样率降低以实现慢速播放
    output_sr = int(sr * PLAYBACK_SPEED)
    
    stream = sd.OutputStream(samplerate=output_sr, channels=1, dtype=np.int16)
    stream.start()

    # 跟踪已写入帧数，用于精确同步
    frames_written = 0

    try:
        while not stop_event.is_set():
            data = wf.readframes(chunk_size)
            if len(data) == 0:
                break

            chunk = np.frombuffer(data, dtype=np.int16)
            stream.write(chunk)
            frames_written += len(chunk)

            # 节流：只在间隔足够时才更新舵机
            now = time.time()
            if now - last_servo_time >= SERVO_INTERVAL:
                rms = calc_rms(chunk)
                
                # 基于已写入帧数计算当前音频位置（更精确）
                current_audio_time = frames_written / output_sr
                idx = min(int(current_audio_time / viseme_step), len(visemes) - 1)

                viseme = visemes[idx]
                pose = mix_pose(viseme, rms)
                servo_engine.apply_pose(pose)
                last_servo_time = now
    finally:
        stream.stop()
        wf.close()


# ==========================
# Piper接口
# ==========================

def synthesize_to_wav(text, output_wav):
    with wave.open(output_wav, "wb") as wav_file:
        voice.synthesize_wav(text, wav_file)


# ==========================
# Main
# ==========================

def main():
    # ---- 启动 GUI（必须在主线程）----
    face_display = FaceDisplay()
    face_display._running = True

    # ---- 停止事件 ----
    stop_event = threading.Event()

    # 首次说话后启动后台线程
    bg_started = False

    # ---- 后台逻辑循环（在子线程中运行）----
    speak_thread = None  # 当前播放线程引用
    ctrl = None
    connected = False

    def _logic_loop():
        nonlocal bg_started, speak_thread, ctrl, connected

        # 等 tkinter 就绪后再连接舵机（避免 hidapi 与 tkinter 初始化冲突）
        while face_display.root is None:
            time.sleep(0.01)
        time.sleep(0.3)

        ctrl_obj = ServoController()
        connected = ctrl_obj.connect()
        ctrl = ctrl_obj if connected else None

        if not connected:
            print("[!] 舵机未连接，仅运行模拟显示模式")
            face_display.set_status("模拟模式 - 无硬件连接")

        servo_engine = ServoEngine(
            ctrl if connected else None,
            face_display
        )

        while True:
            cmd = face_display.get_text_input(timeout=0.05)

            # 处理滑杆发出的舵机指令
            while True:
                servo_cmd = face_display.get_servo_cmd()
                if servo_cmd is None:
                    break
                sid, pwm = servo_cmd
                if ctrl and connected:
                    ctrl.send_pwm(sid, pwm, 50)

            # 检查播放是否已结束
            if speak_thread is not None and not speak_thread.is_alive():
                speak_thread = None
                stop_event.clear()
                face_display.set_speaking(False)

                # 说话结束，复位嘴巴到闭合状态 (S3: 1450=闭合)
                mouth_closed = {3: 1450, 4: 1500, 5: 1500, 6: 1500}
                servo_engine.apply_pose(mouth_closed)

                # 同步复位 GUI 滑杆
                face_display.reset_sliders()

            if cmd is None:
                continue

            # -- 退出 --
            if cmd == "__exit__":
                stop_event.set()
                if speak_thread and speak_thread.is_alive():
                    speak_thread.join(timeout=3)
                # 通过队列通知主线程销毁 tkinter（线程安全）
                face_display.stop()
                break

            # -- 停止 --
            if cmd == "__stop__":
                stop_event.set()
                if speak_thread and speak_thread.is_alive():
                    speak_thread.join(timeout=2)
                speak_thread = None
                stop_event.clear()
                face_display.set_speaking(False)

                # 停止时也复位嘴巴到闭合状态
                mouth_closed = {3: 1450, 4: 1500, 5: 1500, 6: 1500}
                servo_engine.apply_pose(mouth_closed)
                face_display.reset_sliders()
                continue

            # -- 说话（如果正在播放，忽略新输入）--
            text = cmd
            if speak_thread and speak_thread.is_alive():
                face_display.set_status("正在播放中，请先停止")
                continue

            if not text:
                continue

            # 首次说话时启动后台线程
            if connected and not bg_started:
                # BlinkThread(ctrl, face_display).start()   # 眨眼 - 已暂停
                # NeckThread(ctrl, face_display).start()
                bg_started = True

            stop_event.clear()
            face_display.set_speaking(True)

            # TTS 合成 + 播放 (在工作线程，以便停止按钮生效)
            def _speak(t):
                try:
                    visemes = text_to_visemes(t)
                    print(f"Viseme: {visemes}")
                    wav_file = "tts.wav"
                    synthesize_to_wav(t, wav_file)
                    play_wav_with_servo(wav_file, visemes, servo_engine, stop_event)
                except Exception as e:
                    print(f"[播放错误] {e}")
                    face_display.set_status(f"播放出错: {e}")

            speak_thread = threading.Thread(target=_speak, args=(text,), daemon=True)
            speak_thread.start()

    # 在后台线程运行逻辑循环（含舵机连接），主线程留给 tkinter
    logic_thread = threading.Thread(target=_logic_loop, daemon=True)
    logic_thread.start()

    # ---- 主线程运行 tkinter（阻塞直到窗口关闭）----
    face_display._tk_main()

    # ---- 清理 ----
    if ctrl and connected:
        ctrl.close()

    print("程序已退出。")


if __name__ == "__main__":
    main()
