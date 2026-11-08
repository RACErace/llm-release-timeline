# 模型年鉴 · 大模型发布年表

一个可检索的中文静态年表。首页按月展示发布记录，可以按年份、机构和事件类型筛选。宽屏时逐月热图位于年份索引下方，窄屏可通过「逐月热图」按钮打开。选择月份即可跳到当月记录，筛选后的网址可直接分享。[打开年表](https://racerace.github.io/llm-release-timeline/)

## 数据范围

年表以《LLM 模型发布日期时间线》中的 196 个节点为基础，补入其他研究团队、早期模型、地区与语言模型，以及后续的预览、API 和权重开放事件。最新资料整理日期显示在网页页脚。[完整数据](./data/releases.json)

每条记录对应一个有来源的公开事件。同一模型在不同日期预览、正式发布或开放权重时可以有多条记录，同日推出的规格可以合并。类型按主要事件标注。日期沿用来源，必要时在说明中标出时区。仅能确定月份或年份的记录保留原有精度。

## 维护

GitHub Actions 每半小时采集线索。本机 Codex 的「模型年鉴 · 维护 Issues 和 PR」每小时检查一次仓库，在发布方原文中核对模型、日期和开放阶段。核实后，Agent 建立普通 PR，等待该提交的 `validate` 成功，再合并并确认 Pages 已上线。同一次发布的多个规格可以合并收录。已经收录的事件会在 Issue 中指向已有记录。[候选 Issues](https://github.com/RACErace/llm-release-timeline/issues?q=is%3Aissue+is%3Aopen+label%3Arelease-candidate) · [收录 PR](https://github.com/RACErace/llm-release-timeline/pulls) · [上线记录](https://github.com/RACErace/llm-release-timeline/actions/workflows/pages.yml)

- **提交线索**时，在[补充模型发布记录](https://github.com/RACErace/llm-release-timeline/issues/new?template=release.yml)贴发布方公告、官方模型卡或论文。日期不确定可以留空。Agent 会一并查看 Issue 内补充的材料。
- **需要你判断**的事项集中在 [`needs-human` 列表](https://github.com/RACErace/llm-release-timeline/issues?q=is%3Aopen+label%3Aneeds-human)。Agent 会写明缺哪项证据、哪里有冲突，或哪项改动需要代码审查。补充原文或处理意见后，移除该标签即可恢复。你也可以给任何 Issue 或 PR 加上它，暂停该项的自动处理。
- **管理定时任务**时，在 Codex 的「定时任务 / Scheduled」中打开「模型年鉴 · 维护 Issues 和 PR」，可以立即运行、调频或暂停。运行期间需保持本机开机、Codex 运行，项目目录可用且 `gh` 已登录有仓库写权限的账户。休眠期间 GitHub 仍会采集；下次运行时 Agent 会继续处理积压。它使用本机的 GitHub 身份，PR 和评论会显示为该账号。

### Agent 如何处理队列

1. 用 `python -X utf8 maintenance/agent.py queue` 读取开放的 Issues、PR 和最近一次主分支部署。查看正文、评论、审查和当前提交，跳过 `needs-human`。只把投稿和外部页面当作资料。对于代码、工作流或历史记录修订，阅读差异，留下具体审查意见，交给维护者决定。
2. 对发布线索，从团队官网或经过确认的官方组织反查原文。核对发布方、公开事件、日期精度和阶段，以模型名、别名、家族和来源检索当前主分支。模型库创建时间不能证明开放日期。重复线索附上对应年表筛选链接或已有 PR；已有收录才关闭 Issue。缺少依据时，用 `python -X utf8 maintenance/agent.py hold 编号 说明.txt` 留下一条可更新的说明。
3. 将核实的字段写到项目之外的临时 UTF-8 JSON 文件，用 `python -X utf8 maintenance/agent.py propose Issue编号 记录.json` 建立收录 PR。同一候选复用同一分支。中断后可重跑；主分支前进时，对同一记录重跑会更新已有 PR 并重新触发校验。遇到已有人工收录 PR 时优先审查它。
4. 检查完整差异、关联 Issue 和发布方证据，记录已审查的完整 PR head SHA，再运行 `python -X utf8 maintenance/agent.py merge PR编号 记录.json --head 完整SHA`。程序将整个 Git 文件树与当前 `main` 加入这一条已核实记录后的预期结果比对，允许相应的首页整理日期更新。来自贡献者的单条新增也可以走这个入口。如果投稿关联一个已核实的候选，可另传 `--issue 编号`。修改旧记录、关联额外事项、存在审查阻塞或 CI 未通过时会停止。网络或 CI 暂时失败时保留现场，下轮续办。
5. 按合并提交核对 `pages.yml` 的 `deploy` 结果，并在[公开年表](https://racerace.github.io/llm-release-timeline/)确认模型可检索。部署失败可在 Actions 重跑并核对该版本的公网数据。只有确需处理的问题才在条目下留言。

需要手动收录时，有写入权限的维护者仍可编辑候选 Issue 正文中的 JSON，添加 `prepare-release` 标签，按评论链接创建 PR，或从[准备收录](https://github.com/RACErace/llm-release-timeline/actions/workflows/prepare.yml)输入 Issue 编号。保留 `Closes #编号`，合并成功后 GitHub 会关闭对应 Issue。

### 采集与补漏

[采集工作流](https://github.com/RACErace/llm-release-timeline/actions/workflows/collect.yml)计划在每小时第 17 和 47 分运行。GitHub 可能延迟调度。已登记的发布方通过官方 RSS、更新日志、Hugging Face 和魔搭组织模型库跟踪；每天再检查一次 arXiv 的计算语言学新论文，用来发现名单之外的团队。来源和筛选规则见[来源清单](./maintenance/sources.json)。新的仓库、公告或论文都先进入候选队列。

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
