import re
import requests
import logging
import json
import time
from collections import OrderedDict
from datetime import datetime
import config
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[logging.FileHandler("function.log", "w", encoding="utf-8"), logging.StreamHandler()])

# 加载配置文件
def load_config():
    """加载配置文件"""
    try:
        with open(r"myconfig.json", encoding='utf-8') as json_file:
            parms = json.load(json_file)
            if 'ctype' not in parms:
                parms['ctype'] = 0x01
            if 'checkfile_list' not in parms:
                parms['checkfile_list'] = []
            if 'keywords' not in parms:
                parms['keywords'] = []
            if 'otype' not in parms:
                parms['otype'] = 0x01 | 0x02 | 0x10
            if 'sendfile_list' not in parms:
                parms['sendfile_list'] = []
            if 'newDb' not in parms:
                parms['newDb'] = False
            if 'webhook' not in parms:
                parms['webhook'] = ''
            if 'secret' not in parms:
                parms['secret'] = ''
            if 'max_check_count' not in parms:
                parms['max_check_count'] = 2000
    except Exception as e:
        logging.error(f"未发现myconfig.json配置文件，或配置文件格式有误。错误：{e}")
        return {}
    return parms

# 设置请求的重试机制
def is_url_valid(url, retries=3, backoff_factor=1):
    """检查URL是否有效，增加重试机制"""
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[500, 502, 503, 504],
        method_whitelist=["HEAD", "GET", "POST"]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    try:
        response = session.head(url, timeout=10)
        if response.status_code == 200:
            return True
        else:
            logging.warning(f"无效链接: {url} (状态码: {response.status_code})")
            return False
    except requests.RequestException as e:
        logging.warning(f"请求失败: {url} - 错误: {e}")
        return False

# 解析模板文件
def parse_template(template_file):
    template_channels = OrderedDict()
    current_category = None

    with open(template_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                if "#genre#" in line:
                    current_category = line.split(",")[0].strip()
                    template_channels[current_category] = []
                elif current_category:
                    channel_name = line.split(",")[0].strip()
                    template_channels[current_category].append(channel_name)

    return template_channels

# 获取频道列表
def fetch_channels(url):
    channels = OrderedDict()

    try:
        response = requests.get(url)
        response.raise_for_status()
        response.encoding = 'utf-8'
        lines = response.text.split("\n")
        current_category = None
        is_m3u = any("#EXTINF" in line for line in lines[:15])
        source_type = "m3u" if is_m3u else "txt"
        logging.info(f"url: {url} 获取成功，判断为{source_type}格式")

        if is_m3u:
            for line in lines:
                line = line.strip()
                if line.startswith("#EXTINF"):
                    match = re.search(r'group-title="(.*?)",(.*)', line)
                    if match:
                        current_category = match.group(1).strip()
                        channel_name = match.group(2).strip()
                        if current_category not in channels:
                            channels[current_category] = []
                elif line and not line.startswith("#"):
                    channel_url = line.strip()
                    if current_category and channel_name and is_url_valid(channel_url):  # 检查URL是否有效
                        channels[current_category].append((channel_name, channel_url))
        else:
            for line in lines:
                line = line.strip()
                if "#genre#" in line:
                    current_category = line.split(",")[0].strip()
                    channels[current_category] = []
                elif current_category:
                    match = re.match(r"^(.*?),(.*?)$", line)
                    if match:
                        channel_name = match.group(1).strip()
                        channel_url = match.group(2).strip()
                        if is_url_valid(channel_url):  # 检查URL是否有效
                            channels[current_category].append((channel_name, channel_url))
                    elif line:
                        if is_url_valid(line):  # 检查URL是否有效
                            channels[current_category].append((line, ''))
        if channels:
            categories = ", ".join(channels.keys())
            logging.info(f"url: {url} 爬取成功✅，包含频道分类: {categories}")
    except requests.RequestException as e:
        logging.error(f"url: {url} 爬取失败❌, Error: {e}")

    return channels

# 匹配模板中的频道
def match_channels(template_channels, all_channels):
    matched_channels = OrderedDict()

    for category, channel_list in template_channels.items():
        matched_channels[category] = OrderedDict()
        for channel_name in channel_list:
            for online_category, online_channel_list in all_channels.items():
                for online_channel_name, online_channel_url in online_channel_list:
                    if channel_name == online_channel_name:
                        matched_channels[category].setdefault(channel_name, []).append(online_channel_url)

    return matched_channels

# 过滤源URL并获取匹配的频道
def filter_source_urls(template_file):
    template_channels = parse_template(template_file)
    source_urls = config.source_urls

    all_channels = OrderedDict()
    for url in source_urls:
        fetched_channels = fetch_channels(url)
        for category, channel_list in fetched_channels.items():
            if category in all_channels:
                all_channels[category].extend(channel_list)
            else:
                all_channels[category] = channel_list

    matched_channels = match_channels(template_channels, all_channels)

    return matched_channels, template_channels

# 检查是否为IPv6
def is_ipv6(url):
    return re.match(r'^http:\/\/\[[0-9a-fA-F:]+\]', url) is not None

# 读取已存在的URL
def read_existing_urls(file_path):
    """读取文件中的所有URL"""
    urls = set()
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):  # 跳过注释
                    urls.add(line)
    except FileNotFoundError:
        logging.warning(f"文件未找到: {file_path}")
    return urls

# 移除文件中无效的URLs
def remove_invalid_urls(file_path, valid_urls):
    """移除文件中无效的URLs"""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        with open(file_path, "w", encoding="utf-8") as f:
            for line in lines:
                line = line.strip()
                if line and not line.startswith("#"):
                    if line in valid_urls:
                        f.write(line + "\n")  # 仅写入有效的URL
                else:
                    f.write(line + "\n")  # 保留注释行
    except Exception as e:
        logging.error(f"移除无效URL失败: {e}")

# 更新频道列表
def updateChannelUrlsM3U(channels, template_channels):
    written_urls = set()

    # 读取现有的文件中的URL并验证它们是否有效
    existing_urls_m3u = read_existing_urls("live.m3u")
    existing_urls_txt = read_existing_urls("live.txt")
    valid_urls_m3u = set(url for url in existing_urls_m3u if is_url_valid(url))
    valid_urls_txt = set(url for url in existing_urls_txt if is_url_valid(url))

    # 移除文件中无效的URL
    remove_invalid_urls("live.m3u", valid_urls_m3u)
    remove_invalid_urls("live.txt", valid_urls_txt)

    current_date = datetime.now().strftime("%Y-%m-%d")
    for group in config.announcements:
        for announcement in group['entries']:
            if announcement['name'] is None:
                announcement['name'] = current_date

    with open("live.m3u", "w", encoding="utf-8") as f_m3u:
        f_m3u.write(f"""#EXTM3U x-tvg-url={",".join(f'"{epg_url}"' for epg_url in config.epg_urls)}\n""")

        with open("live.txt", "w", encoding="utf-8") as f_txt:
            for group in config.announcements:
                f_txt.write(f"{group['channel']},#genre#\n")
                for announcement in group['entries']:
                    f_m3u.write(f"""#EXTINF:-1 tvg-id="1" tvg-name="{announcement['name']}" tvg-logo="{announcement['logo']}" group-title="{group['channel']}",{announcement['name']}\n""")
                    f_m3u.write(f"{announcement['url']}\n")
                    f_txt.write(f"{announcement['name']},{announcement['url']}\n")

            for category, channel_list in template_channels.items():
                f_txt.write(f"{category},#genre#\n")
                if category in channels:
                    for channel_name in channel_list:
                        if channel_name in channels[category]:
                            sorted_urls = sorted(channels[category][channel_name], key=lambda url: not is_ipv6(url) if config.ip_version_priority == "ipv6" else is_ipv6(url))
                            filtered_urls = []
                            for url in sorted_urls:
                                if url and url not in written_urls and not any(blacklist in url for blacklist in config.url_blacklist):
                                    # 检查URL是否有效
                                    if is_url_valid(url):
                                        filtered_urls.append(url)
                                        written_urls.add(url)

                            total_urls = len(filtered_urls)
                            for index, url in enumerate(filtered_urls, start=1):
                                if is_ipv6(url):
                                    url_suffix = f"$LR•IPV6" if total_urls == 1 else f"$LR•IPV6『线路{index}』"
                                else:
                                    url_suffix = f"$LR•IPV4" if total_urls == 1 else f"$LR•IPV4『线路{index}』"
                                if '$' in url:
                                    base_url = url.split('$', 1)[0]
                                else:
                                    base_url = url

                                new_url = f"{base_url}{url_suffix}"

                                f_m3u.write(f"#EXTINF:-1 tvg-id=\"{index}\" tvg-name=\"{channel_name}\" tvg-logo=\"https://gcore.jsdelivr.net/gh/yuanzl77/TVlogo@master/png/{channel_name}.png\" group-title=\"{category}\",{channel_name}\n")
                                f_m3u.write(new_url + "\n")
                                f_txt.write(f"{channel_name},{new_url}\n")

            f_txt.write("\n")

if __name__ == "__main__":
    template_file = "demo.txt"
    channels, template_channels = filter_source_urls(template_file)
    updateChannelUrlsM3U(channels, template_channels)
