import time
import sys
from tracemalloc import take_snapshot
import hid

from logger import get_logger
log = get_logger(__name__)

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
                    log.info("    [Windows] 锁定 col02 供应商端点")
                    return d["path"]

        # 策略2: 全平台 - 按 usage_page=0xFFxx (厂商自定义范围) 匹配
        for d in devices:
            up = d.get("usage_page", 0)
            if (up & 0xFF00) == 0xFF00 and up != 0xFFFF:
                log.info("    锁定 usage_page=0x%04X 厂商自定义接口", up)
                return d["path"]

        # 策略3: 按 interface_number=1 匹配 (STM32 供应商接口通常是接口1)
        for d in devices:
            if d.get("interface_number") == 1:
                log.info("    锁定 interface_number=1")
                return d["path"]

        # 兜底: 最后一个接口
        log.info("    未匹配到特定接口，使用最后一个")
        return devices[-1]["path"]

    def is_connected(self):
        """设备是否已连接"""
        return self.device is not None
        
    def connect(self):
        """扫描设备并连接供应商端点"""
        log.info("正在扫描 HID 设备 (平台: %s)...", sys.platform)
        devices = hid.enumerate(self.vid, self.pid)

        if not devices:
            log.error("未发现 VID=0x%04X PID=0x%04X 的设备", self.vid, self.pid)
            return False

        log.info("    发现 %d 个逻辑接口:", len(devices))
        for d in devices:
            path = d["path"].decode("utf-8", errors="ignore")
            log.info("      path=%s", path[:60])
            log.info("      usage_page=0x%04X usage=0x%04X interface=%s",
                     d.get('usage_page', 0), d.get('usage', 0), d.get('interface_number', -1))

        self.device_path = self._select_interface(devices)

        try:
            self.device = hid.device()
            self.device.open_path(self.device_path)
            log.info("设备连接成功")
            return True
        except Exception as e:
            log.error("连接失败: %s", e)
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
            log.error("设备未连接")
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
                if servo_id in [11]:
                    log.debug("S%d PWM=%d (%s)", servo_id, pwm, cmd_str)
                return True
            else:
                log.warning("S%d %s  result=%s", servo_id, cmd_str, result)
                return False
        except Exception as e:
            log.error("S%d %s  %s", servo_id, cmd_str, e)
            return False

    def send_pwm_batch(self, commands):
        """
        批量发送一组舵机 PWM 指令（合并到单个 HID 事务）。
        指令用 {} 包裹: {#009P1600T0100!#012P1800T0100!}
        :param commands: list of (servo_id, pwm, time_ms)
            如 [(3, 1300, 500), (9, 1500, 1000)]
        :return: True/False
        """
        if not self.device:
            log.error("设备未连接")
            return False

        # 1. 生成指令: {#XXXP{pwm}T{time}!#XXXP{pwm}T{time}!}
        cmd_parts = []
        for sid, pwm, time_ms in commands:
            cmd_parts.append(f"#{sid:03d}P{pwm}T{time_ms:04d}!")
        cmd_str = "{" + "".join(cmd_parts) + "}"
        cmd_bytes = cmd_str.encode('ascii')

        # 2. 构建完整数据包:
        #    02 02 [len*1] [00 00 00 00 01] [{...payload...}]
        #    len = 5(fixed cmd) + len(cmd_bytes)
        fixed_cmd = [0x00, 0x00, 0x00, 0x00, 0x01]
        payload_len = len(fixed_cmd) + len(cmd_bytes)
        packet = [0x02, 0x02, payload_len]
        packet.extend(fixed_cmd)
        packet.extend(cmd_bytes)

        # 3. 按 64 字节分片发送，超过则续写
        try:
            offset = 0
            while offset < len(packet):
                chunk = packet[offset:offset + 64]
                while len(chunk) < 64:
                    chunk.append(0x00)
                self.device.write(chunk)
                offset += 64
                if offset < len(packet):
                    time.sleep(0.005)

            log.debug("BATCH %s", cmd_str)
            return True
        except Exception as e:
            log.error("BATCH %s  %s", cmd_str, e)
            return False

    def close(self):
        """关闭设备连接"""
        if self.device:
            self.device.close()
            self.device = None
            log.info("设备已关闭")


# ================= 主程序入口 =================
if __name__ == "__main__":
    ctrl = ServoController()

    def test_servo(t: float):
        print(f"测试舵机 3 {t} 秒")
        ctrl.send_pwm(9, 1300)
        ctrl.send_pwm(3, 1300)
        time.sleep(t)
        # ctrl.send_pwm(3, 1200)
        # time.sleep(1)
        # ctrl.send_pwm(3, 1100)
        # time.sleep(1)
        # ctrl.send_pwm(3, 1100)
        ctrl.send_pwm(3, 1000)
        ctrl.send_pwm(9, 1600)
        time.sleep(t)
        ctrl.send_pwm(9, 1300)
        ctrl.send_pwm(3, 1300)
    
    def test_servo_batch(t: float):
        print(f"测试舵机 3 {t} 秒")
        ctrl.send_pwm_batch([(9, 1300, 1000), (12, 1300, 1000), (3, 1000, 1000)])
        # ctrl.send_pwm_batch([(3, 1000, 1000)])
        time.sleep(t)
        ctrl.send_pwm_batch([(9, 1600, 100), (12, 1800, 100), (3, 1300, 600)])

    if ctrl.connect():
        try:
            while True:
                print("\n--- 舵机控制 ---")
                print("输入格式: 通道号 PWM值  (如: 4 1500)")
                print("输入 q 退出")
                raw = input(">> ").strip()

                if raw.lower() == 'q':
                    break
                elif raw.lower() == 't':
                    test_servo(1)
                    continue

                parts = raw.split()
                if len(parts) != 2:
                    print("  [!] 格式错误，请输入: 通道号 PWM值")
                    continue

                try:
                    if parts[0] == 't':
                        test_servo(float(parts[1]))
                        continue
                    elif parts[0] == 'b':
                        test_servo_batch(float(parts[1]))
                        continue
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
