import time
import sys
import logging
import hid

# 配置 logger（带时间戳）
logger = logging.getLogger("servo")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d  %(message)s",
        datefmt="%H:%M:%S"
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

VENDOR_ID = 0x0483
PRODUCT_ID = 0x5750


class ServoController:
    """HID 舵机控制器 - 跨平台 (Windows/Linux/macOS)"""

    def __init__(self, vid=VENDOR_ID, pid=PRODUCT_ID):
        self.vid = vid
        self.pid = pid
        self.device = None
        self.device_path = None

    def _select_interface(self, devices):
        """
        跨平台选择正确的 HID 接口
        Windows: 优先选 col02 (供应商定义设备)
        全平台: 优先选 usage_page=0xFF00 (厂商自定义)
        兜底: 选最后一个接口
        """
        platform = sys.platform

        # 策略1: Windows 下优先匹配 col02
        if platform == "win32":
            for d in devices:
                path = d["path"].decode("utf-8", errors="ignore").lower()
                if "col02" in path:
                    print(f"     [Windows] 锁定 col02 供应商端点")
                    return d["path"]

        # 策略2: 全平台 - 按 usage_page=0xFFxx (厂商自定义范围) 匹配
        for d in devices:
            up = d.get("usage_page", 0)
            if (up & 0xFF00) == 0xFF00 and up != 0xFFFF:
                print(f"     锁定 usage_page=0x{up:04X} 厂商自定义接口")
                return d["path"]

        # 策略3: 按 interface_number=1 匹配 (STM32 供应商接口通常是接口1)
        for d in devices:
            if d.get("interface_number") == 1:
                print(f"     锁定 interface_number=1")
                return d["path"]

        # 兜底: 最后一个接口
        print(f"     未匹配到特定接口，使用最后一个")
        return devices[-1]["path"]

    def connect(self):
        """扫描设备并连接供应商端点"""
        print(f"[..] 正在扫描 HID 设备 (平台: {sys.platform})...")
        devices = hid.enumerate(self.vid, self.pid)

        if not devices:
            print(f"[ERR] 未发现 VID=0x{self.vid:04X} PID=0x{self.pid:04X} 的设备")
            return False

        print(f"     发现 {len(devices)} 个逻辑接口:")
        for d in devices:
            path = d["path"].decode("utf-8", errors="ignore")
            print(f"       path={path[:60]}")
            print(f"       usage_page=0x{d.get('usage_page', 0):04X} "
                  f"usage=0x{d.get('usage', 0):04X} "
                  f"interface={d.get('interface_number', -1)}")

        self.device_path = self._select_interface(devices)

        try:
            self.device = hid.device()
            self.device.open_path(self.device_path)
            print("[OK] 设备连接成功")
            return True
        except Exception as e:
            print(f"[ERR] 连接失败: {e}")
            self.device = None
            return False

    def send_pwm(self, servo_id, pwm):
        """
        发送一条舵机 PWM 控制指令。
        移动速度由调用方通过步长+sleep控制，duration 固定 1000。
        :param servo_id: 舵机通道号 (如 4, 6)
        :param pwm: PWM 值 (如 1500)
        :return: True/False
        """
        if not self.device:
            logger.error("设备未连接")
            return False

        # 1. 初始化 64 字节全零缓冲区
        buf = [0x00] * 64

        # 2. 填充协议前缀: 02 02 14 00 00 00 00 01
        prefix = [0x02, 0x02, 0x14, 0x00, 0x00, 0x00, 0x00, 0x01]
        for i in range(len(prefix)):
            buf[i] = prefix[i]

        # 3. 生成 ASCII 指令: #xxxPxxxxT1000!
        cmd_str = f"#{servo_id:03d}P{pwm}T1000!"
        cmd_bytes = cmd_str.encode('ascii')

        # 4. 填入缓冲区 (从第 8 字节开始)
        for i, b in enumerate(cmd_bytes):
            buf[8 + i] = b
        buf[8 + len(cmd_bytes)] = 0x00  # null 结尾

        # 5. 发送 (中断传输)
        try:
            result = self.device.write(buf)
            if result and result > 0:
                if (servo_id in [11]):
                    logger.info(f"S{servo_id} PWM={pwm} ({cmd_str})")
                return True
            else:
                logger.warning(f"S{servo_id} {cmd_str}  result={result}")
                return False
        except Exception as e:
            logger.error(f"S{servo_id} {cmd_str}  {e}")
            return False

    def close(self):
        """关闭设备连接"""
        if self.device:
            self.device.close()
            self.device = None
            print("[OK] 设备已关闭")


# ================= 主程序入口 =================
if __name__ == "__main__":
    ctrl = ServoController()

    if ctrl.connect():
        try:
            while True:
                print("\n--- 舵机控制 ---")
                print("输入格式: 通道号 PWM值  (如: 4 1500)")
                print("输入 q 退出")
                raw = input(">> ").strip()

                if raw.lower() == 'q':
                    break

                parts = raw.split()
                if len(parts) != 2:
                    print("  [!] 格式错误，请输入: 通道号 PWM值")
                    continue

                try:
                    ch = int(parts[0])
                    pwm = int(parts[1])
                except ValueError:
                    print("  [!] 请输入数字")
                    continue

                ctrl.send_pwm(ch, pwm)

        except KeyboardInterrupt:
            pass
        finally:
            ctrl.close()
