import os
import re
import sys
import json
import time
import random
import shutil
from datetime import datetime
import pytz
from collections import OrderedDict
from urllib.parse import urlparse, urljoin
import cloudscraper

BASE_URL = os.getenv("BASE_URL")
DEFAULT_STREAM_DOMAIN = "chatgpt.hereisman.net"
OUTPUT_FILE = "Goozapp.json"

def get_ist_time():
    ist = pytz.timezone('Asia/Kolkata')
    return datetime.now(ist).strftime('%d/%m/%y %H:%M:%S IST')

def log_to_console(message):
    print(message, file=sys.stderr)

def deduplicate(seq):
    seen = set()
    return [x for x in seq if not (x in seen or seen.add(x))]

def push_to_github():
    GITHUB_TOKEN = os.getenv("GH_TOKEN")
    GITHUB_USER = os.getenv("TGITHUB_USER")
    GITHUB_REPO = os.getenv("TGITHUB_REPO")
    GITHUB_EMAIL = os.getenv("TGITHUB_EMAIL")
    
    if not GITHUB_TOKEN or not GITHUB_USER or not GITHUB_REPO:
        log_to_console("[ERROR] GitHub secrets are missing. Skipping push.")
        return

    temp_dir = "temp_external_repo"
    remote_url = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{GITHUB_REPO}.git"

    try:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            
        clone_status = os.system(f"git clone {remote_url} {temp_dir}")
        if clone_status != 0:
            raise Exception("Git Clone failed. Please check your token or repo permissions.")
        
        shutil.copy(OUTPUT_FILE, os.path.join(temp_dir, OUTPUT_FILE))
        
        current_dir = os.getcwd()
        os.chdir(temp_dir)
        
        os.system(f'git config user.email "{GITHUB_EMAIL if GITHUB_EMAIL else "action@github.com"}"')
        os.system(f'git config user.name "{GITHUB_USER}"')
        os.system(f"git add {OUTPUT_FILE}")
        os.system(f'git commit -m "Auto Update: {get_ist_time()}" || echo "No changes"')
        push_status = os.system("git push origin main")
        
        os.chdir(current_dir)
        shutil.rmtree(temp_dir)
        
        if push_status == 0:
            log_to_console(f"[SUCCESS] {OUTPUT_FILE} successfully updated in {GITHUB_USER}/{GITHUB_REPO}.")
        else:
            log_to_console("[ERROR] Git push command failed.")
            
    except Exception as e:
        log_to_console(f"[ERROR] Push failed: {e}")

def run_scraper():
    if not BASE_URL:
        error_package = OrderedDict([
            ("Owner", "Ivan-FluX"),
            ("App name", "Goozapp-auto-scraper"),
            ("Status", "Failed"),
            ("Error", "BASE_URL environment variable is missing. Please add BASE_URL to GitHub Secrets.")
        ])
        print(json.dumps(error_package, indent=4))
        return

    scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'android', 'desktop': False})
    raw_matches = []
    active_stream_domain = ""
    
    clean_base = BASE_URL.rstrip('/')
    if clean_base.endswith('/index1'):
        clean_base = clean_base[:-7]

    target_url = clean_base
    log_to_console(f"[*] Loading homepage: {target_url}")
    try:
        res = scraper.get(target_url, timeout=15)
        homepage_html = res.text
        
        if 'list-group-item' not in homepage_html and not clean_base.endswith('/v8'):
            target_url = f"{clean_base}/v8"
            log_to_console(f"[*] Retrying with fallback endpoint: {target_url}")
            res = scraper.get(target_url, timeout=15)
            homepage_html = res.text
            
        log_to_console("[+] Homepage loaded successfully.")
    except Exception as e:
        error_package = OrderedDict([
            ("Owner", "Ivan-FluX"),
            ("App name", "Goozapp-auto-scraper"),
            ("Status", "Failed"),
            ("Error", "Could not connect to the website. Possibly blocked by Cloudflare or network timeout."),
            ("Details", str(e))
        ])
        print(json.dumps(error_package, indent=4))
        return

    matches = re.findall(r'<a class="list-group-item"[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', homepage_html, re.S)
    log_to_console(f"[+] Total {len(matches)} raw matches found.")
    
    for m_url, m_text in matches:
        if re.search(r'\b(ended|finished|final)\b', m_text, re.I):
            continue
            
        cat_match = re.search(r'<strong>(.*?)</strong>', m_text, re.I)
        if cat_match:
            cat_name = cat_match.group(1).strip()
        else:
            cat_url_match = re.search(r'/tv-live/([^/]+)/', m_url)
            cat_name = cat_url_match.group(1).upper() if cat_url_match else "Live Sports"

        temp_text = re.sub(r'<span[^>]*>\s*<img[^>]*>.*?</span>', '', m_text, flags=re.S | re.I)
        temp_text = re.sub(r'<strong[^>]*>.*?</strong>', '', temp_text, flags=re.S | re.I)
        temp_text = re.sub(r'<span[^>]*class=["\'][^"\']*time-badge[^"\']*["\'][^>]*>.*?</span>', '', temp_text, flags=re.S | re.I)
        temp_text = re.sub(r'<span[^>]*class=["\'][^"\']*hd-text[^"\']*["\'][^>]*>.*?</span>', '', temp_text, flags=re.S | re.I)
        
        clean_rivals = re.sub(r'<[^>]+>', '', temp_text)
        clean_rivals = re.sub(r'\s+', ' ', clean_rivals).strip()
        clean_rivals = clean_rivals.rstrip(':').strip()
        
        full_m_url = m_url if m_url.startswith("http") else urljoin(clean_base, m_url)
        
        match_id_search = re.search(r'/(\d+)/?$', full_m_url)
        match_id = match_id_search.group(1) if match_id_search else ""
        
        raw_matches.append({
            "cat_name": cat_name,
            "clean_rivals": clean_rivals,
            "full_m_url": full_m_url,
            "backup_id": match_id,
            "extracted_ids": []
        })

    log_to_console(f"\n[*] Scanning {len(raw_matches)} matches for server IDs...")
    for item in raw_matches:
        log_to_console(f"  [-] Fetching page: {item['clean_rivals']}...")
        try:
            time.sleep(random.uniform(0.8, 1.5))
            m_res = scraper.get(item["full_m_url"], timeout=10)
            m_html = m_res.text
            
            if re.search(r'\bFinal at\b', m_html, re.I):
                log_to_console("    [!] Match is already final. Skipping stream extraction.")
                continue

            stream_ids = []
            stream_ids.extend(re.findall(r'changeStream\s*\(\s*[\'"]?([a-zA-Z0-9_-]+)[\'"]?\s*\)', m_html))
            stream_ids.extend(re.findall(r'stream-btn-([a-zA-Z0-9_-]+)', m_html))
            stream_ids.extend(re.findall(r'new-stream-embed/([a-zA-Z0-9_-]+)', m_html))
            stream_ids.extend(re.findall(r'embed/([a-zA-Z0-9_-]+)', m_html))
            
            filtered_ids = [
                s for s in stream_ids 
                if s.lower() not in ('streamid', 'null', 'undefined', 'cx-iframe')
            ]
            
            if filtered_ids:
                item["extracted_ids"] = deduplicate(filtered_ids)
                log_to_console(f"    [+] Extracted IDs: {item['extracted_ids']}")
            else:
                log_to_console("    [!] No stream buttons found in HTML.")

            if not active_stream_domain:
                candidate_embed_urls = []
                candidate_embed_urls.extend(re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', m_html, re.I))
                candidate_embed_urls.extend(re.findall(r'[\'"](https?://[a-zA-Z0-9.-]+/new-stream-embed/[^\'"]+)[\'"]', m_html))
                
                candidate_embed_base = re.search(r'[\'"](https?://[a-zA-Z0-9.-]+/new-stream-embed/)[\'"]', m_html)
                if candidate_embed_base:
                    test_id = item["extracted_ids"][0] if item["extracted_ids"] else item["backup_id"]
                    if test_id:
                        candidate_embed_urls.append(f"{candidate_embed_base.group(1)}{test_id}")
                
                for embed_url in deduplicate(candidate_embed_urls):
                    if not embed_url.startswith('http'):
                        if embed_url.startswith('//'):
                            embed_url = 'https:' + embed_url
                        else:
                            embed_url = urljoin(item["full_m_url"], embed_url)
                    
                    try:
                        embed_res = scraper.get(embed_url, timeout=10)
                        playlist_match = re.search(r'(https?://[a-zA-Z0-9.-]+/playlist/[a-zA-Z0-9_.-]+/load-playlist[^"\'\s>]*)', embed_res.text)
                        if playlist_match:
                            parsed_url = urlparse(playlist_match.group(1))
                            active_stream_domain = parsed_url.netloc
                            log_to_console(f"    [*] Active domain detected: {active_stream_domain}")
                            break
                    except Exception:
                        continue
        except Exception as e:
            log_to_console(f"    [ERROR] Failed to fetch or parse: {e}")
            continue

    if not active_stream_domain:
        active_stream_domain = DEFAULT_STREAM_DOMAIN
        log_to_console(f"\n[!] Using default domain: {active_stream_domain}")

    all_live_matches = []
    
    for item in raw_matches:
        ids_to_use = item["extracted_ids"]
        
        if not ids_to_use and item["backup_id"]:
            ids_to_use = [item["backup_id"]]
            
        if ids_to_use:
            for index, stream_id in enumerate(ids_to_use, 1):
                raw_link = f"https://{active_stream_domain}/playlist/{stream_id}/load-playlist"
                clean_link = raw_link.split('?')[0].rstrip('/')
                final_link = f"{clean_link}.m3u8|Referer=https://gooz.aapmains.net"
                
                all_live_matches.append(OrderedDict([
                    ("Id", str(len(all_live_matches) + 1)),
                    ("Rivels", item["clean_rivals"]),
                    ("Title", f"{item['cat_name']} (S-{index})"),
                    ("Link", final_link)
                ]))

    final_package = OrderedDict([
        ("Owner", "Ivan-FluX"),
        ("App name", "Goozapp-auto-scraper"),
        ("Last update", get_ist_time()),
        ("Total_Matches", len(all_live_matches)),
        ("Live_Data", all_live_matches)
    ])
    
    with open(OUTPUT_FILE, "w") as f:
        json.dump(final_package, f, indent=4)
        
    push_to_github()
    print(json.dumps(final_package, indent=4))

if __name__ == "__main__":
    run_scraper()
