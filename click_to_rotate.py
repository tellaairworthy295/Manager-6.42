import pyautogui
import random
import time
from datetime import datetime


def click_random_top_right_area():
    # 1. 获取当前日期和时间
    now = datetime.now()
    current_date = now.date()
    current_time = now.time()

    # 判断是否为周末 (Saturday is 5, Sunday is 6)
    is_weekend = now.weekday() >= 5

    # 定义白天开始和结束的时间 (08:30 - 18:30)
    day_start = datetime.strptime("08:30", "%H:%M").time()
    day_end = datetime.strptime("18:30", "%H:%M").time()

    # 新增逻辑：如果是周末，则总是执行，无需判断时间
    if is_weekend:
        print(f"[execute] weekends ({current_date.strftime('%A')})，clicking...")
    else:
        # 如果是工作日，则按原有逻辑判断时间
        if day_start <= current_time < day_end:
            print(f"[skip] daytime ({current_time.strftime('%H:%M')})，skipping...。")
            return
        print(f"[execute] nighttime ({current_time.strftime('%H:%M')})，clicking...")

    # 2. 获取屏幕尺寸
    screen_width, screen_height = pyautogui.size()

    # 3. 计算区域边界
    min_x = int(screen_width * 0.11)
    max_x = int(screen_width * 0.97)
    min_y = int(screen_height * 0.21)
    max_y = int(screen_height * 0.73)

    # 4. 生成随机坐标
    random_x = random.randint(min_x, max_x)
    random_y = random.randint(min_y, max_y)

    print(f"Screen size: {screen_width}x{screen_height}")
    print(f"Calculated Area: X[{min_x}-{max_x}], Y[{min_y}-{max_y}]")
    print(f"Clicking at coordinates: X={random_x}, Y={random_y}")

    # 5. 执行点击 - 使用正确的随机坐标，并加上异常处理
    try:
        # BUG FIX: Use random_x, random_y instead of max_x, max_y
        pyautogui.click(x=random_x, y=random_y, duration=0.7)
        print("Click executed successfully.")
    except pyautogui.FailSafeException as e:
        print(f"PyAutoGUI Fail-Safe Triggered! Error: {e}")
        print("Mouse likely moved to a corner of the screen. Action cancelled.")
    except Exception as e:
        print(f"An unexpected error occurred during the click: {e}")


if __name__ == "__main__":
    # 主程序入口
    print(f"init: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 如果需要等待用户切换窗口，可以在调用前 sleep
    time.sleep(2)

    # 直接调用函数，函数内部会自动判断时间和日期
    click_random_top_right_area()