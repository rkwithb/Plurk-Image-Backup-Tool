import os
import re
import json
import requests
from datetime import datetime
from pathlib import Path

# 嘗試匯入 piexif，讓功能變成「選配」，增加程式的靈活性 (Flexible)
try:
    import piexif
    PIEXIF_AVAILABLE = True
except ImportError:
    PIEXIF_AVAILABLE = False

# --- 設定區 ---
OUTPUT_ROOT = Path("噗浪JS圖片備份_精確分類")
PLURKS_DIR = Path("data/plurks")
RESPONSES_DIR = Path("data/responses")

# 正規表示式：排除官方貼圖，抓取一般圖檔
PLURK_EMOJI_PATTERN = re.compile(r'https://images\.plurk\.com/mx_')
GENERAL_IMAGE_PATTERN = re.compile(r'https?://[^\s"\'\\]+\.(?:jpg|png|gif|jpeg)', re.IGNORECASE)

def get_all_valid_images(text_content):
    """擷取有效圖片連結，排除官方表情與系統圖"""
    if not text_content: return set()
    clean_text = text_content.replace('\\/', '/')
    all_urls = GENERAL_IMAGE_PATTERN.findall(clean_text)
    valid_urls = set()
    for url in all_urls:
        low_url = url.lower()
        if "emos.plurk.com" in low_url or "static.plurk.com" in low_url:
            continue
        if "images.plurk.com" in low_url and PLURK_EMOJI_PATTERN.search(url):
            continue
        valid_urls.add(url)
    return valid_urls

def write_exif_time(file_path, dt_obj):
    """
    聰明的 EXIF 檢查機制：
    只有在時間空白或與噗文時間不一致時，才執行覆寫（Overwrite）。
    """
    if not PIEXIF_AVAILABLE or file_path.suffix.lower() not in ['.jpg', '.jpeg']:
        return False
    try:
        target_time_str = dt_obj.strftime("%Y:%m:%d %H:%M:%S")
        exif_dict = piexif.load(str(file_path))
        
        # 取得拍攝日期欄位 (DateTimeOriginal)
        current_time = exif_dict.get("Exif", {}).get(piexif.ExifIFD.DateTimeOriginal)
        current_time_str = current_time.decode('utf-8') if isinstance(current_time, bytes) else current_time

        # 如果已經有一致的時間，就跳過不處理，節省時間
        if current_time_str == target_time_str:
            return False

        # 更新/覆寫標頭時間
        print(f"  🕒 正在更新 EXIF 時間標頭: {file_path.name}")
        exif_dict["0th"][piexif.ImageIFD.DateTime] = target_time_str
        exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = target_time_str
        exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = target_time_str
        #print(f"🕒 覆寫/校正 EXIF 標頭:",target_time_str,"/",str(file_path))
        
        piexif.insert(piexif.dump(exif_dict), str(file_path))
        return True
    except:
        # 若原檔格式特殊或無 EXIF 區塊，則強制新建
        try:
            exif_date = dt_obj.strftime("%Y:%m:%d %H:%M:%S")
            new_exif = {"0th": {piexif.ImageIFD.DateTime: exif_date}, 
                        "Exif": {piexif.ExifIFD.DateTimeOriginal: exif_date}}
            piexif.insert(piexif.dump(new_exif), str(file_path))
            return True
        except: return False

def download_image(url, target_folder, dt_obj, do_exif):
    """下載邏輯：支援靈活選擇是否處理 EXIF"""
    file_name = url.split('/')[-1].split('?')[0]
    save_path = target_folder / file_name
    target_folder.mkdir(exist_ok=True, parents=True)

    # 檢查檔案是否已存在，並根據 user 選擇決定是否更新 EXIF
    if save_path.exists():
        updated = write_exif_time(save_path, dt_obj) if do_exif else False
        return False, True, updated

    try:
        res = requests.get(url, timeout=15)
        if res.status_code == 200 and len(res.content) > 5120:
            with open(save_path, "wb") as f:
                f.write(res.content)
            updated = write_exif_time(save_path, dt_obj) if do_exif else False
            return True, False, updated
    except: pass
    return False, False, False

def parse_js_content(file_path):
    """物理切割法：精確處理 BackupData 格式，刪除等號前字串"""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            raw_text = f.read().strip()
            eq_index = raw_text.find('=')
            if eq_index == -1: return []
            json_part = raw_text[eq_index + 1:].strip()
            if json_part.endswith(';'): json_part = json_part[:-1].strip()
            return json.loads(json_part, strict=False)
    except: return []

def _process_folder(source_dir, label, do_exif):
    """掃描 JS 檔案並進行圖檔分類與處理"""
    counts = {"dl": 0, "skip": 0, "exif": 0}
    if not source_dir.exists():
        print(f"⚠️ 找不到 {label} 資料夾，略過處理。")
        return counts

    for js_file in source_dir.glob("*.js"):
        items = parse_js_content(js_file)
        if not items: continue
        
        print(f"📂 [{label}] 處理檔案中: {js_file.name}")
        for item in items:
            posted_date = item.get("posted", "")
            try:
                dt = datetime.strptime(posted_date, "%a, %d %b %Y %H:%M:%S GMT")
                # 維持按天分類 (YYYY-MM-DD)
                date_folder = OUTPUT_ROOT / dt.strftime("%Y-%m-%d")
            except: continue

            content = (item.get("content", "") or "") + " " + (item.get("content_raw", "") or "")
            urls = get_all_valid_images(content)
            
            for url in urls:
                is_dl, is_exist, is_exif = download_image(url, date_folder, dt, do_exif)
                if is_dl: counts["dl"] += 1
                if is_exist: counts["skip"] += 1
                if is_exif: counts["exif"] += 1
    return counts

def main():
    print("🚀 噗浪 JS 備份圖檔整理工具 (Flexible Version)")
    
    # EXIF 選擇邏輯
    do_exif = False
    if PIEXIF_AVAILABLE:
        # 提供聰明的選擇機制
        choice = input("👉 是否要檢查並補寫/覆蓋圖檔的 EXIF 時間標頭？(y/N): ").lower()
        if choice == 'y': do_exif = True
    else:
        print("💡 提示：系統未安裝 piexif 模組，將改為純下載模式。")

    OUTPUT_ROOT.mkdir(exist_ok=True)
    
    # 執行主噗與回應的處理
    p_stats = _process_folder(PLURKS_DIR, "主噗", do_exif)
    r_stats = _process_folder(RESPONSES_DIR, "回應", do_exif)

    print("\n" + "="*40)
    print("✨ 備份整理結果：")
    print(f"📥 新下載圖片: {p_stats['dl'] + r_stats['dl']} 張")
    print(f"⏭️ 略過已存在圖檔: {p_stats['skip'] + r_stats['skip']} 張")
    if do_exif:
        print(f"🕒 覆寫/校正 EXIF 標頭: {p_stats['exif'] + r_stats['exif']} 張")
    print("="*40)

if __name__ == "__main__":
    main()