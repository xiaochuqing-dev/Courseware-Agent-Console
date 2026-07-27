# 当前任务

项目显示名：{{PROJECT_NAME}}
项目 ID：{{PROJECT_ID}}
任务类型：首次制作
预期输出：{{EXPECTED_OUTPUT}}
产物 ID：{{ARTIFACT_ID}}
版本号：{{VERSION_NUMBER}}

## 特殊要求

{{SPECIAL_REQUIREMENTS}}

## 执行

请按照根目录《AGENT任务规则.md》中“首次制作”规则执行本项目。

重点读取：
- 当前项目/原始需求/
- 公共工具/WORKFLOW.md
- 公共工具/template.html
- 公共工具/validate-tool.js

“原始需求”目录不只包含 JSON，还可能包含图片、PDF、Word、PPT、表格、Markdown 或其他参考材料。开始制作前必须先枚举该目录中的全部文件，并读取当前工具能够读取的所有材料：
- JSON 是结构化主需求。
- 其他文件是补充内容和视觉参考，不得遗漏图片或文档材料。
- 对无法读取的二进制材料，必须在项目记录中写明文件名、未读取原因和实际处理方式，不得假装已经读取。

任务完成后：
- 严格保存为 {{EXPECTED_OUTPUT}}
- 在 HTML head 写入项目 ID、产物 ID、版本号和反馈轮次 meta
- 如果预期文件已存在，停止并报告，绝不覆盖历史版本
- 追加更新 项目记录.md，记录项目显示名、项目 ID、产品、产物 ID和版本
