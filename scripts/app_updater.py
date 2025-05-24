import requests # همچنان برای برخی کارها ممکن است لازم باشد
from bs4 import BeautifulSoup
import re
import json
import os
from packaging.version import parse, InvalidVersion
from urllib.parse import urljoin, urlparse, unquote
import logging
import time

# ایمپورت های Selenium
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from webdriver_manager.chrome import ChromeDriverManager # برای مدیریت آسان درایور کروم
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By


# --- پیکربندی اولیه ---
URL_FILE = "urls_to_check.txt"
TRACKING_FILE = "versions_tracker.json"
OUTPUT_JSON_FILE = "updates_found.json"
GITHUB_OUTPUT_FILE = os.environ.get('GITHUB_OUTPUT', 'local_github_output.txt') # برای خروجی تعداد

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

# --- توابع کمکی ---

def load_tracker():
    """فایل ردیابی نسخه ها را بارگذاری می کند."""
    if os.path.exists(TRACKING_FILE):
        try:
            with open(TRACKING_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                logging.info(f"فایل ردیابی {TRACKING_FILE} با موفقیت بارگذاری شد.")
                return data
        except json.JSONDecodeError:
            logging.warning(f"{TRACKING_FILE} خراب است. با ردیاب خالی شروع می شود.")
            return {}
    logging.info(f"فایل ردیابی {TRACKING_FILE} یافت نشد. با ردیاب خالی شروع می شود.")
    return {}

def compare_versions(current_v_str, last_v_str):
    """نسخه فعلی را با آخرین نسخه شناخته شده مقایسه می کند."""
    logging.info(f"مقایسه نسخه ها: فعلی='{current_v_str}', قبلی='{last_v_str}'")
    try:
        if not current_v_str:
            logging.warning("نسخه فعلی نامعتبر است (خالی).")
            return False

        if not last_v_str or last_v_str == "0.0.0":
            logging.info("نسخه قبلی یافت نشد یا 0.0.0 بود، نسخه فعلی جدید است.")
            return True

        normalize_for_parse = lambda v: re.split(r'[^0-9.]', v, 1)[0].strip('.')
        
        current_norm = normalize_for_parse(current_v_str)
        last_norm = normalize_for_parse(last_v_str)

        if not current_norm:
            logging.warning(f"نسخه فعلی '{current_v_str}' پس از نرمال سازی نامعتبر شد ('{current_norm}').")
            return False # یا True اگر می خواهید در صورت عدم امکان مقایسه، دانلود کنید
        if not last_norm:
            logging.warning(f"نسخه قبلی '{last_v_str}' پس از نرمال سازی نامعتبر شد ('{last_norm}').")
            return True # اگر نسخه قبلی نامعتبر است، فعلی را جدید در نظر بگیرید

        parsed_current = parse(current_norm)
        parsed_last = parse(last_norm)
        is_newer = parsed_current > parsed_last
        logging.info(f"نتیجه مقایسه (تجزیه شده): فعلی='{parsed_current}', قبلی='{parsed_last}', جدیدتر: {is_newer}")
        return is_newer
    except InvalidVersion as e:
        logging.warning(f"خطای InvalidVersion هنگام مقایسه '{current_v_str}' با '{last_v_str}': {e}. مقایسه به صورت رشته ای انجام می شود.")
        return current_v_str != last_v_str # بازگشت به مقایسه رشته ای
    except Exception as e:
        logging.error(f"خطای پیش بینی نشده هنگام مقایسه نسخه ها: {e}. مقایسه به صورت رشته ای انجام می شود.")
        return current_v_str != last_v_str

def sanitize_text(text, for_filename=False):
    """متن را پاکسازی می کند."""
    if not text: return ""
    text = text.strip().lower()
    # حذف (FarsRoid.com) و موارد مشابه
    text = re.sub(r'\((farsroid\.com|.*?)\)', '', text, flags=re.IGNORECASE).strip()
    text = re.sub(r'[\(\)]', '', text) # حذف پرانتز اضافی
    if for_filename:
        # برای نام فایل، کاراکترهای غیرمجاز بیشتری را جایگزین می کنیم
        text = re.sub(r'[<>:"/\\|?*]', '_', text)
        text = re.sub(r'\s+', '_', text) # جایگزینی فاصله ها با آندرلاین
    else:
        # برای شناسه، فقط فاصله ها را با آندرلاین جایگزین می کنیم
        text = re.sub(r'\s+', '_', text)
    return text

def extract_app_name_from_page(soup, page_url):
    """تلاش برای استخراج نام برنامه از صفحه."""
    # تلاش برای تگ H1 با کلاس title (معمولا عنوان اصلی پست)
    h1_tag = soup.find('h1', class_=re.compile(r'title', re.IGNORECASE))
    if h1_tag:
        title_text = h1_tag.text.strip()
        # حذف "دانلود " از ابتدا و هر چیزی بعد از شماره نسخه یا " – "
        match = re.match(r'^(?:دانلود\s+)?(.+?)(?:\s+\d+\.\d+.*|\s+–\s+.*|$)', title_text, re.IGNORECASE)
        if match and match.group(1):
            app_name = match.group(1).strip()
            # حذف کلمات کلیدی اضافی مانند "اندروید" اگر در انتها باشند
            app_name = re.sub(r'\s+(?:برنامه|اندروید|مود|آنلاک|پرمیوم|حرفه ای|طلایی)$', '', app_name, flags=re.IGNORECASE).strip()
            if app_name: return app_name

    # اگر از H1 پیدا نشد، تگ <title> را امتحان کنید
    title_tag = soup.find('title')
    if title_tag:
        title_text = title_tag.text.strip()
        match = re.match(r'^(?:دانلود\s+)?(.+?)(?:\s+\d+\.\d+.*|\s+برنامه.*|\s+–\s+.*|$)', title_text, re.IGNORECASE)
        if match and match.group(1):
            app_name = match.group(1).strip()
            app_name = re.sub(r'\s+(?:اندروید|آیفون|ios|android)$', '', app_name, flags=re.IGNORECASE).strip()
            if app_name: return app_name
    
    # راه حل نهایی: استفاده از URL
    parsed_url = urlparse(page_url)
    path_parts = [part for part in parsed_url.path.split('/') if part]
    if path_parts:
        guessed_name = path_parts[-1].replace('-', ' ').replace('_', ' ')
        guessed_name = re.sub(r'\.(html|php|asp|aspx)$', '', guessed_name, flags=re.IGNORECASE)
        return guessed_name.title()
    return "UnknownApp"

def get_page_source_with_selenium(url, wait_time=20, wait_for_class="downloadbox"):
    """صفحه را با Selenium بارگذاری کرده و سورس HTML آن را پس از اجرای JS برمی گرداند."""
    logging.info(f"در حال دریافت {url} با Selenium...")
    chrome_options = ChromeOptions()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080") # تنظیم اندازه پنجره
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36") # User-Agent جدیدتر

    driver = None
    try:
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        driver.get(url)
        
        logging.info(f"منتظر بارگذاری محتوای دینامیک (تا {wait_time} ثانیه) برای کلاس '{wait_for_class}'...")
        try:
            WebDriverWait(driver, wait_time).until(
                EC.presence_of_element_located((By.CLASS_NAME, wait_for_class))
            )
            # حتی پس از پیدا شدن عنصر اولیه، کمی صبر اضافی برای اطمینان از اجرای کامل اسکریپت ها
            # و همچنین برای بارگذاری کامل عناصر داخل باکس دانلود
            time.sleep(7) # افزایش زمان انتظار
            logging.info(f"عنصر با کلاس '{wait_for_class}' پیدا شد و زمان اضافی برای بارگذاری داده شد.")
        except Exception as e_wait:
            logging.warning(f"Timeout یا خطا هنگام انتظار برای '{wait_for_class}': {e_wait}. ممکن است صفحه کامل بارگذاری نشده باشد.")
            # در صورت خطا، سورس فعلی را برمی گردانیم تا ببینیم چه چیزی بارگذاری شده
            if driver: return driver.page_source
            return None


        page_source = driver.page_source
        logging.info(f"موفقیت در دریافت سورس صفحه با Selenium برای {url}")
        return page_source
    except Exception as e:
        logging.error(f"خطای Selenium هنگام دریافت {url}: {e}", exc_info=True)
        return None
    finally:
        if driver:
            driver.quit()
            logging.info("Selenium WebDriver بسته شد.")


# --- منطق خراش دادن خاص سایت فارسروید ---
def scrape_farsroid_page(page_url, soup, tracker_data):
    updates_found_on_page = []
    page_app_name = extract_app_name_from_page(soup, page_url)
    logging.info(f"پردازش صفحه فارسروید: {page_url} (نام برنامه: {page_app_name})")

    download_box = soup.find('section', class_='downloadbox')
    if not download_box:
        logging.warning(f"باکس دانلود در {page_url} پیدا نشد.")
        return updates_found_on_page
    logging.info("باکس دانلود پیدا شد.")

    download_links_ul = download_box.find('ul', class_='download-links')
    if not download_links_ul:
        logging.warning(f"لیست لینک های دانلود (ul.download-links) در {page_url} پیدا نشد.")
        logging.info(f"محتوای HTML باکس دانلود (اگر ul پیدا نشد):\n{download_box.prettify()[:2000]}")
        return updates_found_on_page
    logging.info("لیست لینک های دانلود (ul.download-links) پیدا شد.")

    # محتوای کامل ul.download-links را برای دیباگ چاپ می کنیم
    logging.info(f"محتوای کامل HTML تگ ul.download-links (قبل از جستجوی li):\n{download_links_ul.prettify()}")

    found_lis = download_links_ul.find_all('li', class_='download-link')
    logging.info(f"تعداد {len(found_lis)} آیتم li.download-link پیدا شد.")

    if not found_lis:
        logging.warning("هیچ آیتم li.download-link پیدا نشد. شاید ساختار عوض شده یا توسط JS بارگذاری نشده؟")
        all_li_in_ul = download_links_ul.find_all('li') # بررسی همه li ها
        logging.info(f"تعداد کل li ها (صرف نظر از کلاس) داخل ul: {len(all_li_in_ul)}")
        if all_li_in_ul: logging.info(f"نمونه اولین li (صرف نظر از کلاس):\n{all_li_in_ul[0].prettify()}")
        return updates_found_on_page

    for i, li in enumerate(found_lis):
        logging.info(f"--- پردازش li شماره {i+1} ---")
        link_tag = li.find('a', class_='download-btn')
        if not link_tag:
            logging.warning(f"  تگ a.download-btn در li شماره {i+1} پیدا نشد. رد شدن...")
            logging.debug(f"محتوای li شماره {i+1}:\n{li.prettify()}")
            continue

        download_url = urljoin(page_url, link_tag.get('href'))
        link_text_span = link_tag.find('span', class_='txt')
        link_text = link_text_span.text.strip() if link_text_span else "متن لینک یافت نشد"

        if not download_url or not link_text:
            logging.warning(f"  URL یا متن لینک در li شماره {i+1} ناقص است. رد شدن...")
            continue

        logging.info(f"  URL: {download_url}")
        logging.info(f"  متن: {link_text}")

        version_match_url = re.search(r'(\d+\.\d+(?:\.\d+){0,2}(?:[.-][a-zA-Z0-9]+)*)', download_url)
        current_version = version_match_url.group(1) if version_match_url else None
        if not current_version:
            version_match_text = re.search(r'(\d+\.\d+(?:\.\d+){0,2}(?:[.-][a-zA-Z0-9]+)*)', link_text)
            current_version = version_match_text.group(1) if version_match_text else None

        if not current_version:
            logging.warning(f"  نسخه از URL '{download_url}' یا متن '{link_text}' استخراج نشد. رد شدن...")
            continue
        logging.info(f"  نسخه: {current_version}")

        variant = "Universal"
        filename_in_url = unquote(urlparse(download_url).path.split('/')[-1])
        if re.search(r'Armeabi-v7a', filename_in_url + link_text, re.IGNORECASE): variant = "Armeabi-v7a"
        elif re.search(r'Arm64-v8a', filename_in_url + link_text, re.IGNORECASE): variant = "Arm64-v8a"
        elif re.search(r'x86_64', filename_in_url + link_text, re.IGNORECASE): variant = "x86_64"
        elif re.search(r'x86', filename_in_url + link_text, re.IGNORECASE): variant = "x86"
        elif re.search(r'Universal', filename_in_url + link_text, re.IGNORECASE): variant = "Universal"
        logging.info(f"  نوع: {variant}")

        tracking_id = f"{sanitize_text(page_app_name)}_{sanitize_text(variant)}".lower()
        last_known_version = tracker_data.get(tracking_id, "0.0.0")

        if compare_versions(current_version, last_known_version):
            logging.info(f"    => آپدیت جدید برای {tracking_id}: {current_version}")
            app_name_for_file = sanitize_text(page_app_name, for_filename=True)
            variant_for_file = sanitize_text(variant, for_filename=True)
            suggested_filename = f"{app_name_for_file}_v{current_version}_{variant_for_file}.apk"
            updates_found_on_page.append({
                "app_name": page_app_name,
                "version": current_version,
                "variant": variant,
                "download_url": download_url,
                "page_url": page_url,
                "tracking_id": tracking_id,
                "suggested_filename": suggested_filename,
                "current_version_for_tracking": current_version
            })
        else:
            logging.info(f"    => {tracking_id} به‌روز است.")
    return updates_found_on_page

# --- منطق اصلی ---
def main():
    if not os.path.exists(URL_FILE):
        logging.error(f"فایل URL ها یافت نشد: {URL_FILE}")
        with open(OUTPUT_JSON_FILE, 'w', encoding='utf-8') as f: json.dump([], f)
        if 'GITHUB_OUTPUT' in os.environ:
            with open(GITHUB_OUTPUT_FILE, 'a') as gh_output: gh_output.write(f"updates_count=0\n")
        sys.exit(1)

    with open(URL_FILE, 'r', encoding='utf-8') as f:
        urls_to_process = [line.strip() for line in f if line.strip() and not line.startswith('#')]

    if not urls_to_process:
        logging.info("فایل URL ها خالی است.")
        with open(OUTPUT_JSON_FILE, 'w', encoding='utf-8') as f: json.dump([], f)
        if 'GITHUB_OUTPUT' in os.environ:
            with open(GITHUB_OUTPUT_FILE, 'a') as gh_output: gh_output.write(f"updates_count=0\n")
        return

    tracker_data = load_tracker()
    all_updates_found = []

    for page_url in urls_to_process:
        logging.info(f"\n--- شروع بررسی URL: {page_url} ---")
        page_content = get_page_source_with_selenium(page_url, wait_for_class="downloadbox") # استفاده از Selenium
        
        if not page_content:
            logging.error(f"محتوای صفحه برای {page_url} با Selenium دریافت نشد. رد شدن...")
            continue
        
        try:
            soup = BeautifulSoup(page_content, 'html.parser')
            if "farsroid.com" in page_url.lower():
                updates_on_page = scrape_farsroid_page(page_url, soup, tracker_data)
                all_updates_found.extend(updates_on_page)
            else:
                logging.warning(f"خراش دهنده برای {page_url} پیاده سازی نشده است.")
        except Exception as e:
            logging.error(f"خطا هنگام پردازش محتوای دریافت شده از Selenium برای {page_url}: {e}", exc_info=True)
        logging.info(f"--- پایان بررسی URL: {page_url} ---")

    with open(OUTPUT_JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(all_updates_found, f, ensure_ascii=False, indent=2)

    num_updates = len(all_updates_found)
    if 'GITHUB_OUTPUT' in os.environ:
        with open(GITHUB_OUTPUT_FILE, 'a') as gh_output:
            gh_output.write(f"updates_count={num_updates}\n")

    logging.info(f"\nخلاصه: {num_updates} آپدیت پیدا شد. جزئیات در {OUTPUT_JSON_FILE}")

if __name__ == "__main__":
    main()
