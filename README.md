# 📺 流媒体服务器使用说明

## 访问地址
**https://tv.drifter.indevs.in**

## 视频目录
```
~/streaming-server/videos/
```

## 下载《低智商犯罪》

### 方法一：使用迅雷/NDM下载
剧集信息：
- 名称：低智商犯罪
- 集数：24集
- 类型：国产剧/喜剧/悬疑
- 主演：王骁、田曦薇、王传君

推荐下载源：
1. **低端影视** (ddys.io) - 高清MP4
2. **星空影院** - 磁力链接

### 方法二：使用命令行下载（需要可用源）
```bash
cd ~/streaming-server/videos

# 示例（需要替换为实际可用链接）
curl -L -o "低智商犯罪.ep01.mp4" "下载链接"
```

### 方法三：从其他设备传输
- AirDrop (Mac/iOS)
- 网盘下载后上传到 videos 目录

## 支持的格式
- MP4 ✅ (推荐)
- MKV
- AVI
- MOV
- WEBM

## 管理命令

### 启动服务器
```bash
cd ~/streaming-server && python3 stream_server.py
```

### 停止服务器
```bash
pkill -f "stream_server.py"
```

### 查看运行状态
```bash
lsof -i :9878
```

### Cloudflare隧道管理
```bash
# 查看隧道状态
cloudflared tunnel list

# 重启隧道
pkill -f cloudflared
cloudflared tunnel --config ~/.cloudflared/config.yml run
```

## 注意事项
1. 视频文件放入 `~/streaming-server/videos/` 后刷新网页即可看到
2. 支持Range请求，可以拖拽进度条播放
3. 移动端支持横屏播放
4. 视频命名建议：`剧名.ep01.mp4`、`剧名.ep02.mp4`...
