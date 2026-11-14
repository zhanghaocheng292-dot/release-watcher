# Release Watcher · 开源版本更新监控

免费、可复用的 **GitHub Actions** 小工具：每天自动检查你关心的开源项目有没有发新版本，把更新说明整理成**中文 Markdown**，保存到本仓库的 `reports/` 里，形成可查阅的历史记录。

即使以后没有 AI 助手帮你，只要这个仓库还在，GitHub 也会继续按计划自动跑。

---

## 它做什么

1. 读取 `config/watchlist.yml` 里的关注列表  
2. 调用 GitHub Releases API 检查是否有新版本（只用免费的 `GITHUB_TOKEN`，无需付费 API）  
3. 生成中文摘要：优先尝试免费的 GitHub Models；失败则自动改用「模板 + 原文要点提取」，**不会因为 AI 失败而中断**  
4. 写入 `reports/YYYY-MM-DD.md`，并更新本页的「最新报告」入口  
5. 由 Actions 机器人自动提交推送

<!-- LATEST_REPORT_START -->
**最新报告**：[reports/2026-11-14.md](reports/2026-11-14.md)  
本次：openai/openai-python v3.51.0
<!-- LATEST_REPORT_END -->

---

## 如何增加 / 删除关注的项目

1. 打开文件：[`config/watchlist.yml`](config/watchlist.yml)  
2. 按下面格式增删（在 iPhone 的 GitHub App 里也可以直接编辑）：

```yaml
repos:
  - owner: ollama
    name: ollama
    note: 本地大模型运行工具

  - owner: facebook
    name: react
    note: 可选备注，随便写
```

3. 保存提交后，下次定时任务（或手动运行）就会按新列表检查。

> `owner` = GitHub 用户名或组织名，`name` = 仓库名。  
> 例如地址 `https://github.com/ollama/ollama` → owner=`ollama`，name=`ollama`。

---

## 如何手动运行一次

1. 打开本仓库 → 上方 **Actions**  
2. 左侧点 **Check Releases**  
3. 右侧点 **Run workflow** → 再点绿色 **Run workflow**  
4. 等它变成绿色勾；然后到 **Code** → `reports/` 看今天的报告

---

## 在 iPhone 上怎么看结果

推荐用 **GitHub App**（App Store 免费）：

| 你想看什么 | 怎么点 |
| --- | --- |
| 今天的中文摘要 | 打开本仓库 → **Code** → **reports** → 点 `YYYY-MM-DD.md` |
| 有没有跑成功 | 打开本仓库 → **Actions** → 点最近一次 **Check Releases** |
| 改关注列表 | **Code** → `config` → `watchlist.yml` → 右上角铅笔编辑 |

也可以用手机 Safari 打开同样的页面。

---

## 定时说明

- 默认每天 **UTC 01:00** 运行，对应上海时间 **上午 09:00**  
- 可在 [`.github/workflows/check-releases.yml`](.github/workflows/check-releases.yml) 里改 `cron`

---

## 费用与限制

- GitHub 免费账户的 Actions 分钟数通常足够每天跑一次  
- 不调用任何付费翻译 / AI API  
- GitHub Models（若账号可用）为可选增强；不可用时自动回退，不影响成功

---

## 本地试跑（可选）

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export GITHUB_TOKEN=你的token   # 可选；读公开仓库不强制
python scripts/check_releases.py
```

---

## 目录结构

```
config/watchlist.yml          # 关注列表（常改这个）
scripts/check_releases.py     # 检测与生成摘要
state/last_seen.json          # 已见过的最新版本（勿手改）
reports/                      # 历史中文报告
.github/workflows/            # 定时与手动工作流
```
