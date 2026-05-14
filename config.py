# -*- coding: utf-8 -*-
"""
Config module - Load environment variables from .env file
"""

import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()


class Config:
    """Configuration class to access environment variables"""

    # Login credentials
    BAOCAO_USERNAME = os.getenv('BAOCAO_USERNAME', '')
    BAOCAO_PASSWORD = os.getenv('BAOCAO_PASSWORD', '')
    BAOCAO_BASE_URL = os.getenv('BAOCAO_BASE_URL', 'https://baocao.hanoi.vnpt.vn')

    # Backward compatibility
    BAOCAO_URL = BAOCAO_BASE_URL + '/'

    # OTP settings
    OTP_FILE_PATH = os.getenv('OTP_FILE_PATH', '/home/vtst/otp/otp_logs.txt')
    OTP_MAX_AGE_SECONDS = int(os.getenv('OTP_MAX_AGE_SECONDS', '120'))

    # Report URLs - Full URLs from .env
    REPORT_C11_URL = os.getenv('REPORT_C11_URL', f'{BAOCAO_BASE_URL}/report/report-info?id=522457&menu_id=522561')
    REPORT_C12_URL = os.getenv('REPORT_C12_URL', f'{BAOCAO_BASE_URL}/report/report-info?id=522459&menu_id=522562')
    REPORT_C13_URL = os.getenv('REPORT_C13_URL', f'{BAOCAO_BASE_URL}/report/report-info?id=522461&menu_id=522563')
    REPORT_C14_URL = os.getenv('REPORT_C14_URL', f'{BAOCAO_BASE_URL}/report/report-info?id=522463&menu_id=522564')
    REPORT_C15_URL = os.getenv('REPORT_C15_URL', f'{BAOCAO_BASE_URL}/report/report-info?id=522465&menu_id=522565')
    REPORT_I15_URL = os.getenv('REPORT_I15_URL', f'{BAOCAO_BASE_URL}/report/report-info?id=521580&menu_id=521601')
    REPORT_TBM_URL = os.getenv('REPORT_TBM_URL', f'{BAOCAO_BASE_URL}/report/report-info?id=270922&menu_id=276242')
    REPORT_THUCTANG_URL = os.getenv('REPORT_THUCTANG_URL', f'{BAOCAO_BASE_URL}/report/report-info?id=521560&menu_id=521600')

    # Report Data URLs - Templates (chứa {date} placeholder)
    REPORT_KR6_NVKT_URL = os.getenv('REPORT_KR6_NVKT_URL',
        f'{BAOCAO_BASE_URL}/report/report-info-data?id=264354&vdvvt_id=9&vdenngay={{date}}&vdonvi_id=14324&vloai=1')

    REPORT_KR6_TONGHOP_URL = os.getenv('REPORT_KR6_TONGHOP_URL',
        f'{BAOCAO_BASE_URL}/report/report-info-data?id=260054&vdvvt_id=9&vdenngay={{date}}&vdonvi_id=14324&vloai=1&vloai_bc=luyke_thang_hoancong')

    REPORT_KR7_NVKT_URL = os.getenv('REPORT_KR7_NVKT_URL',
        f'{BAOCAO_BASE_URL}/report/report-info-data?id=264354&vdvvt_id=8&vdenngay={{date}}&vdonvi_id=14324&vloai=1')

    REPORT_KR7_TONGHOP_URL = os.getenv('REPORT_KR7_TONGHOP_URL',
        f'{BAOCAO_BASE_URL}/report/report-info-data?id=260054&vdvvt_id=8&vdenngay={{date}}&vdonvi_id=14324&vloai=1&vloai_bc=luyke_thang_hoancong')

    # Timeouts (milliseconds)
    PAGE_LOAD_TIMEOUT = int(os.getenv('PAGE_LOAD_TIMEOUT', '60000'))
    NETWORK_IDLE_TIMEOUT = int(os.getenv('NETWORK_IDLE_TIMEOUT', '500000'))
    DOWNLOAD_TIMEOUT = int(os.getenv('DOWNLOAD_TIMEOUT', '120000'))

    # Browser settings
    BROWSER_HEADLESS = os.getenv('BROWSER_HEADLESS', 'True').lower() == 'true'
    ACCEPT_DOWNLOADS = os.getenv('ACCEPT_DOWNLOADS', 'True').lower() == 'true'

    @classmethod
    def get_report_url(cls, report_type):
        """
        Get full report URL based on report type

        Args:
            report_type: 'c11', 'c12', 'c13', 'c14', 'c15', 'i15', 'tbm', 'thuctang'

        Returns:
            str: Full report URL from .env
        """
        report_type = report_type.lower()

        url_mapping = {
            'c11': cls.REPORT_C11_URL,
            'c12': cls.REPORT_C12_URL,
            'c13': cls.REPORT_C13_URL,
            'c14': cls.REPORT_C14_URL,
            'c15': cls.REPORT_C15_URL,
            'i15': cls.REPORT_I15_URL,
            'tbm': cls.REPORT_TBM_URL,
            'thuctang': cls.REPORT_THUCTANG_URL,
        }

        return url_mapping.get(report_type, cls.BAOCAO_BASE_URL)

    @classmethod
    def get_report_data_url(cls, report_type, encoded_date):
        """
        Get report data URL with date parameter

        Args:
            report_type: 'kr6_nvkt', 'kr6_tonghop', 'kr7_nvkt', 'kr7_tonghop'
            encoded_date: URL-encoded date string (e.g., '01%2F11%2F2025')

        Returns:
            str: Full report data URL with date replaced
        """
        report_type = report_type.lower()

        url_templates = {
            'kr6_nvkt': cls.REPORT_KR6_NVKT_URL,
            'kr6_tonghop': cls.REPORT_KR6_TONGHOP_URL,
            'kr7_nvkt': cls.REPORT_KR7_NVKT_URL,
            'kr7_tonghop': cls.REPORT_KR7_TONGHOP_URL,
        }

        template = url_templates.get(report_type)
        if template:
            return template.replace('{date}', encoded_date)

        return None

    @classmethod
    def print_config(cls):
        """Print current configuration (without password)"""
        print("="*80)
        print("CURRENT CONFIGURATION")
        print("="*80)
        print(f"BAOCAO_BASE_URL: {cls.BAOCAO_BASE_URL}")
        print(f"BAOCAO_USERNAME: {cls.BAOCAO_USERNAME}")
        print(f"BAOCAO_PASSWORD: {'*' * len(cls.BAOCAO_PASSWORD)}")
        print(f"\nOTP_FILE_PATH: {cls.OTP_FILE_PATH}")
        print(f"OTP_MAX_AGE_SECONDS: {cls.OTP_MAX_AGE_SECONDS}")
        print(f"\nPAGE_LOAD_TIMEOUT: {cls.PAGE_LOAD_TIMEOUT}ms")
        print(f"NETWORK_IDLE_TIMEOUT: {cls.NETWORK_IDLE_TIMEOUT}ms")
        print(f"BROWSER_HEADLESS: {cls.BROWSER_HEADLESS}")
        print("\nREPORT URLs:")
        print(f"  C1.1: {cls.REPORT_C11_URL}")
        print(f"  I1.5: {cls.REPORT_I15_URL}")
        print(f"  TBM:  {cls.REPORT_TBM_URL}")
        print("\nREPORT DATA URLs (templates):")
        print(f"  KR6 NVKT: {cls.REPORT_KR6_NVKT_URL[:80]}...")
        print("="*80)

    @classmethod
    def validate(cls):
        """Validate required configuration values"""
        errors = []

        if not cls.BAOCAO_USERNAME:
            errors.append("BAOCAO_USERNAME không được để trống")
        if not cls.BAOCAO_PASSWORD:
            errors.append("BAOCAO_PASSWORD không được để trống")
        if not cls.OTP_FILE_PATH:
            errors.append("OTP_FILE_PATH không được để trống")

        if errors:
            raise ValueError("Config validation failed:\n- " + "\n- ".join(errors))

        return True


if __name__ == "__main__":
    # Test config
    try:
        Config.validate()
        print("✅ Config validation passed\n")
    except ValueError as e:
        print(f"❌ Config validation failed:\n{e}\n")

    Config.print_config()

    print("\n" + "="*80)
    print("TEST get_report_url:")
    print("="*80)
    print(f"C1.1: {Config.get_report_url('c11')}")
    print(f"C1.2: {Config.get_report_url('c12')}")
    print(f"I1.5: {Config.get_report_url('i15')}")

    print("\n" + "="*80)
    print("TEST get_report_data_url:")
    print("="*80)
    from urllib.parse import quote
    test_date = quote('01/11/2025', safe='')
    print(f"KR6 NVKT: {Config.get_report_data_url('kr6_nvkt', test_date)}")
    print(f"KR7 Tổng hợp: {Config.get_report_data_url('kr7_tonghop', test_date)}")
