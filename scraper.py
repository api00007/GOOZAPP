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

# Settings fetched from environment variables
BASE_URL = os.getenv("BASE_URL")
DEFAULT_STREAM_DOMAIN = "chatgpt.hereisman.net"
OUTPUT_FILE = "Goozapp.json"

def get_ist_time():
    ist = pytz.timezone('Asia/Kolkata')
    return datetime.now(ist).strftime('%d/%m/%y %H:%M:%S IST')

def log_to_console(message):
    """Prints logs to sys.stderr so they appear in GitHub Actions but do not pollute the raw JSON output."""
    print(message, file=sys.stderr)

def deduplicate(seq):
    """Helper function to remove duplicates while preserving order."""
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
    # Verify if BASE_URL secret is provided
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
    
    log_to_console(f"[*] Loading homepage: {BASE_URL}/index1")
    try:
        res = scraper.get(f"{BASE_URL}/index1", timeout=15)
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

    # Extract sports categories and matches blocks
    blocks = re.findall(r'<div class="col-lg-12">\s*<h4>(.*?)</h4>.*?<ol[^>]*>(.*?)</ol>', homepage_html, re.S)
    log_to_console(f"[+] Total {len(blocks)} categories found.")
    
    for cat_name, block_html in blocks:
        cat_name = cat_name.strip()
        matches = re.findall(r'<a class="list-group-item"[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', block_html, re.S)
        
        for m_url, m_text in matches:
            # Skip matches that are already ended or finished
            if re.search(r'\b(ended|finished)\b', m_text, re.I):
                continue
                
            # Clean match name
            clean_rivals = re.sub(r'<[^>]+>', '', m_text)
            clean_rivals = re.sub(r'\s+', ' ', clean_rivals).strip()
            
            # Remove any time badges if present in the text
            if ":" in clean_rivals:
                clean_rivals = clean_rivals.split(":")[0].strip()
                
            full_m_url = m_url if m_url.startswith("http") else f"{BASE_URL}/{m_url.lstrip('/')}"
            
            # Extract numeric match ID from URL as a backup
            match_id_search = re.search(r'/(\d+)/?$', full_m_url)
            match_id = match_id_search.group(1) if match_id_search else ""
            
            raw_matches.append({
                "cat_name": cat_name,
                "clean_rivals": clean_rivals,
                "full_m_url": full_m_url,
                "backup_id": match_id,
                "extracted_ids": []
            })

    # Pass 1: Scan active matches silently to find the streaming domain and stream IDs
    log_to_console(f"\n[*] Scanning {len(raw_matches)} matches for server IDs...")
    for item in raw_matches:
        log_to_console(f"  [-] Fetching page: {item['clean_rivals']}...")
        try:
            # Safe delay to prevent getting blocked by Cloudflare
            time.sleep(random.uniform(0.8, 1.5))
            m_res = scraper.get(item["full_m_url"], timeout=10)
            m_html = m_res.text
            
            # Extract stream IDs from different potential patterns
            stream_ids = []
            
            # Pattern 1: changeStream(ID)
            stream_ids.extend(re.findall(r'changeStream\s*\(\s*(\d+)\s*\)', m_html))
            
            # Pattern 2: stream-btn-ID
            stream_ids.extend(re.findall(r'stream-btn-(\d+)', m_html))
            
            # Pattern 3: new-stream-embed/ID
            stream_ids.extend(re.findall(r'new-stream-embed/(\d+)', m_html))
            
            # Pattern 4: any generic embed path
            stream_ids.extend(re.findall(r'embed/(\d+)', m_html))
            
            if stream_ids:
                item["extracted_ids"] = deduplicate(stream_ids)
                log_to_console(f"    [+] Extracted IDs: {item['extracted_ids']}")
            else:
                log_to_console("    [!] No stream IDs found in page HTML.")
            
            # Extract active stream domain from the iframe source
            if not active_stream_domain:
                iframe_matches = re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', m_html, re.I)
                for iframe_url in deduplicate(iframe_matches):
                    if not iframe_url.startswith('http'):
                        if iframe_url.startswith('//'):
                            iframe_url = 'https:' + iframe_url
                        else:
                            iframe_url = urljoin(item["full_m_url"], iframe_url)
                    
                    try:
                        iframe_res = scraper.get(iframe_url, timeout=10)
                        playlist_match = re.search(r'(https?://[a-zA-Z0-9.-]+/playlist/[a-zA-Z0-9_.-]+/load-playlist[^"\'\s>]*)', iframe_res.text)
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

    # Pass 2: Generate final server-categorized links with Referer formatting
    all_live_matches = []
    
    for item in raw_matches:
        ids_to_use = item["extracted_ids"]
        
        if not ids_to_use and item["backup_id"]:
            ids_to_use = [item["backup_id"]]
            
        if ids_to_use:
            for index, stream_id in enumerate(ids_to_use, 1):
                # Construct raw base link
                raw_link = f"https://{active_stream_domain}/playlist/{stream_id}/load-playlist"
                
                # Remove trailing question mark and parameters, then strip trailing slashes if any
                clean_link = raw_link.split('?')[0].rstrip('/')
                
                # Format final link with .m3u8 and the Referer pipe
                final_link = f"{clean_link}.m3u8|Referer=https://gooz.aapmains.net"
                
                all_live_matches.append(OrderedDict([
                    ("Id", str(len(all_live_matches) + 1)),
                    ("Rivels", item["clean_rivals"]),
                    ("Title", f"{item['cat_name']} (S-{index})"),
                    ("Link", final_link)
                ]))

    # Structure final JSON package
    final_package = OrderedDict([
        ("Owner", "Ivan-FluX"),
        ("App name", "Goozapp-auto-scraper"),
        ("Last update", get_ist_time()),
        ("Total_Matches", len(all_live_matches)),
        ("Live_Data", all_live_matches)
    ])
    
    # Save output inside the Action runner locally before pushing
    with open(OUTPUT_FILE, "w") as f:
        json.dump(final_package, f, indent=4)
        
    # Push to target repository
    push_to_github()
    
    # Print raw formatted JSON output to standard output only
    print(json.dumps(final_package, indent=4))

if __name__ == "__main__":
    run_scraper()
