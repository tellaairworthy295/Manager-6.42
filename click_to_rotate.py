import pyautogui
import random
import time
from datetime import datetime

def click_random_top_right_area():
    # 1. 获取当前时间并判断是否为白天
    now = datetime.now()
    current_time = now.time()
    
    # 定义白天开始和结束的时间 (08:30 - 18:30)
    day_start = datetime.strptime("08:30", "%H:%M").time()
    day_end = datetime.strptime("18:30", "%H:%M").time()
    
    # 如果当前时间在 [08:30, 18:30) 区间内，则是白天，直接退出函数
    if day_start <= current_time < day_end:
        print(f"[跳过] 当前时间是白天 ({current_time.strftime('%H:%M')})，不执行点击。")
        return

    print(f"[执行] 当前时间是晚上 ({current_time.strftime('%H:%M')})，准备执行点击...")

    # 2. 获取屏幕尺寸
    screen_width, screen_height = pyautogui.size()

    # 3. 计算区域边界
    min_x = int(screen_width * 0.11)
    max_x = int(screen_width * 0.97)
    min_y = int(screen_height * 0.15)
    max_y = int(screen_height * 0.91)

    # 4. 生成随机坐标
    random_x = random.randint(min_x, max_x)
    random_y = random.randint(min_y, max_y)

    print(f"Screen size: {screen_width}x{screen_height}")
    print(f"Calculated Area: X[{min_x}-{max_x}], Y[{min_y}-{max_y}]")
    print(f"Clicking at coordinates: X={random_x}, Y={random_y}")

    # 5. 执行点击
    # 注意：这里使用生成的 random_x 和 random_y，而不是固定的 max_x/max_y
    pyautogui.click(x=random_x, y=random_y, duration=1.0)
    print("Click executed successfully.")

if __name__ == "__main__":
    # 主程序入口
    print(f"脚本启动时间: {datetime.now().strftime('%H:%M:%S')}")
    
    # 如果需要等待用户切换窗口，可以在调用前 sleep
    time.sleep(2) 
    
    # 直接调用函数，函数内部会自动判断时间
    click_random_top_right_area()