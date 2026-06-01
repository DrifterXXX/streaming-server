#!/bin/bash
# 下载《低智商犯罪》剧集
# 使用方法: ./download_low_iq_crime.sh [集数范围, 默认1-24]

set -e

VIDEO_DIR="$HOME/streaming-server/videos"
mkdir -p "$VIDEO_DIR"

START=${1:-1}
END=${2:-24}

echo "=========================================="
echo "  📺 低智商犯罪 下载脚本"
echo "=========================================="
echo ""
echo "注意：由于网络限制，可能需要使用代理或备用源"
echo ""

# 尝试多个下载源
download_from_source() {
    local url="$1"
    local filename="$2"
    
    echo "尝试下载: $filename"
    if curl -L --connect-timeout 30 --max-time 600 -o "$VIDEO_DIR/$filename" "$url" 2>&1 | tail -3; then
        if [ -f "$VIDEO_DIR/$filename" ] && [ $(stat -f%z "$VIDEO_DIR/$filename" 2>/dev/null || stat -c%s "$VIDEO_DIR/$filename") -gt 1000000 ]; then
            echo "✅ 下载成功: $filename"
            return 0
        fi
    fi
    return 1
}

echo "视频目录: $VIDEO_DIR"
echo "下载范围: 第${START}集 - 第${END}集"
echo ""

# 如果已经有视频，跳过
for i in $(seq $START $END); do
    ep=$(printf "%02d" $i)
    if [ -f "$VIDEO_DIR/低智商犯罪.ep${ep}.mp4" ]; then
        echo "⏭️  已存在: 低智商犯罪.ep${ep}.mp4"
    else
        echo "⏳ 待下载: 低智商犯罪.ep${ep}.mp4"
    fi
done

echo ""
echo "=========================================="
echo "  📥 下载选项"
echo "=========================================="
echo ""
echo "由于直接下载源不稳定，推荐以下方法："
echo ""
echo "1️⃣  使用迅雷/NDM下载磁力链接"
echo "    - 搜索 '低智商犯罪 磁力' 获取资源"
echo "    - 下载后重命名放入: $VIDEO_DIR"
echo ""
echo "2️⃣  使用网盘下载"
echo "    - 百度网盘/阿里云盘搜索剧集资源"
echo "    - 下载后上传到 videos 目录"
echo ""
echo "3️⃣  手动下载（如果有可用链接）"
echo "    cd $VIDEO_DIR"
echo "    curl -L -o '低智商犯罪.ep01.mp4' '你的下载链接'"
echo ""
echo "=========================================="
echo "  📁 文件命名规范"
echo "=========================================="
echo "建议命名格式:"
echo "  低智商犯罪.ep01.mp4"
echo "  低智商犯罪.ep02.mp4"
echo "  ..."
echo "  低智商犯罪.ep24.mp4"
echo ""
echo "下载完成后访问: https://tv.drifter.indevs.in"
echo "=========================================="
