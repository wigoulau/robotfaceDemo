import time
import threading
import queue
import wave
import sys
import random

# numpy, sounddevice, pypinyin 延迟到实际需要时再导入

from logger import get_logger
log = get_logger(__name__)

# 延迟加载模型（在后台线程中）
voice = None
voice_ready = threading.Event()

def _load_voice_async():
    global voice
    try:
        from piper.voice import PiperVoice
        voice = PiperVoice.load("models/tts/zh_CN-huayan-medium.onnx")
        voice_ready.set()
        log.info("TTS 模型加载完成")
    except Exception as e:
        log.error("模型加载失败: %s", e)

# ==========================
# 仅导入轻量模块
# ==========================

from face_display import FaceDisplay
from servo_interpolator import ServoInterpolator
from servo_interface import ServoInterface
from joint_controller import JointController
from servo_constants import S3_JAW, S4_SMILE_R, S5_SMILE_L, S6_LIP_UP

# 命令行参数: python demo.py [lerp|spring]
INTERP_MODE = sys.argv[1] if len(sys.argv) > 1 else "lerp"
log.info("插值器模式: %s", INTERP_MODE)


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
# Blink Thread (通过 JointController 驱动)
# ==========================

class BlinkThread(threading.Thread):
    """眨眼 + 眼球转动线程，通过 JointController 驱动"""

    # 眼球活动偏移量（相对中心 1500，单位 PWM）
    EYE_LR_RANGE = 150   # 左右偏移上限（±150）
    EYE_UD_RANGE = 80    # 上下偏移上限

    def __init__(self, joint_ctrl: JointController):
        super().__init__(daemon=True)
        self._jc = joint_ctrl
        # 初始化：眼睑睁开、眼球居中
        self._jc.eyes_open(preset="blink")

    def _blink(self):
        """左右眼同步眨眼：快速闭合，缓慢睁开"""
        log.debug("Blink")
        self._jc.eyes_close(speed=25)
        time.sleep(0.05 + random.uniform(0, 0.04))  # 轻微随机保持
        log.debug("Open")
        self._jc.eyes_open(preset="blink")

    def _saccade(self):
        """眼球随机凝视转动（左右眼完全同步）"""
        lr = random.randint(-self.EYE_LR_RANGE, self.EYE_LR_RANGE)
        ud = random.randint(-self.EYE_UD_RANGE, self.EYE_UD_RANGE)
        self._jc.eye_saccade(lr, ud)
        time.sleep(random.uniform(0.5, 2.0))

    def run(self):
        # 用绝对时间戳调度，避免被阻塞操作累积延迟
        next_blink   = time.time() + random.uniform(2, 4)
        next_saccade = time.time() + random.uniform(2, 5)

        while True:
            now = time.time()

            if now >= next_blink:
                self._blink()
                next_blink = time.time() + random.uniform(2, 5)

            if time.time() >= next_saccade:
                self._saccade()
                next_saccade = time.time() + random.uniform(3, 7)

            time.sleep(0.05)  # 50ms 主循环轮询


# ==========================
# Neck Thread (通过 JointController 驱动)
# ==========================

class NeckThread(threading.Thread):
    """脖子摆动线程"""

    def __init__(self, joint_ctrl: JointController):
        super().__init__(daemon=True)
        self._jc = joint_ctrl

    def run(self):
        import numpy as np
        t = 0
        while True:
            angle = np.sin(t) * 0.16  # -0.16 ~ +0.16
            self._jc.neck_set(angle)
            t += 0.05
            time.sleep(0.05)


# ==========================
# WAV播放+同步舵机 (支持停止)
# ==========================

def play_wav_with_servo(wav_file, visemes, joint_ctrl: JointController, stop_event):
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
                joint_ctrl.mouth_viseme(viseme, rms)
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
        log.error("语音模型加载超时")
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
    
    # ---- 创建插值器 ----
    interpolator = ServoInterpolator(mode=INTERP_MODE)

    # ---- 创建舵机接口（延迟连接）----
    servo_iface = ServoInterface()  # 不传入 controller，稍后连接

    # ---- 创建关节控制器（面部插值器共享）----
    joint_ctrl = JointController(
        servo_iface=servo_iface,
        face_display=face_display,
        interpolator=interpolator
    )

    # ---- 后台逻辑循环 ----
    stop_event = threading.Event()
    bg_started = False
    speak_thread = None
    connected = False

    def _logic_loop():
        nonlocal bg_started, speak_thread, connected

        # 等 tkinter 就绪后赋值插值器（不连接舵机，避免阻塞 GUI）
        while face_display.root is None:
            time.sleep(0.01)
        time.sleep(0.3)
        face_display.interpolator = interpolator
        # 立即启动硬件 tick 线程（内部有连接检查，无硬件也安全）
        joint_ctrl.start()


        def _ensure_servo():
            """首次说话时才连接舵机（避免 hid 扫描卡住 GUI）"""
            nonlocal connected
            if connected:
                return
            try:
                ok = servo_iface.connect()
                connected = ok
                if not connected:
                    log.warning("舵机未连接，仅运行模拟显示模式")
                    face_display.set_status("模拟模式 - 无硬件连接")
                else:
                    log.info("舵机连接成功")
            except Exception as e:
                log.error("舵机连接失败: %s", e)

        while True:
            cmd = face_display.get_text_input(timeout=0.05)

            # 处理滑杆发出的舵机指令（走 JointController 插值器）
            while True:
                servo_cmd = face_display.get_servo_cmd()
                if servo_cmd is None:
                    break
                sid, pwm = servo_cmd
                joint_ctrl.set_servo_target(sid, pwm, preset="slider")

            # 检查播放是否已结束
            if speak_thread is not None and not speak_thread.is_alive():
                speak_thread = None
                stop_event.clear()
                face_display.set_speaking(False)

                # 说话结束，复位嘴巴到闭合状态
                log.info("说话结束，复位嘴巴")
                # joint_ctrl.mouth_reset()

                # 同步复位 GUI 滑杆
                # face_display.reset_sliders()

            if cmd is None:
                continue

            # -- 退出 --
            if cmd == "__exit__":
                log.info("收到退出指令")
                stop_event.set()
                if speak_thread and speak_thread.is_alive():
                    speak_thread.join(timeout=3)
                # 停止硬件 tick
                joint_ctrl.stop()
                # 通过队列通知主线程销毁 tkinter（线程安全）
                face_display.stop()
                break

            # -- 停止 --
            if cmd == "__stop__":
                log.info("收到停止指令")
                stop_event.set()
                if speak_thread and speak_thread.is_alive():
                    speak_thread.join(timeout=2)
                speak_thread = None
                stop_event.clear()
                face_display.set_speaking(False)

                # 停止时也复位嘴巴到闭合状态
                joint_ctrl.mouth_reset()
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
                # BlinkThread(joint_ctrl).start()
                # NeckThread(joint_ctrl).start()
                bg_started = True

            stop_event.clear()
            face_display.set_speaking(True)

            # TTS 合成 + 播放 (在工作线程，以便停止按钮生效)
            def _speak(t):
                try:
                    visemes = text_to_visemes(t)
                    log.info("Viseme: %s", visemes)
                    wav_file = "tts.wav"
                    synthesize_to_wav(t, wav_file)
                    play_wav_with_servo(wav_file, visemes, joint_ctrl, stop_event)
                except Exception as e:
                    log.error("播放错误: %s", e)
                    face_display.set_status(f"播放出错: {e}")

            speak_thread = threading.Thread(target=_speak, args=(text,), daemon=True)
            speak_thread.start()

    # 在后台线程运行逻辑循环（含舵机连接），主线程留给 tkinter
    logic_thread = threading.Thread(target=_logic_loop, daemon=True)
    logic_thread.start()

    # ---- 主线程运行 tkinter（阻塞直到窗口关闭）----
    face_display.start()

    # ---- 清理 ----
    joint_ctrl.stop()
    servo_iface.close()

    log.info("程序已退出。")


if __name__ == "__main__":
    main()
