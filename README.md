# 模型年鉴 · 大模型发布年表

[打开年表](https://racerace.github.io/llm-release-timeline/) · [下载完整数据（JSON）](./data/releases.json) · [补充发布记录](https://github.com/RACErace/llm-release-timeline/issues/new?template=release.yml)

按时间查阅大语言模型的发布、预览、API 上线和权重开放记录。可以搜索模型名，按年份、机构或事件类型筛选，也可以点击热图中的月份查看当月发布。筛选后的网址可以直接分享。

## 收录口径

每条记录对应一次有公开来源的事件。同一模型的预览、正式发布和权重开放可能发生在不同日期，分别收录；同日发布的多个规格可以合并为一条。日期以发布方公告、开发者日志、官方模型卡或论文为依据。只能确认到月份或年份时，保留相应精度。

研究论文可作为新模型或基础模型架构公开的依据。只评测已有模型、发表基准或数据集、提出训练或推理技巧的论文不单独收录。引用 arXiv 时，链接到首次披露该项成果的具体版本；后续版本新增的模型规格按该版本的日期记录。

## 补充与勘误

提交新记录时，请附上模型名称、发布方、事件类型及一手来源链接。如果日期尚不明确，可以留空并说明已知信息。更正已有记录时，请指出要修改的条目、字段和依据。[提交勘误](https://github.com/RACErace/llm-release-timeline/issues/new)

数据保存在 [data/releases.json](./data/releases.json)，按日期升序排列。

| 字段 | 含义 |
| --- | --- |
| `date` | 事件日期，支持 `YYYY-MM-DD`、`YYYY-MM` 或 `YYYY` |
| `org`、`name` | 发布机构或团队、公开模型名称 |
| `kind` | 发布、预览、开放权重、开放 API、受限开放、版本更新、研究公开或公告 |
| `note` | 该事件公开了什么，以及适用范围 |
| `url`、`source` | 一手来源的网址和名称 |
| `url2`、`stage` | 可选的补充来源和阶段说明 |

## 本地预览

在仓库根目录运行 `python -m http.server 8000`，打开 [localhost:8000](http://localhost:8000/)。修改数据后，可用 `python maintenance/catalog.py check` 校验记录。
