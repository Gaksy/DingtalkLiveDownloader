import os
import re
import requests
from datetime import datetime
import concurrent.futures
import threading


def list_m3u8_files():
    """列出downloaded_m3u8目录下的所有文件，并按日期排序"""
    directory = "downloaded_m3u8"

    if not os.path.exists(directory):
        print(f"目录 {directory} 不存在")
        return []

    # 获取文件列表及其修改时间
    files = []
    for filename in os.listdir(directory):
        if filename.endswith(".m3u8"):
            filepath = os.path.join(directory, filename)
            mod_time = os.path.getmtime(filepath)
            mod_date = datetime.fromtimestamp(mod_time).strftime('%Y-%m-%d %H:%M:%S')
            files.append((filename, mod_date, mod_time))

    # 按修改时间排序（新的在前）
    files.sort(key=lambda x: x[2], reverse=True)

    return files


def display_files(files):
    """显示文件列表"""
    print("序号\t[文件名]\t\t[日期]")
    print("-" * 50)
    for i, (filename, mod_date, _) in enumerate(files, 1):
        print(f"{i}\t{filename}\t{mod_date}")


def process_m3u8_file(selected_file, prefix_url):
    """处理选中的m3u8文件，修改内容并保存到temp目录"""
    input_path = os.path.join("downloaded_m3u8", selected_file)

    # 创建temp目录（如果不存在）
    temp_dir = "temp"
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir)

    output_path = os.path.join(temp_dir, selected_file)

    with open(input_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    modified_lines = []
    for line in lines:
        line_strip = line.strip()
        if line_strip.endswith(".ts") or (".ts?" in line_strip):
            # 保留原有的.ts链接及其query参数，加上前缀
            modified_lines.append(prefix_url + line_strip + "\n")
        else:
            modified_lines.append(line)

    modified_content = "".join(modified_lines)

    # 保存修改后的文件
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(modified_content)

    return output_path, modified_content


def download_ts_segment(url, segment_name, download_dir):
    """下载单个TS片段，使用伪造请求头，保留URL的query参数"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
                      " Chrome/114.0.0.0 Safari/537.36",
        "Referer": url,
        "Accept": "*/*",
        "Connection": "keep-alive",
    }
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            filepath = os.path.join(download_dir, segment_name)
            with open(filepath, 'wb') as f:
                f.write(response.content)
            return True
        else:
            print(f"下载失败: {url}, 状态码: {response.status_code}")
            return False
    except Exception as e:
        print(f"下载出错: {url}, 错误: {str(e)}")
        return False


def download_all_segments(m3u8_content, download_dir, base_url="https://dtliving-sz.dingtalk.com/live/"):
    """多线程下载m3u8文件中的所有视频片段，伪造请求头，保留query参数"""
    # 创建下载目录（如果不存在）
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)

    # 提取所有.ts链接（完整URL，包含query参数）
    lines = m3u8_content.splitlines()
    ts_urls = []
    for line in lines:
        line_strip = line.strip()
        if line_strip.startswith("http") and (".ts" in line_strip):
            ts_urls.append(line_strip)

    if not ts_urls:
        print("未找到TS片段链接")
        return False

    # 询问用户并行下载线程数，默认为5
    try:
        thread_count_input = input("请输入并行下载线程数（默认5）: ").strip()
        thread_count = int(thread_count_input) if thread_count_input else 5
        if thread_count < 1:
            print("线程数必须至少为1，使用默认值5")
            thread_count = 5
    except ValueError:
        print("输入无效，使用默认线程数5")
        thread_count = 5

    print(f"找到 {len(ts_urls)} 个TS片段，开始下载，使用 {thread_count} 线程...")

    success_count = 0
    total = len(ts_urls)
    success_lock = threading.Lock()

    def download_task(index_url):
        i, url = index_url
        segment_name = f"segment_{i:04d}.ts"
        result = download_ts_segment(url, segment_name, download_dir)
        nonlocal success_count
        if result:
            with success_lock:
                success_count += 1
        with success_lock:
            print(f"下载第 {i+1}/{total} 个片段 ({((i+1)/total)*100:.1f}%)，已成功下载 {success_count} 个")
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as executor:
        list(executor.map(download_task, enumerate(ts_urls)))

    print(f"下载完成: {success_count}/{len(ts_urls)} 个片段成功下载")

    return success_count == len(ts_urls)


def main():
    # 列出所有m3u8文件
    files = list_m3u8_files()

    if not files:
        print("没有找到m3u8文件")
        return

    # 显示文件列表
    display_files(files)

    # 用户选择
    try:
        choice = int(input("\n请选择要下载的文件序号: "))
        if choice < 1 or choice > len(files):
            print("无效的选择")
            return

        selected_file, mod_date, _ = files[choice - 1]
        print(f"已选择: {selected_file}")

        # 询问是否只需要处理m3u8文件而不下载
        download_choice = input("是否需要下载视频片段? (y/n, 默认y): ").strip().lower()
        download_segments = download_choice != 'n'

        # 处理m3u8文件
        prefix_url = "https://dtliving-sz.dingtalk.com/live/"
        output_path, modified_content = process_m3u8_file(selected_file, prefix_url)
        print(f"文件已处理并保存到: {output_path}")

        if download_segments:
            # 下载所有视频片段
            download_dir = os.path.join("temp", selected_file.replace(".m3u8", "_segments"))
            download_all_segments(modified_content, download_dir, prefix_url)
        else:
            print("已跳过下载视频片段，只输出处理好的m3u8文件")

    except ValueError:
        print("请输入有效的数字")
    except Exception as e:
        print(f"发生错误: {str(e)}")


if __name__ == "__main__":
    main()