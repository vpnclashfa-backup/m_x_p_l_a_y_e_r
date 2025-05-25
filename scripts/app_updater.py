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

VERSION_REGEX_PATTERNS = [
    r'(?<![\w.-])(?:[vV])?(\d+(?:\.\d+){1,3}(?:(?:[-._]?[a-zA-Z0-9]+)+)?)(?![.\w])',
    r'(?<![\w.-])(?:[vV])?(\d+(?:\.\d+){1,2})(?![.\w])',
]
VERSION_PATTERNS_FOR_CLEANING = [
    r'\s*[vV]?\d+(?:\.\d+){1,3}(?:(?:[-._]?[a-zA-Z0-9]+)+)?\b',
    r'\s*[vV]?\d+(?:\.\d+){1,2}\b',
    r'\s+\d+(?:\.\d+)*\b' 
]

COMMON_VARIANT_KEYWORDS_TO_DETECT_AND_CLEAN = [
    "Mod-Extra", "مود اکسترا", "موداکسترا",
    "Mod-Lite", "مود لایت", "مودلایت",
    "Ad-Free", "بدون تبلیغات",
    "Unlocked", "آنلاک شده", "آنلاک",
    "Patched", "پچ شده",
    "Premium", "پرمیوم",
    "Persian", "فارسی",
    "English", "انگلیسی",
    "Universal", "یونیورسال",
    "Original", "اورجینال", "اصلی", "معمولی",
    "Arm64-v8a", "Armeabi-v7a", "x86_64",
    "Arm64", "Armv7", "Arm", "x86", 
    "Windows", "ویندوز", "PC", "کامپیوتر", 
    "macOS", "Mac", "OSX", 
    "Linux", "لینوکس", 
    "Ultra", "اولترا",
    "Clone", "کلون",
    "Beta", "بتا",
    "Full", "کامل",
    "Lite", "لایت",
    "Main", 
    "Data", "دیتا", "Obb",
    "Mod", "مود", 
    "Pro", "پرو", 
    "VIP", "وی آی پی",
    "Plus", "پلاس",
    "Image", "تصویر", 
    "Audio", "صوتی", 
    "Video", "ویدیو", 
    "Document", "سند", "Text", "متن",
    "Archive", "آرشیو", 
    "Font", "فونت"
]

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
            if parsed_current > parsed_last: return True
            elif parsed_current < parsed_last: return False
            else: return current_v_str != last_v_str and current_v_str > last_v_str 
        except InvalidVersion:
            logging.warning(f"InvalidVersion ao تجزیه '{current_v_str}' یا '{last_v_str}'. مقایسه رشته ای.")
            return current_v_str != last_v_str and current_v_str > last_v_str
        except TypeError: 
            logging.warning(f"TypeError هنگام مقایسه '{current_v_str}' با '{last_v_str}'. مقایسه رشته ای.")
            return current_v_str != last_v_str and current_v_str > last_v_str
    except Exception as e:
        logging.error(f"خطا در compare_versions ('{current_v_str}' vs '{last_v_str}'): {e}")
        return current_v_str != last_v_str and current_v_str > last_v_str

def sanitize_text_for_tracking_id(text): # Simplified sanitize for tracking ID parts
    if not text: return ""
    text_cleaned = text.strip().lower()
    text_cleaned = text_cleaned.replace('–', '-').replace('—', '-')
    text_cleaned = re.sub(r'[^a-z0-9-_]', '', text_cleaned) # Keep only alphanumeric, dash, underscore
    text_cleaned = re.sub(r'[-_]+', '_', text_cleaned) # Consolidate dash/underscore to single underscore
    text_cleaned = text_cleaned.strip('_')
    return text_cleaned


def aggressively_clean_name_for_tracking(name_to_clean):
    """Aggressively cleans a name for tracking ID purposes."""
    cleaned_name = name_to_clean
    
    for pattern in VERSION_PATTERNS_FOR_CLEANING:
        cleaned_name = re.sub(pattern, '', cleaned_name, flags=re.IGNORECASE).strip("-_ ")
        cleaned_name = re.sub(r'\s+', ' ', cleaned_name).strip("-_ ")

    all_keywords_for_aggressive_clean = COMMON_VARIANT_KEYWORDS_TO_DETECT_AND_CLEAN + \
                                        ["PC", "کامپیوتر", "ویندوز", "Windows", "Lite", "لایت", "Pro", "پرو"] 
    
    sorted_keywords = sorted(list(set(all_keywords_for_aggressive_clean)), key=len, reverse=True)

    for kw in sorted_keywords:
        kw_regex = r'\b' + re.escape(kw) + r'\b'
        prev_name = None
        while prev_name != cleaned_name: 
            prev_name = cleaned_name
            cleaned_name = re.sub(kw_regex, '', cleaned_name, flags=re.IGNORECASE).strip("-_ ")
            cleaned_name = re.sub(r'\s+', ' ', cleaned_name).strip("-_ ")

    cleaned_name = re.sub(r'\s*\((?:www\.)?farsroid\.com.*?\)\s*$', '', cleaned_name, flags=re.IGNORECASE).strip()
    cleaned_name = re.sub(r'\s*[-–—]\s*Farsroid\s*$', '', cleaned_name, flags=re.IGNORECASE).strip()
    cleaned_name = cleaned_name.strip(' -–—') 
    cleaned_name = re.sub(r'\s+', ' ', cleaned_name).strip()
    if not cleaned_name: 
        name_parts = name_to_clean.split()
        if name_parts: cleaned_name = name_parts[0] 
    return cleaned_name


def extract_app_name_from_page(soup, page_url):
    """Extracts app name from H1/Title, performs light cleaning (versions at end, site tags)."""
    app_name_candidate = None
    h1_tag = soup.find('h1', class_=re.compile(r'title', re.IGNORECASE))
    if h1_tag and h1_tag.text.strip():
        app_name_candidate = h1_tag.text.strip()
    
    if not app_name_candidate:
        title_tag = soup.find('title')
        if title_tag and title_tag.text.strip():
            app_name_candidate = title_tag.text.strip()
            app_name_candidate = re.sub(r'\s*[-|–—]\s*(?:فارسروید|دانلود.*)$', '', app_name_candidate, flags=re.IGNORECASE).strip()
            app_name_candidate = re.sub(r'\s*–\s*اپلیکیشن.*$', '', app_name_candidate, flags=re.IGNORECASE).strip()

    if app_name_candidate:
        original_name = app_name_candidate 
        if app_name_candidate.lower().startswith("دانلود "):
            app_name_candidate = app_name_candidate[len("دانلود "):].strip()
        
        page_name_for_display = app_name_candidate # Keep it richer for display
        
        # Lightly clean for display (remove Farsroid tags, maybe trailing versions)
        page_name_for_display = re.sub(r'\s*\((?:www\.)?farsroid\.com.*?\)\s*$', '', page_name_for_display, flags=re.IGNORECASE).strip()
        for pattern in VERSION_PATTERNS_FOR_CLEANING: # Remove versions if they are at the very end
            page_name_for_display = re.sub(pattern + r'$', '', page_name_for_display, flags=re.IGNORECASE).strip("-_ ")

        page_name_for_display = page_name_for_display.strip(' -–—')
        page_name_for_display = re.sub(r'\s+', ' ', page_name_for_display).strip()


        if page_name_for_display:
            logging.info(f"نام برنامه از H1/Title (اصلی: '{original_name}', برای نمایش: '{page_name_for_display}')")
            return page_name_for_display
    
    # Fallback to URL if H1/Title fails (less aggressive cleaning here)
    logging.info(f"نام برنامه از H1/Title استخراج نشد، تلاش برای استخراج از URL: {page_url}")
    parsed_url = urlparse(page_url)
    path_parts = [part for part in unquote(parsed_url.path).split('/') if part]
    if path_parts:
        guessed_name = path_parts[-1]
        # Remove extension
        known_extensions_regex = r'\.(apk|zip|exe|rar|xapk|apks|msi|dmg|pkg|deb|rpm|appimage|tar\.gz|tgz|tar\.bz2|tbz2|tar\.xz|txz|7z|gz|bz2|xz|jpg|jpeg|png|gif|bmp|tiff|tif|webp|svg|ico|mp3|wav|ogg|aac|flac|m4a|wma|mp4|mkv|avi|mov|wmv|flv|webm|mpeg|mpg|txt|pdf|doc|docx|xls|xlsx|ppt|pptx|odt|ods|odp|rtf|csv|html|htm|xml|json|md|ttf|otf|woff|woff2|eot)$'
        guessed_name = re.sub(known_extensions_regex, '', guessed_name, flags=re.IGNORECASE)
        # Remove versions
        for pattern in VERSION_PATTERNS_FOR_CLEANING:
            guessed_name = re.sub(pattern, '', guessed_name, flags=re.IGNORECASE).strip("-_ ")
        # Remove only very generic URL terms
        generic_url_terms = r'\b(دانلود|Download|برنامه|App|Apk|Farsroid|Android)\b'
        guessed_name = re.sub(generic_url_terms, '', guessed_name, flags=re.IGNORECASE).strip("-_ ")
        # Capitalize and join
        guessed_name = ' '.join(word.capitalize() for word in re.split(r'[-_]+', guessed_name) if word)
        guessed_name = re.sub(r'\s+', ' ', guessed_name).strip()
        if guessed_name:
            logging.info(f"نام حدس زده شده از URL (پاکسازی شده): {guessed_name}")
            return guessed_name
            
    logging.warning(f"نام برنامه از هیچ منبعی استخراج نشد. URL: {page_url}")
    return "UnknownApp"


def get_page_source_with_selenium(url, wait_time=20, wait_for_class="downloadbox"):
    # Note: The URL cleaning is now done in main() before this function is called.
    # So, the 'url' parameter here is expected to be already cleaned.
    logging.info(f"در حال دریافت {url} با Selenium...")
    chrome_options = ChromeOptions()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu") 
    chrome_options.add_argument("--window-size=1920,1080") 
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36")
    driver = None
    try:
        try:
            driver_path = ChromeDriverManager().install()
            service = ChromeService(executable_path=driver_path)
        except Exception as e_driver_manager:
            logging.warning(f"خطا در ChromeDriverManager: {e_driver_manager}. استفاده از درایور پیشفرض.")
            service = ChromeService() # Fallback to default service if manager fails
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.get(url) # The URL passed here should be clean
        WebDriverWait(driver, wait_time).until(EC.presence_of_element_located((By.CLASS_NAME, wait_for_class)))
        time.sleep(5) 
        page_source = driver.page_source
        logging.info(f"موفقیت در دریافت سورس صفحه با Selenium برای {url}")
        return page_source
    except Exception as e:
        logging.error(f"خطای Selenium برای {url}: {e}", exc_info=True)
        if driver: 
            try: return driver.page_source # Try to get source even on error if driver exists
            except: pass
        return None
    finally:
        if driver:
            driver.quit()


def extract_version_from_text_or_url(text_content, url_content):
    if text_content:
        for pattern in VERSION_REGEX_PATTERNS:
            match = re.search(pattern, text_content)
            if match: return match.group(1).strip("-_ ")
    if url_content:
        for pattern in VERSION_REGEX_PATTERNS:
            match = re.search(pattern, url_content) 
            if match: return match.group(1).strip("-_ ")
    # Fallback pattern if more specific ones fail
    fallback_pattern = r'(\d+\.\d+(?:\.\d+){0,2}(?:[.-]?[a-zA-Z0-9]+)*)' 
    if text_content:
        match = re.search(fallback_pattern, text_content)
        if match: return match.group(1).strip("-_ ")
    if url_content:
        match = re.search(fallback_pattern, url_content)
        if match: return match.group(1).strip("-_ ")
    return None

def get_file_extension_from_url(download_url, combined_text_for_variant):
    parsed_url_path = urlparse(download_url).path
    raw_filename_from_url = os.path.basename(parsed_url_path)
    
    double_extensions = [".tar.gz", ".tar.bz2", ".tar.xz"]
    for de in double_extensions:
        if raw_filename_from_url.lower().endswith(de): return de

    _, ext_from_url = os.path.splitext(raw_filename_from_url)
    
    known_extensions = [
        '.apk', '.zip', '.exe', '.rar', '.xapk', '.apks', '.7z', '.gz', '.bz2', '.xz',
        '.msi', '.dmg', '.pkg', '.deb', '.rpm', '.appimage',
        '.tgz', '.tbz2', '.txz', 
        '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp', '.svg', '.ico',
        '.mp3', '.wav', '.ogg', '.aac', '.flac', '.m4a', '.wma',
        '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.mpeg', '.mpg',
        '.txt', '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', 
        '.odt', '.ods', '.odp', '.rtf', '.csv', '.html', '.htm', '.xml', '.json', '.md',
        '.ttf', '.otf', '.woff', '.woff2', '.eot'
    ]
    
    if ext_from_url and ext_from_url.lower() in known_extensions:
        return ext_from_url.lower()
    else:
        # Guess based on variant text if primary extension detection fails
        combined_text_for_variant_lower = combined_text_for_variant.lower()
        if "windows" in combined_text_for_variant_lower or "pc" in combined_text_for_variant_lower : return ".exe" 
        if "macos" in combined_text_for_variant_lower or "mac" in combined_text_for_variant_lower: return ".dmg"
        if "linux" in combined_text_for_variant_lower : return ".appimage" 
        if "data" in combined_text_for_variant_lower or "obb" in combined_text_for_variant_lower : return ".zip" 
        if "font" in combined_text_for_variant_lower: return ".zip" 
        if ext_from_url: return ext_from_url.lower() # Return original if still unknown but present
        return ".bin" # Default fallback


def scrape_farsroid_page(page_url, soup, tracker_data):
    updates_found_on_page = []
    # page_app_name_full is the name from H1/Title, lightly cleaned
    page_app_name_for_display = extract_app_name_from_page(soup, page_url) 
    logging.info(f"پردازش صفحه: {page_url} (نام برنامه از صفحه برای نمایش: '{page_app_name_for_display}')")

    # For tracking ID, use an aggressively cleaned name to ensure stability
    base_app_name_for_tracking_id = aggressively_clean_name_for_tracking(page_app_name_for_display) # Use the display name as input
    if not base_app_name_for_tracking_id: base_app_name_for_tracking_id = "UnknownApp" 
    logging.info(f"  نام پایه برای شناسه ردیابی: '{base_app_name_for_tracking_id}'")

    download_box = soup.find('section', class_='downloadbox')
    if not download_box: return updates_found_on_page
    download_links_ul = download_box.find('ul', class_='download-links')
    if not download_links_ul: return updates_found_on_page
    found_lis = download_links_ul.find_all('li', class_='download-link')
    if not found_lis: return updates_found_on_page

    logging.info(f"تعداد {len(found_lis)} آیتم li.download-link پیدا شد.")

    for i, li in enumerate(found_lis):
        logging.info(f"--- پردازش li شماره {i+1} ---")
        link_tag = li.find('a', class_='download-btn')
        if not link_tag or not link_tag.get('href'): continue

        download_url = urljoin(page_url, link_tag['href'])
        link_text_span = link_tag.find('span', class_='txt')
        link_text = link_text_span.text.strip() if link_text_span else ""
        logging.info(f"  URL: {download_url}, متن لینک: {link_text}")

        filename_from_url_decoded = unquote(urlparse(download_url).path.split('/')[-1])
        current_version = extract_version_from_text_or_url(link_text, filename_from_url_decoded)

        if not current_version:
            logging.warning(f"  نسخه استخراج نشد.")
            continue
        logging.info(f"  نسخه: {current_version}")

        # --- تشخیص نوع (Variant) فقط از لینک دانلود ---
        link_only_variant_parts = []
        # Prepare a combined text from link and filename for robust variant detection
        combined_text_for_link_variant_detection = (filename_from_url_decoded.lower() + " " + link_text.lower()).replace('(farsroid.com)', '').replace('دانلود فایل نصبی', '').replace('برنامه با لینک مستقیم', '').strip()
        combined_text_for_link_variant_detection = re.sub(r'\b(?:با لینک مستقیم|مگابایت|\d+)\b', '', combined_text_for_link_variant_detection, flags=re.IGNORECASE).strip()
        
        variant_keywords_ordered = { 
            "Mod-Extra": ["mod-extra", "مود اکسترا"], "Mod-Lite": ["mod-lite", "مود لایت"],
            "Ad-Free": ["ad-free", "بدون تبلیغات"], "Unlocked": ["unlocked", "آنلاک"], "Patched": ["patched", "پچ شده"],
            "Premium": ["premium", "پرمیوم"], "Ultra": ["ultra", "اولترا"], "Clone": ["clone", "کلون"],
            "Beta": ["beta", "بتا"], "Full": ["full", "کامل"], "Lite": ["lite", "لایت"], "Main": ["main"],
            "Pro": ["pro", "پرو"], "VIP": ["vip"], "Plus": ["plus", "پلاس"],
            "Persian": ["persian", "فارسی"], "English": ["english", "انگلیسی"],
            "Arm64-v8a": ["arm64-v8a", "arm64"], "Armeabi-v7a": ["armeabi-v7a", "armv7"],
            "x86_64": ["x86_64"], "x86": ["x86"], "Arm": ["arm"], 
            "Mod": ["mod", "مود"], 
            "PC": ["pc", "کامپیوتر"], "Windows": ["windows", "ویندوز"], 
            "Data": ["data", "obb", "دیتا"]
        }
        
        temp_combined_text = combined_text_for_link_variant_detection
        for key, patterns in variant_keywords_ordered.items():
            for pattern in patterns:
                if re.search(r'\b' + re.escape(pattern) + r'\b', temp_combined_text, flags=re.IGNORECASE):
                    if key == "Mod" and any(k in link_only_variant_parts for k in ["Mod-Extra", "Mod-Lite"]): continue
                    if key == "Lite" and "Mod-Lite" in link_only_variant_parts: continue
                    if key not in link_only_variant_parts: link_only_variant_parts.append(key)
                    break 
        
        file_extension = get_file_extension_from_url(download_url, combined_text_for_link_variant_detection)
        logging.info(f"  پسوند فایل: {file_extension}")
        
        if file_extension == ".exe":
            if "PC" in link_only_variant_parts:
                link_only_variant_parts.remove("PC")
            if "Windows" not in link_only_variant_parts:
                link_only_variant_parts.append("Windows")
        
        arch_found_in_link_variants = any(arch_kw in link_only_variant_parts for arch_kw in ["Arm64-v8a", "Armeabi-v7a", "x86_64", "x86", "Arm"])
        
        temp_display_variants = sorted(list(set(link_only_variant_parts))) 
        variant_final_for_display_tracking = "-".join(temp_display_variants) if temp_display_variants else ""
        
        if not variant_final_for_display_tracking:
            if file_extension == ".apk" and not arch_found_in_link_variants : variant_final_for_display_tracking = "Universal"
            elif file_extension == ".exe": variant_final_for_display_tracking = "Windows"
            # Add more defaults based on extension if needed
            else: variant_final_for_display_tracking = "Default" # Fallback for JSON/tracking
        
        logging.info(f"  نوع نهایی برای نمایش/ردیابی: '{variant_final_for_display_tracking}'")

        tracking_id_app_part = sanitize_text_for_tracking_id(base_app_name_for_tracking_id)
        tracking_id_variant_part = sanitize_text_for_tracking_id(variant_final_for_display_tracking)
        tracking_id = f"{tracking_id_app_part}_{tracking_id_variant_part}".lower()
        tracking_id = re.sub(r'_+', '_', tracking_id).strip('_')
        # Refine tracking_id: remove generic suffixes if not an APK or if they are redundant
        if tracking_id.endswith(("_default", "_archive", "_image", "_audio", "_video", "_document", "_font")) and file_extension != ".apk":
            tracking_id = tracking_id.rsplit('_', 1)[0]
        elif tracking_id.endswith('_universal') and file_extension != ".apk":
            tracking_id = tracking_id[:-len('_universal')]
        
        if not tracking_id_app_part and tracking_id_variant_part: # If app name was empty, use variant as base
            tracking_id = tracking_id_variant_part
        elif not tracking_id_variant_part and tracking_id_app_part: # If variant was empty, use app name as base
            tracking_id = tracking_id_app_part
        elif not tracking_id_app_part and not tracking_id_variant_part:
            tracking_id = "unknown_app_variant" # Absolute fallback

        logging.info(f"  شناسه ردیابی: {tracking_id}")
        
        # --- ساخت نام فایل پیشنهادی (رویکرد جدید و ساده‌تر) ---
        suggested_filename = filename_from_url_decoded
        # فقط پسوند سایت را حذف کن
        site_suffix_pattern = r'\s*\((?:www\.)?farsroid\.com.*?\)\s*'
        suggested_filename = re.sub(site_suffix_pattern, '', suggested_filename, flags=re.IGNORECASE).strip()
        # اطمینان از اینکه پسوند فایل حفظ شده
        if not os.path.splitext(suggested_filename)[1]: # اگر پسوند ندارد
            base_name_no_ext = os.path.splitext(filename_from_url_decoded)[0]
            base_name_no_ext_cleaned = re.sub(site_suffix_pattern, '', base_name_no_ext, flags=re.IGNORECASE).strip()
            suggested_filename = base_name_no_ext_cleaned + file_extension

        logging.info(f"  نام فایل پیشنهادی (ساده شده): {suggested_filename}")
        
        last_known_version = tracker_data.get(tracking_id, "0.0.0")
        if compare_versions(current_version, last_known_version):
            logging.info(f"    => آپدیت جدید برای {tracking_id}: {current_version} (قبلی: {last_known_version})")
            updates_found_on_page.append({
                "app_name": page_app_name_for_display, # Use the richer name for display
                "version": current_version,
                "variant": variant_final_for_display_tracking, 
                "download_url": download_url,
                "page_url": page_url,
                "tracking_id": tracking_id,
                "suggested_filename": suggested_filename,
                "current_version_for_tracking": current_version # Store the version used for comparison
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
        sys.exit(1) 

    raw_urls_from_file = []
    with open(URL_FILE, 'r', encoding='utf-8') as f:
        raw_urls_from_file = [line.strip() for line in f if line.strip() and not line.startswith('#')]

    urls_to_process = []
    for raw_url in raw_urls_from_file:
        # *** NEW: Clean the URL by removing leading BOM characters ***
        cleaned_url = raw_url.lstrip('\ufeff')
        if cleaned_url != raw_url:
            # Log if a URL was actually cleaned
            logging.info(f"کاراکتر BOM از ابتدای URL '{raw_url}' حذف و به '{cleaned_url}' تبدیل شد.")
        urls_to_process.append(cleaned_url)

    if not urls_to_process:
        logging.info("فایل URL ها خالی است یا فقط شامل کامنت است.")
        with open(OUTPUT_JSON_FILE, 'w', encoding='utf-8') as f: json.dump([], f)
        if os.getenv('GITHUB_OUTPUT'):
            with open(GITHUB_OUTPUT_FILE, 'a', encoding='utf-8') as gh_output: gh_output.write(f"updates_count=0\n")
        return

    tracker_data = load_tracker()
    all_updates_found = []
    
    for page_url in urls_to_process: # page_url is now the cleaned version
        logging.info(f"\n--- شروع بررسی URL: {page_url} ---")
        # Pass the cleaned page_url to Selenium function
        page_content = get_page_source_with_selenium(page_url, wait_for_class="downloadbox") 
        
        if not page_content:
            logging.error(f"محتوای صفحه برای {page_url} با Selenium دریافت نشد. رد شدن...")
            continue
        try:
            soup = BeautifulSoup(page_content, 'html.parser')
            # Assuming only farsroid.com URLs are processed this way for now
            if "farsroid.com" in page_url.lower(): 
                updates_on_page = scrape_farsroid_page(page_url, soup, tracker_data)
                all_updates_found.extend(updates_on_page)
            else:
                logging.warning(f"خراش دهنده برای {page_url} پیاده سازی نشده است.")
        except Exception as e:
            logging.error(f"خطا هنگام پردازش محتوای دریافت شده از Selenium برای {page_url}: {e}", exc_info=True)
        logging.info(f"--- پایان بررسی URL: {page_url} ---")

    new_tracker_data_for_save = tracker_data.copy()
    for update_item in all_updates_found:
        new_tracker_data_for_save[update_item["tracking_id"]] = update_item["current_version_for_tracking"]

    with open(OUTPUT_JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(all_updates_found, f, ensure_ascii=False, indent=2)
    
    try:
        with open(TRACKING_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_tracker_data_for_save, f, ensure_ascii=False, indent=2)
        logging.info(f"فایل ردیاب {TRACKING_FILE} با موفقیت بروزرسانی شد.")
    except Exception as e:
        logging.error(f"خطا در ذخیره فایل ردیاب {TRACKING_FILE}: {e}")

    num_updates = len(all_updates_found)
    if os.getenv('GITHUB_OUTPUT'): 
        with open(GITHUB_OUTPUT_FILE, 'a', encoding='utf-8') as gh_output:
            gh_output.write(f"updates_count={num_updates}\n")
    logging.info(f"\nخلاصه: {num_updates} آپدیت پیدا شد. جزئیات در {OUTPUT_JSON_FILE}")

if __name__ == "__main__":
    main()
