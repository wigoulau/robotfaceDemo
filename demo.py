import time
import threading
import queue
import wave
import sys

# numpy, sounddevice, pypinyin 延迟到实际需要时再导入

# 延迟加载模型（在后台线程中）
voice = None
voice_ready = threading.Event()

def _load_voice_async():
    global voice
    try:
        from piper.voice import PiperVoice
        voice = PiperVoice.load("models/tts/zh_CN-huayan-medium.onnx")
        voice_ready.set()
    except Exception as e:
        print(f"[ERR] 模型加载失败: {e}")

# ==========================
# 仅导入轻量模块
# ==========================

from face_display import FaceDisplay
from servo_interpolator import ServoInterpolator

# 命令行参数: python demo.py [lerp|spring]
INTERP_MODE = sys.argv[1] if len(sys.argv) > 1 else "lerp"
print(f"[插值器模式] {INTERP_MODE}")


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
    from pypinyin import lazy_pinyin
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
    import numpy as np
    audio = chunk.astype(np.float32)
    rms = np.sqrt(np.mean(audio * audio))
    rms /= 32768.0
    rms = max(0.0, min(1.0, rms * 8))
    return rms


# ==========================
# Servo Engine
# ==========================

class ServoEngine:

    def __init__(self, ctrl, face_display=None, interpolator=None):
        self.ctrl = ctrl
        self.face = face_display
        self.interpolator = interpolator  # 插值器（与 face_display 共享同一个实例）

    def apply_pose(self, pose, preset="mouth"):
        """应用姿势：硬件舵机直接发送，面部显示走插值器"""
        if self.ctrl:
            for sid, pwm in pose.items():
                # 硬件舵机自带速度控制 (send_pwm 第3个参数为速度)
                self.ctrl.send_pwm(sid, int(pwm), 50)

        if self.face:
            if self.interpolator:
                # 面部显示通过插值器平滑过渡
                self.face.set_pose_target(pose, preset=preset)
            else:
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
    import numpy as np
    import sounddevice as sd

    wf = wave.open(wav_file, "rb")
    sr = wf.getframerate()
    total_frames = wf.getnframes()
    
    # 播放速度：0.7 = 70%速度
    PLAYBACK_SPEED = 0.7
    output_sr = int(sr * PLAYBACK_SPEED)
    
    # 计算步长
    actual_duration = (total_frames / sr) / PLAYBACK_SPEED
    viseme_step = actual_duration / max(len(visemes), 1)
    
    chunk_size = 1024
    SERVO_INTERVAL = 0.08  # 80ms 节流，更跟手

    # 预读所有音频数据
    all_data = wf.readframes(total_frames)
    wf.close()
    audio_array = np.frombuffer(all_data, dtype=np.int16)
    
    # 使用回调模式播放，精确跟踪已播放帧数
    playback_pos = [0]  # 用列表以便在闭包中修改
    data_event = threading.Event()

    def audio_callback(outdata, frame_count, time_info, status):
        start = playback_pos[0]
        end = start + frame_count
        if end <= len(audio_array):
            outdata[:, 0] = audio_array[start:end]
        else:
            remaining = len(audio_array) - start
            if remaining > 0:
                outdata[:remaining, 0] = audio_array[start:start + remaining]
            outdata[remaining:, 0] = 0
        playback_pos[0] = min(end, len(audio_array))
        data_event.set()

    stream = sd.OutputStream(
        samplerate=output_sr, channels=1, dtype=np.int16,
        callback=audio_callback, blocksize=chunk_size
    )
    stream.start()

    last_servo_time = 0
    try:
        while not stop_event.is_set() and playback_pos[0] < len(audio_array):
            data_event.wait(timeout=0.05)
            data_event.clear()

            now = time.time()
            if now - last_servo_time >= SERVO_INTERVAL:
                # 基于实际已播放帧数计算位置
                played_frames = playback_pos[0]
                current_audio_time = played_frames / output_sr
                idx = min(int(current_audio_time / viseme_step), len(visemes) - 1)

                # 取当前位置附近的音频计算 RMS
                sample_start = max(0, played_frames - chunk_size)
                sample_end = min(played_frames, len(audio_array))
                if sample_end > sample_start:
                    rms = calc_rms(audio_array[sample_start:sample_end])
                else:
                    rms = 0.0

                viseme = visemes[idx]
                pose = mix_pose(viseme, rms)
                servo_engine.apply_pose(pose)
                last_servo_time = now
    finally:
        stream.stop()
        stream.close()


# ==========================
# Piper接口
# ==========================

def synthesize_to_wav(text, output_wav):
    # 等待模型加载完成
    if not voice_ready.wait(timeout=30):
        print("[ERR] 语音模型加载超时")
        return
    with wave.open(output_wav, "wb") as wav_file:
        voice.synthesize_wav(text, wav_file)


# ==========================
# Main
# ==========================

def main():
    # ---- 后台加载语音模型 ----
    threading.Thread(target=_load_voice_async, daemon=True).start()
    
    # ---- 创建 GUI（主线程运行 tkinter）----
    face_display = FaceDisplay()
    
    # 等 tkinter root 创建后（在 _tk_main 调用前）启动后台逻辑
    # 使用 after 回调来确保 tkinter 初始化完成
    
    # ---- 创建插值器 ----
    interpolator = ServoInterpolator(mode=INTERP_MODE)

    # ---- 后台逻辑循环 ----
    stop_event = threading.Event()
    bg_started = False
    speak_thread = None
    ctrl = None
    connected = False

    def _logic_loop():
        nonlocal bg_started, speak_thread, ctrl, connected

        # 等 tkinter 就绪后赋值插值器（不连接舵机，避免阻塞 GUI）
        while face_display.root is None:
            time.sleep(0.01)
        time.sleep(0.3)
        face_display.interpolator = interpolator

        servo_engine = ServoEngine(None, face_display, interpolator)

        def _ensure_servo():
            """首次说话时才连接舵机（避免 hid 扫描卡住 GUI）"""
            nonlocal ctrl, connected, servo_engine
            if ctrl is not None or connected:
                return
            try:
                from servo_controller import ServoController
                ctrl_obj = ServoController()
                connected = ctrl_obj.connect()
                ctrl = ctrl_obj if connected else None
                if not connected:
                    print("[!] 舵机未连接，仅运行模拟显示模式")
                    face_display.set_status("模拟模式 - 无硬件连接")
                servo_engine = ServoEngine(
                    ctrl if connected else None,
                    face_display,
                    interpolator
                )
            except Exception as e:
                print(f"[ERR] 舵机连接失败: {e}")

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
                servo_engine.apply_pose(mouth_closed, preset="reset")

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
                servo_engine.apply_pose(mouth_closed, preset="reset")
                face_display.reset_sliders()
                continue

            # -- 说话（如果正在播放，忽略新输入）--
            text = cmd
            if speak_thread and speak_thread.is_alive():
                face_display.set_status("正在播放中，请先停止")
                continue

            if not text:
                continue

            # 首次说话时连接舵机
            _ensure_servo()

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
    face_display.start()

    # ---- 清理 ----
    if ctrl and connected:
        ctrl.close()

    print("程序已退出。")


if __name__ == "__main__":
    main()
