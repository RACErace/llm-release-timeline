# 模型年鉴 · 大模型发布年表

[打开年表](https://racerace.github.io/llm-release-timeline/) · [下载完整数据（JSON）](./data/releases.json) · [补充发布记录](https://github.com/RACErace/llm-release-timeline/issues/new?template=release.yml)

按时间查阅大语言基模型的发布、预览、API 上线和权重开放记录。可以搜索模型名，按年份、机构或事件类型筛选，也可以点击热图中的月份查看当月发布。筛选后的网址可以直接分享。

## 收录口径

年表收录大语言基模型及其原开发方正式发布的模型版本，包括原生多模态语言模型、闭源 API 模型和官方预览、对话、指令、推理等版本。官方版本经过后训练时，注明所属基座和实际开放方式。第三方微调、任务适配器、合并或量化重打包、专用嵌入模型和评测均不单列。

同一基模型的预览、正式发布、API 上线和权重开放可以有各自的记录。同日发布的多个基础规格可以合并。日期依据发布公告、开发者日志或官方模型卡。早期基模型若最早在作者的技术报告中公开，保留该版本作为历史出处。只能确认到月份或年份时，保留相应精度。

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
