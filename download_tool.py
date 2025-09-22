import os
import re
import requests
from datetime import datetime
import concurrent.futures


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
        content = f.read()

    # 修改内容：为所有.ts链接添加前置链接
    pattern = r'^(.*?\.ts\?.*?)$'
    modified_content = re.sub(pattern, prefix_url + r'\1', content, flags=re.MULTILINE)

    # 保存修改后的文件
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(modified_content)

    return output_path, modified_content


def download_ts_segment(url, segment_name, download_dir):
    """下载单个TS片段"""
    try:
        response = requests.get(url, timeout=30)
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
    """下载m3u8文件中的所有视频片段"""
    # 创建下载目录（如果不存在）
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)

    # 提取所有.ts链接
    ts_pattern = r'^https?://.*?\.ts\?.*?$'
    ts_urls = re.findall(ts_pattern, m3u8_content, re.MULTILINE)

    if not ts_urls:
        print("未找到TS片段链接")
        return False

    print(f"找到 {len(ts_urls)} 个TS片段，开始下载...")

    # 使用线程池并发下载
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = []
        for i, url in enumerate(ts_urls):
            segment_name = f"segment_{i:04d}.ts"
            futures.append(executor.submit(download_ts_segment, url, segment_name, download_dir))

        # 等待所有下载完成
        results = [future.result() for future in concurrent.futures.as_completed(futures)]

    success_count = sum(results)
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