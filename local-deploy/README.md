# TrendRadar 本机服务

- 报告网页：`http://127.0.0.1:8080/`
- 配置编辑器：`http://127.0.0.1:8080/config/`
- 采集：自动采集已禁用，仅在手动执行时更新
- 网络：网页服务仅绑定 `127.0.0.1`
- 数据：`output/`，保留最近 30 天
- 通知：已关闭
- AI 分析和翻译：已关闭

## 可视化配置

打开 `http://127.0.0.1:8080/config/` 后，页面会从当前运行副本读取并展示：

- `config/config.yaml`
- `config/frequency_words.txt`
- `config/timeline.yaml`

编辑完成后点击“保存并应用”，或按 `Command+S` / `Ctrl+S`。保存前会校验 YAML，成功后原子写入当前配置；下次打开编辑器会重新读取这些文件，不依赖浏览器缓存。

每次保存前的版本会备份到 `config/backups/`。保存配置不会立即触发采集；自动采集保持禁用，下一次手动采集会使用已保存的配置。

由于 macOS 不允许 `launchd` 后台任务直接访问 `Documents`，常驻运行副本位于：

`/Users/administrator/.local/share/InfoTide/TrendRadar`

## 服务名

- `com.infotide.trendradar.web`
- `com.infotide.trendradar.crawler`（已停止并持久禁用）

## 手动采集

```bash
cd /Users/administrator/.local/share/InfoTide/TrendRadar
uv run python -m trendradar
```

## 停止服务

```bash
launchctl bootout gui/$(id -u)/com.infotide.trendradar.web
```

## 重新启动

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.infotide.trendradar.web.plist
```

## 重新启用每 30 分钟自动采集

```bash
launchctl enable gui/$(id -u)/com.infotide.trendradar.crawler
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.infotide.trendradar.crawler.plist
```

## 查看日志

```bash
tail -f output/logs/crawler.log
tail -f output/logs/crawler-error.log
tail -f output/logs/web-error.log
```
