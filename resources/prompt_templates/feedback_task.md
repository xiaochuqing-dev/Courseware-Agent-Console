# 当前任务

项目显示名：{{PROJECT_NAME}}
项目 ID：{{PROJECT_ID}}
任务类型：反馈修改
反馈轮次：{{FEEDBACK_ROUND}}
基于：当前登记的最新有效产品
预期输出：{{EXPECTED_OUTPUT}}
产物 ID：{{ARTIFACT_ID}}
版本号：{{VERSION_NUMBER}}

## 特殊要求

{{SPECIAL_REQUIREMENTS}}

## 执行

请按照根目录《AGENT任务规则.md》中“反馈修改”规则执行。

重点读取：
- 客户反馈/{{FEEDBACK_ROUND}}/
- 产品迭代/中的最新有效 HTML
- 项目记录.md
- 原始需求/
- 公共工具/WORKFLOW.md
- 公共工具/validate-tool.js

任务完成后：
- 严格保存为 {{EXPECTED_OUTPUT}}
- 在 HTML head 写入项目 ID、产物 ID、版本号和反馈轮次 meta
- 如果预期文件已存在，停止并报告，绝不覆盖历史版本
- 追加更新 项目记录.md，记录反馈轮次、产品、产物 ID和版本
