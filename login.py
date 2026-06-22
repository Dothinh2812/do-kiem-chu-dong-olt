# -*- coding: utf-8 -*-
from playwright.sync_api import sync_playwright
import time
import os
import re

try:
    from .config import Config
except ImportError:
    from config import Config


OTP_FIELD_SELECTORS = (
    '//*[@id="passOTP"]',
    "#passOTP",
)

OTP_CONFIRM_BUTTON_SELECTORS = (
    '//*[@id="loginForm"]/section/button',
    '//*[@id="loginForm"]/div[1]/button',
    '#loginForm button[type="submit"]',
    '#loginForm button',
    'button[type="submit"]',
)


def _first_visible_locator(page, selectors, first_timeout=30000, fallback_timeout=5000):
    last_error = None
    for index, selector in enumerate(selectors):
        locator = page.locator(selector)
        try:
            timeout = first_timeout if index == 0 else fallback_timeout
            locator.wait_for(state="visible", timeout=timeout)
            return locator
        except Exception as exc:
            last_error = exc

    raise last_error


def _click_first_visible_button(page, selectors):
    button = _first_visible_locator(page, selectors)
    button.click()
    return button


def read_otp_from_file():
    """
    Đọc mã OTP từ file (đường dẫn cấu hình trong .env)
    Chỉ chấp nhận file được tạo trong vòng OTP_MAX_AGE_SECONDS gần đây
    Xóa mã OTP sau khi đọc thành công

    Returns:
        str: Mã OTP 6 chữ số hoặc None nếu không tìm thấy
    """
    file_path = Config.OTP_FILE_PATH
    max_age_seconds = Config.OTP_MAX_AGE_SECONDS
    max_retries = 10
    retry_count = 0
    otp_code = None

    print(f"Đang đọc mã OTP từ file: {file_path}")
    print(f"OTP max age: {max_age_seconds} seconds")

    while retry_count < max_retries:
        if os.path.exists(file_path):
            file_time = os.path.getmtime(file_path)
            file_time_formatted = time.strftime('%H:%M:%S', time.localtime(file_time))
            print(f"File time: {file_time_formatted}")
            current_time = time.time()
            current_time_formatted = time.strftime('%H:%M:%S', time.localtime(current_time))
            print(f"Current time: {current_time_formatted}")
            time_diff = current_time - file_time
            print(f"Time difference: {time_diff:.2f} seconds")

            if time_diff <= max_age_seconds:  # File is recent enough
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # Find 6-digit number using regex
                    otp_match = re.search(r'\b\d{6}\b', content)
                    if otp_match:
                        otp_code = otp_match.group(0)
                        print(f"✅ Found OTP code in file: {otp_code}")

                        # Xóa mã OTP bằng cách ghi đè file trống
                        try:
                            with open(file_path, 'w', encoding='utf-8') as f:
                                f.write('')
                            print("✅ Đã xóa mã OTP khỏi file")
                        except Exception as e:
                            print(f"⚠️ Không thể xóa mã OTP: {e}")

                        return otp_code
            else:
                print(f"⚠️ File quá cũ (hơn {max_age_seconds} giây), chờ file mới...")
        else:
            print(f"⚠️ Không tìm thấy file OTP tại {file_path}")

        retry_count += 1
        if retry_count < max_retries:
            print(f"Waiting for OTP... Attempt {retry_count}/{max_retries}")
            time.sleep(2)  # Wait 2 seconds before next attempt

    print("❌ Không tìm thấy OTP hợp lệ sau nhiều lần thử")
    return None


def login_baocao_hanoi():
    """
    Đăng nhập vào trang báo cáo (URL từ config)

    Returns:
        tuple: (page_baocao, browser_baocao, playwright_baocao) - Trả về đối tượng page đã đăng nhập
    """
    print(f"=== Bắt đầu đăng nhập vào {Config.BAOCAO_URL} ===")

    # Khởi tạo Playwright và Browser
    playwright_baocao = sync_playwright().start()
    browser_baocao = playwright_baocao.chromium.launch(headless=Config.BROWSER_HEADLESS)
    context_baocao = browser_baocao.new_context(accept_downloads=Config.ACCEPT_DOWNLOADS)
    page_baocao = context_baocao.new_page()

    # Bước 1: Truy cập trang đăng nhập
    print("Đang truy cập trang đăng nhập...")
    page_baocao.goto(Config.BAOCAO_URL, timeout=Config.PAGE_LOAD_TIMEOUT)
    page_baocao.wait_for_load_state("networkidle", timeout=Config.PAGE_LOAD_TIMEOUT)

    # Bước 2: Điền username
    print(f"Đang điền username: {Config.BAOCAO_USERNAME}")
    username_field = _first_visible_locator(page_baocao, ('//*[@id="username"]', "#username"))
    username_field.fill(Config.BAOCAO_USERNAME)
    time.sleep(1)

    # Bước 3: Điền password
    print("Đang điền password...")
    password_field = _first_visible_locator(page_baocao, ('//*[@id="password"]', "#password"))
    password_field.fill(Config.BAOCAO_PASSWORD)
    time.sleep(1)

    # Bước 4: Click button Đăng nhập
    print("Đang click button Đăng nhập...")
    _click_first_visible_button(
        page_baocao,
        (
            '//*[@id="fm1"]/section/button',
            '#fm1 button[type="submit"]',
            '#fm1 button',
            'button[type="submit"]',
        ),
    )
    time.sleep(3)

    # Bước 5: Đợi trường input OTP xuất hiện
    print("Đang đợi trường nhập OTP...")
    otp_field = _first_visible_locator(page_baocao, OTP_FIELD_SELECTORS)

    # Bước 6: Đọc OTP từ file
    otp_code = read_otp_from_file()

    if otp_code is None:
        print("❌ Không thể đọc OTP từ file.")
        print("⏸️  Vui lòng nhập OTP thủ công vào trường trên trang web và click xác nhận.")
        print("⏸️  Script sẽ chờ 10 giây để bạn hoàn tất đăng nhập...")
        time.sleep(10)
        print("✅ Tiếp tục sau khi chờ...")
    else:
        # Bước 7: Điền OTP
        print(f"Đang điền OTP: {otp_code}")
        otp_field.fill(otp_code)
        time.sleep(1)

        # Bước 8: Click button xác nhận OTP
        print("Đang click button xác nhận OTP...")
        _click_first_visible_button(page_baocao, OTP_CONFIRM_BUTTON_SELECTORS)
        time.sleep(5)

    # Bước 9: Kiểm tra đăng nhập thành công
    page_baocao.wait_for_load_state("networkidle", timeout=Config.PAGE_LOAD_TIMEOUT)
    print("✅ Đăng nhập thành công!")

    # Kiểm tra cookies và session
    cookies = context_baocao.cookies()
    print(f"📝 Đã lưu {len(cookies)} cookies")

    return page_baocao, browser_baocao, playwright_baocao
