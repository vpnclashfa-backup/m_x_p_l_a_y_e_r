import requests
from bs4 import BeautifulSoup
import re
import json
import os
from packaging.version import parse, InvalidVersion
from urllib.parse import urljoin, urlparse, unquote
import logging
import time
import sys

# Selenium imports
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By

URL_FILE = "urls_to_check.txt"
TRACKING_FILE = "versions_tracker.json"
OUTPUT_JSON_FILE = "updates_found.json"
GITHUB_OUTPUT_FILE = os.getenv('GITHUB_OUTPUT', 'local_github_output.txt')

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

def load_tracker():
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
    logging.info(f"مقایسه نسخه ها: فعلی='{current_v_str}', قبلی='{last_v_str}'")
    try:
        if not current_v_str:
            logging.warning("نسخه فعلی نامعتبر است (خالی).")
            return False
        if not last_v_str or last_v_str == "0.0.0":
            logging.info(f"نسخه قبلی یافت نشد یا 0.0.0 بود. نسخه فعلی '{current_v_str}' جدید است.")
            return True
        try:
            parsed_current = parse(current_v_str)
            parsed_last = parse(last_v_str)
            if parsed_current > parsed_last:
                logging.info(f"نتیجه مقایسه (packaging.version): فعلی='{parsed_current}' > قبلی='{parsed_last}'. جدیدتر است.")
                return True
            elif parsed_current < parsed_last:
                logging.info(f"نتیجه مقایسه (packaging.version): فعلی='{parsed_current}' < قبلی='{parsed_last}'. جدیدتر نیست.")
                return False
            else: # parsed_current == parsed_last
                # اگر نسخه های تجزیه شده یکسان هستند، اما رشته های اصلی متفاوتند
                # (مثلا 1.0.0 در مقابل 1.0.0b)، رشته های اصلی را مقایسه کنید.
                if current_v_str != last_v_str:
                    logging.info(f"نسخه های تجزیه شده یکسان ('{parsed_current}'), اما رشته های اصلی متفاوت: فعلی='{current_v_str}', قبلی='{last_v_str}'. نتیجه مقایسه رشته ای: {current_v_str > last_v_str}")
                    return current_v_str > last_v_str
                logging.info(f"نتیجه مقایسه (packaging.version): فعلی='{parsed_current}' == قبلی='{parsed_last}'. جدیدتر نیست (یا یکسان است).")
                return False
        except InvalidVersion:
            logging.warning(f"InvalidVersion هنگام تجزیه مستقیم '{current_v_str}' یا '{last_v_str}'. مقایسه به صورت رشته ای.")
            # اگر یکی از نسخه ها نامعتبر باشد، مقایسه رشته ای انجام دهید
            # این حالت ممکن است زمانی رخ دهد که یک نسخه دارای حروف غیر استاندارد باشد
            return current_v_str != last_v_str and current_v_str > last_v_str
        except TypeError: # ممکن است در مقایسه انواع مختلف رخ دهد
            logging.warning(f"TypeError هنگام مقایسه تجزیه شده '{current_v_str}' با '{last_v_str}'. مقایسه به صورت رشته ای.")
            return current_v_str != last_v_str and current_v_str > last_v_str
    except Exception as e:
        logging.error(f"خطای پیش بینی نشده هنگام مقایسه نسخه ها ('{current_v_str}' vs '{last_v_str}'): {e}. مقایسه به صورت رشته ای.")
        return current_v_str != last_v_str and current_v_str > last_v_str


def sanitize_text(text, for_filename=False):
    if not text: return ""
    text = text.strip()
    # حذف عبارت های رایج مانند (farsroid.com) از انتهای متن
    text = re.sub(r'\s*\((?:farsroid\.com|www\.farsroid\.com|.*?)\)\s*$', '', text, flags=re.IGNORECASE).strip()
    if for_filename:
        text = text.lower()
        text = text.replace('–', '-').replace('—', '-') # یکسان سازی خط تیره ها
        text = re.sub(r'[<>:"/\\|?*()\[\]]', '_', text) # جایگزینی کاراکترهای نامعتبر در نام فایل
        text = re.sub(r'\s+', '_', text) # جایگزینی فاصله ها با آندرلاین
        text = re.sub(r'(-_|_-(?=_))', '_', text) # جایگزینی -_ یا _- که قبل از _ دیگری هستند با _
        text = re.sub(r'_+', '_', text) # چند آندرلاین پشت سر هم را یکی کن
        text = text.strip('_') # حذف آندرلاین از ابتدا و انتها
    else: # برای شناسه ردیابی یا موارد دیگر که محدودیت نام فایل را ندارند
        text = text.lower()
        text = text.replace('–', '-').replace('—', '-')
        text = re.sub(r'[\(\)\[\]]', '', text) # حذف پرانتز و براکت
        text = re.sub(r'\s+', '_', text)
        text = text.strip('_')
    return text

def extract_app_name_from_page(soup, page_url):
    app_name_candidate = None
    # اولویت با تگ h1 با کلاس شامل title
    h1_tag = soup.find('h1', class_=re.compile(r'title', re.IGNORECASE))
    if h1_tag and h1_tag.text.strip():
        app_name_candidate = h1_tag.text.strip()

    # اگر h1 نبود یا خالی بود، تلاش برای تگ title
    if not app_name_candidate:
        title_tag = soup.find('title')
        if title_tag and title_tag.text.strip():
            app_name_candidate = title_tag.text.strip()
            # تلاش برای حذف بخش‌های اضافی از تگ title
            app_name_candidate = re.sub(r'\s*[-|–—]\s*(?:فارسروید|دانلود.*)$', '', app_name_candidate, flags=re.IGNORECASE).strip()
            app_name_candidate = re.sub(r'\s*–\s*اپلیکیشن.*$', '', app_name_candidate, flags=re.IGNORECASE).strip()

    if app_name_candidate:
        # حذف پیشوند "دانلود " اگر وجود داشته باشد
        if app_name_candidate.lower().startswith("دانلود "):
            app_name_candidate = app_name_candidate[len("دانلود "):].strip()
        if app_name_candidate: # اگر بعد از حذف هنوز چیزی باقی مانده
            return app_name_candidate

    # اگر از تگ های بالا نامی استخراج نشد، تلاش برای استخراج از URL
    logging.info(f"نام برنامه از H1 یا Title به طور کامل استخراج نشد، تلاش برای استخراج از URL: {page_url}")
    parsed_url = urlparse(page_url)
    path_parts = [part for part in unquote(parsed_url.path).split('/') if part] # unquote برای هندل کردن کاراکترهای % در URL
    if path_parts:
        guessed_name = path_parts[-1] # آخرین بخش مسیر URL
        # حذف پسوندهای رایج فایل
        guessed_name = re.sub(r'\.(apk|zip|html|php|asp|aspx)$', '', guessed_name, flags=re.IGNORECASE)
        # حذف نسخه از انتهای نام فایل URL (مثلا -1.2.3 یا _v2.3.4b)
        guessed_name = re.sub(r'[-_][vV]?\d+(\.\d+)+[a-zA-Z0-9.-]*$', '', guessed_name, flags=re.IGNORECASE)
        # حذف نسخه از ابتدای نام فایل URL (اگر با نسخه شروع شده باشد)
        guessed_name = re.sub(r'^[vV]?\d+(\.\d+)+[a-zA-Z0-9.-]*[-_]', '', guessed_name, flags=re.IGNORECASE)
        # تبدیل کلمات جدا شده با - یا _ به حروف بزرگ و اتصال با فاصله
        guessed_name = ' '.join(word.capitalize() for word in re.split(r'[-_]+', guessed_name) if word)
        # حذف کلمات کلیدی رایج
        guessed_name = re.sub(r'\b(دانلود|Download|برنامه|App|Apk|Mod|Hack|Premium|Pro|Full|Unlocked|Final|Update|Android|Farsroid)\b', '', guessed_name, flags=re.IGNORECASE).strip()
        guessed_name = re.sub(r'\s+', ' ', guessed_name).strip() # نرمال سازی فاصله ها
        if guessed_name:
            logging.info(f"نام حدس زده شده از URL: {guessed_name}")
            return guessed_name
    logging.warning(f"نام برنامه از هیچ منبعی استخراج نشد. URL: {page_url}")
    return "UnknownApp" # یک نام پیشفرض

def get_page_source_with_selenium(url, wait_time=20, wait_for_class="downloadbox"):
    logging.info(f"در حال دریافت {url} با Selenium...")
    chrome_options = ChromeOptions()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu") # معمولا برای headless لازم است
    chrome_options.add_argument("--window-size=1920,1080") # اندازه پنجره برای برخی سایت ها مهم است
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36")
    driver = None
    try:
        # تلاش برای استفاده از webdriver-manager برای نصب خودکار ChromeDriver
        try:
            driver_path = ChromeDriverManager().install()
            service = ChromeService(executable_path=driver_path)
            logging.info(f"ChromeDriverManager در مسیر '{driver_path}' پیدا/نصب شد.")
        except Exception as e_driver_manager:
            logging.warning(f"خطا در استفاده از ChromeDriverManager: {e_driver_manager}. تلاش برای استفاده از درایور پیشفرض سیستم.")
            # اگر webdriver-manager به هر دلیلی کار نکرد، به صورت پیشفرض تلاش می‌کند.
            # این ممکن است نیاز به نصب دستی chromedriver در محیط GitHub Action داشته باشد.
            service = ChromeService()

        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.get(url)
        logging.info(f"منتظر بارگذاری محتوای دینامیک (تا {wait_time} ثانیه) برای کلاس '{wait_for_class}'...")
        try:
            WebDriverWait(driver, wait_time).until(
                EC.presence_of_element_located((By.CLASS_NAME, wait_for_class))
            )
            # اضافه کردن یک تاخیر کوچک دیگر برای اطمینان از اجرای کامل اسکریپت‌های JS صفحه
            time.sleep(5) # این مقدار ممکن است نیاز به تنظیم داشته باشد
            logging.info(f"عنصر با کلاس '{wait_for_class}' پیدا شد و زمان اضافی برای بارگذاری داده شد.")
        except Exception as e_wait:
            logging.warning(f"Timeout یا خطا هنگام انتظار برای '{wait_for_class}' در {url}: {e_wait}. ممکن است صفحه کامل بارگذاری نشده باشد یا کلاس مورد نظر وجود نداشته باشد.")
            # حتی اگر انتظار ناموفق بود، سورس فعلی صفحه را برگردان
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

def extract_version_from_text_or_url(text_content, url_content):
    """نسخه را از متن یا URL با استفاده از الگوهای دقیق‌تر استخراج می‌کند."""
    # الگوهای Regex برای پیدا کردن نسخه، از خاص به عام
    # (?<![\w.-]) برای جلوگیری از تطابق با بخشی از یک کلمه یا آی‌پی یا نام فایل طولانی‌تر
    # (?![.\w]) برای اطمینان از اینکه بعد از نسخه، کاراکتر نامربوطی نیامده
    version_regex_patterns = [
        r'(?<![\w.-])(?:[vV])?(\d+(?:\.\d+){1,3}(?:(?:[-._]?[a-zA-Z0-9]+)+)?)(?![.\w])', # e.g., v1.2.3, 2.3.4-beta, 1.0.0_RC1, 2.2.9b, 23.5.0.23
        r'(?<![\w.-])(?:[vV])?(\d+(?:\.\d+){1,2})(?![.\w])', # e.g., 1.0, 22.5 (ساده‌تر)
    ]
    
    # اولویت با متن لینک
    if text_content:
        for pattern in version_regex_patterns:
            match = re.search(pattern, text_content)
            if match:
                return match.group(1).strip()
            
    # سپس URL (نام فایل decode شده از URL)
    if url_content:
        for pattern in version_regex_patterns:
            match = re.search(pattern, url_content) 
            if match:
                return match.group(1).strip()
            
    # فال‌بک به یک regex عمومی‌تر اگر هیچکدام پیدا نشد
    fallback_pattern = r'(\d+\.\d+(?:\.\d+){0,2}(?:[.-]?[a-zA-Z0-9]+)*)' # کمی عمومی تر
    if text_content:
        match = re.search(fallback_pattern, text_content)
        if match: return match.group(1).strip()
    if url_content:
        match = re.search(fallback_pattern, url_content)
        if match: return match.group(1).strip()
    
    return None

def scrape_farsroid_page(page_url, soup, tracker_data):
    updates_found_on_page = []
    page_app_name_full = extract_app_name_from_page(soup, page_url) # نام کامل برنامه از صفحه
    logging.info(f"پردازش صفحه فارسروید: {page_url} (نام کامل برنامه: '{page_app_name_full}')")

    download_box = soup.find('section', class_='downloadbox')
    if not download_box:
        logging.warning(f"باکس دانلود در {page_url} پیدا نشد.")
        return updates_found_on_page
    
    download_links_ul = download_box.find('ul', class_='download-links')
    if not download_links_ul:
        logging.warning(f"لیست لینک های دانلود (ul.download-links) در {page_url} پیدا نشد.")
        return updates_found_on_page
        
    found_lis = download_links_ul.find_all('li', class_='download-link')
    if not found_lis:
        logging.warning("هیچ آیتم li.download-link پیدا نشد.")
        return updates_found_on_page

    logging.info(f"تعداد {len(found_lis)} آیتم li.download-link پیدا شد.")

    for i, li in enumerate(found_lis):
        logging.info(f"--- پردازش li شماره {i+1} ---")
        link_tag = li.find('a', class_='download-btn')
        if not link_tag or not link_tag.get('href'):
            logging.warning(f"  تگ دانلود معتبر در li شماره {i+1} پیدا نشد. رد شدن...")
            continue

        download_url = urljoin(page_url, link_tag['href'])
        link_text_span = link_tag.find('span', class_='txt')
        link_text = link_text_span.text.strip() if link_text_span else "متن لینک یافت نشد"

        logging.info(f"  URL: {download_url}")
        logging.info(f"  متن لینک: {link_text}")

        # نام فایل از خود URL (decode شده)
        filename_from_url_decoded = unquote(urlparse(download_url).path.split('/')[-1])
        logging.info(f"  نام فایل از URL (decoded): {filename_from_url_decoded}")
        
        current_version = extract_version_from_text_or_url(link_text, filename_from_url_decoded)

        if not current_version:
            logging.warning(f"  نسخه از متن لینک '{link_text}' یا نام فایل '{filename_from_url_decoded}' استخراج نشد. رد شدن...")
            continue
        logging.info(f"  نسخه استخراج شده: {current_version}")

        # --- تشخیص نوع (Variant) ---
        variant_parts = []
        # متن ترکیبی از نام فایل در URL و متن لینک برای تشخیص بهتر نوع
        combined_text_for_variant = (filename_from_url_decoded.lower() + " " + link_text.lower()).replace('(farsroid.com)', '')


        # کلمات کلیدی برای نوع (با اضافه شدن موارد جدید)
        if 'mod-extra' in combined_text_for_variant or 'مود اکسترا' in combined_text_for_variant or 'موداکسترا' in combined_text_for_variant:
            variant_parts.append("Mod-Extra")
        elif 'mod-lite' in combined_text_for_variant or 'مود لایت' in combined_text_for_variant or 'مودلایت' in combined_text_for_variant:
            variant_parts.append("Mod-Lite")
        elif 'mod' in combined_text_for_variant or 'مود شده' in combined_text_for_variant or 'مود' in combined_text_for_variant :
            variant_parts.append("Mod")
        
        if 'premium' in combined_text_for_variant or 'پرمیوم' in combined_text_for_variant:
            if not any(p.lower().startswith("mod") for p in variant_parts):
                 variant_parts.append("Premium")

        # --- کلمات کلیدی جدید ---
        if 'ultra' in combined_text_for_variant or 'اولترا' in combined_text_for_variant:
            variant_parts.append("Ultra")
        if 'unlocked' in combined_text_for_variant or 'آنلاک' in combined_text_for_variant:
            variant_parts.append("Unlocked")
        if 'ad-free' in combined_text_for_variant or 'بدون تبلیغات' in combined_text_for_variant:
            variant_parts.append("Ad-Free")
        if 'patched' in combined_text_for_variant or 'پچ شده' in combined_text_for_variant:
            variant_parts.append("Patched")
        if 'vip' in combined_text_for_variant:
            variant_parts.append("VIP")
        if 'plus' in combined_text_for_variant or 'پلاس' in combined_text_for_variant:
            variant_parts.append("Plus")
        if 'clone' in combined_text_for_variant or 'کلون' in combined_text_for_variant:
            variant_parts.append("Clone")
        if 'full' in combined_text_for_variant or 'کامل' in combined_text_for_variant:
            if not any(k in p.lower() for p in variant_parts for k in ["mod", "premium", "unlocked", "vip"]):
                 variant_parts.append("Full")
        if 'beta' in combined_text_for_variant or 'بتا' in combined_text_for_variant:
            variant_parts.append("Beta")
        # --- پایان کلمات کلیدی جدید ---

        if not any("lite" in p.lower() for p in variant_parts) and ('lite' in combined_text_for_variant or 'لایت' in combined_text_for_variant):
             variant_parts.append("Lite")

        # تشخیص زبان 
        if 'persian' in combined_text_for_variant or 'فارسی' in combined_text_for_variant:
            variant_parts.append("Persian")
        elif 'english' in combined_text_for_variant or 'انگلیسی' in combined_text_for_variant:
            if not any("Persian" in p for p in variant_parts):
                 variant_parts.append("English")
        
        # تشخیص معماری
        arch_found = False
        if 'arm64-v8a' in combined_text_for_variant or 'arm64' in combined_text_for_variant: variant_parts.append("Arm64-v8a"); arch_found=True
        elif 'armeabi-v7a' in combined_text_for_variant or 'armv7' in combined_text_for_variant: variant_parts.append("Armeabi-v7a"); arch_found=True
        elif 'arm' in combined_text_for_variant and not arch_found: variant_parts.append("Arm"); arch_found=True
        elif 'x86_64' in combined_text_for_variant: variant_parts.append("x86_64"); arch_found=True
        elif 'x86' in combined_text_for_variant and not arch_found : variant_parts.append("x86"); arch_found=True

        file_extension = ".zip" if download_url.lower().endswith(".zip") else ".apk"
        
        # اگر فایل zip است
        if file_extension == ".zip":
            if "windows" in combined_text_for_variant or "ویندوز" in combined_text_for_variant:
                 if not variant_parts: variant_parts.append("Windows")
            elif "data" in combined_text_for_variant or "دیتا" in combined_text_for_variant or "obb" in combined_text_for_variant :
                 if not variant_parts: variant_parts.append("Data")
            elif not variant_parts:
                 variant_parts.append("Data") 
        
        # اگر apk است و هیچ نوع خاصی ندارد
        if file_extension == ".apk" and not variant_parts and not arch_found:
            if 'universal' in combined_text_for_variant or 'اصلی' in combined_text_for_variant or 'original' in combined_text_for_variant or 'معمولی' in combined_text_for_variant:
                variant_parts.append("Universal")
            elif 'main' in combined_text_for_variant:
                 variant_parts.append("Main")

        # ساختن رشته نهایی نوع (variant_final)
        unique_variant_parts = sorted(list(set(p for p in variant_parts if p)))
        if not unique_variant_parts:
            # اگر هیچ بخشی برای نوع پیدا نشد
            if file_extension == ".apk":
                if 'اصلی' in link_text or 'معمولی' in link_text:
                    variant_final = "Universal"
                elif 'universal' in filename_from_url_decoded.lower() or 'main' in filename_from_url_decoded.lower():
                    variant_final = "Universal"
                else:
                    variant_final = "Default"
            else:
                variant_final = "Default"
        else:
            variant_final = "-".join(unique_variant_parts)
            if not variant_final:
                 variant_final = "Universal" if file_extension == ".apk" else "Default"
        
        logging.info(f"  نوع (Variant) نهایی: {variant_final}")

        # --- شناسه ردیابی (Tracking ID) ---
        base_app_name_for_id = page_app_name_full
        base_app_name_for_id = re.sub(r'\s*[vV]?' + re.escape(current_version) + r'\b', '', base_app_name_for_id, flags=re.IGNORECASE).strip()
        base_app_name_for_id = re.split(r'\s*[-–—]\s*', base_app_name_for_id, 1)[0].strip()
        if not base_app_name_for_id: base_app_name_for_id = "App"

        tracking_id_app_part = sanitize_text(base_app_name_for_id, for_filename=False)
        tracking_id_variant_part = sanitize_text(variant_final, for_filename=False)
        tracking_id = f"{tracking_id_app_part}_{tracking_id_variant_part}".lower()
        tracking_id = re.sub(r'_+', '_', tracking_id).strip('_')
        logging.info(f"  شناسه ردیابی (پیشنهادی): {tracking_id} (مقدار آن نسخه خواهد بود)")
        
        # --- نام فایل پیشنهادی (Suggested Filename) ---
        app_name_for_file_base = base_app_name_for_id
        app_name_sanitized = sanitize_text(app_name_for_file_base, for_filename=True)
        version_for_file = sanitize_text(current_version, for_filename=True).replace('.', '_')
        variant_sanitized_for_file = sanitize_text(variant_final, for_filename=True)

        filename_constructor_parts = [app_name_sanitized]
        if version_for_file:
            filename_constructor_parts.append(f"v{version_for_file}")
        if variant_sanitized_for_file: 
            filename_constructor_parts.append(variant_sanitized_for_file)
            
        suggested_filename = "_".join(filter(None, filename_constructor_parts)) + file_extension
        suggested_filename = re.sub(r'_+', '_', suggested_filename).strip('_')
        logging.info(f"  نام فایل پیشنهادی: {suggested_filename}")
        
        # مقایسه با نسخه قبلی ذخیره شده برای این tracking_id
        last_known_version = tracker_data.get(tracking_id, "0.0.0")
        if compare_versions(current_version, last_known_version):
            logging.info(f"    => آپدیت جدید برای {tracking_id}: {current_version} (قبلی: {last_known_version})")
            updates_found_on_page.append({
                "app_name": page_app_name_full,
                "version": current_version,
                "variant": variant_final,
                "download_url": download_url,
                "page_url": page_url,
                "tracking_id": tracking_id,
                "suggested_filename": suggested_filename,
                "current_version_for_tracking": current_version
            })
        else:
            logging.info(f"    => {tracking_id} به‌روز است (فعلی: {current_version}, قبلی: {last_known_version}).")
    return updates_found_on_page

def main():
    if not os.path.exists(URL_FILE):
        logging.error(f"فایل URL ها یافت نشد: {URL_FILE}")
        with open(OUTPUT_JSON_FILE, 'w', encoding='utf-8') as f: json.dump([], f)
        if os.getenv('GITHUB_OUTPUT'):
            with open(GITHUB_OUTPUT_FILE, 'a', encoding='utf-8') as gh_output: gh_output.write(f"updates_count=0\n")
        sys.exit(1) # خروج با کد خطا

    with open(URL_FILE, 'r', encoding='utf-8') as f:
        urls_to_process = [line.strip() for line in f if line.strip() and not line.startswith('#')]

    if not urls_to_process:
        logging.info("فایل URL ها خالی است یا فقط شامل کامنت است.")
        with open(OUTPUT_JSON_FILE, 'w', encoding='utf-8') as f: json.dump([], f)
        if os.getenv('GITHUB_OUTPUT'):
            with open(GITHUB_OUTPUT_FILE, 'a', encoding='utf-8') as gh_output: gh_output.write(f"updates_count=0\n")
        return

    tracker_data = load_tracker()
    all_updates_found = []
    # new_tracker_data برای ذخیره سازی نهایی استفاده می‌شود.
    # در طول اجرا، tracker_data اصلی برای مقایسه استفاده می‌شود.
    # new_tracker_data در انتها با مقادیر جدید آپدیت می‌شود.

    for page_url in urls_to_process:
        logging.info(f"\n--- شروع بررسی URL: {page_url} ---")
        # همیشه از Selenium برای فارسروید استفاده کنید چون محتوای دانلود باکس ممکن است دینامیک باشد
        page_content = get_page_source_with_selenium(page_url, wait_for_class="downloadbox") 
        
        if not page_content:
            logging.error(f"محتوای صفحه برای {page_url} با Selenium دریافت نشد. رد شدن...")
            continue
        try:
            soup = BeautifulSoup(page_content, 'html.parser')
            if "farsroid.com" in page_url.lower(): # یا هر شرط دیگری برای تشخیص نوع صفحه
                updates_on_page = scrape_farsroid_page(page_url, soup, tracker_data)
                all_updates_found.extend(updates_on_page)
            else:
                logging.warning(f"خراش دهنده برای {page_url} پیاده سازی نشده است.")
        except Exception as e:
            logging.error(f"خطا هنگام پردازش محتوای دریافت شده از Selenium برای {page_url}: {e}", exc_info=True)
        logging.info(f"--- پایان بررسی URL: {page_url} ---")

    # آپدیت فایل ردیاب با اطلاعات جدید
    # tracker_data اصلی را کپی کرده و سپس آپدیت کنید
    new_tracker_data_for_save = tracker_data.copy()
    for update_item in all_updates_found:
        # از tracking_id جدید (appname_variant) و current_version_for_tracking استفاده کنید
        new_tracker_data_for_save[update_item["tracking_id"]] = update_item["current_version_for_tracking"]

    # ذخیره لیست آپدیت های پیدا شده در فایل JSON خروجی
    with open(OUTPUT_JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(all_updates_found, f, ensure_ascii=False, indent=2)
    
    # ذخیره فایل ردیاب آپدیت شده
    try:
        with open(TRACKING_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_tracker_data_for_save, f, ensure_ascii=False, indent=2)
        logging.info(f"فایل ردیاب {TRACKING_FILE} با موفقیت بروزرسانی شد.")
    except Exception as e:
        logging.error(f"خطا در ذخیره فایل ردیاب {TRACKING_FILE}: {e}")

    num_updates = len(all_updates_found)
    if os.getenv('GITHUB_OUTPUT'): # اگر در محیط GitHub Actions اجرا می‌شود
        with open(GITHUB_OUTPUT_FILE, 'a', encoding='utf-8') as gh_output:
            gh_output.write(f"updates_count={num_updates}\n")
    logging.info(f"\nخلاصه: {num_updates} آپدیت پیدا شد. جزئیات در {OUTPUT_JSON_FILE}")

if __name__ == "__main__":
    main()
