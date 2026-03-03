import pyautogui
import random
import time

def click_random_top_right_area():
    # Get the screen dimensions
    screen_width, screen_height = pyautogui.size()

    # Calculate the boundaries for the "4/5 right" and "4/5 top" area.
    # "Right 4/5" means X goes from 20% of the screen width to 100%
    min_x = int(screen_width * 0.2)
    max_x = int(screen_width * 0.9)

    # "Top 4/5" means Y goes from 0% of the screen height down to 80%
    min_y = int(screen_height * 0.2)
    max_y = int(screen_height * 0.6)

    # Generate a random X and Y coordinate within those boundaries
    random_x = random.randint(min_x, max_x)
    random_y = random.randint(min_y, max_y)

    print(f"Screen size: {screen_width}x{screen_height}")
    print(f"Clicking at coordinates: X={random_x}, Y={random_y}")

    # Move the mouse to the coordinates and click
    # duration=0.5 adds a half-second animation so you can see where it goes
    pyautogui.click(x=random_x, y=random_y, duration=0.5)

if __name__ == "__main__":
    # A short delay before the script runs, giving you time to switch windows if needed
    time.sleep(2) 
    click_random_top_right_area()