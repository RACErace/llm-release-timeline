# 模型年鉴 · 大模型发布年表

一个可检索的中文静态年表。首页按月展示发布记录，可以按年份、机构和事件类型筛选。宽屏时逐月热图位于年份索引下方，窄屏可通过「逐月热图」按钮打开。选择月份即可跳到当月记录，筛选后的网址可直接分享。[打开年表](https://racerace.github.io/llm-release-timeline/)

## 数据范围

年表以《LLM 模型发布日期时间线》中的 196 个节点为基础，补入其他研究团队、早期模型、地区与语言模型，以及后续的预览、API 和权重开放事件。最新资料整理日期显示在网页页脚。[完整数据](./data/releases.json)

每条记录对应一个有来源的公开事件。同一模型在不同日期预览、正式发布或开放权重时可以有多条记录，同日推出的规格可以合并。类型按主要事件标注。日期沿用来源，必要时在说明中标出时区。仅能确定月份或年份的记录保留原有精度。

## 维护

1. **接收消息。** 在仓库右上角选择 **Watch → Custom → Issues**，按个人 GitHub 通知设置在收件箱或邮件接收提醒。自动发现的消息会进入[候选 Issue 列表](https://github.com/RACErace/llm-release-timeline/issues?q=is%3Aissue+is%3Aopen+label%3Arelease-candidate)。手动发现的消息也可以用[补充模型发布记录](https://github.com/RACErace/llm-release-timeline/issues/new?template=release.yml)提交。
2. **核实一条候选。** 打开原文，核对模型、发布方、日期、发布阶段及开放范围。通过 Issue 正文右上角的菜单编辑 JSON，填写一句中文说明和可核实的来源。权重仓库的创建或修改日期可能早于实际开放，采集器会把该类候选的日期留空。已经收录或不相关的候选直接关闭。
3. **生成并合并 PR。** 有仓库写入权限的维护者给 Issue 加上 `prepare-release` 标签。机器人会在该 Issue 内给出“查看改动并创建 PR”链接。点击链接，选择 **Create pull request**，等 `validate` 通过后合并。需要改候选时，编辑 JSON 后从[准备收录](https://github.com/RACErace/llm-release-timeline/actions/workflows/prepare.yml)选择 **Run workflow**，输入 Issue 编号。保留预填的 `Closes #编号`，合并时 GitHub 会自动关闭对应 Issue。
4. **查看上线结果。** 合入 `main` 后，[校验与发布年表](https://github.com/RACErace/llm-release-timeline/actions/workflows/pages.yml)检查全库、发布静态文件，再从公网读取首页与数据并核对哈希。该次 `deploy` 显示绿色后，可以在[公开年表](https://racerace.github.io/llm-release-timeline/)搜索模型名。网页的计数、筛选和热图会随数据更新。

### 采集与补漏

[采集工作流](https://github.com/RACErace/llm-release-timeline/actions/workflows/collect.yml)计划在每小时第 17 和 47 分运行。GitHub 可能延迟调度。已登记的发布方通过官方 RSS、更新日志、Hugging Face 和魔搭组织模型库跟踪；每天再检查一次 arXiv 的计算语言学新论文，用来发现名单之外的团队。来源和筛选规则见[来源清单](./maintenance/sources.json)。新的仓库、公告或论文都先进入人工核实环节。

首次访问一个来源只建立基线，因此启用或增加来源时不会集中提醒旧消息。模型库检查新出现的权重或开放状态，平台内沿用同一模型名的更新主要依赖发布日志。同一模型、同一阶段可识别的消息合到同一 Issue，歧义名称保留待核实。发现模型家族一次发布多个规格时，可按原文合成一条。创建时间、抓取时间与官方公布时间分别使用。

想立即检查时，在采集工作流点击 **Run workflow**。可在可选输入中填写一个清单内官方账号的 Hugging Face 模型标识，单独补查基线以前的模型。其他平台的历史遗漏用上述 Issue 表单补录。添加信息源时在清单中使用团队的官方账号或页面，通过普通 PR 合入；来源首次成功后才开始产生新提醒。

采集运行摘要列出成功来源数、新线索数、失败来源和待投递数量。成功读取的数据和待投递队列先持久化到[采集状态分支](https://github.com/RACErace/llm-release-timeline/tree/collector-state)，随后每轮投递最多 10 组。个别来源失败会保留其上次成功进度；公开模型库按时间分页回读，RSS 和网页能补回的范围取决于上游保留的条目。连续失败时先看对应来源的运行日志，修正网址或选择器后重新运行。公开仓库长期无活动时，GitHub 可能停用定时任务，可在工作流页面重新启用。

### 记录字段

| 字段 | 说明 |
| --- | --- |
| `date` | 事件日期，使用 `YYYY-MM-DD`、`YYYY-MM` 或 `YYYY` |
| `org`、`name` | 机构或团队标识、公开模型名 |
| `kind` | 发布、预览、开放权重、开放 API、受限开放、版本更新、研究公开、公告 |
| `note` | 本次事件对应的模型、范围或开放渠道 |
| `url`、`source` | 原始来源网址和可读标签 |
| `url2`、`stage` | 可选的补充来源和阶段说明 |

优先引用研发团队的公告、开发者日志、官方模型卡或论文。补充现有模型时，请核对同一日期和开放阶段是否已被收录。[提交补充或勘误](https://github.com/RACErace/llm-release-timeline/issues)

## 本地运行

在仓库根目录运行 `python -m http.server 8000`，再打开 [本地预览](http://localhost:8000/)。本地验证维护程序可使用 Python 3.13：

```sh
python -m pip install -r maintenance/requirements.txt
python maintenance/catalog.py check
python -m unittest discover -s maintenance -p "test_*.py"
```

网页使用原生 HTML、CSS 和 JavaScript。GitHub Pages 的发布来源选择 **GitHub Actions**；部署包只包含首页、样式、脚本、图标和公开数据。
