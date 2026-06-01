#!/bin/bash
# 低智商犯罪 全集下载脚本
# 来源: https://www.huaren.la/

VIDEO_DIR="$HOME/streaming-server/videos"
mkdir -p "$VIDEO_DIR"

cd "$VIDEO_DIR"

# 所有24集下载链接
declare -A URLs
URLS[1]="https://lzdown27.com/20260504/5280_c72609ab/低智商犯罪第1集.mp4"
URLS[2]="https://lzdown27.com/20260504/5281_46ccf81d/低智商犯罪第2集.mp4"
URLS[3]="https://lzdown27.com/20260504/5282_6e92da0d/低智商犯罪第3集.mp4"
URLS[4]="https://lzdown27.com/20260504/5278_6f450d31/低智商犯罪第4集.mp4"
URLS[5]="https://lzdown27.com/20260504/5279_6f2870ca/低智商犯罪第5集.mp4"
URLS[6]="https://lzdown27.com/20260505/5311_701782fc/低智商犯罪第6集.mp4"
URLS[7]="https://lzdown27.com/20260505/5312_ca381581/低智商犯罪第7集.mp4"
URLS[8]="https://lzdown27.com/20260506/5334_c0683d89/低智商犯罪第8集.mp4"
URLS[9]="https://lzdown27.com/20260506/5335_f3bbab36/低智商犯罪第9集.mp4"
URLS[10]="https://lzdown27.com/20260507/5368_abb95ca3/低智商犯罪第10集.mp4"
URLS[11]="https://lzdown27.com/20260507/5367_79225c02/低智商犯罪第11集.mp4"
URLS[12]="https://lzdown27.com/20260508/5425_259d3aae/低智商犯罪第12集.mp4"
URLS[13]="https://lzdown27.com/20260508/5424_c844fe43/低智商犯罪第13集.mp4"
URLS[14]="https://lzdown27.com/20260509/5479_a4b55e08/低智商犯罪第14集.mp4"
URLS[15]="https://lzdown27.com/20260509/5478_73fb4cfd/低智商犯罪第15集.mp4"
URLS[16]="https://lzdown27.com/20260510/5512_e05ac7fa/低智商犯罪16.mp4"
URLS[17]="https://lzdown27.com/20260510/5513_cedaab11/低智商犯罪17.mp4"
URLS[18]="https://lzdown27.com/20260511/5651_fa81a965/低智商犯罪_18.mp4"
URLS[19]="https://lzdown27.com/20260511/5652_e82f3bdc/低智商犯罪_19.mp4"
URLS[20]="https://lzdown27.com/20260512/5686_f2787ee0/低智商犯罪_20.mp4"
URLS[21]="https://lzdown27.com/20260513/5731_c5066319/低智商犯罪_21.mp4"
URLS[22]="https://lzdown27.com/20260513/5730_ade011c4/低智商犯罪_22.mp4"
URLS[23]="https://lzdown27.com/20260513/5729_f14beb9f/低智商犯罪_23.mp4"
URLS[24]="https://lzdown27.com/20260513/5728_39091d89/低智商犯罪_24.mp4"

# 输出文件名映射
declare -A OUTPUTS
OUTPUTS[1]="低智商犯罪.ep01.mp4"
OUTPUTS[2]="低智商犯罪.ep02.mp4"
OUTPUTS[3]="低智商犯罪.ep03.mp4"
OUTPUTS[4]="低智商犯罪.ep04.mp4"
OUTPUTS[5]="低智商犯罪.ep05.mp4"
OUTPUTS[6]="低智商犯罪.ep06.mp4"
OUTPUTS[7]="低智商犯罪.ep07.mp4"
OUTPUTS[8]="低智商犯罪.ep08.mp4"
OUTPUTS[9]="低智商犯罪.ep09.mp4"
OUTPUTS[10]="低智商犯罪.ep10.mp4"
OUTPUTS[11]="低智商犯罪.ep11.mp4"
OUTPUTS[12]="低智商犯罪.ep12.mp4"
OUTPUTS[13]="低智商犯罪.ep13.mp4"
OUTPUTS[14]="低智商犯罪.ep14.mp4"
OUTPUTS[15]="低智商犯罪.ep15.mp4"
OUTPUTS[16]="低智商犯罪.ep16.mp4"
OUTPUTS[17]="低智商犯罪.ep17.mp4"
OUTPUTS[18]="低智商犯罪.ep18.mp4"
OUTPUTS[19]="低智商犯罪.ep19.mp4"
OUTPUTS[20]="低智商犯罪.ep20.mp4"
OUTPUTS[21]="低智商犯罪.ep21.mp4"
OUTPUTS[22]="低智商犯罪.ep22.mp4"
OUTPUTS[23]="低智商犯罪.ep23.mp4"
OUTPUTS[24]="低智商犯罪.ep24.mp4"

echo "=========================================="
echo "  📺 低智商犯罪 全集下载"
echo "=========================================="
echo "视频目录: $VIDEO_DIR"
echo "共24集，预计总大小约 40-50GB"
echo ""

SUCCESS=0
FAILED=0

for i in $(seq 1 24); do
    URL="${URLS[$i]}"
    OUTPUT="${OUTPUTS[$i]}"
    
    if [ -f "$VIDEO_DIR/$OUTPUT" ]; then
        SIZE=$(stat -f%z "$VIDEO_DIR/$OUTPUT" 2>/dev/null || stat -c%s "$VIDEO_DIR/$OUTPUT" 2>/dev/null)
        if [ "$SIZE" -gt 1000000 ]; then
            echo "⏭️  已存在: $OUTPUT ($SIZE bytes)"
            continue
        else
            echo "🗑️  文件过小，重新下载: $OUTPUT"
        fi
    fi
    
    echo "⏳ 下载中: 第$i集 -> $OUTPUT"
    
    if curl -L --connect-timeout 30 --max-time 1800 -o "$VIDEO_DIR/$OUTPUT" "$URL" 2>&1 | tail -1; then
        SIZE=$(stat -f%z "$VIDEO_DIR/$OUTPUT" 2>/dev/null || stat -c%s "$VIDEO_DIR/$OUTPUT" 2>/dev/null)
        if [ "$SIZE" -gt 100000000 ]; then
            echo "✅ 下载成功: $OUTPUT ($SIZE bytes)"
            SUCCESS=$((SUCCESS + 1))
        else
            echo "❌ 文件过小: $OUTPUT ($SIZE bytes)"
            rm -f "$VIDEO_DIR/$OUTPUT"
            FAILED=$((FAILED + 1))
        fi
    else
        echo "❌ 下载失败: $OUTPUT"
        FAILED=$((FAILED + 1))
    fi
    
    echo ""
done

echo "=========================================="
echo "  📊 下载完成统计"
echo "=========================================="
echo "成功: $SUCCESS 集"
echo "失败: $FAILED 集"
echo ""
echo "访问地址: https://tv.drifter.indevs.in"
echo "=========================================="
